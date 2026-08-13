#!/usr/bin/env python3
"""ffmpeg-backed smokes: inventory, graphic, still extract, overlay composite."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

from graphic import render_card, render_lower_third  # noqa: E402
from inventory import build_bin  # noqa: E402
from render import concat_segments, extract_segment, build_final_composite  # noqa: E402


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@unittest.skipUnless(have_ffmpeg(), "ffmpeg required")
class PipelineSmoke(unittest.TestCase):
    def test_inventory_image_and_look(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            edit = root / "edit"
            stills = root / "stills"
            stills.mkdir(parents=True)
            img = Image.new("RGB", (320, 180), (200, 40, 20))
            img.save(stills / "hero.png")
            data = build_bin(root, edit, set_looks={"stills/hero": "red product plate"})
            ids = [a["id"] for a in data["assets"]]
            self.assertEqual(ids, ["stills/hero"])
            self.assertEqual(data["assets"][0]["look"], "red product plate")
            self.assertTrue((edit / data["assets"][0]["thumb"]).exists())
            # second run keeps the look without --set-look
            data2 = build_bin(root, edit)
            self.assertEqual(data2["assets"][0]["look"], "red product plate")

    def test_graphic_and_still_composite(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            still = tmp_p / "hero.png"
            Image.new("RGB", (640, 360), (30, 30, 40)).save(still)
            card = tmp_p / "card.png"
            render_card(card, "3×", "faster", 640, 360, "FF5A00", "FFFFFF", "0A0A0A")
            self.assertTrue(card.exists())
            render_lower_third(tmp_p / "lt.png", "Ada", "Founder", 640, 360, "FF5A00", "FFFFFF", "0A0A0A")

            clip = tmp_p / "hold.mp4"
            extract_segment(still, 0.0, 1.0, "", clip, preview=True)
            self.assertTrue(clip.exists())
            probe = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
                text=True,
            ).strip()
            self.assertGreater(float(probe), 0.8)

            out = tmp_p / "out.mp4"
            build_final_composite(
                clip,
                [{"file": str(card), "start_in_output": 0.0, "duration": 1.0, "fade_in": 0.1}],
                None,
                out,
                tmp_p,
            )
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 1000)

            kb = tmp_p / "kb.mp4"
            extract_segment(still, 0.0, 1.0, "", kb, preview=True, kenburns=True)
            faded = tmp_p / "faded.mp4"
            extract_segment(still, 0.0, 1.0, "", faded, preview=True, fade_in=0.2, fade_out=0.2)
            base = tmp_p / "base.mp4"
            concat_segments([clip, faded], base, tmp_p)
            self.assertTrue(base.exists())

            broll = tmp_p / "broll.mp4"
            extract_segment(
                clip, 0.0, 1.0, "", broll, preview=True,
                picture=card, picture_start=0.0,
            )
            self.assertTrue(broll.exists())
            probe = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(broll)],
                text=True,
            ).strip()
            self.assertGreater(float(probe), 0.5)


if __name__ == "__main__":
    unittest.main()
