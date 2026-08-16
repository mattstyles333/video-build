#!/usr/bin/env python3
from __future__ import annotations

import unittest

from video_build.grade import PRESETS, compute_auto_adjustments, compute_auto_filter, get_preset


class GradeTests(unittest.TestCase):
    def test_get_preset_known(self) -> None:
        self.assertIn("contrast=", get_preset("neutral_punch"))

    def test_get_preset_unknown_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_preset("not_a_preset")

    def test_compute_auto_adjustments_dark(self) -> None:
        adj = compute_auto_adjustments({"y_mean": 0.35, "y_std": 0.12, "sat_mean": 0.15})
        self.assertGreater(adj["gamma"], 1.0)
        self.assertGreater(adj["contrast"], 1.0)

    def test_compute_auto_filter_dark_flat(self) -> None:
        filt = compute_auto_filter({"y_mean": 0.35, "y_std": 0.12, "sat_mean": 0.15})
        self.assertIn("gamma=", filt)
        self.assertIn("contrast=", filt)
        self.assertIn("saturation=", filt)

    def test_compute_auto_filter_balanced_is_empty_or_subtle(self) -> None:
        filt = compute_auto_filter({"y_mean": 0.48, "y_std": 0.18, "sat_mean": 0.25})
        self.assertTrue(filt == "" or filt.startswith("eq="))

    def test_all_presets_registered(self) -> None:
        for name in ("subtle", "neutral_punch", "warm_cinematic", "none"):
            self.assertIn(name, PRESETS)


if __name__ == "__main__":
    unittest.main()
