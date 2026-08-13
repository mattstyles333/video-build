#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_build.timeline_view import find_silences, words_in_range


class TimelineViewTests(unittest.TestCase):
    def test_words_in_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tr = Path(tmp) / "t.json"
            tr.write_text(json.dumps({
                "words": [
                    {"type": "word", "text": "before", "start": 0.0, "end": 0.5},
                    {"type": "word", "text": "inside", "start": 1.0, "end": 1.5},
                    {"type": "word", "text": "after", "start": 3.0, "end": 3.5},
                ]
            }))
            words = words_in_range(tr, 0.8, 2.0)
            self.assertEqual([w["text"] for w in words], ["inside"])

    def test_find_silences(self) -> None:
        words = [
            {"type": "word", "text": "a", "start": 0.0, "end": 0.5},
            {"type": "word", "text": "b", "start": 1.2, "end": 1.6},
        ]
        gaps = find_silences(words, 0.0, 2.0, threshold=0.4)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0][0], 0.5)
        self.assertAlmostEqual(gaps[0][1], 1.2)


if __name__ == "__main__":
    unittest.main()
