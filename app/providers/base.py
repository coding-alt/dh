"""Provider contracts so vendors (Doubao / Zhiying / Kling / …) stay swappable."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str | int | None = None, raw: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.raw = raw


@dataclass
class CloneResult:
    voice_id: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SynthResult:
    path: Path
    duration: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DriveStatus:
    status: str  # COMMIT | MAKING | SUCCESS | FAIL
    progress: int = 0
    media_url: str | None = None
    subtitles_url: str | None = None
    fail_code: Any = None
    fail_message: str | None = None
    duration_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status.upper() in {"SUCCESS", "FAIL"}


class VoiceProvider(ABC):
    name = "voice"

    @abstractmethod
    async def clone(self, sample: Path, voice_name: str) -> CloneResult:
        """Register a reusable voice from a 14-30s clean sample."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        dest: Path,
        *,
        speed: float = 1.0,
    ) -> SynthResult:
        """Render text to a local audio file."""

    async def aclose(self) -> None:  # pragma: no cover - optional hook
        return None


class AvatarProvider(ABC):
    name = "avatar"

    @abstractmethod
    async def submit(self, ref_video_url: str, audio_url: str) -> str:
        """Submit a lip-sync job and return the vendor task id."""

    @abstractmethod
    async def poll(self, task_id: str) -> DriveStatus:
        """Fetch current job status."""

    async def aclose(self) -> None:  # pragma: no cover - optional hook
        return None
