#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

from video_build.render import (  # noqa: E402
    SUB_FORCE_STYLE,
    apply_caption_case,
    apply_transition_sugar,
    build_master_srt,
    build_overlay_filters,
    duck_linear_gain,
    duck_volume_expr,
    force_style_from_edl,
    overlay_input_args,
    speech_windows_from_edl,
    video_fade_filter,
    wants_duck,
)


class TransitionTests(unittest.TestCase):
    def test_fade_sugar_sets_neighbors(self) -> None:
        ranges = [
            {"source": "a", "start": 0, "end": 5, "transition_out": "fade", "transition_duration": 0.5},
            {"source": "b", "start": 1, "end": 4},
        ]
        out = apply_transition_sugar(ranges)
        self.assertEqual(out[0]["fade_out"], 0.5)
        self.assertEqual(out[1]["fade_in"], 0.5)
        self.assertNotIn("fade_in", ranges[1])  # no mutate original

    def test_cut_is_noop(self) -> None:
        ranges = [{"source": "a", "start": 0, "end": 2}]
        out = apply_transition_sugar(ranges)
        self.assertNotIn("fade_out", out[0])


class FadeFilterTests(unittest.TestCase):
    def test_both_edges(self) -> None:
        f = video_fade_filter(4.0, 0.4, 0.4)
        self.assertIn("fade=t=in:st=0:d=0.400", f)
        self.assertIn("fade=t=out:st=3.600:d=0.400", f)


class SubtitleStyleTests(unittest.TestCase):
    def test_default_matches_constant(self) -> None:
        self.assertEqual(force_style_from_edl({}), SUB_FORCE_STYLE)

    def test_map_overrides(self) -> None:
        style = force_style_from_edl({"subtitle_style": {"size": 22, "margin_v": 70, "case": "natural"}})
        self.assertIn("FontSize=22", style)
        self.assertIn("MarginV=70", style)

    def test_raw_string(self) -> None:
        self.assertEqual(force_style_from_edl({"subtitle_style": "FontName=Menlo"}), "FontName=Menlo")

    def test_caption_case(self) -> None:
        self.assertEqual(apply_caption_case("we fixed this", "upper"), "WE FIXED THIS")
        self.assertEqual(apply_caption_case("we fixed this", "natural"), "we fixed this")


class OverlayFilterTests(unittest.TestCase):
    def test_fullscreen_default(self) -> None:
        parts, last = build_overlay_filters([
            {"file": "x.mp4", "start_in_output": 1.5, "duration": 2.0},
        ])
        self.assertEqual(last, "[v1]")
        self.assertTrue(any("setpts=PTS-STARTPTS+1.5/TB" in p for p in parts))
        self.assertTrue(any("overlay=x=0:y=0:enable='between(t,1.500,3.500)'" in p for p in parts))

    def test_geometry_and_fade(self) -> None:
        parts, _ = build_overlay_filters([
            {
                "file": "lt.png",
                "start_in_output": 0.0,
                "duration": 4.0,
                "x": 80,
                "y": 100,
                "w": 400,
                "opacity": 0.8,
                "fade_in": 0.25,
                "fade_out": 0.25,
            },
        ])
        joined = ";".join(parts)
        self.assertIn("scale=400:-2", joined)
        self.assertIn("colorchannelmixer=aa=0.800", joined)
        self.assertIn("fade=t=in:st=0.000:d=0.250:alpha=1", joined)
        self.assertIn("overlay=x=80:y=100", joined)

    def test_image_overlay_loops(self) -> None:
        args = overlay_input_args(
            {"file": "/tmp/card.png", "duration": 3.0, "start_in_output": 0},
            Path("/tmp"),
        )
        self.assertEqual(args[:4], ["-loop", "1", "-framerate", "24"])


class DuckTests(unittest.TestCase):
    def _edl(self, ranges: list[dict]) -> tuple[dict, Path]:
        return {
            "sources": {"hero": "/tmp/hero.png", "talk": "/tmp/talk.mp4"},
            "ranges": ranges,
        }, Path("/tmp")

    def test_stills_do_not_duck_speech_windows(self) -> None:
        edl, edit_dir = self._edl([
            {"source": "hero", "start": 0, "end": 2.0},
            {"source": "talk", "start": 1.0, "end": 3.0},
        ])
        self.assertEqual(speech_windows_from_edl(edl, edit_dir), [(2.0, 4.0)])

    def test_duck_flag_overrides(self) -> None:
        edl, edit_dir = self._edl([
            {"source": "talk", "start": 0, "end": 2.0, "duck": False},
            {"source": "hero", "start": 0, "end": 2.0, "duck": True},
        ])
        self.assertEqual(speech_windows_from_edl(edl, edit_dir), [(2.0, 4.0)])

    def test_default_gain_is_8db(self) -> None:
        self.assertAlmostEqual(duck_linear_gain({}), 10 ** (-8 / 20), places=6)
        self.assertAlmostEqual(duck_linear_gain({"duck_db": 6}), 10 ** (-6 / 20), places=6)

    def test_volume_expr_ducks_inside_windows(self) -> None:
        expr = duck_volume_expr(0.22, 0.3981, [(2.0, 4.0)])
        self.assertIn("0.220000*if(between(t,2.000,4.000),0.3981,1)", expr)

    def test_no_windows_keeps_level(self) -> None:
        self.assertEqual(duck_volume_expr(0.22, 0.3981, []), "0.220000")

    def test_wants_duck(self) -> None:
        self.assertTrue(wants_duck({"duck": True}))
        self.assertTrue(wants_duck({"duck_db": 4}))
        self.assertFalse(wants_duck({"loop": True}))


class MasterSrtTests(unittest.TestCase):
    def test_skips_stills_and_applies_style(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            still = edit / "hero.png"
            still.write_bytes(b"x")
            tr_dir = edit / "transcripts"
            tr_dir.mkdir()
            (tr_dir / "talk.json").write_text(json.dumps({
                "words": [
                    {"type": "word", "text": "we", "start": 1.0, "end": 1.2},
                    {"type": "word", "text": "fixed", "start": 1.25, "end": 1.5},
                    {"type": "word", "text": "this.", "start": 1.55, "end": 1.8},
                ]
            }))
            edl = {
                "sources": {"hero": str(still), "talk": str(edit / "talk.mp4")},
                "ranges": [
                    {"source": "hero", "start": 0, "end": 2.0},
                    {"source": "talk", "start": 1.0, "end": 2.0},
                ],
                "subtitle_style": {"chunk_words": 3, "case": "natural"},
            }
            out = edit / "master.srt"
            build_master_srt(edl, edit, out)
            text = out.read_text()
            self.assertIn("we fixed this.", text)
            # still is 2s, so first speech cue starts at ~2.00 not 0.00
            self.assertIn("00:00:02,000", text)


if __name__ == "__main__":
    unittest.main()
