# AI DevOps Assistant

A web app that helps a developer with AI at four moments:

1. **Explain logs** - paste error logs, get a plain-English explanation and fix.
2. **Review code** - paste code, get bugs, risks and improvements.
3. **Build code** - the code builder:
   - *Build code using prompt*: describe what you need and get code.
   - *Build tool*: save fixed instructions plus the types of resources a
     repeated job needs (for example `@pre_call`).
   - *Build code using tool*: pick a tool, fill in its resources, get code.
   - *Your tools*: search, edit and delete tools.
4. **AI chat** - ask general questions in the box on the right.

Also included: a **GitHub bot** that reviews new pull requests automatically,
and a **History** tab where every result is saved. History rows can be deleted
one by one, by date range, or all at once. Anything that deletes data asks
"Are you sure?" first.

## Project layout

```
app/main.py            the web server and all endpoints (start reading here)
app/ai.py              prompts, the AI API call, and demo mode
app/database.py        saving and reading history and tools (SQLite)
app/github_bot.py      the pull request bot (webhook)
app/static/index.html  the web page: what is on it
app/static/style.css   the web page: how it looks
app/static/app.js      the web page: what it does
tests/test_app.py      automated tests
Dockerfile             recipe for packaging the app as a container
.github/workflows/ci.yml   CI pipeline (tests + Docker build on every push)
.env.example           the settings the app reads
sample_data/           a sample log and a sample buggy code file for demos
```

## 1. Run it on your computer

You need Python 3.11 or newer.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 and paste the contents of `sample_data/sample_error.log`.

Without an API key the app runs in **demo mode**: answers come from simple
built-in rules and are labelled as such. This is so you can always run it.

## 2. Turn on the real AI

1. Create a free API key at an OpenAI-compatible provider (for example Groq:
   https://console.groq.com).
2. Copy `.env.example` to `.env` and put your key in `LLM_API_KEY`.
3. Set `LLM_MODEL` to a model your key can use, for example
   `openai/gpt-oss-120b` on Groq (model names change over time, so check the
   provider's list).
4. Restart the server.

`http://127.0.0.1:8000/health` shows `"ai_configured": true` when the key is read.

## 3. Run the tests

```bash
pytest -v
```

## 4. Run it in Docker

```bash
docker build -t ai-devops-assistant .
docker run -p 8000:8000 --env-file .env ai-devops-assistant
```

## 5. Put it on the cloud (Render, the simplest option)

1. Push this folder to a new GitHub repository.
2. On https://render.com create a **Web Service** from that repository.
   Render finds the `Dockerfile` and builds it.
3. Add the environment variables from `.env.example` in the Render dashboard.
4. Render gives you a public URL. Each push to `main` redeploys the app.

The same Docker image also runs on Google Cloud Run or an AWS EC2 machine.

## 6. Turn on the GitHub bot (optional, needs the app on a public URL)

1. Invent a secret word and set it as `GITHUB_WEBHOOK_SECRET` on the cloud.
2. Create a GitHub fine-grained personal access token for your repository with
   **Pull requests: Read and write** permission. Set it as `GITHUB_TOKEN`.
3. In the repository: Settings -> Webhooks -> Add webhook
   - Payload URL: `https://YOUR-APP-URL/webhook/github`
   - Content type: `application/json`
   - Secret: the same secret word
   - Events: "Let me select individual events" -> **Pull requests**
4. Open a pull request. A comment titled "AI Code Review" should appear.
