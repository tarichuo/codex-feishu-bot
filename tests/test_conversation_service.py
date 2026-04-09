from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from feishu_codex_bot.models.inbound import InboundMessage, MentionRef, TextContent
from feishu_codex_bot.persistence.session_repo import SessionRecord


_codex_client_stub = types.ModuleType("feishu_codex_bot.adapters.codex_client")
_codex_client_stub.CodexClient = object
_codex_client_stub.DEFER_SERVER_REQUEST = object()
sys.modules.setdefault("feishu_codex_bot.adapters.codex_client", _codex_client_stub)

_feishu_adapter_stub = types.ModuleType("feishu_codex_bot.adapters.feishu_adapter")
_feishu_adapter_stub.FeishuAdapter = object
_feishu_adapter_stub.FeishuReplyCardRef = object
sys.modules.setdefault("feishu_codex_bot.adapters.feishu_adapter", _feishu_adapter_stub)

_media_service_stub = types.ModuleType("feishu_codex_bot.services.media_service")
_media_service_stub.MediaService = object
sys.modules.setdefault("feishu_codex_bot.services.media_service", _media_service_stub)

_session_executor_stub = types.ModuleType("feishu_codex_bot.workers.session_executor")
_session_executor_stub.SessionExecutor = object
sys.modules.setdefault("feishu_codex_bot.workers.session_executor", _session_executor_stub)

from feishu_codex_bot.services.conversation_service import ConversationService


@dataclass(frozen=True)
class _FakeThread:
    id: str


@dataclass(frozen=True)
class _FakeTurn:
    id: str


class _FakeCodexClient:
    def __init__(self) -> None:
        self.thread_counter = 0
        self.turn_counter = 0
        self.resume_calls: list[str] = []
        self.start_turn_calls: list[object] = []

    async def start_thread(self, _options) -> _FakeThread:
        self.thread_counter += 1
        return _FakeThread(id=f"thread-{self.thread_counter}")

    async def resume_thread(self, options) -> _FakeThread:
        self.resume_calls.append(options.thread_id)
        return _FakeThread(id=options.thread_id)

    async def start_turn(self, options) -> _FakeTurn:
        self.turn_counter += 1
        self.start_turn_calls.append(options)
        return _FakeTurn(id=f"turn-{self.turn_counter}")


class _FakeFeishuAdapter:
    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []
        self.owner_alerts: list[tuple[str, str]] = []

    def reply_text(self, *, message_id: str, text: str) -> str:
        self.replies.append((message_id, text))
        return f"reply-{len(self.replies)}"

    def send_owner_alert(self, *, owner_open_id: str, text: str) -> str:
        self.owner_alerts.append((owner_open_id, text))
        return f"alert-{len(self.owner_alerts)}"


class _FakeDownloadedMedia:
    def __init__(self, path: Path) -> None:
        self.local_path = path


class _FakeMediaService:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.download_calls: list[tuple[str, str | None]] = []

    def download_image(self, image_key: str, *, source_message_id: str | None = None) -> _FakeDownloadedMedia:
        self.download_calls.append((image_key, source_message_id))
        return _FakeDownloadedMedia(self.base_dir / f"{image_key}.png")


class _FakeSessionRepository:
    def __init__(self, bot_app_id: str) -> None:
        self.bot_app_id = bot_app_id
        self.records: dict[str, SessionRecord] = {}
        self.sequence = 0

    def get_by_scope_key(self, scope_key: str) -> SessionRecord | None:
        return self.records.get(scope_key)

    def upsert_session(
        self,
        *,
        scope_type: str,
        scope_key: str,
        bot_app_id: str,
        thread_id: str,
        user_open_id: str | None = None,
        chat_id: str | None = None,
        thread_generation: int = 1,
        last_message_at: str | None = None,
        expires_at: str | None = None,
        status: str = "active",
    ) -> SessionRecord:
        self.sequence += 1
        existing = self.records.get(scope_key)
        created_at = existing.created_at if existing else "2026-01-01T00:00:00+00:00"
        record = SessionRecord(
            id=existing.id if existing else self.sequence,
            scope_type=scope_type,
            scope_key=scope_key,
            bot_app_id=bot_app_id,
            user_open_id=user_open_id,
            chat_id=chat_id,
            thread_id=thread_id,
            thread_generation=thread_generation,
            last_message_at=last_message_at,
            expires_at=expires_at,
            status=status,
            created_at=created_at,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.records[scope_key] = record
        return record

    def touch_session(
        self,
        scope_key: str,
        *,
        last_message_at: str,
        expires_at: str | None,
    ) -> SessionRecord | None:
        existing = self.records.get(scope_key)
        if existing is None:
            return None
        updated = SessionRecord(
            id=existing.id,
            scope_type=existing.scope_type,
            scope_key=existing.scope_key,
            bot_app_id=existing.bot_app_id,
            user_open_id=existing.user_open_id,
            chat_id=existing.chat_id,
            thread_id=existing.thread_id,
            thread_generation=existing.thread_generation,
            last_message_at=last_message_at,
            expires_at=expires_at,
            status=existing.status,
            created_at=existing.created_at,
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.records[scope_key] = updated
        return updated


class _FakeDedupeRepository:
    def __init__(self) -> None:
        self.accepted: set[tuple[str, str]] = set()
        self.status_updates: list[dict[str, str | None]] = []

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
        key = (bot_app_id, feishu_message_id)
        if key in self.accepted:
            return False
        self.accepted.add(key)
        return True

    def update_status(
        self,
        *,
        bot_app_id: str,
        feishu_message_id: str,
        status: str,
        turn_id: str | None = None,
        session_scope_key: str | None = None,
    ) -> dict[str, str | None]:
        payload = {
            "bot_app_id": bot_app_id,
            "feishu_message_id": feishu_message_id,
            "status": status,
            "turn_id": turn_id,
            "session_scope_key": session_scope_key,
        }
        self.status_updates.append(payload)
        return payload


class _FakeSecurityDecision:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed


class _FakeSecurityAlert:
    def __init__(self, alert_id: int) -> None:
        self.id = alert_id


class _FakeSecurityService:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.recorded_attempts = []
        self.sent_alerts = []
        self.failed_alerts = []

    def evaluate_user(self, sender_user_id: str) -> _FakeSecurityDecision:
        return _FakeSecurityDecision(allowed=self.allowed)

    def record_unauthorized_attempt(self, message) -> _FakeSecurityAlert:
        self.recorded_attempts.append(message)
        return _FakeSecurityAlert(alert_id=len(self.recorded_attempts))

    def mark_alert_sent(self, alert_id: int, *, owner_alert_message_id: str):
        self.sent_alerts.append((alert_id, owner_alert_message_id))
        return None

    def mark_alert_failed(self, alert_id: int):
        self.failed_alerts.append(alert_id)
        return None


class _FakeSessionExecutor:
    async def run(self, _scope_key: str, callback):
        return await callback()


def _build_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        feishu=FeishuConfig(app_id="cli_test_app", app_secret="secret"),
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
            allowed_user_ids=frozenset({"ou_owner", "ou_allowed"}),
        ),
        logging=LoggingConfig(level="DEBUG"),
    )


def _build_service(tmp_path: Path, *, allowed: bool = True) -> tuple[ConversationService, _FakeCodexClient, _FakeFeishuAdapter, _FakeSessionRepository, _FakeDedupeRepository, _FakeSecurityService]:
    config = _build_config(tmp_path)
    codex_client = _FakeCodexClient()
    feishu_adapter = _FakeFeishuAdapter()
    session_repository = _FakeSessionRepository(config.feishu.app_id)
    dedupe_repository = _FakeDedupeRepository()
    security_service = _FakeSecurityService(allowed=allowed)
    service = ConversationService(
        config,
        codex_client=codex_client,
        feishu_adapter=feishu_adapter,
        media_service=_FakeMediaService(tmp_path),
        session_repository=session_repository,
        dedupe_repository=dedupe_repository,
        security_service=security_service,
        session_executor=_FakeSessionExecutor(),
    )
    return service, codex_client, feishu_adapter, session_repository, dedupe_repository, security_service


def _build_message(
    *,
    message_id: str,
    text: str,
    created_at: datetime,
    chat_type: str = "p2p",
    chat_id: str = "chat-1",
    sender_open_id: str = "ou_allowed",
    mentions: tuple[MentionRef, ...] = (),
) -> InboundMessage:
    return InboundMessage(
        event_id=f"evt-{message_id}",
        event_type="im.message.receive_v1",
        tenant_key="tenant",
        app_id="cli_test_app",
        sender_open_id=sender_open_id,
        sender_user_id=None,
        sender_union_id=None,
        sender_type="user",
        message_id=message_id,
        root_id=None,
        parent_id=None,
        chat_id=chat_id,
        thread_id=None,
        chat_type=chat_type,
        message_type="text",
        mentions=mentions,
        parts=(TextContent(text=text),),
        raw_content=text,
        raw_payload={"text": text},
        created_at=created_at,
        updated_at=None,
    )


def test_dm_reuses_existing_thread_within_one_hour(tmp_path: Path) -> None:
    service, codex_client, _, session_repository, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    scope_key = "cli_test_app:ou_allowed"
    session_repository.upsert_session(
        scope_type="dm",
        scope_key=scope_key,
        bot_app_id="cli_test_app",
        thread_id="thread-existing",
        user_open_id="ou_allowed",
        chat_id="chat-dm",
        thread_generation=1,
        last_message_at=(now - timedelta(minutes=10)).isoformat(),
        expires_at=(now + timedelta(minutes=50)).isoformat(),
    )

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-1",
                text="你好",
                created_at=now,
                chat_id="chat-dm",
            )
        )
    )

    assert result.status == "submitted"
    assert result.thread_id == "thread-existing"
    assert codex_client.resume_calls == ["thread-existing"]
    assert codex_client.thread_counter == 0


def test_dm_rotates_thread_after_one_hour(tmp_path: Path) -> None:
    service, codex_client, _, session_repository, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    scope_key = "cli_test_app:ou_allowed"
    session_repository.upsert_session(
        scope_type="dm",
        scope_key=scope_key,
        bot_app_id="cli_test_app",
        thread_id="thread-expired",
        user_open_id="ou_allowed",
        chat_id="chat-dm",
        thread_generation=1,
        last_message_at=(now - timedelta(hours=2)).isoformat(),
        expires_at=(now - timedelta(minutes=1)).isoformat(),
    )

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-2",
                text="继续",
                created_at=now,
                chat_id="chat-dm",
            )
        )
    )

    assert result.status == "submitted"
    assert result.thread_id == "thread-1"
    assert result.thread_generation == 2
    assert codex_client.resume_calls == []


def test_group_thread_persists_without_expiration(tmp_path: Path) -> None:
    service, codex_client, _, session_repository, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    mentions = (MentionRef(key="@_user_1", name="bot", open_id="ou_bot", user_id=None, union_id=None),)
    scope_key = "cli_test_app:chat-group"
    session_repository.upsert_session(
        scope_type="group",
        scope_key=scope_key,
        bot_app_id="cli_test_app",
        thread_id="thread-group",
        user_open_id=None,
        chat_id="chat-group",
        thread_generation=1,
        last_message_at=(now - timedelta(days=10)).isoformat(),
        expires_at=None,
    )

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-3",
                text="@_user_1 继续讨论",
                created_at=now,
                chat_type="group",
                chat_id="chat-group",
                mentions=mentions,
            )
        )
    )

    assert result.status == "submitted"
    assert result.thread_id == "thread-group"
    assert codex_client.resume_calls == ["thread-group"]
    assert codex_client.start_turn_calls[0].input_items[0].text == "继续讨论"


def test_clear_rotates_thread(tmp_path: Path) -> None:
    service, codex_client, _, session_repository, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    scope_key = "cli_test_app:ou_allowed"
    session_repository.upsert_session(
        scope_type="dm",
        scope_key=scope_key,
        bot_app_id="cli_test_app",
        thread_id="thread-existing",
        user_open_id="ou_allowed",
        chat_id="chat-dm",
        thread_generation=2,
        last_message_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=59)).isoformat(),
    )

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-4",
                text="/clear",
                created_at=now,
                chat_id="chat-dm",
            )
        )
    )

    assert result.status == "submitted"
    assert result.is_slash_command is True
    assert result.slash_command == "/clear"
    assert result.thread_id == "thread-1"
    assert result.thread_generation == 3
    assert codex_client.resume_calls == []


def test_group_message_strips_all_mention_placeholders_before_dispatch(tmp_path: Path) -> None:
    service, codex_client, _, session_repository, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    mentions = (
        MentionRef(key="@_user_1", name="bot", open_id="ou_bot", user_id=None, union_id=None),
        MentionRef(key="@_user_2", name="user", open_id="ou_user", user_id=None, union_id=None),
    )
    scope_key = "cli_test_app:chat-group"
    session_repository.upsert_session(
        scope_type="group",
        scope_key=scope_key,
        bot_app_id="cli_test_app",
        thread_id="thread-group",
        user_open_id=None,
        chat_id="chat-group",
        thread_generation=1,
        last_message_at=(now - timedelta(minutes=5)).isoformat(),
        expires_at=None,
    )

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-group-clean",
                text=" @_user_1   你好  @_user_2  世界 ",
                created_at=now,
                chat_type="group",
                chat_id="chat-group",
                mentions=mentions,
            )
        )
    )

    assert result.status == "submitted"
    assert codex_client.start_turn_calls[0].input_items[0].text == "你好 世界"


def test_group_slash_command_is_detected_after_placeholder_cleanup(tmp_path: Path) -> None:
    service, codex_client, _, session_repository, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    mentions = (MentionRef(key="@_user_1", name="bot", open_id="ou_bot", user_id=None, union_id=None),)
    scope_key = "cli_test_app:chat-group"
    session_repository.upsert_session(
        scope_type="group",
        scope_key=scope_key,
        bot_app_id="cli_test_app",
        thread_id="thread-group",
        user_open_id=None,
        chat_id="chat-group",
        thread_generation=2,
        last_message_at=now.isoformat(),
        expires_at=None,
    )

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-group-slash",
                text="@_user_1   /clear",
                created_at=now,
                chat_type="group",
                chat_id="chat-group",
                mentions=mentions,
            )
        )
    )

    assert result.status == "submitted"
    assert result.is_slash_command is True
    assert result.slash_command == "/clear"
    assert codex_client.start_turn_calls[0].input_items[0].text == "/clear"


def test_unsupported_slash_command_is_rejected(tmp_path: Path) -> None:
    service, _, feishu_adapter, _, dedupe_repository, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)

    result = asyncio.run(
        service.handle_message(
            _build_message(
                message_id="msg-5",
                text="/unknown abc",
                created_at=now,
                chat_id="chat-dm",
            )
        )
    )

    assert result.status == "rejected_unsupported_slash"
    assert result.reply_text is not None
    assert "不支持的slash命令" in result.reply_text
    assert feishu_adapter.replies == [("msg-5", result.reply_text)]
    assert dedupe_repository.status_updates[-1]["status"] == "ignored_unsupported_slash"


def test_duplicate_message_is_ignored(tmp_path: Path) -> None:
    service, codex_client, _, _, _, _ = _build_service(tmp_path)
    now = datetime.now(tz=timezone.utc)
    message = _build_message(
        message_id="msg-6",
        text="第一次",
        created_at=now,
        chat_id="chat-dm",
    )

    first = asyncio.run(service.handle_message(message))
    second = asyncio.run(service.handle_message(message))

    assert first.status == "submitted"
    assert second.status == "ignored_duplicate"
    assert len(codex_client.start_turn_calls) == 1
