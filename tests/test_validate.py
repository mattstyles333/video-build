#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from video_build.validate import ValidationError, validate_edl

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "schemas" / "edl.schema.json").read_text()
)


def minimal_edl(talk: str, still: str) -> dict:
    return {
        "version": 1,
        "sources": {"talk": talk, "still": still},
        "ranges": [
            {"source": "talk", "start": 0, "end": 5, "beat": "HOOK"},
            {"source": "still", "start": 0, "end": 3, "beat": "HOLD"},
        ],
        "total_duration_s": 8.0,
    }


class ValidateEdlTests(unittest.TestCase):
    def test_valid_edl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            still = edit / "still.png"
            talk.write_bytes(b"x")
            still.write_bytes(b"x")
            edl = minimal_edl(str(talk), str(still))
            validate_edl(edl, edit)

    def test_missing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = minimal_edl(str(talk), str(edit / "missing.png"))
            with self.assertRaises(ValidationError):
                validate_edl(edl, edit)

    def test_bad_range_times(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = {
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 5, "end": 2}],
            }
            with self.assertRaises(ValidationError):
                validate_edl(edl, edit)

    def test_total_duration_mismatch_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = {
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 0, "end": 5}],
                "total_duration_s": 20,
            }
            warnings = validate_edl(edl, edit, check_word_boundaries=False)
            self.assertTrue(any("total_duration_s" in w for w in warnings))

    def test_word_boundary_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            tr_dir = edit / "transcripts"
            tr_dir.mkdir()
            (tr_dir / "talk.json").write_text(json.dumps({
                "words": [
                    {"type": "word", "text": "hi", "start": 1.0, "end": 1.5},
                ]
            }))
            edl = {
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 1.1, "end": 1.5}],
            }
            warnings = validate_edl(edl, edit)
            self.assertTrue(any("word boundary" in w for w in warnings))

    def test_strict_word_boundary_via_validate_cli_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            tr_dir = edit / "transcripts"
            tr_dir.mkdir()
            (tr_dir / "talk.json").write_text(json.dumps({
                "words": [{"type": "word", "text": "hi", "start": 1.0, "end": 1.5}]
            }))
            edl = {
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 1.1, "end": 1.5}],
            }
            warnings = validate_edl(edl, edit)
            self.assertTrue(warnings)

    def test_schema_via_validate_edl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = {
                "version": 1,
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 0, "end": 1}],
            }
            validate_edl(edl, edit, check_word_boundaries=False)

    def test_invalid_schema_raises(self) -> None:
        with self.assertRaises(ValidationError):
            validate_edl({"ranges": []}, Path("/tmp"), check_files=False)

    def test_skip_subtitle_file_for_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = {
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 0, "end": 5}],
                "subtitles": "master.srt",
            }
            validate_edl(edl, edit, skip_subtitle_file=True)

    def test_schema_matches_minimal_edl(self) -> None:
        edl = {
            "version": 1,
            "sources": {"a": "/tmp/a.mp4"},
            "ranges": [{"source": "a", "start": 0, "end": 1}],
        }
        jsonschema.validate(edl, SCHEMA)


if __name__ == "__main__":
    unittest.main()
