"""Persist raw Codex callbacks for debugging and protocol inspection."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from feishu_codex_bot.models.actions import JsonObject, extract_routing_ids


class CodexDumpService:
    """Write raw Codex callbacks to a single ordered JSON file."""

    _STREAMING_NOTIFICATION_METHOD = "item/agentMessage/delta"
    _FLUSH_NOTIFICATION_METHOD = "turn/completed"

    def __init__(self, dump_path: Path) -> None:
        self._dump_path = dump_path
        self._entries: list[JsonObject] = []
        self._streaming_entry_index_by_key: dict[tuple[str, str, str], int] = {}
        self._sequence = 0

    @property
    def dump_path(self) -> Path:
        return self._dump_path

    def reset(self) -> None:
        self._entries = []
        self._streaming_entry_index_by_key = {}
        self._sequence = 0
        self._write_dump_file()

    def record_notification(self, payload: JsonObject) -> None:
        method = payload.get("method")
        if not isinstance(method, str):
            return
        if method == self._STREAMING_NOTIFICATION_METHOD:
            self._record_streaming_notification(method=method, payload=payload)
            return
        self._record_entry(
            callback_kind="notification",
            method=method,
            payload=payload,
        )
        if method == self._FLUSH_NOTIFICATION_METHOD:
            self._write_dump_file()

    def record_server_request(self, payload: JsonObject) -> None:
        method = payload.get("method")
        if not isinstance(method, str):
            return
        self._record_entry(
            callback_kind="server_request",
            method=method,
            payload=payload,
        )

    def _record_streaming_notification(self, *, method: str, payload: JsonObject) -> None:
        params = payload.get("params")
        thread_id, turn_id, item_id, request_id = extract_routing_ids(params)
        if request_id is None:
            top_level_request_id = payload.get("id")
            if isinstance(top_level_request_id, (str, int)):
                request_id = top_level_request_id
        if thread_id is None or turn_id is None or item_id is None:
            self._record_entry(
                callback_kind="notification",
                method=method,
                payload=payload,
            )
            return

        key = (method, turn_id, item_id)
        timestamp = self._timestamp()
        delta = ""
        if isinstance(params, dict):
            raw_delta = params.get("delta")
            if isinstance(raw_delta, str):
                delta = raw_delta

        existing_index = self._streaming_entry_index_by_key.get(key)
        if existing_index is None:
            self._sequence += 1
            entry: JsonObject = {
                "seq": self._sequence,
                "received_at": timestamp,
                "updated_at": timestamp,
                "callback_kind": "notification",
                "method": method,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "item_id": item_id,
                "request_id": request_id,
                "chunk_count": 1,
                "content": delta,
                "payload": payload,
            }
            self._entries.append(entry)
            self._streaming_entry_index_by_key[key] = len(self._entries) - 1
            return

        entry = self._entries[existing_index]
        entry["updated_at"] = timestamp
        entry["chunk_count"] = int(entry.get("chunk_count", 0)) + 1
        entry["content"] = f"{entry.get('content', '')}{delta}"
        entry["payload"] = payload

    def _record_entry(
        self,
        *,
        callback_kind: str,
        method: str,
        payload: JsonObject,
    ) -> None:
        params = payload.get("params")
        thread_id, turn_id, item_id, request_id = extract_routing_ids(params)
        if request_id is None:
            top_level_request_id = payload.get("id")
            if isinstance(top_level_request_id, (str, int)):
                request_id = top_level_request_id
        self._sequence += 1
        self._entries.append(
            {
                "seq": self._sequence,
                "received_at": self._timestamp(),
                "callback_kind": callback_kind,
                "method": method,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "item_id": item_id,
                "request_id": request_id,
                "content": params,
                "payload": payload,
            }
        )

    def _write_dump_file(self) -> None:
        self._dump_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._dump_path.with_suffix(f"{self._dump_path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(self._entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._dump_path)

    def _timestamp(self) -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")
