#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

from imagine import (  # noqa: E402
    ImagineError,
    enrich_prompt,
    file_to_data_uri,
    generate_image,
    load_gaps,
    resolve_ref,
    slugify,
    start_edit,
    start_extend,
    start_video,
    tag_voices,
)


class SlugTests(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(slugify("generated/Night Street!!"), "night-street")
        with self.assertRaises(ImagineError):
            slugify("...")


class PromptTests(unittest.TestCase):
    def test_enrich_appends_looks(self) -> None:
        out = enrich_prompt("Night street", [{"look": "warm sodium, wet asphalt"}])
        self.assertIn("Night street", out)
        self.assertIn("warm sodium, wet asphalt", out)

    def test_enrich_noop_without_looks(self) -> None:
        self.assertEqual(enrich_prompt("Night street", [{"id": "x"}]), "Night street")


class RefTests(unittest.TestCase):
    def test_resolve_bin_id_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edit = root / "edit"
            still = root / "stills"
            still.mkdir(parents=True)
            hero = still / "hero.png"
            hero.write_bytes(b"png")
            assets = {"stills/hero": {"id": "stills/hero", "path": str(hero), "look": "red plate"}}
            path, entry = resolve_ref("stills/hero", edit, assets)
            self.assertEqual(path, hero)
            self.assertEqual(entry["look"], "red plate")
            path2, _ = resolve_ref(str(hero), edit, assets)
            self.assertEqual(path2, hero.resolve())

    def test_missing_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ImagineError):
                resolve_ref("nope", Path(tmp), {})


class DataUriTests(unittest.TestCase):
    def test_png_uri(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"abc")
            p = Path(f.name)
        try:
            uri = file_to_data_uri(p)
            self.assertTrue(uri.startswith("data:image/png;base64,"))
            self.assertEqual(base64.b64decode(uri.split(",", 1)[1]), b"abc")
        finally:
            p.unlink(missing_ok=True)


class GapsTests(unittest.TestCase):
    def test_load_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            (edit / "gaps.json").write_text(json.dumps({
                "gaps": [{"slug": "street", "prompt": "night", "refs": ["stills/alley"], "duration": 6}]
            }))
            gaps = load_gaps(edit)
            self.assertEqual(gaps[0]["slug"], "street")


class HttpPayloadTests(unittest.TestCase):
    def test_generate_uses_edits_when_refs(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"img")
            ref = Path(f.name)

        class Resp:
            status_code = 200
            def json(self):
                return {"data": [{"b64_json": base64.b64encode(b"OUT").decode()}]}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return Resp()

        try:
            with patch("imagine.requests.post", fake_post):
                raw = generate_image("key", "hello", refs=[ref])
            self.assertEqual(raw, b"OUT")
            self.assertTrue(captured["url"].endswith("/images/edits"))
            self.assertIn("image", captured["json"])
            self.assertEqual(captured["json"]["prompt"], "hello")
        finally:
            ref.unlink(missing_ok=True)

    def test_generate_multi_ref_uses_images_array(self) -> None:
        refs = []
        for i in range(2):
            ref = Path(tempfile.mkdtemp()) / f"r{i}.png"
            ref.write_bytes(b"img")
            refs.append(ref)

        class Resp:
            status_code = 200
            def json(self):
                return {"data": [{"b64_json": base64.b64encode(b"OUT").decode()}]}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return Resp()

        try:
            with patch("imagine.requests.post", fake_post):
                generate_image("key", "hello", refs=refs)
            self.assertTrue(captured["url"].endswith("/images/edits"))
            self.assertNotIn("image", captured["json"])
            self.assertEqual(len(captured["json"]["images"]), 2)
        finally:
            for ref in refs:
                ref.unlink(missing_ok=True)

    def test_generate_no_ref_is_generations(self) -> None:
        class Resp:
            status_code = 200
            def json(self):
                return {"data": [{"b64_json": base64.b64encode(b"X").decode()}]}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            return Resp()

        with patch("imagine.requests.post", fake_post):
            generate_image("key", "hello", aspect_ratio="9:16")
        self.assertTrue(captured["url"].endswith("/images/generations"))

    def test_start_video_image_vs_refs(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"j")
            img = Path(f.name)

        class Resp:
            status_code = 200
            def json(self):
                return {"request_id": "abc"}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["json"] = json
            return Resp()

        try:
            with patch("imagine.requests.post", fake_post):
                rid = start_video("key", "push in", first_frame=img, duration=6)
            self.assertEqual(rid, "abc")
            self.assertIn("image", captured["json"])
            self.assertNotIn("reference_images", captured["json"])
            with self.assertRaises(ImagineError):
                start_video("key", "x", first_frame=img, ref_images=[img])
            with patch("imagine.requests.post", fake_post):
                start_video("key", "she speaks", first_frame=img, voices=["eve"])
            self.assertEqual(captured["json"]["reference_audios"], [{"voice_id": "eve"}])
            self.assertIn("<AUDIO_0>", captured["json"]["prompt"])
        finally:
            img.unlink(missing_ok=True)

    def test_edit_and_extend_endpoints(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake-mp4")
            vid = Path(f.name)

        class Resp:
            status_code = 200
            def json(self):
                return {"request_id": "rid-1"}

        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return Resp()

        try:
            with patch("imagine.requests.post", fake_post):
                start_edit("key", "stormier sky", vid)
            self.assertTrue(captured["url"].endswith("/videos/edits"))
            self.assertIn("video", captured["json"])
            self.assertTrue(captured["json"]["video"]["url"].startswith("data:video/mp4;base64,"))
            with patch("imagine.requests.post", fake_post):
                start_extend("key", "keep walking", vid, duration=5)
            self.assertTrue(captured["url"].endswith("/videos/extensions"))
            self.assertEqual(captured["json"]["duration"], 5)
        finally:
            vid.unlink(missing_ok=True)

    def test_extend_duration_bounds(self) -> None:
        from imagine import run_revise

        with tempfile.TemporaryDirectory() as tmp:
            edit = Path(tmp)
            for bad in (1, 11):
                with self.assertRaises(ImagineError):
                    run_revise(edit, "x", "keep walking", "",
                               kind="extend", duration=bad, model="m", force=False)

    def test_moderation_raises(self) -> None:
        class Resp:
            status_code = 200
            def json(self):
                return {"respect_moderation": False, "data": []}

        with patch("imagine.requests.post", lambda *a, **k: Resp()):
            with self.assertRaises(ImagineError):
                generate_image("key", "blocked")


if __name__ == "__main__":
    unittest.main()
