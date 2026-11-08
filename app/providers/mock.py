"""Offline providers used by MOCK_PROVIDERS=1 to exercise the full pipeline without keys.

The mock voice uses macOS `say` when available, otherwise a silent track; the mock
avatar simply loops the reference video over the audio (no real lip sync).
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid
from pathlib import Path

from ..config import Settings
from ..media import estimate_duration, run
from .base import AvatarProvider, CloneResult, DriveStatus, SynthResult, VoiceProvider

CANDIDATE_VOICES = ("Meijia", "Tingting", "Sinji", "Li-mu", "Samantha")


def _say_voice() -> str | None:
    if not shutil.which("say"):
        return None
    try:
        listing = subprocess.run(
            ["say", "-v", "?"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10
        ).stdout.decode("utf-8", "ignore")
    except Exception:
        return None
    for voice in CANDIDATE_VOICES:
        if voice in listing:
            return voice
    return None


class MockVoiceProvider(VoiceProvider):
    name = "mock-voice"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def clone(self, sample: Path, voice_name: str) -> CloneResult:
        await asyncio.sleep(0.4)
        voice_id = f"S_mock_{uuid.uuid4().hex[:8]}"
        return CloneResult(voice_id=voice_id, raw={"mock": True, "name": voice_name})

    async def synthesize(
        self, text: str, voice_id: str, dest: Path, *, speed: float = 1.0
    ) -> SynthResult:
        dest.parent.mkdir(parents=True, exist_ok=True)
        voice = _say_voice()
        tmp = dest.with_suffix(".aiff")
        ok = False
        if voice:
            try:
                subprocess.run(
                    ["say", "-v", voice, "-r", str(int(190 * speed)), "-o", str(tmp), text],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=180,
                    check=True,
                )
                ok = True
            except Exception:
                ok = False
        if ok and tmp.exists() and tmp.stat().st_size > 1000:
            run(
                [
                    self.settings.ffmpeg,
                    "-y",
                    "-i",
                    str(tmp),
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(dest),
                ]
            )
            tmp.unlink(missing_ok=True)
            duration = estimate_duration(text, speed)
        else:
            duration = max(1.2, estimate_duration(text, speed))
            run(
                [
                    self.settings.ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=24000:cl=mono",
                    "-t",
                    f"{duration:.2f}",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(dest),
                ]
            )
        return SynthResult(
            path=dest, duration=duration, raw={"mock": True, "voice_id": voice_id}
        )


class MockAvatarProvider(AvatarProvider):
    """Poll-free mock: the service renders locally when this provider is active."""

    name = "mock-avatar"
    local_render = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def submit(self, ref_video_url: str, audio_url: str) -> str:
        await asyncio.sleep(0.3)
        return f"mock-{uuid.uuid4().hex[:12]}"

    async def poll(self, task_id: str) -> DriveStatus:
        await asyncio.sleep(0.3)
        return DriveStatus(status="SUCCESS", progress=100)
