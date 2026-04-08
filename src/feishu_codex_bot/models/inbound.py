"""飞书入站事件和媒体标准化模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias


def _utc_from_millis(value: int | str | None) -> datetime:
    if value is None:
        return datetime.now(tz=timezone.utc)

    raw = int(value)
    # 飞书事件时间通常是毫秒时间戳，兼容部分场景直接传秒级时间戳。
    if raw > 10_000_000_000:
        return datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
    return datetime.fromtimestamp(raw, tz=timezone.utc)


@dataclass(frozen=True, slots=True)
class MentionRef:
    key: str | None
    name: str | None
    open_id: str | None
    user_id: str | None
    union_id: str | None


@dataclass(frozen=True, slots=True)
class TextContent:
    text: str

    @property
    def kind(self) -> str:
        return "text"


@dataclass(frozen=True, slots=True)
class ImageContent:
    image_key: str
    file_name: str | None = None

    @property
    def kind(self) -> str:
        return "image"


@dataclass(frozen=True, slots=True)
class FileContent:
    file_key: str
    file_name: str | None = None
    file_size: int | None = None

    @property
    def kind(self) -> str:
        return "file"


InboundContentPart: TypeAlias = TextContent | ImageContent | FileContent


@dataclass(frozen=True, slots=True)
class InboundMessage:
    event_id: str | None
    event_type: str | None
    tenant_key: str | None
    app_id: str | None
    sender_open_id: str | None
    sender_user_id: str | None
    sender_union_id: str | None
    sender_type: str | None
    message_id: str
    root_id: str | None
    parent_id: str | None
    chat_id: str
    thread_id: str | None
    chat_type: str
    message_type: str
    mentions: tuple[MentionRef, ...]
    parts: tuple[InboundContentPart, ...]
    raw_content: str | None
    raw_payload: Any
    created_at: datetime
    updated_at: datetime | None

    @property
    def scope_type(self) -> str:
        return "dm" if self.chat_type == "p2p" else "group"

    @property
    def is_direct_message(self) -> bool:
        return self.scope_type == "dm"

    @property
    def is_group_message(self) -> bool:
        return self.scope_type == "group"

    @property
    def mention_open_ids(self) -> tuple[str, ...]:
        return tuple(mention.open_id for mention in self.mentions if mention.open_id)

    @property
    def text_parts(self) -> tuple[str, ...]:
        return tuple(part.text for part in self.parts if isinstance(part, TextContent))

    @property
    def contains_image(self) -> bool:
        return any(isinstance(part, ImageContent) for part in self.parts)

    @property
    def contains_file(self) -> bool:
        return any(isinstance(part, FileContent) for part in self.parts)

    @staticmethod
    def utc_from_millis(value: int | str | None) -> datetime:
        return _utc_from_millis(value)


@dataclass(frozen=True, slots=True)
class BotAddedEvent:
    event_id: str | None
    event_type: str | None
    tenant_key: str | None
    app_id: str | None
    chat_id: str
    operator_open_id: str | None
    operator_user_id: str | None
    operator_union_id: str | None
    chat_name: str | None
    is_external_chat: bool
    occurred_at: datetime

    @staticmethod
    def utc_from_millis(value: int | str | None) -> datetime:
        return _utc_from_millis(value)


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    media_type: str
    source_key: str
    source_message_id: str | None
    local_path: Path
    file_name: str
    size_bytes: int
    sha256: str
    mime_type: str | None
    downloaded_at: datetime


InboundEvent: TypeAlias = InboundMessage | BotAddedEvent


@dataclass(frozen=True, slots=True)
class CardActionCallback:
    event_id: str | None
    event_type: str | None
    tenant_key: str | None
    app_id: str | None
    operator_open_id: str | None
    operator_user_id: str | None
    operator_union_id: str | None
    open_message_id: str | None
    open_chat_id: str | None
    action_tag: str | None
    action_name: str | None
    action_value: dict[str, str]
    form_value: dict[str, str]
    input_value: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CardActionCallbackResult:
    toast_type: str
    toast_text: str
    card_payload: dict[str, object] | None = None
