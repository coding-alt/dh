"""Subtitle timing/formatting helpers (no ffmpeg required)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.media import (
    build_cues,
    estimate_duration,
    normalize_srt,
    parse_srt,
    srt_from_text,
    write_ass,
)

SCRIPT = "先完成，再完美。写作是最低成本的思考。把复杂的事讲简单。"


class EstimateDurationTest(unittest.TestCase):
    def test_chinese_speech_rate(self):
        # 4.4 chars per second, whitespace ignored.
        self.assertAlmostEqual(estimate_duration("一" * 44), 10.0)
        self.assertAlmostEqual(estimate_duration("一 二 三 四 五 六 七 八 九"), 2.0)

    def test_faster_speed_shortens_duration(self):
        self.assertLess(estimate_duration(SCRIPT, 1.5), estimate_duration(SCRIPT, 1.0))

    def test_never_below_one_second(self):
        self.assertGreaterEqual(estimate_duration("嗯"), 1.0)


class CuesTest(unittest.TestCase):
    def test_split_on_sentence_punctuation(self):
        cues = build_cues(SCRIPT, 9.0)
        self.assertEqual([c["text"] for c in cues], ["先完成，再完美。", "写作是最低成本的思考。", "把复杂的事讲简单。"])

    def test_cues_cover_duration_without_gaps(self):
        cues = build_cues(SCRIPT, 9.0)
        self.assertEqual(cues[0]["start"], 0.0)
        self.assertEqual(cues[-1]["end"], 9.0)
        for prev, nxt in zip(cues, cues[1:]):
            self.assertAlmostEqual(prev["end"], nxt["start"])

    def test_long_sentence_is_wrapped(self):
        cues = build_cues("这是一个非常非常长的句子，" * 4, 12.0, max_len=12)
        self.assertTrue(all(len(c["text"]) <= 14 for c in cues))
        self.assertGreater(len(cues), 1)

    def test_single_sentence_spans_whole_clip(self):
        cues = build_cues("只有一句话", 5.5)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["end"], 5.5)


class SrtTest(unittest.TestCase):
    def test_generated_srt_round_trips(self):
        cues = parse_srt(srt_from_text(SCRIPT, 9.0))
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[0]["text"], "先完成，再完美。")
        self.assertAlmostEqual(cues[-1]["end"], 9.0)

    def test_timestamp_format(self):
        srt = srt_from_text("一", 1.234)
        self.assertIn("00:00:00,000 --> 00:00:01,234", srt)

    def test_vendor_srt_is_kept(self):
        vendor = "1\n00:00:01,000 --> 00:00:03,500\nvendor line\n"
        self.assertEqual(normalize_srt(vendor, fallback_text=SCRIPT, duration=9.0), vendor)

    def test_vendor_srt_with_dot_separator_is_kept(self):
        vendor = "1\n00:00:01.000 --> 00:00:03.500\nvendor line\n"
        self.assertIn("vendor line", normalize_srt(vendor, fallback_text=SCRIPT, duration=9.0))

    def test_garbage_falls_back_to_script(self):
        out = normalize_srt("not a subtitle file", fallback_text=SCRIPT, duration=9.0)
        self.assertIn("先完成，再完美。", out)
        self.assertEqual(len(parse_srt(out)), 3)


class AssTest(unittest.TestCase):
    def test_ass_contains_ai_label_and_cues(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sub.ass"
            write_ass(build_cues(SCRIPT, 9.0), 9.0, dest)
            body = dest.read_text(encoding="utf-8")
        self.assertIn("[V4+ Styles]", body)
        self.assertIn("AI 生成", body)
        self.assertIn("先完成，再完美。", body)
        self.assertEqual(body.count("Dialogue:"), 4)  # 3 cues + watermark

    def test_braces_are_neutralised(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sub.ass"
            write_ass(build_cues("{测试}台词", 2.0), 2.0, dest)
            body = dest.read_text(encoding="utf-8")
        self.assertNotIn("{测试}", body)
        self.assertIn("（测试）台词", body)

    def test_ass_timestamps_use_centiseconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sub.ass"
            write_ass([{"start": 3.213, "end": 6.16, "text": "测试"}], 6.16, dest)
            body = dest.read_text(encoding="utf-8")
        self.assertIn("0:00:03.21,0:00:06.16,Sub", body)
        self.assertIn("0:00:00.00,0:00:06.16,Mark", body)
        # ASS 只接受两位厘秒，三位会被 libass 当成厘秒导致字幕迟到
        self.assertNotRegex(body, r"Dialogue: 0,[^\n]*\.\d{3},")

    def test_label_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sub.ass"
            write_ass(build_cues(SCRIPT, 9.0), 9.0, dest, ai_label=False)
            body = dest.read_text(encoding="utf-8")
        self.assertNotIn("AI 生成", body)
        self.assertEqual(body.count("Dialogue:"), 3)  # 只有 3 条字幕

    def test_no_cues_and_no_label_writes_no_dialogue(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "sub.ass"
            write_ass([], 9.0, dest, ai_label=False)
            body = dest.read_text(encoding="utf-8")
        self.assertIn("[Events]", body)
        self.assertEqual(body.count("Dialogue:"), 0)


class VideoFilterTest(unittest.TestCase):
    def test_vendor_mark_is_blurred_only_in_the_first_seconds(self):
        from app.media import VENDOR_MARK, video_filters

        graph = video_filters("sub.ass", strip_vendor_mark=True)
        self.assertIn("delogo=", graph)
        self.assertIn(f"x={VENDOR_MARK['x']}", graph)
        self.assertIn("enable='lt(t,", graph)
        self.assertTrue(graph.endswith(",ass=sub.ass"))
        # 水印框必须完整落在 1080x1920 画面内
        self.assertLessEqual(VENDOR_MARK["x"] + VENDOR_MARK["w"], 1080)
        self.assertLessEqual(VENDOR_MARK["y"] + VENDOR_MARK["h"], 1920)

    def test_mock_render_keeps_the_frame_untouched(self):
        from app.media import video_filters

        graph = video_filters("sub.ass", strip_vendor_mark=False)
        self.assertNotIn("delogo", graph)


class SettingsPathTest(unittest.TestCase):
    def test_relative_data_dir_is_anchored_to_project_root(self):
        import os
        from unittest.mock import patch

        from app.config import ROOT, Settings

        with patch.dict(os.environ, {"DATA_DIR": "./data"}, clear=False):
            settings = Settings.from_env()
        self.assertTrue(settings.data_dir.is_absolute())
        self.assertEqual(settings.data_dir, (ROOT / "data").resolve())
        self.assertTrue(settings.output_dir.is_absolute())


if __name__ == "__main__":
    unittest.main()
