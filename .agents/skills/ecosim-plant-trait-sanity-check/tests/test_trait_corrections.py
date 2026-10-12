#!/usr/bin/env python3
"""Tests for literature-backed plant-trait corrections."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import trait_corrections as tc

PFTPAR = SKILL_DIR.parents[3] / "input_data" / "ecosim_pftpar_20260915.nc"

REPORT = {"findings": [
    {"severity": "ERROR", "pft_code": "bdlf42", "parameter": "VCMX/VOMX/XKCO2/XKO2",
     "message": "strict physiology: Rubisco CO2/O2 specificity implied by VCMX*XKO2/(VOMX*XKCO2) is 189.5, outside [70, 140]"},
    {"severity": "ERROR", "pft_code": "bdlf42", "parameter": "CHL",
     "message": "strict physiology: chlorophyll-bound/light-harvesting protein fraction ... [0.08, 0.3]: 0.025"},
    {"severity": "WARN", "pft_code": "bdlf42", "parameter": "IEBTYP",
     "message": "bdlf42 'broadleaf tree' is expected to use embryophyte type eudicot (4); found gymnosperm (2)"},
    {"severity": "WARN", "pft_code": "bdlf42", "parameter": "IEBTYP",
     "message": "value 'Gymnosperms' does not contain web-evidence expectation 'Eudicot' (taxon=x)"},
    {"severity": "WARN", "pft_code": "bdlf42", "parameter": "ETMX*CHL/VCMX*RUBP",
     "message": "model-formulation-sensitive protein-normalized Jmax:Vcmax diagnostic is 0.51"},
    {"severity": "WARN", "pft_code": "bdlf42", "parameter": "SLA1",
     "message": "value 0.009 is outside web-evidence range [0.018, 0.04] m2 gC-1 (taxon=x)"},
    {"severity": "WARN", "pft_code": "bdlf42", "parameter": "CNLF",
     "message": "value 0.1 is outside web-evidence range [0.025, 0.06] gN gC-1 (taxon=x)"},
]}

EVIDENCE = {"plants": [{
    "pft_code": "bdlf42",
    "numeric_ranges": [
        {"variable": "SLA1", "min": 0.018, "max": 0.040, "source": "SLA study", "url": "https://example.org/sla"},
        {"variable": "CNLF", "min": 0.025, "max": 0.06, "source": "leaf N study", "url": "https://example.org/n"},
    ],
    "derived_ranges": [
        {"variable": "RUBISCO_SPECIFICITY", "min": 81.0, "max": 100.0, "source": "Sc/o survey", "url": "https://example.org/sc"},
    ],
    "categorical_expectations": [
        {"variable": "IEBTYP", "contains": "Eudicot", "source": "USDA PLANTS", "url": "https://plants.usda.gov/"},
    ],
}]}

CURRENT = {"bdlf42": {"VCMX": 45.0, "VOMX": 9.5, "XKCO2": 12.5, "XKO2": 500.0, "SLA1": 0.009, "CNLF": 0.1, "IEBTYP": 2}}


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = tc.plan_corrections(REPORT, EVIDENCE, CURRENT)
        self.by_var = {c["variable"]: c for c in self.plan["corrections"]}

    def test_sla_is_quarter_of_literature_typical(self):
        self.assertAlmostEqual(self.by_var["SLA1"]["new"], 0.25 * 0.029)
        self.assertEqual(self.by_var["SLA1"]["evidence"]["url"], "https://example.org/sla")

    def test_numeric_uses_literature_midpoint(self):
        self.assertAlmostEqual(self.by_var["CNLF"]["new"], 0.0425)

    def test_rubisco_sets_vomx_for_literature_specificity(self):
        vomx = self.by_var["VOMX"]["new"]
        self.assertAlmostEqual(45.0 * 500.0 / (vomx * 12.5), 90.5)
        self.assertEqual(self.by_var["VOMX"]["evidence"]["source"], "Sc/o survey")

    def test_categorical_uses_enum_code_with_citation(self):
        self.assertEqual(self.by_var["IEBTYP"]["new"], 4)
        self.assertEqual(self.by_var["IEBTYP"]["evidence"]["source"], "USDA PLANTS")

    def test_finding_without_literature_is_unresolved(self):
        unresolved = {u["variable"] for u in self.plan["unresolved"]}
        self.assertEqual(unresolved, {"CHL"})
        self.assertNotIn("CHL", self.by_var)

    def test_c3_tree_chl_gets_user_default(self):
        current = {"bdlf42": {**CURRENT["bdlf42"], "CHL": 0.025, "ETMX": 405.0, "RUBP": 0.125, "ICTYP": 3}}
        plan = tc.plan_corrections(REPORT, EVIDENCE, current)
        chl = next(c for c in plan["corrections"] if c["variable"] == "CHL")
        self.assertEqual(chl["new"], 0.12)
        self.assertEqual(chl["evidence"]["evidence_level"], "user-default")
        self.assertIn("Jmax:Vcmax 0.51 -> 2.47", chl["rule"])
        self.assertNotIn("CHL", {u["variable"] for u in plan["unresolved"]})

    def test_chl_default_only_for_c3_trees(self):
        for pft, ictyp in (("gr3s42", 3), ("bdlf42", 4)):
            report = {"findings": [{**REPORT["findings"][1], "pft_code": pft}]}
            current = {pft: {"CHL": 0.025, "ICTYP": ictyp}}
            plan = tc.plan_corrections(report, {"plants": []}, current)
            self.assertEqual(plan["corrections"], [], pft)
            self.assertEqual([u["variable"] for u in plan["unresolved"]], ["CHL"], pft)

    def test_literature_chl_range_overrides_default(self):
        evidence = {"plants": [{**EVIDENCE["plants"][0], "numeric_ranges": EVIDENCE["plants"][0]["numeric_ranges"]
                                + [{"variable": "CHL", "min": 0.1, "max": 0.2, "source": "lit", "url": "u"}]}]}
        current = {"bdlf42": {**CURRENT["bdlf42"], "CHL": 0.025, "ICTYP": 3}}
        chl = next(c for c in tc.plan_corrections(REPORT, evidence, current)["corrections"] if c["variable"] == "CHL")
        self.assertAlmostEqual(chl["new"], 0.15)

    def test_minor_diagnostic_is_not_corrected(self):
        self.assertNotIn("ETMX*CHL/VCMX*RUBP", self.by_var)

    def test_rubisco_without_literature_is_unresolved(self):
        evidence = {"plants": [{**EVIDENCE["plants"][0], "derived_ranges": []}]}
        plan = tc.plan_corrections(REPORT, evidence, CURRENT)
        self.assertIn("VCMX/VOMX/XKCO2/XKO2", {u["variable"] for u in plan["unresolved"]})

    def test_sla_at_rule_target_counts_as_resolved(self):
        current = {"bdlf42": {**CURRENT["bdlf42"], "SLA1": 0.25 * 0.029}}
        report = {"findings": [f for f in REPORT["findings"] if f["parameter"] == "SLA1"]}
        report["findings"][0] = {**report["findings"][0], "message": "value 0.00725 is outside web-evidence range [0.018, 0.04]"}
        plan = tc.plan_corrections(report, EVIDENCE, current)
        self.assertEqual(plan["corrections"], [])
        self.assertEqual(plan["unresolved"], [])
        self.assertEqual(plan["satisfied_by_rule"][0]["variable"], "SLA1")


@unittest.skipUnless(PFTPAR.exists(), "shared PFT parameter file not available")
class ApplyTests(unittest.TestCase):
    def test_apply_edits_copy_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp, "US-Tst_ecosim_pftpar.nc")
            shutil.copy2(PFTPAR, copy)
            plan = tc.plan_corrections(REPORT, EVIDENCE, CURRENT)
            applied = tc.apply_corrections(copy, plan["corrections"])
            readback = {a["variable"]: a["readback"] for a in applied}
            self.assertEqual(int(readback["IEBTYP"]), 4)
            self.assertAlmostEqual(readback["CNLF"], 0.0425, places=6)
            self.assertTrue(Path(tmp, "US-Tst_ecosim_pftpar.jsonl").exists())
            other = tc.read_pft_values(copy, "bdlf43", ["IEBTYP", "CNLF"])
            self.assertEqual(int(other["IEBTYP"]), 2)  # other records untouched


if __name__ == "__main__":
    unittest.main()
