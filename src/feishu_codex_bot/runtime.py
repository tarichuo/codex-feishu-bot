"""应用运行时装配与事件分发。"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import json
import shlex
import threading

from feishu_codex_bot.adapters.codex_client import CodexConnectionClosedError
from feishu_codex_bot.bootstrap import RuntimeContext, bootstrap_runtime
from feishu_codex_bot.config import ConfigError
from feishu_codex_bot.logging import ContextLoggerAdapter
from feishu_codex_bot.models.actions import CodexNotification, CodexServerRequest
from feishu_codex_bot.models.inbound import (
    BotAddedEvent,
    CardActionCallback,
    CardActionCallbackResult,
    InboundMessage,
    TextContent,
)
from feishu_codex_bot.persistence.action_repo import PendingActionRecord
from feishu_codex_bot.services.approval_service import ApprovalRequestContext
from feishu_codex_bot.services.security_service import UnauthorizedMessage


_ACTIVE_TURN_BUSY_MESSAGE = "当前会话仍有进行中的回复，请等待本轮完成后再发送新消息。"
_CONTROL_HELP_MESSAGE = (
    "控制命令格式错误。支持：\n"
    "/approve <request_id> <accept|acceptForSession|decline|cancel> [scope=turn|session]\n"
    "/input <request_id> <question_id>=<value> ...\n"
    "/input <request_id> --action <accept|decline|cancel> [--content <json_or_text>]"
)


@dataclass(frozen=True, slots=True)
class _TurnRuntimeContext:
    session_scope_key: str
    source_message_id: str
    thread_id: str
    is_group_chat: bool
    reply_in_thread: bool


class ApplicationRuntime:
    """Orchestrate Feishu events, Codex events and service wiring."""

    def __init__(
        self,
        context: RuntimeContext,
        *,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._context = context
        self._logger = logger or context.logger.bind(component="runtime")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event = asyncio.Event()
        self._feishu_thread: threading.Thread | None = None
        self._turn_contexts: dict[str, _TurnRuntimeContext] = {}

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._register_codex_handlers()
        await self._context.codex_client.connect()
        await self._context.codex_client.initialize()
        self._start_feishu_long_connection()

        pending_count = len(self._context.action_repository.list_by_status(status="pending", limit=200))
        pending_count += len(
            self._context.action_repository.list_by_status(
                status="awaiting_private_reply",
                limit=200,
            )
        )
        self._logger.bind(
            event="runtime.started",
            bot_app_id=self._context.config.feishu.app_id,
            pending_action_count=pending_count,
        ).info("Application runtime started")

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            self._logger.bind(event="runtime.cancelled").info("Application runtime cancelled")
            raise
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        self._shutdown_event.set()
        await self._context.reply_service.close()
        await self._context.codex_client.close()
        self._logger.bind(event="runtime.stopped").info("Application runtime stopped")

    def _register_codex_handlers(self) -> None:
        self._context.codex_client.register_notification_handler("*", self._handle_codex_notification)
        self._context.codex_client.register_server_request_handler("*", self._handle_codex_server_request)

    def _start_feishu_long_connection(self) -> None:
        if self._feishu_thread is not None and self._feishu_thread.is_alive():
            return

        thread = threading.Thread(
            target=self._run_feishu_long_connection,
            name="feishu-long-connection",
            daemon=True,
        )
        self._feishu_thread = thread
        thread.start()
        self._logger.bind(event="runtime.feishu_thread.started").info(
            "Started Feishu long-connection thread"
        )

    def _run_feishu_long_connection(self) -> None:
        try:
            self._context.feishu_adapter.start_long_connection(
                on_message=self._handle_feishu_message_sync,
                on_bot_added=self._handle_feishu_bot_added_sync,
                on_card_action=self._handle_feishu_card_action_sync,
            )
        except Exception:
            self._logger.bind(event="runtime.feishu_thread.failed").exception(
                "Feishu long connection terminated unexpectedly"
            )
            self._schedule_coroutine(self._request_shutdown(), description="runtime_shutdown")

    def _handle_feishu_message_sync(self, message: InboundMessage) -> None:
        self._schedule_coroutine(
            self._handle_feishu_message(message),
            description=f"feishu_message:{message.message_id}",
        )

    def _handle_feishu_bot_added_sync(self, event: BotAddedEvent) -> None:
        self._schedule_coroutine(
            self._handle_feishu_bot_added(event),
            description=f"feishu_bot_added:{event.chat_id}",
        )

    def _handle_feishu_card_action_sync(
        self,
        action: CardActionCallback,
    ) -> CardActionCallbackResult:
        if self._loop is None:
            return CardActionCallbackResult(
                toast_type="error",
                toast_text="应用尚未初始化完成，请稍后重试。",
            )
        future = asyncio.run_coroutine_threadsafe(
            self._handle_feishu_card_action(action),
            self._loop,
        )
        try:
            return future.result(timeout=2.8)
        except FutureTimeoutError:
            self._logger.bind(
                event="runtime.card_action.timeout",
                request_id=action.action_value.get("request_id"),
                operator_open_id=action.operator_open_id,
            ).warning("Card action handling timed out")
            return CardActionCallbackResult(
                toast_type="warning",
                toast_text="处理超时，请稍后查看卡片状态。",
            )
        except Exception:
            self._logger.bind(
                event="runtime.card_action.failed",
                request_id=action.action_value.get("request_id"),
                operator_open_id=action.operator_open_id,
            ).exception("Failed to handle Feishu card action")
            return CardActionCallbackResult(
                toast_type="error",
                toast_text="处理审批按钮失败，请稍后重试。",
            )

    def _schedule_coroutine(self, coroutine: asyncio.Future | asyncio.Task | object, *, description: str) -> None:
        if self._loop is None:
            raise RuntimeError("Runtime loop has not been initialized")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        future.add_done_callback(lambda completed: self._consume_future(completed, description))

    def _consume_future(self, future: Future[object], description: str) -> None:
        try:
            future.result()
        except Exception:
            self._logger.bind(
                event="runtime.scheduled_task.failed",
                description=description,
            ).exception("Scheduled runtime task failed")

    async def _request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def _handle_feishu_message(self, message: InboundMessage) -> None:
        if await self._handle_control_message(message):
            return

        session_scope_key = self._compute_session_scope_key(message)
        if session_scope_key is not None and self._should_gate_on_active_turn(message):
            active_turn = await self._context.session_executor.get_active_turn(session_scope_key)
            if active_turn is not None:
                self._context.feishu_adapter.reply_text(
                    message_id=message.message_id,
                    text=_ACTIVE_TURN_BUSY_MESSAGE,
                )
                self._logger.bind(
                    event="runtime.message.rejected_active_turn",
                    feishu_message_id=message.message_id,
                    session_scope_key=session_scope_key,
                    active_turn=active_turn,
                ).info("Rejected message because the session already has an active turn")
                return

        dispatch_result = await self._context.conversation_service.handle_message(message)
        if dispatch_result.status != "submitted":
            return
        if not dispatch_result.turn_id or not dispatch_result.thread_id or not dispatch_result.session_scope_key:
            return

        self._turn_contexts[dispatch_result.turn_id] = _TurnRuntimeContext(
            session_scope_key=dispatch_result.session_scope_key,
            source_message_id=message.message_id,
            thread_id=dispatch_result.thread_id,
            is_group_chat=message.is_group_message,
            reply_in_thread=False,
        )
        try:
            await self._context.reply_service.start_turn(
                session_scope_key=dispatch_result.session_scope_key,
                source_message_id=message.message_id,
                thread_id=dispatch_result.thread_id,
                turn_id=dispatch_result.turn_id,
                reply_in_thread=False,
            )
        except Exception:
            self._turn_contexts.pop(dispatch_result.turn_id, None)
            self._logger.bind(
                event="runtime.reply.start_failed",
                feishu_message_id=message.message_id,
                turn_id=dispatch_result.turn_id,
                session_scope_key=dispatch_result.session_scope_key,
            ).exception("Failed to start reply stream for submitted turn")
            raise

    async def _handle_feishu_bot_added(self, event: BotAddedEvent) -> None:
        await self._context.conversation_service.handle_bot_added(event)

    async def _handle_feishu_card_action(
        self,
        action: CardActionCallback,
    ) -> CardActionCallbackResult:
        action_value = action.action_value
        self._logger.bind(
            event="runtime.card_action.received",
            request_id=action_value.get("request_id"),
            open_message_id=action.open_message_id,
            open_chat_id=action.open_chat_id,
            operator_open_id=action.operator_open_id,
            action_tag=action.action_tag,
            action_name=action.action_name,
            decision=action_value.get("decision"),
            scope=action_value.get("scope"),
        ).info("Received Feishu card action callback")

        sender_id = (
            action.operator_open_id
            or action.operator_user_id
            or action.operator_union_id
        )
        if sender_id is None:
            return CardActionCallbackResult(
                toast_type="error",
                toast_text="无法识别操作人，审批未执行。",
            )

        if sender_id not in self._context.config.security.allowed_user_ids:
            await self._handle_unauthorized_card_action(action=action, sender_id=sender_id)
            return CardActionCallbackResult(
                toast_type="warning",
                toast_text="你不在允许操作该机器人的白名单中。",
            )

        if action_value.get("kind") != "approval":
            return CardActionCallbackResult(
                toast_type="warning",
                toast_text="暂不支持此卡片交互。",
            )

        request_id = action_value.get("request_id")
        if not request_id:
            return CardActionCallbackResult(
                toast_type="error",
                toast_text="审批请求缺少 request_id。",
            )

        record = self._context.approval_service.get_pending_action(request_id)
        if record is None:
            return CardActionCallbackResult(
                toast_type="warning",
                toast_text=f"审批请求 {request_id} 不存在。",
            )

        if record.status != "pending":
            return CardActionCallbackResult(
                toast_type="info",
                toast_text=f"审批请求 {request_id} 已处理。",
            )

        decision = action_value.get("decision") or "decline"
        scope = action_value.get("scope") or "turn"
        updated = await self._context.approval_service.submit_approval_response(
            request_id,
            decision,
            scope=scope,
        )
        return CardActionCallbackResult(
            toast_type="success",
            toast_text=f"审批请求 {updated.request_id} 已处理，状态: {updated.status}",
        )

    async def _handle_codex_notification(self, notification: CodexNotification) -> None:
        try:
            handled = await self._context.reply_service.handle_notification(notification)
        except CodexConnectionClosedError:
            self._logger.bind(
                event="runtime.codex.notification.connection_closed",
                method=notification.method,
                turn_id=notification.turn_id,
            ).warning("Codex notification handling stopped because connection was closed")
            return
        except Exception:
            self._logger.bind(
                event="runtime.codex.notification.failed",
                method=notification.method,
                turn_id=notification.turn_id,
                thread_id=notification.thread_id,
            ).exception("Failed to handle Codex notification")
            if notification.turn_id:
                await self._context.reply_service.fail_turn(notification.turn_id)
                self._turn_contexts.pop(notification.turn_id, None)
            return

        if handled and notification.method == "turn/completed" and notification.turn_id:
            self._turn_contexts.pop(notification.turn_id, None)

    async def _handle_codex_server_request(self, request: CodexServerRequest) -> object:
        if request.turn_id and request.turn_id in self._turn_contexts:
            turn_context = self._turn_contexts[request.turn_id]
            return await self._context.approval_service.handle_server_request(
                request,
                context=ApprovalRequestContext(
                    session_scope_key=turn_context.session_scope_key,
                    source_message_id=turn_context.source_message_id,
                    is_group_chat=turn_context.is_group_chat,
                    reply_in_thread=turn_context.reply_in_thread,
                ),
            )

        self._logger.bind(
            event="runtime.codex.server_request.missing_context",
            request_id=request.id,
            method=request.method,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
        ).warning("Server request arrived without matching turn context")
        raise ValueError("Missing turn context for server request")

    async def _handle_control_message(self, message: InboundMessage) -> bool:
        normalized_text = self._extract_control_text(message)
        if not normalized_text.startswith("/"):
            return False
        if not self._is_sender_allowed(message):
            return False
        if message.is_group_message and not self._is_group_control_addressed(message):
            return False

        try:
            tokens = shlex.split(normalized_text)
        except ValueError:
            self._context.feishu_adapter.reply_text(
                message_id=message.message_id,
                text=_CONTROL_HELP_MESSAGE,
            )
            return True

        if not tokens:
            return False
        command = tokens[0].lower()
        if command == "/approve":
            await self._handle_approve_command(message, tokens)
            return True
        if command == "/input":
            await self._handle_input_command(message, tokens)
            return True
        return False

    async def _handle_approve_command(self, message: InboundMessage, tokens: list[str]) -> None:
        if len(tokens) < 3:
            self._context.feishu_adapter.reply_text(
                message_id=message.message_id,
                text=_CONTROL_HELP_MESSAGE,
            )
            return

        request_id = tokens[1]
        decision = tokens[2]
        scope = "turn"
        for token in tokens[3:]:
            if token.startswith("scope="):
                scope = token.split("=", 1)[1] or "turn"

        try:
            updated = await self._context.approval_service.submit_approval_response(
                request_id,
                decision,
                scope=scope,
            )
        except Exception as exc:
            self._context.feishu_adapter.reply_text(
                message_id=message.message_id,
                text=f"处理审批响应失败: {exc}",
            )
            return

        self._context.feishu_adapter.reply_text(
            message_id=message.message_id,
            text=f"审批请求 {updated.request_id} 已处理，状态: {updated.status}",
        )

    async def _handle_input_command(self, message: InboundMessage, tokens: list[str]) -> None:
        if len(tokens) < 2:
            self._context.feishu_adapter.reply_text(
                message_id=message.message_id,
                text=_CONTROL_HELP_MESSAGE,
            )
            return

        request_id = tokens[1]
        record = self._context.approval_service.get_pending_action(request_id)
        if record is None:
            self._context.feishu_adapter.reply_text(
                message_id=message.message_id,
                text=f"未找到待处理请求: {request_id}",
            )
            return

        try:
            if record.payload.get("method") == "mcpServer/elicitation/request":
                updated = await self._handle_elicitation_input(record, tokens[2:])
            else:
                updated = await self._handle_tool_user_input(record, tokens[2:])
        except Exception as exc:
            self._context.feishu_adapter.reply_text(
                message_id=message.message_id,
                text=f"处理补充输入失败: {exc}",
            )
            return

        self._context.feishu_adapter.reply_text(
            message_id=message.message_id,
            text=f"补充输入请求 {updated.request_id} 已处理，状态: {updated.status}",
        )

    async def _handle_tool_user_input(
        self,
        record: PendingActionRecord,
        arguments: list[str],
    ) -> PendingActionRecord:
        payload = record.payload
        params = payload.get("params") or {}
        questions = params.get("questions") if isinstance(params, dict) else None
        answers: dict[str, list[str]] = {}

        for argument in arguments:
            if "=" not in argument:
                continue
            key, value = argument.split("=", 1)
            answers.setdefault(key, []).append(value)

        if not answers and isinstance(questions, list) and len(questions) == 1 and arguments:
            question = questions[0]
            if isinstance(question, dict) and isinstance(question.get("id"), str):
                answers[question["id"]] = [" ".join(arguments)]

        if not answers:
            raise ValueError("缺少 answers，格式应为 /input <request_id> key=value ...")

        return await self._context.approval_service.submit_user_input_response(
            record.request_id,
            answers=answers,
        )

    async def _handle_elicitation_input(
        self,
        record: PendingActionRecord,
        arguments: list[str],
    ) -> PendingActionRecord:
        action = "accept"
        content: object | None = None
        index = 0
        while index < len(arguments):
            token = arguments[index]
            if token == "--action" and index + 1 < len(arguments):
                action = arguments[index + 1]
                index += 2
                continue
            if token == "--content" and index + 1 < len(arguments):
                content = self._parse_content_argument(arguments[index + 1])
                index += 2
                continue
            index += 1

        return await self._context.approval_service.submit_user_input_response(
            record.request_id,
            action=action,
            content=content,
        )

    def _parse_content_argument(self, raw_value: str) -> object:
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value

    def _extract_control_text(self, message: InboundMessage) -> str:
        parts = message.parts
        if message.is_group_message and message.mentions:
            parts = self._strip_group_mentions(message)
        text = "".join(
            part.text for part in parts if isinstance(part, TextContent)
        )
        return text.strip()

    def _strip_group_mentions(self, message: InboundMessage) -> tuple[object, ...]:
        normalized: list[object] = []
        strip_leading = True
        for part in message.parts:
            if strip_leading and isinstance(part, TextContent):
                text = part.text.lstrip()
                while text.startswith("@"):
                    fragment = text.split(maxsplit=1)
                    if len(fragment) == 1:
                        text = ""
                        break
                    text = fragment[1].lstrip()
                if text:
                    normalized.append(TextContent(text))
                    strip_leading = False
                continue
            normalized.append(part)
            strip_leading = False
        return tuple(normalized)

    def _compute_session_scope_key(self, message: InboundMessage) -> str | None:
        sender_id = (
            message.sender_open_id
            or message.sender_user_id
            or message.sender_union_id
        )
        if message.is_direct_message:
            if sender_id is None:
                return None
            return f"{self._context.config.feishu.app_id}:{sender_id}"
        return f"{self._context.config.feishu.app_id}:{message.chat_id}"

    def _is_sender_allowed(self, message: InboundMessage) -> bool:
        sender_id = (
            message.sender_open_id
            or message.sender_user_id
            or message.sender_union_id
        )
        return sender_id in self._context.config.security.allowed_user_ids

    def _is_group_control_addressed(self, message: InboundMessage) -> bool:
        return bool(message.mentions)

    def _should_gate_on_active_turn(self, message: InboundMessage) -> bool:
        if not self._is_sender_allowed(message):
            return False
        if message.is_direct_message:
            return True
        return bool(message.mentions)

    async def _handle_unauthorized_card_action(
        self,
        *,
        action: CardActionCallback,
        sender_id: str,
    ) -> None:
        alert = self._context.security_service.record_unauthorized_attempt(
            UnauthorizedMessage(
                bot_app_id=self._context.config.feishu.app_id,
                sender_user_id=sender_id,
                sender_open_id=action.operator_open_id,
                chat_id=action.open_chat_id,
                chat_type="card_callback",
                feishu_message_id=action.open_message_id or "",
                feishu_event_id=action.event_id,
            )
        )
        alert_text = "\n".join(
            (
                "检测到非白名单用户尝试点击审批卡片",
                f"时间: {action.occurred_at.isoformat()}",
                f"发送者: {sender_id}",
                f"chat_id: {action.open_chat_id or '-'}",
                f"message_id: {action.open_message_id or '-'}",
                f"request_id: {action.action_value.get('request_id') or '-'}",
            )
        )
        try:
            owner_alert_message_id = self._context.feishu_adapter.send_owner_alert(
                owner_open_id=self._context.config.security.owner_user_id,
                text=alert_text,
            )
        except Exception:
            self._context.security_service.mark_alert_failed(alert.id)
            self._logger.bind(
                event="runtime.card_action.security.alert_failed",
                sender_id=sender_id,
                request_id=action.action_value.get("request_id"),
            ).exception("Failed to deliver owner alert for unauthorized card action")
            return

        self._context.security_service.mark_alert_sent(
            alert.id,
            owner_alert_message_id=owner_alert_message_id,
        )
        self._logger.bind(
            event="runtime.card_action.security.blocked",
            sender_id=sender_id,
            request_id=action.action_value.get("request_id"),
            owner_alert_message_id=owner_alert_message_id,
        ).warning("Blocked unauthorized card action and sent owner alert")


async def run_application() -> None:
    """Bootstrap and run the full application runtime."""
    runtime = ApplicationRuntime(bootstrap_runtime())
    await runtime.run()


def run_application_sync() -> int:
    """Run the application from a synchronous entrypoint."""
    try:
        asyncio.run(run_application())
    except ConfigError:
        raise
    except KeyboardInterrupt:
        return 130
    return 0
