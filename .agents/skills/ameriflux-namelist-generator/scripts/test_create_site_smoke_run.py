#!/usr/bin/env python3
"""Tests for the 2-year site smoke run and plant-trait evaluation."""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import create_site_smoke_run as smoke

SITE = "US-Tst"
NAMELIST = """&ecosim
case_name='US-Tst'
grid_file_in='../input/US-Tst_ecosim_grid.nc'
start_date  = '1912010100000'
continue_run=.true.
/
&ecosim_time
stop_n= 113             !this shall overwrite what is set in forc_periods
stop_option='nyears' !run by nyears, nmonths, ndays,
/
"""

# Fake EcoSIM: reports NSTEP restarts and writes a plant trait table copied from DESC.
FAKE_EXE = """#!/bin/sh
echo " Successfully wrote out restart data at nstep =       $NSTEP"
if [ -n "$DESC" ]; then cp "$DESC" ./US-Tst.plant_trait.desc.1912; fi
exit ${RC:-0}
"""


class HelperTests(unittest.TestCase):
    def test_namelist_is_shortened_from_same_start(self):
        text = smoke.smoke_namelist(NAMELIST, 2)
        self.assertIn("stop_n=2\n", text)
        self.assertIn("stop_option='nyears'", text)
        self.assertIn("continue_run=.false.", text)
        self.assertIn("start_date  = '1912010100000'", text)
        self.assertIn("../input/US-Tst_ecosim_grid.nc", text)

    def test_expected_nstep_counts_leap_days(self):
        self.assertEqual(smoke.expected_nstep(1909, 2), 17520)
        self.assertEqual(smoke.expected_nstep(1912, 2), 17544)

    def test_last_nstep(self):
        log = ("x\n Successfully wrote out restart data at nstep =   8760\n"
               " Successfully wrote out restart data at nstep =  17520\n")
        self.assertEqual(smoke.last_nstep(log), 17520)
        self.assertEqual(smoke.last_nstep("crashed"), 0)


class SmokeRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = Path(self.tmp.name, "result")
        self.site = self.result / SITE
        (self.site / "run_dir").mkdir(parents=True)
        (self.site / "input").mkdir()
        (self.site / "run_dir" / f"{SITE}.namelist").write_text(NAMELIST)
        self.exe = Path(self.tmp.name, "ecosim.f90.x")
        self.exe.write_text(FAKE_EXE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        os.environ["ECOSIM_PROGRESS_TRACKER"] = "0"

    def tearDown(self):
        os.environ.pop("ECOSIM_PROGRESS_TRACKER", None)
        for key in ("NSTEP", "RC", "DESC"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def run_smoke(self, *extra, history="clean"):
        # The fake executable writes no NetCDF history; real runs use check_history.py.
        scan = mock.patch.object(smoke, "scan_history", return_value={"status": history, "detail": ""})
        with scan, redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            status = smoke.main(["--site-id", SITE, "--result-dir", str(self.result), "--exe", str(self.exe), *extra])
        return status, out.getvalue()

    def summary(self):
        return json.loads((self.site / "smoke_run" / "smoke_summary.json").read_text())

    def test_passing_run_without_trait_table(self):
        os.environ["NSTEP"] = "17544"
        status, out = self.run_smoke()
        self.assertEqual(status, 0)
        summary = self.summary()
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["expected_nstep"], 17544)
        self.assertEqual(summary["trait_check"]["status"], "missing")
        self.assertTrue((self.site / "smoke_run" / f"{SITE}.namelist").exists())
        self.assertEqual(sorted(p.name for p in (self.site / "run_dir").iterdir()), [f"{SITE}.namelist"])

    def test_short_run_fails(self):
        os.environ["NSTEP"] = "8784"
        status, _ = self.run_smoke()
        self.assertEqual(status, 1)
        self.assertEqual(self.summary()["status"], "failed")

    def test_bad_history_fails(self):
        os.environ["NSTEP"] = "17544"
        status, _ = self.run_smoke(history="failed")
        self.assertEqual(status, 1)
        self.assertEqual(self.summary()["history_scan"]["status"], "failed")

    def test_nonzero_exit_fails(self):
        os.environ["NSTEP"] = "17544"
        os.environ["RC"] = "3"
        status, _ = self.run_smoke()
        self.assertEqual(status, 1)
        self.assertEqual(self.summary()["returncode"], 3)

    def test_trait_table_is_checked_and_evidence_kept(self):
        desc = Path(self.tmp.name, "trait.desc")
        desc.write_text(" PLANT traits for FUNCTIONAL TYPE (NZ,NY,NX)=  1  1  1 bdlf42\n")
        os.environ["NSTEP"] = "17544"
        os.environ["DESC"] = str(desc)
        evidence = self.site / "smoke_run" / f"{SITE}_plant_trait_web_evidence.json"
        evidence.parent.mkdir()
        evidence.write_text('{"plants": []}')
        status, _ = self.run_smoke()
        self.assertEqual(status, 0)
        traits = self.summary()["trait_check"]
        self.assertTrue(traits["desc_file"].endswith(f"{SITE}.plant_trait.desc.1912"))
        self.assertTrue(evidence.exists())
        self.assertEqual(Path(traits["web_evidence"]).resolve(), evidence.resolve())
        self.assertTrue((self.site / "smoke_run" / f"{SITE}_plant_trait_check.md").exists())

    def run_with_plans(self, plans):
        """Run the loop with a mocked trait check that always reports one ERROR."""
        os.environ["NSTEP"] = "17544"
        trait = {"status": "errors", "errors": 1, "warnings": 0, "findings": []}
        rounds = iter(plans)
        with mock.patch.object(smoke, "check_traits", return_value=trait), \
                mock.patch.object(smoke, "correction_round", side_effect=lambda *a, **k: next(rounds)), \
                mock.patch.object(smoke, "smoke_round", wraps=smoke.smoke_round) as smoke_round:
            status, out = self.run_smoke()
        return status, out, smoke_round.call_count

    def test_two_no_change_iterations_stop_the_loop(self):
        unresolved = {"corrections": [], "unresolved": [{"pft": "bdlf42", "variable": "CHL", "reason": "no literature"}],
                      "satisfied_by_rule": []}
        status, out, runs = self.run_with_plans([dict(unresolved), dict(unresolved), dict(unresolved)])
        self.assertEqual(status, 0)
        self.assertIn("No parameter changed in 2 consecutive iterations", out)
        self.assertEqual(runs, 1)  # unchanged parameters: no model rerun
        summary = self.summary()
        self.assertEqual(len(summary["trait_rounds"]), 2)
        self.assertEqual(summary["trait_fix_status"], "unresolved")

    def test_changed_iteration_reruns_smoke(self):
        applied = {"corrections": [{"pft": "bdlf42", "variable": "CNLF"}], "unresolved": [], "satisfied_by_rule": [],
                   "applied": [{"pft": "bdlf42", "variable": "CNLF", "old": 0.1, "new": 0.0425, "readback": 0.0425,
                                "rule": "midpoint", "evidence": {"source": "leaf N study"}}]}
        done = {"corrections": [], "unresolved": [], "satisfied_by_rule": []}
        status, _, runs = self.run_with_plans([applied, done])
        self.assertEqual(runs, 2)
        self.assertEqual(self.summary()["trait_fix_status"], "fixed")

    def test_trait_only_requires_previous_run(self):
        status, _ = self.run_smoke("--trait-only")
        self.assertEqual(status, 2)

    def test_missing_run_folder(self):
        (self.site / "run_dir" / f"{SITE}.namelist").unlink()
        status, _ = self.run_smoke()
        self.assertEqual(status, 2)


if __name__ == "__main__":
    unittest.main()
