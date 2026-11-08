"""Stage 1 — build a reusable digital-human twin from a single upload."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .. import db
from ..config import settings
from ..logger import log
from ..media import MediaError, extract_voice_sample, make_thumbnail, media_info, to_vertical_h264
from ..providers import ProviderError, build_voice_provider
from ..storage import StorageError, build_storage


async def run_setup(job_id: str, avatar_id: str, source: Path) -> None:
    """抽音 → 复刻音色 → 转 H.264 竖屏 → 上传 COS → 落库分身。"""
    voice = build_voice_provider(settings)
    storage = build_storage(settings)
    try:
        db.update_task(
            job_id,
            stage="audio",
            progress=8,
            log="已接收视频，开始抽取人声",
        )
        sample_path = settings.audio_dir / f"{avatar_id}_sample.wav"
        segment = await asyncio.to_thread(
            extract_voice_sample, source, sample_path, settings.clone_segment_seconds
        )
        db.update_task(
            job_id,
            stage="clone",
            progress=28,
            log=f"已抽取 {segment['duration']}s 干净人声（起点 {segment['start']}s），提交 MiniMax 声音复刻",
        )

        clone = await voice.clone(sample_path, f"dh_{avatar_id}")
        db.update_avatar(
            avatar_id,
            voice_id=clone.voice_id,
            meta={"sample_start": segment["start"], "sample_duration": segment["duration"]},
        )
        db.update_task(
            job_id,
            stage="transcode",
            progress=52,
            log=f"音色已生成 {clone.voice_id}，开始转码 9:16 H.264",
        )

        ref_path = settings.upload_dir / f"{avatar_id}_ref.mp4"
        info = await asyncio.to_thread(to_vertical_h264, source, ref_path)
        await asyncio.to_thread(make_thumbnail, ref_path, settings.upload_dir / f"{avatar_id}.jpg")
        db.update_task(
            job_id,
            stage="uploading",
            progress=78,
            log=f"竖屏转码完成 {info['width']}x{info['height']} / {info['duration']:.1f}s，上传 COS",
        )

        ref_url = await storage.put_file(ref_path, f"avatars/{avatar_id}/ref.mp4")
        db.update_avatar(
            avatar_id,
            status="ready",
            ref_video_url=ref_url,
            ref_video_key=f"avatars/{avatar_id}/ref.mp4",
            preview_url=settings.media_url(f"uploads/{ref_path.name}"),
            duration=info["duration"],
            error=None,
            meta={
                "sample_start": segment["start"],
                "sample_duration": segment["duration"],
                "width": info["width"],
                "height": info["height"],
                "source_duration": media_info(source)["duration"],
                "storage": storage.name,
            },
        )
        db.update_task(
            job_id,
            status="success",
            stage="done",
            progress=100,
            avatar_id=avatar_id,
            log="分身就绪，可无限次复用（每次只替换音频）",
            meta={"avatar_id": avatar_id, "ref_video_url": ref_url},
        )
        log.info("分身创建完成 avatar=%s voice=%s", avatar_id, clone.voice_id)
    except (ProviderError, MediaError, StorageError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        _fail(job_id, avatar_id, message)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("创建分身失败")
        _fail(job_id, avatar_id, f"创建分身失败: {exc}")
    finally:
        await voice.aclose()


def _fail(job_id: str, avatar_id: str, message: str) -> None:
    db.update_avatar(avatar_id, status="failed", error=message)
    db.update_task(
        job_id,
        status="failed",
        stage="failed",
        progress=100,
        error=message,
        log=f"失败：{message}",
    )
    log.error("创建分身失败: %s", message)
