#!/usr/bin/env python3
"""API response normalization contract tests (no live API calls)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from video_build.transcribe import grok_to_scribe
from video_build.tts import words_from_timestamps

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class GrokSttContractTests(unittest.TestCase):
    def test_normalizes_words_and_spacing(self) -> None:
        raw = json.loads((FIXTURES / "grok_stt_response.json").read_text())
        out = grok_to_scribe(raw)
        self.assertEqual(out["provider"], "grok")
        self.assertEqual(out["text"], raw["text"])
        types = [w["type"] for w in out["words"]]
        self.assertIn("word", types)
        self.assertIn("spacing", types)
        word = next(w for w in out["words"] if w["type"] == "word")
        self.assertEqual(word["speaker_id"], "speaker_0")
        gap = max(
            (w for w in out["words"] if w["type"] == "spacing"),
            key=lambda w: w["end"] - w["start"],
        )
        self.assertAlmostEqual(gap["start"], 3.60)
        self.assertAlmostEqual(gap["end"], 6.08)

    def test_empty_words(self) -> None:
        out = grok_to_scribe({"text": "", "words": []})
        self.assertEqual(out["words"], [])


class TtsContractTests(unittest.TestCase):
    def test_dict_shaped_graph_times(self) -> None:
        payload = json.loads((FIXTURES / "grok_tts_response.json").read_text())
        words = words_from_timestamps(payload)
        self.assertEqual([w["text"] for w in words], ["We", "fixed", "this"])
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[-1]["end"], 1.20)


if __name__ == "__main__":
    unittest.main()
