"""Object storage abstraction: Tencent COS in production, local disk for mock runs."""
from __future__ import annotations

import asyncio
import mimetypes
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from .config import Settings, settings as default_settings
from .logger import log

try:  # pragma: no cover - import guard for lightweight installs
    from qcloud_cos import CosConfig, CosS3Client
except Exception:  # pragma: no cover
    CosConfig = None  # type: ignore
    CosS3Client = None  # type: ignore


class StorageError(RuntimeError):
    pass


def guess_content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class Storage(ABC):
    """Uploads a local file and returns a URL the vendors can fetch."""

    name: str = "storage"

    @abstractmethod
    async def put_file(self, path: Path, key: str) -> str: ...


class LocalStorage(Storage):
    name = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def put_file(self, path: Path, key: str) -> str:
        # Files already live under DATA_DIR; they are served by the /media mount.
        try:
            relative = path.resolve().relative_to(self.settings.data_dir.resolve())
        except ValueError as exc:
            raise StorageError("本地文件不在 DATA_DIR 内，无法提供访问地址") from exc
        return self.settings.media_url(relative.as_posix())


class CosStorage(Storage):
    name = "cos"

    def __init__(self, settings: Settings) -> None:
        if not settings.has_cos or CosConfig is None:
            raise StorageError("COS 未配置完整（COS_SECRET_ID/SECRET_KEY/REGION/BUCKET）")
        self.settings = settings
        config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.cos_secret_id,
            SecretKey=settings.cos_secret_key,
            Scheme="https",
        )
        self.client = CosS3Client(config)

    def _url(self, key: str) -> str:
        if self.settings.cos_cdn_base:
            return f"{self.settings.cos_cdn_base.rstrip('/')}/{key}"
        return (
            f"https://{self.settings.cos_bucket}.cos.{self.settings.cos_region}.myqcloud.com/{key}"
        )

    async def put_file(self, path: Path, key: str) -> str:
        import asyncio

        key = f"{self.settings.cos_prefix}/{key}".lstrip("/")

        def _upload() -> None:
            self.client.upload_file(
                Bucket=self.settings.cos_bucket,
                Key=key,
                LocalFilePath=str(path),
                PartSize=1,
                MAXThread=4,
                EnableMD5=False,
            )
            try:
                self.client.put_object_acl(
                    Bucket=self.settings.cos_bucket, Key=key, ACL=self.settings.cos_acl
                )
            except Exception as exc:  # bucket-level public read is common
                log.warning("COS ACL 设置跳过: %s", exc)

        try:
            await asyncio.to_thread(_upload)
        except Exception as exc:
            raise StorageError(f"COS 上传失败: {exc}") from exc
        return self._url(key)


def build_storage(settings: Settings = default_settings) -> Storage:
    if settings.mock or not settings.has_cos:
        if not settings.mock and settings.has_tencent:
            log.warning("未配置 COS：智影需要公网可访问 URL，真实模式下请补齐 COS 配置")
        return LocalStorage(settings)
    return CosStorage(settings)


async def download_file(
    url: str, dest: Path, *, timeout: float = 120.0, attempts: int = 4
) -> Path:
    """Download with retries — 智影返回成片地址后，CDN 可能还要几秒才可访问。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with tmp.open("wb") as fh:
                        async for chunk in response.aiter_bytes(65536):
                            fh.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as exc:  # noqa: BLE001 - 网络抖动一律重试
            last_error = exc
            tmp.unlink(missing_ok=True)
            if attempt < attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
    detail = str(last_error) or type(last_error).__name__
    raise StorageError(f"下载失败（已重试 {attempts} 次）: {detail}") from last_error


def copy_local(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return dest
