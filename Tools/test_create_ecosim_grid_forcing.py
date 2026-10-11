#!/usr/bin/env python3
"""Tests for soil-profile gap filling in create_ecosim_grid_forcing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import create_ecosim_grid_forcing as grid

FILL = grid.SOIL_FILL_VALUE


def profile():
    # Organic O horizon (layers 1-2) over sand, as gSSURGO returns for US-UMB.
    return {
        "CDPTH": [0.01, 0.04, 0.07, 0.14, 0.25, 0.51],
        "BKDSI": [FILL, FILL, 1.31, 1.39, 1.46, 1.58],
        "FC": [0.05, 0.05, 0.14, 0.16, 0.15, 0.11],
        "WP": [FILL, FILL, 0.09, 0.07, 0.05, 0.02],
        "SCNV": [1080.0, 1080.0, 492.0, 198.0, 198.0, 198.0],
        "SCNH": [1080.0, 1080.0, 492.0, 198.0, 198.0, 198.0],
        "CSAND": [FILL, FILL, 910.0, 921.0, 926.0, 970.0],
        "CSILT": [FILL, FILL, 70.0, 59.0, 52.0, 20.0],
        "ROCK": [FILL, FILL, 0.03, 0.03, 0.03, 0.03],
        "PH": [FILL, FILL, 4.8, 4.9, 5.1, 6.1],
        "CEC": [92.0, 92.0, 92.0, FILL, 2.8, 0.8],
        "CORGC": [493.0, 493.0, 71.0, 13.7, 8.3, 2.9],
    }


class GapFillTests(unittest.TestCase):
    def test_organic_layers_get_organic_defaults(self):
        filled, _ = grid.gap_fill_soil_profile(profile(), 20)
        self.assertEqual(filled["BKDSI"][:2], [grid.ORGANIC_BULK_DENSITY] * 2)
        self.assertEqual(filled["ROCK"][:2], [0.0, 0.0])

    def test_mineral_properties_copied_from_nearest_layer(self):
        filled, notes = grid.gap_fill_soil_profile(profile(), 20)
        self.assertEqual(filled["CSAND"][:2], [910.0, 910.0])
        self.assertEqual(filled["PH"][:2], [4.8, 4.8])
        self.assertIn("PH layer 1: copied from layer 3", notes)

    def test_model_estimated_variables_use_negative_sentinel(self):
        filled, _ = grid.gap_fill_soil_profile(profile(), 20)
        self.assertEqual(filled["WP"][:2], [-1.0, -1.0])
        self.assertEqual(filled["CEC"][3], -1.0)

    def test_template_defaults_replace_fill_only_variables(self):
        filled, _ = grid.gap_fill_soil_profile(profile(), 20)
        self.assertEqual(filled["FHOL"], [0.15, 0.15, 0.15, 0.15, 0.15, 0.0])
        self.assertEqual(filled["AEC"], [grid.DEFAULT_AEC] * 6)
        self.assertAlmostEqual(filled["CORGR"][0], grid.DEFAULT_CORGR_FRACTION * 493.0)

    def test_no_required_value_left_invalid(self):
        filled, _ = grid.gap_fill_soil_profile(profile(), 20)
        for varname in grid.REQUIRED_SOIL_RANGES:
            for value in filled[varname]:
                self.assertTrue(grid.in_range(varname, value), (varname, value))

    def test_missing_depths_raise(self):
        data = profile()
        data["CDPTH"][2] = FILL
        with self.assertRaises(ValueError):
            grid.gap_fill_soil_profile(data, 20)


if __name__ == "__main__":
    unittest.main()
