from __future__ import annotations

import json
from pathlib import Path

from feishu_codex_bot.services.codex_dump_service import CodexDumpService


def _read_dump(path: Path) -> list[dict[str, object]]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_dump_service_records_callbacks_in_order(tmp_path: Path) -> None:
    dump_path = tmp_path / "dump.json"
    service = CodexDumpService(dump_path)

    service.reset()
    assert _read_dump(dump_path) == []
    service.record_notification(
        {
            "jsonrpc": "2.0",
            "method": "turn/started",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {"id": "turn-1", "status": "in_progress"},
            },
        }
    )
    service.record_server_request(
        {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
            },
        }
    )
    assert _read_dump(dump_path) == []
    service.record_notification(
        {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        }
    )

    dumped = _read_dump(dump_path)
    assert [entry["seq"] for entry in dumped] == [1, 2, 3]
    assert dumped[0]["callback_kind"] == "notification"
    assert dumped[0]["method"] == "turn/started"
    assert dumped[0]["turn_id"] == "turn-1"
    assert dumped[0]["content"] == {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "turn": {"id": "turn-1", "status": "in_progress"},
    }
    assert dumped[1]["callback_kind"] == "server_request"
    assert dumped[1]["method"] == "item/commandExecution/requestApproval"
    assert dumped[1]["request_id"] == "req-1"
    assert dumped[1]["item_id"] == "item-1"
    assert dumped[2]["method"] == "turn/completed"
    assert dumped[2]["turn_id"] == "turn-1"


def test_dump_service_aggregates_agent_message_delta_into_single_entry(tmp_path: Path) -> None:
    dump_path = tmp_path / "dump.json"
    service = CodexDumpService(dump_path)

    service.reset()
    service.record_notification(
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
                "delta": "你",
            },
        }
    )
    service.record_notification(
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
                "delta": "好",
            },
        }
    )
    service.record_notification(
        {
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "item-1",
                "item": {"id": "item-1", "type": "agentMessage", "text": "你好"},
            },
        }
    )
    assert _read_dump(dump_path) == []
    service.record_notification(
        {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        }
    )

    dumped = _read_dump(dump_path)
    assert len(dumped) == 3
    assert dumped[0]["seq"] == 1
    assert dumped[0]["method"] == "item/agentMessage/delta"
    assert dumped[0]["chunk_count"] == 2
    assert dumped[0]["content"] == "你好"
    assert dumped[1]["seq"] == 2
    assert dumped[1]["method"] == "item/completed"
    assert dumped[2]["seq"] == 3
    assert dumped[2]["method"] == "turn/completed"
