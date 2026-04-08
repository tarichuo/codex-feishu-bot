"""会话编排与 slash 命令路由服务。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from feishu_codex_bot.adapters.codex_client import CodexClient
from feishu_codex_bot.adapters.feishu_adapter import FeishuAdapter
from feishu_codex_bot.config import AppConfig
from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger
from feishu_codex_bot.models.actions import (
    CodexInputItem,
    CodexLocalImageInput,
    CodexTextInput,
    ThreadResumeOptions,
    ThreadStartOptions,
    TurnStartOptions,
)
from feishu_codex_bot.models.inbound import (
    BotAddedEvent,
    FileContent,
    ImageContent,
    InboundContentPart,
    InboundMessage,
    MentionRef,
    TextContent,
)
from feishu_codex_bot.models.session import (
    ConversationDispatchResult,
    GroupSessionBootstrapResult,
    PreparedConversationInput,
    SUPPORTED_SLASH_COMMANDS,
    UNSUPPORTED_SLASH_MESSAGE,
)
from feishu_codex_bot.persistence.dedupe_repo import DedupeRepository
from feishu_codex_bot.persistence.session_repo import SessionRecord, SessionRepository
from feishu_codex_bot.services.media_service import MediaService
from feishu_codex_bot.services.security_service import SecurityService, UnauthorizedMessage
from feishu_codex_bot.workers.session_executor import SessionExecutor


_DM_SESSION_TTL = timedelta(hours=1)
_LEADING_MENTION_PATTERN = re.compile(r"^@\S+")


class ConversationService:
    """协调白名单、去重、session 和 Codex turn 派发。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        codex_client: CodexClient,
        feishu_adapter: FeishuAdapter,
        media_service: MediaService,
        session_repository: SessionRepository,
        dedupe_repository: DedupeRepository,
        security_service: SecurityService,
        session_executor: SessionExecutor,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._config = config
        self._codex_client = codex_client
        self._feishu_adapter = feishu_adapter
        self._media_service = media_service
        self._session_repository = session_repository
        self._dedupe_repository = dedupe_repository
        self._security_service = security_service
        self._session_executor = session_executor
        self._logger = logger or get_logger(__name__, bot_app_id=config.feishu.app_id)

    async def handle_message(self, message: InboundMessage) -> ConversationDispatchResult:
        sender_id = self._resolve_sender_id(message)
        log = self._logger.bind(
            event="conversation.message.received",
            bot_app_id=self._config.feishu.app_id,
            feishu_event_id=message.event_id,
            feishu_message_id=message.message_id,
            chat_id=message.chat_id,
            chat_type=message.chat_type,
            sender_id=sender_id,
        )
        log.info("Handling inbound message")

        if message.is_group_message and not self._is_group_addressed(message):
            log.bind(event="conversation.message.ignored", reason="group_not_addressed").info(
                "Ignoring group message that is not addressed to the bot"
            )
            return ConversationDispatchResult(status="ignored_not_addressed")

        security_decision = self._security_service.evaluate_user(sender_id)
        if not security_decision.allowed:
            await self._handle_unauthorized_message(message=message, sender_id=sender_id, log=log)
            return ConversationDispatchResult(status="blocked_unauthorized")

        session_scope_key = self._build_session_scope_key(message=message, sender_id=sender_id)
        accepted = self._dedupe_repository.try_mark_accepted(
            bot_app_id=self._config.feishu.app_id,
            feishu_event_id=message.event_id,
            feishu_message_id=message.message_id,
            chat_id=message.chat_id,
            sender_open_id=message.sender_open_id,
            session_scope_key=session_scope_key,
        )
        if not accepted:
            log.bind(
                event="conversation.message.duplicate",
                session_scope_key=session_scope_key,
            ).info("Skipping duplicated inbound message")
            return ConversationDispatchResult(
                status="ignored_duplicate",
                session_scope_key=session_scope_key,
            )

        try:
            return await self._session_executor.run(
                session_scope_key,
                lambda: self._dispatch_message(
                    message=message,
                    session_scope_key=session_scope_key,
                    sender_id=sender_id,
                ),
            )
        except Exception:
            self._dedupe_repository.update_status(
                bot_app_id=self._config.feishu.app_id,
                feishu_message_id=message.message_id,
                status="dispatch_failed",
                session_scope_key=session_scope_key,
            )
            log.bind(
                event="conversation.message.failed",
                session_scope_key=session_scope_key,
            ).exception("Conversation dispatch failed")
            raise

    async def handle_bot_added(self, event: BotAddedEvent) -> GroupSessionBootstrapResult:
        session_scope_key = self._build_group_scope_key(event.chat_id)
        log = self._logger.bind(
            event="conversation.group.bootstrap",
            session_scope_key=session_scope_key,
            chat_id=event.chat_id,
            feishu_event_id=event.event_id,
        )

        async def bootstrap() -> GroupSessionBootstrapResult:
            existing = self._session_repository.get_by_scope_key(session_scope_key)
            record = await self._create_session_record(
                scope_type="group",
                scope_key=session_scope_key,
                user_open_id=None,
                chat_id=event.chat_id,
                occurred_at=event.occurred_at,
                existing_session=existing,
                reason="bot_added",
                log=log,
            )
            return GroupSessionBootstrapResult(
                session_scope_key=session_scope_key,
                thread_id=record.thread_id,
                thread_generation=record.thread_generation,
            )

        return await self._session_executor.run(session_scope_key, bootstrap)

    async def _dispatch_message(
        self,
        *,
        message: InboundMessage,
        session_scope_key: str,
        sender_id: str,
    ) -> ConversationDispatchResult:
        log = self._logger.bind(
            event="conversation.dispatch",
            session_scope_key=session_scope_key,
            feishu_message_id=message.message_id,
            chat_id=message.chat_id,
            sender_id=sender_id,
        )
        prepared = self._prepare_conversation_input(
            message=message,
            session_scope_key=session_scope_key,
            sender_id=sender_id,
            log=log,
        )
        if not prepared.input_items:
            self._dedupe_repository.update_status(
                bot_app_id=self._config.feishu.app_id,
                feishu_message_id=message.message_id,
                status="ignored_empty_input",
                session_scope_key=session_scope_key,
            )
            log.bind(event="conversation.input.ignored", reason="empty_input").info(
                "No text or image content available for Codex"
            )
            return ConversationDispatchResult(
                status="ignored_empty_input",
                session_scope_key=session_scope_key,
                is_slash_command=prepared.is_slash_command,
                slash_command=prepared.slash_command,
            )

        if prepared.is_slash_command and prepared.slash_command not in SUPPORTED_SLASH_COMMANDS:
            self._feishu_adapter.reply_text(
                message_id=message.message_id,
                text=UNSUPPORTED_SLASH_MESSAGE,
            )
            self._dedupe_repository.update_status(
                bot_app_id=self._config.feishu.app_id,
                feishu_message_id=message.message_id,
                status="ignored_unsupported_slash",
                session_scope_key=session_scope_key,
            )
            log.bind(
                event="conversation.slash.unsupported",
                slash_command=prepared.slash_command,
            ).info("Rejected unsupported slash command")
            return ConversationDispatchResult(
                status="rejected_unsupported_slash",
                session_scope_key=session_scope_key,
                is_slash_command=True,
                slash_command=prepared.slash_command,
                reply_text=UNSUPPORTED_SLASH_MESSAGE,
            )

        session = await self._resolve_session_for_dispatch(
            message=message,
            prepared=prepared,
            sender_id=sender_id,
            log=log,
        )
        turn = await self._codex_client.start_turn(
            TurnStartOptions(
                thread_id=session.thread_id,
                input_items=prepared.input_items,
                cwd=self._config.storage.base_dir,
            )
        )
        touched_session = self._session_repository.touch_session(
            session.scope_key,
            last_message_at=self._to_isoformat(message.created_at),
            expires_at=self._expires_at_for_message(message, message.created_at),
        )
        effective_session = touched_session or session
        self._dedupe_repository.update_status(
            bot_app_id=self._config.feishu.app_id,
            feishu_message_id=message.message_id,
            status="turn_started",
            turn_id=turn.id,
            session_scope_key=session.scope_key,
        )
        log.bind(
            event="conversation.turn.started",
            thread_id=effective_session.thread_id,
            thread_generation=effective_session.thread_generation,
            turn_id=turn.id,
            is_slash_command=prepared.is_slash_command,
            slash_command=prepared.slash_command,
        ).info("Submitted message to Codex")
        return ConversationDispatchResult(
            status="submitted",
            session_scope_key=effective_session.scope_key,
            thread_id=effective_session.thread_id,
            turn_id=turn.id,
            thread_generation=effective_session.thread_generation,
            is_slash_command=prepared.is_slash_command,
            slash_command=prepared.slash_command,
        )

    def _prepare_conversation_input(
        self,
        *,
        message: InboundMessage,
        session_scope_key: str,
        sender_id: str,
        log: ContextLoggerAdapter,
    ) -> PreparedConversationInput:
        normalized_parts = self._normalize_parts_for_message(message)
        normalized_text = "".join(
            part.text for part in normalized_parts if isinstance(part, TextContent)
        ).strip()
        slash_command = self._extract_slash_command(normalized_text)
        input_items: list[CodexInputItem] = []

        for part in normalized_parts:
            if isinstance(part, TextContent):
                if part.text:
                    input_items.append(CodexTextInput(text=part.text))
                continue
            if isinstance(part, ImageContent):
                media = self._media_service.download_image(
                    part.image_key,
                    source_message_id=message.message_id,
                )
                input_items.append(CodexLocalImageInput(path=media.local_path))
                continue
            if isinstance(part, FileContent):
                log.bind(
                    event="conversation.input.file_ignored",
                    file_key=part.file_key,
                    file_name=part.file_name,
                ).warning("Inbound file content is not supported for Codex input")

        prepared = PreparedConversationInput(
            session_scope_key=session_scope_key,
            scope_type=message.scope_type,
            sender_id=sender_id,
            normalized_text=normalized_text,
            input_items=tuple(input_items),
            is_slash_command=slash_command is not None,
            slash_command=slash_command,
            should_rotate_thread=slash_command == "/clear",
        )
        log.bind(
            event="conversation.input.prepared",
            normalized_text=prepared.normalized_text,
            input_item_types=[item.to_payload()["type"] for item in prepared.input_items],
            is_slash_command=prepared.is_slash_command,
            slash_command=prepared.slash_command,
        ).info("Prepared Codex input items")
        return prepared

    async def _resolve_session_for_dispatch(
        self,
        *,
        message: InboundMessage,
        prepared: PreparedConversationInput,
        sender_id: str,
        log: ContextLoggerAdapter,
    ) -> SessionRecord:
        existing = self._session_repository.get_by_scope_key(prepared.session_scope_key)
        reference_time = message.created_at
        if prepared.should_rotate_thread:
            return await self._create_session_record(
                scope_type=message.scope_type,
                scope_key=prepared.session_scope_key,
                user_open_id=sender_id if message.is_direct_message else None,
                chat_id=message.chat_id,
                occurred_at=reference_time,
                existing_session=existing,
                reason="slash_clear",
                log=log,
            )

        if existing is None:
            return await self._create_session_record(
                scope_type=message.scope_type,
                scope_key=prepared.session_scope_key,
                user_open_id=sender_id if message.is_direct_message else None,
                chat_id=message.chat_id,
                occurred_at=reference_time,
                existing_session=None,
                reason="session_missing",
                log=log,
            )

        if existing.status != "active":
            return await self._create_session_record(
                scope_type=message.scope_type,
                scope_key=prepared.session_scope_key,
                user_open_id=sender_id if message.is_direct_message else None,
                chat_id=message.chat_id,
                occurred_at=reference_time,
                existing_session=existing,
                reason="session_archived",
                log=log,
            )

        if message.is_direct_message and existing.is_expired(reference_time):
            return await self._create_session_record(
                scope_type=message.scope_type,
                scope_key=prepared.session_scope_key,
                user_open_id=sender_id,
                chat_id=message.chat_id,
                occurred_at=reference_time,
                existing_session=existing,
                reason="session_expired",
                log=log,
            )

        try:
            resumed = await self._codex_client.resume_thread(
                ThreadResumeOptions(
                    thread_id=existing.thread_id,
                    cwd=self._config.storage.base_dir,
                )
            )
        except Exception as exc:
            log.bind(
                event="session.thread.resume_failed",
                thread_id=existing.thread_id,
                error=str(exc),
            ).warning("Failed to resume existing thread, creating a new one")
            return await self._create_session_record(
                scope_type=message.scope_type,
                scope_key=prepared.session_scope_key,
                user_open_id=sender_id if message.is_direct_message else None,
                chat_id=message.chat_id,
                occurred_at=reference_time,
                existing_session=existing,
                reason="resume_failed",
                log=log,
            )

        if resumed.id != existing.thread_id:
            updated = self._session_repository.upsert_session(
                scope_type=existing.scope_type,
                scope_key=existing.scope_key,
                bot_app_id=existing.bot_app_id,
                thread_id=resumed.id,
                user_open_id=existing.user_open_id,
                chat_id=existing.chat_id,
                thread_generation=existing.thread_generation,
                last_message_at=existing.last_message_at,
                expires_at=existing.expires_at,
                status=existing.status,
            )
            log.bind(
                event="session.loaded",
                thread_id=updated.thread_id,
                thread_generation=updated.thread_generation,
            ).info("Loaded existing session after thread resume")
            return updated

        log.bind(
            event="session.loaded",
            thread_id=existing.thread_id,
            thread_generation=existing.thread_generation,
        ).info("Loaded existing session")
        return existing

    async def _create_session_record(
        self,
        *,
        scope_type: str,
        scope_key: str,
        user_open_id: str | None,
        chat_id: str | None,
        occurred_at: datetime,
        existing_session: SessionRecord | None,
        reason: str,
        log: ContextLoggerAdapter,
    ) -> SessionRecord:
        thread = await self._codex_client.start_thread(
            ThreadStartOptions(
                cwd=self._config.storage.base_dir,
            )
        )
        thread_generation = (existing_session.thread_generation if existing_session else 0) + 1
        record = self._session_repository.upsert_session(
            scope_type=scope_type,
            scope_key=scope_key,
            bot_app_id=self._config.feishu.app_id,
            thread_id=thread.id,
            user_open_id=user_open_id,
            chat_id=chat_id,
            thread_generation=thread_generation,
            last_message_at=self._to_isoformat(occurred_at),
            expires_at=self._expires_at_for_scope(scope_type, occurred_at),
            status="active",
        )
        log.bind(
            event="session.thread.created" if existing_session is None else "session.thread.rotated",
            scope_key=scope_key,
            thread_id=record.thread_id,
            thread_generation=record.thread_generation,
            reason=reason,
        ).info("Created or rotated session thread")
        return record

    async def _handle_unauthorized_message(
        self,
        *,
        message: InboundMessage,
        sender_id: str,
        log: ContextLoggerAdapter,
    ) -> None:
        alert = self._security_service.record_unauthorized_attempt(
            UnauthorizedMessage(
                bot_app_id=self._config.feishu.app_id,
                sender_user_id=sender_id,
                sender_open_id=message.sender_open_id,
                chat_id=message.chat_id,
                chat_type=message.chat_type,
                feishu_message_id=message.message_id,
                feishu_event_id=message.event_id,
            )
        )
        alert_text = "\n".join(
            (
                "检测到非白名单用户尝试访问 Codex 机器人",
                f"时间: {self._to_isoformat(message.created_at)}",
                f"发送者: {sender_id}",
                f"会话类型: {message.scope_type}",
                f"chat_id: {message.chat_id}",
                f"message_id: {message.message_id}",
            )
        )
        try:
            owner_alert_message_id = self._feishu_adapter.send_owner_alert(
                owner_open_id=self._config.security.owner_user_id,
                text=alert_text,
            )
        except Exception as exc:
            self._security_service.mark_alert_failed(alert.id)
            log.bind(
                event="conversation.security.alert_failed",
                error=str(exc),
                sender_id=sender_id,
            ).exception("Failed to deliver owner alert for unauthorized message")
            return

        self._security_service.mark_alert_sent(
            alert.id,
            owner_alert_message_id=owner_alert_message_id,
        )
        log.bind(
            event="conversation.security.blocked",
            sender_id=sender_id,
            owner_alert_message_id=owner_alert_message_id,
        ).warning("Blocked unauthorized message and sent owner alert")

    def _normalize_parts_for_message(
        self,
        message: InboundMessage,
    ) -> tuple[InboundContentPart, ...]:
        if not message.is_group_message:
            return message.parts

        normalized: list[InboundContentPart] = []
        strip_leading_mentions = True
        for part in message.parts:
            if strip_leading_mentions and isinstance(part, TextContent):
                stripped = self._strip_leading_mentions(part.text, message.mentions)
                if stripped:
                    normalized.append(TextContent(stripped))
                    strip_leading_mentions = False
                continue

            normalized.append(part)
            strip_leading_mentions = False
        return tuple(normalized)

    def _extract_slash_command(self, text: str) -> str | None:
        candidate = text.strip()
        if not candidate.startswith("/"):
            return None
        command = candidate.split(None, 1)[0].lower()
        return command

    def _strip_leading_mentions(
        self,
        text: str,
        mentions: tuple[MentionRef, ...],
    ) -> str:
        remaining = text.lstrip()
        if not remaining:
            return ""

        mention_tokens = [
            f"@{name.strip()}"
            for name in (mention.name for mention in mentions if mention.name)
            if name.strip()
        ]
        mention_tokens.sort(key=len, reverse=True)

        changed = False
        while remaining.startswith("@"):
            matched = False
            for token in mention_tokens:
                if not remaining.startswith(token):
                    continue
                suffix = remaining[len(token) : len(token) + 1]
                if suffix and not suffix.isspace():
                    continue
                remaining = remaining[len(token) :].lstrip()
                matched = True
                changed = True
                break
            if matched:
                continue

            generic_match = _LEADING_MENTION_PATTERN.match(remaining)
            if generic_match is None:
                break
            remaining = remaining[generic_match.end() :].lstrip()
            changed = True

        return remaining if changed else text

    def _is_group_addressed(self, message: InboundMessage) -> bool:
        return bool(message.mentions)

    def _resolve_sender_id(self, message: InboundMessage) -> str:
        return (
            message.sender_open_id
            or message.sender_user_id
            or message.sender_union_id
            or "unknown_sender"
        )

    def _build_session_scope_key(self, *, message: InboundMessage, sender_id: str) -> str:
        if message.is_direct_message:
            return f"{self._config.feishu.app_id}:{sender_id}"
        return self._build_group_scope_key(message.chat_id)

    def _build_group_scope_key(self, chat_id: str) -> str:
        return f"{self._config.feishu.app_id}:{chat_id}"

    def _expires_at_for_message(
        self,
        message: InboundMessage,
        reference_time: datetime,
    ) -> str | None:
        return self._expires_at_for_scope(message.scope_type, reference_time)

    def _expires_at_for_scope(self, scope_type: str, reference_time: datetime) -> str | None:
        if scope_type == "group":
            return None
        return self._to_isoformat(reference_time + _DM_SESSION_TTL)

    def _to_isoformat(self, value: datetime) -> str:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
