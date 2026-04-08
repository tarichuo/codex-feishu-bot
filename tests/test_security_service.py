from __future__ import annotations

from dataclasses import dataclass

from feishu_codex_bot.config import SecurityConfig
from feishu_codex_bot.services.security_service import SecurityService, UnauthorizedMessage


@dataclass
class _FakeAlertRecord:
    id: int
    status: str
    owner_alert_message_id: str | None = None


class _FakeSecurityAlertRepository:
    def __init__(self) -> None:
        self.created = []
        self.updated = []

    def create_alert(self, **kwargs) -> _FakeAlertRecord:
        self.created.append(kwargs)
        return _FakeAlertRecord(id=len(self.created), status=kwargs["status"])

    def update_alert_result(
        self,
        alert_id: int,
        *,
        status: str,
        owner_alert_message_id: str | None = None,
    ) -> _FakeAlertRecord:
        payload = {
            "alert_id": alert_id,
            "status": status,
            "owner_alert_message_id": owner_alert_message_id,
        }
        self.updated.append(payload)
        return _FakeAlertRecord(
            id=alert_id,
            status=status,
            owner_alert_message_id=owner_alert_message_id,
        )


def _build_service() -> tuple[SecurityService, _FakeSecurityAlertRepository]:
    repository = _FakeSecurityAlertRepository()
    service = SecurityService(
        SecurityConfig(
            owner_user_id="ou_owner",
            allowed_user_ids=frozenset({"ou_owner", "ou_allowed"}),
        ),
        repository,
    )
    return service, repository


def test_allowed_user_passes_whitelist() -> None:
    service, _ = _build_service()

    decision = service.evaluate_user("ou_allowed")

    assert decision.allowed is True
    assert decision.reason == "allowed_user"
    assert decision.owner_user_id == "ou_owner"


def test_blocked_user_is_reported() -> None:
    service, _ = _build_service()

    decision = service.evaluate_user("ou_intruder")

    assert decision.allowed is False
    assert decision.reason == "user_not_in_whitelist"
    assert decision.sender_user_id == "ou_intruder"


def test_record_unauthorized_attempt_creates_alert() -> None:
    service, repository = _build_service()

    alert = service.record_unauthorized_attempt(
        UnauthorizedMessage(
            bot_app_id="cli_test_app",
            sender_user_id="ou_intruder",
            sender_open_id="ou_intruder",
            chat_id="chat-1",
            chat_type="p2p",
            feishu_message_id="om_xxx",
            feishu_event_id="evt_xxx",
        )
    )

    assert alert.id == 1
    assert repository.created[0]["owner_open_id"] == "ou_owner"
    assert repository.created[0]["status"] == "blocked"


def test_mark_alert_sent_updates_repository() -> None:
    service, repository = _build_service()

    record = service.mark_alert_sent(7, owner_alert_message_id="om_alert")

    assert record is not None
    assert record.status == "alert_sent"
    assert repository.updated[-1] == {
        "alert_id": 7,
        "status": "alert_sent",
        "owner_alert_message_id": "om_alert",
    }


def test_mark_alert_failed_updates_repository() -> None:
    service, repository = _build_service()

    record = service.mark_alert_failed(9)

    assert record is not None
    assert record.status == "alert_failed"
    assert repository.updated[-1] == {
        "alert_id": 9,
        "status": "alert_failed",
        "owner_alert_message_id": None,
    }
