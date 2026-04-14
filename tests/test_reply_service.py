from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
import types

import pytest

from feishu_codex_bot.config import (
    AppConfig,
    CodexConfig,
    FeishuConfig,
    LoggingConfig,
    SecurityConfig,
    StorageConfig,
)
from feishu_codex_bot.models.actions import (
    CodexNotification,
    CodexTextDeltaEvent,
    CodexTurnErrorEvent,
    CodexTurnLifecycleEvent,
)

_feishu_adapter_stub = types.ModuleType("feishu_codex_bot.adapters.feishu_adapter")
_feishu_adapter_stub.FeishuAdapter = object
_feishu_adapter_stub.FeishuReplyCardRef = object
sys.modules.setdefault("feishu_codex_bot.adapters.feishu_adapter", _feishu_adapter_stub)

from feishu_codex_bot.services.reply_service import ReplyService


@dataclass(frozen=True)
class _FakeReplyCardRef:
    message_id: str
    card_id: str


@dataclass(frozen=True)
class _FakeReplyRecord:
    reply_message_id: str
    turn_id: str | None
    agent_item_id: str | None
    status: str
    reaction_applied: bool


class _FakeFeishuAdapter:
    def __init__(self) -> None:
        self.reply_cards: list[dict[str, object]] = []
        self.failure_cards: list[dict[str, object]] = []
        self.card_updates: list[dict[str, object]] = []
        self.card_streaming_modes: list[dict[str, object]] = []
        self.added_reactions: list[dict[str, str]] = []
        self.removed_reactions: list[dict[str, str]] = []

    def reply_streaming_card(
        self,
        *,
        message_id: str,
        text: str,
        reply_in_thread: bool = False,
        status: str = "streaming",
    ) -> _FakeReplyCardRef:
        call = {
            "message_id": message_id,
            "text": text,
            "reply_in_thread": reply_in_thread,
            "status": status,
        }
        self.reply_cards.append(call)
        index = len(self.reply_cards)
        return _FakeReplyCardRef(
            message_id=f"reply-message-{index}",
            card_id=f"card-{index}",
        )

    def reply_failure_card(
        self,
        *,
        message_id: str,
        error_text: str,
        reply_in_thread: bool = False,
    ) -> _FakeReplyCardRef:
        call = {
            "message_id": message_id,
            "error_text": error_text,
            "reply_in_thread": reply_in_thread,
        }
        self.failure_cards.append(call)
        index = len(self.reply_cards) + len(self.failure_cards)
        return _FakeReplyCardRef(
            message_id=f"reply-message-{index}",
            card_id=f"card-{index}",
        )

    def add_reaction(self, *, message_id: str, emoji_type: str) -> str:
        self.added_reactions.append({"message_id": message_id, "emoji_type": emoji_type})
        return "reaction-1"

    def remove_reaction(self, *, message_id: str, reaction_id: str) -> None:
        self.removed_reactions.append({"message_id": message_id, "reaction_id": reaction_id})

    def update_streaming_card(
        self,
        *,
        card_id: str,
        text: str,
        status: str,
        sequence: int,
    ) -> int:
        self.card_updates.append(
            {
                "card_id": card_id,
                "text": text,
                "status": status,
                "sequence": sequence,
            }
        )
        return 2

    def disable_streaming_card(self, *, card_id: str, sequence: int) -> None:
        self.card_streaming_modes.append(
            {
                "card_id": card_id,
                "enabled": False,
                "sequence": sequence,
            }
        )


class _FakeReplyRepository:
    def __init__(self) -> None:
        self.records: dict[str, _FakeReplyRecord] = {}

    def create_reply(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
        reply_message_id: str,
        thread_id: str,
        turn_id: str | None = None,
        agent_item_id: str | None = None,
        status: str = "streaming",
        reaction_applied: bool = False,
    ) -> _FakeReplyRecord:
        record = _FakeReplyRecord(
            reply_message_id=reply_message_id,
            turn_id=turn_id,
            agent_item_id=agent_item_id,
            status=status,
            reaction_applied=reaction_applied,
        )
        self.records[reply_message_id] = record
        return record

    def update_reply(
        self,
        *,
        bot_app_id: str,
        reply_message_id: str,
        status: str | None = None,
        turn_id: str | None = None,
        agent_item_id: str | None = None,
        reaction_applied: bool | None = None,
    ) -> _FakeReplyRecord | None:
        current = self.records.get(reply_message_id)
        if current is None:
            return None
        updated = _FakeReplyRecord(
            reply_message_id=reply_message_id,
            turn_id=turn_id if turn_id is not None else current.turn_id,
            agent_item_id=agent_item_id if agent_item_id is not None else current.agent_item_id,
            status=status if status is not None else current.status,
            reaction_applied=(
                reaction_applied if reaction_applied is not None else current.reaction_applied
            ),
        )
        self.records[reply_message_id] = updated
        return updated


class _FakeSessionExecutor:
    def __init__(self) -> None:
        self.activated: list[tuple[str, str]] = []
        self.completed: list[tuple[str, str]] = []

    async def activate_turn(self, session_scope_key: str, turn_id: str) -> None:
        self.activated.append((session_scope_key, turn_id))

    async def complete_turn(self, session_scope_key: str, turn_id: str) -> None:
        self.completed.append((session_scope_key, turn_id))


class _FakeClassifier:
    def __init__(self, *events) -> None:
        self._events = list(events)

    def classify(self, _notification: CodexNotification):
        return list(self._events)


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


def test_reply_service_uses_streaming_card_updates(tmp_path: Path) -> None:
    feishu_adapter = _FakeFeishuAdapter()
    reply_repository = _FakeReplyRepository()
    session_executor = _FakeSessionExecutor()
    classifier = _FakeClassifier(
        CodexTextDeltaEvent(
            channel="agentMessage",
            text="你好，世界",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="item-1",
        ),
        CodexTurnLifecycleEvent(
            phase="completed",
            thread_id="thread-1",
            turn_id="turn-1",
            status="completed",
            error=None,
        ),
    )
    service = ReplyService(
        _build_config(tmp_path),
        feishu_adapter=feishu_adapter,
        reply_repository=reply_repository,
        session_executor=session_executor,
        classifier=classifier,
    )

    async def _run() -> bool:
        await service.start_turn(
            session_scope_key="p2p:ou_owner",
            source_message_id="om_source",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        return await service.handle_notification(
            CodexNotification(
                method="test.notification",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id=None,
                request_id=None,
            )
        )

    handled = asyncio.run(_run())

    assert handled is True
    assert feishu_adapter.reply_cards == [
        {
            "message_id": "om_source",
            "text": "你好，世界",
            "reply_in_thread": False,
            "status": "streaming",
        }
    ]
    assert feishu_adapter.card_updates == [
        {
            "card_id": "card-1",
            "text": "你好，世界",
            "status": "completed",
            "sequence": 2,
        },
    ]
    assert feishu_adapter.card_streaming_modes == [
        {
            "card_id": "card-1",
            "enabled": False,
            "sequence": 4,
        }
    ]
    assert feishu_adapter.added_reactions == [
        {
            "message_id": "om_source",
            "emoji_type": "Typing",
        }
    ]
    assert feishu_adapter.removed_reactions == [
        {
            "message_id": "om_source",
            "reaction_id": "reaction-1",
        }
    ]
    assert reply_repository.records["reply-message-1"].status == "completed"
    assert reply_repository.records["reply-message-1"].reaction_applied is False
    assert session_executor.activated == [("p2p:ou_owner", "turn-1")]
    assert session_executor.completed == [("p2p:ou_owner", "turn-1")]


def test_reply_service_fail_turn_updates_failed_card(tmp_path: Path) -> None:
    feishu_adapter = _FakeFeishuAdapter()
    reply_repository = _FakeReplyRepository()
    session_executor = _FakeSessionExecutor()
    service = ReplyService(
        _build_config(tmp_path),
        feishu_adapter=feishu_adapter,
        reply_repository=reply_repository,
        session_executor=session_executor,
    )

    async def _run() -> bool:
        await service.start_turn(
            session_scope_key="p2p:ou_owner",
            source_message_id="om_source",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        return await service.fail_turn("turn-1")

    failed = asyncio.run(_run())

    assert failed is True
    assert feishu_adapter.reply_cards == []
    assert feishu_adapter.card_updates == []
    assert feishu_adapter.card_streaming_modes == []
    assert reply_repository.records == {}
    assert feishu_adapter.removed_reactions == [
        {
            "message_id": "om_source",
            "reaction_id": "reaction-1",
        }
    ]
    assert session_executor.completed == [("p2p:ou_owner", "turn-1")]
    assert feishu_adapter.failure_cards == []


def test_reply_service_starts_new_card_after_approval_followup(tmp_path: Path) -> None:
    feishu_adapter = _FakeFeishuAdapter()
    reply_repository = _FakeReplyRepository()
    session_executor = _FakeSessionExecutor()
    classifier = _FakeClassifier(
        CodexTextDeltaEvent(
            channel="agentMessage",
            text="审批前方案",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="item-1",
        ),
    )
    service = ReplyService(
        _build_config(tmp_path),
        feishu_adapter=feishu_adapter,
        reply_repository=reply_repository,
        session_executor=session_executor,
        classifier=classifier,
    )

    async def _run() -> None:
        await service.start_turn(
            session_scope_key="p2p:ou_owner",
            source_message_id="om_source",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        await service.handle_notification(
            CodexNotification(
                method="notification.before_approval",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id=None,
                request_id=None,
            )
        )
        await service.start_followup_turn("turn-1")
        classifier._events = [
            CodexTextDeltaEvent(
                channel="agentMessage",
                text="审批后执行结果",
                thread_id="thread-1",
                turn_id="turn-1",
                item_id="item-2",
            ),
            CodexTurnLifecycleEvent(
                phase="completed",
                thread_id="thread-1",
                turn_id="turn-1",
                status="completed",
                error=None,
            ),
        ]
        await service.handle_notification(
            CodexNotification(
                method="notification.after_approval",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id=None,
                request_id=None,
            )
        )

    asyncio.run(_run())

    assert feishu_adapter.reply_cards == [
        {
            "message_id": "om_source",
            "text": "审批前方案",
            "reply_in_thread": False,
            "status": "streaming",
        },
        {
            "message_id": "om_source",
            "text": "审批后执行结果",
            "reply_in_thread": False,
            "status": "streaming",
        },
    ]
    assert feishu_adapter.card_updates == [
        {
            "card_id": "card-1",
            "text": "审批前方案",
            "status": "completed",
            "sequence": 2,
        },
        {
            "card_id": "card-2",
            "text": "审批后执行结果",
            "status": "completed",
            "sequence": 2,
        },
    ]
    assert feishu_adapter.card_streaming_modes == [
        {
            "card_id": "card-1",
            "enabled": False,
            "sequence": 4,
        },
        {
            "card_id": "card-2",
            "enabled": False,
            "sequence": 4,
        },
    ]
    assert reply_repository.records["reply-message-1"].status == "superseded"
    assert reply_repository.records["reply-message-2"].status == "completed"


def test_reply_service_does_not_create_followup_card_until_output_arrives(tmp_path: Path) -> None:
    feishu_adapter = _FakeFeishuAdapter()
    reply_repository = _FakeReplyRepository()
    session_executor = _FakeSessionExecutor()
    service = ReplyService(
        _build_config(tmp_path),
        feishu_adapter=feishu_adapter,
        reply_repository=reply_repository,
        session_executor=session_executor,
    )

    async def _run() -> None:
        await service.start_turn(
            session_scope_key="p2p:ou_owner",
            source_message_id="om_source",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        started = await service.start_followup_turn("turn-1")
        assert started is True

    asyncio.run(_run())

    assert feishu_adapter.reply_cards == []
    assert feishu_adapter.card_updates == []
    assert feishu_adapter.card_streaming_modes == []
    assert reply_repository.records == {}


def test_reply_service_caps_update_rate_at_ten_per_second(tmp_path: Path) -> None:
    service = ReplyService(
        _build_config(tmp_path),
        feishu_adapter=_FakeFeishuAdapter(),
        reply_repository=_FakeReplyRepository(),
        session_executor=_FakeSessionExecutor(),
        update_interval_seconds=0.01,
    )

    assert service._update_interval_seconds == 0.1


def test_reply_service_sends_failure_card_when_turn_fails_before_any_reply(tmp_path: Path) -> None:
    feishu_adapter = _FakeFeishuAdapter()
    reply_repository = _FakeReplyRepository()
    session_executor = _FakeSessionExecutor()
    classifier = _FakeClassifier(
        CodexTurnErrorEvent(
            error={
                "message": "The model server disconnected.",
                "additionalDetails": "response stream closed unexpectedly",
            },
            thread_id="thread-1",
            turn_id="turn-1",
            item_id=None,
            will_retry=False,
        ),
        CodexTurnLifecycleEvent(
            phase="completed",
            thread_id="thread-1",
            turn_id="turn-1",
            status="failed",
            error={
                "message": "The model server disconnected.",
                "additionalDetails": "response stream closed unexpectedly",
            },
        ),
    )
    service = ReplyService(
        _build_config(tmp_path),
        feishu_adapter=feishu_adapter,
        reply_repository=reply_repository,
        session_executor=session_executor,
        classifier=classifier,
    )

    async def _run() -> None:
        await service.start_turn(
            session_scope_key="p2p:ou_owner",
            source_message_id="om_source",
            thread_id="thread-1",
            turn_id="turn-1",
        )
        await service.handle_notification(
            CodexNotification(
                method="notification.failed",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id=None,
                request_id=None,
            )
        )

    asyncio.run(_run())

    assert feishu_adapter.reply_cards == []
    assert feishu_adapter.failure_cards == [
        {
            "message_id": "om_source",
            "error_text": (
                "错误原因：The model server disconnected.\n\n"
                "附加信息：response stream closed unexpectedly"
            ),
            "reply_in_thread": False,
        }
    ]
    assert feishu_adapter.card_updates == []
    assert feishu_adapter.card_streaming_modes == []
    assert reply_repository.records["reply-message-1"].status == "failed"
