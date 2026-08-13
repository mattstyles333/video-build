#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

from render import (  # noqa: E402
    audio_mix_filter_parts,
    duck_volume_expr,
    is_music_bed,
    speech_windows_from_edl,
)
from strategy import parse_model_json  # noqa: E402
from tts import words_from_timestamps  # noqa: E402
from imagine import tag_voices  # noqa: E402


class TtsWordsTests(unittest.TestCase):
    def test_groups_characters(self) -> None:
        payload = {
            "audio_timestamps": {
                "graph_chars": list("Hi you"),
                "graph_times": [
                    [0.0, 0.1], [0.1, 0.2], [0.2, 0.3],
                    [0.3, 0.35],
                    [0.35, 0.5], [0.5, 0.6], [0.6, 0.8],
                ],
            }
        }
        words = words_from_timestamps(payload)
        self.assertEqual([w["text"] for w in words], ["Hi", "you"])
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[1]["end"], 0.6)

    def test_groups_dict_shaped_times(self) -> None:
        payload = {
            "audio_timestamps": {
                "graph_chars": list("Hi you"),
                "graph_times": [
                    {"start": 0.0, "end": 0.1}, {"start": 0.1, "end": 0.2},
                    {"start": 0.2, "end": 0.3}, {"start": 0.3, "end": 0.35},
                    {"start": 0.35, "end": 0.5}, {"start": 0.5, "end": 0.6},
                    {"start": 0.6, "end": 0.8},
                ],
            }
        }
        words = words_from_timestamps(payload)
        self.assertEqual([w["text"] for w in words], ["Hi", "you"])
        self.assertAlmostEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[1]["end"], 0.6)


class StrategyParseTests(unittest.TestCase):
    def test_fence_and_raw(self) -> None:
        raw = '```json\n{"strategy_md": "# Strategy\\n", "gaps": [{"slug": "x"}]}\n```'
        data = parse_model_json(raw)
        self.assertIn("Strategy", data["strategy_md"])
        self.assertEqual(data["gaps"][0]["slug"], "x")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(Exception):
            parse_model_json('{"nope": 1}')


class VoiceTagTests(unittest.TestCase):
    def test_tag(self) -> None:
        self.assertIn("<AUDIO_0>", tag_voices("Hello", ["eve"]))
        self.assertEqual(tag_voices("Use <AUDIO_0> now", ["eve"]), "Use <AUDIO_0> now")


class AudioMixTests(unittest.TestCase):
    def test_delay_and_volume(self) -> None:
        parts = audio_mix_filter_parts([
            {"file": "vo.wav", "start_in_output": 1.5, "volume": 0.8},
        ])
        joined = ";".join(parts)
        self.assertIn("adelay=1500:all=1", joined)
        self.assertIn("volume=0.800", joined)
        self.assertIn("amix=inputs=2", joined)

    def test_duck_expr_and_bed(self) -> None:
        self.assertTrue(is_music_bed({"loop": True, "file": "m.wav"}))
        self.assertFalse(is_music_bed({"file": "vo.wav"}))
        expr = duck_volume_expr(0.22, 0.3981, [(2.0, 8.0), (10.0, 14.0)])
        self.assertIn("between(t,2.000,8.000)", expr)
        self.assertIn("0.3981", expr)
        parts = audio_mix_filter_parts(
            [{"file": "m.wav", "volume": 0.22, "duck": True, "duck_db": 8}],
            speech_windows=[(1.0, 3.0)],
        )
        joined = ";".join(parts)
        self.assertIn("volume=0.220000*if(between(t\\,1.000\\,3.000)", joined)
        self.assertIn("[0:a][a1]amix=", joined)

    def test_speech_windows_skip_stills(self) -> None:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            still = edit / "hero.png"
            still.write_bytes(b"x")
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = {
                "sources": {"hero": str(still), "talk": str(talk)},
                "ranges": [
                    {"source": "talk", "start": 0, "end": 5, "beat": "HOOK"},
                    {"source": "hero", "start": 0, "end": 3, "beat": "STILL"},
                    {"source": "talk", "start": 5, "end": 8, "beat": "CTA", "duck": False},
                ],
            }
            wins = speech_windows_from_edl(edl, edit)
            self.assertEqual(wins, [(0.0, 5.0)])


if __name__ == "__main__":
    unittest.main()
