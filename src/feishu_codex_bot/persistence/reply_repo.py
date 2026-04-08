"""Reply message repository for streaming response state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from feishu_codex_bot.persistence.db import DatabaseManager


ReplyStatus = str


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ReplyMessageRecord:
    id: int
    bot_app_id: str
    feishu_message_id: str
    reply_message_id: str
    thread_id: str
    turn_id: str | None
    agent_item_id: str | None
    status: ReplyStatus
    reaction_applied: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ReplyMessageRecord":
        return cls(
            id=row["id"],
            bot_app_id=row["bot_app_id"],
            feishu_message_id=row["feishu_message_id"],
            reply_message_id=row["reply_message_id"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            agent_item_id=row["agent_item_id"],
            status=row["status"],
            reaction_applied=bool(row["reaction_applied"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class ReplyRepository:
    """Persist one-reply-per-turn mappings and their stream state."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def create_reply(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
        reply_message_id: str,
        thread_id: str,
        turn_id: str | None = None,
        agent_item_id: str | None = None,
        status: ReplyStatus = "streaming",
        reaction_applied: bool = False,
    ) -> ReplyMessageRecord:
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_messages (
                    bot_app_id,
                    feishu_message_id,
                    reply_message_id,
                    thread_id,
                    turn_id,
                    agent_item_id,
                    status,
                    reaction_applied,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_app_id,
                    feishu_message_id,
                    reply_message_id,
                    thread_id,
                    turn_id,
                    agent_item_id,
                    status,
                    int(reaction_applied),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM reply_messages
                WHERE bot_app_id = ? AND reply_message_id = ?
                """,
                (bot_app_id, reply_message_id),
            ).fetchone()
        return ReplyMessageRecord.from_row(row)

    def get_by_reply_message_id(
        self,
        *,
        bot_app_id: str,
        reply_message_id: str,
    ) -> ReplyMessageRecord | None:
        with self._db.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM reply_messages
                WHERE bot_app_id = ? AND reply_message_id = ?
                """,
                (bot_app_id, reply_message_id),
            ).fetchone()
        return ReplyMessageRecord.from_row(row) if row else None

    def get_latest_by_source_message(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
    ) -> ReplyMessageRecord | None:
        with self._db.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM reply_messages
                WHERE bot_app_id = ? AND feishu_message_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (bot_app_id, feishu_message_id),
            ).fetchone()
        return ReplyMessageRecord.from_row(row) if row else None

    def update_reply(
        self,
        *,
        bot_app_id: str,
        reply_message_id: str,
        status: ReplyStatus | None = None,
        turn_id: str | None = None,
        agent_item_id: str | None = None,
        reaction_applied: bool | None = None,
    ) -> ReplyMessageRecord | None:
        current = self.get_by_reply_message_id(
            bot_app_id=bot_app_id,
            reply_message_id=reply_message_id,
        )
        if current is None:
            return None

        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                UPDATE reply_messages
                SET status = ?,
                    turn_id = ?,
                    agent_item_id = ?,
                    reaction_applied = ?,
                    updated_at = ?
                WHERE bot_app_id = ? AND reply_message_id = ?
                """,
                (
                    status if status is not None else current.status,
                    turn_id if turn_id is not None else current.turn_id,
                    agent_item_id if agent_item_id is not None else current.agent_item_id,
                    int(reaction_applied if reaction_applied is not None else current.reaction_applied),
                    timestamp,
                    bot_app_id,
                    reply_message_id,
                ),
            )
            row = connection.execute(
                """
                SELECT *
                FROM reply_messages
                WHERE bot_app_id = ? AND reply_message_id = ?
                """,
                (bot_app_id, reply_message_id),
            ).fetchone()
        return ReplyMessageRecord.from_row(row) if row else None
