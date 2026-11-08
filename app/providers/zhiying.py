"""Tencent Zhiying (数智人) video lip-sync provider.

Signature is the aPaaS HMAC scheme (not TC3):
    signing_content = "&".join(f"{k}={v}" for k in sorted(params))
    signature       = base64(hmac_sha256(accesstoken, signing_content))
    URL             = path + "?" + signing_content + "&signature=" + urlencode(signature)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import quote

import httpx

from ..config import Settings
from ..logger import log
from .base import AvatarProvider, DriveStatus, ProviderError

SUBMIT_PATH = "/v2/ivh/videomaker/broadcastservice/videomakenotrain"
PROGRESS_PATH = "/v2/ivh/videomaker/broadcastservice/getprogress"


def sign_url(base_url: str, path: str, params: dict[str, str], access_token: str) -> str:
    signing_content = "&".join(f"{key}={params[key]}" for key in sorted(params))
    digest = hmac.new(
        access_token.encode("utf-8"), signing_content.encode("utf-8"), hashlib.sha256
    ).digest()
    signature = quote(base64.b64encode(digest).decode("utf-8"), safe="")
    return f"{base_url}{path}?{signing_content}&signature={signature}"


class ZhiyingAvatarProvider(AvatarProvider):
    name = "zhiying"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.concurrency_type = settings.tencent_concurrency
        self.callback_url = settings.tencent_callback_url

    # ---- plumbing --------------------------------------------------------
    def _url(self, path: str) -> str:
        s = self.settings
        params = {"appkey": s.tencent_appkey, "timestamp": str(int(time.time()))}
        if s.tencent_query_appid and s.tencent_appid:
            params["appid"] = s.tencent_appid
        return sign_url(s.zhiying_base_url, path, params, s.tencent_accesstoken)

    async def _post(self, path: str, payload: dict, *, timeout: float = 60) -> dict:
        s = self.settings
        if not s.has_tencent:
            raise ProviderError("缺少 TENCENT_APPKEY / TENCENT_ACCESSTOKEN")
        body = {"Header": {}, "Payload": payload}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self._url(path),
                json=body,
                headers={"Content-Type": "application/json;charset=utf-8"},
            )
        try:
            data = response.json()
        except Exception as exc:
            raise ProviderError(
                f"智影返回非 JSON（HTTP {response.status_code}）: {response.text[:300]}"
            ) from exc
        if response.status_code >= 400:
            raise ProviderError(f"智影 HTTP {response.status_code}: {_error_text(data)}", raw=data)
        node = data.get("Response", data)
        header = node.get("Header") if isinstance(node, dict) else None
        if isinstance(header, dict):
            code = header.get("Code")
            if code not in (None, 0, "0"):
                raise ProviderError(
                    f"智影接口错误 [{code}] {header.get('Message') or ''}".strip(),
                    code=code,
                    raw=data,
                )
        return data

    # ---- provider API ----------------------------------------------------
    def submit_payload(self, ref_video_url: str, audio_url: str) -> dict:
        payload: dict[str, Any] = {
            "RefVideoUrl": ref_video_url,
            "DriverType": "OriginalVoice",
            "InputAudioUrl": audio_url,
            "VideoLoop": 1,
        }
        if self.concurrency_type:
            payload["ConcurrencyType"] = self.concurrency_type
        if self.callback_url:
            payload["CallbackUrl"] = self.callback_url
        return payload

    async def submit(self, ref_video_url: str, audio_url: str) -> str:
        data = await self._post(SUBMIT_PATH, self.submit_payload(ref_video_url, audio_url))
        task_id = _find(data, ("TaskId", "TaskID", "taskId"))
        if not task_id:
            raise ProviderError(f"智影未返回 TaskId: {_error_text(data)}", raw=data)
        log.info("智影任务已提交 TaskId=%s", task_id)
        return str(task_id)

    async def poll(self, task_id: str) -> DriveStatus:
        data = await self._post(PROGRESS_PATH, {"TaskId": task_id})
        payload = _payload(data)
        status = str(_find(payload, ("Status", "status")) or "MAKING").upper()
        if status in {"FAIL", "FAILED"}:
            message = _find(payload, ("FailMessage", "Message")) or "视频合成失败"
            return DriveStatus(
                status="FAIL",
                progress=-1,
                fail_code=_find(payload, ("FailCode",)),
                fail_message=str(message),
                raw=payload,
            )
        progress = _find(payload, ("Progress", "progress"))
        duration = _find(payload, ("Duration", "duration"))
        return DriveStatus(
            status=status if status in {"COMMIT", "MAKING", "SUCCESS"} else "MAKING",
            progress=int(progress) if isinstance(progress, (int, float)) else 0,
            media_url=_find(payload, ("MediaUrl", "MediaURL", "VideoUrl")),
            subtitles_url=_find(payload, ("SubtitlesUrl", "SubtitleUrl", "SrtUrl")),
            duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
            raw=payload,
        )


# --------------------------------------------------------------------------
# response helpers
# --------------------------------------------------------------------------
def _payload(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    node = data.get("Response", data)
    if not isinstance(node, dict):
        return {}
    for key in ("Payload", "Data", "Result"):
        if isinstance(node.get(key), dict):
            return node[key]
    return node


def _find(data: Any, keys: tuple[str, ...]) -> Any:
    """Depth-first search for the first matching key (vendor wrappers vary)."""
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = _find(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find(item, keys)
            if found not in (None, ""):
                return found
    return None


def _error_text(data: Any) -> str:
    if isinstance(data, dict):
        node = data.get("Response", data)
        header = node.get("Header") if isinstance(node, dict) else None
        if isinstance(header, dict):
            code = header.get("Code")
            message = header.get("Message")
            if code not in (None, 0, "0") and message:
                return f"[{code}] {message}"
        for key in ("Message", "message", "FailMessage", "Error", "error"):
            value = _find(node, (key,))
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                nested = value.get("Message") or value.get("message")
                if nested:
                    return str(nested)
    return str(data)[:400]

