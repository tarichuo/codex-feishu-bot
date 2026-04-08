"""Whitelist authorization and security alert orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from feishu_codex_bot.config import SecurityConfig
from feishu_codex_bot.persistence.security_repo import (
    SecurityAlertRecord,
    SecurityAlertRepository,
)


@dataclass(frozen=True, slots=True)
class SecurityDecision:
    """Decision returned by whitelist validation."""

    allowed: bool
    owner_user_id: str
    sender_user_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class UnauthorizedMessage:
    """Minimal message metadata needed to record a security alert."""

    bot_app_id: str
    sender_user_id: str
    sender_open_id: str | None
    chat_id: str | None
    chat_type: str
    feishu_message_id: str
    feishu_event_id: str | None


class SecurityService:
    """Evaluate whitelist access and persist unauthorized attempts."""

    def __init__(
        self,
        config: SecurityConfig,
        alert_repository: SecurityAlertRepository,
    ) -> None:
        self._config = config
        self._alert_repository = alert_repository

    def evaluate_user(self, sender_user_id: str) -> SecurityDecision:
        allowed = sender_user_id in self._config.allowed_user_ids
        if allowed:
            reason = "allowed_user"
        else:
            reason = "user_not_in_whitelist"
        return SecurityDecision(
            allowed=allowed,
            owner_user_id=self._config.owner_user_id,
            sender_user_id=sender_user_id,
            reason=reason,
        )

    def record_unauthorized_attempt(
        self,
        message: UnauthorizedMessage,
    ) -> SecurityAlertRecord:
        return self._alert_repository.create_alert(
            bot_app_id=message.bot_app_id,
            sender_open_id=message.sender_open_id,
            chat_id=message.chat_id,
            chat_type=message.chat_type,
            feishu_message_id=message.feishu_message_id,
            feishu_event_id=message.feishu_event_id,
            owner_open_id=self._config.owner_user_id,
            status="blocked",
        )

    def mark_alert_sent(
        self,
        alert_id: int,
        *,
        owner_alert_message_id: str,
    ) -> SecurityAlertRecord | None:
        return self._alert_repository.update_alert_result(
            alert_id,
            status="alert_sent",
            owner_alert_message_id=owner_alert_message_id,
        )

    def mark_alert_failed(self, alert_id: int) -> SecurityAlertRecord | None:
        return self._alert_repository.update_alert_result(
            alert_id,
            status="alert_failed",
        )
