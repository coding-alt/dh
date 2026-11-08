"""MiniMax voice cloning + synchronous TTS.

Docs:
  * upload sample: POST /v1/files/upload   (multipart, purpose=voice_clone)
  * clone voice:   POST /v1/voice_clone    (JSON: file_id + voice_id)
  * synthesize:    POST /v1/t2a_v2         (JSON, returns hex encoded mp3)

Auth is ``Authorization: Bearer <API key>``. The mainland endpoint
(``https://api.minimaxi.com``) additionally accepts a ``GroupId`` query
parameter; set ``VERASPEAK_MINIMAX_GROUP_ID`` when your account needs it.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from ..logger import log
from ..media import duration_of
from .base import CloneResult, ProviderError, SynthResult, VoiceProvider

CLONE_MIN_SECONDS = 10
CLONE_MAX_SECONDS = 300
CLONE_MAX_BYTES = 20 * 1024 * 1024


class MiniMaxVoiceProvider(VoiceProvider):
    name = "minimax"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ---- plumbing --------------------------------------------------------
    def _url(self, path: str) -> str:
        base = f"{self.settings.minimax_origin.rstrip('/')}{path}"
        group = self.settings.minimax_group_id
        return f"{base}?GroupId={group}" if group else base

    def _headers(self, *, json_body: bool = True) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.minimax_api_key}"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _require_key(self) -> None:
        if not self.settings.minimax_api_key:
            raise ProviderError("缺少 VERASPEAK_MINIMAX_API_KEY")

    # ---- voice clone -----------------------------------------------------
    async def clone(self, sample: Path, voice_name: str) -> CloneResult:
        self._require_key()
        size = sample.stat().st_size if sample.exists() else 0
        if not size:
            raise ProviderError("复刻样本为空，请重新上传视频")
        if size > CLONE_MAX_BYTES:
            raise ProviderError("复刻样本超过 20MB，请缩短片段")
        seconds = duration_of(sample)
        if seconds < CLONE_MIN_SECONDS:
            raise ProviderError(
                f"复刻样本仅 {seconds:.1f}s，MiniMax 要求至少 {CLONE_MIN_SECONDS}s"
            )
        if seconds > CLONE_MAX_SECONDS:
            raise ProviderError(
                f"复刻样本 {seconds:.0f}s 超限，MiniMax 最多 {CLONE_MAX_SECONDS}s"
            )

        file_id = await self._upload(sample)
        voice_id = self.settings.minimax_voice_id or _new_voice_id(voice_name)
        payload = {
            "file_id": file_id,
            "voice_id": voice_id,
            # 不传 text 就没有试听合成，不会产生字符计费
            "need_noise_reduction": True,
            "need_volume_normalization": True,
        }
        data = await self._post("/v1/voice_clone", payload, timeout=180, stage="声音复刻")
        log.info("MiniMax 声音复刻成功 voice_id=%s", voice_id)
        return CloneResult(voice_id=voice_id, raw={"voice_id": voice_id, "response": data})

    async def _upload(self, sample: Path) -> int:
        content_type = _content_type(sample)
        files = {"file": (sample.name, sample.read_bytes(), content_type)}
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                self._url("/v1/files/upload"),
                files=files,
                data={"purpose": "voice_clone"},
                headers=self._headers(json_body=False),
            )
        data = _read_json(response)
        _raise_for_status(response, data, "上传复刻样本")
        file_id = (data.get("file") or {}).get("file_id")
        if file_id is None:
            raise ProviderError(f"MiniMax 未返回 file_id: {_error_text(data)}", raw=data)
        return int(file_id)

    # ---- TTS -------------------------------------------------------------
    async def synthesize(
        self, text: str, voice_id: str, dest: Path, *, speed: float = 1.0
    ) -> SynthResult:
        self._require_key()
        if not text.strip():
            raise ProviderError("文案不能为空")
        dest.parent.mkdir(parents=True, exist_ok=True)

        payload: dict[str, Any] = {
            "model": self.settings.minimax_model,
            "text": text,
            "stream": False,
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": max(0.5, min(2.0, float(speed))),
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }
        data = await self._post("/v1/t2a_v2", payload, timeout=300, stage="语音合成")
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise ProviderError(f"MiniMax 未返回音频: {_error_text(data)}", raw=data)
        try:
            audio = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise ProviderError("MiniMax 返回的音频不是合法的 hex 数据", raw=data) from exc
        dest.write_bytes(audio)

        extra = data.get("extra_info") or {}
        duration = float(extra.get("audio_length") or 0) / 1000
        if not duration:
            try:
                duration = duration_of(dest)
            except Exception:
                duration = 0.0
        return SynthResult(path=dest, duration=duration, raw={"voice_id": voice_id})

    # ---- http ------------------------------------------------------------
    async def _post(
        self, path: str, payload: dict, *, timeout: float, stage: str = ""
    ) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self._url(path), json=payload, headers=self._headers()
            )
        data = _read_json(response)
        _raise_for_status(response, data, stage or path)
        return data


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _new_voice_id(voice_name: str) -> str:
    """MiniMax: 8-256 chars, starts with a letter, letters/digits/-/_ only."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", voice_name or "")[:48].strip("-_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"dh{cleaned}"
    return f"{cleaned}_{uuid.uuid4().hex[:6]}"[:256]


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".m4a", ".aac"}:
        return "audio/mp4"
    return "audio/wav"


def _read_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except Exception:
        return {"raw": response.text[:500]}
    return data if isinstance(data, dict) else {"raw": data}


def _raise_for_status(response: httpx.Response, data: dict, stage: str) -> None:
    base = data.get("base_resp") or {}
    code = base.get("status_code")
    if response.status_code >= 400 or (code is not None and code != 0):
        message = base.get("status_msg") or _error_text(data)
        if "no valid audio clips" in message:
            message = (
                "样本中没有可用人声（可能只有背景音乐/静音），"
                "请换一段包含清晰人声的视频后重试"
            )
        elif code == 1008 or "insufficient balance" in message:
            message = "账户余额不足，请充值后重试（platform.minimaxi.com → 账户余额）"
        raise ProviderError(
            f"MiniMax {stage}失败（HTTP {response.status_code} / code={code}）：{message}",
            code=code,
            raw=data,
        )


def _error_text(data: Any) -> str:
    if isinstance(data, dict):
        base = data.get("base_resp") or {}
        if base.get("status_msg"):
            return str(base["status_msg"])
        for key in ("message", "msg", "error", "raw"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value[:400]
        return str(data)[:400]
    return str(data)[:400]
