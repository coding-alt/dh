"""Runtime configuration, loaded from environment variables / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key) or default)
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    web_dir: Path = ROOT / "web"
    database_path: Path = ROOT / "data" / "app.db"

    # ---- http server -----------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    public_base_url: str = "http://127.0.0.1:8000"

    # ---- ffmpeg ----------------------------------------------------------
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    max_upload_mb: int = 500
    max_video_seconds: int = 120
    clone_segment_seconds: int = 24
    burn_subtitles: bool = True

    # ---- minimax (voice clone + TTS) -------------------------------------
    minimax_api_key: str = ""
    minimax_origin: str = "https://api.minimaxi.com"
    minimax_model: str = "speech-2.8-hd"
    # 可选：国内账号若要求把 GroupId 拼进 query；留空则不带
    minimax_group_id: str = ""
    # 可选：指定已复刻好的 voice_id；留空则每次自动生成
    minimax_voice_id: str = ""

    # ---- tencent zhiying (ivh aPaaS) ------------------------------------
    zhiying_base_url: str = "https://gw-sg.tvs.qq.com"
    tencent_appkey: str = ""
    tencent_accesstoken: str = ""
    tencent_appid: str = ""
    tencent_query_appid: bool = False
    tencent_concurrency: str = ""
    tencent_callback_url: str = ""

    # ---- COS -------------------------------------------------------------
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_bucket: str = ""
    cos_acl: str = "public-read"
    cos_cdn_base: str = ""  # e.g. https://cdn.example.com (optional)
    cos_prefix: str = "digital-human"

    # ---- pipeline --------------------------------------------------------
    mock: bool = False
    poll_interval: float = 2.0
    poll_timeout: int = 900
    max_text_chars: int = 1200
    tts_speed: float = 1.0

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(_str("DATA_DIR", str(ROOT / "data"))).expanduser()
        if not data_dir.is_absolute():
            # 相对路径按项目根目录解析，避免依赖启动时的工作目录
            data_dir = (ROOT / data_dir).resolve()
        public = _str("PUBLIC_BASE_URL", f"http://127.0.0.1:{_int('PORT', 8000)}").rstrip("/")
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "app.db",
            host=_str("HOST", "0.0.0.0"),
            port=_int("PORT", 8000),
            public_base_url=public,
            ffmpeg=_str("FFMPEG_BIN", "ffmpeg"),
            ffprobe=_str("FFPROBE_BIN", "ffprobe"),
            max_upload_mb=_int("MAX_UPLOAD_MB", 500),
            max_video_seconds=_int("MAX_VIDEO_SECONDS", 120),
            clone_segment_seconds=_int("CLONE_SEGMENT_SECONDS", 24),
            burn_subtitles=_bool("BURN_SUBTITLES", True),
            minimax_api_key=_str("VERASPEAK_MINIMAX_API_KEY"),
            minimax_origin=_str("VERASPEAK_MINIMAX_ORIGIN", "https://api.minimaxi.com").rstrip("/"),
            minimax_model=_str("VERASPEAK_MINIMAX_MODEL", "speech-2.8-hd"),
            minimax_group_id=_str("VERASPEAK_MINIMAX_GROUP_ID"),
            minimax_voice_id=_str("VERASPEAK_MINIMAX_VOICE_ID"),
            zhiying_base_url=_str("TENCENT_BASE_URL", "https://gw-sg.tvs.qq.com").rstrip("/"),
            tencent_appkey=_str("TENCENT_APPKEY"),
            tencent_accesstoken=_str("TENCENT_ACCESSTOKEN"),
            tencent_appid=_str("TENCENT_APPID"),
            tencent_query_appid=_bool("TENCENT_QUERY_APPID", False),
            tencent_concurrency=_str("TENCENT_CONCURRENCY_TYPE"),
            tencent_callback_url=_str("TENCENT_CALLBACK_URL"),
            cos_secret_id=_str("COS_SECRET_ID"),
            cos_secret_key=_str("COS_SECRET_KEY"),
            cos_region=_str("COS_REGION"),
            cos_bucket=_str("COS_BUCKET"),
            cos_acl=_str("COS_ACL", "public-read"),
            cos_cdn_base=_str("COS_CDN_BASE").rstrip("/"),
            cos_prefix=_str("COS_PREFIX", "digital-human").strip("/"),
            mock=_bool("MOCK_PROVIDERS", False),
            poll_interval=_float("POLL_INTERVAL_SECONDS", 2.0),
            poll_timeout=_int("POLL_TIMEOUT_SECONDS", 900),
            max_text_chars=_int("MAX_TEXT_CHARS", 1200),
            tts_speed=_float("TTS_SPEED", 1.0),
        )

    # ---- derived paths ---------------------------------------------------
    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def subtitle_dir(self) -> Path:
        return self.data_dir / "subtitles"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.upload_dir,
            self.audio_dir,
            self.output_dir,
            self.subtitle_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # ---- capability checks ----------------------------------------------
    @property
    def has_minimax(self) -> bool:
        return bool(self.minimax_api_key)

    @property
    def has_tencent(self) -> bool:
        return bool(self.tencent_appkey and self.tencent_accesstoken)

    @property
    def has_cos(self) -> bool:
        return bool(
            self.cos_secret_id and self.cos_secret_key and self.cos_region and self.cos_bucket
        )

    def missing_for_real_run(self) -> list[str]:
        missing = []
        if not self.has_minimax:
            missing.append("VERASPEAK_MINIMAX_API_KEY")
        if not self.has_tencent:
            missing.append("TENCENT_APPKEY / TENCENT_ACCESSTOKEN")
        if not self.has_cos:
            missing.append("COS_SECRET_ID / COS_SECRET_KEY / COS_REGION / COS_BUCKET")
        return missing

    def media_url(self, relative: str) -> str:
        return f"{self.public_base_url}/media/{relative.lstrip('/')}"


settings = Settings.from_env()
settings.ensure_dirs()
