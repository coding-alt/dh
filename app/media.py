"""ffmpeg helpers: probing, voice-sample extraction, 9:16 conversion, subtitle burn-in."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from .config import settings
from .logger import log


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], *, timeout: int = 900, cwd: Path | None = None) -> str:
    log.debug("exec: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"找不到命令 {cmd[0]}，请先安装 ffmpeg") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg 执行超时（{timeout}s）") from exc
    stderr = proc.stderr.decode("utf-8", "ignore")
    if proc.returncode != 0:
        tail = "\n".join(stderr.strip().splitlines()[-8:])
        raise MediaError(f"ffmpeg 失败: {tail}")
    return stderr


def probe(path: Path) -> dict:
    try:
        proc = subprocess.run(
            [
                settings.ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise MediaError("找不到 ffprobe，请先安装 ffmpeg") from exc
    if proc.returncode != 0:
        raise MediaError("无法解析媒体文件，请确认上传的是有效视频")
    return json.loads(proc.stdout.decode("utf-8", "ignore") or "{}")


def media_info(path: Path) -> dict:
    data = probe(path)
    fmt = data.get("format") or {}
    video = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
    )
    audio = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None
    )
    duration = float(fmt.get("duration") or 0)
    if not duration and video:
        duration = float(video.get("duration") or 0)
    return {
        "duration": duration,
        "size": int(fmt.get("size") or 0),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "has_audio": audio is not None,
        "has_video": video is not None,
        "fps": (video or {}).get("r_frame_rate"),
    }


def duration_of(path: Path) -> float:
    return media_info(path)["duration"]


def has_libass() -> bool:
    """True when the configured ffmpeg provides the ``ass`` filter (needs libass).

    Homebrew's default ``ffmpeg`` bottle ships without libass, so subtitle burn-in
    silently degrades to "return the raw clip". Check explicitly instead.
    """
    try:
        proc = subprocess.run(
            [settings.ffmpeg, "-hide_banner", "-filters"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        return False
    for line in proc.stdout.decode("utf-8", "ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in {"ass", "subtitles"}:
            return True
    return False


def _speech_start(path: Path) -> float:
    """Roughly skip leading silence so the clone sample starts on speech."""
    try:
        proc = subprocess.run(
            [
                settings.ffmpeg,
                "-hide_banner",
                "-i",
                str(path),
                "-af",
                "silencedetect=noise=-35dB:d=0.8",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=180,
        )
        text = proc.stderr.decode("utf-8", "ignore")
        ends = re.findall(r"silence_end: ([0-9.]+)", text)
        starts = re.findall(r"silence_start: ([0-9.]+)", text)
        if starts and ends and float(starts[0]) <= 0.2 and float(ends[0]) < 2.5:
            return max(0.0, float(ends[0]) - 0.15)
    except Exception:
        pass
    return 0.0


def extract_voice_sample(video: Path, out_wav: Path, seconds: int | None = None) -> dict:
    """Extract a clean 14-30s mono 24k wav sample suitable for voice cloning."""
    seconds = seconds or settings.clone_segment_seconds
    info = media_info(video)
    if not info["has_audio"]:
        raise MediaError("视频没有音轨，无法克隆声音")
    if not info["has_video"]:
        raise MediaError("请上传包含人物的口播视频")

    total = info["duration"]
    start = _speech_start(video)
    if total - start < 5:
        raise MediaError("视频有效语音太短（至少需要 5 秒）")
    length = min(float(seconds), max(5.0, total - start - 0.2))
    if length < 14:
        log.warning("可用语音仅 %.1fs，建议上传 30 秒以上以获得更好的复刻效果", length)

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            settings.ffmpeg,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{length:.3f}",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            "-af",
            "highpass=f=70,loudnorm=I=-18:TP=-2:LRA=11",
            str(out_wav),
        ]
    )
    return {"start": round(start, 2), "duration": round(length, 2)}


def to_vertical_h264(source: Path, dest: Path, *, max_seconds: int | None = None) -> dict:
    """Normalise any input to a 9:16 H.264/AAC mp4 that Tencent can ingest."""
    max_seconds = max_seconds or settings.max_video_seconds
    info = media_info(source)
    duration = min(info["duration"], float(max_seconds))
    if duration < 1:
        raise MediaError("视频时长不足 1 秒")

    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            settings.ffmpeg,
            "-y",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,fps=25,setsar=1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "44100",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    out = media_info(dest)
    return {"duration": out["duration"], "width": out["width"], "height": out["height"]}


def estimate_duration(text: str, speed: float = 1.0) -> float:
    chars = len(re.sub(r"\s+", "", text))
    rate = 4.4 * max(0.5, speed)
    return round(max(1.0, chars / rate), 1)


# --------------------------------------------------------------------------
# subtitles
# --------------------------------------------------------------------------
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;…])")
_SOFT_SPLIT = re.compile(r"(?<=[，,、：:])")


def _split_sentences(text: str, max_len: int = 22) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text.replace("\r", "").strip())
    raw: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for part in _SENTENCE_SPLIT.split(line):
            part = part.strip()
            if part:
                raw.append(part)
    result: list[str] = []
    for sentence in raw:
        if len(sentence) <= max_len:
            result.append(sentence)
            continue
        buffer = ""
        for piece in _SOFT_SPLIT.split(sentence):
            if len(buffer) + len(piece) <= max_len:
                buffer += piece
            else:
                if buffer:
                    result.append(buffer)
                buffer = piece
        if buffer:
            result.append(buffer)
    return result or [text.strip() or " "]


def _ts(seconds: float, *, comma: bool = True) -> str:
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    sep = "," if comma else "."
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _ass_ts(seconds: float) -> str:
    """ASS 时间戳是「时:分:秒.厘秒」，只允许两位小数。

    写成三位（00:00:03.213）时 libass 会把 213 当成 213 厘秒，
    字幕会比语音晚 2 秒多，因此这里单独格式化。
    """
    seconds = max(0.0, seconds)
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def build_cues(text: str, duration: float, *, max_len: int = 22) -> list[dict]:
    sentences = _split_sentences(text, max_len=max_len)
    weights = [max(1, len(re.sub(r"\s+", "", s))) for s in sentences]
    total_weight = sum(weights)
    duration = max(duration, 1.2)
    cues: list[dict] = []
    cursor = 0.0
    for sentence, weight in zip(sentences, weights):
        span = duration * weight / total_weight
        start = cursor
        end = min(duration, cursor + span)
        cursor = end
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": sentence})
    if cues:
        cues[-1]["end"] = round(duration, 3)
    return cues


def srt_from_cues(cues: list[dict]) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        text = cue["text"].replace("\n", " ")
        blocks.append(f"{i}\n{_ts(cue['start'])} --> {_ts(cue['end'])}\n{text}\n")
    return "\n".join(blocks).strip() + "\n"


def srt_from_text(text: str, duration: float) -> str:
    return srt_from_cues(build_cues(text, duration))


def normalize_srt(raw: str, *, fallback_text: str, duration: float) -> str:
    """Keep vendor SRT when usable, otherwise rebuild from the script."""
    raw = (raw or "").strip()
    if "-->" in raw and re.search(r"\d\d:\d\d:\d\d[,.]\d{3}\s*-->", raw):
        return raw if raw.endswith("\n") else raw + "\n"
    return srt_from_text(fallback_text, duration)


_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,PingFang SC,54,&H00FFFFFF,&H00FFFFFF,&H50000000,&H78000000,-1,0,0,0,100,100,0,0,1,2.4,1.6,2,96,96,150,1
Style: Mark,PingFang SC,32,&H00E8E8E8,&H00E8E8E8,&H60000000,&H60000000,0,0,0,0,100,100,2,0,1,0,0,9,48,48,52,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(
    cues: list[dict], duration: float, dest: Path, *, ai_label: bool = True
) -> Path:
    lines = [_ASS_HEADER]
    if ai_label and duration > 0:
        lines.append(
            f"Dialogue: 0,0:00:00.00,{_ass_ts(duration)},Mark,,0,0,0,,AI 生成"
        )
    for cue in cues:
        text = (
            cue["text"]
            .replace("\\", "\\\\")
            .replace("{", "（")
            .replace("}", "）")
            .replace("\n", "\\N")
        )
        lines.append(
            f"Dialogue: 0,{_ass_ts(cue['start'])},{_ass_ts(cue['end'])},"
            f"Sub,,0,0,0,,{text}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


# 智影「视频免训练」会在成片右上角烧录一枚淡淡的 “AI合成” 水印，只出现在前 2 秒。
# 我们自己会烧一版「AI 生成」标识，先抹掉这枚厂商水印，否则成片会同时出现两个 AI 标识、
# 且两者位置重叠导致文字糊成一团。坐标基于 1080x1920 输出画面。
VENDOR_MARK = {"x": 884, "y": 52, "w": 144, "h": 56, "until": 2.05}


def video_filters(ass_name: str, *, strip_vendor_mark: bool = True) -> str:
    """Build the ffmpeg filter graph for the final render."""
    filters = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if strip_vendor_mark:
        mark = VENDOR_MARK
        filters += (
            f",delogo=x={mark['x']}:y={mark['y']}:w={mark['w']}:h={mark['h']}"
            f":enable='lt(t,{mark['until']})'"
        )
    return filters + f",ass={ass_name}"


def burn_subtitles(
    source: Path,
    dest: Path,
    srt_text: str,
    duration: float,
    *,
    strip_vendor_mark: bool = True,
    ai_label: bool = True,
) -> Path:
    """Render subtitles + the AI label into the video (libass based, no GPU needed)."""
    # ffmpeg 以 ass 所在目录为工作目录运行，传给它的输入/输出必须是绝对路径，
    # 否则 DATA_DIR=./data 这类相对配置会导致 “No such file or directory”。
    source = Path(source).resolve()
    dest = Path(dest).resolve()
    cues = parse_srt(srt_text) if srt_text else []
    ass_path = write_ass(
        cues, duration, source.parent / f"{source.stem}.ass", ai_label=ai_label
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    filters = video_filters(ass_path.name, strip_vendor_mark=strip_vendor_mark)
    run(
        [
            settings.ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vf",
            filters,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        cwd=ass_path.parent,
    )
    return dest


def parse_srt(text: str) -> list[dict]:
    cues: list[dict] = []
    pattern = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
    )
    blocks = re.split(r"\n\s*\n", (text or "").replace("\r", "").strip())
    for block in blocks:
        match = pattern.search(block)
        if not match:
            continue
        g = [int(x) for x in match.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = block[match.end() :].strip().replace("\n", " ")
        if body:
            cues.append({"start": start, "end": end, "text": body})
    return cues


def mock_lipsync(ref_video: Path, audio: Path, dest: Path) -> Path:
    """Local stand-in for the lip-sync vendor: loop the reference video over the audio."""
    audio_len = duration_of(audio)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            settings.ffmpeg,
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(ref_video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{max(1.0, audio_len):.3f}",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=25",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    )
    return dest


def make_thumbnail(video: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        run(
            [
                settings.ffmpeg,
                "-y",
                "-ss",
                "1",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-2",
                "-q:v",
                "4",
                str(dest),
            ]
        )
    return dest
