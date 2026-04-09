"""审批请求与用户输入桥接服务。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import time
from typing import Any, Mapping, Sequence

from feishu_codex_bot.adapters.codex_client import (
    DEFER_SERVER_REQUEST,
    CodexClient,
)
from feishu_codex_bot.adapters.codex_output_classifier import CodexOutputClassifier
from feishu_codex_bot.adapters.feishu_adapter import FeishuAdapter, FeishuReplyCardRef
from feishu_codex_bot.config import AppConfig
from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger
from feishu_codex_bot.models.actions import (
    CodexApprovalRequestEvent,
    CodexServerRequest,
    CodexUserInputRequestEvent,
)
from feishu_codex_bot.persistence.action_repo import (
    PendingActionRecord,
    PendingActionRepository,
)


_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
}
_USER_INPUT_METHODS = {
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
}


def _elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


@dataclass(frozen=True, slots=True)
class ApprovalRequestContext:
    """飞书侧发送审批/补充输入消息所需的上下文。"""

    session_scope_key: str | None
    source_message_id: str
    chat_id: str
    is_group_chat: bool = False
    reply_in_thread: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalDispatchResult:
    """审批/补充输入请求派发后的摘要。"""

    request_id: str
    action_type: str
    status: str
    feishu_message_id: str | None


class ApprovalService:
    """Bridge Codex server requests to Feishu messages and back."""

    def __init__(
        self,
        config: AppConfig,
        *,
        codex_client: CodexClient,
        feishu_adapter: FeishuAdapter,
        action_repository: PendingActionRepository,
        classifier: CodexOutputClassifier | None = None,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._config = config
        self._codex_client = codex_client
        self._feishu_adapter = feishu_adapter
        self._action_repository = action_repository
        self._classifier = classifier or CodexOutputClassifier()
        self._logger = logger or get_logger(__name__, bot_app_id=config.feishu.app_id)

    async def handle_server_request(
        self,
        request: CodexServerRequest,
        *,
        context: ApprovalRequestContext,
    ) -> object:
        if not context.source_message_id:
            raise ValueError("ApprovalRequestContext.source_message_id is required")

        action_type = self._action_type_for_request(request)
        prompt_text, status = self._build_prompt(request=request, context=context)
        prompt_card = self._build_pending_approval_card(request=request, prompt_text=prompt_text)
        prompt_message_ref = self._send_prompt_message(
            request=request,
            chat_id=context.chat_id,
            source_message_id=context.source_message_id,
            text=prompt_text,
            card_payload=prompt_card,
            reply_in_thread=context.reply_in_thread,
        )
        payload = {
            "requestId": request.id,
            "method": request.method,
            "params": request.params,
            "context": asdict(context),
            "promptText": prompt_text,
            "response": None,
        }
        if isinstance(prompt_message_ref, FeishuReplyCardRef):
            payload["feishuCardId"] = prompt_message_ref.card_id
            payload["feishuCardSequence"] = 0
        record = self._action_repository.upsert_action(
            request_id=request.id,
            action_type=action_type,
            thread_id=request.thread_id or request.params.get("threadId") or "",
            turn_id=request.turn_id or request.params.get("turnId") or "",
            item_id=request.item_id,
            session_scope_key=context.session_scope_key,
            feishu_message_id=(
                prompt_message_ref.message_id
                if isinstance(prompt_message_ref, FeishuReplyCardRef)
                else prompt_message_ref
            ),
            payload=payload,
            status=status,
        )
        self._logger.bind(
            event="approval.request.dispatched",
            request_id=record.request_id,
            action_type=record.action_type,
            thread_id=record.thread_id,
            turn_id=record.turn_id,
            item_id=record.item_id,
            session_scope_key=record.session_scope_key,
            feishu_message_id=record.feishu_message_id,
            status=record.status,
        ).info("Dispatched Codex server request to Feishu")
        return DEFER_SERVER_REQUEST

    async def submit_approval_response(
        self,
        request_id: str | int,
        decision: str,
        *,
        scope: str = "turn",
        granted_permissions: Mapping[str, Any] | None = None,
        update_prompt: bool = True,
    ) -> PendingActionRecord:
        total_start = time.perf_counter()
        record = self._require_record(request_id)
        payload_start = time.perf_counter()
        response_payload = self._build_approval_response_payload(
            record=record,
            decision=decision,
            scope=scope,
            granted_permissions=granted_permissions,
        )
        self._logger.bind(
            event="approval.response.payload_built",
            request_id=record.request_id,
            decision=decision,
            scope=scope,
            elapsed_ms=_elapsed_ms(payload_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Built approval response payload")
        codex_start = time.perf_counter()
        await self._codex_client.respond_to_server_request(
            record.original_request_id,
            response_payload,
        )
        self._logger.bind(
            event="approval.response.codex_submitted",
            request_id=record.request_id,
            elapsed_ms=_elapsed_ms(codex_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Submitted approval response to Codex")
        persist_start = time.perf_counter()
        updated = self._persist_response(
            record=record,
            response_payload=response_payload,
            status=self._status_from_approval_response(record, response_payload),
        )
        self._logger.bind(
            event="approval.response.persist_phase_completed",
            request_id=updated.request_id,
            status=updated.status,
            elapsed_ms=_elapsed_ms(persist_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Persisted approval response state")
        prompt_update_start = time.perf_counter()
        if update_prompt:
            self._update_prompt_after_response(updated)
        self._logger.bind(
            event="approval.response.completed",
            request_id=updated.request_id,
            status=updated.status,
            prompt_updated=update_prompt,
            prompt_update_elapsed_ms=_elapsed_ms(prompt_update_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Completed approval response handling")
        return updated

    def finalize_response_side_effects(self, record: PendingActionRecord) -> None:
        self._update_prompt_after_response(record)

    async def submit_user_input_response(
        self,
        request_id: str | int,
        *,
        answers: Mapping[str, str | Sequence[str]] | None = None,
        action: str = "accept",
        content: object | None = None,
    ) -> PendingActionRecord:
        total_start = time.perf_counter()
        record = self._require_record(request_id)
        payload_start = time.perf_counter()
        response_payload = self._build_user_input_response_payload(
            record=record,
            answers=answers,
            action=action,
            content=content,
        )
        self._logger.bind(
            event="user_input.response.payload_built",
            request_id=record.request_id,
            action=action,
            elapsed_ms=_elapsed_ms(payload_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Built user input response payload")
        codex_start = time.perf_counter()
        await self._codex_client.respond_to_server_request(
            record.original_request_id,
            response_payload,
        )
        self._logger.bind(
            event="user_input.response.codex_submitted",
            request_id=record.request_id,
            elapsed_ms=_elapsed_ms(codex_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Submitted user input response to Codex")
        persist_start = time.perf_counter()
        updated = self._persist_response(
            record=record,
            response_payload=response_payload,
            status=self._status_from_user_input_response(record, response_payload),
        )
        self._logger.bind(
            event="user_input.response.persist_phase_completed",
            request_id=updated.request_id,
            status=updated.status,
            elapsed_ms=_elapsed_ms(persist_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Persisted user input response state")
        prompt_update_start = time.perf_counter()
        self._update_prompt_after_response(updated)
        self._logger.bind(
            event="user_input.response.completed",
            request_id=updated.request_id,
            status=updated.status,
            prompt_update_elapsed_ms=_elapsed_ms(prompt_update_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Completed user input response handling")
        return updated

    def get_pending_action(self, request_id: str | int) -> PendingActionRecord | None:
        return self._action_repository.get_by_request_id(request_id)

    def list_pending_actions(self, *, status: str = "pending", limit: int = 50) -> list[PendingActionRecord]:
        return self._action_repository.list_by_status(status=status, limit=limit)

    def _action_type_for_request(self, request: CodexServerRequest) -> str:
        if request.method == "mcpServer/elicitation/request":
            return "user_input"
        events = self._classifier.classify(request)
        if any(isinstance(event, CodexApprovalRequestEvent) for event in events):
            return "approval"
        if any(isinstance(event, CodexUserInputRequestEvent) for event in events):
            return "user_input"
        raise ValueError(f"Unsupported server request method for approval bridge: {request.method}")

    def _build_prompt(
        self,
        *,
        request: CodexServerRequest,
        context: ApprovalRequestContext,
    ) -> tuple[str, str]:
        if request.method == "item/commandExecution/requestApproval":
            return self._build_command_approval_prompt(request), "pending"
        if request.method == "item/fileChange/requestApproval":
            return self._build_file_approval_prompt(request), "pending"
        if request.method == "item/permissions/requestApproval":
            return self._build_permissions_prompt(request), "pending"
        if request.method == "item/tool/requestUserInput":
            prompt_text = self._build_user_input_prompt(request)
            if context.is_group_chat and self._request_contains_secret(request):
                return self._build_secret_group_guidance(request, prompt_text), "awaiting_private_reply"
            return prompt_text, "pending"
        if request.method == "mcpServer/elicitation/request":
            return self._build_elicitation_prompt(request), "pending"
        raise ValueError(f"Unsupported server request method: {request.method}")

    def _build_command_approval_prompt(self, request: CodexServerRequest) -> str:
        params = request.params
        lines = [
            f"**命令**: {params.get('command') or '-'}",
            f"**cwd**: {params.get('cwd') or '-'}",
        ]
        return "\n".join(lines)

    def _build_file_approval_prompt(self, request: CodexServerRequest) -> str:
        params = request.params
        lines = [
            f"**授权目录**: {params.get('grantRoot') or '-'}",
            f"**原因**: {params.get('reason') or '-'}",
        ]
        return "\n".join(lines)

    def _build_permissions_prompt(self, request: CodexServerRequest) -> str:
        params = request.params
        permissions = params.get("permissions")
        lines = [
            *self._build_permissions_prompt_lines(permissions),
        ]
        reason = params.get("reason")
        if reason:
            lines.append(f"**原因**: {reason}")
        return "\n".join(lines)

    def _build_user_input_prompt(self, request: CodexServerRequest) -> str:
        questions = request.params.get("questions")
        lines = [
            "Codex 请求补充输入",
            f"request_id: {request.id}",
            f"thread_id: {request.thread_id or request.params.get('threadId') or '-'}",
            f"turn_id: {request.turn_id or request.params.get('turnId') or '-'}",
            "问题列表:",
        ]
        if isinstance(questions, list):
            for question in questions:
                if not isinstance(question, dict):
                    continue
                question_id = question.get("id") or "-"
                header = question.get("header") or "-"
                prompt = question.get("question") or "-"
                lines.append(f"- {header} ({question_id}): {prompt}")
                options = question.get("options")
                if isinstance(options, list) and options:
                    option_labels = [
                        option.get("label")
                        for option in options
                        if isinstance(option, dict) and isinstance(option.get("label"), str)
                    ]
                    if option_labels:
                        lines.append(f"  可选项: {' | '.join(option_labels)}")
        sample = {"question_id": ["your answer"]}
        lines.append("回传格式: submit_user_input_response(request_id, answers={...})")
        lines.append(f"示例 answers: {json.dumps(sample, ensure_ascii=False)}")
        return "\n".join(lines)

    def _build_elicitation_prompt(self, request: CodexServerRequest) -> str:
        params = request.params
        lines = [
            "Codex 请求 MCP 补充输入",
            f"request_id: {request.id}",
            f"server_name: {params.get('serverName') or '-'}",
            f"thread_id: {request.thread_id or params.get('threadId') or '-'}",
            f"turn_id: {request.turn_id or params.get('turnId') or '-'}",
            f"mode: {params.get('mode') or '-'}",
            f"message: {params.get('message') or '-'}",
        ]
        if params.get("mode") == "url":
            lines.append(f"url: {params.get('url') or '-'}")
        lines.append("回传格式: submit_user_input_response(request_id, action='accept|decline|cancel', content=...)")
        return "\n".join(lines)

    def _build_secret_group_guidance(self, request: CodexServerRequest, prompt_text: str) -> str:
        lines = [
            "该补充输入包含敏感信息，请在和机器人单聊时完成，不要直接在群里回复。",
            f"request_id: {request.id}",
            "",
            prompt_text,
        ]
        return "\n".join(lines)

    def _request_contains_secret(self, request: CodexServerRequest) -> bool:
        if request.method != "item/tool/requestUserInput":
            return False
        questions = request.params.get("questions")
        if not isinstance(questions, list):
            return False
        return any(
            isinstance(question, dict) and bool(question.get("isSecret"))
            for question in questions
        )

    def _send_prompt_message(
        self,
        *,
        request: CodexServerRequest,
        chat_id: str,
        source_message_id: str,
        text: str,
        card_payload: dict[str, object] | None,
        reply_in_thread: bool,
    ) -> str | FeishuReplyCardRef:
        if request.method in _APPROVAL_METHODS:
            return self._feishu_adapter.send_approval_message(
                receive_id=chat_id,
                card_payload=card_payload or self._build_fallback_info_card("审批请求", text),
            )
        return self._feishu_adapter.send_user_input_message(
            message_id=source_message_id,
            text=text,
            reply_in_thread=reply_in_thread,
        )

    def _build_approval_response_payload(
        self,
        *,
        record: PendingActionRecord,
        decision: str,
        scope: str,
        granted_permissions: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        method = record.payload.get("method")
        params = record.payload.get("params")
        if method == "item/commandExecution/requestApproval":
            return {"decision": decision}
        if method == "item/fileChange/requestApproval":
            return {"decision": decision}
        if method == "item/permissions/requestApproval":
            permissions = dict(granted_permissions) if granted_permissions is not None else None
            if permissions is None:
                if decision in {"accept", "acceptForSession", "approved"}:
                    permissions = dict(params.get("permissions") or {})
                else:
                    permissions = {}
            return {
                "permissions": permissions,
                "scope": scope,
            }
        raise ValueError(f"Request {record.request_id} is not an approval request")

    def _build_user_input_response_payload(
        self,
        *,
        record: PendingActionRecord,
        answers: Mapping[str, str | Sequence[str]] | None,
        action: str,
        content: object | None,
    ) -> dict[str, Any]:
        method = record.payload.get("method")
        if method == "item/tool/requestUserInput":
            if not answers:
                raise ValueError("answers is required for item/tool/requestUserInput")
            return {"answers": self._normalize_tool_answers(answers)}
        if method == "mcpServer/elicitation/request":
            response = {"action": action}
            if content is not None:
                response["content"] = content
            return response
        raise ValueError(f"Request {record.request_id} is not a user-input request")

    def _normalize_tool_answers(
        self,
        answers: Mapping[str, str | Sequence[str]],
    ) -> dict[str, dict[str, list[str]]]:
        normalized: dict[str, dict[str, list[str]]] = {}
        for question_id, raw_value in answers.items():
            if isinstance(raw_value, str):
                answer_values = [raw_value]
            else:
                answer_values = [value for value in raw_value if isinstance(value, str)]
            normalized[str(question_id)] = {"answers": answer_values}
        return normalized

    def _persist_response(
        self,
        *,
        record: PendingActionRecord,
        response_payload: dict[str, Any],
        status: str,
    ) -> PendingActionRecord:
        payload = record.payload
        payload["response"] = response_payload
        updated = self._action_repository.update_action(
            record.request_id,
            status=status,
            payload=payload,
        )
        if updated is None:
            raise RuntimeError(f"Pending action {record.request_id} disappeared during update")
        self._logger.bind(
            event="approval.response.persisted",
            request_id=updated.request_id,
            action_type=updated.action_type,
            status=updated.status,
        ).info("Persisted Codex server request response")
        return updated

    def _update_prompt_after_response(self, record: PendingActionRecord) -> None:
        if not record.feishu_message_id:
            return
        start_time = time.perf_counter()
        response = record.payload.get("response")
        method = record.payload.get("method")
        card_id = record.payload.get("feishuCardId")
        try:
            if method in _APPROVAL_METHODS:
                if not isinstance(card_id, str) or not card_id:
                    raise ValueError(f"Approval request {record.request_id} is missing feishuCardId")
                payload = record.payload
                next_sequence = self._next_approval_card_sequence(payload)
                payload["feishuCardSequence"] = next_sequence
                updated_record = self._action_repository.update_action(
                    record.request_id,
                    payload=payload,
                )
                if updated_record is None:
                    raise RuntimeError(
                        f"Pending action {record.request_id} disappeared before approval prompt update"
                    )
                self._feishu_adapter.update_approval_message(
                    card_id=card_id,
                    card_payload=self.build_resolved_approval_card(updated_record),
                    sequence=next_sequence,
                )
                self._logger.bind(
                    event="approval.prompt_updated",
                    request_id=updated_record.request_id,
                    status=updated_record.status,
                    feishu_message_id=updated_record.feishu_message_id,
                    feishu_card_id=card_id,
                    sequence=next_sequence,
                    elapsed_ms=_elapsed_ms(start_time),
                ).info("Updated Feishu approval prompt after response")
                return
            summary = "\n".join(
                (
                    "该请求已处理",
                    f"request_id: {record.request_id}",
                    f"status: {record.status}",
                    f"response: {json.dumps(response, ensure_ascii=False)}",
                )
            )
            self._feishu_adapter.update_user_input_message(
                message_id=record.feishu_message_id,
                text=summary,
            )
            self._logger.bind(
                event="user_input.prompt_updated",
                request_id=record.request_id,
                status=record.status,
                feishu_message_id=record.feishu_message_id,
                elapsed_ms=_elapsed_ms(start_time),
            ).info("Updated Feishu user-input prompt after response")
        except Exception:
            self._logger.bind(
                event="approval.prompt_update_failed",
                request_id=record.request_id,
                status=record.status,
                feishu_message_id=record.feishu_message_id,
                elapsed_ms=_elapsed_ms(start_time),
            ).exception("Failed to update Feishu prompt after approval response")

    def _next_approval_card_sequence(self, payload: Mapping[str, Any]) -> int:
        raw_sequence = payload.get("feishuCardSequence", 0)
        if isinstance(raw_sequence, bool):
            current = 0
        elif isinstance(raw_sequence, int):
            current = raw_sequence
        elif isinstance(raw_sequence, str) and raw_sequence.isdigit():
            current = int(raw_sequence)
        else:
            current = 0
        return current + 1

    def _status_from_approval_response(
        self,
        record: PendingActionRecord,
        response_payload: Mapping[str, Any],
    ) -> str:
        method = record.payload.get("method")
        if method == "item/permissions/requestApproval":
            permissions = response_payload.get("permissions")
            if isinstance(permissions, dict) and permissions:
                return "approved"
            return "rejected"
        decision = response_payload.get("decision")
        if isinstance(decision, str):
            if decision in {"accept", "acceptForSession", "approved", "approved_for_session"}:
                return "approved"
            return "rejected"
        return "approved"

    def _status_from_user_input_response(
        self,
        record: PendingActionRecord,
        response_payload: Mapping[str, Any],
    ) -> str:
        if record.payload.get("method") == "mcpServer/elicitation/request":
            action = response_payload.get("action")
            if action == "accept":
                return "completed"
            if action == "cancel":
                return "cancelled"
            return "declined"
        return "completed"

    def _require_record(self, request_id: str | int) -> PendingActionRecord:
        record = self._action_repository.get_by_request_id(request_id)
        if record is None:
            raise ValueError(f"Pending action {request_id!r} not found")
        return record

    def _summarize_permissions(self, permissions: object) -> str:
        if not isinstance(permissions, dict):
            return "-"
        chunks: list[str] = []
        file_system = permissions.get("fileSystem")
        if isinstance(file_system, dict):
            read_paths = file_system.get("read")
            write_paths = file_system.get("write")
            if isinstance(read_paths, list) and read_paths:
                chunks.append(f"read={','.join(str(path) for path in read_paths)}")
            if isinstance(write_paths, list) and write_paths:
                chunks.append(f"write={','.join(str(path) for path in write_paths)}")
        network = permissions.get("network")
        if isinstance(network, dict) and "enabled" in network:
            chunks.append(f"network_enabled={network.get('enabled')}")
        return "; ".join(chunks) if chunks else "-"

    def build_resolved_approval_card(self, record: PendingActionRecord) -> dict[str, object]:
        payload = record.payload
        method = str(payload.get("method") or "")
        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}
        title, template = self._resolved_approval_title(record)
        return {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
                "template": template,
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": self._approval_prompt_for_method(method=method, params=params),
                    },
                ],
            },
        }

    def build_card_action_not_found_card(self, request_id: str) -> dict[str, object]:
        return self._build_fallback_info_card(
            title="审批请求不存在",
            text=f"request_id: {request_id}\n\n该审批请求不存在，或已被清理。",
            template="grey",
        )

    def _build_pending_approval_card(
        self,
        *,
        request: CodexServerRequest,
        prompt_text: str,
    ) -> dict[str, object]:
        if request.method not in _APPROVAL_METHODS:
            return self._build_fallback_info_card("请求处理", prompt_text)
        return {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": self._approval_title_for_method(request.method),
                },
                "template": "orange",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": prompt_text,
                    },
                    *self._build_approval_buttons(request),
                ],
            },
        }

    def _build_approval_buttons(self, request: CodexServerRequest) -> list[dict[str, object]]:
        request_id = str(request.id)
        base_value = {
            "kind": "approval",
            "request_id": request_id,
        }
        if request.method == "item/permissions/requestApproval":
            return [
                self._build_approval_button(
                    text="同意",
                    button_type="primary",
                    value={**base_value, "decision": "accept", "scope": "session"},
                ),
                self._build_approval_button(
                    text="拒绝",
                    button_type="danger",
                    value={**base_value, "decision": "decline", "scope": "session"},
                ),
            ]
        return [
            self._build_approval_button(
                text="同意",
                button_type="primary",
                value={**base_value, "decision": "accept", "scope": "turn"},
            ),
            self._build_approval_button(
                text="本会话内同意",
                button_type="primary",
                value={**base_value, "decision": "acceptForSession", "scope": "session"},
            ),
            self._build_approval_button(
                text="拒绝",
                button_type="danger",
                value={**base_value, "decision": "decline", "scope": "turn"},
            ),
            self._build_approval_button(
                text="取消",
                button_type="default",
                value={**base_value, "decision": "cancel", "scope": "turn"},
            ),
        ]

    def _build_approval_button(
        self,
        *,
        text: str,
        button_type: str,
        value: dict[str, str],
        disabled: bool = False,
    ) -> dict[str, object]:
        button = {
            "tag": "button",
            "type": button_type,
            "text": {
                "tag": "plain_text",
                "content": text,
            },
        }
        if disabled:
            button["disabled"] = True
            return button
        button["behaviors"] = [
            {
                "type": "callback",
                "value": value,
            }
        ]
        return button

    def _approval_prompt_for_method(self, *, method: str, params: Mapping[str, Any]) -> str:
        if method == "item/commandExecution/requestApproval":
            return "\n".join(
                (
                    f"**命令**: {params.get('command') or '-'}",
                    f"**cwd**: {params.get('cwd') or '-'}",
                )
            )
        if method == "item/fileChange/requestApproval":
            return "\n".join(
                (
                    f"**授权目录**: {params.get('grantRoot') or '-'}",
                    f"**原因**: {params.get('reason') or '-'}",
                )
            )
        if method == "item/permissions/requestApproval":
            lines = self._build_permissions_prompt_lines(params.get("permissions"))
            reason = params.get("reason")
            if reason:
                lines.append(f"**原因**: {reason}")
            return "\n".join(lines)
        return "-"

    def _build_permissions_prompt_lines(self, permissions: object) -> list[str]:
        if not isinstance(permissions, dict):
            return ["**文件权限**: -", "**网络权限**: -"]
        file_system = permissions.get("fileSystem")
        network = permissions.get("network")
        file_segments: list[str] = []
        if isinstance(file_system, dict):
            reads = file_system.get("read")
            writes = file_system.get("write")
            if isinstance(reads, list) and reads:
                file_segments.append(f"read={', '.join(str(item) for item in reads)}")
            if isinstance(writes, list) and writes:
                file_segments.append(f"write={', '.join(str(item) for item in writes)}")
        network_summary = "-"
        if isinstance(network, dict):
            enabled = network.get("enabled")
            if enabled is True:
                network_summary = "enabled"
            elif enabled is False:
                network_summary = "disabled"
        return [
            f"**文件权限**: {'; '.join(file_segments) if file_segments else '-'}",
            f"**网络权限**: {network_summary}",
        ]

    def _build_resolved_approval_buttons(self, record: PendingActionRecord) -> list[dict[str, object]]:
        payload = record.payload
        method = str(payload.get("method") or "")
        decision = self._resolved_approval_decision(record)
        if method == "item/permissions/requestApproval":
            button_specs = [
                ("accept", "同意", "primary", {"decision": "accept", "scope": "session"}),
                ("decline", "拒绝", "danger", {"decision": "decline", "scope": "session"}),
            ]
        else:
            button_specs = [
                ("accept", "同意", "primary", {"decision": "accept", "scope": "turn"}),
                ("acceptForSession", "本会话内同意", "primary", {"decision": "acceptForSession", "scope": "session"}),
                ("decline", "拒绝", "danger", {"decision": "decline", "scope": "turn"}),
                ("cancel", "取消", "default", {"decision": "cancel", "scope": "turn"}),
            ]

        base_value = {
            "kind": "approval",
            "request_id": str(record.request_id),
        }
        buttons: list[dict[str, object]] = []
        for current_decision, text, button_type, value in button_specs:
            is_selected = current_decision == decision
            buttons.append(
                self._build_approval_button(
                    text=self._resolved_button_text(current_decision, text) if is_selected else text,
                    button_type=button_type if is_selected else "default",
                    value={**base_value, **value},
                    disabled=True,
                )
            )
        return buttons

    def _resolved_approval_decision(self, record: PendingActionRecord) -> str:
        response = record.payload.get("response")
        if isinstance(response, dict):
            decision = response.get("decision")
            if isinstance(decision, str) and decision:
                return decision
            if record.payload.get("method") == "item/permissions/requestApproval":
                permissions = response.get("permissions")
                if isinstance(permissions, dict) and permissions:
                    return "accept"
        if record.status == "approved":
            return "accept"
        if record.status == "rejected":
            return "decline"
        if record.status == "cancelled":
            return "cancel"
        return ""

    def _resolved_button_text(self, decision: str, fallback_text: str) -> str:
        mapping = {
            "accept": "已同意",
            "acceptForSession": "已本会话内同意",
            "decline": "已拒绝",
            "cancel": "已取消",
        }
        return mapping.get(decision, fallback_text)

    def _approval_title_for_method(self, method: str) -> str:
        mapping = {
            "item/commandExecution/requestApproval": "Codex 请求命令审批",
            "item/fileChange/requestApproval": "Codex 请求文件变更审批",
            "item/permissions/requestApproval": "Codex 请求权限审批",
        }
        return mapping.get(method, "Codex 请求审批")

    def _resolved_approval_title(self, record: PendingActionRecord) -> tuple[str, str]:
        decision = self._resolved_approval_decision(record)
        if decision == "accept":
            return "审批已通过", "green"
        if decision == "acceptForSession":
            return "审批已在本对话内同意", "green"
        if decision == "decline":
            return "审批已拒绝", "red"
        if decision == "cancel":
            return "审批已取消", "grey"
        if record.status == "approved":
            return "审批已通过", "green"
        if record.status == "rejected":
            return "审批已拒绝", "red"
        if record.status == "cancelled":
            return "审批已取消", "grey"
        return "审批已处理", "grey"

    def _build_fallback_info_card(
        self,
        title: str,
        text: str,
        *,
        template: str = "blue",
    ) -> dict[str, object]:
        return {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
                "template": template,
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": text,
                    }
                ],
            },
        }
