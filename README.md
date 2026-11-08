# 数字人口播 MVP

一次性上传 1–2 分钟真人视频，生成**可无限复用的数字人分身**；之后每次只输入文案，就产出 9:16 竖屏、带字幕、带「AI 生成」标识的口播成片。

- 后端：Python 3.11 + FastAPI + SQLite（无账号体系、无消息队列、无 GPU）
- 前端：移动端 H5（原生 JS/CSS，无需构建）
- 声音：MiniMax · 声音快速复刻 + Speech 2.8 HD 合成
- 口型：腾讯智影 · 数智人「视频免训练」
- 存储：腾讯云 COS（公网可读），Mock 模式落本地磁盘

```
① 建分身（只做一次）
   上传 1–2min 视频 → ffmpeg 抽 24s 干净人声 → MiniMax voice_clone → voice_id
                     → ffmpeg 转 9:16 H.264 → 上传 COS → ref_video_url
                     → 落库 { voice_id, ref_video_url }

② 生成口播（每次复用）
   文案 → MiniMax t2a_v2（复用 voice_id）→ audio_url
        → 智影 videomakenotrain（DriverType=OriginalVoice）→ 轮询 getprogress
        → 下载成片 + 字幕 → 压 ASS 字幕与「AI 生成」→ 9:16 mp4
```

---

## 快速开始（零密钥，先跑通全流程）

```bash
cd /path/to/dh

python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# ffmpeg（字幕烧录需要 libass）
brew install ffmpeg          # macOS：注意 Homebrew 的 ffmpeg 默认不含 libass
brew install ffmpeg-full     # 需要烧录字幕时用它，并在 .env 里把 FFMPEG_BIN 指过去
# sudo apt install ffmpeg    # Ubuntu

cp .env.example .env
MOCK_PROVIDERS=1 .venv/bin/python run.py
```

打开 <http://127.0.0.1:8000>：

1. 首页上传任意 5–120s 带音轨视频（仓库自带 `samples/demo_source.mp4` 可用来测试）；
2. 等分身变为「就绪」；之后可在「我的分身」列表里点「新建分身」继续添加，互不覆盖；
3. 在列表里选「使用此分身创作」→ 输入文案 → 生成 → 进度页 → 成片播放/下载。

Mock 模式用 macOS `say` 合成语音、循环参考视频模拟口型，**不调用任何外部接口**，用于验证上传、转码、字幕、播放器和复用逻辑。首页顶部会显示 `MOCK 演示` 标记。

> 没有 `say` 命令的 Linux 环境，Mock 会退化为静音音轨，流程仍可跑通。

---

## 真实模式配置

```bash
cp .env.example .env
# 填入下方变量后
.venv/bin/python run.py
```

### 必填

| 变量 | 说明 |
| --- | --- |
| `VERASPEAK_MINIMAX_API_KEY` | MiniMax 控制台 API Key（声音复刻 + TTS 鉴权） |
| `TENCENT_APPKEY` | 智影 aPaaS `appkey` |
| `TENCENT_ACCESSTOKEN` | 智影 aPaaS `accesstoken`（HMAC 签名密钥） |
| `TENCENT_APPID` | 智影应用 ID（部分环境需拼进 query，见 `TENCENT_QUERY_APPID`） |
| `COS_SECRET_ID` / `COS_SECRET_KEY` | 腾讯云 API 密钥 |
| `COS_REGION` | 如 `ap-guangzhou` |
| `COS_BUCKET` | 如 `bucket-1250000000` |
| `PUBLIC_BASE_URL` | 服务对外地址（有 COS 时仅用于本地预览链接） |

### 常用可选

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MOCK_PROVIDERS` | `0` | `1` = 全本地模拟 |
| `VERASPEAK_MINIMAX_ORIGIN` | `https://api.minimaxi.com` | 接口域名（国际站为 `https://api.minimax.io`） |
| `VERASPEAK_MINIMAX_MODEL` | `speech-2.8-hd` | 合成模型，可换 `speech-2.8-turbo` 等 |
| `VERASPEAK_MINIMAX_GROUP_ID` | 空 | 国内账号要求时把 `GroupId` 拼进 query |
| `VERASPEAK_MINIMAX_VOICE_ID` | 空 | 指定已复刻音色，留空每次自动生成 |
| `TENCENT_BASE_URL` | `https://gw-sg.tvs.qq.com` | 智影网关，国内环境按控制台覆盖 |
| `TENCENT_CONCURRENCY_TYPE` | 空 | `Exclusive` / `Shared`，留空由平台决定 |
| `TENCENT_CALLBACK_URL` | 空 | 预留回调，当前实现走轮询 |
| `COS_CDN_BASE` | 空 | 自定义 CDN 域名，替换默认 bucket 域名 |
| `COS_PREFIX` | `digital-human` | COS 对象前缀 |
| `COS_ACL` | `public-read` | 上传后对象 ACL（桶已开公有读可忽略） |
| `CLONE_SEGMENT_SECONDS` | `24` | 送给声音复刻的人声长度（MiniMax 要求 10–300s） |
| `MAX_VIDEO_SECONDS` | `120` | 上传视频上限（超出 +30s 才拒绝） |
| `MAX_TEXT_CHARS` | `1200` | 单条文案字数上限 |
| `BURN_SUBTITLES` | `1` | 是否把字幕 + 「AI 生成」压进成片 |
| `TTS_SPEED` | `1.0` | 默认语速倍率（0.5–2.0，前端可单独指定） |
| `POLL_INTERVAL_SECONDS` | `2` | 智影轮询间隔 |
| `POLL_TIMEOUT_SECONDS` | `900` | 智影单任务超时 |
| `DATA_DIR` | `./data` | 数据/成品目录 |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | 自定义 ffmpeg 路径（默认走 PATH） |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | 监听地址 |

启动时日志会打印缺失项：

```bash
curl -s localhost:8000/api/health | python3 -m json.tool
# {"ok":true,"mock":false,"providers":{...},"missing":[...]}
```

### COS 前置检查

- 桶需允许公网读（`public-read`），或配置 `COS_CDN_BASE` 指向可读 CDN；智影必须能直接拉取 `ref_video_url` 与 `audio_url`。
- 两个 URL 都要是 **https、无鉴权参数**、可直接 GET 到 200。
- 未配置 COS 且 `PUBLIC_BASE_URL` 是 localhost 时，`/api/setup_video` 与 `/api/generate` 会直接返回 400 并提示原因，避免把内网地址传给供应商。

---

## 真实资源接入（curl 全流程）

### 1. 建分身

```bash
curl -X POST http://127.0.0.1:8000/api/setup_video \
  -F "file=@/path/to/talking-head.mp4" \
  -F "name=我的数字人"
```

响应：

```json
{
  "job_id": "task_ab12...",
  "avatar_id": "av_9f3c...",
  "status": "processing",
  "stage": "uploaded",
  "stage_label": "视频已上传",
  "duration": 96.4
}
```

### 2. 轮询建分身进度

```bash
curl -s http://127.0.0.1:8000/api/setup_video/task_ab12... | python3 -m json.tool
```

阶段：`uploaded` → `audio`（抽人声）→ `clone`（MiniMax 复刻）→ `transcode`（9:16 H.264）→ `uploading`（COS）→ `done`。

成功后 `avatar` 字段里就是可复用资产：

```json
{
  "id": "av_9f3c...",
  "status": "ready",
  "voice_id": "S_a1b2c3d4",
  "ref_video_url": "https://bucket-1250000000.cos.ap-guangzhou.myqcloud.com/digital-human/avatars/av_9f3c.../ref.mp4",
  "duration": 96.4,
  "reuse_count": 0
}
```

把这两个值记下来 —— 之后**永远不需要再上传视频**。

### 3. 已经有现成资产？直接导入

如果 `voice_id` / `ref_video_url` 是此前在别处创建的，跳过上传和复刻：

```bash
curl -X POST http://127.0.0.1:8000/api/avatars/import \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "已有分身",
    "voice_id": "S_a1b2c3d4",
    "ref_video_url": "https://bucket-1250000000.cos.ap-guangzhou.myqcloud.com/digital-human/avatars/av_9f3c/ref.mp4",
    "preview_url": "https://cdn.example.com/preview.jpg"
  }'
```

要求：`voice_id` 属于当前 `VERASPEAK_MINIMAX_API_KEY` 账号；`ref_video_url` 为 9:16、H.264、公网可读，建议 10–120s、单人口播、正脸、口型清晰、光线均匀。

### 4. 试听音色（可选）

```bash
curl -X POST http://127.0.0.1:8000/api/tts \
  -H 'Content-Type: application/json' \
  -d '{"avatar_id":"av_9f3c...","text":"这是试听文案。"}'
# → {"audio_url":"https://.../audio/preview_xxx.mp3","duration":3.2}
```

### 5. 生成成片

```bash
curl -X POST http://127.0.0.1:8000/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"avatar_id":"av_9f3c...","text":"先完成，再完美。","title":"第一条口播"}'
# → {"task_id":"task_77aa...","status":"pending"}
```

### 6. 轮询任务

```bash
curl -s http://127.0.0.1:8000/api/task/task_77aa... | python3 -m json.tool
```

```json
{
  "stage": "done",
  "status": "success",
  "progress": 100,
  "video_url": "http://127.0.0.1:8000/media/outputs/task_77aa..._final.mp4",
  "download_url": "/api/download/task_77aa...",
  "srt_url": "/api/srt/task_77aa...",
  "duration": 18.6,
  "watermark": "AI 生成"
}
```

阶段推进：`pending` → `tts` → `driving`（智影 `COMMIT/MAKING`）→ `subtitle` → `done`。

成片落在 `data/outputs/{task_id}_final.mp4`：1080×1920、H.264、AAC，烧录字幕 + 右上角「AI 生成」；`data/subtitles/{task_id}.srt` 可单独下载。

```bash
curl -OJ http://127.0.0.1:8000/api/download/task_77aa...
```

---

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 运行模式、供应商、缺失配置 |
| GET | `/api/config` | 前端参数（字数上限、克隆时长等） |
| GET | `/api/avatars` | 分身列表 |
| POST | `/api/setup_video` | 上传视频，异步建分身（multipart：`file`、`name`） |
| GET | `/api/setup_video/{job_id}` | 建分身进度 |
| POST | `/api/avatars/import` | 导入已有 `{voice_id, ref_video_url}` |
| GET | `/api/avatars/{id}` | 分身详情 + 最近成片 |
| PATCH | `/api/avatars/{id}` | 改名 |
| DELETE | `/api/avatars/{id}` | 删除分身记录（不回收 COS 对象） |
| POST | `/api/tts` | 音色试听，返回 `audio_url` |
| POST | `/api/generate` | 提交成片生成任务 |
| GET | `/api/task/{id}` | 任务状态 / 阶段 / 日志 |
| GET | `/api/tasks` | 最近生成任务 |
| GET | `/api/srt/{id}` | 下载 SRT |
| GET | `/api/download/{id}` | 下载成片 |

任务与分身在 `data/app.db`（SQLite）。服务重启时未完成任务会被标记为失败，避免假 `processing`。

---

## 复用验收（≥10 条）

需要服务已启动（Mock 或真实模式均可，真实模式会真实计费）。脚本只用公开 HTTP 接口，不依赖内部数据结构。

```bash
.venv/bin/python scripts/check_reuse.py --count 10
# 指定实例 / 分身 / 自定义文案 / 输出明细
.venv/bin/python scripts/check_reuse.py \
  --base-url http://127.0.0.1:8000 --avatar-id av_9f3c... --count 12 \
  --json-out /tmp/reuse.json
```

判定三条同时满足才输出「通过」：

1. N/N 任务 `success`，且都有成片与字幕；
2. 全部任务使用同一个 `avatar_id`，`ref_video_url` 全程不变（不重新上传）；
3. 分身 `reuse_count` 至少 +N。

Mock 模式实测输出：

```
成功 10/10
参考视频未变: True
reuse_count: 12 → 22（要求 +10）: True
结果: 通过
```

---

## 单元测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

覆盖：

- 智影 aPaaS 签名（官方文档向量 `example_appkey / 1717639699` → `aCNWYzZdplxWVo+JsqzZc9+J9XrwWWITfX3eQpsLVno=`）、参数排序、签名 URL 编码
- MiniMax 请求形态（Bearer 鉴权、multipart 上传、复刻 file_id/voice_id、t2a_v2 hex 解码与错误码）
- 字幕：断句、时间轴覆盖、SRT 生成/解析、脏字幕回退、ASS 转义与「AI 生成」角标
- 存储：COS/CDN URL 拼接、Mock 本地 `/media` 地址、路径越界拒绝
- 离线渲染的参考视频定位：本地文件 / `/media` URL / 路径穿越拒绝
- 接口序列化：阶段名、失败阶段、下载与字幕链接、日志截断

---

## 目录结构

```
app/
  config.py           环境变量 → Settings（含能力检查）
  main.py             FastAPI 入口、静态资源、/media
  db.py               SQLite（avatars / tasks）
  media.py            ffmpeg：抽人声、竖屏转码、SRT/ASS、字幕烧录、缩略图
  storage.py          COS / 本地存储、下载
  providers/
    base.py           VoiceProvider / AvatarProvider 契约
    minimax.py        声音复刻 + 同步 TTS
    zhiying.py        aPaaS 签名、提交、轮询
    mock.py           say + 循环视频
  services/
    avatars.py        阶段一流水线
    generation.py     阶段二流水线
    runner.py         进程内异步任务
  api/routes.py       全部 HTTP 接口
web/                  H5（index.html / styles.css / app.js）
scripts/check_reuse.py 复用验收
tests/                unittest
samples/demo_source.mp4
```

---

## 换供应商

流水线只依赖 `app/providers/base.py` 的两个抽象类：

```python
class VoiceProvider:
    async def clone(self, sample: Path, voice_name: str) -> CloneResult
    async def synthesize(self, text, voice_id, dest, *, speed=1.0) -> SynthResult

class AvatarProvider:
    async def submit(self, ref_video_url, audio_url) -> str
    async def poll(self, remote_task_id) -> DriveStatus
```

接入可灵 / 黑狐等只需：

1. 新建 `app/providers/xxx.py` 实现上述方法；
2. 在 `app/providers/__init__.py` 的 `build_voice_provider` / `build_avatar_provider` 里按配置分支返回；
3. 在 `Settings` 增加对应密钥字段（`.env.example` 同步）。

`services/`、`api/`、前端均无需改动。

---

## 交互与可用性

前端不引入框架与构建步骤，交互细节集中在 `web/app.js` / `web/styles.css`：

| 场景 | 行为 |
| --- | --- |
| 分身管理 | 首页以列表展示全部分身：「新建分身」入口独立于已有分身，不会覆盖；每张卡片可「使用此分身创作」，`⋯` 内支持重命名 / 删除；当前分身带「当前使用」标识，创建中 / 失败有独立状态行 |
| 页面切换 | 按前进/后退方向做 18px 位移 + 淡入，`history.pushState` 维护栈，浏览器返回键可用；每个屏幕记住滚动位置 |
| 首屏加载 | 分身卡、统计、作品列表使用骨架屏（shimmer），数据到达后原位替换，不闪白 |
| 生成提交 | 按钮内嵌 spinner + `aria-busy`，重复点击由 `state.generating` 拦截；成功有震动反馈与「成片已生成」提示，进度环 100% 停留 420ms 再跳转 |
| 文案编辑 | 本地草稿自动保存（停止输入 800ms 后提示）、清空可撤销 5 秒、`Cmd/Ctrl + Enter` 直接生成、超过 900 字变色、1200 字上限 |
| 播放器 | 点击画面播放/暂停；控制条 2.6s 自动隐藏；空格/K 播放、M 静音、←/→ 快退快进 5s、↑/↓ 音量、F 全屏 |
| 作品抽屉 | 拖拽把手下拉关闭、Esc 关闭、打开时焦点锁定在抽屉内并在关闭后归还、背景 `inert` 且禁止滚动 |
| 下载 / 分享 | 下载按 `content-length` 显示百分比；分享优先系统分享面板，20s 无响应自动恢复按钮并提示改用下载 |
| 无障碍 | 屏幕切换设置 `inert`、进度环 `role=progressbar`、阶段变化写入 `aria-live` 区域、Toast 为 `role=status` |

---

## 已知限制与排错

| 现象 | 原因 / 处理 |
| --- | --- |
| 上传后立刻 400「需要公网可访问 URL」 | 未配 COS 且 `PUBLIC_BASE_URL` 是 localhost。配 COS，或用 `MOCK_PROVIDERS=1` 本地体验 |
| 智影报签名错误 | 核对 `TENCENT_APPKEY/ACCESSTOKEN`；确认网关域名与账号区域一致；少数环境需 `TENCENT_QUERY_APPID=1` |
| 智影拉不到视频 | COS 对象必须公网可读；`ref_video_url` 用浏览器无痕窗口能直接打开 |
| 生成失败 `FAIL` | 查看 `GET /api/task/{id}` 的 `error` 与 `logs`；常见为参考视频不是正脸/多人/时长超限 |
| 成片没有字幕 | 供应商未返回 SRT 时会用文案自动生成；确认 `BURN_SUBTITLES=1` 且 ffmpeg 带 libass（`ffmpeg -filters \| grep ass`）。macOS 上 Homebrew 的 `ffmpeg` 已不含 libass，需装 `ffmpeg-full` 并把 `FFMPEG_BIN` 指到 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`；启动日志会提示缺 libass |
| 字幕中文变方块 | 系统缺中文字体，安装 Noto Sans CJK / 苹方，或在 `media.py` 的 `_ASS_HEADER` 改 `Fontname` |
| 并发提交被限流 | 智影按资源并发计费；`TENCENT_CONCURRENCY_TYPE` 设为已购类型，或在业务层排队 |
| MiniMax 复刻报「无克隆权限」 | 控制台需完成实名/克隆权限校验（错误码 2038）；样本需 10–300s、mp3/m4a/wav、≤20MB |
| 视频 7 天后打不开 | 智影临时链接有有效期；本项目会把成片下载到本地/COS，长期保存请自行转存 |
| 端口占用 | `pkill -f "python run.py"` 或改 `.env` 的 `PORT` |

### 合规注意

- 成片默认烧录「AI 生成」角标，前端播放器另有兜底徽标；**请勿关闭**，符合深度合成内容标识要求。
- 声音复刻需获得被克隆人明确授权；上线前应补充上传授权确认、内容审核与实名/账号体系。
- 本项目为本地 MVP：无鉴权、无多租户隔离，请勿直接暴露到公网。
