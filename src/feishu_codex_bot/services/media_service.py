"""飞书媒体下载与上传能力。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import mimetypes
from pathlib import Path
import re

from lark_oapi import Client
from lark_oapi.api.im.v1.model.create_file_request import CreateFileRequest
from lark_oapi.api.im.v1.model.create_file_request_body import CreateFileRequestBody
from lark_oapi.api.im.v1.model.create_image_request import CreateImageRequest
from lark_oapi.api.im.v1.model.create_image_request_body import CreateImageRequestBody
from lark_oapi.api.im.v1.model.get_file_request import GetFileRequest
from lark_oapi.api.im.v1.model.get_image_request import GetImageRequest
from lark_oapi.core.model import BaseResponse

from feishu_codex_bot.logging import ContextLoggerAdapter, get_logger
from feishu_codex_bot.models.inbound import DownloadedMedia


class MediaServiceError(RuntimeError):
    """Raised when Feishu media transfer fails."""


def _sanitize_file_name(file_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name).strip("._")
    return cleaned or "media"


def _content_type_from_response(response: BaseResponse) -> str | None:
    if response.raw is None or response.raw.headers is None:
        return None
    return response.raw.headers.get("Content-Type") or response.raw.headers.get("content-type")


def _guess_suffix(file_name: str | None, content_type: str | None) -> str:
    if file_name:
        suffix = Path(file_name).suffix
        if suffix:
            return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return guessed
    return ".bin"


class MediaService:
    """Wrap Feishu image/file upload and local download workflows."""

    def __init__(
        self,
        *,
        client: Client,
        media_dir: Path,
        logger: ContextLoggerAdapter | None = None,
    ) -> None:
        self._client = client
        self._media_dir = media_dir.resolve()
        self._logger = logger or get_logger(__name__)
        (self._media_dir / "images").mkdir(parents=True, exist_ok=True)
        (self._media_dir / "files").mkdir(parents=True, exist_ok=True)

    def download_image(
        self,
        image_key: str,
        *,
        source_message_id: str | None = None,
    ) -> DownloadedMedia:
        response = self._client.im.v1.image.get(
            GetImageRequest.builder().image_key(image_key).build()
        )
        self._ensure_success(response, action="download_image", key=image_key)
        return self._persist_download(
            media_type="image",
            source_key=image_key,
            source_message_id=source_message_id,
            file_name=getattr(response, "file_name", None),
            file_bytes=response.file.getvalue(),
            mime_type=_content_type_from_response(response),
        )

    def download_file(
        self,
        file_key: str,
        *,
        source_message_id: str | None = None,
    ) -> DownloadedMedia:
        response = self._client.im.v1.file.get(
            GetFileRequest.builder().file_key(file_key).build()
        )
        self._ensure_success(response, action="download_file", key=file_key)
        return self._persist_download(
            media_type="file",
            source_key=file_key,
            source_message_id=source_message_id,
            file_name=getattr(response, "file_name", None),
            file_bytes=response.file.getvalue(),
            mime_type=_content_type_from_response(response),
        )

    def upload_image(self, local_path: Path) -> str:
        file_path = local_path.resolve()
        with file_path.open("rb") as handle:
            response = self._client.im.v1.image.create(
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(handle)
                    .build()
                )
                .build()
            )
        self._ensure_success(response, action="upload_image", key=str(file_path))
        image_key = getattr(response.data, "image_key", None)
        if not image_key:
            raise MediaServiceError("Feishu image upload succeeded but image_key is missing")
        self._logger.bind(
            event="feishu.media.uploaded",
            media_type="image",
            local_path=file_path,
            source_key=image_key,
        ).info("Feishu image uploaded")
        return image_key

    def upload_file(
        self,
        local_path: Path,
        *,
        file_name: str | None = None,
        file_type: str = "stream",
        duration: int | None = None,
    ) -> str:
        file_path = local_path.resolve()
        body_builder = (
            CreateFileRequestBody.builder()
            .file_type(file_type)
            .file_name(file_name or file_path.name)
        )
        if duration is not None:
            body_builder = body_builder.duration(duration)

        with file_path.open("rb") as handle:
            response = self._client.im.v1.file.create(
                CreateFileRequest.builder()
                .request_body(body_builder.file(handle).build())
                .build()
            )
        self._ensure_success(response, action="upload_file", key=str(file_path))
        file_key = getattr(response.data, "file_key", None)
        if not file_key:
            raise MediaServiceError("Feishu file upload succeeded but file_key is missing")
        self._logger.bind(
            event="feishu.media.uploaded",
            media_type="file",
            local_path=file_path,
            source_key=file_key,
        ).info("Feishu file uploaded")
        return file_key

    def _persist_download(
        self,
        *,
        media_type: str,
        source_key: str,
        source_message_id: str | None,
        file_name: str | None,
        file_bytes: bytes,
        mime_type: str | None,
    ) -> DownloadedMedia:
        downloaded_at = datetime.now(tz=timezone.utc)
        sha256 = hashlib.sha256(file_bytes).hexdigest()
        suffix = _guess_suffix(file_name, mime_type)
        final_name = f"{media_type}_{sha256[:16]}{suffix}"
        target_dir = self._media_dir / ("images" if media_type == "image" else "files")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / final_name
        target_path.write_bytes(file_bytes)

        resolved_name = file_name or f"{_sanitize_file_name(source_key)}{suffix}"
        media = DownloadedMedia(
            media_type=media_type,
            source_key=source_key,
            source_message_id=source_message_id,
            local_path=target_path,
            file_name=resolved_name,
            size_bytes=len(file_bytes),
            sha256=sha256,
            mime_type=mime_type,
            downloaded_at=downloaded_at,
        )
        self._logger.bind(
            event="feishu.media.downloaded",
            media_type=media.media_type,
            source_key=media.source_key,
            source_message_id=media.source_message_id,
            local_path=media.local_path,
            size_bytes=media.size_bytes,
            sha256=media.sha256,
        ).info("Feishu media downloaded")
        return media

    def _ensure_success(self, response: BaseResponse, *, action: str, key: str) -> None:
        if response.success():
            return
        raise MediaServiceError(
            f"Feishu {action} failed for {key}: code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
        )
