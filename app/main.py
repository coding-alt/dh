"""FastAPI entrypoint for the digital-human MVP."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, media
from .api.routes import router
from .config import settings
from .logger import log
from .services import runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    stale = db.recover_unfinished_tasks()
    if stale:
        log.warning("发现 %d 个上次未完成的任务，已标记为失败", len(stale))
    if settings.mock:
        log.warning("MOCK_PROVIDERS=1：使用本地模拟供应商，无需任何密钥")
    else:
        missing = settings.missing_for_real_run()
        if missing:
            log.error("缺少必要配置：%s（详见 .env.example）", ", ".join(missing))
        else:
            log.info("供应商就绪：MiniMax 声音复刻 + 腾讯智影 + COS")
    log.info("控制台：http://127.0.0.1:%s", settings.port)
    if settings.burn_subtitles and not media.has_libass():
        log.warning(
            "当前 ffmpeg 缺少 libass（ass/subtitles 滤镜），成片不会烧录字幕；"
            "请安装带 libass 的 ffmpeg 并用 FFMPEG_BIN 指向它（macOS: brew install ffmpeg-full）"
        )
    yield
    await runner.shutdown()


app = FastAPI(title="数字人口播 MVP", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

settings.ensure_dirs()
app.mount("/media", StaticFiles(directory=settings.data_dir), name="media")
app.mount("/static", StaticFiles(directory=settings.web_dir), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(settings.web_dir / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    icon = settings.web_dir / "favicon.svg"
    if icon.exists():
        return FileResponse(icon)
    return JSONResponse(status_code=204, content=None)
