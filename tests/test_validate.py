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

    def test_total_duration_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            talk = edit / "talk.mp4"
            talk.write_bytes(b"x")
            edl = {
                "sources": {"talk": str(talk)},
                "ranges": [{"source": "talk", "start": 0, "end": 5}],
                "total_duration_s": 20,
            }
            with self.assertRaises(ValidationError):
                validate_edl(edl, edit)

    def test_schema_matches_minimal_edl(self) -> None:
        edl = {
            "version": 1,
            "sources": {"a": "/tmp/a.mp4"},
            "ranges": [{"source": "a", "start": 0, "end": 1}],
        }
        jsonschema.validate(edl, SCHEMA)


if __name__ == "__main__":
    unittest.main()
