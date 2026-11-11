"""
Automated tests. Run them with:   pytest

A test calls the app the same way a browser would and checks the answer.
The CI pipeline runs these on every push, so broken code is caught
before it is deployed.

No real AI or GitHub call is made here: tests must be free, fast and
work without internet. Where needed we replace the network function
with a fake one ("monkeypatching").
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import ai, github_bot
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test browser, using a fresh empty database and no API key."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "AI DevOps Assistant" in response.text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "ai_configured": False}


def test_log_analysis_in_demo_mode(client):
    response = client.post("/api/analyze-log", json={"text": "INFO start\nERROR db connection refused"})
    data = response.json()
    assert response.status_code == 200
    assert data["mode"] == "demo"
    assert "db connection refused" in data["result"]


def test_code_review_in_demo_mode(client):
    response = client.post("/api/review-code", json={"text": "try:\n    x = eval(y)\nexcept:\n    pass"})
    data = response.json()
    assert data["mode"] == "demo"
    assert "eval()" in data["result"]


def test_empty_input_is_rejected(client):
    assert client.post("/api/analyze-log", json={"text": ""}).status_code == 422


def test_too_long_input_is_rejected(client):
    assert client.post("/api/review-code", json={"text": "x" * 20001}).status_code == 422


def test_history_saves_and_returns_items(client):
    client.post("/api/analyze-log", json={"text": "ERROR one"})
    client.post("/api/review-code", json={"text": "print('two')"})
    items = client.get("/api/history").json()
    assert [item["kind"] for item in items] == ["code", "log"]  # newest first
    full = client.get(f"/api/history/{items[0]['id']}").json()
    assert full["input_text"] == "print('two')"
    assert client.get("/api/history/999").status_code == 404


def test_delete_one_and_delete_all(client):
    for text in ("ERROR one", "ERROR two", "ERROR three"):
        client.post("/api/analyze-log", json={"text": text})
    assert client.delete("/api/history/2").json() == {"deleted": 1}
    assert [item["id"] for item in client.get("/api/history").json()] == [3, 1]
    assert client.delete("/api/history/2").status_code == 404   # already gone
    assert client.delete("/api/history").json() == {"deleted": 2}
    assert client.get("/api/history").json() == []


def test_real_ai_path_with_fake_provider(client, monkeypatch):
    """With a key set, the app must call the provider and return its answer."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    sent = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "WHAT HAPPENED\nfake answer"}}]}

    def fake_post(url, headers, json, timeout):
        sent["url"], sent["headers"], sent["json"] = url, headers, json
        return FakeResponse()

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    data = client.post("/api/analyze-log", json={"text": "ERROR boom"}).json()
    assert data == {"id": 1, "kind": "log", "mode": "ai", "result": "WHAT HAPPENED\nfake answer"}
    assert sent["url"].endswith("/chat/completions")
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["json"]["messages"][1] == {"role": "user", "content": "ERROR boom"}


def test_ai_provider_failure_gives_502(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def failing_post(*args, **kwargs):
        raise ai.httpx.ConnectError("no internet")

    monkeypatch.setattr(ai.httpx, "post", failing_post)
    response = client.post("/api/analyze-log", json={"text": "ERROR boom"})
    assert response.status_code == 502


# ----- GitHub webhook -----

def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


PR_EVENT = json.dumps({
    "action": "opened",
    "repository": {"full_name": "student/demo-repo"},
    "pull_request": {"number": 7},
}).encode()


def test_webhook_rejects_wrong_signature(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "right-secret")
    response = client.post(
        "/webhook/github", content=PR_EVENT,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(PR_EVENT, "wrong-secret")},
    )
    assert response.status_code == 401


def test_webhook_rejects_when_no_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    response = client.post(
        "/webhook/github", content=PR_EVENT,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(PR_EVENT, "")},
    )
    assert response.status_code == 401


def test_webhook_ignores_other_events(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    body = b'{"zen": "hello"}'
    response = client.post(
        "/webhook/github", content=body,
        headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": sign(body, "s3cret")},
    )
    assert response.json() == {"status": "ignored"}


def test_webhook_reviews_pull_request_and_comments(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret")
    posted = {}
    monkeypatch.setattr(github_bot, "fetch_pr_diff", lambda repo, number: "+ password = '1234'")
    monkeypatch.setattr(
        github_bot, "post_pr_comment",
        lambda repo, number, comment: posted.update(repo=repo, number=number, comment=comment),
    )
    response = client.post(
        "/webhook/github", content=PR_EVENT,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": sign(PR_EVENT, "s3cret")},
    )
    assert response.json()["status"] == "review started"
    # TestClient runs the background task before returning, so we can check it.
    assert posted["repo"] == "student/demo-repo" and posted["number"] == 7
    assert "AI Code Review" in posted["comment"] and "secret" in posted["comment"]
    assert client.get("/api/history").json()[0]["kind"] == "pull_request"


# ----- AI chat -----

def test_chat_in_demo_mode(client):
    data = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).json()
    assert data["mode"] == "demo" and "AI key" in data["reply"]


def test_chat_sends_the_conversation_to_the_provider(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    seen = {}
    monkeypatch.setattr(ai, "ask_llm_messages", lambda messages: seen.update(messages=messages) or "A webhook is...")
    conversation = [
        {"role": "user", "content": "What is a webhook?"},
        {"role": "assistant", "content": "A message between apps."},
        {"role": "user", "content": "Why sign it?"},
    ]
    data = client.post("/api/chat", json={"messages": conversation}).json()
    assert data == {"reply": "A webhook is...", "mode": "ai"}
    assert seen["messages"][0]["role"] == "system"          # our chat prompt comes first
    assert seen["messages"][1:] == conversation             # then the whole conversation
    assert client.get("/api/history").json() == []          # chat is not saved to History


def test_chat_rejects_bad_roles(client):
    assert client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422


# ----- Code builder: prompt -----

def test_build_from_prompt_is_saved_in_history(client):
    data = client.post("/api/build", json={"prompt": "add two numbers", "language": "Go"}).json()
    assert data["kind"] == "build" and data["mode"] == "demo" and "Go" in data["result"]
    assert client.get("/api/history").json()[0]["kind"] == "build"


# ----- Code builder: tools -----

PRE_CALL = {
    "name": "pre_call",
    "description": "Pre-call setup code for a voice bot",
    "language": "Python",
    "instructions": "Write a pre_call(lead) function.",
    "resources": [
        {"name": "Call flow", "description": "the steps of the call", "required": True},
        {"name": "Notes", "description": "", "required": False},
    ],
}


def test_tool_create_list_edit_delete(client):
    created = client.post("/api/tools", json=PRE_CALL)
    assert created.status_code == 201
    tool_id = created.json()["id"]
    assert client.get("/api/tools").json()[0]["resources"][0]["name"] == "Call flow"

    # The same name twice is refused.
    assert client.post("/api/tools", json=PRE_CALL).status_code == 409
    # Names must be lower-case letters, digits and underscores.
    assert client.post("/api/tools", json={**PRE_CALL, "name": "Pre Call"}).status_code == 422
    # Two resources with the same name are refused.
    twice = {**PRE_CALL, "name": "other", "resources": [PRE_CALL["resources"][0]] * 2}
    assert client.post("/api/tools", json=twice).status_code == 400

    edited = client.put(f"/api/tools/{tool_id}", json={**PRE_CALL, "description": "Changed"}).json()
    assert edited["description"] == "Changed"
    assert client.put("/api/tools/999", json=PRE_CALL).status_code == 404

    assert client.delete(f"/api/tools/{tool_id}").json() == {"deleted": 1}
    assert client.get(f"/api/tools/{tool_id}").status_code == 404


def test_build_with_tool(client, monkeypatch):
    tool_id = client.post("/api/tools", json=PRE_CALL).json()["id"]

    # A required resource is missing.
    response = client.post(f"/api/tools/{tool_id}/build", json={"resources": {"Notes": "x"}})
    assert response.status_code == 400 and "Call flow" in response.json()["detail"]

    # Too much text for one request.
    response = client.post(f"/api/tools/{tool_id}/build", json={"resources": {"Call flow": "x" * 25000}})
    assert response.status_code == 400 and "limit" in response.json()["detail"]

    # A good run: the AI receives the instructions, the resources and the extra note.
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    seen = {}
    monkeypatch.setattr(ai, "ask_llm", lambda system, user: seen.update(system=system, user=user) or "```\ncode\n```\nEXPLANATION\nok")
    data = client.post(
        f"/api/tools/{tool_id}/build",
        json={"resources": {"Call flow": "greet, verify, close"}, "extra": "client is ABC"},
    ).json()
    assert data["tool"] == "pre_call" and data["kind"] == "build" and data["mode"] == "ai"
    assert "Python" in seen["system"]
    for expected in ("Write a pre_call(lead) function.", "RESOURCE: Call flow (the steps of the call)",
                     "greet, verify, close", "client is ABC"):
        assert expected in seen["user"]
    assert "RESOURCE: Notes" not in seen["user"]            # empty optional resource is left out
    assert "CODE FORMAT TO FOLLOW" not in seen["user"]      # no example saved = general format

    # With example code saved in the tool, the AI is told to follow its format.
    client.put(f"/api/tools/{tool_id}", json={**PRE_CALL, "example": "def pre_call(lead):\n    return {}"})
    assert client.get(f"/api/tools/{tool_id}").json()["example"].startswith("def pre_call")
    client.post(f"/api/tools/{tool_id}/build", json={"resources": {"Call flow": "greet"}})
    assert "CODE FORMAT TO FOLLOW" in seen["user"] and "def pre_call(lead):" in seen["user"]
    # Both runs were saved; the first one (now second in the list) had the extra note.
    assert client.get("/api/history").json()[1]["preview"].startswith("@pre_call client is ABC")


# ----- History: delete by date -----

def test_count_and_delete_by_date_range(client, monkeypatch):
    from app import database
    times = iter(["2026-11-01T10:00:00+00:00", "2026-11-05T10:00:00+00:00", "2026-11-09T10:00:00+00:00"])
    monkeypatch.setattr(database, "now_utc", lambda: next(times))
    for text in ("one", "two", "three"):
        client.post("/api/analyze-log", json={"text": text})

    # The browser sends times like 2026-11-02T00:00:00.000Z
    window = {"start": "2026-11-02T00:00:00.000Z", "end": "2026-11-09T09:59:59.000Z"}
    assert client.get("/api/history/count", params=window).json() == {"count": 1}
    assert client.request("DELETE", "/api/history", params=window).json() == {"deleted": 1}
    assert [item["id"] for item in client.get("/api/history").json()] == [3, 1]

    assert client.request("DELETE", "/api/history", params={"start": window["start"]}).status_code == 400
    assert client.get("/api/history/count", params={"start": "junk", "end": "junk"}).status_code == 400


def test_old_database_without_example_column_is_upgraded(tmp_path, monkeypatch):
    """A tools table made by an older version must keep its tools and gain the new column."""
    import sqlite3
    from app import database
    path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE tools (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
                "description TEXT NOT NULL, language TEXT NOT NULL, instructions TEXT NOT NULL, "
                "resources TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    old.execute("INSERT INTO tools (name, description, language, instructions, resources, created_at, updated_at) "
                "VALUES ('old_tool', 'd', 'Python', 'i', '[]', 't', 't')")
    old.commit()
    old.close()
    database.create_table()
    database.create_table()                                  # running it twice is safe
    tool = database.list_tools()[0]
    assert tool["name"] == "old_tool" and tool["example"] == ""
