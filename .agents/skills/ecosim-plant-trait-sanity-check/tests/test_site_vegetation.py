#!/usr/bin/env python3
"""Tests for site-vegetation-driven trait evidence."""

import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import site_vegetation as sv

SITE = "US-Tst"
ROWS = [
    # GROUP_ID, VARIABLE_GROUP, VARIABLE, DATAVALUE
    (1, "GRP_SPP_O", "SPP_O", "Populus grandidentata"), (1, "GRP_SPP_O", "SPP_O_PERC", 60), (1, "GRP_SPP_O", "SPP_DATE", 20100714),
    (2, "GRP_SPP_O", "SPP_O", "Acer rubra"), (2, "GRP_SPP_O", "SPP_O_PERC", 30), (2, "GRP_SPP_O", "SPP_DATE", 20100714),
    (3, "GRP_SPP_O", "SPP_O", "Pinus strobus"), (3, "GRP_SPP_O", "SPP_O_PERC", 10), (3, "GRP_SPP_O", "SPP_DATE", 20100714),
    (4, "GRP_SPP_O", "SPP_O", "Quercus rubra"), (4, "GRP_SPP_O", "SPP_O_PERC", 50), (4, "GRP_SPP_O", "SPP_DATE", 19970101),
    (10, "GRP_LMA", "LMA", 40.0), (10, "GRP_LMA", "LMA_SPP", "POGR4 (NRCS plant code)"),
    (11, "GRP_LMA", "LMA", 30.0), (11, "GRP_LMA", "LMA_SPP", "ACRU (NRCS plant code)"),
    (20, "GRP_BIOMASS_CHEM", "BIOMASS_C", 5.0), (20, "GRP_BIOMASS_CHEM", "BIOMASS_N", 0.25),
    (20, "GRP_BIOMASS_CHEM", "BIOMASS_SPP", "POGR4 (NRCS plant code)"),
    (21, "GRP_BIOMASS_CHEM", "BIOMASS_C", 5.0), (21, "GRP_BIOMASS_CHEM", "BIOMASS_N", 0.02),
    (21, "GRP_BIOMASS_CHEM", "BIOMASS_SPP", "POGR4 (NRCS plant code)"), (21, "GRP_BIOMASS_CHEM", "BIOMASS_COMMENT", "sapwood only"),
    (22, "GRP_BIOMASS_CHEM", "BIOMASS_C", 4.8), (22, "GRP_BIOMASS_CHEM", "BIOMASS_N", 0.16),
    (22, "GRP_BIOMASS_CHEM", "BIOMASS_SPP", "ACRU (NRCS plant code)"),
    (30, "GRP_UTC_OFFSET", "UTC_OFFSET", -5),
]


def write_bif(path):
    import pandas as pd

    pd.DataFrame(
        [{"SITE_ID": SITE, "GROUP_ID": g, "VARIABLE_GROUP": grp, "VARIABLE": var, "DATAVALUE": val} for g, grp, var, val in ROWS]
        + [{"SITE_ID": "US-Oth", "GROUP_ID": 99, "VARIABLE_GROUP": "GRP_SPP_O", "VARIABLE": "SPP_O", "DATAVALUE": "Zea mays"}]
    ).to_csv(path, index=False)


class SiteVegetationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bif = Path(self.tmp.name, "AMF_AA-Flx_BIF_test.csv")
        write_bif(self.bif)
        self.vegetation = sv.extract(SITE, self.bif)

    def tearDown(self):
        self.tmp.cleanup()

    def test_latest_composition_with_shares_and_spelling_fix(self):
        species = {s["taxon"]: s["canopy_share"] for s in self.vegetation["species"]}
        self.assertEqual(species, {"Populus grandidentata": 0.6, "Acer rubrum": 0.3, "Pinus strobus": 0.1})
        self.assertEqual(self.vegetation["site_context"]["utc_offset_hours"], -5)

    def test_nrcs_codes_map_to_site_taxa(self):
        taxa = [s["taxon"] for s in self.vegetation["species"]]
        self.assertEqual(sv.nrcs_match("POGR4 (NRCS plant code)", taxa), "Populus grandidentata")
        self.assertEqual(sv.nrcs_match("ACRU (NRCS plant code)", taxa), "Acer rubrum")
        self.assertIsNone(sv.nrcs_match("QURU (NRCS plant code)", taxa))

    def test_site_evidence_uses_foliage_only(self):
        values = sv.site_species_values(self.vegetation)
        aspen = next(v for v in values["CNLF"] if v["taxon"] == "Populus grandidentata")
        self.assertAlmostEqual(aspen["max"], 0.05)  # sapwood row (0.004) excluded
        self.assertEqual(aspen["evidence_level"], "site")
        sla = next(v for v in values["SLA1"] if v["taxon"] == "Populus grandidentata")
        self.assertAlmostEqual(sla["min"], 1.0 / (40.0 * 0.5))  # site leaf C 5.0 -> 50 %

    def test_canopy_weighting(self):
        entry = {"species_values": [
            {"taxon": "Populus grandidentata", "min": 0.04, "max": 0.06},
            {"taxon": "Acer rubrum", "min": 0.02, "max": 0.03},
        ]}
        result = sv.weight_range(entry, self.vegetation)
        self.assertTrue(result["applied"])
        self.assertAlmostEqual(result["coverage"], 0.9)
        self.assertAlmostEqual(result["min"], (0.6 * 0.04 + 0.3 * 0.02) / 0.9)

    def test_genus_values_cover_genus_share_and_low_coverage_is_not_weighted(self):
        self.assertAlmostEqual(sv.taxon_shares(self.vegetation, "Populus"), 0.6)
        result = sv.weight_range({"species_values": [{"taxon": "Pinus strobus", "min": 1, "max": 2}]}, self.vegetation)
        self.assertFalse(result["applied"])

    def test_site_evidence_supersedes_and_lint(self):
        evidence = {"plants": [{"pft_code": "bdlf42", "numeric_ranges": [
            {"variable": "SLA1", "min": 0.018, "max": 0.04, "evidence_level": "species", "source": "x", "url": "u",
             "conversion_note": "c"},
            {"variable": "RVSR", "min": 1e-5, "max": 5e-5, "evidence_level": "genus", "source": "x", "url": "u",
             "conversion_note": "c", "species_values": [{"taxon": "Zea mays", "min": 1, "max": 2}]},
        ]}]}
        problems = sv.lint(evidence, self.vegetation)
        self.assertTrue(any("SLA1: site measurements exist" in p for p in problems))
        self.assertTrue(any("Zea mays" in p for p in problems))
        sv.apply_site_evidence(evidence, self.vegetation, "bdlf42")
        sv.apply_weights(evidence, self.vegetation)
        sla = next(r for r in evidence["plants"][0]["numeric_ranges"] if r["variable"] == "SLA1")
        self.assertEqual(sla["evidence_level"], "site")
        self.assertEqual(sla["superseded"]["min"], 0.018)
        self.assertTrue(sla["weighting"]["applied"])
        self.assertFalse(any("SLA1" in p for p in sv.lint(evidence, self.vegetation)))


if __name__ == "__main__":
    unittest.main()
