#!/usr/bin/env python3
"""Golden-file tests for ffmpeg filter string generation (no ffmpeg required)."""

from __future__ import annotations

import unittest

from video_build.render import (
    apply_transition_sugar,
    audio_mix_filter_parts,
    build_overlay_filters,
    duck_volume_expr,
    force_style_from_edl,
    video_fade_filter,
)

# Expected fragments — stable contracts for the render pipeline.
GOLDEN = {
    "fade_both_edges": "fade=t=in:st=0:d=0.400,fade=t=out:st=3.600:d=0.400",
    "overlay_pts_shift": "setpts=PTS-STARTPTS+1.5/TB",
    "overlay_enable": "overlay=x=0:y=0:enable='between(t,1.500,3.500)'",
    "duck_expr": "0.220000*if(between(t,2.000,4.000),0.3981,1)",
    "subtitle_default_font": "FontName=Helvetica,FontSize=18,Bold=1",
    "transition_fade_out": 0.5,
}


class GoldenFilterTests(unittest.TestCase):
    def test_video_fade_filter(self) -> None:
        self.assertEqual(video_fade_filter(4.0, 0.4, 0.4), GOLDEN["fade_both_edges"])

    def test_overlay_filters(self) -> None:
        parts, last = build_overlay_filters([
            {"file": "x.mp4", "start_in_output": 1.5, "duration": 2.0},
        ])
        joined = ";".join(parts)
        self.assertIn(GOLDEN["overlay_pts_shift"], joined)
        self.assertIn(GOLDEN["overlay_enable"], joined)
        self.assertEqual(last, "[v1]")

    def test_duck_volume_expr(self) -> None:
        expr = duck_volume_expr(0.22, 0.3981, [(2.0, 4.0)])
        self.assertEqual(expr, GOLDEN["duck_expr"])

    def test_audio_mix_delay(self) -> None:
        parts = audio_mix_filter_parts([{"file": "vo.wav", "start_in_output": 1.5}])
        self.assertIn("adelay=1500:all=1", ";".join(parts))

    def test_subtitle_force_style(self) -> None:
        style = force_style_from_edl({})
        self.assertIn(GOLDEN["subtitle_default_font"], style)

    def test_transition_sugar(self) -> None:
        ranges = [
            {"source": "a", "start": 0, "end": 5, "transition_out": "fade", "transition_duration": 0.5},
            {"source": "b", "start": 1, "end": 4},
        ]
        out = apply_transition_sugar(ranges)
        self.assertEqual(out[0]["fade_out"], GOLDEN["transition_fade_out"])
        self.assertEqual(out[1]["fade_in"], GOLDEN["transition_fade_out"])


if __name__ == "__main__":
    unittest.main()
