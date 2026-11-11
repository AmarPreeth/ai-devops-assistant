"""
ai.py - the "AI part" of the project.

This file does three things:
  1. Holds the PROMPTS (the instructions we give the AI model).
  2. Sends the user's text to the AI model over the internet (an API call).
  3. Provides a DEMO MODE that works without any API key, so the app
     can always be shown even if there is no internet or no key.

We do NOT train a model. We use an existing Large Language Model (LLM)
through its API. The API follows the "OpenAI-compatible chat completions"
format, which many providers support (Groq, Google Gemini, OpenAI...).
That means we can switch provider by changing 3 settings, not the code.
"""

import os

import httpx

# ---------------------------------------------------------------------------
# 1. PROMPTS
# A "system prompt" tells the model what role to play and what format
# to answer in. Asking for fixed headings makes every answer look the same.
# ---------------------------------------------------------------------------

LOG_PROMPT = """You are a senior DevOps engineer helping a beginner.
You will receive application logs or an error message.
Answer using exactly these four headings, in plain text:

WHAT HAPPENED
(2-3 simple sentences)

LIKELY ROOT CAUSE
(the most probable cause, and which log line shows it)

HOW TO FIX IT
(numbered steps)

HOW TO PREVENT IT
(1-3 short points)

Only use information found in the logs. If the logs do not contain enough
information, say so instead of guessing."""

CODE_PROMPT = """You are a careful senior software engineer doing a code review.
You will receive source code or a code diff.
Answer using exactly these four headings, in plain text:

SUMMARY
(what the code does, 1-2 sentences)

BUGS AND RISKS
(real problems: bugs, security issues, crashes. Mention the line or function.)

IMPROVEMENTS
(readability, naming, performance suggestions)

VERDICT
(one of: LOOKS GOOD / NEEDS SMALL CHANGES / NEEDS MAJOR CHANGES, with one reason)

Be specific and polite. If you find no bugs, say so. Do not invent problems."""


CHAT_PROMPT = """You are a friendly assistant inside a developer tool called AI DevOps Assistant.
Answer general questions about programming, DevOps, cloud, deployment and AI.
Explain simply, as if to a beginner, and keep answers short (a few sentences,
or a short list). Use plain text. If you are not sure, say so."""

BUILD_PROMPT = """You are a senior software engineer. Write {language} code for the request.
Reply in exactly this format:
1. First, the complete code inside ONE fenced code block (three backticks).
2. Then a line containing only the word EXPLANATION.
3. Then 2-4 short sentences explaining what the code does and any assumption you made.

Follow every instruction and use every resource you are given. Do not invent
requirements. If something essential is missing, say so in the explanation."""


class AIError(Exception):
    """Raised when the AI provider cannot be reached or returns an error."""


# ---------------------------------------------------------------------------
# 2. CALLING THE REAL AI MODEL
# ---------------------------------------------------------------------------

def ask_llm(system_prompt: str, user_text: str) -> str:
    """Send one question to the LLM and return its answer as text."""
    return ask_llm_messages([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ])


def ask_llm_messages(messages: list[dict]) -> str:
    """Send a whole conversation (a list of messages) to the LLM.
    Each message is {"role": "system" | "user" | "assistant", "content": "..."}."""
    # Settings come from environment variables, never from the code itself,
    # so the secret API key is never uploaded to GitHub.
    api_key = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                # Low temperature = less random, more consistent answers.
                "temperature": 0.2,
            },
            timeout=60,
        )
        response.raise_for_status()  # turns 4xx/5xx replies into an error
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except httpx.HTTPStatusError as error:
        raise AIError(
            f"The AI provider replied with error {error.response.status_code}. "
            "Check LLM_API_KEY, LLM_BASE_URL and LLM_MODEL."
        ) from error
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as error:
        raise AIError(f"Could not get an answer from the AI provider: {error}") from error


# ---------------------------------------------------------------------------
# 3. DEMO MODE (no API key needed)
# These functions do NOT use AI. They use simple rules so the whole app
# can still be run and demonstrated. The answer is clearly labelled.
# ---------------------------------------------------------------------------

DEMO_NOTE = (
    "[DEMO MODE - no AI key is configured, so this answer comes from simple "
    "built-in rules, not from an AI model. Set LLM_API_KEY to get real analysis.]"
)


def demo_log_answer(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    keywords = ("error", "exception", "critical", "fatal", "traceback", "failed")
    problem_lines = [line for line in lines if any(k in line.lower() for k in keywords)]
    last_problem = problem_lines[-1].strip() if problem_lines else "No obvious error line found."
    return (
        f"{DEMO_NOTE}\n\n"
        "WHAT HAPPENED\n"
        f"The log has {len(lines)} lines and {len(problem_lines)} of them look like errors.\n\n"
        "LIKELY ROOT CAUSE\n"
        f"The last error-looking line is:\n  {last_problem[:300]}\n\n"
        "HOW TO FIX IT\n"
        "1. Read the line above and the few lines before it.\n"
        "2. Search the error message in the project code or documentation.\n\n"
        "HOW TO PREVENT IT\n"
        "- Add a real AI key to get a proper explanation."
    )


def demo_code_answer(text: str) -> str:
    lines = text.splitlines()
    notes = []
    if "password" in text.lower() or "api_key" in text.lower() or "secret" in text.lower():
        notes.append("- The code mentions a password/key/secret. Never hard-code secrets.")
    if "except:" in text:
        notes.append("- A bare 'except:' hides every error. Catch a specific exception.")
    if "eval(" in text:
        notes.append("- eval() runs any text as code, which is a security risk.")
    if "TODO" in text:
        notes.append("- There is a TODO left in the code.")
    if not notes:
        notes.append("- The simple built-in rules found nothing.")
    return (
        f"{DEMO_NOTE}\n\n"
        "SUMMARY\n"
        f"The submitted code has {len(lines)} lines.\n\n"
        "BUGS AND RISKS\n" + "\n".join(notes) + "\n\n"
        "IMPROVEMENTS\n"
        "- Add a real AI key to get detailed suggestions.\n\n"
        "VERDICT\n"
        "NOT REVIEWED BY AI (demo mode)."
    )


def demo_build_answer(language: str) -> str:
    return (
        "```\n"
        f"# DEMO MODE: no AI key is configured, so no {language} code was generated.\n"
        "# Set LLM_API_KEY in the .env file and restart the server.\n"
        "```\n"
        "EXPLANATION\n"
        f"{DEMO_NOTE}"
    )


def compose_tool_request(tool: dict, resources: dict[str, str], extra: str) -> str:
    """Join a tool's fixed instructions with the resources given for this run
    into one text. This is what gets sent to the AI (and saved in History)."""
    parts = [
        f"TOOL: @{tool['name']} - {tool['description']}",
        "FIXED INSTRUCTIONS:\n" + tool["instructions"],
    ]
    # Optional: if the tool has example code, the new code must copy its format.
    # With no example, the AI is free to use a standard format.
    if tool.get("example", "").strip():
        parts.append(
            "CODE FORMAT TO FOLLOW:\n"
            "The example below shows the required format. Write the new code with the same "
            "structure, naming style, comments style and layout. Copy the FORMAT only, "
            "not the example's specific values.\n" + tool["example"].strip()
        )
    for spec in tool["resources"]:
        text = resources.get(spec["name"], "").strip()
        if text:
            title = f"RESOURCE: {spec['name']}"
            if spec.get("description"):
                title += f" ({spec['description']})"
            parts.append(title + "\n" + text)
    if extra.strip():
        parts.append("EXTRA INSTRUCTIONS FOR THIS RUN:\n" + extra.strip())
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# The functions the rest of the app calls.
# Each returns (answer_text, mode) where mode is "ai" or "demo".
# ---------------------------------------------------------------------------

def is_ai_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def explain_log(log_text: str) -> tuple[str, str]:
    if not is_ai_configured():
        return demo_log_answer(log_text), "demo"
    return ask_llm(LOG_PROMPT, log_text), "ai"


def review_code(code_text: str) -> tuple[str, str]:
    if not is_ai_configured():
        return demo_code_answer(code_text), "demo"
    return ask_llm(CODE_PROMPT, code_text), "ai"


def chat(messages: list[dict]) -> tuple[str, str]:
    """General question-and-answer chat. `messages` is the conversation so far."""
    if not is_ai_configured():
        return "Chat needs a real AI key. " + DEMO_NOTE, "demo"
    return ask_llm_messages([{"role": "system", "content": CHAT_PROMPT}] + messages), "ai"


def build_code(request_text: str, language: str) -> tuple[str, str]:
    """Write code from a prompt, or from a composed tool request."""
    if not is_ai_configured():
        return demo_build_answer(language), "demo"
    return ask_llm(BUILD_PROMPT.format(language=language), request_text), "ai"
