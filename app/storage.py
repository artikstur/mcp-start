"""SQLite persistence for isolated conversation history and confirmed facts."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

ALLOWED_FACT_KEYS = frozenset({"group", "course", "subject"})
SESSION_TTL = timedelta(days=7)
FACT_TTL = timedelta(days=180)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLiteStore:
    def __init__(self, db_path: str | Path, now: Callable[[], datetime] = _utcnow):
        self.db_path = str(db_path)
        self._now = now
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, session_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_session_idx
                    ON messages(user_id, session_id, id);
                CREATE TABLE IF NOT EXISTS memory (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                );
                CREATE TABLE IF NOT EXISTS pending_facts (
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, session_id)
                );
                CREATE TABLE IF NOT EXISTS pending_forgets (
                    user_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS request_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS request_events_user_time_idx
                    ON request_events(user_id, created_at);
                """
            )
        self.cleanup_expired()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _stamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat()

    def cleanup_expired(self) -> dict[str, int]:
        now = self._now().astimezone(timezone.utc)
        session_cutoff = (now - SESSION_TTL).isoformat()
        fact_cutoff = (now - FACT_TTL).isoformat()
        with self._connect() as db:
            expired_sessions = db.execute(
                "SELECT user_id, session_id FROM sessions WHERE updated_at < ?", (session_cutoff,)
            ).fetchall()
            for row in expired_sessions:
                db.execute("DELETE FROM messages WHERE user_id=? AND session_id=?", tuple(row))
            db.execute("DELETE FROM sessions WHERE updated_at < ?", (session_cutoff,))
            facts = db.execute("DELETE FROM memory WHERE updated_at < ?", (fact_cutoff,))
            pending_cutoff = session_cutoff
            pending = db.execute("DELETE FROM pending_facts WHERE created_at < ?", (pending_cutoff,))
            forgotten = db.execute("DELETE FROM pending_forgets WHERE created_at < ?", (pending_cutoff,))
        return {"sessions": len(expired_sessions), "facts": facts.rowcount,
                "pending": pending.rowcount + forgotten.rowcount}

    def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        stamp = self._stamp()
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(user_id, session_id, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id, session_id) DO UPDATE SET updated_at=excluded.updated_at",
                (user_id, session_id, stamp),
            )
            db.execute(
                "INSERT INTO messages(user_id, session_id, role, content, created_at) VALUES(?,?,?,?,?)",
                (user_id, session_id, role, content, stamp),
            )

    def consume_request(self, user_id: str, limit: int = 20, window_seconds: int = 60) -> bool:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit and window_seconds must be positive")
        now = self._now().astimezone(timezone.utc)
        stamp = now.isoformat()
        cutoff = (now - timedelta(seconds=window_seconds)).isoformat()
        with self._connect() as db:
            db.execute("DELETE FROM request_events WHERE created_at < ?", (cutoff,))
            count = db.execute(
                "SELECT COUNT(*) FROM request_events WHERE user_id=? AND created_at >= ?",
                (user_id, cutoff),
            ).fetchone()[0]
            if count >= limit:
                return False
            db.execute(
                "INSERT INTO request_events(user_id, created_at) VALUES(?,?)",
                (user_id, stamp),
            )
        return True

    def load_history(self, user_id: str, session_id: str) -> list[dict[str, str]]:
        self.cleanup_expired()
        with self._connect() as db:
            rows = db.execute(
                "SELECT role, content, created_at FROM messages "
                "WHERE user_id=? AND session_id=? ORDER BY id",
                (user_id, session_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_history(self, user_id: str, session_id: str, messages: list[dict[str, str]]) -> None:
        """Replace a session's stored context after compaction."""
        stamp = self._stamp()
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(user_id, session_id, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id, session_id) DO UPDATE SET updated_at=excluded.updated_at",
                (user_id, session_id, stamp),
            )
            db.execute("DELETE FROM messages WHERE user_id=? AND session_id=?", (user_id, session_id))
            db.executemany(
                "INSERT INTO messages(user_id, session_id, role, content, created_at) VALUES(?,?,?,?,?)",
                [(user_id, session_id, message["role"], message["content"], stamp) for message in messages],
            )

    def propose_fact(self, user_id: str, session_id: str, key: str, value: str) -> None:
        if key not in ALLOWED_FACT_KEYS:
            raise ValueError(f"unsupported fact key: {key}")
        if not isinstance(value, str):
            raise ValueError("fact value must be text")
        value = value.strip()
        if not value or len(value) > 160 or any(not char.isprintable() for char in value):
            raise ValueError("fact value must be 1–160 printable characters")
        with self._connect() as db:
            db.execute(
                "INSERT INTO pending_facts(user_id, session_id, key, value, created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(user_id, session_id) DO UPDATE SET key=excluded.key, "
                "value=excluded.value, created_at=excluded.created_at",
                (user_id, session_id, key, value, self._stamp()),
            )

    def pending_fact(self, user_id: str, session_id: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT key, value FROM pending_facts WHERE user_id=? AND session_id=?",
                (user_id, session_id),
            ).fetchone()
        return dict(row) if row else None

    def confirm_pending_fact(self, user_id: str, session_id: str, accepted: bool) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT key, value FROM pending_facts WHERE user_id=? AND session_id=?",
                (user_id, session_id),
            ).fetchone()
            if row is None:
                return False
            if accepted:
                db.execute(
                    "INSERT INTO memory(user_id, key, value, updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (user_id, row["key"], row["value"], self._stamp()),
                )
            db.execute("DELETE FROM pending_facts WHERE user_id=? AND session_id=?", (user_id, session_id))
        return accepted

    def get_memory(self, user_id: str) -> dict[str, str]:
        self.cleanup_expired()
        with self._connect() as db:
            rows = db.execute("SELECT key, value FROM memory WHERE user_id=?", (user_id,)).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def forget_user(self, user_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM memory WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM pending_facts WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM pending_forgets WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM request_events WHERE user_id=?", (user_id,))

    def request_forget(self, user_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO pending_forgets(user_id, created_at) VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET created_at=excluded.created_at",
                (user_id, self._stamp()),
            )

    def has_pending_forget(self, user_id: str) -> bool:
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM pending_forgets WHERE user_id=?", (user_id,)
            ).fetchone() is not None

    def cancel_forget(self, user_id: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM pending_forgets WHERE user_id=?", (user_id,))
