// =====================================================================
// app.js - what the page does.
//
// Sections in this file:
//   1. Small helpers          6. Explain logs / Review code
//   2. Confirmation dialog    7. History (list, delete, delete by date)
//   3. Dark mode and tabs     8. AI chat
//   4. Server status          9. Code builder (prompt, tools)
//   5. Showing AI answers    10. What runs when the page loads
// =====================================================================

// ---------------------------------------------------------------------
// 1. Small helpers
// ---------------------------------------------------------------------

const byId = (id) => document.getElementById(id);

// Create an HTML element with a class and text.
// We always use textContent (never innerHTML) for text that comes from the
// user or the AI, so it is shown as plain text and never run as code.
function make(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

// Talk to the backend. fetch() makes an HTTP request without reloading the page.
// Returns { ok: true, data } or { ok: false, message }.
async function api(method, url, body) {
  const options = { method: method };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);           // JSON sent to the backend
  }
  try {
    const response = await fetch(url, options);
    const data = await response.json();            // JSON received back
    if (response.ok) return { ok: true, data: data };
    const message = typeof data.detail === "string" ? data.detail
      : "The input was rejected. It may be empty or too long.";
    return { ok: false, message: message };
  } catch (error) {
    return { ok: false, message: "Could not reach the server. Is it still running in the terminal?" };
  }
}

function showLoading(box, text) {
  const loading = make("div", "loading");
  loading.append(make("div", "spinner"), make("span", "", text));
  box.replaceChildren(loading);
  box.style.display = "block";
}

function showError(box, message) {
  box.replaceChildren(make("div", "error-box", message));
  box.style.display = "block";
}

const niceTime = (iso) => new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

// ---------------------------------------------------------------------
// 2. Confirmation dialog
// Anything that deletes data asks "Are you sure?" first.
// Usage:  if (await confirmAction("Delete this?", "Delete")) { ... }
// ---------------------------------------------------------------------

let answerConfirm = null;      // the function that finishes the open dialog

function confirmAction(text, yesLabel) {
  byId("confirm-text").textContent = text;
  byId("confirm-yes").textContent = yesLabel;
  byId("confirm-overlay").hidden = false;
  byId("confirm-no").focus();                      // Cancel is the safe default
  return new Promise(function (resolve) { answerConfirm = resolve; });
}

function closeConfirm(answer) {
  byId("confirm-overlay").hidden = true;
  if (answerConfirm) answerConfirm(answer);
  answerConfirm = null;
}

byId("confirm-yes").addEventListener("click", () => closeConfirm(true));
byId("confirm-no").addEventListener("click", () => closeConfirm(false));
byId("confirm-overlay").addEventListener("click", function (event) {
  if (event.target === byId("confirm-overlay")) closeConfirm(false);   // click outside the box
});
document.addEventListener("keydown", function (event) {
  if (event.key === "Escape" && !byId("confirm-overlay").hidden) closeConfirm(false);
});

// ---------------------------------------------------------------------
// 3. Dark mode and tabs
// ---------------------------------------------------------------------

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  byId("theme-button").textContent = theme === "dark" ? "Light mode" : "Dark mode";
  try { localStorage.setItem("theme", theme); } catch (error) { /* storage blocked: ignore */ }
}
byId("theme-button").addEventListener("click", function () {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
try { setTheme(localStorage.getItem("theme") || "light"); } catch (error) { setTheme("light"); }

function openTab(panelId) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.panel === panelId));
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("active", p.id === panelId));
  if (panelId === "history-panel") refreshHistory();
}
document.querySelectorAll(".tab").forEach(function (tab) {
  tab.addEventListener("click", () => openTab(tab.dataset.panel));
});

// ---------------------------------------------------------------------
// 4. Server status (calls GET /health)
// ---------------------------------------------------------------------

async function checkServer() {
  const reply = await api("GET", "/health");
  const dot = byId("status-dot"), text = byId("status-text");
  if (!reply.ok) {
    dot.className = "dot down";
    text.textContent = "Server not reachable";
  } else {
    dot.className = "dot " + (reply.data.ai_configured ? "live" : "demo");
    text.textContent = reply.data.ai_configured ? "AI connected" : "Demo mode (no AI key)";
  }
}

// ---------------------------------------------------------------------
// 5. Showing AI answers
// ---------------------------------------------------------------------

// The prompts ask the AI to answer under fixed headings. We split the
// answer at those headings and draw one box per heading.
const HEADINGS = ["WHAT HAPPENED", "LIKELY ROOT CAUSE", "HOW TO FIX IT", "HOW TO PREVENT IT",
                  "SUMMARY", "BUGS AND RISKS", "IMPROVEMENTS", "VERDICT"];
const RISK_HEADINGS = ["LIKELY ROOT CAUSE", "BUGS AND RISKS"];
const FINAL_HEADINGS = ["HOW TO PREVENT IT", "VERDICT"];

function splitIntoSections(answer) {
  const sections = [];
  let current = { name: "", lines: [] };            // text before the first heading
  answer.split("\n").forEach(function (line) {
    // "## What happened:" or "**WHAT HAPPENED**" should also count as a heading.
    const cleaned = line.replace(/[#*:]/g, "").trim().toUpperCase();
    if (HEADINGS.includes(cleaned)) {
      sections.push(current);
      current = { name: cleaned, lines: [] };
    } else {
      current.lines.push(line);
    }
  });
  sections.push(current);
  return sections
    .map(s => ({ name: s.name, body: s.lines.join("\n").trim() }))
    .filter(s => s.body !== "");
}

// The title row above every result: title, badges and buttons.
function resultHead(title, badges, buttons) {
  const head = make("div", "result-head");
  head.append(make("span", "result-title", title));
  badges.forEach(badge => head.append(make("span", "badge " + badge.style, badge.text)));
  head.append(make("span", "spacer"));
  buttons.forEach(button => head.append(button));
  return head;
}

function copyButton(text) {
  const button = make("button", "secondary", "Copy");
  button.addEventListener("click", function () {
    navigator.clipboard.writeText(text).then(() => { button.textContent = "Copied"; });
  });
  return button;
}

const modeBadge = (mode) => ({ style: mode, text: mode === "ai" ? "AI answer" : "Demo answer" });

// A log explanation or code review: one box per heading.
function drawAnswer(box, title, kind, mode, answer) {
  box.replaceChildren(resultHead(title,
    [{ style: "kind", text: kind.replace("_", " ") }, modeBadge(mode)], [copyButton(answer)]));
  splitIntoSections(answer).forEach(function (section) {
    if (section.name === "") {                       // e.g. the demo-mode note
      box.append(make("div", "notice", section.body));
      return;
    }
    let className = "section";
    if (RISK_HEADINGS.includes(section.name)) className += " risk";
    if (FINAL_HEADINGS.includes(section.name)) className += " final";
    const card = make("div", className);
    card.append(make("div", "section-name", section.name), make("div", "section-body", section.body));
    box.append(card);
  });
  box.style.display = "block";
}

// Built code: the AI replies with a ``` code block ``` followed by an
// EXPLANATION. We separate the two and show the code in a dark block.
function splitCode(answer) {
  const match = answer.match(/```[^\n]*\n([\s\S]*?)```/);
  if (!match) return { code: answer.trim(), explanation: "" };
  const rest = answer.slice(match.index + match[0].length);
  return {
    code: match[1].replace(/\s+$/, ""),
    explanation: rest.replace(/^\s*[#*]*\s*EXPLANATION[*:]*\s*/i, "").trim()
  };
}

const LANGUAGES = { "Python": "py", "JavaScript": "js", "TypeScript": "ts", "Java": "java", "C#": "cs",
                    "Go": "go", "SQL": "sql", "Bash": "sh", "JSON": "json", "HTML": "html" };

function drawBuild(box, title, label, mode, answer, fileName) {
  const parts = splitCode(answer);
  const download = make("button", "secondary", "Download");
  download.addEventListener("click", function () {
    // Turn the code into a file in the browser and click a hidden link to save it.
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([parts.code + "\n"], { type: "text/plain" }));
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(link.href);
  });
  box.replaceChildren(resultHead(title,
    [{ style: "kind", text: label }, modeBadge(mode)], [copyButton(parts.code), download]));
  box.append(make("pre", "codeblock mono", parts.code));
  if (parts.explanation) {
    const card = make("div", "section");
    card.append(make("div", "section-name", "EXPLANATION"), make("div", "section-body", parts.explanation));
    box.append(card);
  }
  box.style.display = "block";
}

// ---------------------------------------------------------------------
// 6. Explain logs / Review code
// ---------------------------------------------------------------------

const SAMPLE_LOG = `2026-11-10 09:14:02 INFO  Starting order-service v2.3.1
2026-11-10 09:14:03 INFO  Connecting to database at db.internal:5432
2026-11-10 09:14:03 INFO  Connection pool created (max_size=5)
2026-11-10 09:20:41 WARNING Connection pool usage at 80% (4/5)
2026-11-10 09:21:07 WARNING Connection pool usage at 100% (5/5)
2026-11-10 09:21:37 ERROR Request POST /orders failed after 30000 ms
Traceback (most recent call last):
  File "/srv/app/orders.py", line 88, in create_order
    conn = pool.get_connection(timeout=30)
  File "/srv/app/db/pool.py", line 41, in get_connection
    raise PoolTimeoutError("no free connection after 30s")
db.pool.PoolTimeoutError: no free connection after 30s
2026-11-10 09:21:38 ERROR Request GET /orders/1182 failed after 30000 ms
2026-11-10 09:21:40 CRITICAL Health check failed: database unreachable from pool`;

const SAMPLE_CODE = `import sqlite3

DB_PASSWORD = "admin123"


def get_user(username):
    conn = sqlite3.connect("users.db")
    query = "SELECT * FROM users WHERE name = '" + username + "'"
    result = conn.execute(query).fetchall()
    return result[0]


def average(numbers):
    try:
        return sum(numbers) / len(numbers)
    except:
        pass`;

// Run one request with a "Working..." button and a spinner.
// `work` is a function that calls the backend and draws the result.
async function withBusyButton(button, box, work) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Working...";
  showLoading(box, "Asking the assistant...");
  await work();
  button.disabled = false;
  button.textContent = label;
}

// Wire up one big text box: counter, run button, Ctrl+Enter, Clear.
function setUpEditor(prefix, run) {
  const input = byId(prefix + "-input"), counter = byId(prefix + "-count");
  const updateCount = () => { counter.textContent = input.value.length.toLocaleString() + " / 20,000"; };
  input.addEventListener("input", updateCount);
  input.addEventListener("keydown", (event) => { if (event.ctrlKey && event.key === "Enter") run(); });
  byId(prefix + "-button").addEventListener("click", run);
  byId(prefix + "-clear").addEventListener("click", function () {
    input.value = "";
    updateCount();
    byId(prefix + "-result").style.display = "none";
  });
  return updateCount;
}

function setUpAnalysis(prefix, url, title, sample) {
  const input = byId(prefix + "-input"), box = byId(prefix + "-result");
  const updateCount = setUpEditor(prefix, function () {
    if (!input.value.trim()) { showError(box, "Please paste some text first."); return; }
    withBusyButton(byId(prefix + "-button"), box, async function () {
      const reply = await api("POST", url, { text: input.value });
      if (!reply.ok) { showError(box, reply.message); return; }
      drawAnswer(box, title, reply.data.kind, reply.data.mode, reply.data.result);
      refreshHistory();
    });
  });
  byId(prefix + "-sample").addEventListener("click", () => { input.value = sample; updateCount(); });
}
setUpAnalysis("log", "/api/analyze-log", "Log explanation", SAMPLE_LOG);
setUpAnalysis("code", "/api/review-code", "Code review", SAMPLE_CODE);

// ---------------------------------------------------------------------
// 7. History
// ---------------------------------------------------------------------

let historyItems = [];          // filled from GET /api/history
let historyFilter = "all";
let openHistoryId = null;       // the result currently shown under the list

const SHORT_NAME = { log: "LOG", code: "CODE", pull_request: "PR", build: "BUILD" };
// Pull request reviews are counted together with code reviews.
const groupOf = (kind) => kind === "pull_request" ? "code" : kind;

function historyRow(item, compact) {
  const row = make("div", "item");
  const open = make("button", "item-open");
  const main = make("span", "item-main");
  main.append(make("div", "item-preview mono", item.preview),
              make("div", "item-meta", "#" + item.id + " · " + niceTime(item.created_at)));
  open.append(make("span", "item-icon mono", SHORT_NAME[item.kind] || "?"), main);
  open.addEventListener("click", () => openHistoryItem(item.id));
  row.append(open);
  if (!compact) row.append(make("span", "badge " + item.mode, item.mode));

  const remove = make("button", "row-del", "Delete");
  remove.addEventListener("click", async function () {
    if (!(await confirmAction("Delete result #" + item.id + "? This cannot be undone.", "Delete"))) return;
    await api("DELETE", "/api/history/" + item.id);
    if (openHistoryId === item.id) byId("history-result").style.display = "none";
    refreshHistory();
  });
  row.append(remove);
  return row;
}

function drawHistory() {
  const count = (group) => historyItems.filter(i => groupOf(i.kind) === group).length;
  byId("stat-total").textContent = historyItems.length;
  byId("stat-logs").textContent = count("log");
  byId("stat-code").textContent = count("code");
  byId("stat-build").textContent = count("build");

  const list = byId("history-list");
  const shown = historyItems.filter(i => historyFilter === "all" || groupOf(i.kind) === historyFilter);
  list.replaceChildren(...shown.map(item => historyRow(item, false)));
  if (shown.length === 0) list.append(make("div", "empty", "Nothing here yet."));

  const recent = byId("recent-list");
  recent.replaceChildren(...historyItems.slice(0, 4).map(item => historyRow(item, true)));
  if (historyItems.length === 0) recent.append(make("div", "empty", "Your latest results will appear here."));
}

async function refreshHistory() {
  const reply = await api("GET", "/api/history");
  if (reply.ok) { historyItems = reply.data; drawHistory(); }
}

async function openHistoryItem(id) {
  const reply = await api("GET", "/api/history/" + id);
  if (!reply.ok) return;
  const item = reply.data, box = byId("history-result");
  openTab("history-panel");
  if (item.kind === "build") {
    drawBuild(box, "Saved result #" + item.id, "build", item.mode, item.result, "code.txt");
  } else {
    drawAnswer(box, "Saved result #" + item.id, item.kind, item.mode, item.result);
  }
  const details = make("details");
  details.append(make("summary", "", "Show the original input"), make("pre", "input-view mono", item.input_text));
  box.insertBefore(details, box.children[1]);
  openHistoryId = item.id;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.querySelectorAll(".chip").forEach(function (chip) {
  chip.addEventListener("click", function () {
    document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    historyFilter = chip.dataset.filter;
    drawHistory();
  });
});

byId("clear-all").addEventListener("click", async function () {
  if (historyItems.length === 0) return;
  const question = "Delete ALL saved results? This cannot be undone.";
  if (!(await confirmAction(question, "Delete everything"))) return;
  await api("DELETE", "/api/history");
  byId("history-result").style.display = "none";
  refreshHistory();
  updateDateButton();
});

// ----- Delete by date -----
// The date boxes give dates in YOUR time zone. The database stores UTC,
// so we convert: start of the first day and end of the last day -> UTC text.
function chosenRange() {
  const from = byId("date-from").value, to = byId("date-to").value;
  if (!from || !to) return null;
  const start = new Date(from + "T00:00:00"), end = new Date(to + "T23:59:59");
  if (end < start) return null;
  return "start=" + encodeURIComponent(start.toISOString()) + "&end=" + encodeURIComponent(end.toISOString());
}

let rangeCount = 0;

async function updateDateButton() {
  const button = byId("date-delete"), range = chosenRange();
  rangeCount = 0;
  button.disabled = true;
  if (!range) { button.textContent = "Pick two dates"; return; }
  const reply = await api("GET", "/api/history/count?" + range);
  if (!reply.ok) { button.textContent = "Pick two dates"; return; }
  rangeCount = reply.data.count;
  button.textContent = rangeCount === 0 ? "Nothing in this range"
    : "Delete " + rangeCount + (rangeCount === 1 ? " result" : " results");
  button.disabled = rangeCount === 0;
}
byId("date-from").addEventListener("change", updateDateButton);
byId("date-to").addEventListener("change", updateDateButton);

byId("date-delete").addEventListener("click", async function () {
  const range = chosenRange();
  if (!range || rangeCount === 0) return;
  const question = "Delete " + rangeCount + " saved result(s) from " + byId("date-from").value +
                   " to " + byId("date-to").value + "? This cannot be undone.";
  if (!(await confirmAction(question, "Delete"))) return;
  await api("DELETE", "/api/history?" + range);
  byId("history-result").style.display = "none";
  refreshHistory();
  updateDateButton();
});

// ---------------------------------------------------------------------
// 8. AI chat
// The conversation lives only in this list. It is sent to the backend
// with every question so the AI remembers earlier messages. Reloading
// the page clears it; nothing is saved to the database.
// ---------------------------------------------------------------------

let chatMessages = [];     // [{ role: "user" | "assistant", content: "..." }]
let chatBusy = false;

function drawChat(extraBubble) {
  const box = byId("chat-bubbles");
  box.replaceChildren();
  if (chatMessages.length === 0 && !extraBubble) {
    box.append(make("div", "note", "Ask a general question, for example: What is Docker?"));
  }
  chatMessages.forEach(function (message) {
    box.append(make("div", "bub " + (message.role === "user" ? "me" : "ai"), message.content));
  });
  if (extraBubble) box.append(extraBubble);
  box.scrollTop = box.scrollHeight;                 // keep the newest message visible
}

async function sendChat() {
  const input = byId("chat-input"), text = input.value.trim();
  if (!text || chatBusy) return;
  chatBusy = true;
  chatMessages.push({ role: "user", content: text });
  input.value = "";
  drawChat(make("div", "bub ai wait", "Thinking..."));

  // Send the recent conversation (the backend also limits how much it uses).
  const recent = chatMessages.slice(-20).map(m => ({ role: m.role, content: m.content.slice(0, 8000) }));
  const reply = await api("POST", "/api/chat", { messages: recent });
  chatBusy = false;
  if (reply.ok) {
    chatMessages.push({ role: "assistant", content: reply.data.reply });
    drawChat();
  } else {
    chatMessages.pop();                              // the question was not answered
    input.value = text;                              // give it back so it can be re-sent
    drawChat(make("div", "bub err", reply.message));
  }
}
byId("chat-send").addEventListener("click", sendChat);
byId("chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter") sendChat(); });

byId("chat-new").addEventListener("click", async function () {
  if (chatMessages.length === 0) return;
  if (!(await confirmAction("Start a new chat? The current conversation will be cleared.", "Clear chat"))) return;
  chatMessages = [];
  drawChat();
});

// ---------------------------------------------------------------------
// 9. Code builder
// ---------------------------------------------------------------------

let tools = [];               // filled from GET /api/tools
let editingToolId = null;     // null = the form creates a new tool
const TOOL_LIMIT = 24000;     // same limit as MAX_TOOL_CHARS in main.py

// Fill the two language drop-downs.
["build-language", "tool-language"].forEach(function (id) {
  Object.keys(LANGUAGES).forEach(name => byId(id).append(new Option(name, name)));
});

function setBuilderMode(mode) {
  document.querySelectorAll(".seg-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  document.querySelectorAll(".mode").forEach(m => m.classList.toggle("active", m.id === "mode-" + mode));
}
document.querySelectorAll(".seg-btn").forEach(function (button) {
  button.addEventListener("click", () => setBuilderMode(button.dataset.mode));
});

// ----- 9a. Build code using a prompt -----
setUpEditor("build", function () {
  const input = byId("build-input"), box = byId("build-result"), text = input.value.trim();
  if (!text) { showError(box, "Please describe the code you need first."); return; }

  // "@pre_call make it for client ABC" -> open that tool with the rest as extra instructions.
  const mention = text.match(/^@([A-Za-z0-9_]+)\s*([\s\S]*)$/);
  if (mention) {
    const tool = tools.find(t => t.name === mention[1].toLowerCase());
    if (!tool) { showError(box, "There is no tool named @" + mention[1] + ". Check Your tools."); return; }
    useTool(tool, mention[2]);
    return;
  }

  const language = byId("build-language").value;
  withBusyButton(byId("build-button"), box, async function () {
    const reply = await api("POST", "/api/build", { prompt: text, language: language });
    if (!reply.ok) { showError(box, reply.message); return; }
    drawBuild(box, "Generated code", language, reply.data.mode, reply.data.result, "code." + (LANGUAGES[language] || "txt"));
    refreshHistory();
  });
});

// ----- 9b. Loading the tools -----
async function loadTools() {
  const reply = await api("GET", "/api/tools");
  if (!reply.ok) return;
  tools = reply.data;
  byId("seg-list").textContent = "Your tools (" + tools.length + ")";

  // Rebuild the drop-down, keeping the current choice if it still exists.
  const select = byId("tool-select"), previous = select.value;
  select.replaceChildren();
  tools.forEach(t => select.append(new Option("@" + t.name + "  -  " + t.description, t.id)));
  if (tools.some(t => String(t.id) === previous)) select.value = previous;

  byId("tool-none").hidden = tools.length > 0;
  byId("tool-run").hidden = tools.length === 0;
  drawSlots();
  drawToolList();
}

const selectedTool = () => tools.find(t => String(t.id) === byId("tool-select").value);

// ----- 9c. Build code using a tool -----
// One box per resource type the tool needs.
function drawSlots() {
  const tool = selectedTool(), slots = byId("tool-slots");
  slots.replaceChildren();
  byId("tool-result").style.display = "none";
  if (!tool) return;
  byId("tool-build").textContent = "Build with @" + tool.name;
  byId("tool-note").textContent = (tool.resources.length === 0
    ? "This tool needs no resources. Add extra instructions if you want, then build."
    : "This tool needs " + tool.resources.length + " resource(s) every time:") +
    (tool.example ? " The code will follow this tool's example format." : "");

  tool.resources.forEach(function (spec, index) {
    const slot = make("div", "slot");
    const head = make("div", "slot-head");
    const pill = make("span", "pill");
    head.append(make("span", "num", index + 1), make("span", "", spec.name),
                make("span", "help", spec.description), make("span", "spacer"), pill);

    const text = make("textarea", "input mono slot-text");
    text.rows = 4;
    text.placeholder = "Paste text here, or load a file...";
    text.dataset.name = spec.name;

    function updatePill() {
      const filled = text.value.trim() !== "";
      pill.className = "pill " + (filled ? "ok" : spec.required ? "req" : "opt");
      pill.textContent = filled ? "Added" : spec.required ? "Required" : "Optional";
      updateMeter();
    }
    text.addEventListener("input", updatePill);

    // "Load from file": the browser reads the file as text and puts it in the box.
    // The file itself is never uploaded; only its text is sent when you build.
    const picker = document.createElement("input");
    picker.type = "file";
    picker.hidden = true;
    picker.addEventListener("change", function () {
      if (!picker.files[0]) return;
      const reader = new FileReader();
      reader.onload = function () { text.value = reader.result; updatePill(); };
      reader.readAsText(picker.files[0]);
      picker.value = "";
    });
    const load = make("button", "small-btn", "Load from file");
    load.addEventListener("click", () => picker.click());

    slot.append(head, text, load, picker);
    slots.append(slot);
    updatePill();
  });
  updateMeter();
}
byId("tool-select").addEventListener("change", drawSlots);

const slotTexts = () => Array.from(document.querySelectorAll(".slot-text"));

// Shows how much of the size limit this run uses.
function updateMeter() {
  const tool = selectedTool(), meter = byId("tool-meter");
  if (!tool) { meter.textContent = ""; return; }
  let used = tool.instructions.length + (tool.example || "").length + byId("tool-extra").value.length;
  slotTexts().forEach(box => { used += box.value.length; });
  meter.textContent = used.toLocaleString() + " of " + TOOL_LIMIT.toLocaleString() +
    " characters used (the limit of one AI request)";
  meter.className = "note" + (used > TOOL_LIMIT ? " bad" : "");
}
byId("tool-extra").addEventListener("input", updateMeter);

// Open "Build code using tool" with a tool already chosen.
function useTool(tool, extra) {
  openTab("build-panel");
  setBuilderMode("tool");
  byId("tool-select").value = tool.id;
  drawSlots();
  byId("tool-extra").value = extra || "";
  updateMeter();
}

byId("tool-build").addEventListener("click", function () {
  const tool = selectedTool(), box = byId("tool-result");
  if (!tool) return;
  const resources = {};
  slotTexts().forEach(text => { resources[text.dataset.name] = text.value; });
  withBusyButton(byId("tool-build"), box, async function () {
    const reply = await api("POST", "/api/tools/" + tool.id + "/build",
                            { resources: resources, extra: byId("tool-extra").value });
    if (!reply.ok) { showError(box, reply.message); return; }
    drawBuild(box, "Generated code", "using @" + tool.name, reply.data.mode, reply.data.result,
              tool.name + "." + (LANGUAGES[tool.language] || "txt"));
    refreshHistory();
  });
});

byId("tool-clear").addEventListener("click", function () {
  byId("tool-extra").value = "";
  drawSlots();
});

// ----- 9d. Build tool (the form to create or edit a tool) -----
function addResourceRow(spec) {
  const row = make("div", "res-row");
  const name = make("input", "input res-name"), what = make("input", "input res-what");
  name.placeholder = "Call flow";
  name.maxLength = 60;
  what.placeholder = "the steps of the call";
  what.maxLength = 200;
  const required = make("select", "res-required");
  required.append(new Option("Yes", "yes"), new Option("No", "no"));
  const remove = make("button", "x-btn", "×");
  remove.title = "Remove this resource";
  remove.addEventListener("click", () => row.remove());
  if (spec) {
    name.value = spec.name;
    what.value = spec.description;
    required.value = spec.required ? "yes" : "no";
  }
  row.append(name, what, required, remove);
  byId("resource-rows").append(row);
}
byId("resource-add").addEventListener("click", () => addResourceRow());

function fillToolForm(tool) {
  editingToolId = tool ? tool.id : null;
  byId("form-title").textContent = tool ? "Editing @" + tool.name : "New tool";
  byId("form-message").textContent = "";
  byId("tool-name").value = tool ? tool.name : "";
  byId("tool-description").value = tool ? tool.description : "";
  byId("tool-instructions").value = tool ? tool.instructions : "";
  byId("tool-example").value = tool ? (tool.example || "") : "";
  byId("tool-language").value = tool && LANGUAGES[tool.language] ? tool.language : "Python";
  byId("resource-rows").replaceChildren();
  if (tool && tool.resources.length > 0) tool.resources.forEach(addResourceRow);
  else addResourceRow();
}

byId("tool-save").addEventListener("click", async function () {
  const message = byId("form-message");
  const body = {
    name: byId("tool-name").value.trim().toLowerCase(),
    description: byId("tool-description").value.trim(),
    language: byId("tool-language").value,
    instructions: byId("tool-instructions").value.trim(),
    example: byId("tool-example").value.trim(),     // optional: empty means "general format"
    resources: []
  };
  // Check the form here first, so the user gets a clear message.
  if (!/^[a-z][a-z0-9_]{1,29}$/.test(body.name)) {
    message.textContent = "Tool name: 2 to 30 characters, starting with a letter. Use only letters, digits and underscores (no spaces).";
    return;
  }
  if (!body.description) { message.textContent = "Please fill in what the tool builds."; return; }
  if (!body.instructions) { message.textContent = "Please fill in the fixed instructions."; return; }
  for (const row of document.querySelectorAll("#resource-rows .res-row")) {
    const name = row.querySelector(".res-name").value.trim();
    const what = row.querySelector(".res-what").value.trim();
    if (!name && !what) continue;                    // an empty row is ignored
    if (!name) { message.textContent = "Every resource needs a name."; return; }
    body.resources.push({ name: name, description: what, required: row.querySelector(".res-required").value === "yes" });
  }

  const reply = editingToolId === null
    ? await api("POST", "/api/tools", body)
    : await api("PUT", "/api/tools/" + editingToolId, body);
  if (!reply.ok) { message.textContent = reply.message; return; }
  fillToolForm(null);
  await loadTools();
  byId("list-message").textContent = "Saved @" + reply.data.name + ".";
  setBuilderMode("list");
});

// The optional example code can be typed, pasted or loaded from a text file.
byId("example-load").addEventListener("click", () => byId("example-file").click());
byId("example-file").addEventListener("change", function () {
  const file = byId("example-file").files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function () { byId("tool-example").value = reader.result.slice(0, 12000); };
  reader.readAsText(file);
  byId("example-file").value = "";
});

// The "i" button: the tip shows on hover or keyboard focus (done in CSS).
// A click also keeps it open, which is how it works on touch screens.
byId("example-info").addEventListener("click", function (event) {
  event.preventDefault();                           // do not move focus to the text box
  byId("example-tip").classList.toggle("show");
});

byId("tool-cancel").addEventListener("click", function () {
  fillToolForm(null);
  setBuilderMode("list");
});

// ----- 9e. Your tools (list, search, use, edit, delete) -----
function drawToolList() {
  const list = byId("tool-list"), search = byId("tool-search").value.trim().toLowerCase();
  const shown = tools.filter(t => (t.name + " " + t.description).toLowerCase().includes(search));
  list.replaceChildren();
  if (shown.length === 0) {
    list.append(make("div", "empty", tools.length === 0
      ? "No tools yet. Click \"+ New tool\" to build your first one." : "No tool matches that search."));
  }
  shown.forEach(function (tool) {
    const card = make("div", "tool"), top = make("div", "tool-top"), meta = make("div", "tool-meta");
    const use = make("button", "small-btn go", "Use"), edit = make("button", "small-btn", "Edit");
    const remove = make("button", "row-del", "Delete");
    use.addEventListener("click", () => useTool(tool, ""));
    edit.addEventListener("click", function () { fillToolForm(tool); setBuilderMode("make"); });
    remove.addEventListener("click", async function () {
      const question = "Delete the tool @" + tool.name + "? Code it already built stays in History.";
      if (!(await confirmAction(question, "Delete tool"))) return;
      await api("DELETE", "/api/tools/" + tool.id);
      if (editingToolId === tool.id) fillToolForm(null);
      byId("list-message").textContent = "Deleted @" + tool.name + ".";
      loadTools();
    });
    top.append(make("span", "tool-name mono", "@" + tool.name), make("span", "tool-desc", tool.description), use, edit, remove);

    meta.append(make("span", "", tool.resources.length ? "Needs:" : "Needs no resources"));
    tool.resources.forEach(spec => meta.append(make("span", "tag", spec.name)));
    if (tool.example) meta.append(make("span", "pill ok", "Follows example format"));
    meta.append(make("span", "spacer"),
                make("span", "", tool.language + " · created " + new Date(tool.created_at).toLocaleDateString([], { dateStyle: "medium" })));
    card.append(top, meta);
    list.append(card);
  });
}
byId("tool-search").addEventListener("input", drawToolList);
byId("tool-new").addEventListener("click", function () { fillToolForm(null); setBuilderMode("make"); });

// ---------------------------------------------------------------------
// 10. What runs when the page loads
// ---------------------------------------------------------------------

fillToolForm(null);
drawChat();
checkServer();
refreshHistory();
loadTools();
