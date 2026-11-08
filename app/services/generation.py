"""Stage 2 — script → cloned TTS → lip-sync → subtitled 9:16 video."""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

import httpx

from .. import db
from ..config import settings
from ..logger import log
from ..media import (
    MediaError,
    burn_subtitles,
    srt_from_text,
    mock_lipsync,
    media_info,
)
from ..providers import ProviderError, build_avatar_provider, build_voice_provider
from ..storage import StorageError, build_storage, download_file

STAGE_LABELS = {
    "pending": "排队中",
    "tts": "复刻音色合成语音",
    "driving": "智影口型驱动",
    "subtitle": "字幕与封装",
    "done": "已完成",
    "failed": "生成失败",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


async def run_generation(task_id: str) -> None:
    task = db.get_task(task_id)
    if not task:
        return
    avatar = db.get_avatar(task.get("avatar_id") or "")
    if not avatar:
        _fail(task_id, "分身不存在，请先创建数字人分身")
        return
    if avatar["status"] != "ready" or not avatar.get("voice_id"):
        _fail(task_id, "分身尚未就绪，请等待创建完成或重新上传视频")
        return

    text = (task.get("text") or "").strip()
    if not text:
        _fail(task_id, "文案不能为空")
        return

    voice = build_voice_provider(settings)
    avatar_provider = build_avatar_provider(settings)
    storage = build_storage(settings)
    voice_id = (task.get("meta") or {}).get("voice_id") or avatar["voice_id"]
    speed = float((task.get("meta") or {}).get("speed") or settings.tts_speed)

    try:
        # ---- 1. TTS ------------------------------------------------------
        _set(task_id, stage="tts", progress=8, status="processing", log="调用 MiniMax 复刻音色合成语音")
        audio_path = settings.audio_dir / f"{task_id}.mp3"
        synth = await voice.synthesize(text, voice_id, audio_path, speed=speed)
        _set(
            task_id,
            progress=26,
            audio_path=str(audio_path),
            audio_duration=synth.duration,
            log=f"语音合成完成（{synth.duration:.1f}s），上传音频",
        )
        audio_url = await storage.put_file(audio_path, f"audio/{task_id}.mp3")
        _set(task_id, audio_url=audio_url, progress=34)

        # ---- 2. lip-sync -------------------------------------------------
        raw_path = settings.output_dir / f"{task_id}.mp4"
        subtitles_url: str | None = None

        if getattr(avatar_provider, "local_render", False):
            _set(task_id, stage="driving", progress=42, log="Mock 模式：本地循环参考视频")
            await _render_mock(avatar, audio_path, raw_path)
            await asyncio.sleep(0.6)
            duration = synth.duration
        else:
            _set(task_id, stage="driving", progress=40, log="提交智影「视频免训练」口型驱动")
            remote_id = await avatar_provider.submit(avatar["ref_video_url"], audio_url)
            _set(task_id, remote_task_id=remote_id, progress=44)

            status = None
            deadline = time.monotonic() + settings.poll_timeout
            polls = 0
            while True:
                status = await avatar_provider.poll(remote_id)
                polls += 1
                if status.status == "SUCCESS":
                    break
                if status.status == "FAIL":
                    raise ProviderError(
                        f"智影合成失败：{status.fail_message or '未知原因'}"
                        f"（code={status.fail_code}）"
                    )
                if time.monotonic() > deadline:
                    raise ProviderError("智影合成超时，请稍后在任务列表重试")
                _set(
                    task_id,
                    progress=min(78, 44 + polls),
                    log=_progress_log(status.status, polls),
                )
                await asyncio.sleep(settings.poll_interval)

            video_duration = (
                status.duration_ms / 1000 if status.duration_ms else synth.duration
            )
            subtitles_url = status.subtitles_url
            _set(
                task_id,
                stage="subtitle",
                progress=82,
                remote_media_url=status.media_url,
                remote_subtitles_url=status.subtitles_url,
                log="口型驱动完成，下载成片并压制字幕",
            )
            if not status.media_url:
                raise ProviderError("智影未返回成片地址")

            # ---- 3. download -------------------------------------------
            await download_file(status.media_url, raw_path, timeout=600)
            duration = video_duration or media_info(raw_path)["duration"]
            _set(task_id, progress=86)

        srt_text = await _resolve_srt(subtitles_url, text, duration, task_id)
        srt_path = settings.subtitle_dir / f"{task_id}.srt"
        srt_path.write_text(srt_text, encoding="utf-8")

        meta = task.get("meta") or {}
        want_subs = bool(meta.get("subtitles")) and settings.burn_subtitles
        want_label = bool(meta.get("ai_label"))

        final_path = raw_path
        # 即便不要字幕/标识，真实链路仍需重编码一次，用来抹掉智影的「AI合成」水印
        if want_subs or want_label or not settings.mock:
            burned = settings.output_dir / f"{task_id}_final.mp4"
            try:
                await asyncio.to_thread(
                    burn_subtitles,
                    raw_path,
                    burned,
                    srt_text if want_subs else "",
                    duration,
                    strip_vendor_mark=not settings.mock,
                    ai_label=want_label,
                )
                final_path = burned
            except MediaError as exc:
                log.warning("字幕压制失败，回退到原片: %s", exc)
                _set(task_id, log=f"字幕压制失败，已回退到智影原片（{exc}）")

        _finish(task_id, avatar, final_path, srt_path, duration)
    except (ProviderError, MediaError, StorageError) as exc:
        _fail(task_id, getattr(exc, "message", None) or str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("生成失败")
        _fail(task_id, f"生成失败：{exc}")
    finally:
        await voice.aclose()
        await avatar_provider.aclose()


async def _render_mock(avatar: dict, audio_path: Path, dest: Path) -> None:
    ref = await _local_ref(avatar)
    await asyncio.to_thread(mock_lipsync, ref, audio_path, dest)


async def _local_ref(avatar: dict) -> Path:
    """Locate a local copy of the reference video for offline rendering."""
    avatar_id = avatar["id"]
    local = settings.upload_dir / f"{avatar_id}_ref.mp4"
    if local.exists():
        return local

    url = (avatar.get("ref_video_url") or "").strip()
    marker = "/media/"
    if marker in url:
        relative = url.split(marker, 1)[1].split("?", 1)[0]
        candidate = (settings.data_dir / relative).resolve()
        try:
            candidate.relative_to(settings.data_dir.resolve())
        except ValueError as exc:
            raise MediaError("ref_video_url 指向 DATA_DIR 之外的路径") from exc
        if candidate.exists():
            return candidate

    if url.startswith(("http://", "https://")):
        digest = hashlib.sha1(url.encode()).hexdigest()[:8]
        cached = settings.upload_dir / f"ref_{avatar_id}_{digest}.mp4"
        if not cached.exists():
            try:
                await download_file(url, cached)
            except StorageError as exc:
                raise MediaError(
                    f"参考视频不可访问（{url}），请检查分身的 ref_video_url 是否公网可读"
                ) from exc
        return cached

    raise MediaError("找不到参考视频（mock 模式需要本地可读的 ref_video_url）")


async def _resolve_srt(
    subtitles_url: str | None, text: str, duration: float, task_id: str
) -> str:
    if subtitles_url:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                response = await client.get(subtitles_url)
                response.raise_for_status()
                raw = response.text
            if "-->" in raw:
                return raw if raw.endswith("\n") else raw + "\n"
        except Exception as exc:
            log.warning("下载智影字幕失败，使用本地打轴: %s", exc)
    return srt_from_text(text, duration)


def _progress_log(status: str, polls: int) -> str:
    if status == "COMMIT":
        return f"智影排队中（第 {polls} 次查询）"
    return f"智影合成中（第 {polls} 次查询）"


def _set(task_id: str, **fields) -> None:
    db.update_task(task_id, **fields)


def _finish(
    task_id: str,
    avatar: dict,
    final_path: Path,
    srt_path: Path,
    duration: float,
) -> None:
    local_url = settings.media_url(f"outputs/{final_path.name}")
    db.update_task(
        task_id,
        status="success",
        stage="done",
        progress=100,
        video_url=local_url,
        srt_path=str(srt_path),
        audio_duration=duration,
        error=None,
        log="成片已生成，记得 7 天内下载备份",
    )
    db.bump_reuse(avatar["id"])
    log.info("成片完成 task=%s avatar=%s", task_id, avatar["id"])


def _fail(task_id: str, message: str) -> None:
    task = db.get_task(task_id) or {}
    meta = dict(task.get("meta") or {})
    if task.get("stage") and task["stage"] not in {"failed", "pending"}:
        meta["failed_stage"] = task["stage"]
    db.update_task(
        task_id,
        status="failed",
        stage="failed",
        progress=100,
        error=message,
        meta=meta,
        log=f"失败：{message}",
    )
    log.error("生成失败 %s: %s", task_id, message)
