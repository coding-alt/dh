"""Request/response models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1)
    avatar_id: str | None = None
    voice_id: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class GenerateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    avatar_id: str
    voice_id: str | None = None
    title: str | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    # 是否烧录字幕 / 是否打「AI 生成」标识（默认都不加，成片更干净）
    subtitles: bool | None = None
    ai_label: bool | None = None


class RenameAvatarRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=24)


class ImportAvatarRequest(BaseModel):
    """Use an existing voice + reference video (e.g. assets created earlier)."""

    name: str = Field(default="导入的分身", max_length=24)
    voice_id: str = Field(..., min_length=1)
    ref_video_url: str = Field(..., min_length=1)
    preview_url: str | None = None
