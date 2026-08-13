#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video_build.pack_transcripts import (
    format_time,
    group_into_phrases,
    pack_one_file,
    render_markdown,
)


class PackTranscriptsTests(unittest.TestCase):
    def test_group_breaks_on_silence(self) -> None:
        words = [
            {"type": "word", "text": "Hello", "start": 0.0, "end": 0.3},
            {"type": "spacing", "text": " ", "start": 0.3, "end": 0.8},
            {"type": "word", "text": "world", "start": 0.8, "end": 1.1},
        ]
        phrases = group_into_phrases(words, silence_threshold=0.5)
        self.assertEqual(len(phrases), 2)
        self.assertEqual(phrases[0]["text"], "Hello")
        self.assertEqual(phrases[1]["text"], "world")

    def test_group_breaks_on_speaker_change(self) -> None:
        words = [
            {"type": "word", "text": "Hi", "start": 0.0, "end": 0.2, "speaker_id": "speaker_0"},
            {"type": "word", "text": "there", "start": 0.25, "end": 0.5, "speaker_id": "speaker_1"},
        ]
        phrases = group_into_phrases(words)
        self.assertEqual(len(phrases), 2)

    def test_pack_file_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "C0103.json"
            p.write_text(json.dumps({
                "words": [
                    {"type": "word", "text": "Test", "start": 1.0, "end": 1.5, "speaker_id": "speaker_0"},
                ]
            }))
            name, duration, phrases = pack_one_file(p, 0.5)
            self.assertEqual(name, "C0103")
            self.assertEqual(len(phrases), 1)
            md = render_markdown([(name, duration, phrases)], 0.5)
            self.assertIn("C0103", md)
            self.assertIn(format_time(1.0), md)


if __name__ == "__main__":
    unittest.main()
