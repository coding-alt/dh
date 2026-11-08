"""Storage URL building: COS keys vs local /media paths."""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from app.config import Settings, settings as default_settings
from app.storage import (
    CosStorage,
    LocalStorage,
    StorageError,
    build_storage,
    guess_content_type,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        data_dir=Path(tempfile.gettempdir()) / "dh-storage-test",
        public_base_url="https://cdn.example.com",
    )
    base.update(overrides)
    return dataclasses.replace(default_settings, **base)


COS_ENV = dict(
    cos_secret_id="AKIDexample",
    cos_secret_key="secret",
    cos_region="ap-guangzhou",
    cos_bucket="bucket-1250000000",
)


class CosUrlTest(unittest.TestCase):
    def test_default_bucket_url(self):
        storage = CosStorage(make_settings(**COS_ENV))
        self.assertEqual(
            storage._url("digital-human/ref.mp4"),
            "https://bucket-1250000000.cos.ap-guangzhou.myqcloud.com/digital-human/ref.mp4",
        )

    def test_cdn_base_overrides_bucket_url(self):
        storage = CosStorage(
            make_settings(**COS_ENV, cos_cdn_base="https://cdn.example.com")
        )
        self.assertEqual(
            storage._url("digital-human/ref.mp4"),
            "https://cdn.example.com/digital-human/ref.mp4",
        )

    def test_cdn_base_trailing_slash_is_trimmed(self):
        storage = CosStorage(make_settings(**COS_ENV, cos_cdn_base="https://cdn.example.com/"))
        self.assertNotIn("//digital", storage._url("digital-human/a.mp3"))

    def test_missing_credentials_raise(self):
        with self.assertRaises(StorageError):
            CosStorage(make_settings(cos_bucket=""))


class FactoryTest(unittest.TestCase):
    def test_mock_mode_uses_local_storage(self):
        self.assertIsInstance(build_storage(make_settings(mock=True, **COS_ENV)), LocalStorage)

    def test_cos_mode_uses_cos_storage(self):
        self.assertIsInstance(build_storage(make_settings(mock=False, **COS_ENV)), CosStorage)

    def test_no_cos_falls_back_to_local(self):
        self.assertIsInstance(build_storage(make_settings(mock=False, cos_bucket="")), LocalStorage)


class LocalStorageTest(unittest.TestCase):
    def test_serves_files_under_media_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "outputs").mkdir()
            target = data_dir / "outputs" / "demo.mp4"
            target.write_bytes(b"x")
            storage = LocalStorage(
                make_settings(
                    mock=True, data_dir=data_dir, public_base_url="http://127.0.0.1:8000"
                )
            )
            import asyncio

            url = asyncio.run(storage.put_file(target, "outputs/demo.mp4"))
        self.assertEqual(url, "http://127.0.0.1:8000/media/outputs/demo.mp4")

    def test_file_outside_data_dir_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalStorage(make_settings(mock=True, data_dir=Path(tmp) / "data"))
            outside = Path(tmp) / "outside.mp4"
            outside.write_bytes(b"x")
            import asyncio

            with self.assertRaises(StorageError):
                asyncio.run(storage.put_file(outside, "outside.mp4"))


class ContentTypeTest(unittest.TestCase):
    def test_known_types(self):
        self.assertEqual(guess_content_type(Path("a.mp4")), "video/mp4")
        self.assertEqual(guess_content_type(Path("a.mp3")), "audio/mpeg")

    def test_unknown_defaults_to_binary(self):
        self.assertEqual(guess_content_type(Path("a.zzz")), "application/octet-stream")


if __name__ == "__main__":
    unittest.main()
