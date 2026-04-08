"""Message deduplication repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from feishu_codex_bot.persistence.db import DatabaseManager


MessageStatus = str


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ProcessedMessageRecord:
    id: int
    bot_app_id: str
    feishu_event_id: str | None
    feishu_message_id: str
    chat_id: str | None
    sender_open_id: str | None
    session_scope_key: str | None
    turn_id: str | None
    status: MessageStatus
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProcessedMessageRecord":
        return cls(
            id=row["id"],
            bot_app_id=row["bot_app_id"],
            feishu_event_id=row["feishu_event_id"],
            feishu_message_id=row["feishu_message_id"],
            chat_id=row["chat_id"],
            sender_open_id=row["sender_open_id"],
            session_scope_key=row["session_scope_key"],
            turn_id=row["turn_id"],
            status=row["status"],
            created_at=row["created_at"],
        )


class DedupeRepository:
    """Persist and update processed message markers for idempotency."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def get_by_message_id(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
    ) -> ProcessedMessageRecord | None:
        with self._db.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM processed_messages
                WHERE bot_app_id = ? AND feishu_message_id = ?
                """,
                (bot_app_id, feishu_message_id),
            ).fetchone()
        return ProcessedMessageRecord.from_row(row) if row else None

    def try_mark_accepted(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
        feishu_event_id: str | None = None,
        chat_id: str | None = None,
        sender_open_id: str | None = None,
        session_scope_key: str | None = None,
    ) -> bool:
        try:
            with self._db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO processed_messages (
                        bot_app_id,
                        feishu_event_id,
                        feishu_message_id,
                        chat_id,
                        sender_open_id,
                        session_scope_key,
                        status,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'accepted', ?)
                    """,
                    (
                        bot_app_id,
                        feishu_event_id,
                        feishu_message_id,
                        chat_id,
                        sender_open_id,
                        session_scope_key,
                        _utc_now(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def update_status(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
        status: MessageStatus,
        turn_id: str | None = None,
        session_scope_key: str | None = None,
    ) -> ProcessedMessageRecord | None:
        with self._db.transaction() as connection:
            connection.execute(
                """
                UPDATE processed_messages
                SET status = ?,
                    turn_id = COALESCE(?, turn_id),
                    session_scope_key = COALESCE(?, session_scope_key)
                WHERE bot_app_id = ? AND feishu_message_id = ?
                """,
                (status, turn_id, session_scope_key, bot_app_id, feishu_message_id),
            )
            row = connection.execute(
                """
                SELECT *
                FROM processed_messages
                WHERE bot_app_id = ? AND feishu_message_id = ?
                """,
                (bot_app_id, feishu_message_id),
            ).fetchone()
        return ProcessedMessageRecord.from_row(row) if row else None

