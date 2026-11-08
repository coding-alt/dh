"""Reference-video resolution for the offline (mock) renderer."""
from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import settings as default_settings
from app.media import MediaError
from app.services import generation


def run(coro):
    return asyncio.run(coro)


class LocalRefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        (self.data_dir / "uploads").mkdir(parents=True)
        self.settings = dataclasses.replace(
            default_settings,
            data_dir=self.data_dir,
            public_base_url="http://127.0.0.1:8000",
        )
        self.patch = mock.patch.object(generation, "settings", self.settings)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_prefers_local_upload_for_owned_avatar(self):
        local = self.data_dir / "uploads" / "av_1_ref.mp4"
        local.write_bytes(b"video")
        avatar = {"id": "av_1", "ref_video_url": "https://cos.example.com/x.mp4"}
        self.assertEqual(run(generation._local_ref(avatar)).resolve(), local.resolve())

    def test_maps_media_url_into_data_dir(self):
        target = self.data_dir / "uploads" / "imported_ref.mp4"
        target.write_bytes(b"video")
        avatar = {
            "id": "av_2",
            "ref_video_url": "http://127.0.0.1:8000/media/uploads/imported_ref.mp4",
        }
        self.assertEqual(run(generation._local_ref(avatar)).resolve(), target.resolve())

    def test_media_url_with_query_string_is_handled(self):
        target = self.data_dir / "uploads" / "imported_ref.mp4"
        target.write_bytes(b"video")
        avatar = {
            "id": "av_2",
            "ref_video_url": "http://127.0.0.1:8000/media/uploads/imported_ref.mp4?x=1",
        }
        self.assertEqual(run(generation._local_ref(avatar)).resolve(), target.resolve())

    def test_path_traversal_is_rejected(self):
        avatar = {"id": "av_3", "ref_video_url": "http://127.0.0.1:8000/media/../../etc/passwd"}
        with self.assertRaises(MediaError):
            run(generation._local_ref(avatar))

    def test_missing_reference_raises_clear_error(self):
        avatar = {"id": "av_4", "ref_video_url": ""}
        with self.assertRaises(MediaError) as ctx:
            run(generation._local_ref(avatar))
        self.assertIn("参考视频", ctx.exception.args[0])


if __name__ == "__main__":
    unittest.main()
