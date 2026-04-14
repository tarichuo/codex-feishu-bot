from __future__ import annotations

from feishu_codex_bot.adapters.codex_output_classifier import CodexOutputClassifier
from feishu_codex_bot.models.actions import CodexNotification, CodexTurnErrorEvent


def test_classifier_maps_error_notification_to_turn_error_event() -> None:
    classifier = CodexOutputClassifier()

    events = classifier.classify(
        CodexNotification.from_payload(
            "error",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "willRetry": False,
                "error": {
                    "message": "The model server disconnected.",
                    "additionalDetails": "response stream closed",
                },
            },
        )
    )

    assert events == (
        CodexTurnErrorEvent(
            error={
                "message": "The model server disconnected.",
                "additionalDetails": "response stream closed",
            },
            thread_id="thread-1",
            turn_id="turn-1",
            item_id=None,
            will_retry=False,
        ),
    )
