#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video_build.session import (  # noqa: E402
    SessionError,
    confirm,
    inspect,
    require_confirmed,
    strategy_state,
)


class SessionGateTests(unittest.TestCase):
    def test_draft_confirm_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            self.assertEqual(strategy_state(edit), "missing")
            (edit / "strategy.md").write_text("# Strategy\n> **UNCONFIRMED DRAFT**\n\nGo.\n")
            self.assertEqual(strategy_state(edit), "draft")
            with self.assertRaises(SessionError):
                require_confirmed(edit, action="render")
            require_confirmed(edit, force=True, action="render")
            confirm(edit)
            self.assertEqual(strategy_state(edit), "confirmed")
            self.assertNotIn("UNCONFIRMED", (edit / "strategy.md").read_text())
            (edit / "strategy.md").write_text("# Strategy\nchanged\n")
            self.assertEqual(strategy_state(edit), "stale")
            info = inspect(edit)
            self.assertFalse(info["ready"])
            self.assertEqual(info["strategy"], "stale")


class PictureParseTests(unittest.TestCase):
    def test_picture_shapes(self) -> None:
        from video_build.render import parse_picture
        self.assertIsNone(parse_picture({"source": "a"}))
        self.assertEqual(parse_picture({"picture": "hero"})["source"], "hero")
        p = parse_picture({"picture": {"source": "hero", "start": 1.5, "kenburns": True}})
        self.assertEqual(p["start"], 1.5)
        self.assertTrue(p["kenburns"])
