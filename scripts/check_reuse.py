#!/usr/bin/env python3
"""复用验收：同一个分身连续生成 N 条视频，中途不上传任何素材。

用法（服务需已启动）：
    python scripts/check_reuse.py                       # 默认 http://127.0.0.1:8000，10 条
    python scripts/check_reuse.py --count 12
    python scripts/check_reuse.py --base-url http://127.0.0.1:8001 --avatar-id av_xxx

判定标准（全部满足才算通过）：
  1. 每条任务最终 status == success，且产出视频 + 字幕；
  2. 全部任务共用同一个 avatar_id，其 ref_video_url 在整轮里没有变化；
  3. 分身的 reuse_count 至少增加 N。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import httpx

DEFAULT_TEXTS = [
    "先完成，再完美。",
    "写作是最低成本的思考。",
    "把复杂的事讲简单，是一种能力。",
    "坚持比天赋更稀缺。",
    "每天进步一点点，一年就是三十七倍。",
    "复盘让经验变成能力。",
    "慢就是快，少就是多。",
    "注意力是这个时代最稀缺的资源。",
    "别用战术上的勤奋，掩盖战略上的懒惰。",
    "长期主义不是等待，而是持续做对的事。",
]


async def pick_avatar(client: httpx.AsyncClient, base: str, avatar_id: str | None) -> dict:
    if avatar_id:
        resp = await client.get(f"{base}/api/avatars/{avatar_id}")
        resp.raise_for_status()
        return resp.json()["avatar"]
    resp = await client.get(f"{base}/api/avatars")
    resp.raise_for_status()
    ready = [a for a in resp.json()["items"] if a["status"] == "ready"]
    if not ready:
        raise SystemExit("没有可用的分身，请先调用 /api/setup_video 或 /api/avatars/import")
    return ready[-1]


async def run_one(client: httpx.AsyncClient, base: str, avatar_id: str, text: str, index: int,
                  timeout: float, interval: float) -> dict:
    started = time.time()
    resp = await client.post(
        f"{base}/api/generate",
        json={"avatar_id": avatar_id, "text": text, "title": f"复用验收 {index:02d}"},
    )
    resp.raise_for_status()
    task_id = resp.json()["task_id"]

    stages: list[str] = []
    while True:
        await asyncio.sleep(interval)
        task = (await client.get(f"{base}/api/task/{task_id}")).json()
        if not stages or stages[-1] != task["stage"]:
            stages.append(task["stage"])
        if task["status"] in {"success", "failed"}:
            break
        if time.time() - started > timeout:
            raise SystemExit(f"任务 {task_id} 超时（>{timeout}s）")

    return {
        "n": index,
        "task_id": task_id,
        "status": task["status"],
        "stages": stages,
        "video": bool(task["video_url"]),
        "subtitles": bool(task["srt_url"]),
        "duration": task["duration"],
        "seconds": round(time.time() - started, 1),
        "error": task["error"],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="数字人分身复用验收")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--avatar-id", default=None)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--texts", default=None, help="可选：每行一句文案的文本文件")
    parser.add_argument("--timeout", type=float, default=300.0, help="单条任务超时（秒）")
    parser.add_argument("--interval", type=float, default=1.0, help="轮询间隔（秒）")
    parser.add_argument("--json-out", default=None, help="把结果写入 JSON 文件")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if args.texts:
        texts = [line.strip() for line in open(args.texts, encoding="utf-8") if line.strip()]
        if not texts:
            raise SystemExit("文案文件为空")
    else:
        texts = DEFAULT_TEXTS

    async with httpx.AsyncClient(timeout=60) as client:
        health = (await client.get(f"{base}/api/health")).json()
        print(f"服务 {base} | mock={health['mock']} | 存储={health['providers']['storage']}")

        avatar = await pick_avatar(client, base, args.avatar_id)
        avatar_id = avatar["id"]
        ref_before = avatar["ref_video_url"]
        reuse_before = avatar["reuse_count"]
        print(f"分身 {avatar_id}「{avatar['name']}」 reuse_count={reuse_before}")
        print(f"参考视频 {ref_before}\n")

        results = []
        for index in range(1, args.count + 1):
            text = texts[(index - 1) % len(texts)]
            item = await run_one(
                client, base, avatar_id, text, index, args.timeout, args.interval
            )
            after = (await client.get(f"{base}/api/avatars/{avatar_id}")).json()["avatar"]
            item["ref_same"] = after["ref_video_url"] == ref_before
            item["reuse_count"] = after["reuse_count"]
            results.append(item)
            print(json.dumps(item, ensure_ascii=False))

        final = (await client.get(f"{base}/api/avatars/{avatar_id}")).json()["avatar"]

    ok = all(r["status"] == "success" and r["video"] and r["subtitles"] for r in results)
    same_ref = all(r["ref_same"] for r in results)
    reused = final["reuse_count"] >= reuse_before + args.count
    passed = ok and same_ref and reused

    print("\n" + "=" * 52)
    print(f"成功 {sum(r['status'] == 'success' for r in results)}/{len(results)}")
    print(f"参考视频未变: {same_ref}")
    print(f"reuse_count: {reuse_before} → {final['reuse_count']}（要求 +{args.count}）: {reused}")
    print(f"结果: {'通过' if passed else '未通过'}")
    print("=" * 52)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "base_url": base,
                    "avatar_id": avatar_id,
                    "ref_video_url": ref_before,
                    "reuse_before": reuse_before,
                    "reuse_after": final["reuse_count"],
                    "passed": passed,
                    "results": results,
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
        print(f"明细已写入 {args.json_out}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
