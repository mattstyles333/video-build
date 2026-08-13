#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_build.inventory import parse_set_look, render_bin_md  # noqa: E402
from video_build.media import asset_id, fingerprint, iter_assets, kind_of  # noqa: E402


class KindTests(unittest.TestCase):
    def test_extensions(self) -> None:
        self.assertEqual(kind_of(Path("a.MP4")), "video")
        self.assertEqual(kind_of(Path("stills/hero.png")), "image")
        self.assertEqual(kind_of(Path("bed.wav")), "audio")
        self.assertIsNone(kind_of(Path("notes.md")))


class AssetIdTests(unittest.TestCase):
    def test_relative_to_root(self) -> None:
        root = Path("/proj")
        edit = Path("/proj/edit")
        self.assertEqual(asset_id(Path("/proj/broll/street.mp4"), root, edit), "broll/street")

    def test_generated_under_edit(self) -> None:
        root = Path("/proj")
        edit = Path("/proj/edit")
        self.assertEqual(asset_id(Path("/proj/edit/generated/city.png"), root, edit), "generated/city")


class WalkTests(unittest.TestCase):
    def test_skips_edit_and_finds_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stills").mkdir()
            (root / "edit" / "clips_graded").mkdir(parents=True)
            (root / "talk.mp4").write_bytes(b"x")
            (root / "stills" / "hero.png").write_bytes(b"x")
            (root / "edit" / "ignore.mp4").write_bytes(b"x")
            found = {(p.name, k) for p, k in iter_assets(root)}
            self.assertEqual(found, {("talk.mp4", "video"), ("hero.png", "image")})


class LookParseTests(unittest.TestCase):
    def test_set_look(self) -> None:
        self.assertEqual(parse_set_look(['C0103=talking head, warm']), {"C0103": "talking head, warm"})

    def test_bad_set_look(self) -> None:
        with self.assertRaises(ValueError):
            parse_set_look(["nocolon"])


class BinMdTests(unittest.TestCase):
    def test_empty_look_placeholder(self) -> None:
        md = render_bin_md({
            "scanned_at": "now",
            "assets": [{
                "id": "hero",
                "kind": "image",
                "path": "/p/hero.png",
                "width": 100,
                "height": 80,
                "look": "",
            }],
        })
        self.assertIn("_(not yet described)_", md)
        self.assertIn("### hero", md)


class FingerprintTests(unittest.TestCase):
    def test_changes_with_mtime(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            p = Path(f.name)
            f.write(b"abc")
        try:
            a = fingerprint(p)
            p.write_bytes(b"abcd")
            b = fingerprint(p)
            self.assertNotEqual(a, b)
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
