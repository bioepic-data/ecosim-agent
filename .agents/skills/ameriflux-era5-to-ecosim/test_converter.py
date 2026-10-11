#!/usr/bin/env python3
"""Tests for AmeriFlux ERA5 conversion and in situ validation."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import numpy as np
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import era5_to_ecosim_converter as converter


class ConverterComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_inputs(self, shortwave_scale=1.0, measured_shortwave_qc=0):
        timestamps = pd.date_range("2001-06-01", periods=96, freq="30min")
        decimal_hour = timestamps.hour + timestamps.minute / 60.0
        daylight = np.sin(np.pi * (decimal_hour - 5.0) / 15.0)
        shortwave = 850.0 * np.maximum(0.0, daylight)
        temperature = 12.0 + 8.0 * np.sin(2.0 * np.pi * (decimal_hour - 7.0) / 24.0)
        wind = 2.5 + 0.5 * np.sin(2.0 * np.pi * decimal_hour / 24.0)
        vpd_hpa = 8.0 + 2.0 * np.maximum(0.0, daylight)
        pressure = np.full(len(timestamps), 90.0)
        precipitation = np.zeros(len(timestamps))
        precipitation[(timestamps.hour == 3) & (timestamps.minute == 0)] = 0.2

        era = pd.DataFrame(
            {
                "TIMESTAMP_START": timestamps.strftime("%Y%m%d%H%M"),
                "TIMESTAMP_END": (timestamps + pd.Timedelta(minutes=30)).strftime("%Y%m%d%H%M"),
                "TA_ERA": temperature,
                "SW_IN_ERA": shortwave * shortwave_scale,
                "LW_IN_ERA": 300.0,
                "VPD_ERA": vpd_hpa,
                "PA_ERA": pressure,
                "P_ERA": precipitation,
                "WS_ERA": wind,
            }
        )
        fullset = pd.DataFrame(
            {
                "TIMESTAMP_START": timestamps.strftime("%Y%m%d%H%M"),
                "TA_F": temperature,
                "TA_F_QC": 0,
                "SW_IN_F": shortwave,
                "SW_IN_F_QC": measured_shortwave_qc,
                "VPD_F": vpd_hpa,
                "VPD_F_QC": 0,
                "PA_F": pressure,
                "PA_F_QC": 0,
                "P_F": precipitation,
                "P_F_QC": 0,
                "WS_F": wind,
                "WS_F_QC": 0,
            }
        )

        era_file = self.root / "AMF_US-Tst_FLUXNET_ERA5_HH_2001-2001_1-1.csv"
        fullset_file = self.root / "AMF_US-Tst_FLUXNET_FULLSET_HH_2001-2001_1-1.csv"
        era.to_csv(era_file, index=False)
        fullset.to_csv(fullset_file, index=False)
        return era_file, fullset_file

    def convert(self, era_file, output_name="forcing.nc", in_situ_file=None):
        output_file = self.root / output_name
        report_file = self.root / f"{output_name}.quality.json"
        report = converter.convert_era5_to_ecosim(
            str(era_file),
            str(output_file),
            longitude=-121.5,
            quality_report_file=str(report_file),
            in_situ_file=str(in_situ_file) if in_situ_file else None,
            comparison_min_pairs=24,
        )
        return report, json.loads(report_file.read_text())

    def test_auto_discovers_matching_fullset(self):
        era_file, fullset_file = self.write_inputs()
        discovered = converter.discover_in_situ_file(str(era_file), "US-Tst")
        self.assertTrue(os.path.samefile(discovered, fullset_file))

    def test_matching_forcing_passes_without_warnings(self):
        era_file, fullset_file = self.write_inputs()
        report, persisted = self.convert(era_file, in_situ_file=fullset_file)
        comparison = report["in_situ_comparison"]

        self.assertEqual(comparison["status"], "compared")
        self.assertEqual(comparison["warnings"], [])
        self.assertEqual(comparison["variables"]["SRADH"]["paired_hours"], 48)
        self.assertLess(comparison["variables"]["SRADH"]["root_mean_square_error"], 1.0e-4)
        self.assertEqual(persisted["in_situ_comparison"]["status"], "compared")

    def test_large_shortwave_underestimate_emits_warning(self):
        era_file, fullset_file = self.write_inputs(shortwave_scale=0.10)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            report, _ = self.convert(era_file, "low_shortwave.nc", fullset_file)

        comparison = report["in_situ_comparison"]
        warning_variables = {warning["variable"] for warning in comparison["warnings"]}
        self.assertIn("SRADH", warning_variables)
        self.assertIn("WARNING: SRADH", stderr.getvalue())
        self.assertLess(
            comparison["variables"]["SRADH"]["relative_mean_bias_fraction"],
            -0.85,
        )

    def test_qc_two_records_are_not_treated_as_in_situ_measurements(self):
        era_file, fullset_file = self.write_inputs(
            shortwave_scale=0.10,
            measured_shortwave_qc=2,
        )
        report, _ = self.convert(era_file, "qc_filtered.nc", fullset_file)
        shortwave = report["in_situ_comparison"]["variables"]["SRADH"]

        self.assertEqual(shortwave["paired_hours"], 0)
        self.assertNotIn(
            "SRADH",
            {warning["variable"] for warning in report["in_situ_comparison"]["warnings"]},
        )

    def test_dwpth_is_vapor_pressure_not_deficit(self):
        era_file, fullset_file = self.write_inputs()
        report, _ = self.convert(era_file, "vapor.nc", fullset_file)
        forcing = converter.read_ecosim_hourly_forcing(report["output_file"])

        era = pd.read_csv(era_file)
        expected = converter.vapor_pressure_from_vpd_kpa(era["TA_ERA"], era["VPD_ERA"])
        hourly_expected = pd.Series(expected).groupby(np.arange(len(expected)) // 2).mean()
        actual = forcing["DWPTH"].dropna().to_numpy()
        np.testing.assert_allclose(actual, hourly_expected.to_numpy()[: len(actual)], rtol=1.0e-5)
        saturation = converter.saturation_vapor_pressure_kpa(forcing["TMPH"].dropna().to_numpy())
        self.assertTrue(np.all(actual <= saturation + 1.0e-6))

    def test_znoong_matches_local_forcing_clock(self):
        era_file, _ = self.write_inputs()
        # Synthetic daylight peaks near 12.5-12.75 h on the CSV clock; at
        # lon -127.5 on UTC-8, mean solar noon is 12.5 h local (20.5 h UTC).
        report = converter.convert_era5_to_ecosim(
            str(era_file),
            str(self.root / "noon.nc"),
            longitude=-127.5,
            compare_in_situ=False,
        )
        solar_noon = report["solar_noon"]

        self.assertEqual(solar_noon["utc_offset_hours"], -8)
        self.assertEqual(solar_noon["utc_offset_source"], "inferred_from_SRADH_diurnal_cycle")
        self.assertAlmostEqual(solar_noon["ZNOONG_hours"], 12.5, places=3)
        self.assertLess(abs(solar_noon["centroid_minus_ZNOONG_hours"]), 0.75)
        self.assertIsNone(solar_noon["warning"])

    def test_wrong_utc_offset_emits_solar_noon_warning(self):
        era_file, _ = self.write_inputs()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            report = converter.convert_era5_to_ecosim(
                str(era_file),
                str(self.root / "utc_noon.nc"),
                longitude=-121.5,
                compare_in_situ=False,
                utc_offset_hours=0,
            )
        self.assertAlmostEqual(report["solar_noon"]["ZNOONG_hours"], 20.1, places=3)
        self.assertIsNotNone(report["solar_noon"]["warning"])
        self.assertIn("WARNING: ZNOONG", stderr.getvalue())

    def test_one_hour_forcing_clock_shift_emits_warning(self):
        era_file, fullset_file = self.write_inputs()
        era = pd.read_csv(era_file, dtype={"TIMESTAMP_START": str, "TIMESTAMP_END": str})
        era["SW_IN_ERA"] = np.roll(era["SW_IN_ERA"].to_numpy(), 2)  # one hour late
        era.to_csv(era_file, index=False)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            report, _ = self.convert(era_file, "shifted.nc", fullset_file)

        shortwave = report["in_situ_comparison"]["variables"]["SRADH"]
        self.assertAlmostEqual(shortwave["diurnal_centroid_lag_hours"], 1.0, places=1)
        self.assertIn("forcing clock likely shifted", shortwave["warning"])

    def write_long_inputs(self, era5_lag_steps=0):
        """60 days of half-hourly data with daily cloudiness; ERA5 optionally late."""
        timestamps = pd.date_range("2001-05-01", periods=60 * 48, freq="30min")
        decimal_hour = timestamps.hour + timestamps.minute / 60.0
        rng = np.random.default_rng(7)
        cloud = np.repeat(rng.uniform(0.3, 1.0, 60), 48)
        shortwave = 850.0 * cloud * np.maximum(0.0, np.sin(np.pi * (decimal_hour - 5.0) / 15.0))
        temperature = 12.0 + 8.0 * np.sin(2.0 * np.pi * (decimal_hour - 7.0) / 24.0)
        base = {
            "TA": temperature,
            "SW_IN": shortwave,
            "VPD": 8.0 + 2.0 * shortwave / 850.0,
            "PA": np.full(len(timestamps), 90.0),
            "P": np.zeros(len(timestamps)),
            "WS": np.full(len(timestamps), 2.5),
        }
        late = {key: np.roll(values, era5_lag_steps) for key, values in base.items()}
        era = pd.DataFrame(
            {
                "TIMESTAMP_START": timestamps.strftime("%Y%m%d%H%M"),
                "TIMESTAMP_END": (timestamps + pd.Timedelta(minutes=30)).strftime("%Y%m%d%H%M"),
                "TA_ERA": late["TA"],
                "SW_IN_ERA": late["SW_IN"],
                "LW_IN_ERA": 300.0,
                "VPD_ERA": late["VPD"],
                "PA_ERA": late["PA"],
                "P_ERA": late["P"],
                "WS_ERA": late["WS"],
            }
        )
        fullset = pd.DataFrame({"TIMESTAMP_START": timestamps.strftime("%Y%m%d%H%M")})
        for key in ("TA", "SW_IN", "VPD", "PA", "P", "WS"):
            fullset[f"{key}_F"] = base[key]
            fullset[f"{key}_F_QC"] = 0
        era_file = self.root / "AMF_US-Lag_FLUXNET_ERA5_HH_2001-2001_1-1.csv"
        fullset_file = self.root / "AMF_US-Lag_FLUXNET_FULLSET_HH_2001-2001_1-1.csv"
        era.to_csv(era_file, index=False)
        fullset.to_csv(fullset_file, index=False)
        return era_file, fullset_file

    def convert_long(self, era_file, name, **kwargs):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            report = converter.convert_era5_to_ecosim(
                str(era_file),
                str(self.root / name),
                longitude=-127.5,
                **kwargs,
            )
        return report, stderr.getvalue()

    def test_late_era5_is_coregistered_with_tower(self):
        era_file, _ = self.write_long_inputs(era5_lag_steps=2)
        report, _ = self.convert_long(era_file, "registered.nc")
        registration = report["time_registration"]

        self.assertEqual(registration["status"], "corrected")
        self.assertEqual(registration["lag_hours"], 1.0)
        self.assertEqual(registration["applied_shift_hours"], -1.0)
        self.assertGreater(registration["correlation_at_lag"], registration["correlation_at_zero_lag"])
        shortwave = report["in_situ_comparison"]["variables"]["SRADH"]
        self.assertLess(abs(shortwave["diurnal_centroid_lag_hours"]), 0.1)
        self.assertNotIn("warning", shortwave)
        # Record span is kept: no spill into the previous day/year.
        forcing = converter.read_ecosim_hourly_forcing(report["output_file"])
        self.assertEqual(forcing["timestamp_start"].min(), pd.Timestamp("2001-05-01 00:00"))
        self.assertEqual(int(forcing["SRADH"].notna().sum()), 60 * 24)

    def test_check_mode_reports_without_shifting(self):
        era_file, _ = self.write_long_inputs(era5_lag_steps=2)
        report, stderr = self.convert_long(era_file, "checked.nc", time_registration="check")
        registration = report["time_registration"]

        self.assertEqual(registration["status"], "misregistered")
        self.assertEqual(registration["applied_shift_hours"], 0.0)
        self.assertIn("not corrected", stderr)
        self.assertAlmostEqual(
            report["in_situ_comparison"]["variables"]["SRADH"]["diurnal_centroid_lag_hours"], 1.0, places=1
        )

    def test_registered_era5_is_left_unchanged(self):
        era_file, _ = self.write_long_inputs(era5_lag_steps=0)
        report, _ = self.convert_long(era_file, "aligned.nc")
        self.assertEqual(report["time_registration"]["status"], "registered")
        self.assertEqual(report["time_registration"]["applied_shift_hours"], 0.0)

    def test_manual_shift_without_tower(self):
        era_file, fullset_file = self.write_long_inputs(era5_lag_steps=2)
        os.remove(fullset_file)
        report, _ = self.convert_long(era_file, "manual.nc", time_shift_hours=1.0)
        self.assertEqual(report["time_registration"]["status"], "manual")
        self.assertEqual(report["time_registration"]["applied_shift_hours"], -1.0)

    def test_registration_unverified_without_tower(self):
        era_file, fullset_file = self.write_long_inputs(era5_lag_steps=2)
        os.remove(fullset_file)
        report, _ = self.convert_long(era_file, "unverified.nc")
        self.assertEqual(report["time_registration"]["status"], "unverified")
        self.assertEqual(report["time_registration"]["applied_shift_hours"], 0.0)

    def test_output_name_carries_forcing_years(self):
        era_file, fullset_file = self.write_inputs()
        report, _ = self.convert(era_file, "US-Tst_ecosim_climate.nc", fullset_file)
        self.assertEqual(Path(report["output_file"]).name, "US-Tst_ecosim_climate_2001-2001.nc")
        self.assertTrue(Path(report["output_file"]).exists())
        self.assertFalse((self.root / "US-Tst_ecosim_climate.nc").exists())

    def test_missing_fullset_is_reported_without_failing_conversion(self):
        era_file, fullset_file = self.write_inputs()
        os.remove(fullset_file)
        report, _ = self.convert(era_file, "no_observations.nc")

        comparison = report["in_situ_comparison"]
        self.assertEqual(comparison["status"], "not_available")
        self.assertIn("No matching", comparison["reason"])


if __name__ == "__main__":
    unittest.main()
