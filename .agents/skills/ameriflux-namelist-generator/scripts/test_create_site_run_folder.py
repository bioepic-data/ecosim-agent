#!/usr/bin/env python3
"""Tests for the result/<SITE_ID>/{input,run_dir} run-folder layout."""

import io
import re
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_site_run_folder as run_folder
from Tools.test_driving_data_conflicts import write_grid, write_site

SITE = "US-Tst"


def write_climate(path, first_year, last_year):
    from netCDF4 import Dataset

    with Dataset(path, "w") as ds:
        ds.createDimension("year", last_year - first_year + 1)
        ds.createVariable("year", "i4", ("year",))[:] = list(range(first_year, last_year + 1))


class RunFolderTests(unittest.TestCase):
    def setUp(self):
        # Stub files exercise orchestration; full input validation has dedicated tests.
        self.preflight = mock.patch.object(run_folder, 'enforce_preflight', return_value={'status': 'ready'}).start()
        self.addCleanup(mock.patch.stopall)
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name, "ecosim-agent")
        self.site_root = self.repo / "result" / SITE
        self.site_root.mkdir(parents=True)
        self.shared = Path(self.tmp.name, "input_data")
        self.shared.mkdir()
        for name in ("ecosim_pftpar_20260915.nc", "fatm_hist_GHGs_1750-2025.nc", "MicrobePars.20260211.nc"):
            Path(self.shared, name).write_bytes(b"x")
        write_climate(self.site_root / f"{SITE}_ecosim_climate.nc", 1981, 2021)
        write_grid(self.site_root / f'{SITE}_ecosim_grid.nc')
        write_site(self.site_root, SITE)
        write_climate(self.site_root / f'{SITE}_pft_mgmt.nc', 1981, 2021)
        for name in (f"{SITE}_pft_mgmt.xlsx",
                     f"{SITE}_era5_quality_report.json"):
            Path(self.site_root, name).write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def run_folder(self, *extra):
        argv = ["--site-id", SITE, "--repo-root", str(self.repo), *extra]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
            status = run_folder.main(argv)
        return status, stderr.getvalue()

    def namelist(self):
        return (self.site_root / "run_dir" / f"{SITE}.namelist").read_text()

    def test_layout_has_only_input_and_run_dir(self):
        status, _ = self.run_folder()
        self.assertEqual(status, 0)
        self.assertEqual(
            sorted(p.name for p in self.site_root.iterdir()),
            ["PROGRESS.md", "input", "progress.json", "run_dir"],
        )
        self.assertEqual([p.name for p in (self.site_root / "run_dir").iterdir()], [f"{SITE}.namelist"])
        inputs = {p.name for p in (self.site_root / "input").iterdir()}
        self.assertIn(f"{SITE}_pft_mgmt.xlsx", inputs)
        self.assertIn("ecosim_pftpar_20260915.nc", inputs)
        self.assertIn("MicrobePars.20260211.nc", inputs)

    def test_tracker_records_run_folder_and_failures(self):
        import json

        self.run_folder()
        state = json.loads((self.site_root / "progress.json").read_text())
        namelist = state["stages"]["namelist_creation"]["steps"]["namelist"]
        self.assertEqual(namelist["status"], "done")
        Path(self.site_root, "input", f"{SITE}_pft_mgmt.nc").unlink()
        self.run_folder()
        state = json.loads((self.site_root / "progress.json").read_text())
        self.assertEqual(state["stages"]["namelist_creation"]["steps"]["namelist"]["status"], "failed")

    def test_tracker_files_stay_at_site_root(self):
        Path(self.site_root, "progress.json").write_text("{}")
        Path(self.site_root, "PROGRESS.md").write_text("# progress")
        self.run_folder()
        self.assertTrue((self.site_root / "progress.json").exists())
        self.assertFalse((self.site_root / "input" / "progress.json").exists())

    def test_namelist_paths_point_into_input(self):
        self.run_folder()
        text = self.namelist()
        for field in ("pft_file_in", "grid_file_in", "pft_mgmt_in", "clm_hour_file_in", "micpar_file_in", "atm_ghg_in"):
            value = re.search(rf"^{field}='([^']*)'", text, flags=re.MULTILINE).group(1)
            self.assertTrue(value.startswith("../input/"), (field, value))
        self.assertIn("soil_mgmt_in='NO'", text)

    def test_site_guide_and_relative_links_survive_reassembly(self):
        guide = self.site_root / f"{SITE}.html"
        content = f'<a href="input/{SITE}_ecosim_grid.nc">Soil input</a>'
        guide.write_text(content)
        for _ in range(2):
            status, _ = self.run_folder()
            self.assertEqual(status, 0)
            self.assertEqual(guide.read_text(), content)
            self.assertTrue((guide.parent / "input" / f"{SITE}_ecosim_grid.nc").exists())
            self.assertFalse((self.site_root / "input" / guide.name).exists())

    def test_default_chronology_covers_spinup(self):
        self.run_folder()
        text = self.namelist()
        self.assertIn("start_date  = '19090101000000'", text)
        self.assertIn("forc_periods=1981,1984,18,1981,2021,1,1981,2021,0", text)
        self.assertRegex(text, r"stop_n=\s*113\b")

    def test_soil_management_is_wired_when_present(self):
        write_climate(self.site_root / f'{SITE}_soil_mgmt.nc', 1981, 2021)
        self.run_folder()
        self.assertIn(f"soil_mgmt_in='../input/{SITE}_soil_mgmt.nc'", self.namelist())

    def test_missing_required_input_fails(self):
        Path(self.site_root, f"{SITE}_pft_mgmt.nc").unlink()
        status, stderr = self.run_folder()
        self.assertEqual(status, 2)
        self.assertIn("pft_mgmt_in", stderr)
        self.assertFalse((self.site_root / "run_dir" / f"{SITE}.namelist").exists())

    def test_legacy_climate_name_gets_year_range(self):
        status, _ = self.run_folder()
        self.assertEqual(status, 0)
        inputs = {p.name for p in (self.site_root / "input").iterdir()}
        self.assertIn(f"{SITE}_ecosim_climate_1981-2021.nc", inputs)
        self.assertNotIn(f"{SITE}_ecosim_climate.nc", inputs)
        self.assertIn(f"clm_hour_file_in='../input/{SITE}_ecosim_climate_1981-2021.nc'", self.namelist())

    def test_competing_climate_files_require_choice(self):
        write_climate(self.site_root / f"{SITE}_ecosim_climate_1981-2020.nc", 1981, 2020)
        (self.site_root / f"{SITE}_ecosim_climate.nc").rename(self.site_root / f"{SITE}_ecosim_climate_1981-2021.nc")
        status, stderr = self.run_folder()
        self.assertEqual(status, 2)
        self.assertIn("--climate-file", stderr)

    def test_rerun_is_idempotent(self):
        self.run_folder()
        first = self.namelist()
        status, _ = self.run_folder()
        self.assertEqual(status, 0)
        self.assertEqual(self.namelist(), first)

    def test_conflict_blocks_assembly_before_namelist(self):
        from netCDF4 import Dataset
        with Dataset(self.site_root / f'{SITE}_ecosim_grid.nc', 'a') as ds:
            ds['ALATG'][:] = -45
        status, stderr = self.run_folder()
        self.assertEqual(status, 1)
        self.assertIn('driving-data conflicts', stderr)
        self.assertFalse((self.site_root / 'run_dir' / f'{SITE}.namelist').exists())


    def test_mandatory_preflight_failure_blocks_assembly_success(self):
        self.preflight.side_effect = run_folder.PreflightError('invalid selected input')
        status, stderr = self.run_folder()
        self.assertEqual(status, 1)
        self.assertIn('invalid selected input', stderr)
        self.preflight.assert_called_once()


if __name__ == "__main__":
    unittest.main()
