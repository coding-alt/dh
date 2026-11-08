"""MiniMax request shapes: file upload → voice_clone → t2a_v2."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.providers.base import ProviderError
from app.providers.minimax import MiniMaxVoiceProvider, _new_voice_id

ORIGIN = "https://api.minimaxi.com"
UPLOAD_URL = f"{ORIGIN}/v1/files/upload"
CLONE_URL = f"{ORIGIN}/v1/voice_clone"
TTS_URL = f"{ORIGIN}/v1/t2a_v2"


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class FakeClient:
    """Records every POST and replays a per-URL handler."""

    calls: list[tuple[str, dict]] = []
    handler = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url: str, **kwargs):
        FakeClient.calls.append((url, kwargs))
        return FakeClient.handler(url, kwargs)


def settings(**overrides) -> Settings:
    base = dict(
        minimax_api_key="test-key",
        minimax_origin=ORIGIN,
        minimax_model="speech-2.8-hd",
    )
    base.update(overrides)
    return Settings(**base)


def ok(payload: dict) -> dict:
    payload.setdefault("base_resp", {"status_code": 0, "status_msg": "success"})
    return payload


class CloneFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeClient.calls = []
        self.sample = Path(tempfile.mkdtemp()) / "s.wav"
        self.sample.write_bytes(b"RIFFfakewav")

    async def test_uploads_then_clones(self):
        def handler(url: str, kwargs: dict) -> FakeResponse:
            if url == UPLOAD_URL:
                return FakeResponse(200, ok({"file": {"file_id": 987654321}}))
            return FakeResponse(200, ok({"demo_audio": ""}))

        FakeClient.handler = handler
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient), patch(
            "app.providers.minimax.duration_of", return_value=24.0
        ):
            result = await MiniMaxVoiceProvider(settings()).clone(self.sample, "dh_av_123")

        self.assertEqual([url for url, _ in FakeClient.calls], [UPLOAD_URL, CLONE_URL])
        upload = FakeClient.calls[0][1]
        self.assertEqual(upload["data"], {"purpose": "voice_clone"})
        self.assertEqual(upload["headers"], {"Authorization": "Bearer test-key"})
        self.assertIn("file", upload["files"])

        clone = FakeClient.calls[1][1]
        self.assertEqual(clone["json"]["file_id"], 987654321)
        self.assertEqual(clone["json"]["voice_id"], result.voice_id)
        self.assertNotIn("text", clone["json"])  # 不触发试听计费
        self.assertEqual(clone["headers"]["Authorization"], "Bearer test-key")

    async def test_configured_voice_id_is_reused(self):
        FakeClient.handler = lambda url, kw: FakeResponse(
            200, ok({"file": {"file_id": 1}}) if url == UPLOAD_URL else ok({})
        )
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient), patch(
            "app.providers.minimax.duration_of", return_value=24.0
        ):
            result = await MiniMaxVoiceProvider(
                settings(minimax_voice_id="custom_voice_01")
            ).clone(self.sample, "whatever")
        self.assertEqual(result.voice_id, "custom_voice_01")

    async def test_short_sample_is_rejected_before_upload(self):
        with patch("app.providers.minimax.duration_of", return_value=4.0):
            with self.assertRaises(ProviderError) as ctx:
                await MiniMaxVoiceProvider(settings()).clone(self.sample, "x")
        self.assertIn("10", ctx.exception.message)
        self.assertEqual(FakeClient.calls, [])

    async def test_auth_failure_surfaces_vendor_message(self):
        FakeClient.handler = lambda url, kw: FakeResponse(
            200, {"base_resp": {"status_code": 1004, "status_msg": "authentication failed"}}
        )
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient), patch(
            "app.providers.minimax.duration_of", return_value=24.0
        ):
            with self.assertRaises(ProviderError) as ctx:
                await MiniMaxVoiceProvider(settings()).clone(self.sample, "x")
        self.assertIn("authentication failed", ctx.exception.message)
        self.assertEqual(ctx.exception.code, 1004)

    async def test_silent_sample_gets_actionable_message(self):
        FakeClient.handler = lambda url, kw: FakeResponse(
            200,
            {
                "base_resp": {
                    "status_code": 2013,
                    "status_msg": "invalid params, biz error: code=406, msg=tts fail, "
                    "err = get no valid audio clips from openplatform/x.wav",
                }
            },
        )
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient), patch(
            "app.providers.minimax.duration_of", return_value=24.0
        ):
            with self.assertRaises(ProviderError) as ctx:
                await MiniMaxVoiceProvider(settings()).clone(self.sample, "x")
        self.assertIn("没有可用人声", ctx.exception.message)


class SynthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeClient.calls = []
        self.dest = Path(tempfile.mkdtemp()) / "out.mp3"

    async def test_payload_and_hex_decoding(self):
        audio = b"fake-mp3-bytes"
        FakeClient.handler = lambda url, kw: FakeResponse(
            200,
            ok(
                {
                    "data": {"audio": audio.hex(), "status": 2},
                    "extra_info": {"audio_length": 11124},
                }
            ),
        )
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient):
            result = await MiniMaxVoiceProvider(settings()).synthesize(
                "你好", "dh_voice_1", self.dest, speed=1.2
            )

        self.assertEqual(self.dest.read_bytes(), audio)
        self.assertAlmostEqual(result.duration, 11.124)

        url, kwargs = FakeClient.calls[0]
        self.assertEqual(url, TTS_URL)
        body = kwargs["json"]
        self.assertEqual(body["model"], "speech-2.8-hd")
        self.assertEqual(body["text"], "你好")
        self.assertFalse(body["stream"])
        self.assertEqual(body["output_format"], "hex")
        self.assertEqual(body["voice_setting"]["voice_id"], "dh_voice_1")
        self.assertAlmostEqual(body["voice_setting"]["speed"], 1.2)
        self.assertEqual(body["audio_setting"]["format"], "mp3")

    async def test_speed_is_clamped(self):
        FakeClient.handler = lambda url, kw: FakeResponse(
            200, ok({"data": {"audio": b"x".hex()}, "extra_info": {}})
        )
        provider = MiniMaxVoiceProvider(settings())
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient):
            await provider.synthesize("hi", "v", self.dest, speed=9)
        self.assertEqual(FakeClient.calls[0][1]["json"]["voice_setting"]["speed"], 2.0)

    async def test_empty_audio_is_an_error(self):
        FakeClient.handler = lambda url, kw: FakeResponse(200, ok({"data": None}))
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient):
            with self.assertRaises(ProviderError) as ctx:
                await MiniMaxVoiceProvider(settings()).synthesize("hi", "v", self.dest)
        self.assertIn("未返回音频", ctx.exception.message)

    async def test_missing_key_fails_before_request(self):
        with self.assertRaises(ProviderError) as ctx:
            await MiniMaxVoiceProvider(settings(minimax_api_key="")).synthesize(
                "hi", "v", self.dest
            )
        self.assertIn("VERASPEAK_MINIMAX_API_KEY", ctx.exception.message)
        self.assertEqual(FakeClient.calls, [])


class UrlTest(unittest.TestCase):
    def test_group_id_is_appended_when_configured(self):
        with patch("app.providers.minimax.httpx.AsyncClient", FakeClient):
            provider = MiniMaxVoiceProvider(settings(minimax_group_id="123456"))
        self.assertEqual(provider._url("/v1/t2a_v2"), f"{TTS_URL}?GroupId=123456")

    def test_no_group_id_keeps_url_clean(self):
        provider = MiniMaxVoiceProvider(settings())
        self.assertEqual(provider._url("/v1/t2a_v2"), TTS_URL)

    def test_generated_voice_id_is_valid(self):
        voice_id = _new_voice_id("dh_av_069b5797bd3842dc")
        self.assertGreaterEqual(len(voice_id), 8)
        self.assertTrue(voice_id[0].isalpha())
        self.assertRegex(voice_id, r"^[A-Za-z][A-Za-z0-9_-]*[A-Za-z0-9]$")

    def test_non_latin_name_still_starts_with_letter(self):
        self.assertTrue(_new_voice_id("测试音色")[0].isalpha())


class ErrorMessageTest(unittest.TestCase):
    def test_insufficient_balance_is_explained(self):
        import httpx

        from app.providers.base import ProviderError
        from app.providers.minimax import _raise_for_status

        response = httpx.Response(200, json={})
        data = {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}
        with self.assertRaises(ProviderError) as ctx:
            _raise_for_status(response, data, "语音合成")
        self.assertIn("余额不足", ctx.exception.message)
        self.assertEqual(ctx.exception.code, 1008)


if __name__ == "__main__":
    unittest.main()
