#!/usr/bin/env python3
"""Tests for the EcoSIM site run progress tracker."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import site_run_tracker as tracker

SITE = "US-Tst"

NAMELIST = """&ecosim
pft_file_in='../input/pft.nc'
grid_file_in='../input/US-Tst_ecosim_grid.nc'
pft_mgmt_in='../input/US-Tst_pft_mgmt.nc'
clm_hour_file_in='../input/US-Tst_ecosim_climate_1981-2021.nc'
soil_mgmt_in='NO'
atm_ghg_in='../input/ghg.nc'
start_date  = '{start}0101000000'
forc_periods=1981,1984,18,1981,2021,1,1981,2021,0
/
&ecosim_time
stop_n= {stop_n}
/
"""


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = Path(self.tmp.name, "result")
        self.site = self.result / SITE

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        with redirect_stdout(io.StringIO()) as out:
            status = tracker.main([args[0], "--site-id", SITE, "--result-dir", str(self.result), *args[1:]])
        return status, out.getvalue()

    def state(self):
        return json.loads((self.site / "progress.json").read_text())

    def step(self, name):
        return self.state()["stages"][tracker.STEP_TO_STAGE[name]]["steps"][name]

    def build_run_folder(self, stop_n=113, start=1909):
        inputs = self.site / "input"
        inputs.mkdir(parents=True)
        (self.site / "run_dir").mkdir()
        for name in ("pft.nc", "ghg.nc", f"{SITE}_ecosim_grid.nc", f"{SITE}_pft_mgmt.nc",
                     f"{SITE}_ecosim_climate_1981-2021.nc"):
            (inputs / name).write_bytes(b"x")
        (inputs / f"{SITE}_ecosim_site.json").write_text(json.dumps({
            "site_name": SITE, "ALATG": 45.0, "ALONG": -85.0, "ALTIG": 200.0, "ATCAG": 6.0, "IETYPG": 42,
            "IXTYP1": 10, "_location_validation": {"status": "passed", "errors": [], "warnings": []},
        }))
        (inputs / f"{SITE}_grid_derivation_report.json").write_text(json.dumps(
            {"report": {"soil_source": "gssurgo", "layer_validation_problems": []}}
        ))
        (self.site / "run_dir" / f"{SITE}.namelist").write_text(NAMELIST.format(start=start, stop_n=stop_n))

    def test_init_on_empty_site_is_all_pending(self):
        status, text = self.run_cli("init", "--request", "create an ecosim run for site US-Tst")
        self.assertEqual(status, 0)
        self.assertEqual(self.state()["request"], "create an ecosim run for site US-Tst")
        for stage in self.state()["stages"].values():
            self.assertEqual(tracker.stage_status(stage["steps"]), "pending")
        self.assertTrue((self.site / "PROGRESS.md").exists())
        self.assertIn("| Data preparation | pending |", text)

    def test_run_folder_marks_preparation_and_namelist(self):
        self.build_run_folder()
        self.run_cli("init")
        self.assertEqual(self.step("site_metadata")["status"], "done")
        # NLDAS/CDS forcing has no ERA5 quality report; its absence is noted, not a warning.
        self.assertEqual(self.step("climate_forcing")["status"], "done")
        self.assertIn("no ERA5 quality report", self.step("climate_forcing")["detail"])
        self.assertEqual(self.step("soil_grid")["status"], "done")
        self.assertEqual(self.step("namelist")["status"], "done")
        self.assertEqual(self.step("chronology")["status"], "done")
        self.assertIn("1909-2021", self.step("chronology")["detail"])

    def test_stop_n_shorter_than_forcing_plan_warns(self):
        self.build_run_folder(stop_n=41, start=1981)
        self.run_cli("init")
        self.assertEqual(self.step("chronology")["status"], "warning")
        self.assertIn("113", self.step("chronology")["detail"])

    def test_missing_namelist_input_fails(self):
        self.build_run_folder()
        (self.site / "input" / f"{SITE}_pft_mgmt.nc").unlink()
        self.run_cli("init")
        self.assertEqual(self.step("namelist")["status"], "failed")
        self.assertEqual(tracker.stage_status(self.state()["stages"]["namelist_creation"]["steps"]), "failed")

    def test_history_output_is_in_progress_until_confirmed(self):
        self.build_run_folder()
        (self.site / "run_dir" / f"{SITE}.ecosim.h0.1909-01-01-00000.nc").write_bytes(b"x")
        self.run_cli("init")
        self.assertEqual(self.step("full_run")["status"], "in_progress")
        self.run_cli("set", "--step", "full_run", "--status", "done", "--note", "exit 0")
        self.run_cli("scan")
        self.assertEqual(self.step("full_run")["status"], "done")
        self.assertEqual(self.step("full_run")["source"], "manual")
        self.assertEqual(self.step("full_run")["auto_status"], "in_progress")

    def test_manual_skip_and_return_to_auto(self):
        self.run_cli("init")
        self.run_cli("set", "--step", "soil_management", "--status", "skipped", "--note", "unmanaged")
        self.assertEqual(self.step("soil_management")["status"], "skipped")
        self.run_cli("set", "--step", "soil_management", "--auto")
        self.assertEqual(self.step("soil_management")["status"], "pending")
        self.assertEqual(self.step("soil_management")["source"], "auto")

    def test_evaluation_files_are_detected(self):
        self.build_run_folder()
        evaluation = self.site / "evaluation"
        evaluation.mkdir()
        (evaluation / f"{SITE}_h0_targets.json").write_text("{}")
        self.run_cli("init")
        self.assertEqual(self.step("target_extraction")["status"], "done")
        self.assertEqual(self.step("benchmark_comparison")["status"], "pending")

    def test_unreadable_artifact_fails_only_its_step(self):
        self.build_run_folder()
        (self.site / "input" / f"{SITE}_ecosim_site.json").write_text("not json")
        self.run_cli("init")
        self.assertEqual(self.step("site_metadata")["status"], "failed")
        self.assertIn("Could not check", self.step("site_metadata")["detail"])
        self.assertEqual(self.step("namelist")["status"], "done")

    def test_show_requires_init(self):
        status, _ = self.run_cli("show")
        self.assertEqual(status, 1)


if __name__ == "__main__":
    unittest.main()
