"""
main.py - the backend (the web server). This is the entry point of the app.

It is built with FastAPI. Each function below with an @app.get / @app.post
line above it is an ENDPOINT: an address the browser (or GitHub) can call.

    GET    /                      -> the web page
    GET    /health                -> "am I alive?" check used by cloud platforms

    POST   /api/analyze-log       -> explain logs
    POST   /api/review-code       -> review code
    POST   /api/chat              -> AI chat for general questions
    POST   /api/build             -> build code from a prompt

    GET    /api/tools             -> list the code-builder tools
    POST   /api/tools             -> create a tool
    GET    /api/tools/{id}        -> one tool
    PUT    /api/tools/{id}        -> edit a tool
    DELETE /api/tools/{id}        -> delete a tool
    POST   /api/tools/{id}/build  -> build code using a tool

    GET    /api/history           -> list of past results
    GET    /api/history/count     -> how many results are in a date range
    GET    /api/history/{id}      -> one past result in full
    DELETE /api/history/{id}      -> delete one past result
    DELETE /api/history           -> delete a date range, or everything

    POST   /webhook/github        -> GitHub pull request bot

Run locally with:   uvicorn app.main:app --reload
"""

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import ai, database, github_bot

# Read settings from a local ".env" file (if there is one) into
# environment variables. On the cloud we set them in the dashboard instead.
load_dotenv()

STATIC_FOLDER = Path(__file__).parent / "static"
MAX_INPUT_CHARS = 20000   # refuse very large inputs (cost and speed)
MAX_TOOL_CHARS = 24000    # instructions + resources of one tool run
CHAT_MEMORY = 12          # how many recent chat messages are sent to the AI


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Code here runs once when the server starts."""
    database.create_table()
    yield


app = FastAPI(title="AI DevOps Assistant", lifespan=lifespan)

# Serve style.css and app.js from the static folder at /static/...
app.mount("/static", StaticFiles(directory=STATIC_FOLDER), name="static")


# ---------------------------------------------------------------------------
# Shapes of the JSON the browser must send. FastAPI checks every request
# against these automatically and rejects anything that does not fit (422).
# ---------------------------------------------------------------------------

class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatInput(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)


class BuildInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)
    language: str = Field(default="Python", min_length=1, max_length=30)


class ResourceSpec(BaseModel):
    """One TYPE of resource a tool needs on every run, e.g. 'Call flow'."""
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    required: bool = True


class ToolInput(BaseModel):
    # The name becomes @name: lower-case letters, digits and underscores.
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,29}$")
    description: str = Field(min_length=1, max_length=200)
    language: str = Field(default="Python", min_length=1, max_length=30)
    instructions: str = Field(min_length=1, max_length=8000)
    resources: list[ResourceSpec] = Field(default=[], max_length=10)
    # Optional example code / code format. Empty = use a general format.
    example: str = Field(default="", max_length=12000)


class ToolBuildInput(BaseModel):
    resources: dict[str, str] = {}          # {"Call flow": "text...", ...}
    extra: str = Field(default="", max_length=4000)


# ---------------------------------------------------------------------------
# Pages and health
# ---------------------------------------------------------------------------

@app.get("/")
def home_page():
    return FileResponse(STATIC_FOLDER / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "ai_configured": ai.is_ai_configured()}


# ---------------------------------------------------------------------------
# Explain logs and review code
# ---------------------------------------------------------------------------

def run_and_save(kind: str, input_text: str, ask_ai) -> dict:
    """Shared steps: ask the AI, save the result to History, return it."""
    try:
        result, mode = ask_ai()
    except ai.AIError as error:
        # 502 = "the service I depend on failed" (here: the AI provider).
        raise HTTPException(status_code=502, detail=str(error))
    analysis_id = database.save_analysis(kind, input_text, result, mode)
    return {"id": analysis_id, "kind": kind, "mode": mode, "result": result}


@app.post("/api/analyze-log")
def analyze_log(body: TextInput):
    return run_and_save("log", body.text, lambda: ai.explain_log(body.text))


@app.post("/api/review-code")
def review_code(body: TextInput):
    return run_and_save("code", body.text, lambda: ai.review_code(body.text))


# ---------------------------------------------------------------------------
# AI chat (not saved to History)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
def chat(body: ChatInput):
    # Only the most recent messages are sent, to keep the request small.
    recent = [message.model_dump() for message in body.messages[-CHAT_MEMORY:]]
    try:
        reply, mode = ai.chat(recent)
    except ai.AIError as error:
        raise HTTPException(status_code=502, detail=str(error))
    return {"reply": reply, "mode": mode}


# ---------------------------------------------------------------------------
# Code builder: from a prompt
# ---------------------------------------------------------------------------

@app.post("/api/build")
def build_from_prompt(body: BuildInput):
    return run_and_save("build", body.prompt, lambda: ai.build_code(body.prompt, body.language))


# ---------------------------------------------------------------------------
# Code builder: tools (create, read, update, delete = "CRUD")
# ---------------------------------------------------------------------------

def check_resource_names(tool: ToolInput) -> list[dict]:
    names = [resource.name.strip().lower() for resource in tool.resources]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="Two resources have the same name.")
    return [resource.model_dump() for resource in tool.resources]


@app.get("/api/tools")
def list_tools():
    return database.list_tools()


@app.post("/api/tools", status_code=201)
def create_tool(body: ToolInput):
    resources = check_resource_names(body)
    try:
        tool_id = database.create_tool(body.name, body.description, body.language,
                                       body.instructions, resources, body.example)
    except sqlite3.IntegrityError:
        # 409 = conflict: the name column is UNIQUE in the database.
        raise HTTPException(status_code=409, detail=f"A tool named @{body.name} already exists.")
    return database.get_tool(tool_id)


@app.get("/api/tools/{tool_id}")
def get_tool(tool_id: int):
    tool = database.get_tool(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="No tool with that id.")
    return tool


@app.put("/api/tools/{tool_id}")
def update_tool(tool_id: int, body: ToolInput):
    resources = check_resource_names(body)
    try:
        found = database.update_tool(tool_id, body.name, body.description, body.language,
                                     body.instructions, resources, body.example)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail=f"A tool named @{body.name} already exists.")
    if not found:
        raise HTTPException(status_code=404, detail="No tool with that id.")
    return database.get_tool(tool_id)


@app.delete("/api/tools/{tool_id}")
def delete_tool(tool_id: int):
    if not database.delete_tool(tool_id):
        raise HTTPException(status_code=404, detail="No tool with that id.")
    return {"deleted": 1}


@app.post("/api/tools/{tool_id}/build")
def build_with_tool(tool_id: int, body: ToolBuildInput):
    """Build code using a saved tool plus the resources given for this run."""
    tool = get_tool(tool_id)

    # Every resource the tool marks as required must have some text.
    missing = [spec["name"] for spec in tool["resources"]
               if spec["required"] and not body.resources.get(spec["name"], "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail="Missing required resources: " + ", ".join(missing))

    request_text = ai.compose_tool_request(tool, body.resources, body.extra)
    if len(request_text) > MAX_TOOL_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"The instructions and resources add up to {len(request_text):,} characters. "
                   f"The limit is {MAX_TOOL_CHARS:,}. Shorten a resource and try again.",
        )
    saved_input = f"@{tool['name']} {body.extra.strip()}".strip() + "\n\n" + request_text
    result = run_and_save("build", saved_input, lambda: ai.build_code(request_text, tool["language"]))
    result["tool"] = tool["name"]
    return result


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def to_utc_text(value: str) -> str:
    """Turn a date-time sent by the browser into the same UTC text format
    the database uses, so the two can be compared."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be in ISO format.")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


@app.get("/api/history")
def history():
    return database.list_analyses()


# This must come BEFORE /api/history/{analysis_id}, otherwise FastAPI would
# try to read the word "count" as an id.
@app.get("/api/history/count")
def history_count(start: str, end: str):
    return {"count": database.count_analyses_between(to_utc_text(start), to_utc_text(end))}


@app.get("/api/history/{analysis_id}")
def history_item(analysis_id: int):
    item = database.get_analysis(analysis_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No analysis with that id.")
    return item


@app.delete("/api/history/{analysis_id}")
def delete_history_item(analysis_id: int):
    if not database.delete_analysis(analysis_id):
        raise HTTPException(status_code=404, detail="No analysis with that id.")
    return {"deleted": 1}


@app.delete("/api/history")
def delete_history(start: str | None = None, end: str | None = None):
    """With ?start=...&end=... delete that date range. With neither, delete all."""
    if start is None and end is None:
        return {"deleted": database.delete_all_analyses()}
    if start is None or end is None:
        raise HTTPException(status_code=400, detail="Give both start and end, or neither.")
    return {"deleted": database.delete_analyses_between(to_utc_text(start), to_utc_text(end))}


# ---------------------------------------------------------------------------
# GitHub pull request bot
# ---------------------------------------------------------------------------

@app.post("/webhook/github")
async def github_webhook(request: Request, background_tasks: BackgroundTasks):
    """GitHub calls this address whenever something happens to a pull request."""
    body = await request.body()

    # 1. Security: reject anything not signed with our shared secret.
    signature = request.headers.get("X-Hub-Signature-256")
    if not github_bot.is_signature_valid(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    # 2. We only care about pull requests being opened or updated.
    event = request.headers.get("X-GitHub-Event")
    payload = json.loads(body)
    if event != "pull_request" or payload.get("action") not in ("opened", "reopened", "synchronize"):
        return {"status": "ignored"}

    # 3. Do the slow work (AI call) in the background and answer GitHub
    #    immediately, because GitHub only waits a few seconds for a reply.
    repo = payload["repository"]["full_name"]
    pr_number = payload["pull_request"]["number"]
    background_tasks.add_task(github_bot.review_pull_request, repo, pr_number)
    return {"status": "review started", "repo": repo, "pull_request": pr_number}
