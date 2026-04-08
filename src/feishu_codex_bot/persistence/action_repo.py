"""Pending action repository for approvals and user input bridging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any

from feishu_codex_bot.persistence.db import DatabaseManager


PendingActionStatus = str
_UNSET = object()


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize_request_id(value: str | int) -> str:
    return str(value)


@dataclass(frozen=True, slots=True)
class PendingActionRecord:
    id: int
    request_id: str
    action_type: str
    thread_id: str
    turn_id: str
    item_id: str | None
    session_scope_key: str | None
    feishu_message_id: str | None
    payload_json: str
    status: PendingActionStatus
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PendingActionRecord":
        return cls(
            id=row["id"],
            request_id=row["request_id"],
            action_type=row["action_type"],
            thread_id=row["thread_id"],
            turn_id=row["turn_id"],
            item_id=row["item_id"],
            session_scope_key=row["session_scope_key"],
            feishu_message_id=row["feishu_message_id"],
            payload_json=row["payload_json"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @property
    def payload(self) -> dict[str, Any]:
        raw = json.loads(self.payload_json)
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def original_request_id(self) -> str | int:
        payload = self.payload
        raw_value = payload.get("requestId", self.request_id)
        return raw_value if isinstance(raw_value, (str, int)) else self.request_id


class PendingActionRepository:
    """Persist server requests awaiting user action."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def upsert_action(
        self,
        *,
        request_id: str | int,
        action_type: str,
        thread_id: str,
        turn_id: str,
        payload: dict[str, Any],
        item_id: str | None = None,
        session_scope_key: str | None = None,
        feishu_message_id: str | None = None,
        status: PendingActionStatus = "pending",
    ) -> PendingActionRecord:
        request_id_text = _normalize_request_id(request_id)
        timestamp = _utc_now()
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO pending_actions (
                    request_id,
                    action_type,
                    thread_id,
                    turn_id,
                    item_id,
                    session_scope_key,
                    feishu_message_id,
                    payload_json,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_id) DO UPDATE SET
                    action_type = excluded.action_type,
                    thread_id = excluded.thread_id,
                    turn_id = excluded.turn_id,
                    item_id = excluded.item_id,
                    session_scope_key = excluded.session_scope_key,
                    feishu_message_id = COALESCE(excluded.feishu_message_id, pending_actions.feishu_message_id),
                    payload_json = excluded.payload_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    request_id_text,
                    action_type,
                    thread_id,
                    turn_id,
                    item_id,
                    session_scope_key,
                    feishu_message_id,
                    payload_json,
                    status,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM pending_actions WHERE request_id = ?",
                (request_id_text,),
            ).fetchone()
        return PendingActionRecord.from_row(row)

    def get_by_request_id(self, request_id: str | int) -> PendingActionRecord | None:
        with self._db.connection() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM pending_actions
                WHERE request_id = ?
                """,
                (_normalize_request_id(request_id),),
            ).fetchone()
        return PendingActionRecord.from_row(row) if row else None

    def list_by_status(
        self,
        *,
        status: PendingActionStatus,
        limit: int = 50,
    ) -> list[PendingActionRecord]:
        with self._db.connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM pending_actions
                WHERE status = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [PendingActionRecord.from_row(row) for row in rows]

    def update_action(
        self,
        request_id: str | int,
        *,
        status: PendingActionStatus | object = _UNSET,
        feishu_message_id: str | None | object = _UNSET,
        payload: dict[str, Any] | object = _UNSET,
    ) -> PendingActionRecord | None:
        current = self.get_by_request_id(request_id)
        if current is None:
            return None

        next_status = current.status if status is _UNSET else str(status)
        next_feishu_message_id = (
            current.feishu_message_id if feishu_message_id is _UNSET else feishu_message_id
        )
        next_payload_json = (
            current.payload_json
            if payload is _UNSET
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        timestamp = _utc_now()
        with self._db.transaction() as connection:
            connection.execute(
                """
                UPDATE pending_actions
                SET status = ?,
                    feishu_message_id = ?,
                    payload_json = ?,
                    updated_at = ?
                WHERE request_id = ?
                """,
                (
                    next_status,
                    next_feishu_message_id,
                    next_payload_json,
                    timestamp,
                    current.request_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM pending_actions WHERE request_id = ?",
                (current.request_id,),
            ).fetchone()
        return PendingActionRecord.from_row(row) if row else None
