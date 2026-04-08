"""Security alert repository for unauthorized access attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from feishu_codex_bot.persistence.db import DatabaseManager


SecurityAlertStatus = str


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class SecurityAlertRecord:
    id: int
    bot_app_id: str
    sender_open_id: str | None
    chat_id: str | None
    chat_type: str
    feishu_message_id: str
    feishu_event_id: str | None
    owner_open_id: str
    owner_alert_message_id: str | None
    status: SecurityAlertStatus
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SecurityAlertRecord":
        return cls(
            id=row["id"],
            bot_app_id=row["bot_app_id"],
            sender_open_id=row["sender_open_id"],
            chat_id=row["chat_id"],
            chat_type=row["chat_type"],
            feishu_message_id=row["feishu_message_id"],
            feishu_event_id=row["feishu_event_id"],
            owner_open_id=row["owner_open_id"],
            owner_alert_message_id=row["owner_alert_message_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SecurityAlertRepository:
    """Persist unauthorized message attempts and alert delivery state."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def create_alert(
        self,
        *,
        bot_app_id: str,
        sender_open_id: str | None,
        chat_id: str | None,
        chat_type: str,
        feishu_message_id: str,
        feishu_event_id: str | None,
        owner_open_id: str,
        status: SecurityAlertStatus = "blocked",
        owner_alert_message_id: str | None = None,
    ) -> SecurityAlertRecord:
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO security_alerts (
                    bot_app_id,
                    sender_open_id,
                    chat_id,
                    chat_type,
                    feishu_message_id,
                    feishu_event_id,
                    owner_open_id,
                    owner_alert_message_id,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_app_id,
                    sender_open_id,
                    chat_id,
                    chat_type,
                    feishu_message_id,
                    feishu_event_id,
                    owner_open_id,
                    owner_alert_message_id,
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM security_alerts WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return SecurityAlertRecord.from_row(row)

    def update_alert_result(
        self,
        alert_id: int,
        *,
        status: SecurityAlertStatus,
        owner_alert_message_id: str | None = None,
    ) -> SecurityAlertRecord | None:
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                UPDATE security_alerts
                SET status = ?,
                    owner_alert_message_id = COALESCE(?, owner_alert_message_id),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, owner_alert_message_id, timestamp, alert_id),
            )
            row = connection.execute(
                "SELECT * FROM security_alerts WHERE id = ?",
                (alert_id,),
            ).fetchone()
        return SecurityAlertRecord.from_row(row) if row else None

    def list_recent_alerts(
        self,
        *,
        bot_app_id: str,
        limit: int = 20,
    ) -> list[SecurityAlertRecord]:
        with self._db.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM security_alerts
                WHERE bot_app_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (bot_app_id, limit),
            ).fetchall()
        return [SecurityAlertRecord.from_row(row) for row in rows]

