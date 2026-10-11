#!/usr/bin/env python3
"""Tests for year-coverage naming of EcoSIM climate NetCDF files."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import climate_naming as naming


def write_years(path, years):
    from netCDF4 import Dataset

    with Dataset(path, "w") as ds:
        ds.createDimension("year", len(years))
        ds.createVariable("year", "i4", ("year",))[:] = years


class ClimateNamingTests(unittest.TestCase):
    def test_inserts_range_before_extension(self):
        self.assertEqual(
            naming.climate_name_with_years(Path("US-UMB_ecosim_climate.nc"), 1981, 2021).name,
            "US-UMB_ecosim_climate_1981-2021.nc",
        )

    def test_keeps_matching_range_in_either_separator(self):
        for name in ("site_ecosim_climate_1940-2024.nc", "site_ecosim_climate_1940_2024.nc", "Blodget.clim.2012-2022.nc"):
            first, last = (1940, 2024) if "1940" in name else (2012, 2022)
            self.assertEqual(naming.climate_name_with_years(Path(name), first, last).name, name)

    def test_replaces_stale_range(self):
        self.assertEqual(
            naming.climate_name_with_years(Path("US-UMB_ecosim_climate_1981-2020.nc"), 1981, 2021).name,
            "US-UMB_ecosim_climate_1981-2021.nc",
        )

    def test_site_digits_are_not_taken_for_years(self):
        self.assertEqual(
            naming.climate_name_with_years(Path("US-Ha1_ecosim_climate.nc"), 1991, 2020).name,
            "US-Ha1_ecosim_climate_1991-2020.nc",
        )

    def test_finalize_renames_by_year_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "US-Tst_ecosim_climate.nc")
            write_years(path, list(range(2001, 2011)))
            final = naming.finalize_climate_file_name(path)
            self.assertEqual(final.name, "US-Tst_ecosim_climate_2001-2010.nc")
            self.assertTrue(final.exists())
            self.assertFalse(path.exists())

    def test_finalize_leaves_file_without_year_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "other.nc")
            from netCDF4 import Dataset

            Dataset(path, "w").close()
            self.assertEqual(naming.finalize_climate_file_name(path), path)


if __name__ == "__main__":
    unittest.main()
