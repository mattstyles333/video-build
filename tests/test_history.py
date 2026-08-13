#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

from history import (  # noqa: E402
    FOOTAGE_GITIGNORE,
    init_footage,
    list_snapshots,
    ranges_window,
    resolve_snapshot,
    restore_beat,
    restore_full,
    slugify,
    snapshot,
)


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        path.write_text(json.dumps(data, indent=2) + "\n")
    else:
        path.write_text(str(data))


class SlugTests(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(slugify("Tighter hook!"), "tighter-hook")


class WindowTests(unittest.TestCase):
    def test_window(self) -> None:
        ranges = [
            {"source": "a", "start": 0, "end": 5, "beat": "HOOK"},
            {"source": "b", "start": 0, "end": 4, "beat": "CITY"},
            {"source": "c", "start": 1, "end": 3, "beat": "CTA"},
        ]
        self.assertEqual(ranges_window(ranges, "CITY"), (5.0, 9.0))


class BeatRestoreTests(unittest.TestCase):
    def test_replace_middle_beat_and_shift_later_overlay(self) -> None:
        current = {
            "ranges": [
                {"source": "a", "start": 0, "end": 5, "beat": "HOOK"},
                {"source": "newcity", "start": 0, "end": 8, "beat": "CITY"},
                {"source": "c", "start": 0, "end": 3, "beat": "CTA"},
            ],
            "overlays": [
                {"file": "hook.png", "start_in_output": 0.5, "duration": 2.0, "beat": "HOOK"},
                {"file": "city-new.png", "start_in_output": 5.5, "duration": 2.0, "beat": "CITY"},
                {"file": "cta.png", "start_in_output": 13.0, "duration": 2.0, "beat": "CTA"},
            ],
        }
        snapshot_edl = {
            "ranges": [
                {"source": "a", "start": 0, "end": 5, "beat": "HOOK"},
                {"source": "oldcity", "start": 0, "end": 4, "beat": "CITY"},
                {"source": "c", "start": 0, "end": 3, "beat": "CTA"},
            ],
            "overlays": [
                {"file": "city-old.png", "start_in_output": 5.2, "duration": 1.5, "beat": "CITY"},
            ],
        }
        out = restore_beat(current, snapshot_edl, "CITY")
        city = [r for r in out["ranges"] if r["beat"] == "CITY"]
        self.assertEqual(len(city), 1)
        self.assertEqual(city[0]["source"], "oldcity")
        self.assertEqual(out["total_duration_s"], 12.0)
        files = [o["file"] for o in out["overlays"]]
        self.assertIn("city-old.png", files)
        self.assertNotIn("city-new.png", files)
        cta = next(o for o in out["overlays"] if o["file"] == "cta.png")
        # CITY shrank 8 → 4, later overlay shifts -4 (13 → 9)
        self.assertEqual(cta["start_in_output"], 9.0)
        hook = next(o for o in out["overlays"] if o["file"] == "hook.png")
        self.assertEqual(hook["start_in_output"], 0.5)

    def test_missing_beat_errors(self) -> None:
        with self.assertRaises(ValueError):
            restore_beat({"ranges": []}, {"ranges": []}, "CITY")


class SnapshotRoundtripTests(unittest.TestCase):
    def test_snapshot_restore_and_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edit = root / "edit"
            edit.mkdir()
            write(edit / "edl.json", {
                "ranges": [{"source": "a", "start": 0, "end": 2, "beat": "HOOK"}],
                "overlays": [],
            })
            write(edit / "strategy.md", "# Strategy\nfirst\n")
            write(edit / "gaps.json", {"gaps": []})
            (edit / "generated").mkdir()
            write(edit / "generated" / "street.json", {"slug": "street", "prompt": "night"})

            dest = snapshot(edit, "first cut")
            self.assertTrue((dest / "edl.json").exists())
            self.assertTrue((dest / "generated" / "street.json").exists())
            self.assertEqual(list_snapshots(edit)[0], dest)

            write(edit / "edl.json", {"ranges": [{"source": "b", "start": 1, "end": 9, "beat": "HOOK"}]})
            write(edit / "strategy.md", "# Strategy\nchanged\n")
            restore_full(edit, dest)
            self.assertIn("first", (edit / "strategy.md").read_text())
            self.assertEqual(json.loads((edit / "edl.json").read_text())["ranges"][0]["source"], "a")

            snapshot(edit, "second")
            self.assertEqual(resolve_snapshot(edit, "2").name[:3], "002")

            init_footage(root, use_git=False)
            gi = (root / ".gitignore").read_text()
            self.assertIn("video-build: version the program", gi)
            self.assertIn("*.mp4", gi)
            self.assertIn("!edit/history/", gi)
            self.assertIn(FOOTAGE_GITIGNORE.splitlines()[0], gi)


if __name__ == "__main__":
    unittest.main()
