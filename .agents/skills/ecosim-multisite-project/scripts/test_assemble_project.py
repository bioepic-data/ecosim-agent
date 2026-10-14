#!/usr/bin/env python3
"""Tests for assembling single-site run folders into a multi-site project."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assemble_project as ap

NAMELIST = """&ecosim
case_name='{site}'
pft_file_in='../input/{site}_ecosim_pftpar.nc'
grid_file_in='../input/{site}_ecosim_grid.nc'
pft_mgmt_in='../input/{site}_pft_mgmt.nc'
clm_hour_file_in='../input/{site}_ecosim_climate_1981-2021.nc'
soil_mgmt_in='NO'
atm_ghg_in='../input/fatm.nc'
start_date  = '19090101000000'
forc_periods=1981,1984,18,1981,2021,1,1981,2021,0
/
&ecosim_time
stop_n= 113
/
"""


def make_site(result: Path, site: str, smoke="passed"):
    root = result / site
    (root / "input").mkdir(parents=True)
    (root / "run_dir").mkdir()
    for name in (f"{site}_ecosim_pftpar.nc", f"{site}_ecosim_grid.nc", f"{site}_pft_mgmt.nc",
                 f"{site}_ecosim_climate_1981-2021.nc", "fatm.nc", f"{site}_ecosim_grid.before_fcwp_fix.nc"):
        (root / "input" / name).write_bytes(b"x")
    (root / "run_dir" / f"{site}.namelist").write_text(NAMELIST.format(site=site))
    if smoke:
        (root / "smoke_run").mkdir()
        (root / "smoke_run" / "smoke_summary.json").write_text(json.dumps(
            {"status": smoke, "trait_check": {"errors": 0, "warnings": 1}, "trait_fix_status": "fixed"}))


class AssembleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.result = Path(self.tmp.name, "result")
        self.project = self.result / "projects" / "demo"

    def tearDown(self):
        self.tmp.cleanup()

    def run_assemble(self, *sites, extra=()):
        argv = ["--project", "demo", "--result-dir", str(self.result), "--sites", *sites, *extra]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            status = ap.main(argv)
        return status, err.getvalue()

    def test_layout_and_rewritten_paths(self):
        make_site(self.result, "US-AAA")
        make_site(self.result, "US-BBB")
        status, _ = self.run_assemble("US-AAA", "US-BBB")
        self.assertEqual(status, 0)
        self.assertEqual(sorted(p.name for p in (self.project / "input").iterdir()), ["US-AAA", "US-BBB"])
        text = (self.project / "run_dir" / "US-AAA" / "US-AAA.namelist").read_text()
        self.assertIn("grid_file_in='../../input/US-AAA/US-AAA_ecosim_grid.nc'", text)
        self.assertIn("soil_mgmt_in='NO'", text)
        self.assertFalse((self.project / "input" / "US-AAA" / "US-AAA_ecosim_grid.before_fcwp_fix.nc").exists())
        manifest = json.loads((self.project / "project.json").read_text())
        site = manifest["sites"][0]
        self.assertEqual(site["chronology"], {"start_year": 1909, "stop_n": 113,
                                              "forc_periods": "1981,1984,18,1981,2021,1,1981,2021,0",
                                              "climate_years": "1981-2021"})
        self.assertEqual(site["problems"], [])
        self.assertIn("| US-BBB |", (self.project / "PROJECT.md").read_text())

    def test_site_without_passed_smoke_is_skipped(self):
        make_site(self.result, "US-AAA")
        make_site(self.result, "US-BAD", smoke="failed")
        make_site(self.result, "US-NOS", smoke=None)
        status, err = self.run_assemble("US-AAA", "US-BAD", "US-NOS")
        self.assertEqual(status, 1)
        manifest = json.loads((self.project / "project.json").read_text())
        self.assertEqual([s["site_id"] for s in manifest["sites"]], ["US-AAA"])
        self.assertEqual(sorted(manifest["skipped"]), ["US-BAD", "US-NOS"])
        self.assertIn("SKIP US-BAD", err)

    def test_missing_input_is_a_problem(self):
        make_site(self.result, "US-AAA")
        (self.result / "US-AAA" / "input" / "fatm.nc").unlink()
        status, _ = self.run_assemble("US-AAA")
        self.assertEqual(status, 1)
        problems = json.loads((self.project / "project.json").read_text())["sites"][0]["problems"]
        self.assertTrue(any("atm_ghg_in" in p for p in problems))

    def test_reassembly_keeps_other_sites_and_run_output(self):
        make_site(self.result, "US-AAA")
        make_site(self.result, "US-BBB")
        self.run_assemble("US-AAA", "US-BBB")
        output = self.project / "run_dir" / "US-AAA" / "US-AAA.ecosim.h0.1909-01-01-00000.nc"
        output.write_bytes(b"h0")
        self.run_assemble("US-AAA")
        self.assertTrue(output.exists())
        manifest = json.loads((self.project / "project.json").read_text())
        self.assertEqual([s["site_id"] for s in manifest["sites"]], ["US-AAA", "US-BBB"])
        self.run_assemble("US-AAA", extra=("--replace-runs",))
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
