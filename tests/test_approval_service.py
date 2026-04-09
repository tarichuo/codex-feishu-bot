from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

from feishu_codex_bot.config import (
    AppConfig,
    CodexConfig,
    FeishuConfig,
    LoggingConfig,
    SecurityConfig,
    StorageConfig,
)
from feishu_codex_bot.models.actions import CodexServerRequest
from feishu_codex_bot.persistence.action_repo import PendingActionRepository
from feishu_codex_bot.persistence.db import DatabaseManager

_codex_client_stub = types.ModuleType("feishu_codex_bot.adapters.codex_client")
_codex_client_stub.DEFER_SERVER_REQUEST = object()
_codex_client_stub.CodexClient = object
sys.modules.setdefault("feishu_codex_bot.adapters.codex_client", _codex_client_stub)

_feishu_adapter_stub = types.ModuleType("feishu_codex_bot.adapters.feishu_adapter")
_feishu_adapter_stub.FeishuAdapter = object
class _FakeFeishuReplyCardRef:
    def __init__(self, message_id: str, card_id: str) -> None:
        self.message_id = message_id
        self.card_id = card_id

_feishu_adapter_stub.FeishuReplyCardRef = _FakeFeishuReplyCardRef
sys.modules.setdefault("feishu_codex_bot.adapters.feishu_adapter", _feishu_adapter_stub)

from feishu_codex_bot.services.approval_service import ApprovalRequestContext, ApprovalService


class _FakeCodexClient:
    def __init__(self) -> None:
        self.responses: list[tuple[str | int, dict[str, object]]] = []

    async def respond_to_server_request(
        self,
        request_id: str | int,
        response_payload: dict[str, object],
    ) -> None:
        self.responses.append((request_id, response_payload))


class _FakeFeishuAdapter:
    def __init__(self) -> None:
        self.sent_cards: list[dict[str, object]] = []
        self.updated_cards: list[dict[str, object]] = []

    def send_approval_message(
        self,
        *,
        receive_id: str,
        card_payload: dict[str, object],
    ):
        self.sent_cards.append(
            {
                "receive_id": receive_id,
                "card_payload": card_payload,
            }
        )
        return _FakeFeishuReplyCardRef(message_id="approval-message-1", card_id="approval-card-1")

    def update_approval_message(
        self,
        *,
        card_id: str,
        card_payload: dict[str, object],
        sequence: int,
    ) -> str:
        self.updated_cards.append(
            {
                "card_id": card_id,
                "card_payload": card_payload,
                "sequence": sequence,
            }
        )
        return card_id

    def send_user_input_message(self, *, message_id: str, text: str, reply_in_thread: bool = False) -> str:
        raise AssertionError("user input path is not expected in this test")

    def update_user_input_message(self, *, message_id: str, text: str) -> str:
        raise AssertionError("user input path is not expected in this test")


def _build_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        feishu=FeishuConfig(app_id="cli_test", app_secret="secret"),
        codex=CodexConfig(server_url="ws://127.0.0.1:9000"),
        storage=StorageConfig(
            base_dir=tmp_path,
            data_dir=tmp_path / "var",
            sqlite_path=tmp_path / "var" / "app.db",
            media_dir=tmp_path / "var" / "media",
            logs_dir=tmp_path / "var" / "logs",
        ),
        security=SecurityConfig(
            owner_user_id="ou_owner",
            allowed_user_ids=frozenset({"ou_owner"}),
        ),
        logging=LoggingConfig(level="DEBUG"),
    )


def _build_service(tmp_path: Path) -> tuple[ApprovalService, _FakeCodexClient, _FakeFeishuAdapter, PendingActionRepository]:
    config = _build_config(tmp_path)
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)
    config.storage.logs_dir.mkdir(parents=True, exist_ok=True)
    db = DatabaseManager(config.storage.sqlite_path)
    db.initialize()
    codex_client = _FakeCodexClient()
    feishu_adapter = _FakeFeishuAdapter()
    repository = PendingActionRepository(db)
    service = ApprovalService(
        config,
        codex_client=codex_client,
        feishu_adapter=feishu_adapter,
        action_repository=repository,
    )
    return service, codex_client, feishu_adapter, repository


def test_handle_server_request_sends_approval_card(tmp_path: Path) -> None:
    service, _, feishu_adapter, repository = _build_service(tmp_path)
    request = CodexServerRequest(
        id="req-1",
        method="item/commandExecution/requestApproval",
        params={
            "command": "pytest",
            "cwd": "/workspace",
            "reason": "run tests",
        },
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
    )

    async def _run() -> object:
        return await service.handle_server_request(
            request,
            context=ApprovalRequestContext(
                session_scope_key="scope-1",
                source_message_id="om-source-1",
                chat_id="oc_chat_1",
            ),
        )

    asyncio.run(_run())

    assert len(feishu_adapter.sent_cards) == 1
    sent = feishu_adapter.sent_cards[0]
    assert sent["receive_id"] == "oc_chat_1"
    payload = sent["card_payload"]
    assert payload["header"]["title"]["content"] == "Codex 请求命令审批"
    elements = payload["body"]["elements"]
    assert all(element["tag"] != "action" for element in elements)
    assert elements[0]["tag"] == "markdown"
    assert elements[0]["content"] == "**命令**: `pytest`\n**cwd**: `/workspace`"
    assert elements[1]["tag"] == "button"
    assert [element["text"]["content"] for element in elements[1:]] == [
        "同意",
        "本会话内同意",
        "拒绝",
        "取消",
    ]
    assert elements[2]["behaviors"][0]["type"] == "callback"
    assert elements[2]["behaviors"][0]["value"]["decision"] == "acceptForSession"
    assert elements[3]["type"] == "danger"
    assert repository.get_by_request_id("req-1") is not None
    assert repository.get_by_request_id("req-1").payload["feishuCardId"] == "approval-card-1"
    assert repository.get_by_request_id("req-1").payload["feishuCardSequence"] == 0


def test_submit_approval_response_updates_card_summary(tmp_path: Path) -> None:
    service, codex_client, feishu_adapter, _ = _build_service(tmp_path)
    request = CodexServerRequest(
        id="req-2",
        method="item/fileChange/requestApproval",
        params={
            "reason": "apply patch",
            "grantRoot": "/workspace",
        },
        thread_id="thread-2",
        turn_id="turn-2",
        item_id="item-2",
    )

    async def _run() -> None:
        await service.handle_server_request(
            request,
            context=ApprovalRequestContext(
                session_scope_key="scope-2",
                source_message_id="om-source-2",
                chat_id="oc_chat_2",
            ),
        )
        await service.submit_approval_response(
            "req-2",
            "acceptForSession",
            scope="session",
        )

    asyncio.run(_run())

    assert codex_client.responses == [("req-2", {"decision": "acceptForSession"})]
    assert len(feishu_adapter.updated_cards) == 1
    assert feishu_adapter.updated_cards[0]["sequence"] == 1
    updated_payload = feishu_adapter.updated_cards[0]["card_payload"]
    assert updated_payload["header"]["title"]["content"] == "审批已在本对话内同意"
    elements = updated_payload["body"]["elements"]
    assert elements[0]["content"] == "**授权目录**: /workspace\n**原因**: apply patch"
    assert len(elements) == 1


def test_submit_approval_response_can_defer_prompt_update(tmp_path: Path) -> None:
    service, codex_client, feishu_adapter, repository = _build_service(tmp_path)
    request = CodexServerRequest(
        id="req-3",
        method="item/fileChange/requestApproval",
        params={
            "reason": "apply patch",
            "grantRoot": "/workspace",
        },
        thread_id="thread-3",
        turn_id="turn-3",
        item_id="item-3",
    )

    async def _run() -> None:
        await service.handle_server_request(
            request,
            context=ApprovalRequestContext(
                session_scope_key="scope-3",
                source_message_id="om-source-3",
                chat_id="oc_chat_3",
            ),
        )
        await service.submit_approval_response(
            "req-3",
            "accept",
            update_prompt=False,
        )

    asyncio.run(_run())

    assert codex_client.responses == [("req-3", {"decision": "accept"})]
    assert feishu_adapter.updated_cards == []

    record = repository.get_by_request_id("req-3")
    assert record is not None
    service.finalize_response_side_effects(record)

    assert len(feishu_adapter.updated_cards) == 1
    updated_payload = feishu_adapter.updated_cards[0]["card_payload"]
    assert updated_payload["header"]["title"]["content"] == "审批已通过"
    elements = updated_payload["body"]["elements"]
    assert elements[0]["content"] == "**授权目录**: /workspace\n**原因**: apply patch"
    assert len(elements) == 1


def test_submit_approval_response_keeps_selected_decline_button_type(tmp_path: Path) -> None:
    service, _, feishu_adapter, _ = _build_service(tmp_path)
    request = CodexServerRequest(
        id="req-4",
        method="item/fileChange/requestApproval",
        params={
            "reason": "apply patch",
            "grantRoot": "/workspace",
        },
        thread_id="thread-4",
        turn_id="turn-4",
        item_id="item-4",
    )

    async def _run() -> None:
        await service.handle_server_request(
            request,
            context=ApprovalRequestContext(
                session_scope_key="scope-4",
                source_message_id="om-source-4",
                chat_id="oc_chat_4",
            ),
        )
        await service.submit_approval_response(
            "req-4",
            "decline",
            scope="turn",
        )

    asyncio.run(_run())

    assert len(feishu_adapter.updated_cards) == 1
    updated_payload = feishu_adapter.updated_cards[0]["card_payload"]
    assert updated_payload["header"]["title"]["content"] == "审批已拒绝"
    elements = updated_payload["body"]["elements"]
    assert elements[0]["content"] == "**授权目录**: /workspace\n**原因**: apply patch"
    assert len(elements) == 1
