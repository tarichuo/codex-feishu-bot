"""Bootstrap helpers for preparing runtime configuration and directories."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Mapping

from feishu_codex_bot.adapters.codex_client import CodexClient
from feishu_codex_bot.adapters.codex_output_classifier import CodexOutputClassifier
from feishu_codex_bot.adapters.feishu_adapter import FeishuAdapter
from feishu_codex_bot.config import AppConfig, load_config
from feishu_codex_bot.logging import ContextLoggerAdapter, configure_logging
from feishu_codex_bot.persistence.action_repo import PendingActionRepository
from feishu_codex_bot.persistence.dedupe_repo import DedupeRepository
from feishu_codex_bot.persistence.db import DatabaseManager
from feishu_codex_bot.persistence.reply_repo import ReplyRepository
from feishu_codex_bot.persistence.security_repo import SecurityAlertRepository
from feishu_codex_bot.persistence.session_repo import SessionRepository
from feishu_codex_bot.services.approval_service import ApprovalService
from feishu_codex_bot.services.conversation_service import ConversationService
from feishu_codex_bot.services.codex_dump_service import CodexDumpService
from feishu_codex_bot.services.media_service import MediaService
from feishu_codex_bot.services.reply_service import ReplyService
from feishu_codex_bot.services.security_service import SecurityService
from feishu_codex_bot.workers.session_executor import SessionExecutor


@dataclass(frozen=True, slots=True)
class BootstrapContext:
    """Prepared runtime context for the application."""

    config: AppConfig
    logger: ContextLoggerAdapter


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """Fully wired runtime dependencies for the application."""

    config: AppConfig
    logger: ContextLoggerAdapter
    db: DatabaseManager
    codex_client: CodexClient
    feishu_adapter: FeishuAdapter
    media_service: MediaService
    classifier: CodexOutputClassifier
    session_executor: SessionExecutor
    session_repository: SessionRepository
    dedupe_repository: DedupeRepository
    reply_repository: ReplyRepository
    action_repository: PendingActionRepository
    security_alert_repository: SecurityAlertRepository
    security_service: SecurityService
    conversation_service: ConversationService
    reply_service: ReplyService
    approval_service: ApprovalService
    codex_dump_service: CodexDumpService | None = None


def bootstrap(
    env: Mapping[str, str] | None = None,
    *,
    base_dir: Path | None = None,
) -> BootstrapContext:
    """Load configuration, validate it, and prepare required local directories."""
    config = load_config(env, base_dir=base_dir)

    config.storage.data_dir.mkdir(parents=True, exist_ok=True)
    config.storage.media_dir.mkdir(parents=True, exist_ok=True)
    config.storage.logs_dir.mkdir(parents=True, exist_ok=True)
    config.storage.sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    logger = configure_logging(config.logging, config.storage.logs_dir).bind(
        event="bootstrap.initialized",
        bot_app_id=config.feishu.app_id,
        codex_server_url=config.codex.server_url,
        data_dir=config.storage.data_dir,
        sqlite_path=config.storage.sqlite_path,
        logs_dir=config.storage.logs_dir,
    )
    logger.info("Application bootstrap complete")
    logging.getLogger(__name__).debug("Bootstrap logger configured")

    return BootstrapContext(config=config, logger=logger)


def bootstrap_runtime(
    env: Mapping[str, str] | None = None,
    *,
    base_dir: Path | None = None,
    enable_dump: bool = False,
) -> RuntimeContext:
    """Create all runtime dependencies needed to run the application."""
    bootstrap_context = bootstrap(env, base_dir=base_dir)
    config = bootstrap_context.config
    root_logger = bootstrap_context.logger

    db = DatabaseManager(config.storage.sqlite_path)
    db.initialize()

    session_repository = SessionRepository(db)
    dedupe_repository = DedupeRepository(db)
    reply_repository = ReplyRepository(db)
    action_repository = PendingActionRepository(db)
    security_alert_repository = SecurityAlertRepository(db)

    session_executor = SessionExecutor(
        logger=root_logger.bind(component="session_executor"),
    )
    classifier = CodexOutputClassifier()
    feishu_adapter = FeishuAdapter(
        config,
        logger=root_logger.bind(component="feishu_adapter"),
    )
    codex_dump_service: CodexDumpService | None = None
    if enable_dump:
        codex_dump_service = CodexDumpService(config.storage.data_dir / "dump.json")
        codex_dump_service.reset()
        root_logger.bind(
            event="bootstrap.codex_dump.enabled",
            dump_path=codex_dump_service.dump_path,
        ).info("Enabled Codex callback dump")
    codex_client = CodexClient(
        config,
        dump_service=codex_dump_service,
        logger=root_logger.bind(component="codex_client"),
    )
    media_service = MediaService(
        client=feishu_adapter.client,
        media_dir=config.storage.media_dir,
        logger=root_logger.bind(component="media_service"),
    )
    security_service = SecurityService(
        config.security,
        security_alert_repository,
    )
    conversation_service = ConversationService(
        config,
        codex_client=codex_client,
        feishu_adapter=feishu_adapter,
        media_service=media_service,
        session_repository=session_repository,
        dedupe_repository=dedupe_repository,
        security_service=security_service,
        session_executor=session_executor,
        logger=root_logger.bind(component="conversation_service"),
    )
    reply_service = ReplyService(
        config,
        feishu_adapter=feishu_adapter,
        reply_repository=reply_repository,
        session_executor=session_executor,
        classifier=classifier,
        logger=root_logger.bind(component="reply_service"),
    )
    approval_service = ApprovalService(
        config,
        codex_client=codex_client,
        feishu_adapter=feishu_adapter,
        action_repository=action_repository,
        classifier=classifier,
        logger=root_logger.bind(component="approval_service"),
    )

    root_logger.bind(
        event="bootstrap.runtime.initialized",
        sqlite_path=config.storage.sqlite_path,
        media_dir=config.storage.media_dir,
    ).info("Runtime dependencies initialized")

    return RuntimeContext(
        config=config,
        logger=root_logger,
        db=db,
        codex_client=codex_client,
        feishu_adapter=feishu_adapter,
        media_service=media_service,
        classifier=classifier,
        session_executor=session_executor,
        session_repository=session_repository,
        dedupe_repository=dedupe_repository,
        reply_repository=reply_repository,
        action_repository=action_repository,
        security_alert_repository=security_alert_repository,
        security_service=security_service,
        conversation_service=conversation_service,
        reply_service=reply_service,
        approval_service=approval_service,
        codex_dump_service=codex_dump_service,
    )
