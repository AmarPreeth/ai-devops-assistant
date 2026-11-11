"""
database.py - everything that touches the database lives here.

We use SQLite: a database that is just ONE FILE on disk.
Python has SQLite built in (the `sqlite3` module), so nothing extra
needs to be installed or run.

The database has two tables:
  `analyses` - one row for every result (log explanation, code review,
               pull request review or built code). This is the History.
  `tools`    - one row for every code-builder tool the user has created.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone


def get_db_path() -> str:
    """Where the database file is stored. Can be changed with DATABASE_PATH."""
    return os.getenv("DATABASE_PATH", "data/assistant.db")


def get_connection() -> sqlite3.Connection:
    """Open a connection to the database file."""
    path = get_db_path()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)  # create the folder if missing
    connection = sqlite3.connect(path)
    # Row lets us read columns by name (row["result"]) instead of by number.
    connection.row_factory = sqlite3.Row
    return connection


def create_table() -> None:
    """Create the table the first time the app starts. Safe to call again."""
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                kind        TEXT NOT NULL,   -- 'log', 'code', 'pull_request' or 'build'
                input_text  TEXT NOT NULL,   -- what the user sent
                result      TEXT NOT NULL,   -- what the AI answered
                mode        TEXT NOT NULL,   -- 'ai' or 'demo'
                created_at  TEXT NOT NULL    -- date and time (UTC)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tools (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE,  -- used as @name, no duplicates
                description  TEXT NOT NULL,         -- what the tool builds
                language     TEXT NOT NULL,         -- output language
                instructions TEXT NOT NULL,         -- fixed instructions
                resources    TEXT NOT NULL,         -- list of resource types, stored as JSON text
                example      TEXT NOT NULL DEFAULT '',  -- optional example code / code format
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
            """
        )
        # MIGRATION: a database created by an older version of the app has a
        # tools table without the "example" column. Add it if it is missing,
        # so existing tools are kept instead of being lost.
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(tools)")]
        if "example" not in columns:
            connection.execute("ALTER TABLE tools ADD COLUMN example TEXT NOT NULL DEFAULT ''")


def now_utc() -> str:
    """The current date and time in UTC as text, e.g. 2026-11-11T05:59:36+00:00."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_analysis(kind: str, input_text: str, result: str, mode: str) -> int:
    """Insert one row and return its id."""
    created_at = now_utc()
    with get_connection() as connection:
        # The ? marks are placeholders. SQLite fills them in safely,
        # which protects us from "SQL injection" attacks.
        cursor = connection.execute(
            "INSERT INTO analyses (kind, input_text, result, mode, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, input_text, result, mode, created_at),
        )
        return cursor.lastrowid


def list_analyses(limit: int = 50) -> list[dict]:
    """Return the newest analyses first (without the long texts)."""
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, kind, mode, created_at, substr(input_text, 1, 80) AS preview "
            "FROM analyses ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_analysis(analysis_id: int) -> dict | None:
    """Return one full analysis, or None if that id does not exist."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_analysis(analysis_id: int) -> bool:
    """Delete one saved analysis. Returns True if a row was deleted."""
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
        return cursor.rowcount > 0


def delete_all_analyses() -> int:
    """Delete every saved analysis. Returns how many rows were deleted."""
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM analyses")
        return cursor.rowcount


def count_analyses_between(start: str, end: str) -> int:
    """How many analyses were created between two UTC times (inclusive)."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM analyses WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        ).fetchone()
    return row[0]


def delete_analyses_between(start: str, end: str) -> int:
    """Delete the analyses created between two UTC times. Returns how many."""
    with get_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM analyses WHERE created_at >= ? AND created_at <= ?",
            (start, end),
        )
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Code-builder tools
# A tool's list of resource types is a Python list of dictionaries. SQLite
# has no "list" column type, so we store it as JSON text and convert back
# when reading.
# ---------------------------------------------------------------------------

def _tool_from_row(row: sqlite3.Row) -> dict:
    tool = dict(row)
    tool["resources"] = json.loads(tool["resources"])
    return tool


def list_tools() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM tools ORDER BY name").fetchall()
    return [_tool_from_row(row) for row in rows]


def get_tool(tool_id: int) -> dict | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
    return _tool_from_row(row) if row else None


def create_tool(name: str, description: str, language: str, instructions: str,
                resources: list[dict], example: str = "") -> int:
    """Insert a tool. Raises sqlite3.IntegrityError if the name is already used."""
    now = now_utc()
    with get_connection() as connection:
        cursor = connection.execute(
            "INSERT INTO tools (name, description, language, instructions, resources, example, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description, language, instructions, json.dumps(resources), example, now, now),
        )
        return cursor.lastrowid


def update_tool(tool_id: int, name: str, description: str, language: str, instructions: str,
                resources: list[dict], example: str = "") -> bool:
    """Change a tool. Returns False if that id does not exist."""
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE tools SET name = ?, description = ?, language = ?, instructions = ?, resources = ?, "
            "example = ?, updated_at = ? WHERE id = ?",
            (name, description, language, instructions, json.dumps(resources), example, now_utc(), tool_id),
        )
        return cursor.rowcount > 0


def delete_tool(tool_id: int) -> bool:
    with get_connection() as connection:
        cursor = connection.execute("DELETE FROM tools WHERE id = ?", (tool_id,))
        return cursor.rowcount > 0
