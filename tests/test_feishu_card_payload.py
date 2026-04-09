from __future__ import annotations

import sys
import types

_lark_stub = types.ModuleType("lark_oapi")
sys.modules.setdefault("lark_oapi", _lark_stub)
sys.modules.pop("feishu_codex_bot.adapters.feishu_adapter", None)

_log_level = type("LogLevel", (), {"INFO": "INFO", "DEBUG": "DEBUG", "WARNING": "WARNING", "ERROR": "ERROR"})

_core_enum = types.ModuleType("lark_oapi.core.enum")
_core_enum.LogLevel = _log_level
sys.modules.setdefault("lark_oapi.core.enum", _core_enum)

_core_model = types.ModuleType("lark_oapi.core.model")
_core_model.BaseResponse = object
sys.modules.setdefault("lark_oapi.core.model", _core_model)

for module_name, class_names in {
    "lark_oapi.api.cardkit.v1.model.content_card_element_request": ["ContentCardElementRequest"],
    "lark_oapi.api.cardkit.v1.model.content_card_element_request_body": ["ContentCardElementRequestBody"],
    "lark_oapi.api.cardkit.v1.model.create_card_request": ["CreateCardRequest"],
    "lark_oapi.api.cardkit.v1.model.create_card_request_body": ["CreateCardRequestBody"],
    "lark_oapi.api.cardkit.v1.model.card": ["Card"],
    "lark_oapi.api.cardkit.v1.model.settings_card_request": ["SettingsCardRequest"],
    "lark_oapi.api.cardkit.v1.model.settings_card_request_body": ["SettingsCardRequestBody"],
    "lark_oapi.api.cardkit.v1.model.update_card_request": ["UpdateCardRequest"],
    "lark_oapi.api.cardkit.v1.model.update_card_request_body": ["UpdateCardRequestBody"],
    "lark_oapi.api.im.v1.model.create_message_reaction_request": ["CreateMessageReactionRequest"],
    "lark_oapi.api.im.v1.model.create_message_reaction_request_body": ["CreateMessageReactionRequestBody"],
    "lark_oapi.api.im.v1.model.create_message_request": ["CreateMessageRequest"],
    "lark_oapi.api.im.v1.model.create_message_request_body": ["CreateMessageRequestBody"],
    "lark_oapi.api.im.v1.model.delete_message_reaction_request": ["DeleteMessageReactionRequest"],
    "lark_oapi.api.im.v1.model.emoji": ["Emoji"],
    "lark_oapi.api.im.v1.model.p2_im_chat_member_bot_added_v1": ["P2ImChatMemberBotAddedV1"],
    "lark_oapi.api.im.v1.model.p2_im_message_receive_v1": ["P2ImMessageReceiveV1"],
    "lark_oapi.api.im.v1.model.reply_message_request": ["ReplyMessageRequest"],
    "lark_oapi.api.im.v1.model.reply_message_request_body": ["ReplyMessageRequestBody"],
    "lark_oapi.api.im.v1.model.update_message_request": ["UpdateMessageRequest"],
    "lark_oapi.api.im.v1.model.update_message_request_body": ["UpdateMessageRequestBody"],
    "lark_oapi.event.callback.model.p2_card_action_trigger": [
        "CallBackCard",
        "CallBackToast",
        "P2CardActionTrigger",
        "P2CardActionTriggerResponse",
    ],
}.items():
    module = types.ModuleType(module_name)
    for class_name in class_names:
        setattr(module, class_name, type(class_name, (), {}))
    sys.modules.setdefault(module_name, module)

from feishu_codex_bot.adapters.feishu_adapter import FeishuAdapter


def test_streaming_card_status_uses_markdown_icon_structure() -> None:
    adapter = FeishuAdapter.__new__(FeishuAdapter)

    payload = adapter._build_streaming_card_payload(text="正文", status="completed")

    elements = payload["body"]["elements"]
    assert len(elements) == 2
    status_element = elements[1]
    assert status_element["tag"] == "markdown"
    assert status_element["element_id"] == "status"
    assert status_element["text_size"] == "notation"
    assert status_element["icon"] == {
        "tag": "standard_icon",
        "token": "robot_outlined",
        "color": "grey-500",
    }
    assert "<i token=" not in status_element["content"]
