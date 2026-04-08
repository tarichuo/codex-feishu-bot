"""流式回复与处理中表情控制服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from feishu_codex_bot.adapters.codex_output_classifier import CodexOutputClassifier
from feishu_codex_bot.adapters.feishu_adapter import FeishuAdapter
from feishu_codex_bot.config import AppConfig
from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger
from feishu_codex_bot.models.actions import (
    CodexNotification,
    CodexOutputEvent,
    CodexTextDeltaEvent,
    CodexTextMessageEvent,
    CodexTurnLifecycleEvent,
)
from feishu_codex_bot.persistence.reply_repo import ReplyMessageRecord, ReplyRepository
from feishu_codex_bot.workers.session_executor import SessionExecutor


ReplyStreamStatus = Literal["streaming", "completed", "failed"]

_DEFAULT_PLACEHOLDER_TEXT = "正在思考..."
_DEFAULT_FAILURE_TEXT = "本次回复已中断，请稍后重试。"


@dataclass(slots=True)
class _ReplyStreamState:
    session_scope_key: str
    source_message_id: str
    reply_message_id: str
    thread_id: str
    turn_id: str
    reply_in_thread: bool
    reaction_id: str | None
    placeholder_text: str
    last_sent_text: str
    aggregated_text: str = ""
    agent_item_id: str | None = None
    status: ReplyStreamStatus = "streaming"
    flush_task: asyncio.Task[None] | None = None
    dirty: bool = False
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ReplyService:
    """维护单个 turn 对应的飞书流式回复消息。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        feishu_adapter: FeishuAdapter,
        reply_repository: ReplyRepository,
        session_executor: SessionExecutor,
        classifier: CodexOutputClassifier | None = None,
        logger: ContextLoggerAdapter | None = None,
        update_interval_seconds: float = 0.3,
        typing_emoji_type: str = "typing",
        placeholder_text: str = _DEFAULT_PLACEHOLDER_TEXT,
        failure_text: str = _DEFAULT_FAILURE_TEXT,
    ) -> None:
        self._config = config
        self._feishu_adapter = feishu_adapter
        self._reply_repository = reply_repository
        self._session_executor = session_executor
        self._classifier = classifier or CodexOutputClassifier()
        self._logger = logger or get_logger(__name__, bot_app_id=config.feishu.app_id)
        self._update_interval_seconds = max(update_interval_seconds, 0.05)
        self._typing_emoji_type = typing_emoji_type
        self._placeholder_text = placeholder_text
        self._failure_text = failure_text
        self._streams_by_turn: dict[str, _ReplyStreamState] = {}

    async def start_turn(
        self,
        *,
        session_scope_key: str,
        source_message_id: str,
        thread_id: str,
        turn_id: str,
        reply_in_thread: bool = False,
    ) -> ReplyMessageRecord:
        await self._session_executor.activate_turn(session_scope_key, turn_id)

        reaction_id: str | None = None
        try:
            reply_message_id = self._feishu_adapter.reply_text(
                message_id=source_message_id,
                text=self._placeholder_text,
                reply_in_thread=reply_in_thread,
            )
            try:
                reaction_id = self._feishu_adapter.add_reaction(
                    message_id=source_message_id,
                    emoji_type=self._typing_emoji_type,
                )
            except Exception:
                self._logger.bind(
                    event="reply.reaction.add_failed",
                    session_scope_key=session_scope_key,
                    feishu_message_id=source_message_id,
                    turn_id=turn_id,
                ).exception("Failed to add typing reaction")

            record = self._reply_repository.create_reply(
                bot_app_id=self._config.feishu.app_id,
                feishu_message_id=source_message_id,
                reply_message_id=reply_message_id,
                thread_id=thread_id,
                turn_id=turn_id,
                status="streaming",
                reaction_applied=reaction_id is not None,
            )
            state = _ReplyStreamState(
                session_scope_key=session_scope_key,
                source_message_id=source_message_id,
                reply_message_id=reply_message_id,
                thread_id=thread_id,
                turn_id=turn_id,
                reply_in_thread=reply_in_thread,
                reaction_id=reaction_id,
                placeholder_text=self._placeholder_text,
                last_sent_text=self._placeholder_text,
            )
            self._streams_by_turn[turn_id] = state
            self._logger.bind(
                event="reply.turn.started",
                session_scope_key=session_scope_key,
                feishu_message_id=source_message_id,
                reply_message_id=reply_message_id,
                thread_id=thread_id,
                turn_id=turn_id,
                reaction_applied=reaction_id is not None,
            ).info("Started streaming reply turn")
            return record
        except Exception:
            await self._session_executor.complete_turn(session_scope_key, turn_id)
            raise

    async def handle_notification(self, notification: CodexNotification) -> bool:
        if not notification.turn_id:
            return False
        state = self._streams_by_turn.get(notification.turn_id)
        if state is None:
            return False

        handled = False
        for event in self._classifier.classify(notification):
            handled = await self._handle_output_event(state, event) or handled
        return handled

    async def fail_turn(self, turn_id: str, *, error_text: str | None = None) -> bool:
        state = self._streams_by_turn.get(turn_id)
        if state is None:
            return False
        await self._finalize_state(
            state,
            status="failed",
            fallback_text=error_text or self._failure_text,
        )
        return True

    async def close(self) -> None:
        turn_ids = list(self._streams_by_turn.keys())
        for turn_id in turn_ids:
            await self.fail_turn(turn_id, error_text=self._failure_text)

    async def _handle_output_event(
        self,
        state: _ReplyStreamState,
        event: CodexOutputEvent,
    ) -> bool:
        if isinstance(event, CodexTextDeltaEvent):
            if event.channel != "agentMessage":
                return False
            await self._append_text(state, event.text, item_id=event.item_id)
            return True

        if isinstance(event, CodexTextMessageEvent):
            if event.channel != "agentMessage":
                return False
            await self._replace_text(state, event.text, item_id=event.item_id)
            return True

        if isinstance(event, CodexTurnLifecycleEvent) and event.phase == "completed":
            if event.error:
                await self._finalize_state(
                    state,
                    status="failed",
                    fallback_text=self._failure_text,
                )
            else:
                await self._finalize_state(state, status="completed")
            return True

        return False

    async def _append_text(
        self,
        state: _ReplyStreamState,
        text: str,
        *,
        item_id: str | None,
    ) -> None:
        if not text:
            return
        async with state.lock:
            if state.closed:
                return
            state.aggregated_text += text
            if item_id is not None:
                state.agent_item_id = item_id
            state.dirty = True
            should_flush_immediately = state.flush_task is None and self._flush_is_due(state)
            if not should_flush_immediately and state.flush_task is None:
                state.flush_task = asyncio.create_task(self._flush_after_delay(state.turn_id))

        if should_flush_immediately:
            await self._flush_state(state, force=False)

    async def _replace_text(
        self,
        state: _ReplyStreamState,
        text: str,
        *,
        item_id: str | None,
    ) -> None:
        async with state.lock:
            if state.closed:
                return
            if text and (not state.aggregated_text or len(text) >= len(state.aggregated_text)):
                state.aggregated_text = text
                state.dirty = True
            if item_id is not None:
                state.agent_item_id = item_id
            should_flush_immediately = state.flush_task is None and self._flush_is_due(state)
            if not should_flush_immediately and state.flush_task is None:
                state.flush_task = asyncio.create_task(self._flush_after_delay(state.turn_id))

        if should_flush_immediately:
            await self._flush_state(state, force=False)

    def _flush_is_due(self, state: _ReplyStreamState) -> bool:
        return state.last_sent_text == state.placeholder_text or not state.dirty

    async def _flush_after_delay(self, turn_id: str) -> None:
        try:
            await asyncio.sleep(self._update_interval_seconds)
            state = self._streams_by_turn.get(turn_id)
            if state is None:
                return
            await self._flush_state(state, force=False)
        except asyncio.CancelledError:
            raise

    async def _flush_state(self, state: _ReplyStreamState, *, force: bool) -> None:
        async with state.lock:
            state.flush_task = None
            if state.closed:
                return
            target_text = state.aggregated_text or state.placeholder_text
            if not force and (not state.dirty or target_text == state.last_sent_text):
                return
            state.dirty = False

        try:
            self._feishu_adapter.update_text(
                message_id=state.reply_message_id,
                text=target_text,
            )
        except Exception:
            async with state.lock:
                if not state.closed:
                    state.dirty = True
            self._logger.bind(
                event="reply.flush.failed",
                session_scope_key=state.session_scope_key,
                reply_message_id=state.reply_message_id,
                turn_id=state.turn_id,
            ).exception("Failed to update streaming reply message")
            if force:
                raise
            return

        async with state.lock:
            state.last_sent_text = target_text

        self._reply_repository.update_reply(
            bot_app_id=self._config.feishu.app_id,
            reply_message_id=state.reply_message_id,
            turn_id=state.turn_id,
            agent_item_id=state.agent_item_id,
            status=state.status,
            reaction_applied=state.reaction_id is not None,
        )
        self._logger.bind(
            event="reply.flushed",
            session_scope_key=state.session_scope_key,
            reply_message_id=state.reply_message_id,
            turn_id=state.turn_id,
            text_length=len(target_text),
            force=force,
        ).info("Flushed reply text to Feishu")

    async def _finalize_state(
        self,
        state: _ReplyStreamState,
        *,
        status: ReplyStreamStatus,
        fallback_text: str | None = None,
    ) -> None:
        flush_task: asyncio.Task[None] | None = None
        async with state.lock:
            if state.closed:
                return
            state.status = status
            flush_task = state.flush_task
            state.flush_task = None
            if flush_task is not None:
                flush_task.cancel()
            if fallback_text and not state.aggregated_text:
                state.aggregated_text = fallback_text
            state.dirty = True

        if flush_task is not None:
            try:
                await flush_task
            except asyncio.CancelledError:
                pass

        try:
            await self._flush_state(state, force=True)
        except Exception:
            self._logger.bind(
                event="reply.finalize.flush_failed",
                session_scope_key=state.session_scope_key,
                reply_message_id=state.reply_message_id,
                turn_id=state.turn_id,
                status=status,
            ).exception("Failed to flush final reply text")

        if state.reaction_id is not None:
            try:
                self._feishu_adapter.remove_reaction(
                    message_id=state.source_message_id,
                    reaction_id=state.reaction_id,
                )
            except Exception:
                self._logger.bind(
                    event="reply.reaction.remove_failed",
                    session_scope_key=state.session_scope_key,
                    feishu_message_id=state.source_message_id,
                    turn_id=state.turn_id,
                ).exception("Failed to remove typing reaction")

        async with state.lock:
            state.closed = True

        self._reply_repository.update_reply(
            bot_app_id=self._config.feishu.app_id,
            reply_message_id=state.reply_message_id,
            turn_id=state.turn_id,
            agent_item_id=state.agent_item_id,
            status=status,
            reaction_applied=False,
        )
        await self._session_executor.complete_turn(state.session_scope_key, state.turn_id)
        self._streams_by_turn.pop(state.turn_id, None)
        self._logger.bind(
            event="reply.turn.finalized",
            session_scope_key=state.session_scope_key,
            reply_message_id=state.reply_message_id,
            thread_id=state.thread_id,
            turn_id=state.turn_id,
            status=status,
            text_length=len(state.aggregated_text or state.placeholder_text),
        ).info("Finalized reply stream")
