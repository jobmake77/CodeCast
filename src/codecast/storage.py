from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_DB_PATH = Path.home() / ".codecast" / "codecast.db"

STYLE_VALUES = ("formal", "friendly", "punchy")
STATUS_PENDING = "PENDING"
STATUS_FAILED = "FAILED"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_ARCHIVED = "ARCHIVED"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_db_path(db_path: str | None = None) -> Path:
    env_path = os.getenv("CODECAST_DB_PATH")
    final_path = Path(db_path) if db_path else Path(env_path) if env_path else DEFAULT_DB_PATH
    final_path.parent.mkdir(parents=True, exist_ok=True)
    return final_path


def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(resolve_db_path(db_path)))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS repo_settings (
            repo_id INTEGER PRIMARY KEY,
            every_n_pushes INTEGER NOT NULL DEFAULT 1,
            publish_enabled INTEGER NOT NULL DEFAULT 1,
            require_confirm INTEGER NOT NULL DEFAULT 1,
            default_style TEXT NOT NULL DEFAULT 'formal',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS push_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            oldrev TEXT NOT NULL,
            newrev TEXT NOT NULL,
            commit_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            aggregated_draft_id INTEGER,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS commits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            push_event_id INTEGER NOT NULL,
            sha TEXT NOT NULL,
            subject TEXT NOT NULL,
            author TEXT,
            committed_at TEXT,
            files_changed INTEGER NOT NULL DEFAULT 0,
            insertions INTEGER NOT NULL DEFAULT 0,
            deletions INTEGER NOT NULL DEFAULT 0,
            UNIQUE(push_event_id, sha),
            FOREIGN KEY(push_event_id) REFERENCES push_events(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER,
            status TEXT NOT NULL,
            style TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            publish_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            archived_at TEXT,
            FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS draft_push_events (
            draft_id INTEGER NOT NULL,
            push_event_id INTEGER NOT NULL UNIQUE,
            PRIMARY KEY(draft_id, push_event_id),
            FOREIGN KEY(draft_id) REFERENCES drafts(id) ON DELETE CASCADE,
            FOREIGN KEY(push_event_id) REFERENCES push_events(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS publish_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER NOT NULL,
            attempted_at TEXT NOT NULL,
            command TEXT NOT NULL,
            return_code INTEGER NOT NULL,
            stdout TEXT NOT NULL,
            stderr TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(draft_id) REFERENCES drafts(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def ensure_repo(conn: sqlite3.Connection, repo_path: str) -> int:
    normalized = str(Path(repo_path).resolve())
    name = Path(normalized).name
    now = now_iso()
    cur = conn.execute("SELECT id FROM repos WHERE path = ?", (normalized,))
    row = cur.fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO repos(path, name, created_at) VALUES (?, ?, ?)",
        (normalized, name, now),
    )
    repo_id = int(cur.lastrowid)
    conn.execute(
        """INSERT INTO repo_settings(repo_id, every_n_pushes, publish_enabled, require_confirm, default_style, updated_at)
           VALUES (?, 1, 1, 1, 'formal', ?)""",
        (repo_id, now),
    )
    conn.commit()
    return repo_id


def get_repo_id(conn: sqlite3.Connection, repo_path: str) -> int | None:
    normalized = str(Path(repo_path).resolve())
    row = conn.execute("SELECT id FROM repos WHERE path = ?", (normalized,)).fetchone()
    return int(row["id"]) if row else None


@dataclass
class RepoSettings:
    every_n_pushes: int
    publish_enabled: bool
    require_confirm: bool
    default_style: str


def get_repo_settings(conn: sqlite3.Connection, repo_id: int) -> RepoSettings:
    row = conn.execute(
        "SELECT every_n_pushes, publish_enabled, require_confirm, default_style FROM repo_settings WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    if not row:
        return RepoSettings(1, True, True, "formal")
    return RepoSettings(
        every_n_pushes=max(1, int(row["every_n_pushes"])),
        publish_enabled=bool(row["publish_enabled"]),
        require_confirm=bool(row["require_confirm"]),
        default_style=row["default_style"] if row["default_style"] in STYLE_VALUES else "formal",
    )


def update_repo_settings(
    conn: sqlite3.Connection,
    repo_id: int,
    every_n_pushes: int | None = None,
    publish_enabled: bool | None = None,
    default_style: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[object] = []
    if every_n_pushes is not None:
        fields.append("every_n_pushes = ?")
        values.append(max(1, int(every_n_pushes)))
    if publish_enabled is not None:
        fields.append("publish_enabled = ?")
        values.append(1 if publish_enabled else 0)
    if default_style is not None:
        fields.append("default_style = ?")
        values.append(default_style if default_style in STYLE_VALUES else "formal")
    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(repo_id)
    conn.execute(f"UPDATE repo_settings SET {', '.join(fields)} WHERE repo_id = ?", values)
    conn.commit()


def create_push_event(
    conn: sqlite3.Connection,
    repo_id: int,
    oldrev: str,
    newrev: str,
    commits: Iterable[dict],
) -> int:
    commits_list = list(commits)
    cur = conn.execute(
        """INSERT INTO push_events(repo_id, oldrev, newrev, commit_count, created_at, aggregated_draft_id)
           VALUES (?, ?, ?, ?, ?, NULL)""",
        (repo_id, oldrev, newrev, len(commits_list), now_iso()),
    )
    event_id = int(cur.lastrowid)
    for item in commits_list:
        conn.execute(
            """INSERT OR IGNORE INTO commits(
               push_event_id, sha, subject, author, committed_at, files_changed, insertions, deletions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                item["sha"],
                item["subject"],
                item.get("author"),
                item.get("committed_at"),
                int(item.get("files_changed", 0)),
                int(item.get("insertions", 0)),
                int(item.get("deletions", 0)),
            ),
        )
    conn.commit()
    return event_id


def find_unaggregated_events(conn: sqlite3.Connection, repo_id: int, limit: int | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT id, oldrev, newrev, commit_count, created_at
        FROM push_events
        WHERE repo_id = ? AND aggregated_draft_id IS NULL
        ORDER BY id ASC
    """
    params: list[object] = [repo_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(query, params).fetchall())


def _classify(subject: str) -> str:
    s = subject.lower()
    if any(k in s for k in ("fix", "bug", "hotfix")):
        return "fixes"
    if any(k in s for k in ("feat", "feature", "add", "implement")):
        return "features"
    if any(k in s for k in ("refactor", "cleanup", "restructure")):
        return "refactors"
    return "others"


def build_summary(conn: sqlite3.Connection, push_event_ids: list[int]) -> dict:
    placeholders = ",".join("?" for _ in push_event_ids)
    rows = conn.execute(
        f"""SELECT sha, subject, files_changed, insertions, deletions FROM commits
            WHERE push_event_id IN ({placeholders})
            ORDER BY id ASC""",
        push_event_ids,
    ).fetchall()
    buckets: dict[str, list[str]] = {"features": [], "fixes": [], "refactors": [], "others": []}
    files_changed = insertions = deletions = 0
    for row in rows:
        subject = row["subject"]
        buckets[_classify(subject)].append(subject)
        files_changed += int(row["files_changed"])
        insertions += int(row["insertions"])
        deletions += int(row["deletions"])
    return {
        "counts": {k: len(v) for k, v in buckets.items()},
        "samples": {k: v[:3] for k, v in buckets.items()},
        "totals": {
            "commits": len(rows),
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
        },
    }


def render_content(repo_name: str, summary: dict, style: str) -> tuple[str, str]:
    counts = summary["counts"]
    totals = summary["totals"]
    samples = summary["samples"]
    title = f"{repo_name}: {totals['commits']} commits shipped"
    if style == "friendly":
        intro = f"Update on {repo_name}: shipped {totals['commits']} commits in this cycle."
        suffix = "If you want a deeper breakdown, I can share details in thread."
    elif style == "punchy":
        intro = f"{repo_name} just moved fast: {totals['commits']} commits landed."
        suffix = "Shipping mode stays on."
    else:
        intro = f"Progress update for {repo_name}: {totals['commits']} commits completed."
        suffix = "Feedback is welcome."
    lines = [
        intro,
        f"Features: {counts['features']} | Fixes: {counts['fixes']} | Refactors: {counts['refactors']}",
        f"Code delta: {totals['files_changed']} files, +{totals['insertions']} / -{totals['deletions']}",
    ]
    if samples["features"]:
        lines.append(f"Highlights: {', '.join(samples['features'])}")
    elif samples["fixes"]:
        lines.append(f"Highlights: {', '.join(samples['fixes'])}")
    elif samples["others"]:
        lines.append(f"Highlights: {', '.join(samples['others'])}")
    lines.append(suffix)
    return title, "\n".join(lines)


def create_draft(
    conn: sqlite3.Connection,
    repo_id: int | None,
    event_ids: list[int],
    style: str,
    title: str,
    content: str,
) -> int:
    draft_cur = conn.execute(
        """INSERT INTO drafts(repo_id, status, style, title, content, publish_token, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (repo_id, STATUS_PENDING, style, title, content, str(uuid.uuid4()), now_iso(), now_iso()),
    )
    draft_id = int(draft_cur.lastrowid)
    for event_id in event_ids:
        conn.execute("INSERT INTO draft_push_events(draft_id, push_event_id) VALUES (?, ?)", (draft_id, event_id))
        conn.execute("UPDATE push_events SET aggregated_draft_id = ? WHERE id = ?", (draft_id, event_id))
    conn.commit()
    return draft_id


def aggregate_ready_events(conn: sqlite3.Connection, repo_id: int) -> list[int]:
    settings = get_repo_settings(conn, repo_id)
    created: list[int] = []
    if not settings.publish_enabled:
        return created
    while True:
        events = find_unaggregated_events(conn, repo_id, settings.every_n_pushes)
        if len(events) < settings.every_n_pushes:
            break
        event_ids = [int(e["id"]) for e in events]
        repo_name_row = conn.execute("SELECT name FROM repos WHERE id = ?", (repo_id,)).fetchone()
        repo_name = repo_name_row["name"] if repo_name_row else f"repo-{repo_id}"
        summary = build_summary(conn, event_ids)
        title, content = render_content(repo_name, summary, settings.default_style)
        draft_id = create_draft(conn, repo_id, event_ids, settings.default_style, title, content)
        created.append(draft_id)
    return created


def list_drafts(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    query = """
        SELECT d.id, d.status, d.style, d.title, d.content, d.created_at, r.name AS repo_name, r.path AS repo_path
        FROM drafts d
        LEFT JOIN repos r ON d.repo_id = r.id
    """
    params: list[object] = []
    if status:
        query += " WHERE d.status = ?"
        params.append(status)
    query += " ORDER BY d.id DESC"
    return list(conn.execute(query, params).fetchall())


def get_draft(conn: sqlite3.Connection, draft_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT d.*, r.name AS repo_name, r.path AS repo_path
           FROM drafts d LEFT JOIN repos r ON d.repo_id = r.id WHERE d.id = ?""",
        (draft_id,),
    ).fetchone()


def get_draft_event_ids(conn: sqlite3.Connection, draft_id: int) -> list[int]:
    return [int(r["push_event_id"]) for r in conn.execute(
        "SELECT push_event_id FROM draft_push_events WHERE draft_id = ? ORDER BY push_event_id ASC",
        (draft_id,),
    ).fetchall()]


def rerender_draft(conn: sqlite3.Connection, draft_id: int, style: str) -> None:
    draft = get_draft(conn, draft_id)
    if not draft:
        raise ValueError(f"Draft {draft_id} not found")
    repo_name = draft["repo_name"] or "multi-repo"
    event_ids = get_draft_event_ids(conn, draft_id)
    summary = build_summary(conn, event_ids)
    title, content = render_content(repo_name, summary, style)
    conn.execute(
        "UPDATE drafts SET style = ?, title = ?, content = ?, updated_at = ? WHERE id = ?",
        (style, title, content, now_iso(), draft_id),
    )
    conn.commit()


def mark_publish_result(
    conn: sqlite3.Connection,
    draft_id: int,
    command: str,
    return_code: int,
    stdout: str,
    stderr: str,
    dry_run: bool,
) -> None:
    conn.execute(
        """INSERT INTO publish_logs(draft_id, attempted_at, command, return_code, stdout, stderr, dry_run)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (draft_id, now_iso(), command, return_code, stdout, stderr, 1 if dry_run else 0),
    )
    if dry_run:
        conn.commit()
        return
    if return_code == 0:
        conn.execute(
            "UPDATE drafts SET status = ?, published_at = ?, updated_at = ? WHERE id = ?",
            (STATUS_PUBLISHED, now_iso(), now_iso(), draft_id),
        )
        conn.execute(
            "UPDATE drafts SET status = ?, archived_at = ?, updated_at = ? WHERE id = ?",
            (STATUS_ARCHIVED, now_iso(), now_iso(), draft_id),
        )
    else:
        conn.execute(
            "UPDATE drafts SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_FAILED, now_iso(), draft_id),
        )
    conn.commit()


def find_repo_ids(conn: sqlite3.Connection, repo_paths: list[str]) -> list[int]:
    repo_ids: list[int] = []
    for path in repo_paths:
        repo_id = get_repo_id(conn, path)
        if repo_id is not None:
            repo_ids.append(repo_id)
    return repo_ids


def find_publishable_drafts(conn: sqlite3.Connection, repo_ids: list[int]) -> list[sqlite3.Row]:
    if not repo_ids:
        return []
    placeholders = ",".join("?" for _ in repo_ids)
    query = f"""
        SELECT d.*, r.name AS repo_name, r.path AS repo_path
        FROM drafts d
        JOIN repos r ON r.id = d.repo_id
        WHERE d.repo_id IN ({placeholders}) AND d.status IN (?, ?)
        ORDER BY d.id ASC
    """
    params = [*repo_ids, STATUS_PENDING, STATUS_FAILED]
    return list(conn.execute(query, params).fetchall())


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO app_config(key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, now_iso()),
    )
    conn.commit()


def get_config(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return str(row["value"])


def list_config(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT key, value, updated_at FROM app_config ORDER BY key ASC").fetchall())


def list_publish_logs(conn: sqlite3.Connection, draft_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """SELECT id, attempted_at, command, return_code, stdout, stderr, dry_run
               FROM publish_logs
               WHERE draft_id = ?
               ORDER BY id DESC
               LIMIT ?""",
            (draft_id, max(1, int(limit))),
        ).fetchall()
    )
