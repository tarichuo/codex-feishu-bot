"""飞书长连接、消息标准化和消息发送适配层。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import uuid

import lark_oapi as lark
from lark_oapi.api.im.v1.model.create_message_reaction_request import CreateMessageReactionRequest
from lark_oapi.api.im.v1.model.create_message_reaction_request_body import (
    CreateMessageReactionRequestBody,
)
from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import CreateMessageRequestBody
from lark_oapi.api.im.v1.model.delete_message_reaction_request import DeleteMessageReactionRequest
from lark_oapi.api.im.v1.model.emoji import Emoji
from lark_oapi.api.im.v1.model.p2_im_chat_member_bot_added_v1 import P2ImChatMemberBotAddedV1
from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_oapi.api.im.v1.model.reply_message_request import ReplyMessageRequest
from lark_oapi.api.im.v1.model.reply_message_request_body import ReplyMessageRequestBody
from lark_oapi.api.im.v1.model.update_message_request import UpdateMessageRequest
from lark_oapi.api.im.v1.model.update_message_request_body import UpdateMessageRequestBody
from lark_oapi.core.enum import LogLevel
from lark_oapi.core.model import BaseResponse

from feishu_codex_bot.config import AppConfig
from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger
from feishu_codex_bot.models.inbound import (
    BotAddedEvent,
    FileContent,
    ImageContent,
    InboundContentPart,
    InboundMessage,
    MentionRef,
    TextContent,
)


class FeishuAdapterError(RuntimeError):
    """Raised when a Feishu event cannot be normalized safely."""


class FeishuApiError(RuntimeError):
    """Raised when a Feishu OpenAPI call fails."""


class FeishuAdapter:
    """Wrap the official Feishu SDK behind project-specific interfaces."""

    def __init__(
        self,
        config: AppConfig,
        *,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._config = config
        self._logger = logger or get_logger(__name__, bot_app_id=config.feishu.app_id)
        self._client = (
            lark.Client.builder()
            .app_id(config.feishu.app_id)
            .app_secret(config.feishu.app_secret)
            .log_level(self._to_sdk_log_level(config.logging.level))
            .build()
        )

    @property
    def client(self) -> lark.Client:
        return self._client

    def create_long_connection_client(
        self,
        *,
        on_message: Callable[[InboundMessage], None],
        on_bot_added: Callable[[BotAddedEvent], None] | None = None,
    ) -> lark.ws.Client:
        builder = lark.EventDispatcherHandler.builder(
            "",
            "",
            level=self._to_sdk_log_level(self._config.logging.level),
        )
        builder.register_p2_im_message_receive_v1(
            lambda event: on_message(self.normalize_message_event(event))
        )
        builder.register_p2_im_chat_member_bot_added_v1(
            lambda event: self._dispatch_bot_added(event, on_bot_added)
        )
        return lark.ws.Client(
            self._config.feishu.app_id,
            self._config.feishu.app_secret,
            log_level=self._to_sdk_log_level(self._config.logging.level),
            event_handler=builder.build(),
        )

    def start_long_connection(
        self,
        *,
        on_message: Callable[[InboundMessage], None],
        on_bot_added: Callable[[BotAddedEvent], None] | None = None,
    ) -> None:
        self._logger.bind(event="feishu.long_connection.start").info(
            "Starting Feishu long connection"
        )
        self.create_long_connection_client(
            on_message=on_message,
            on_bot_added=on_bot_added,
        ).start()

    def normalize_message_event(self, event: P2ImMessageReceiveV1) -> InboundMessage:
        if event.event is None or event.event.message is None or event.event.sender is None:
            raise FeishuAdapterError("Received Feishu message event without sender or message payload")

        header = getattr(event, "header", None)
        sender = event.event.sender
        message = event.event.message
        raw_payload = self._parse_json_content(message.content)
        parts = self._extract_parts(message.message_type, raw_payload)
        mentions = self._normalize_mentions(message.mentions or [])
        normalized = InboundMessage(
            event_id=getattr(header, "event_id", None),
            event_type=getattr(header, "event_type", None),
            tenant_key=getattr(header, "tenant_key", None),
            app_id=getattr(header, "app_id", None),
            sender_open_id=getattr(getattr(sender, "sender_id", None), "open_id", None),
            sender_user_id=getattr(getattr(sender, "sender_id", None), "user_id", None),
            sender_union_id=getattr(getattr(sender, "sender_id", None), "union_id", None),
            sender_type=sender.sender_type,
            message_id=self._require_value(message.message_id, "message_id"),
            root_id=message.root_id,
            parent_id=message.parent_id,
            chat_id=self._require_value(message.chat_id, "chat_id"),
            thread_id=message.thread_id,
            chat_type=self._require_value(message.chat_type, "chat_type"),
            message_type=self._require_value(message.message_type, "message_type"),
            mentions=mentions,
            parts=parts,
            raw_content=message.content,
            raw_payload=raw_payload,
            created_at=InboundMessage.utc_from_millis(message.create_time or getattr(header, "create_time", None)),
            updated_at=InboundMessage.utc_from_millis(message.update_time) if message.update_time else None,
        )
        self._logger.bind(
            event="feishu.message.normalized",
            feishu_event_id=normalized.event_id,
            feishu_message_id=normalized.message_id,
            chat_id=normalized.chat_id,
            chat_type=normalized.chat_type,
            message_type=normalized.message_type,
            sender_open_id=normalized.sender_open_id,
            parts=[part.kind for part in normalized.parts],
        ).info("Feishu message normalized")
        return normalized

    def normalize_bot_added_event(self, event: P2ImChatMemberBotAddedV1) -> BotAddedEvent:
        if event.event is None or not event.event.chat_id:
            raise FeishuAdapterError("Received bot-added event without chat_id")

        header = getattr(event, "header", None)
        operator = getattr(event.event, "operator_id", None)
        normalized = BotAddedEvent(
            event_id=getattr(header, "event_id", None),
            event_type=getattr(header, "event_type", None),
            tenant_key=getattr(header, "tenant_key", None),
            app_id=getattr(header, "app_id", None),
            chat_id=event.event.chat_id,
            operator_open_id=getattr(operator, "open_id", None),
            operator_user_id=getattr(operator, "user_id", None),
            operator_union_id=getattr(operator, "union_id", None),
            chat_name=event.event.name,
            is_external_chat=bool(event.event.external),
            occurred_at=BotAddedEvent.utc_from_millis(getattr(header, "create_time", None)),
        )
        self._logger.bind(
            event="feishu.bot_added.normalized",
            feishu_event_id=normalized.event_id,
            chat_id=normalized.chat_id,
            operator_open_id=normalized.operator_open_id,
        ).info("Feishu bot-added event normalized")
        return normalized

    def send_text(
        self,
        *,
        receive_id: str,
        text: str,
        receive_id_type: str = "chat_id",
    ) -> str:
        return self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="text",
            content=self._json_content({"text": text}),
        )

    def send_image(
        self,
        *,
        receive_id: str,
        image_key: str,
        receive_id_type: str = "chat_id",
    ) -> str:
        return self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="image",
            content=self._json_content({"image_key": image_key}),
        )

    def send_file(
        self,
        *,
        receive_id: str,
        file_key: str,
        receive_id_type: str = "chat_id",
    ) -> str:
        return self._send_message(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type="file",
            content=self._json_content({"file_key": file_key}),
        )

    def reply_text(
        self,
        *,
        message_id: str,
        text: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self._reply_message(
            message_id=message_id,
            msg_type="text",
            content=self._json_content({"text": text}),
            reply_in_thread=reply_in_thread,
        )

    def reply_image(
        self,
        *,
        message_id: str,
        image_key: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self._reply_message(
            message_id=message_id,
            msg_type="image",
            content=self._json_content({"image_key": image_key}),
            reply_in_thread=reply_in_thread,
        )

    def reply_file(
        self,
        *,
        message_id: str,
        file_key: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self._reply_message(
            message_id=message_id,
            msg_type="file",
            content=self._json_content({"file_key": file_key}),
            reply_in_thread=reply_in_thread,
        )

    def update_text(self, *, message_id: str, text: str) -> str:
        response = self._client.im.v1.message.update(
            UpdateMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                UpdateMessageRequestBody.builder()
                .msg_type("text")
                .content(self._json_content({"text": text}))
                .build()
            )
            .build()
        )
        self._ensure_success(response, action="update_message", key=message_id)
        updated_message_id = getattr(response.data, "message_id", None)
        if not updated_message_id:
            raise FeishuApiError("Feishu update_message succeeded but message_id is missing")
        self._logger.bind(
            event="feishu.message.updated",
            feishu_message_id=updated_message_id,
        ).info("Feishu message updated")
        return updated_message_id

    def add_reaction(self, *, message_id: str, emoji_type: str) -> str:
        response = self._client.im.v1.message_reaction.create(
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()
            )
            .build()
        )
        self._ensure_success(response, action="add_reaction", key=message_id)
        reaction_id = getattr(response.data, "reaction_id", None)
        if not reaction_id:
            raise FeishuApiError("Feishu add_reaction succeeded but reaction_id is missing")
        self._logger.bind(
            event="feishu.message.reaction_added",
            feishu_message_id=message_id,
            reaction_id=reaction_id,
            emoji_type=emoji_type,
        ).info("Feishu message reaction added")
        return reaction_id

    def remove_reaction(self, *, message_id: str, reaction_id: str) -> None:
        response = self._client.im.v1.message_reaction.delete(
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        self._ensure_success(response, action="remove_reaction", key=f"{message_id}:{reaction_id}")
        self._logger.bind(
            event="feishu.message.reaction_removed",
            feishu_message_id=message_id,
            reaction_id=reaction_id,
        ).info("Feishu message reaction removed")

    def send_owner_alert(self, *, owner_open_id: str, text: str) -> str:
        return self.send_text(
            receive_id=owner_open_id,
            text=text,
            receive_id_type="open_id",
        )

    def send_approval_message(
        self,
        *,
        message_id: str,
        text: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self.reply_text(
            message_id=message_id,
            text=text,
            reply_in_thread=reply_in_thread,
        )

    def update_approval_message(self, *, message_id: str, text: str) -> str:
        return self.update_text(
            message_id=message_id,
            text=text,
        )

    def send_user_input_message(
        self,
        *,
        message_id: str,
        text: str,
        reply_in_thread: bool = False,
    ) -> str:
        return self.reply_text(
            message_id=message_id,
            text=text,
            reply_in_thread=reply_in_thread,
        )

    def update_user_input_message(self, *, message_id: str, text: str) -> str:
        return self.update_text(
            message_id=message_id,
            text=text,
        )

    def _dispatch_bot_added(
        self,
        event: P2ImChatMemberBotAddedV1,
        handler: Callable[[BotAddedEvent], None] | None,
    ) -> None:
        normalized = self.normalize_bot_added_event(event)
        if handler is None:
            self._logger.bind(
                event="feishu.bot_added.unhandled",
                chat_id=normalized.chat_id,
                feishu_event_id=normalized.event_id,
            ).info("Bot-added event received without handler")
            return
        handler(normalized)

    def _send_message(
        self,
        *,
        receive_id: str,
        receive_id_type: str,
        msg_type: str,
        content: str,
    ) -> str:
        response = self._client.im.v1.message.create(
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .uuid(uuid.uuid4().hex)
                .build()
            )
            .build()
        )
        self._ensure_success(response, action="send_message", key=receive_id)
        message_id = getattr(response.data, "message_id", None)
        if not message_id:
            raise FeishuApiError("Feishu send_message succeeded but message_id is missing")
        self._logger.bind(
            event="feishu.message.sent",
            feishu_message_id=message_id,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type=msg_type,
        ).info("Feishu message sent")
        return message_id

    def _reply_message(
        self,
        *,
        message_id: str,
        msg_type: str,
        content: str,
        reply_in_thread: bool,
    ) -> str:
        response = self._client.im.v1.message.reply(
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(content)
                .msg_type(msg_type)
                .reply_in_thread(reply_in_thread)
                .uuid(uuid.uuid4().hex)
                .build()
            )
            .build()
        )
        self._ensure_success(response, action="reply_message", key=message_id)
        reply_message_id = getattr(response.data, "message_id", None)
        if not reply_message_id:
            raise FeishuApiError("Feishu reply_message succeeded but reply message_id is missing")
        self._logger.bind(
            event="feishu.message.replied",
            feishu_message_id=reply_message_id,
            source_message_id=message_id,
            msg_type=msg_type,
            reply_in_thread=reply_in_thread,
        ).info("Feishu reply message sent")
        return reply_message_id

    def _extract_parts(
        self,
        message_type: str | None,
        payload: object,
    ) -> tuple[InboundContentPart, ...]:
        if message_type == "text":
            text = self._string_or_none(self._mapping_get(payload, "text"))
            return self._merge_adjacent_text_parts([TextContent(text or "")])

        if message_type == "image":
            image_key = self._string_or_none(self._mapping_get(payload, "image_key"))
            if not image_key:
                raise FeishuAdapterError("Feishu image message is missing image_key")
            return (ImageContent(image_key=image_key),)

        if message_type == "file":
            file_key = self._string_or_none(self._mapping_get(payload, "file_key"))
            if not file_key:
                raise FeishuAdapterError("Feishu file message is missing file_key")
            return (
                FileContent(
                    file_key=file_key,
                    file_name=self._string_or_none(self._mapping_get(payload, "file_name")),
                    file_size=self._int_or_none(self._mapping_get(payload, "file_size")),
                ),
            )

        if message_type == "post":
            return self._extract_post_parts(payload)

        raw_text = self._fallback_raw_text(payload)
        return self._merge_adjacent_text_parts([TextContent(raw_text)])

    def _extract_post_parts(self, payload: object) -> tuple[InboundContentPart, ...]:
        locale_block = self._select_post_locale(payload)
        rows = locale_block.get("content", [])
        if not isinstance(rows, list):
            return (TextContent(self._fallback_raw_text(payload)),)

        parts: list[InboundContentPart] = []
        for row_index, row in enumerate(rows):
            if row_index > 0 and parts:
                parts.append(TextContent("\n"))
            if not isinstance(row, list):
                continue
            for element in row:
                if not isinstance(element, dict):
                    continue
                tag = element.get("tag")
                if tag == "text":
                    text = self._string_or_none(element.get("text"))
                    if text:
                        parts.append(TextContent(text))
                    continue
                if tag == "a":
                    text = self._string_or_none(element.get("text"))
                    href = self._string_or_none(element.get("href"))
                    if text and href and href != text:
                        parts.append(TextContent(f"{text} ({href})"))
                    elif text or href:
                        parts.append(TextContent(text or href or ""))
                    continue
                if tag == "at":
                    name = (
                        self._string_or_none(element.get("user_name"))
                        or self._string_or_none(element.get("text"))
                        or self._string_or_none(element.get("user_id"))
                        or "unknown"
                    )
                    parts.append(TextContent(f"@{name}"))
                    continue
                if tag == "img":
                    image_key = self._string_or_none(element.get("image_key"))
                    if image_key:
                        parts.append(
                            ImageContent(
                                image_key=image_key,
                                file_name=self._string_or_none(element.get("image_name")),
                            )
                        )
                    continue
                if tag == "media":
                    file_key = self._string_or_none(element.get("file_key"))
                    image_key = self._string_or_none(element.get("image_key"))
                    if file_key:
                        parts.append(
                            FileContent(
                                file_key=file_key,
                                file_name=self._string_or_none(element.get("file_name")),
                                file_size=self._int_or_none(element.get("file_size")),
                            )
                        )
                    elif image_key:
                        parts.append(ImageContent(image_key=image_key))
                    continue
                fallback = (
                    self._string_or_none(element.get("text"))
                    or self._string_or_none(element.get("emoji_type"))
                    or None
                )
                if fallback:
                    parts.append(TextContent(fallback))

        if not parts:
            parts.append(TextContent(self._fallback_raw_text(payload)))
        return self._merge_adjacent_text_parts(parts)

    def _normalize_mentions(self, mentions: Sequence[object]) -> tuple[MentionRef, ...]:
        normalized: list[MentionRef] = []
        for mention in mentions:
            normalized.append(
                MentionRef(
                    key=getattr(mention, "key", None),
                    name=getattr(mention, "name", None),
                    open_id=getattr(getattr(mention, "id", None), "open_id", None),
                    user_id=getattr(getattr(mention, "id", None), "user_id", None),
                    union_id=getattr(getattr(mention, "id", None), "union_id", None),
                )
            )
        return tuple(normalized)

    def _select_post_locale(self, payload: object) -> dict[str, object]:
        if not isinstance(payload, dict):
            return {}
        for locale_key in ("zh_cn", "en_us"):
            locale_block = payload.get(locale_key)
            if isinstance(locale_block, dict):
                return locale_block
        for value in payload.values():
            if isinstance(value, dict):
                return value
        return {}

    def _merge_adjacent_text_parts(
        self,
        parts: Sequence[InboundContentPart],
    ) -> tuple[InboundContentPart, ...]:
        merged: list[InboundContentPart] = []
        for part in parts:
            if isinstance(part, TextContent):
                if merged and isinstance(merged[-1], TextContent):
                    merged[-1] = TextContent(merged[-1].text + part.text)
                elif part.text:
                    merged.append(part)
                elif not merged:
                    merged.append(part)
                continue
            merged.append(part)
        return tuple(merged)

    def _ensure_success(self, response: BaseResponse, *, action: str, key: str) -> None:
        if response.success():
            return
        raise FeishuApiError(
            f"Feishu {action} failed for {key}: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
        )

    def _parse_json_content(self, raw_content: str | None) -> object:
        if not raw_content:
            return {}
        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            self._logger.bind(
                event="feishu.message.invalid_json_content",
                raw_content=raw_content,
            ).warning("Feishu message content is not valid JSON")
            return {"raw_text": raw_content}

    def _fallback_raw_text(self, payload: object) -> str:
        if isinstance(payload, dict):
            if "raw_text" in payload:
                raw_text = self._string_or_none(payload.get("raw_text"))
                if raw_text is not None:
                    return raw_text
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if payload is None:
            return ""
        return str(payload)

    def _mapping_get(self, payload: object, key: str) -> object | None:
        if not isinstance(payload, dict):
            return None
        return payload.get(key)

    def _to_sdk_log_level(self, raw_level: str) -> LogLevel:
        try:
            return LogLevel[raw_level.upper()]
        except KeyError as exc:
            raise FeishuAdapterError(f"Unsupported log level for Feishu SDK: {raw_level}") from exc

    def _json_content(self, payload: dict[str, object]) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def _require_value(self, value: str | None, field_name: str) -> str:
        if value:
            return value
        raise FeishuAdapterError(f"Feishu event missing required field: {field_name}")

    def _string_or_none(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    def _int_or_none(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
