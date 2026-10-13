#!/usr/bin/env python3
"""Tests for gSSURGO water-retention conversion to EcoSIM's fine-earth basis."""

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_gssurgo_profile as gssurgo


class FineEarthWaterContentTests(unittest.TestCase):
    def test_whole_soil_percent_is_volumetric_without_rock(self):
        self.assertAlmostEqual(gssurgo.fine_earth_water_content(10.0, 0.0), 0.10)

    def test_rock_fraction_is_removed_from_volume(self):
        # Spivey (US-xGR) surface horizon: 7.9 % whole soil, 55 % rock
        self.assertAlmostEqual(gssurgo.fine_earth_water_content(7.9, 0.55), 0.079 / 0.45)

    def test_not_multiplied_by_bulk_density(self):
        self.assertLess(gssurgo.fine_earth_water_content(20.0, 0.0), 0.2 + 1e-12)

    def test_missing_rock_and_extreme_rock_leave_whole_soil_value(self):
        self.assertAlmostEqual(gssurgo.fine_earth_water_content(12.0, float("nan")), 0.12)
        self.assertAlmostEqual(gssurgo.fine_earth_water_content(12.0, 0.97), 0.12)

    def test_missing_water_is_nan(self):
        self.assertTrue(math.isnan(gssurgo.fine_earth_water_content(float("nan"), 0.1)))


if __name__ == "__main__":
    unittest.main()
