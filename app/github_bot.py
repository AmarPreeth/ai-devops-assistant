"""
github_bot.py - Stage 3: automatic code review on GitHub pull requests.

How it works:
  1. Someone opens a pull request (PR) on GitHub.
  2. GitHub sends our app an HTTP POST request describing the event.
     This automatic message is called a WEBHOOK.
  3. We check the message really came from GitHub (signature check).
  4. We download the PR's "diff" (the lines that were added/removed).
  5. We ask the AI to review the diff.
  6. We post the review as a comment on the PR.

Two secrets are needed (set as environment variables):
  GITHUB_WEBHOOK_SECRET - a password you invent and also type into GitHub.
  GITHUB_TOKEN          - a GitHub access token allowed to read PRs and
                          write comments on your repository.
"""

import hashlib
import hmac
import os

import httpx

from app import ai, database

GITHUB_API = "https://api.github.com"
MAX_DIFF_CHARS = 20000  # very large diffs are cut so the AI request stays small


def is_signature_valid(body: bytes, signature_header: str | None) -> bool:
    """
    GitHub signs every webhook with our shared secret using HMAC-SHA256
    and sends the result in the 'X-Hub-Signature-256' header.
    We calculate the same signature ourselves. If both match, the message
    is genuine. Without this check, anyone on the internet could send
    fake requests to our webhook address.
    """
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest compares safely (it does not leak timing information).
    return hmac.compare_digest(expected, signature_header)


def _headers(accept: str) -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN', '')}",
        "Accept": accept,
    }


def fetch_pr_diff(repo: str, pr_number: int) -> str:
    """Download the changed lines of a pull request as text."""
    response = httpx.get(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
        headers=_headers("application/vnd.github.diff"),
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def post_pr_comment(repo: str, pr_number: int, comment: str) -> None:
    """Write a comment on the pull request."""
    response = httpx.post(
        f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
        headers=_headers("application/vnd.github+json"),
        json={"body": comment},
        timeout=30,
    )
    response.raise_for_status()


def review_pull_request(repo: str, pr_number: int) -> None:
    """Steps 4-6: get the diff, ask the AI, post the comment, save to history."""
    try:
        diff = fetch_pr_diff(repo, pr_number)[:MAX_DIFF_CHARS]
        review, mode = ai.review_code(diff)
        post_pr_comment(repo, pr_number, f"### AI Code Review\n\n{review}")
        database.save_analysis("pull_request", f"{repo} PR #{pr_number}\n{diff}", review, mode)
    except (httpx.HTTPError, ai.AIError) as error:
        # This runs in the background, so we print the problem to the server
        # logs instead of crashing the app.
        print(f"Could not review {repo} PR #{pr_number}: {error}")
