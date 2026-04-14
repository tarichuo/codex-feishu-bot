"""Codex app server 请求、输入和事件模型。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from feishu_codex_bot import __version__


JsonObject: TypeAlias = dict[str, Any]
RequestId: TypeAlias = str | int


def _drop_none(payload: JsonObject) -> JsonObject:
    return {key: value for key, value in payload.items() if value is not None}


def _normalize_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def _find_key(payload: object, key: str) -> object | None:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            nested = _find_key(value, key)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _find_key(item, key)
            if nested is not None:
                return nested
    return None


def _find_nested_object_id(payload: object, object_key: str) -> str | None:
    if isinstance(payload, dict):
        nested_object = payload.get(object_key)
        if isinstance(nested_object, dict):
            nested_id = nested_object.get("id")
            if isinstance(nested_id, str):
                return nested_id
        for value in payload.values():
            nested = _find_nested_object_id(value, object_key)
            if nested is not None:
                return nested
    elif isinstance(payload, list):
        for item in payload:
            nested = _find_nested_object_id(item, object_key)
            if nested is not None:
                return nested
    return None


def extract_routing_ids(payload: object) -> tuple[str | None, str | None, str | None, RequestId | None]:
    thread_id = _find_key(payload, "threadId")
    turn_id = _find_key(payload, "turnId")
    item_id = _find_key(payload, "itemId")
    request_id = _find_key(payload, "requestId")

    normalized_thread_id = thread_id if isinstance(thread_id, str) else _find_nested_object_id(payload, "thread")
    normalized_turn_id = turn_id if isinstance(turn_id, str) else _find_nested_object_id(payload, "turn")
    normalized_item_id = item_id if isinstance(item_id, str) else _find_nested_object_id(payload, "item")
    normalized_request_id = request_id if isinstance(request_id, (str, int)) else None
    return normalized_thread_id, normalized_turn_id, normalized_item_id, normalized_request_id


@dataclass(frozen=True, slots=True)
class InitializeOptions:
    client_name: str = "feishu-codex-bot"
    client_version: str = __version__
    client_title: str | None = None
    experimental_api: bool = True
    opt_out_notification_methods: tuple[str, ...] = ()

    def to_params(self) -> JsonObject:
        capabilities: JsonObject = {
            "experimentalApi": self.experimental_api,
        }
        if self.opt_out_notification_methods:
            capabilities["optOutNotificationMethods"] = list(self.opt_out_notification_methods)

        return {
            "clientInfo": _drop_none(
                {
                    "name": self.client_name,
                    "title": self.client_title,
                    "version": self.client_version,
                }
            ),
            "capabilities": capabilities,
        }


@dataclass(frozen=True, slots=True)
class CodexTextInput:
    text: str
    text_elements: tuple[JsonObject, ...] = ()

    def to_payload(self) -> JsonObject:
        payload: JsonObject = {
            "type": "text",
            "text": self.text,
        }
        if self.text_elements:
            payload["text_elements"] = [dict(element) for element in self.text_elements]
        return payload


@dataclass(frozen=True, slots=True)
class CodexImageInput:
    url: str

    def to_payload(self) -> JsonObject:
        return {"type": "image", "url": self.url}


@dataclass(frozen=True, slots=True)
class CodexLocalImageInput:
    path: str | Path

    def to_payload(self) -> JsonObject:
        return {"type": "localImage", "path": _normalize_path(self.path)}


@dataclass(frozen=True, slots=True)
class CodexMentionInput:
    name: str
    path: str

    def to_payload(self) -> JsonObject:
        return {"type": "mention", "name": self.name, "path": self.path}


@dataclass(frozen=True, slots=True)
class CodexSkillInput:
    name: str
    path: str

    def to_payload(self) -> JsonObject:
        return {"type": "skill", "name": self.name, "path": self.path}


CodexInputItem: TypeAlias = (
    CodexTextInput
    | CodexImageInput
    | CodexLocalImageInput
    | CodexMentionInput
    | CodexSkillInput
)


def to_input_payload(input_item: CodexInputItem | JsonObject) -> JsonObject:
    if isinstance(input_item, dict):
        return dict(input_item)
    return input_item.to_payload()


@dataclass(frozen=True, slots=True)
class ThreadStartOptions:
    cwd: str | Path | None = None
    model: str | None = None
    model_provider: str | None = None
    sandbox: str | None = None
    approval_policy: str | JsonObject | None = None
    approvals_reviewer: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    service_tier: str | None = None
    service_name: str | None = None
    personality: str | None = None
    ephemeral: bool | None = None
    config: JsonObject | None = None

    def to_params(self) -> JsonObject:
        return _drop_none(
            {
                "cwd": _normalize_path(self.cwd) if self.cwd is not None else None,
                "model": self.model,
                "modelProvider": self.model_provider,
                "sandbox": self.sandbox,
                "approvalPolicy": self.approval_policy,
                "approvalsReviewer": self.approvals_reviewer,
                "baseInstructions": self.base_instructions,
                "developerInstructions": self.developer_instructions,
                "serviceTier": self.service_tier,
                "serviceName": self.service_name,
                "personality": self.personality,
                "ephemeral": self.ephemeral,
                "config": dict(self.config) if self.config is not None else None,
            }
        )


@dataclass(frozen=True, slots=True)
class ThreadResumeOptions:
    thread_id: str
    cwd: str | Path | None = None
    model: str | None = None
    model_provider: str | None = None
    sandbox: str | None = None
    approval_policy: str | JsonObject | None = None
    approvals_reviewer: str | None = None
    base_instructions: str | None = None
    developer_instructions: str | None = None
    service_tier: str | None = None
    personality: str | None = None
    config: JsonObject | None = None

    def to_params(self) -> JsonObject:
        return _drop_none(
            {
                "threadId": self.thread_id,
                "cwd": _normalize_path(self.cwd) if self.cwd is not None else None,
                "model": self.model,
                "modelProvider": self.model_provider,
                "sandbox": self.sandbox,
                "approvalPolicy": self.approval_policy,
                "approvalsReviewer": self.approvals_reviewer,
                "baseInstructions": self.base_instructions,
                "developerInstructions": self.developer_instructions,
                "serviceTier": self.service_tier,
                "personality": self.personality,
                "config": dict(self.config) if self.config is not None else None,
            }
        )


@dataclass(frozen=True, slots=True)
class TurnStartOptions:
    thread_id: str
    input_items: tuple[CodexInputItem | JsonObject, ...]
    cwd: str | Path | None = None
    effort: str | None = None
    model: str | None = None
    personality: str | None = None
    service_tier: str | None = None
    summary: str | None = None
    approval_policy: str | JsonObject | None = None
    approvals_reviewer: str | None = None
    sandbox_policy: JsonObject | None = None
    output_schema: object | None = None

    def to_params(self) -> JsonObject:
        return _drop_none(
            {
                "threadId": self.thread_id,
                "input": [to_input_payload(item) for item in self.input_items],
                "cwd": _normalize_path(self.cwd) if self.cwd is not None else None,
                "effort": self.effort,
                "model": self.model,
                "personality": self.personality,
                "serviceTier": self.service_tier,
                "summary": self.summary,
                "approvalPolicy": self.approval_policy,
                "approvalsReviewer": self.approvals_reviewer,
                "sandboxPolicy": dict(self.sandbox_policy) if self.sandbox_policy is not None else None,
                "outputSchema": self.output_schema,
            }
        )


@dataclass(frozen=True, slots=True)
class CodexThreadRef:
    id: str
    status: str | None
    raw: JsonObject

    @classmethod
    def from_payload(cls, payload: object) -> "CodexThreadRef":
        if not isinstance(payload, dict):
            raise ValueError("Codex thread payload must be an object")
        thread_id = payload.get("id")
        if not isinstance(thread_id, str):
            raise ValueError("Codex thread payload is missing string field 'id'")
        status = payload.get("status")
        return cls(id=thread_id, status=status if isinstance(status, str) else None, raw=dict(payload))


@dataclass(frozen=True, slots=True)
class CodexTurnRef:
    id: str
    status: str | None
    error: JsonObject | None
    raw: JsonObject

    @classmethod
    def from_payload(cls, payload: object) -> "CodexTurnRef":
        if not isinstance(payload, dict):
            raise ValueError("Codex turn payload must be an object")
        turn_id = payload.get("id")
        if not isinstance(turn_id, str):
            raise ValueError("Codex turn payload is missing string field 'id'")
        status = payload.get("status")
        error = payload.get("error")
        return cls(
            id=turn_id,
            status=status if isinstance(status, str) else None,
            error=dict(error) if isinstance(error, dict) else None,
            raw=dict(payload),
        )


@dataclass(frozen=True, slots=True)
class CodexNotification:
    method: str
    params: JsonObject
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    request_id: RequestId | None

    @classmethod
    def from_payload(cls, method: str, params: object) -> "CodexNotification":
        normalized_params = dict(params) if isinstance(params, dict) else {}
        thread_id, turn_id, item_id, request_id = extract_routing_ids(normalized_params)
        return cls(
            method=method,
            params=normalized_params,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
            request_id=request_id,
        )


@dataclass(frozen=True, slots=True)
class CodexServerRequest:
    id: RequestId
    method: str
    params: JsonObject
    thread_id: str | None
    turn_id: str | None
    item_id: str | None

    @classmethod
    def from_payload(
        cls,
        request_id: RequestId,
        method: str,
        params: object,
    ) -> "CodexServerRequest":
        normalized_params = dict(params) if isinstance(params, dict) else {}
        thread_id, turn_id, item_id, _ = extract_routing_ids(normalized_params)
        return cls(
            id=request_id,
            method=method,
            params=normalized_params,
            thread_id=thread_id,
            turn_id=turn_id,
            item_id=item_id,
        )


@dataclass(frozen=True, slots=True)
class JsonRpcErrorPayload:
    code: int
    message: str
    data: object | None = None

    @classmethod
    def from_payload(cls, payload: object) -> "JsonRpcErrorPayload":
        if not isinstance(payload, dict):
            raise ValueError("JSON-RPC error payload must be an object")
        code = payload.get("code")
        message = payload.get("message")
        if not isinstance(code, int) or not isinstance(message, str):
            raise ValueError("JSON-RPC error payload is missing code or message")
        return cls(code=code, message=message, data=payload.get("data"))


@dataclass(frozen=True, slots=True)
class CodexTextDeltaEvent:
    channel: str
    text: str
    thread_id: str | None
    turn_id: str | None
    item_id: str | None

    @property
    def kind(self) -> str:
        return "text_delta"


@dataclass(frozen=True, slots=True)
class CodexTextMessageEvent:
    channel: str
    text: str
    thread_id: str | None
    turn_id: str | None
    item_id: str | None

    @property
    def kind(self) -> str:
        return "text_message"


@dataclass(frozen=True, slots=True)
class CodexCommandEvent:
    command: str | None
    cwd: str | None
    status: str | None
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    display_commands: tuple[str, ...] = ()
    aggregated_output: str | None = None
    exit_code: int | None = None
    delta: str | None = None

    @property
    def kind(self) -> str:
        return "command"


@dataclass(frozen=True, slots=True)
class CodexFileReferenceEvent:
    path: str
    file_name: str
    source: str
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    diff: str | None = None
    status: str | None = None

    @property
    def kind(self) -> str:
        return "file"


@dataclass(frozen=True, slots=True)
class CodexImageOutputEvent:
    reference: str
    reference_type: str
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    source: str
    revised_prompt: str | None = None

    @property
    def kind(self) -> str:
        return "image"


@dataclass(frozen=True, slots=True)
class CodexTurnErrorEvent:
    error: JsonObject
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    will_retry: bool | None = None

    @property
    def kind(self) -> str:
        return "turn_error"


@dataclass(frozen=True, slots=True)
class CodexApprovalRequestEvent:
    request_id: RequestId
    approval_type: str
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    params: JsonObject

    @property
    def kind(self) -> str:
        return "approval_request"


@dataclass(frozen=True, slots=True)
class CodexUserInputRequestEvent:
    request_id: RequestId
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    questions: tuple[JsonObject, ...]
    params: JsonObject

    @property
    def kind(self) -> str:
        return "user_input_request"


@dataclass(frozen=True, slots=True)
class CodexTurnLifecycleEvent:
    phase: str
    thread_id: str | None
    turn_id: str | None
    status: str | None
    error: JsonObject | None = None

    @property
    def kind(self) -> str:
        return "turn_lifecycle"


@dataclass(frozen=True, slots=True)
class CodexUnknownEvent:
    source: str
    thread_id: str | None
    turn_id: str | None
    item_id: str | None
    payload: JsonObject

    @property
    def kind(self) -> str:
        return "unknown"


CodexOutputEvent: TypeAlias = (
    CodexTextDeltaEvent
    | CodexTextMessageEvent
    | CodexCommandEvent
    | CodexFileReferenceEvent
    | CodexImageOutputEvent
    | CodexTurnErrorEvent
    | CodexApprovalRequestEvent
    | CodexUserInputRequestEvent
    | CodexTurnLifecycleEvent
    | CodexUnknownEvent
)
