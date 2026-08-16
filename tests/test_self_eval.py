#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_build.self_eval import run_self_eval
from video_build.timeline_view import edl_segments


class EdlSegmentTests(unittest.TestCase):
    def test_output_offsets(self) -> None:
        edl = {
            "sources": {"a": "/a.mp4", "b": "/b.png"},
            "ranges": [
                {"source": "a", "start": 0, "end": 5, "beat": "HOOK"},
                {"source": "b", "start": 0, "end": 3, "beat": "STILL"},
            ],
        }
        segs = edl_segments(edl)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["output_start"], 0.0)
        self.assertEqual(segs[0]["output_end"], 5.0)
        self.assertEqual(segs[1]["output_start"], 5.0)
        self.assertEqual(segs[1]["beat"], "STILL")


class SelfEvalTests(unittest.TestCase):
    def test_requires_rendered_video(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            edl = edit / "edl.json"
            edl.write_text(json.dumps({
                "sources": {"talk": str(edit / "talk.mp4")},
                "ranges": [{"source": "talk", "start": 0, "end": 2, "beat": "HOOK"}],
            }))
            (edit / "talk.mp4").write_bytes(b"x")
            with self.assertRaises(FileNotFoundError):
                run_self_eval(edl)


if __name__ == "__main__":
    unittest.main()
