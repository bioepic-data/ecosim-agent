#!/usr/bin/env python3
"""Tests for the full site run with one continued run on failure."""

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

import run_site_full as full

SITE = "US-Tst"
NAMELIST = """&ecosim
case_name='US-Tst'
start_date  = '1909010100000'
continue_run=.false.
forc_periods=1981,1982,1,1981,1982,1,1981,1982,0
/
&ecosim_time
stop_n= 4             !years
stop_option='nyears'
/
"""
# Fake EcoSIM. Fresh run (continue_run=.false.): writes restarts through FAIL_YEAR and fails,
# or finishes. Continued run: requires rpointer.esim, finishes or fails per CONT_RC.
FAKE = """#!/bin/sh
if grep -q "continue_run=.true." "$1"; then
  [ -f rpointer.esim ] || exit 9
  echo " Successfully wrote out restart data at nstep = $CONT_NSTEP"
  [ "${CONT_RC:-0}" = 0 ] && touch US-Tst.ecosim.r.1913-01-01-000000.nc
  exit ${CONT_RC:-0}
fi
if [ -n "$FAIL_YEAR" ]; then
  touch US-Tst.ecosim.r.$FAIL_YEAR-01-01-000000.nc
  echo "US-Tst.ecosim.r.$FAIL_YEAR-01-01-000000.nc" > rpointer.esim
  echo " Successfully wrote out restart data at nstep = 17520"
  exit 3
fi
echo " Successfully wrote out restart data at nstep = 35064"
touch US-Tst.ecosim.r.1913-01-01-000000.nc
exit 0
"""
TOTAL = 35064  # 1909-1912 hours (1912 leap)


class FullRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = Path(self.tmp.name, "result")
        self.run_dir = self.result / SITE / "run_dir"
        self.run_dir.mkdir(parents=True)
        (self.run_dir / f"{SITE}.namelist").write_text(NAMELIST)
        self.exe = Path(self.tmp.name, "ecosim.f90.x")
        self.exe.write_text(FAKE)
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IEXEC)
        os.environ["ECOSIM_PROGRESS_TRACKER"] = "0"

    def tearDown(self):
        for key in ("ECOSIM_PROGRESS_TRACKER", "FAIL_YEAR", "CONT_RC", "CONT_NSTEP"):
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def run_full(self):
        clean = mock.patch.object(full.smoke, "scan_history", return_value={"status": "clean", "detail": ""})
        with clean, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            status = full.main(["--site-id", SITE, "--result-dir", str(self.result), "--exe", str(self.exe)])
        return status, err.getvalue(), json.loads((self.run_dir / "full_run_summary.json").read_text())

    def test_fresh_success(self):
        status, _, summary = self.run_full()
        self.assertEqual(status, 0)
        self.assertEqual(summary["expected_nstep"], TOTAL)
        self.assertEqual([a["kind"] for a in summary["attempts"]], ["fresh"])

    def test_failure_then_continued_run_with_continue_flag(self):
        os.environ.update({"FAIL_YEAR": "1911", "CONT_NSTEP": str(TOTAL - 17520)})
        status, _, summary = self.run_full()
        self.assertEqual(status, 0)
        self.assertEqual([a["kind"] for a in summary["attempts"]], ["fresh", "continued"])
        self.assertEqual(summary["attempts"][1]["completed_years_before"], 2)
        text = (self.run_dir / f"{SITE}.namelist").read_text()
        self.assertIn("continue_run=.true.", text)
        self.assertIn("stop_n= 4", text)  # stop_n unchanged

    def test_continued_run_failure_reports_error(self):
        os.environ.update({"FAIL_YEAR": "1911", "CONT_NSTEP": "100", "CONT_RC": "5"})
        status, err, summary = self.run_full()
        self.assertEqual(status, 1)
        self.assertEqual(summary["status"], "failed")
        self.assertIn("continued: exit 5", summary["error"])
        self.assertIn("ERROR: full run failed", err)

    def test_failure_before_first_restart_is_not_continued(self):
        os.environ["FAIL_YEAR"] = "1909"
        status, _, summary = self.run_full()
        self.assertEqual(status, 1)
        self.assertEqual(len(summary["attempts"]), 1)
        self.assertIn("no usable yearly restart", summary["continue_skipped"])

    def test_fresh_run_resets_continue_flag(self):
        path = self.run_dir / f"{SITE}.namelist"
        path.write_text(NAMELIST.replace("continue_run=.false.", "continue_run=.true."))
        status, _, _ = self.run_full()
        self.assertEqual(status, 0)
        self.assertIn("continue_run=.false.", path.read_text())


if __name__ == "__main__":
    unittest.main()
