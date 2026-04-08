"""Session repository for chat/thread mapping persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Literal

from feishu_codex_bot.persistence.db import DatabaseManager


SessionScopeType = Literal["dm", "group"]
SessionStatus = Literal["active", "archived"]


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: int
    scope_type: SessionScopeType
    scope_key: str
    bot_app_id: str
    user_open_id: str | None
    chat_id: str | None
    thread_id: str
    thread_generation: int
    last_message_at: str | None
    expires_at: str | None
    status: SessionStatus
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SessionRecord":
        return cls(
            id=row["id"],
            scope_type=row["scope_type"],
            scope_key=row["scope_key"],
            bot_app_id=row["bot_app_id"],
            user_open_id=row["user_open_id"],
            chat_id=row["chat_id"],
            thread_id=row["thread_id"],
            thread_generation=row["thread_generation"],
            last_message_at=row["last_message_at"],
            expires_at=row["expires_at"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        reference = now or datetime.now(tz=timezone.utc)
        return datetime.fromisoformat(self.expires_at) <= reference


class SessionRepository:
    """Persist and query chat-to-thread sessions."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def get_by_scope_key(self, scope_key: str) -> SessionRecord | None:
        with self._db.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM sessions
                WHERE scope_key = ?
                """,
                (scope_key,),
            ).fetchone()
        return SessionRecord.from_row(row) if row else None

    def upsert_session(
        self,
        *,
        scope_type: SessionScopeType,
        scope_key: str,
        bot_app_id: str,
        thread_id: str,
        user_open_id: str | None = None,
        chat_id: str | None = None,
        thread_generation: int = 1,
        last_message_at: str | None = None,
        expires_at: str | None = None,
        status: SessionStatus = "active",
    ) -> SessionRecord:
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    scope_type,
                    scope_key,
                    bot_app_id,
                    user_open_id,
                    chat_id,
                    thread_id,
                    thread_generation,
                    last_message_at,
                    expires_at,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    scope_type = excluded.scope_type,
                    bot_app_id = excluded.bot_app_id,
                    user_open_id = excluded.user_open_id,
                    chat_id = excluded.chat_id,
                    thread_id = excluded.thread_id,
                    thread_generation = excluded.thread_generation,
                    last_message_at = excluded.last_message_at,
                    expires_at = excluded.expires_at,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    scope_type,
                    scope_key,
                    bot_app_id,
                    user_open_id,
                    chat_id,
                    thread_id,
                    thread_generation,
                    last_message_at,
                    expires_at,
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        return SessionRecord.from_row(row)

    def touch_session(
        self,
        scope_key: str,
        *,
        last_message_at: str,
        expires_at: str | None,
    ) -> SessionRecord | None:
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET last_message_at = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE scope_key = ?
                """,
                (last_message_at, expires_at, timestamp, scope_key),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        return SessionRecord.from_row(row) if row else None

    def archive_session(self, scope_key: str) -> SessionRecord | None:
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET status = 'archived',
                    updated_at = ?
                WHERE scope_key = ?
                """,
                (timestamp, scope_key),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE scope_key = ?",
                (scope_key,),
            ).fetchone()
        return SessionRecord.from_row(row) if row else None

