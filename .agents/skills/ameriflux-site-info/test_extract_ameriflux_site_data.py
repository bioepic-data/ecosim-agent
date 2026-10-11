#!/usr/bin/env python3
"""Tests for AmeriFlux site metadata parsing and location cross-checks."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import extract_ameriflux_site_data as site


def page(site_id, name, lat_long, elevation, koppen, mat, description="A flux site."):
    """Page text in the AmeriFlux siteinfo layout: label line, then value line."""
    return "\n".join(
        [
            f"{site_id}: {name}",
            "Lat, Long:",
            lat_long,
            "Elevation(m):",
            elevation,
            "Vegetation IGBP:",
            "WET (Permanent Wetlands)",
            "Climate Koppen:",
            koppen,
            "Mean Annual Temp (°C):",
            mat,
            "Description:",
            description,
        ]
    )


def ecosim(site_id, lat, lon, mat, koppen, description=None, sign_checks=None):
    return {
        "site_name": site_id,
        "ALATG": lat,
        "ALONG": lon,
        "ALTIG": 100.0,
        "ATCAG": mat,
        "IETYPG": site.KOPPEN_MAP[koppen],
        "IXTYP1": 10,
        "_raw": {"climate_code": koppen, "description": description, "sign_checks": sign_checks or {}},
    }


class SignParsingTests(unittest.TestCase):
    def test_negative_values_on_their_own_line_keep_sign(self):
        text = page(
            "BR-Npw", "Northern Pantanal Wetland", "-16.4980, -56.4120", "120", "Aw (Tropical savanna)", "24.9",
            "The site is located in the northern Pantanal in central South America.",
        )
        raw = site.parse_site_metadata_from_text(text, "BR-Npw", "test")
        self.assertEqual((raw["latitude"], raw["longitude"]), (-16.498, -56.412))
        self.assertEqual(raw["sign_checks"]["latitude"], "matched")

    def test_negative_mat_and_elevation_keep_sign(self):
        text = page("US-Brw", "Barrow", "71.3225, -156.6092", "-4", "ET (Tundra)", "-12.6")
        raw = site.parse_site_metadata_from_text(text, "US-Brw", "test")
        self.assertEqual(raw["MAT"], -12.6)
        self.assertEqual(raw["elevation"], -4.0)

    def test_bullet_dash_is_still_removed(self):
        self.assertEqual(site.clean_line("- Site description "), "Site description")
        self.assertEqual(site.clean_line("-16.4980, -56.4120"), "-16.4980, -56.4120")
        self.assertEqual(site.clean_line("Lat, Long: |"), "Lat, Long")

    def test_sign_check_flags_lost_sign(self):
        text = page("CL-SDF", "Senda Darwin", "-41.8830, -73.6760", "50", "Cfb", "10")
        raw = {"latitude": 41.883, "longitude": -73.676, "elevation": 50.0, "MAT": 10.0}
        checks = site.check_signs_against_page(text, raw)
        self.assertEqual(checks["latitude"], "sign_mismatch")
        self.assertEqual(checks["longitude"], "matched")


class LocationValidationTests(unittest.TestCase):
    def test_correct_southern_site_passes(self):
        data = ecosim("BR-Npw", -16.498, -56.412, 24.9, "Aw", "northern Pantanal in central South America")
        result = site.validate_site_location("BR-Npw", data)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checks"]["description_region"], "passed")

    def test_lost_latitude_sign_fails_country_envelope_with_hint(self):
        data = ecosim("BR-Npw", 16.498, -56.412, 24.9, "Aw", "northern Pantanal in central South America")
        result = site.validate_site_location("BR-Npw", data)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["checks"]["country_bounds"], "failed")
        self.assertIn("opposite sign", result["errors"][0])
        self.assertEqual(result["checks"]["description_region"], "failed")

    def test_page_sign_mismatch_is_an_error(self):
        data = ecosim("US-Myb", 38.05, -121.77, 15.9, "Csa", sign_checks={"elevation": "sign_mismatch"})
        result = site.validate_site_location("US-Myb", data)
        self.assertEqual(result["status"], "failed")
        self.assertIn("elevation", result["errors"][0])

    def test_lost_mat_sign_warns_for_tundra(self):
        data = ecosim("US-Brw", 71.3225, -156.6092, 12.6, "ET")
        result = site.validate_site_location("US-Brw", data)
        self.assertEqual(result["status"], "warnings")
        self.assertEqual(result["checks"]["koppen_consistency"], "failed")

    def test_unknown_country_prefix_is_skipped(self):
        data = ecosim("XX-Abc", 10.0, 10.0, 25.0, "Aw")
        result = site.validate_site_location("XX-Abc", data)
        self.assertTrue(result["checks"]["country_bounds"].startswith("skipped"))


class CacheTests(unittest.TestCase):
    def test_cached_metadata_failing_checks_is_refetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = ecosim("BR-Npw", 16.498, -56.412, 24.9, "Aw")
            Path(tmp, "BR-Npw_ecosim_site.json").write_text(json.dumps(bad))
            fresh_raw = {
                "site_name": "Northern Pantanal Wetland", "latitude": -16.498, "longitude": -56.412,
                "elevation": 120.0, "MAT": 24.9, "climate_code": "Aw", "igbp_type": "WSA",
                "description": None, "source": "test", "sign_checks": {},
            }
            with mock.patch.object(site, "fetch_and_parse", return_value=fresh_raw) as fetch, \
                    redirect_stderr(io.StringIO()) as stderr:
                result = site.extract_site_info("BR-Npw", output_dir=tmp)
            fetch.assert_called_once()
            self.assertIn("refetching", stderr.getvalue())
            self.assertEqual(result["ALATG"], -16.498)
            saved = json.loads(Path(tmp, "BR-Npw_ecosim_site.json").read_text())
            self.assertEqual(saved["ALATG"], -16.498)

    def test_failed_fresh_extraction_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            wrong_raw = {
                "site_name": "Senda Darwin", "latitude": 41.883, "longitude": -73.676,
                "elevation": 50.0, "MAT": 10.0, "climate_code": "Cfb", "igbp_type": "EBF",
                "description": None, "source": "test", "sign_checks": {},
            }
            with mock.patch.object(site, "fetch_and_parse", return_value=wrong_raw):
                with self.assertRaises(ValueError):
                    site.extract_site_info("CL-SDF", output_dir=tmp, force_refresh=True)
            self.assertFalse(Path(tmp, "CL-SDF_ecosim_site.json").exists())


if __name__ == "__main__":
    unittest.main()
