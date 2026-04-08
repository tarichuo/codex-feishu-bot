"""Codex app server 的 JSON-RPC WebSocket 客户端。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping
import inspect
import json
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from feishu_codex_bot.config import AppConfig
from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger
from feishu_codex_bot.models.actions import (
    CodexNotification,
    CodexServerRequest,
    CodexThreadRef,
    CodexTurnRef,
    InitializeOptions,
    JsonObject,
    JsonRpcErrorPayload,
    RequestId,
    ThreadResumeOptions,
    ThreadStartOptions,
    TurnStartOptions,
)


NotificationHandler = Callable[[CodexNotification], Awaitable[None] | None]
ServerRequestHandler = Callable[[CodexServerRequest], Awaitable[object | None] | object | None]


class CodexClientError(RuntimeError):
    """Base error for Codex app server integration failures."""


class CodexConnectionClosedError(CodexClientError):
    """Raised when the websocket is not connected or closes unexpectedly."""


class CodexJsonRpcError(CodexClientError):
    """Raised when the server returns a JSON-RPC error response."""

    def __init__(self, request_id: RequestId, error: JsonRpcErrorPayload) -> None:
        super().__init__(f"JSON-RPC request {request_id!r} failed: {error.code} {error.message}")
        self.request_id = request_id
        self.error = error


class CodexDeferredServerRequest:
    """Marker returned by a server request handler to defer the response."""


DEFER_SERVER_REQUEST = CodexDeferredServerRequest()


class CodexClient:
    """Persistent async JSON-RPC client for the local Codex app server."""

    def __init__(
        self,
        config: AppConfig,
        *,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._server_url = config.codex.server_url
        self._logger = logger or get_logger(__name__, codex_server_url=self._server_url)
        self._websocket: Any | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._pending_requests: dict[RequestId, asyncio.Future[Any]] = {}
        self._notification_handlers: dict[str, list[NotificationHandler]] = defaultdict(list)
        self._thread_listeners: dict[str, list[NotificationHandler]] = defaultdict(list)
        self._turn_listeners: dict[str, list[NotificationHandler]] = defaultdict(list)
        self._server_request_handlers: dict[str, ServerRequestHandler] = {}
        self._request_counter = 0

    @property
    def is_connected(self) -> bool:
        return self._websocket is not None and not getattr(self._websocket, "closed", False)

    async def connect(self) -> None:
        async with self._connect_lock:
            if self.is_connected:
                return
            self._websocket = await websockets.connect(self._server_url, max_size=None)
            self._receiver_task = asyncio.create_task(self._receive_loop())
            self._logger.bind(event="codex.connected").info("Connected to Codex app server")

    async def close(self) -> None:
        async with self._connect_lock:
            receiver_task = self._receiver_task
            websocket = self._websocket
            self._receiver_task = None
            self._websocket = None

            if websocket is not None:
                await websocket.close()
            if receiver_task is not None:
                receiver_task.cancel()
                try:
                    await receiver_task
                except asyncio.CancelledError:
                    pass

            self._fail_pending_requests(CodexConnectionClosedError("Codex websocket closed"))
            self._logger.bind(event="codex.closed").info("Closed Codex app server connection")

    async def initialize(self, options: InitializeOptions | None = None) -> JsonObject:
        initialize_options = options or InitializeOptions()
        result = await self.request("initialize", initialize_options.to_params())
        return self._require_object(result, method="initialize")

    async def start_thread(
        self,
        options: ThreadStartOptions | Mapping[str, Any] | None = None,
    ) -> CodexThreadRef:
        params = options.to_params() if isinstance(options, ThreadStartOptions) else dict(options or {})
        result = await self.request("thread/start", params)
        thread_payload = self._require_object(result, method="thread/start").get("thread")
        thread = CodexThreadRef.from_payload(thread_payload)
        self._logger.bind(
            event="codex.thread.started",
            thread_id=thread.id,
            thread_status=thread.status,
        ).info("Codex thread started")
        return thread

    async def resume_thread(
        self,
        options: ThreadResumeOptions | Mapping[str, Any],
    ) -> CodexThreadRef:
        params = options.to_params() if isinstance(options, ThreadResumeOptions) else dict(options)
        result = await self.request("thread/resume", params)
        thread_payload = self._require_object(result, method="thread/resume").get("thread")
        thread = CodexThreadRef.from_payload(thread_payload)
        self._logger.bind(
            event="codex.thread.resumed",
            thread_id=thread.id,
            thread_status=thread.status,
        ).info("Codex thread resumed")
        return thread

    async def start_turn(
        self,
        options: TurnStartOptions | Mapping[str, Any],
    ) -> CodexTurnRef:
        params = options.to_params() if isinstance(options, TurnStartOptions) else dict(options)
        result = await self.request("turn/start", params)
        turn_payload = self._require_object(result, method="turn/start").get("turn")
        turn = CodexTurnRef.from_payload(turn_payload)
        self._logger.bind(
            event="codex.turn.started_response",
            turn_id=turn.id,
            turn_status=turn.status,
        ).info("Codex turn accepted")
        return turn

    async def request(self, method: str, params: object | None = None) -> object:
        await self.connect()
        request_id = self._next_request_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[request_id] = future
        try:
            await self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            self._logger.bind(
                event="codex.request.sent",
                request_id=request_id,
                method=method,
            ).info("Codex request sent")
            return await future
        finally:
            self._pending_requests.pop(request_id, None)

    async def notify(self, method: str, params: object | None = None) -> None:
        await self.connect()
        await self._send_json(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
        )
        self._logger.bind(event="codex.notification.sent", method=method).info(
            "Codex notification sent"
        )

    def register_notification_handler(self, method: str, handler: NotificationHandler) -> None:
        self._notification_handlers[method].append(handler)

    def register_thread_listener(self, thread_id: str, handler: NotificationHandler) -> None:
        self._thread_listeners[thread_id].append(handler)

    def register_turn_listener(self, turn_id: str, handler: NotificationHandler) -> None:
        self._turn_listeners[turn_id].append(handler)

    def register_server_request_handler(self, method: str, handler: ServerRequestHandler) -> None:
        self._server_request_handlers[method] = handler

    async def respond_to_server_request(
        self,
        request_id: RequestId,
        result: object | None = None,
    ) -> None:
        await self.connect()
        await self._send_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        )
        self._logger.bind(
            event="codex.server_request.responded",
            request_id=request_id,
        ).info("Responded to deferred Codex server request")

    async def respond_to_server_request_error(
        self,
        request_id: RequestId,
        *,
        code: int,
        message: str,
        data: object | None = None,
    ) -> None:
        await self.connect()
        await self._send_error_response(
            request_id,
            code=code,
            message=message,
            data=data,
        )
        self._logger.bind(
            event="codex.server_request.responded_error",
            request_id=request_id,
            error_code=code,
        ).info("Responded to deferred Codex server request with error")

    async def _receive_loop(self) -> None:
        websocket = self._websocket
        if websocket is None:
            return
        try:
            async for raw_message in websocket:
                await self._handle_message(raw_message)
        except ConnectionClosed as exc:
            self._logger.bind(
                event="codex.connection.closed",
                code=getattr(exc, "code", None),
                reason=getattr(exc, "reason", None),
            ).warning("Codex websocket connection closed")
            self._fail_pending_requests(CodexConnectionClosedError("Codex websocket closed unexpectedly"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.bind(event="codex.receive.failed", error=str(exc)).exception(
                "Codex receive loop failed"
            )
            self._fail_pending_requests(CodexConnectionClosedError(str(exc)))
        finally:
            self._websocket = None

    async def _handle_message(self, raw_message: object) -> None:
        text = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else str(raw_message)
        payload = json.loads(text)

        if "method" in payload and "id" in payload:
            await self._handle_server_request(payload)
            return
        if "method" in payload:
            await self._handle_notification(payload)
            return
        if "id" in payload and ("result" in payload or "error" in payload):
            self._handle_response(payload)
            return

        self._logger.bind(event="codex.message.ignored", payload=payload).warning(
            "Ignoring unsupported Codex message shape"
        )

    def _handle_response(self, payload: JsonObject) -> None:
        request_id = payload.get("id")
        if not isinstance(request_id, (str, int)):
            raise CodexClientError("Codex response missing request id")

        future = self._pending_requests.get(request_id)
        if future is None:
            self._logger.bind(
                event="codex.response.orphaned",
                request_id=request_id,
            ).warning("Received orphaned Codex response")
            return

        if "error" in payload:
            error = JsonRpcErrorPayload.from_payload(payload["error"])
            future.set_exception(CodexJsonRpcError(request_id, error))
            return

        future.set_result(payload.get("result"))

    async def _handle_notification(self, payload: JsonObject) -> None:
        method = payload.get("method")
        if not isinstance(method, str):
            raise CodexClientError("Codex notification missing method")

        notification = CodexNotification.from_payload(method, payload.get("params"))
        self._logger.bind(
            event="codex.notification.received",
            method=notification.method,
            thread_id=notification.thread_id,
            turn_id=notification.turn_id,
            item_id=notification.item_id,
        ).info("Codex notification received")

        handlers = list(self._notification_handlers.get("*", []))
        handlers.extend(self._notification_handlers.get(notification.method, []))
        if notification.thread_id:
            handlers.extend(self._thread_listeners.get(notification.thread_id, []))
        if notification.turn_id:
            handlers.extend(self._turn_listeners.get(notification.turn_id, []))

        for handler in handlers:
            await self._invoke_handler(handler, notification)

    async def _handle_server_request(self, payload: JsonObject) -> None:
        method = payload.get("method")
        request_id = payload.get("id")
        if not isinstance(method, str) or not isinstance(request_id, (str, int)):
            raise CodexClientError("Codex server request missing method or id")

        request = CodexServerRequest.from_payload(request_id, method, payload.get("params"))
        self._logger.bind(
            event="codex.server_request.received",
            request_id=request.id,
            method=request.method,
            thread_id=request.thread_id,
            turn_id=request.turn_id,
            item_id=request.item_id,
        ).info("Codex server request received")

        handler = self._server_request_handlers.get(request.method) or self._server_request_handlers.get("*")
        if handler is None:
            await self._send_error_response(
                request.id,
                code=-32601,
                message=f"Unhandled server request method: {request.method}",
            )
            return

        try:
            result = handler(request)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, CodexDeferredServerRequest):
                self._logger.bind(
                    event="codex.server_request.deferred",
                    request_id=request.id,
                    method=request.method,
                ).info("Codex server request deferred for later response")
                return
            await self._send_json({"jsonrpc": "2.0", "id": request.id, "result": result})
            self._logger.bind(
                event="codex.server_request.resolved",
                request_id=request.id,
                method=request.method,
            ).info("Codex server request resolved")
        except CodexJsonRpcError as exc:
            await self._send_error_response(
                request.id,
                code=exc.error.code,
                message=exc.error.message,
                data=exc.error.data,
            )
        except Exception as exc:
            await self._send_error_response(
                request.id,
                code=-32000,
                message=str(exc),
            )
            self._logger.bind(
                event="codex.server_request.failed",
                request_id=request.id,
                method=request.method,
                error=str(exc),
            ).exception("Codex server request handler failed")

    async def _send_error_response(
        self,
        request_id: RequestId,
        *,
        code: int,
        message: str,
        data: object | None = None,
    ) -> None:
        error_payload: JsonObject = {
            "code": code,
            "message": message,
        }
        if data is not None:
            error_payload["data"] = data
        await self._send_json({"jsonrpc": "2.0", "id": request_id, "error": error_payload})

    async def _send_json(self, payload: JsonObject) -> None:
        if self._websocket is None:
            raise CodexConnectionClosedError("Codex websocket is not connected")
        async with self._send_lock:
            await self._websocket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def _invoke_handler(self, handler: NotificationHandler, notification: CodexNotification) -> None:
        result = handler(notification)
        if inspect.isawaitable(result):
            await result

    def _require_object(self, payload: object, *, method: str) -> JsonObject:
        if isinstance(payload, dict):
            return dict(payload)
        raise CodexClientError(f"Codex method {method} returned a non-object payload")

    def _next_request_id(self) -> int:
        self._request_counter += 1
        return self._request_counter

    def _fail_pending_requests(self, error: Exception) -> None:
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(error)
        self._pending_requests.clear()
