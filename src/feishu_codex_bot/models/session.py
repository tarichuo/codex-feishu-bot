"""会话编排阶段使用的内部模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from feishu_codex_bot.models.actions import CodexInputItem


SUPPORTED_SLASH_COMMANDS: tuple[str, ...] = (
    "/clear",
    "/model",
    "/compact",
    "/init",
)

UNSUPPORTED_SLASH_MESSAGE = (
    "不支持的slash命令，当前支持的命令包括 "
    + " ".join(SUPPORTED_SLASH_COMMANDS)
)

ConversationStatus = Literal[
    "submitted",
    "ignored_not_addressed",
    "ignored_duplicate",
    "ignored_empty_input",
    "blocked_unauthorized",
    "rejected_unsupported_slash",
]


@dataclass(frozen=True, slots=True)
class PreparedConversationInput:
    """标准化后的会话输入。"""

    session_scope_key: str
    scope_type: Literal["dm", "group"]
    sender_id: str
    normalized_text: str
    input_items: tuple[CodexInputItem, ...]
    is_slash_command: bool
    slash_command: str | None
    should_rotate_thread: bool


@dataclass(frozen=True, slots=True)
class ConversationDispatchResult:
    """消息编排后的结果摘要。"""

    status: ConversationStatus
    session_scope_key: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    thread_generation: int | None = None
    is_slash_command: bool = False
    slash_command: str | None = None
    reply_text: str | None = None


@dataclass(frozen=True, slots=True)
class GroupSessionBootstrapResult:
    """机器人入群时的 thread 初始化结果。"""

    session_scope_key: str
    thread_id: str
    thread_generation: int
