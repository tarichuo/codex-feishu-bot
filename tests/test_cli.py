from __future__ import annotations

from pathlib import Path
import asyncio
import sys
import types

import pytest

from feishu_codex_bot.config import ConfigError
from feishu_codex_bot.models.actions import CodexNotification, CodexServerRequest

_codex_client_stub = types.ModuleType("feishu_codex_bot.adapters.codex_client")
_codex_client_stub.DEFER_SERVER_REQUEST = object()
_codex_client_stub.CodexClient = object
sys.modules.setdefault("feishu_codex_bot.adapters.codex_client", _codex_client_stub)

from feishu_codex_bot.cli import (
    CodexCliApp,
    _ActiveTurnState,
    build_approval_response_payload,
    build_cli_config,
)
from feishu_codex_bot.models.actions import (
    CodexTextDeltaEvent,
    CodexTextMessageEvent,
    CodexTurnLifecycleEvent,
)


def test_build_cli_config_only_requires_codex_server_url(tmp_path: Path) -> None:
    config = build_cli_config(
        {
            "FEISHU_CODEX_BOT_CODEX_SERVER_URL": "ws://127.0.0.1:9000",
            "FEISHU_CODEX_BOT_LOG_LEVEL": "debug",
        },
        base_dir=tmp_path,
    )

    assert config.codex.server_url == "ws://127.0.0.1:9000"
    assert config.logging.level == "DEBUG"
    assert config.storage.base_dir == tmp_path.resolve()


def test_build_cli_config_requires_server_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_cli_config({}, base_dir=tmp_path)


def test_build_approval_response_payload_for_command_request() -> None:
    request = CodexServerRequest(
        id="req-1",
        method="item/commandExecution/requestApproval",
        params={},
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
    )

    payload = build_approval_response_payload(
        request=request,
        decision="acceptForSession",
        scope="session",
    )

    assert payload == {"decision": "acceptForSession"}


def test_build_approval_response_payload_for_permissions_request() -> None:
    request = CodexServerRequest(
        id="req-2",
        method="item/permissions/requestApproval",
        params={"permissions": {"network": {"enabled": True}}},
        thread_id="thread-1",
        turn_id="turn-1",
        item_id="item-1",
    )

    payload = build_approval_response_payload(
        request=request,
        decision="accept",
        scope="session",
    )

    assert payload == {
        "permissions": {"network": {"enabled": True}},
        "scope": "session",
    }


def test_cli_accepts_notifications_before_turn_start_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self) -> None:
            self.notifications = []

    class _FakeClassifier:
        def classify(self, _notification: CodexNotification):
            return [
                CodexTextDeltaEvent(
                    channel="agentMessage",
                    text="hello",
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
            ]

    app = object.__new__(CodexCliApp)
    app._config = build_cli_config(
        {"FEISHU_CODEX_BOT_CODEX_SERVER_URL": "ws://127.0.0.1:9000"},
        base_dir=tmp_path,
    )
    app._logger = None
    app._client = _FakeClient()
    app._classifier = _FakeClassifier()
    app._thread_id = "thread-1"
    app._active_turn = _ActiveTurnState()
    app._pending_requests = {}

    captured: list[str] = []
    monkeypatch.setattr(app, "_write_output", lambda text: captured.append(text))

    async def _run() -> None:
        await app._handle_notification(
            CodexNotification(
                method="item/agentMessage/delta",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id="item-1",
                request_id=None,
            )
        )

    asyncio.run(_run())

    assert captured == ["hello"]
    assert app._active_turn is None


def test_cli_prefers_delta_output_over_completed_full_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClassifier:
        def __init__(self) -> None:
            self.calls = 0

        def classify(self, _notification: CodexNotification):
            self.calls += 1
            if self.calls == 1:
                return [
                    CodexTextDeltaEvent(
                        channel="agentMessage",
                        text="he",
                        thread_id="thread-1",
                        turn_id="turn-1",
                        item_id="item-1",
                    )
                ]
            if self.calls == 2:
                return [
                    CodexTextMessageEvent(
                        channel="agentMessage",
                        text="hello",
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
                ]
            return []

    app = object.__new__(CodexCliApp)
    app._config = build_cli_config(
        {"FEISHU_CODEX_BOT_CODEX_SERVER_URL": "ws://127.0.0.1:9000"},
        base_dir=tmp_path,
    )
    app._logger = None
    app._client = object()
    app._classifier = _FakeClassifier()
    app._thread_id = "thread-1"
    app._active_turn = _ActiveTurnState(turn_id="turn-1", status_printed=True)
    app._pending_requests = {}

    captured_output: list[str] = []
    monkeypatch.setattr(app, "_write_output", lambda text: captured_output.append(text))

    async def _run() -> None:
        await app._handle_notification(
            CodexNotification(
                method="item/agentMessage/delta",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id="item-1",
                request_id=None,
            )
        )
        await app._handle_notification(
            CodexNotification(
                method="item/completed",
                params={},
                thread_id="thread-1",
                turn_id="turn-1",
                item_id="item-1",
                request_id=None,
            )
        )

    asyncio.run(_run())

    assert captured_output == ["he"]
    assert app._active_turn is None


def test_cli_prints_thinking_status_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = object.__new__(CodexCliApp)
    active_turn = _ActiveTurnState()

    app._print_thinking_status(active_turn)
    app._print_thinking_status(active_turn)

    captured = capsys.readouterr()
    assert captured.out == "正在思考中...\n"
