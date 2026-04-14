"""Codex 协议消息到内部输出事件的分类器。"""

from __future__ import annotations

from pathlib import Path

from feishu_codex_bot.models.actions import (
    CodexApprovalRequestEvent,
    CodexCommandEvent,
    CodexFileReferenceEvent,
    CodexImageOutputEvent,
    CodexNotification,
    CodexOutputEvent,
    CodexServerRequest,
    CodexTextDeltaEvent,
    CodexTextMessageEvent,
    CodexTurnErrorEvent,
    CodexTurnLifecycleEvent,
    CodexUnknownEvent,
    CodexUserInputRequestEvent,
)


_TEXT_DELTA_METHOD_CHANNELS = {
    "item/agentMessage/delta": "agentMessage",
    "item/plan/delta": "plan",
    "item/reasoning/summaryTextDelta": "reasoningSummary",
    "item/reasoning/textDelta": "reasoning",
}


class CodexOutputClassifier:
    """Map Codex protocol envelopes to internal output events."""

    def classify(
        self,
        envelope: CodexNotification | CodexServerRequest,
    ) -> tuple[CodexOutputEvent, ...]:
        if isinstance(envelope, CodexServerRequest):
            return self._classify_server_request(envelope)
        return self._classify_notification(envelope)

    def _classify_server_request(
        self,
        request: CodexServerRequest,
    ) -> tuple[CodexOutputEvent, ...]:
        if request.method == "item/tool/requestUserInput":
            questions = request.params.get("questions")
            return (
                CodexUserInputRequestEvent(
                    request_id=request.id,
                    thread_id=request.thread_id,
                    turn_id=request.turn_id,
                    item_id=request.item_id,
                    questions=tuple(questions) if isinstance(questions, list) else (),
                    params=request.params,
                ),
            )

        approval_type = {
            "item/commandExecution/requestApproval": "commandExecution",
            "item/fileChange/requestApproval": "fileChange",
            "item/permissions/requestApproval": "permissions",
        }.get(request.method)
        if approval_type is not None:
            return (
                CodexApprovalRequestEvent(
                    request_id=request.id,
                    approval_type=approval_type,
                    thread_id=request.thread_id,
                    turn_id=request.turn_id,
                    item_id=request.item_id,
                    params=request.params,
                ),
            )

        return (
            CodexUnknownEvent(
                source=request.method,
                thread_id=request.thread_id,
                turn_id=request.turn_id,
                item_id=request.item_id,
                payload=request.params,
            ),
        )

    def _classify_notification(
        self,
        notification: CodexNotification,
    ) -> tuple[CodexOutputEvent, ...]:
        if notification.method in _TEXT_DELTA_METHOD_CHANNELS:
            delta = notification.params.get("delta")
            if isinstance(delta, str):
                return (
                    CodexTextDeltaEvent(
                        channel=_TEXT_DELTA_METHOD_CHANNELS[notification.method],
                        text=delta,
                        thread_id=notification.thread_id,
                        turn_id=notification.turn_id,
                        item_id=notification.item_id,
                    ),
                )

        if notification.method in {"command/exec/outputDelta", "item/commandExecution/outputDelta"}:
            delta = notification.params.get("delta")
            return (
                CodexCommandEvent(
                    command=None,
                    cwd=None,
                    status="inProgress",
                    thread_id=notification.thread_id,
                    turn_id=notification.turn_id,
                    item_id=notification.item_id,
                    delta=delta if isinstance(delta, str) else None,
                ),
            )

        if notification.method == "item/fileChange/outputDelta":
            delta = notification.params.get("delta")
            return (
                CodexFileReferenceEvent(
                    path="",
                    file_name="",
                    source="fileChangeDelta",
                    thread_id=notification.thread_id,
                    turn_id=notification.turn_id,
                    item_id=notification.item_id,
                    diff=delta if isinstance(delta, str) else None,
                ),
            )

        if notification.method == "error":
            error = notification.params.get("error")
            will_retry = notification.params.get("willRetry")
            if isinstance(error, dict):
                return (
                    CodexTurnErrorEvent(
                        error=dict(error),
                        thread_id=notification.thread_id,
                        turn_id=notification.turn_id,
                        item_id=notification.item_id,
                        will_retry=will_retry if isinstance(will_retry, bool) else None,
                    ),
                )

        if notification.method == "turn/started":
            turn = notification.params.get("turn")
            if isinstance(turn, dict):
                return (
                    CodexTurnLifecycleEvent(
                        phase="started",
                        thread_id=notification.thread_id,
                        turn_id=turn.get("id") if isinstance(turn.get("id"), str) else notification.turn_id,
                        status=turn.get("status") if isinstance(turn.get("status"), str) else None,
                    ),
                )

        if notification.method == "turn/completed":
            turn = notification.params.get("turn")
            if isinstance(turn, dict):
                error = turn.get("error")
                return (
                    CodexTurnLifecycleEvent(
                        phase="completed",
                        thread_id=notification.thread_id,
                        turn_id=turn.get("id") if isinstance(turn.get("id"), str) else notification.turn_id,
                        status=turn.get("status") if isinstance(turn.get("status"), str) else None,
                        error=dict(error) if isinstance(error, dict) else None,
                    ),
                )

        if notification.method == "item/completed":
            item = notification.params.get("item")
            if isinstance(item, dict):
                return self._classify_completed_item(notification, item)

        return (
            CodexUnknownEvent(
                source=notification.method,
                thread_id=notification.thread_id,
                turn_id=notification.turn_id,
                item_id=notification.item_id,
                payload=notification.params,
            ),
        )

    def _classify_completed_item(
        self,
        notification: CodexNotification,
        item: dict[str, object],
    ) -> tuple[CodexOutputEvent, ...]:
        item_type = item.get("type")
        item_id = item.get("id") if isinstance(item.get("id"), str) else notification.item_id

        if item_type == "agentMessage":
            text = item.get("text")
            return (
                CodexTextMessageEvent(
                    channel="agentMessage",
                    text=text if isinstance(text, str) else "",
                    thread_id=notification.thread_id,
                    turn_id=notification.turn_id,
                    item_id=item_id,
                ),
            )

        if item_type == "plan":
            text = item.get("text")
            return (
                CodexTextMessageEvent(
                    channel="plan",
                    text=text if isinstance(text, str) else "",
                    thread_id=notification.thread_id,
                    turn_id=notification.turn_id,
                    item_id=item_id,
                ),
            )

        if item_type == "reasoning":
            parts: list[str] = []
            summary = item.get("summary")
            if isinstance(summary, list):
                parts.extend(part for part in summary if isinstance(part, str))
            content = item.get("content")
            if isinstance(content, list):
                parts.extend(part for part in content if isinstance(part, str))
            return (
                CodexTextMessageEvent(
                    channel="reasoning",
                    text="\n".join(parts),
                    thread_id=notification.thread_id,
                    turn_id=notification.turn_id,
                    item_id=item_id,
                ),
            )

        if item_type == "commandExecution":
            exit_code = item.get("exitCode")
            return (
                CodexCommandEvent(
                    command=item.get("command") if isinstance(item.get("command"), str) else None,
                    cwd=item.get("cwd") if isinstance(item.get("cwd"), str) else None,
                    status=item.get("status") if isinstance(item.get("status"), str) else None,
                    thread_id=notification.thread_id,
                    turn_id=notification.turn_id,
                    item_id=item_id,
                    aggregated_output=item.get("aggregatedOutput")
                    if isinstance(item.get("aggregatedOutput"), str)
                    else None,
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                ),
            )

        if item_type == "fileChange":
            changes = item.get("changes")
            if isinstance(changes, list) and changes:
                events: list[CodexOutputEvent] = []
                for change in changes:
                    if not isinstance(change, dict):
                        continue
                    path = change.get("path")
                    if not isinstance(path, str):
                        continue
                    events.append(
                        CodexFileReferenceEvent(
                            path=path,
                            file_name=Path(path).name,
                            source="fileChange",
                            thread_id=notification.thread_id,
                            turn_id=notification.turn_id,
                            item_id=item_id,
                            diff=change.get("diff") if isinstance(change.get("diff"), str) else None,
                            status=item.get("status") if isinstance(item.get("status"), str) else None,
                        )
                    )
                if events:
                    return tuple(events)

        if item_type == "dynamicToolCall":
            content_items = item.get("contentItems")
            if isinstance(content_items, list) and content_items:
                events: list[CodexOutputEvent] = []
                for content_item in content_items:
                    if not isinstance(content_item, dict):
                        continue
                    content_type = content_item.get("type")
                    if content_type == "inputText":
                        text = content_item.get("text")
                        if isinstance(text, str):
                            events.append(
                                CodexTextMessageEvent(
                                    channel="dynamicToolCall",
                                    text=text,
                                    thread_id=notification.thread_id,
                                    turn_id=notification.turn_id,
                                    item_id=item_id,
                                )
                            )
                    elif content_type == "inputImage":
                        image_url = content_item.get("imageUrl")
                        if isinstance(image_url, str):
                            events.append(
                                CodexImageOutputEvent(
                                    reference=image_url,
                                    reference_type="remote_url",
                                    thread_id=notification.thread_id,
                                    turn_id=notification.turn_id,
                                    item_id=item_id,
                                    source="dynamicToolCall",
                                )
                            )
                if events:
                    return tuple(events)

        if item_type == "imageView":
            path = item.get("path")
            if isinstance(path, str):
                return (
                    CodexImageOutputEvent(
                        reference=path,
                        reference_type="local_path",
                        thread_id=notification.thread_id,
                        turn_id=notification.turn_id,
                        item_id=item_id,
                        source="imageView",
                    ),
                )

        if item_type == "imageGeneration":
            result = item.get("result")
            if isinstance(result, str):
                revised_prompt = item.get("revisedPrompt")
                return (
                    CodexImageOutputEvent(
                        reference=result,
                        reference_type="generated_result",
                        thread_id=notification.thread_id,
                        turn_id=notification.turn_id,
                        item_id=item_id,
                        source="imageGeneration",
                        revised_prompt=revised_prompt if isinstance(revised_prompt, str) else None,
                    ),
                )

        return (
            CodexUnknownEvent(
                source=f"item/completed:{item_type}",
                thread_id=notification.thread_id,
                turn_id=notification.turn_id,
                item_id=item_id,
                payload=item,
            ),
        )
