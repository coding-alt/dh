"""API serializers: task/avatar projections used by the H5 client."""
from __future__ import annotations

import unittest

from app.api.routes import avatar_public, task_public


def task(**overrides) -> dict:
    base = {
        "id": "task_1",
        "kind": "generate",
        "status": "processing",
        "stage": "driving",
        "progress": 44,
        "avatar_id": "av_1",
        "title": "测试",
        "text": "先完成，再完美。",
        "audio_url": "https://cdn/audio.mp3",
        "video_url": None,
        "srt_path": None,
        "remote_task_id": "r_1",
        "audio_duration": 3.21,
        "error": None,
        "logs": [{"at": 1, "message": "提交智影"}],
        "meta": {"voice_id": "S_x"},
        "created_at": 1,
        "updated_at": 2,
    }
    base.update(overrides)
    return base


class TaskPublicTest(unittest.TestCase):
    def test_stage_label_and_watermark(self):
        data = task_public(task())
        self.assertEqual(data["stage_label"], "智影口型驱动")
        self.assertEqual(data["watermark"], "AI 生成")
        self.assertEqual(data["duration"], 3.21)

    def test_links_only_when_assets_exist(self):
        data = task_public(task())
        self.assertIsNone(data["download_url"])
        self.assertIsNone(data["srt_url"])

        done = task_public(
            task(
                status="success",
                stage="done",
                video_url="http://x/media/outputs/a.mp4",
                srt_path="/data/subtitles/a.srt",
            )
        )
        self.assertEqual(done["download_url"], "/api/download/task_1")
        self.assertEqual(done["srt_url"], "/api/srt/task_1")

    def test_failed_stage_is_exposed(self):
        data = task_public(
            task(status="failed", stage="failed", meta={"failed_stage": "driving"})
        )
        self.assertEqual(data["failed_stage"], "driving")
        self.assertEqual(data["stage_label"], "生成失败")

    def test_logs_are_capped(self):
        logs = [{"at": i, "message": f"m{i}"} for i in range(40)]
        data = task_public(task(logs=logs))
        self.assertEqual(len(data["logs"]), 12)
        self.assertEqual(data["logs"][-1]["message"], "m39")

    def test_missing_duration_is_zero(self):
        self.assertEqual(task_public(task(audio_duration=None))["duration"], 0)


class AvatarPublicTest(unittest.TestCase):
    def test_ready_avatar_exposes_reusable_assets(self):
        avatar = avatar_public(
            {
                "id": "av_1",
                "name": "我的数字人",
                "status": "ready",
                "voice_id": "S_abc",
                "ref_video_url": "https://cos/ref.mp4",
                "preview_url": "https://cos/thumb.jpg",
                "duration": 96.42,
                "reuse_count": 3,
                "error": None,
                "meta": {"sample_duration": 24.0},
                "created_at": 1,
            }
        )
        self.assertEqual(avatar["voice_id"], "S_abc")
        self.assertEqual(avatar["ref_video_url"], "https://cos/ref.mp4")
        self.assertEqual(avatar["duration"], 96.42)
        self.assertEqual(avatar["sample_seconds"], 24.0)
        self.assertEqual(avatar["reuse_count"], 3)

    def test_null_meta_is_safe(self):
        avatar = avatar_public(
            {"id": "av_2", "name": "n", "status": "processing", "meta": None}
        )
        self.assertIsNone(avatar["sample_seconds"])
        self.assertEqual(avatar["reuse_count"], 0)
        self.assertEqual(avatar["duration"], 0)


if __name__ == "__main__":
    unittest.main()
