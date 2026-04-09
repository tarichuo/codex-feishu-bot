"""用于隔离测试 Codex app server 的命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import sys

from feishu_codex_bot import __version__
from feishu_codex_bot.adapters.codex_client import DEFER_SERVER_REQUEST, CodexClient
from feishu_codex_bot.adapters.codex_output_classifier import CodexOutputClassifier
from feishu_codex_bot.config import (
    ENV_PREFIX,
    AppConfig,
    CodexConfig,
    ConfigError,
    FeishuConfig,
    LoggingConfig,
    SecurityConfig,
    StorageConfig,
)
from feishu_codex_bot.logging import get_logger
from feishu_codex_bot.models.actions import (
    CodexApprovalRequestEvent,
    CodexCommandEvent,
    CodexFileReferenceEvent,
    CodexNotification,
    CodexServerRequest,
    CodexTextDeltaEvent,
    CodexTextInput,
    CodexTextMessageEvent,
    CodexTurnLifecycleEvent,
    CodexUnknownEvent,
    CodexUserInputRequestEvent,
    ThreadStartOptions,
    TurnStartOptions,
)


def _env_name(name: str) -> str:
    return f"{ENV_PREFIX}{name}"


def build_cli_config(
    env: Mapping[str, str] | None = None,
    *,
    base_dir: Path | None = None,
    server_url: str | None = None,
) -> AppConfig:
    """构造仅用于本地 CLI 调试的最小配置。"""
    env_map = env if env is not None else os.environ
    project_root = (base_dir or Path.cwd()).resolve()
    codex_server_url = (server_url or env_map.get(_env_name("CODEX_SERVER_URL"), "")).strip()
    if not codex_server_url:
        raise ConfigError(f"Missing required environment variable: {_env_name('CODEX_SERVER_URL')}")

    log_level = env_map.get(_env_name("LOG_LEVEL"), "INFO").strip().upper() or "INFO"
    data_dir = (project_root / "var").resolve()
    logs_dir = (data_dir / "logs").resolve()
    media_dir = (data_dir / "media").resolve()
    sqlite_path = (data_dir / "cli.db").resolve()

    return AppConfig(
        feishu=FeishuConfig(app_id="cli_debug", app_secret="cli_debug"),
        codex=CodexConfig(server_url=codex_server_url),
        storage=StorageConfig(
            base_dir=project_root,
            data_dir=data_dir,
            sqlite_path=sqlite_path,
            media_dir=media_dir,
            logs_dir=logs_dir,
        ),
        security=SecurityConfig(
            owner_user_id="cli_debug",
            allowed_user_ids=frozenset({"cli_debug"}),
        ),
        logging=LoggingConfig(level=log_level),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="feishu-codex-bot-cli",
        description="Direct CLI for testing the local Codex app server without Feishu.",
    )
    parser.add_argument(
        "--server-url",
        help=f"Codex app server URL. Defaults to {_env_name('CODEX_SERVER_URL')}.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


@dataclass(slots=True)
class _ActiveTurnState:
    turn_id: str | None = None
    printed_text: str = ""
    saw_delta: bool = False
    status_printed: bool = False
    completed: asyncio.Event = field(default_factory=asyncio.Event)


class CodexCliApp:
    """命令行版 Codex 会话调试器。"""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._logger = get_logger(__name__, component="codex_cli")
        self._client = CodexClient(
            config,
            logger=self._logger.bind(codex_server_url=config.codex.server_url),
        )
        self._classifier = CodexOutputClassifier()
        self._thread_id: str | None = None
        self._active_turn: _ActiveTurnState | None = None
        self._pending_requests: dict[str, CodexServerRequest] = {}

    async def run(self) -> int:
        self._client.register_notification_handler("*", self._handle_notification)
        self._client.register_server_request_handler("*", self._handle_server_request)
        await self._client.connect()
        await self._client.initialize()
        thread = await self._client.start_thread(
            ThreadStartOptions(cwd=self._config.storage.base_dir)
        )
        self._thread_id = thread.id
        print(f"已连接 Codex app server，新 thread: {thread.id}")
        print("输入内容后回车发送；输入 exit 或 quit 退出；输入 /help 查看命令。")

        try:
            return await self._repl()
        finally:
            await self._client.close()

    async def _repl(self) -> int:
        while True:
            try:
                raw = await asyncio.to_thread(input, "codex> ")
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print()
                return 130

            text = raw.strip()
            if not text:
                continue
            if text in {"exit", "quit"}:
                return 0
            if await self._handle_local_command(text):
                continue
            if self._active_turn is not None and not self._active_turn.completed.is_set():
                print("当前已有进行中的 turn，请等待完成，或先处理审批命令。")
                continue
            await self._start_turn(text)

    async def _start_turn(self, text: str) -> None:
        if self._thread_id is None:
            raise RuntimeError("CLI thread has not been initialized")
        active_turn = _ActiveTurnState()
        self._active_turn = active_turn
        self._print_thinking_status(active_turn)
        turn = await self._client.start_turn(
            TurnStartOptions(
                thread_id=self._thread_id,
                input_items=(CodexTextInput(text=text),),
                cwd=self._config.storage.base_dir,
            )
        )
        active_turn.turn_id = turn.id
        print(f"[turn started] {turn.id}")

    async def _handle_local_command(self, text: str) -> bool:
        if not text.startswith("/"):
            return False

        try:
            tokens = shlex.split(text)
        except ValueError as exc:
            print(f"命令解析失败: {exc}")
            return True

        if not tokens:
            return True
        command = tokens[0].lower()
        if command == "/help":
            self._print_help()
            return True
        if command == "/pending":
            self._print_pending_requests()
            return True
        if command == "/approve":
            await self._handle_approve_command(tokens)
            return True

        print(f"不支持的本地命令: {command}")
        return True

    async def _handle_approve_command(self, tokens: list[str]) -> None:
        if len(tokens) < 3:
            print("用法: /approve <request_id> <accept|acceptForSession|decline|cancel> [scope=turn|session]")
            return

        request_id = tokens[1]
        decision = tokens[2]
        scope = "turn"
        for token in tokens[3:]:
            if token.startswith("scope="):
                scope = token.split("=", 1)[1] or "turn"

        request = self._pending_requests.get(request_id)
        if request is None:
            print(f"未找到待处理审批请求: {request_id}")
            return

        response_payload = build_approval_response_payload(
            request=request,
            decision=decision,
            scope=scope,
        )
        await self._client.respond_to_server_request(request.id, response_payload)
        self._pending_requests.pop(request_id, None)
        print(f"已提交审批响应: request_id={request_id} decision={decision}")

    async def _handle_notification(self, notification: CodexNotification) -> None:
        for event in self._classifier.classify(notification):
            active_turn = self._active_turn
            if active_turn is None:
                continue
            if active_turn.turn_id is None and event.turn_id is not None:
                active_turn.turn_id = event.turn_id
            if active_turn.turn_id is not None and event.turn_id != active_turn.turn_id:
                continue

            if isinstance(event, CodexTextDeltaEvent) and event.channel == "agentMessage":
                active_turn.saw_delta = True
                self._write_output(event.text)
                active_turn.printed_text += event.text
                continue

            if isinstance(event, CodexTextMessageEvent) and event.channel == "agentMessage":
                if not active_turn.saw_delta and event.text:
                    self._write_output(event.text)
                    active_turn.printed_text = event.text
                continue

            if isinstance(event, CodexTurnLifecycleEvent) and event.phase == "completed":
                if active_turn.printed_text and not active_turn.printed_text.endswith("\n"):
                    print()
                if event.error:
                    print(f"[turn failed] {active_turn.turn_id or '-'}: {event.error}")
                else:
                    print("已完成")
                active_turn.completed.set()
                self._active_turn = None
                continue

            if isinstance(event, CodexCommandEvent):
                delta = event.delta or event.aggregated_output or ""
                if delta:
                    print(f"[command] {delta}", file=sys.stderr)
                continue

            if isinstance(event, CodexFileReferenceEvent):
                print(f"[file] {event.path}", file=sys.stderr)
                continue

            if isinstance(event, CodexUnknownEvent):
                self._logger.bind(
                    event="codex_cli.unknown_notification",
                    source=event.source,
                    turn_id=event.turn_id,
                    item_id=event.item_id,
                ).info("Ignored unknown notification in CLI")

    async def _handle_server_request(self, request: CodexServerRequest) -> object:
        request_id = str(request.id)
        self._pending_requests[request_id] = request
        print()
        print(f"[server request] id={request_id} method={request.method}")
        for event in self._classifier.classify(request):
            if isinstance(event, CodexApprovalRequestEvent):
                print(f"  审批类型: {event.approval_type}")
                print("  处理方式: /approve <request_id> <accept|acceptForSession|decline|cancel> [scope=turn|session]")
            elif isinstance(event, CodexUserInputRequestEvent):
                print("  当前 CLI 暂不支持 request_user_input，请改用简单文本测试。")
            else:
                print("  收到未识别的 server request。")
        print()
        return DEFER_SERVER_REQUEST

    def _write_output(self, text: str) -> None:
        if not text:
            return
        print(text, end="", flush=True)

    def _print_thinking_status(self, active_turn: _ActiveTurnState) -> None:
        if active_turn.status_printed:
            return
        print("正在思考中...")
        active_turn.status_printed = True

    def _print_help(self) -> None:
        print("本地命令：")
        print("  /help")
        print("  /pending")
        print("  /approve <request_id> <accept|acceptForSession|decline|cancel> [scope=turn|session]")
        print("  exit | quit")

    def _print_pending_requests(self) -> None:
        if not self._pending_requests:
            print("当前没有待处理 server request。")
            return
        print("待处理 server request:")
        for request_id, request in self._pending_requests.items():
            print(f"  {request_id}: {request.method}")


def build_approval_response_payload(
    *,
    request: CodexServerRequest,
    decision: str,
    scope: str,
) -> dict[str, object]:
    if request.method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return {"decision": decision}
    if request.method == "item/permissions/requestApproval":
        permissions = (
            dict(request.params.get("permissions") or {})
            if decision in {"accept", "acceptForSession", "approved"}
            else {}
        )
        return {
            "permissions": permissions,
            "scope": scope,
        }
    raise ValueError(f"当前 CLI 仅支持审批请求，不支持方法: {request.method}")


async def run_cli_async(
    env: Mapping[str, str] | None = None,
    *,
    base_dir: Path | None = None,
    server_url: str | None = None,
) -> int:
    config = build_cli_config(env, base_dir=base_dir, server_url=server_url)
    app = CodexCliApp(config)
    return await app.run()


def run_cli(
    env: Mapping[str, str] | None = None,
    *,
    base_dir: Path | None = None,
    server_url: str | None = None,
) -> int:
    return asyncio.run(run_cli_async(env, base_dir=base_dir, server_url=server_url))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return run_cli(server_url=args.server_url)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
