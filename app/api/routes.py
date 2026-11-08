"""HTTP API for the digital-human MVP."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse

from .. import db
from ..config import settings
from ..media import MediaError, estimate_duration, media_info
from ..providers import ProviderError, build_voice_provider
from ..schemas import GenerateRequest, ImportAvatarRequest, RenameAvatarRequest, TTSRequest
from ..services.avatars import run_setup
from ..services.generation import run_generation, stage_label
from ..services.runner import spawn
from ..storage import StorageError, build_storage

router = APIRouter(prefix="/api")

ALLOWED_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


# --------------------------------------------------------------------------
# serializers
# --------------------------------------------------------------------------
def avatar_public(avatar: dict) -> dict:
    meta = avatar.get("meta") or {}
    return {
        "id": avatar["id"],
        "name": avatar["name"],
        "status": avatar["status"],
        "voice_id": avatar.get("voice_id"),
        "ref_video_url": avatar.get("ref_video_url"),
        "preview_url": avatar.get("preview_url"),
        "duration": round(avatar.get("duration") or 0, 2),
        "reuse_count": avatar.get("reuse_count") or 0,
        "error": avatar.get("error"),
        "sample_seconds": meta.get("sample_duration"),
        "created_at": avatar.get("created_at"),
    }


def task_public(task: dict) -> dict:
    meta = task.get("meta") or {}
    video_url = task.get("video_url") or ""
    # `_final` 只有本地压制成功才会出现；老任务没有 meta 字段，默认当作已烧字幕与标识
    finished = "_final" in video_url
    subs_flag = meta.get("subtitles")
    label_flag = meta.get("ai_label")
    burned_subs = finished and (True if subs_flag is None else bool(subs_flag))
    burned_label = finished and (True if label_flag is None else bool(label_flag))
    requested_subs = bool(subs_flag) if subs_flag is not None else finished
    return {
        "id": task["id"],
        "kind": task["kind"],
        "status": task["status"],
        "stage": task["stage"],
        "stage_label": stage_label(task["stage"]),
        "failed_stage": (task.get("meta") or {}).get("failed_stage"),
        "progress": task.get("progress") or 0,
        "avatar_id": task.get("avatar_id"),
        "title": task.get("title"),
        "text": task.get("text"),
        "audio_url": task.get("audio_url"),
        "video_url": task.get("video_url"),
        "srt_url": f"/api/srt/{task['id']}" if task.get("srt_path") else None,
        "download_url": f"/api/download/{task['id']}" if task.get("video_url") else None,
        "remote_task_id": task.get("remote_task_id"),
        "duration": round(task.get("audio_duration") or 0, 2),
        "subtitles_burned": burned_subs,
        "subtitles_requested": requested_subs,
        "label_burned": burned_label,
        "error": task.get("error"),
        "logs": [
            {"at": entry.get("at"), "message": entry.get("message")}
            for entry in (task.get("logs") or [])[-12:]
        ],
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "watermark": "AI 生成",
    }


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------
@router.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "mock": settings.mock,
        "providers": {
            "voice": "mock" if settings.mock else "minimax",
            "avatar": "mock" if settings.mock else "zhiying",
            "storage": "local" if (settings.mock or not settings.has_cos) else "cos",
        },
        "missing": [] if settings.mock else settings.missing_for_real_run(),
        "max_text_chars": settings.max_text_chars,
        "clone_segment_seconds": settings.clone_segment_seconds,
        "burn_subtitles": settings.burn_subtitles,
        "public_base_url": settings.public_base_url,
    }


@router.get("/config")
async def config() -> dict:
    return {
        "mock": settings.mock,
        "max_text_chars": settings.max_text_chars,
        "max_video_seconds": settings.max_video_seconds,
        "clone_segment_seconds": settings.clone_segment_seconds,
        "poll_interval": settings.poll_interval,
        "watermark": "AI 生成",
        "chars_per_second": 4.4,
    }


# --------------------------------------------------------------------------
# avatars / stage 1
# --------------------------------------------------------------------------
@router.get("/avatars")
async def avatars() -> dict:
    return {"items": [avatar_public(a) for a in db.list_avatars()]}


@router.post("/avatars/import")
async def import_avatar(body: ImportAvatarRequest) -> dict:
    """Register an already-created {voice_id, ref_video_url} pair as a twin."""
    avatar = db.create_avatar(body.name.strip() or "导入的分身", meta={"imported": True})
    db.update_avatar(
        avatar["id"],
        status="ready",
        voice_id=body.voice_id.strip(),
        ref_video_url=body.ref_video_url.strip(),
        preview_url=(body.preview_url or body.ref_video_url).strip(),
    )
    return avatar_public(db.get_avatar(avatar["id"]) or avatar)


@router.get("/avatars/{avatar_id}")
async def avatar_detail(avatar_id: str) -> dict:
    avatar = db.get_avatar(avatar_id)
    if not avatar:
        raise HTTPException(404, "分身不存在")
    tasks = db.tasks_for_avatar(avatar_id, limit=8)
    return {
        "avatar": avatar_public(avatar),
        "recent": [task_public(t) for t in tasks if t["kind"] == "generate"],
    }


@router.patch("/avatars/{avatar_id}")
async def rename_avatar(avatar_id: str, body: RenameAvatarRequest) -> dict:
    avatar = db.get_avatar(avatar_id)
    if not avatar:
        raise HTTPException(404, "分身不存在")
    return avatar_public(db.update_avatar(avatar_id, name=body.name.strip()) or avatar)


@router.delete("/avatars/{avatar_id}")
async def remove_avatar(avatar_id: str) -> dict:
    if not db.get_avatar(avatar_id):
        raise HTTPException(404, "分身不存在")
    db.delete_avatar(avatar_id)
    return {"ok": True}


def _require_public_storage() -> None:
    if settings.mock or settings.has_cos:
        return
    base = settings.public_base_url
    if base.startswith("https://") and "127.0.0.1" not in base and "localhost" not in base:
        return
    raise HTTPException(
        400,
        "智影需要公网可访问的视频/音频 URL：请配置 COS_* 或把 PUBLIC_BASE_URL 换成公网地址"
        "（本地体验可设置 MOCK_PROVIDERS=1）",
    )


@router.post("/setup_video")
async def setup_video(
    file: UploadFile = File(...),
    name: str = Form("我的数字人"),
):
    """上传 1-2 分钟口播视频，异步创建可复用的数字人分身。"""
    _require_public_storage()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"不支持的格式 {suffix or '未知'}，请上传 mp4/mov 视频")

    avatar = db.create_avatar(name.strip() or "我的数字人")
    source = settings.upload_dir / f"{avatar['id']}_source{suffix}"

    def _save() -> int:
        size = 0
        limit = settings.max_upload_mb * 1024 * 1024
        with source.open("wb") as fh:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"视频超过 {settings.max_upload_mb}MB 限制")
                fh.write(chunk)
        return size

    try:
        size = await asyncio.to_thread(_save)
    except HTTPException:
        source.unlink(missing_ok=True)
        db.delete_avatar(avatar["id"])
        raise
    finally:
        await file.close()

    if size < 64 * 1024:
        source.unlink(missing_ok=True)
        db.delete_avatar(avatar["id"])
        raise HTTPException(400, "视频文件过小，请上传真实拍摄的口播视频")

    try:
        info = await asyncio.to_thread(media_info, source)
    except MediaError as exc:
        source.unlink(missing_ok=True)
        db.delete_avatar(avatar["id"])
        raise HTTPException(400, str(exc)) from exc

    if not info["has_video"] or not info["has_audio"]:
        source.unlink(missing_ok=True)
        db.delete_avatar(avatar["id"])
        raise HTTPException(400, "视频需要同时包含画面和音轨")
    if info["duration"] < 5:
        source.unlink(missing_ok=True)
        db.delete_avatar(avatar["id"])
        raise HTTPException(400, "视频太短，请上传 1-2 分钟的口播视频")
    if info["duration"] > settings.max_video_seconds + 30:
        source.unlink(missing_ok=True)
        db.delete_avatar(avatar["id"])
        raise HTTPException(
            400, f"视频超过 {settings.max_video_seconds + 30}s，请裁剪到 1-2 分钟"
        )

    db.update_avatar(
        avatar["id"],
        meta={"source_name": file.filename, "source_size": size},
        duration=info["duration"],
    )
    job = db.create_task(
        kind="setup",
        stage="uploaded",
        avatar_id=avatar["id"],
        title=f"创建分身 · {avatar['name']}",
        meta={"source": str(source), "source_duration": info["duration"]},
    )
    spawn(run_setup(job["id"], avatar["id"], source), label=f"setup:{job['id']}")
    return {
        "job_id": job["id"],
        "task_id": job["id"],
        "avatar_id": avatar["id"],
        "status": "processing",
        "stage": "uploaded",
        "stage_label": "视频已上传",
        "duration": round(info["duration"], 2),
        "avatar": avatar_public(db.get_avatar(avatar["id"]) or avatar),
    }


@router.get("/setup_video/{job_id}")
async def setup_status(job_id: str) -> dict:
    job = db.get_task(job_id)
    if not job or job["kind"] != "setup":
        raise HTTPException(404, "任务不存在")
    avatar = db.get_avatar(job.get("avatar_id") or "")
    return {
        "job_id": job_id,
        "status": job["status"],
        "stage": job["stage"],
        "progress": job.get("progress") or 0,
        "error": job.get("error"),
        "logs": task_public(job)["logs"],
        "avatar": avatar_public(avatar) if avatar else None,
    }


# --------------------------------------------------------------------------
# stage 2
# --------------------------------------------------------------------------
@router.post("/tts")
async def tts(body: TTSRequest):
    """Preview endpoint: synthesize with the cloned voice and host the audio."""
    _require_public_storage()
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "文案不能为空")
    if len(text) > settings.max_text_chars:
        raise HTTPException(400, f"文案过长（上限 {settings.max_text_chars} 字）")

    avatar = None
    if body.avatar_id:
        avatar = db.get_avatar(body.avatar_id)
        if not avatar:
            raise HTTPException(404, "分身不存在")
    voice_id = body.voice_id or (avatar or {}).get("voice_id")
    if not voice_id:
        raise HTTPException(400, "缺少音色：请先创建分身或传入 voice_id")

    voice = build_voice_provider(settings)
    storage = build_storage(settings)
    name = uuid.uuid4().hex[:10]
    dest = settings.audio_dir / f"preview_{name}.mp3"
    try:
        result = await voice.synthesize(text, voice_id, dest, speed=body.speed)
        url = await storage.put_file(dest, f"audio/preview_{name}.mp3")
    except ProviderError as exc:
        raise HTTPException(502, exc.message) from exc
    except (MediaError, StorageError) as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await voice.aclose()

    duration = result.duration or estimate_duration(text, body.speed)
    return {
        "audio_url": url,
        "voice_id": voice_id,
        "duration": round(duration, 2),
        "text": text,
    }


@router.post("/generate")
async def generate(body: GenerateRequest):
    _require_public_storage()
    text = body.text.strip()
    if len(text) < 2:
        raise HTTPException(400, "文案太短，至少 2 个字")
    if len(text) > settings.max_text_chars:
        raise HTTPException(400, f"文案过长（上限 {settings.max_text_chars} 字）")

    avatar = db.get_avatar(body.avatar_id)
    if not avatar:
        raise HTTPException(404, "分身不存在，请先创建")
    if avatar["status"] != "ready":
        raise HTTPException(400, f"分身状态为 {avatar['status']}，请等待创建完成")

    voice_id = body.voice_id or avatar.get("voice_id")
    if not voice_id:
        raise HTTPException(400, "分身缺少音色，请重新创建")

    title = (body.title or text[:16]).strip()
    task = db.create_task(
        kind="generate",
        stage="pending",
        status="pending",
        avatar_id=avatar["id"],
        title=title,
        text=text,
        meta={
            "voice_id": voice_id,
            "speed": body.speed,
            "subtitles": bool(body.subtitles),
            "ai_label": bool(body.ai_label),
        },
    )
    spawn(run_generation(task["id"]), label=f"generate:{task['id']}")
    return {"task_id": task["id"], "status": "pending", "task": task_public(task)}


@router.get("/task/{task_id}")
async def task_status(task_id: str) -> dict:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task_public(task)


@router.get("/tasks")
async def tasks(page: int = 1, page_size: int = 10, limit: int | None = None) -> dict:
    # limit 为旧参数（首页「最近成片」仍在使用），等价于第一页取多少条
    if limit is not None:
        page_size = limit
    page_size = max(1, min(page_size, 50))
    counts = db.count_tasks_by_status("generate")
    total = sum(counts.values())
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), pages)
    items = db.list_tasks(kind="generate", limit=page_size, offset=(page - 1) * page_size)
    return {
        "items": [task_public(t) for t in items],
        "total": total,
        "counts": {
            "success": counts.get("success", 0),
            "failed": counts.get("failed", 0),
            "running": counts.get("processing", 0) + counts.get("pending", 0),
        },
        "page": page,
        "pages": pages,
        "page_size": page_size,
    }


@router.delete("/task/{task_id}")
async def remove_task(task_id: str) -> dict:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] in ("pending", "processing"):
        raise HTTPException(409, "作品还在生成中，完成后再删除")

    targets = {
        settings.output_dir / f"{task_id}.mp4",
        settings.output_dir / f"{task_id}_final.mp4",
        settings.output_dir / f"{task_id}.ass",
        settings.subtitle_dir / f"{task_id}.srt",
        settings.audio_dir / f"{task_id}.mp3",
    }
    if task.get("srt_path"):
        targets.add(Path(task["srt_path"]))
    removed: list[str] = []
    for path in targets:
        try:
            path.unlink()
            removed.append(path.name)
        except FileNotFoundError:
            continue
        except OSError:
            continue
    db.delete_task(task_id)
    return {"ok": True, "id": task_id, "removed": removed}


@router.get("/srt/{task_id}", response_class=PlainTextResponse)
async def subtitles(task_id: str):
    task = db.get_task(task_id)
    if not task or not task.get("srt_path"):
        raise HTTPException(404, "字幕不存在")
    path = Path(task["srt_path"])
    if not path.exists():
        raise HTTPException(404, "字幕文件已丢失")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@router.get("/download/{task_id}")
async def download(task_id: str):
    task = db.get_task(task_id)
    if not task or not task.get("video_url"):
        raise HTTPException(404, "成片尚未生成")
    path = Path(task["video_url"].rsplit("/", 1)[-1])
    file_path = settings.output_dir / path.name
    if not file_path.exists():
        raise HTTPException(404, "成片文件已丢失")
    filename = f"{task.get('title') or 'digital-human'}.mp4".replace("/", "-")
    return FileResponse(file_path, media_type="video/mp4", filename=filename)
