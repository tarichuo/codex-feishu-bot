from __future__ import annotations

from feishu_codex_bot.adapters.codex_output_classifier import CodexOutputClassifier
from feishu_codex_bot.models.actions import CodexCommandEvent, CodexNotification, CodexTurnErrorEvent


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


def test_classifier_maps_started_command_execution_to_command_event() -> None:
    classifier = CodexOutputClassifier()

    events = classifier.classify(
        CodexNotification.from_payload(
            "item/started",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {
                    "id": "item-cmd-1",
                    "type": "commandExecution",
                    "status": "in_progress",
                    "cwd": "/workspace",
                    "command": "fallback command",
                    "commandActions": [
                        {"type": "command", "command": "rg -n \"foo\" src"},
                        {"type": "command", "command": "sed -n '1,20p' README.md"},
                    ],
                },
            },
        )
    )

    assert events == (
        CodexCommandEvent(
            command="fallback command",
            cwd="/workspace",
            status="in_progress",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="item-cmd-1",
            display_commands=(
                'rg -n "foo" src',
                "sed -n '1,20p' README.md",
            ),
        ),
    )
