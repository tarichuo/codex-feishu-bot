from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from feishu_codex_bot.services.media_service import MediaService


class _FakeRawResponse:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


class _FakeBinaryResponse:
    def __init__(
        self,
        *,
        file_name: str,
        file_bytes: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = 0
        self.msg = "ok"
        self.file_name = file_name
        self.file = BytesIO(file_bytes)
        self.raw = _FakeRawResponse(headers)

    def success(self) -> bool:
        return True

    def get_log_id(self) -> str | None:
        return None


class _FakeJsonResponse:
    def __init__(self, data: object) -> None:
        self.code = 0
        self.msg = "ok"
        self.data = data
        self.raw = _FakeRawResponse({"Content-Type": "application/json"})

    def success(self) -> bool:
        return True

    def get_log_id(self) -> str | None:
        return None


class _FakeMessageApi:
    def __init__(self, message_content: str) -> None:
        self.message_content = message_content
        self.requests: list[object] = []

    def get(self, request: object) -> _FakeJsonResponse:
        self.requests.append(request)
        item = SimpleNamespace(body=SimpleNamespace(content=self.message_content))
        return _FakeJsonResponse(SimpleNamespace(items=[item]))


class _FakeMessageResourceApi:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def get(self, request: object) -> _FakeBinaryResponse:
        self.requests.append(request)
        return _FakeBinaryResponse(
            file_name="example.png",
            file_bytes=b"png-bytes",
            headers={"Content-Type": "image/png"},
        )


class _FakeClient:
    def __init__(self, message_content: str) -> None:
        self.im = SimpleNamespace(
            v1=SimpleNamespace(
                message=_FakeMessageApi(message_content),
                message_resource=_FakeMessageResourceApi(),
                image=SimpleNamespace(get=lambda _request: None),
                file=SimpleNamespace(get=lambda _request: None),
            )
        )


def test_download_image_uses_message_resource_api_with_resolved_file_key(tmp_path: Path) -> None:
    client = _FakeClient(
        '{"title":"","content":[[{"tag":"img","image_key":"img_123","file_key":"file_456"}],[{"tag":"text","text":"hello"}]]}'
    )
    service = MediaService(client=client, media_dir=tmp_path)

    media = service.download_image("img_123", source_message_id="om_789")

    assert media.source_message_id == "om_789"
    assert media.source_key == "img_123"
    assert media.local_path.exists()
    assert client.im.v1.message.requests[0].message_id == "om_789"
    resource_request = client.im.v1.message_resource.requests[0]
    assert resource_request.message_id == "om_789"
    assert resource_request.file_key == "file_456"
    assert resource_request.type == "image"


def test_download_image_falls_back_to_image_key_when_message_content_has_no_file_key(tmp_path: Path) -> None:
    client = _FakeClient(
        '{"title":"","content":[[{"tag":"img","image_key":"img_123"}],[{"tag":"text","text":"hello"}]]}'
    )
    service = MediaService(client=client, media_dir=tmp_path)

    media = service.download_image("img_123", source_message_id="om_789")

    assert media.source_message_id == "om_789"
    resource_request = client.im.v1.message_resource.requests[0]
    assert resource_request.message_id == "om_789"
    assert resource_request.file_key == "img_123"
    assert resource_request.type == "image"
