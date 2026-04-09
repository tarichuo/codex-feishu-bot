"""流式回复与处理中表情控制服务。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
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
_MAX_STREAMING_UPDATES_PER_SECOND = 10
_MIN_UPDATE_INTERVAL_SECONDS = 1 / _MAX_STREAMING_UPDATES_PER_SECOND


def _elapsed_ms(start_time: float) -> int:
    return int((time.perf_counter() - start_time) * 1000)


@dataclass(slots=True)
class _ReplyStreamState:
    session_scope_key: str
    source_message_id: str
    reply_message_id: str | None
    reply_card_id: str | None
    thread_id: str
    turn_id: str
    reply_in_thread: bool
    reaction_id: str | None
    placeholder_text: str
    last_sent_text: str
    next_sequence: int
    aggregated_text: str = ""
    agent_item_id: str | None = None
    status: ReplyStreamStatus = "streaming"
    flush_task: asyncio.Task[None] | None = None
    dirty: bool = False
    flushing: bool = False
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
        update_interval_seconds: float = _MIN_UPDATE_INTERVAL_SECONDS,
        typing_emoji_type: str = "Typing",
        placeholder_text: str = _DEFAULT_PLACEHOLDER_TEXT,
        failure_text: str = _DEFAULT_FAILURE_TEXT,
    ) -> None:
        self._config = config
        self._feishu_adapter = feishu_adapter
        self._reply_repository = reply_repository
        self._session_executor = session_executor
        self._classifier = classifier or CodexOutputClassifier()
        self._logger = logger or get_logger(__name__, bot_app_id=config.feishu.app_id)
        self._update_interval_seconds = max(update_interval_seconds, _MIN_UPDATE_INTERVAL_SECONDS)
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
    ) -> ReplyMessageRecord | None:
        await self._session_executor.activate_turn(session_scope_key, turn_id)

        reaction_id: str | None = None
        try:
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
                    emoji_type=self._typing_emoji_type,
                ).exception("Failed to add typing reaction")

            state = _ReplyStreamState(
                session_scope_key=session_scope_key,
                source_message_id=source_message_id,
                reply_message_id=None,
                reply_card_id=None,
                thread_id=thread_id,
                turn_id=turn_id,
                reply_in_thread=reply_in_thread,
                reaction_id=reaction_id,
                placeholder_text=self._placeholder_text,
                last_sent_text="",
                next_sequence=0,
            )
            self._streams_by_turn[turn_id] = state
            self._logger.bind(
                event="reply.turn.started",
                session_scope_key=session_scope_key,
                feishu_message_id=source_message_id,
                thread_id=thread_id,
                turn_id=turn_id,
                reaction_applied=reaction_id is not None,
            ).info("Started reply turn without creating reply card yet")
            return None
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

    async def start_followup_turn(self, turn_id: str) -> bool:
        total_start = time.perf_counter()
        state = self._streams_by_turn.get(turn_id)
        if state is None:
            return False

        flush_task: asyncio.Task[None] | None = None
        async with state.lock:
            if state.closed:
                return False
            flush_task = state.flush_task
            state.flush_task = None
            had_reply_card = state.reply_message_id is not None and state.reply_card_id is not None
            previous_reply_message_id = state.reply_message_id
            previous_reply_card_id = state.reply_card_id
            reaction_applied = state.reaction_id is not None
            source_message_id = state.source_message_id
            reply_in_thread = state.reply_in_thread
            session_scope_key = state.session_scope_key
            thread_id = state.thread_id
            turn_id = state.turn_id

        if flush_task is not None:
            flush_task.cancel()
            try:
                await flush_task
            except asyncio.CancelledError:
                pass

        if had_reply_card:
            complete_previous_card_start = time.perf_counter()
            await self._complete_current_card_for_followup(state)
            self._logger.bind(
                event="reply.turn.followup.previous_card_completed",
                turn_id=turn_id,
                previous_reply_message_id=previous_reply_message_id,
                previous_reply_card_id=previous_reply_card_id,
                elapsed_ms=_elapsed_ms(complete_previous_card_start),
                total_elapsed_ms=_elapsed_ms(total_start),
            ).info("Completed previous reply card before follow-up")
            persistence_start = time.perf_counter()
            self._reply_repository.update_reply(
                bot_app_id=self._config.feishu.app_id,
                reply_message_id=previous_reply_message_id,
                status="superseded",
                reaction_applied=reaction_applied,
            )
            self._logger.bind(
                event="reply.turn.followup.persistence_updated",
                turn_id=turn_id,
                previous_reply_message_id=previous_reply_message_id,
                elapsed_ms=_elapsed_ms(persistence_start),
                total_elapsed_ms=_elapsed_ms(total_start),
            ).info("Persisted follow-up reply records")

        memory_state_start = time.perf_counter()
        async with state.lock:
            if state.closed:
                return False
            state.reply_message_id = None
            state.reply_card_id = None
            state.placeholder_text = self._placeholder_text
            state.last_sent_text = ""
            state.next_sequence = 0
            state.aggregated_text = ""
            state.agent_item_id = None
            state.status = "streaming"
            state.dirty = False
            state.flushing = False
        self._logger.bind(
            event="reply.turn.followup.memory_state_updated",
            turn_id=turn_id,
            had_previous_reply_card=had_reply_card,
            elapsed_ms=_elapsed_ms(memory_state_start),
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Reset in-memory follow-up reply state")

        self._logger.bind(
            event="reply.turn.followup_started",
            session_scope_key=session_scope_key,
            source_message_id=source_message_id,
            previous_reply_message_id=previous_reply_message_id,
            previous_reply_card_id=previous_reply_card_id,
            reply_message_id=None,
            reply_card_id=None,
            thread_id=thread_id,
            turn_id=turn_id,
            total_elapsed_ms=_elapsed_ms(total_start),
        ).info("Prepared follow-up turn and will create reply card on first output")
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
            should_flush_immediately = (
                state.flush_task is None and not state.flushing and self._flush_is_due(state)
            )
            if not should_flush_immediately and state.flush_task is None and not state.flushing:
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
            should_flush_immediately = (
                state.flush_task is None and not state.flushing and self._flush_is_due(state)
            )
            if not should_flush_immediately and state.flush_task is None and not state.flushing:
                state.flush_task = asyncio.create_task(self._flush_after_delay(state.turn_id))

        if should_flush_immediately:
            await self._flush_state(state, force=False)

    def _flush_is_due(self, state: _ReplyStreamState) -> bool:
        if state.reply_card_id is None:
            return True
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
        create_reply_card = False
        source_message_id = ""
        reply_in_thread = False
        thread_id = ""
        agent_item_id: str | None = None
        sequence = 0
        status: ReplyStreamStatus = "streaming"
        target_text = ""
        async with state.lock:
            state.flush_task = None
            if state.closed or state.flushing:
                return
            if state.reply_card_id is None:
                if not state.aggregated_text:
                    return
                create_reply_card = True
                target_text = state.aggregated_text
                status = state.status
                source_message_id = state.source_message_id
                reply_in_thread = state.reply_in_thread
                thread_id = state.thread_id
                agent_item_id = state.agent_item_id
                state.flushing = True
            else:
                target_text = state.aggregated_text or state.placeholder_text
                if not force and (not state.dirty or target_text == state.last_sent_text):
                    return
                sequence = state.next_sequence
                status = state.status
                state.flushing = True

        try:
            if create_reply_card:
                reply_card = self._feishu_adapter.reply_streaming_card(
                    message_id=source_message_id,
                    text=target_text,
                    reply_in_thread=reply_in_thread,
                    status=status,
                )
                record = self._reply_repository.create_reply(
                    bot_app_id=self._config.feishu.app_id,
                    feishu_message_id=source_message_id,
                    reply_message_id=reply_card.message_id,
                    thread_id=thread_id,
                    turn_id=state.turn_id,
                    agent_item_id=agent_item_id,
                    status=status,
                    reaction_applied=state.reaction_id is not None,
                )
                async with state.lock:
                    if not state.closed:
                        state.reply_message_id = reply_card.message_id
                        state.reply_card_id = reply_card.card_id
                        state.last_sent_text = target_text
                        state.next_sequence = 2
                        state.dirty = state.aggregated_text != target_text
                self._logger.bind(
                    event="reply.turn.reply_card_created",
                    session_scope_key=state.session_scope_key,
                    feishu_message_id=source_message_id,
                    reply_message_id=reply_card.message_id,
                    reply_card_id=reply_card.card_id,
                    thread_id=state.thread_id,
                    turn_id=state.turn_id,
                    status=status,
                    text_length=len(target_text),
                ).info("Created reply card after first Codex output")
                self._logger.bind(
                    event="reply.flushed",
                    session_scope_key=state.session_scope_key,
                    reply_message_id=record.reply_message_id,
                    reply_card_id=reply_card.card_id,
                    turn_id=state.turn_id,
                    text_length=len(target_text),
                    force=force,
                ).info("Flushed reply text to Feishu")
                return

            consumed_sequences = self._feishu_adapter.update_streaming_card(
                card_id=state.reply_card_id,
                text=target_text,
                status=status,
                sequence=sequence,
            )
        except Exception:
            async with state.lock:
                if not state.closed:
                    state.dirty = True
            self._logger.bind(
                event="reply.flush.failed",
                session_scope_key=state.session_scope_key,
                reply_message_id=state.reply_message_id,
                reply_card_id=state.reply_card_id,
                turn_id=state.turn_id,
            ).exception("Failed to update streaming reply message")
            if force:
                raise
            return
        finally:
            async with state.lock:
                state.flushing = False

        async with state.lock:
            state.last_sent_text = target_text
            state.next_sequence = sequence + consumed_sequences
            state.dirty = False

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
            reply_card_id=state.reply_card_id,
            turn_id=state.turn_id,
            text_length=len(target_text),
            force=force,
        ).info("Flushed reply text to Feishu")

    async def _close_streaming_card(self, state: _ReplyStreamState) -> None:
        async with state.lock:
            if state.closed:
                return
            card_id = state.reply_card_id
            if card_id is None:
                return
            sequence = state.next_sequence

        self._feishu_adapter.disable_streaming_card(
            card_id=card_id,
            sequence=sequence,
        )
        async with state.lock:
            if state.closed:
                return
            state.next_sequence = sequence + 1
        self._logger.bind(
            event="reply.card.streaming_closed",
            session_scope_key=state.session_scope_key,
            reply_message_id=state.reply_message_id,
            reply_card_id=card_id,
            turn_id=state.turn_id,
            sequence=sequence,
        ).info("Closed Feishu streaming mode for reply card")

    async def _complete_current_card_for_followup(self, state: _ReplyStreamState) -> None:
        async with state.lock:
            has_reply_card = state.reply_card_id is not None
        if not has_reply_card:
            return
        async with state.lock:
            if state.closed:
                return
            state.status = "completed"
            state.dirty = True

        try:
            await self._flush_state(state, force=True)
        except Exception:
            self._logger.bind(
                event="reply.followup.flush_failed",
                session_scope_key=state.session_scope_key,
                reply_message_id=state.reply_message_id,
                reply_card_id=state.reply_card_id,
                turn_id=state.turn_id,
            ).exception("Failed to finalize previous reply card before follow-up")

        try:
            await self._close_streaming_card(state)
        except Exception:
            self._logger.bind(
                event="reply.followup.streaming_close_failed",
                session_scope_key=state.session_scope_key,
                reply_message_id=state.reply_message_id,
                reply_card_id=state.reply_card_id,
                turn_id=state.turn_id,
            ).exception("Failed to close previous reply card streaming mode before follow-up")

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
            if fallback_text and not state.aggregated_text and state.reply_card_id is not None:
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
                reply_card_id=state.reply_card_id,
                turn_id=state.turn_id,
                status=status,
            ).exception("Failed to flush final reply text")

        try:
            await self._close_streaming_card(state)
        except Exception:
            self._logger.bind(
                event="reply.finalize.streaming_close_failed",
                session_scope_key=state.session_scope_key,
                reply_message_id=state.reply_message_id,
                reply_card_id=state.reply_card_id,
                turn_id=state.turn_id,
                status=status,
            ).exception("Failed to close reply card streaming mode")

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

        if state.reply_message_id is not None:
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
            reply_card_id=state.reply_card_id,
            thread_id=state.thread_id,
            turn_id=state.turn_id,
            status=status,
            text_length=len(state.aggregated_text or state.placeholder_text),
        ).info("Finalized reply stream")
