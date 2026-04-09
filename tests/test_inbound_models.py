from __future__ import annotations

from datetime import timezone

from feishu_codex_bot.models.inbound import InboundMessage


def test_utc_from_millis_supports_microsecond_precision_timestamp() -> None:
    occurred_at = InboundMessage.utc_from_millis("1775699243519107")

    assert occurred_at.tzinfo == timezone.utc
    assert occurred_at.year == 2026
    assert occurred_at.month == 4
    assert occurred_at.day == 9
