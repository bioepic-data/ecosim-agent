#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_plant_trait_desc.py"
SPEC = importlib.util.spec_from_file_location("trait_checker", SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = CHECKER
SPEC.loader.exec_module(CHECKER)


def parameter(variable, value, line, raw_value=None):
    return CHECKER.Parameter(
        index=line,
        variable=variable,
        description="test",
        raw_value=str(value) if raw_value is None else raw_value,
        numeric_values=[float(value)],
        section="PHOTOSYNTHETIC PROPERTIES",
        line=line,
    )


def c4_block(values):
    block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="maiz41", start_line=1)
    block.parameters = [parameter("ICTYP", 1, 1, "C4 1.0")]
    block.parameters.extend(
        parameter(variable, value, line)
        for line, (variable, value) in enumerate(values.items(), start=2)
    )
    return block


def c3_block(values):
    block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="soyb41", start_line=1)
    block.parameters = [parameter("ICTYP", 1, 1, "C3 0.0")]
    block.parameters.extend(
        parameter(variable, value, line)
        for line, (variable, value) in enumerate(values.items(), start=2)
    )
    return block


def physiology_findings(block):
    findings = []

    def add(block, severity, parameter, line, message, category="general"):
        findings.append(
            CHECKER.Finding(
                severity=severity,
                pft_code=block.code,
                nz=block.nz,
                ny=block.ny,
                nx=block.nx,
                line=line,
                parameter=parameter,
                message=message,
                category=category,
            )
        )

    CHECKER.check_physiological_parameterization(block, CHECKER.by_var(block), add)
    return findings


def active_protein_pool_findings(block):
    findings = []

    def add(block, severity, parameter, line, message, category="general"):
        findings.append(
            CHECKER.Finding(
                severity=severity,
                pft_code=block.code,
                nz=block.nz,
                ny=block.ny,
                nx=block.nx,
                line=line,
                parameter=parameter,
                message=message,
                category=category,
            )
        )

    CHECKER.check_active_structural_protein_pools(block, CHECKER.by_var(block), add)
    return findings


def woody_form_findings(block):
    findings = []

    def add(block, severity, parameter, line, message, category="general"):
        findings.append(
            CHECKER.Finding(
                severity=severity,
                pft_code=block.code,
                nz=block.nz,
                ny=block.ny,
                nx=block.nx,
                line=line,
                parameter=parameter,
                message=message,
                category=category,
            )
        )

    CHECKER.check_woody_form(block, CHECKER.by_var(block), add)
    return findings


class PhysiologicalParameterizationTests(unittest.TestCase):
    def test_species_specific_sla_web_evidence_is_checked(self):
        block = CHECKER.PlantBlock(nz=3, ny=1, nx=1, code="woak35", start_line=1)
        block.parameters = [parameter("SLA1", 0.033, 8)]
        evidence = {
            "plants": [
                {
                    "pft_code": "woak35",
                    "numeric_ranges": [
                        {
                            "variable": "SLA1",
                            "min": 0.016,
                            "max": 0.020,
                            "unit": "m2 gC-1",
                            "source": "site-composition-weighted oak evidence",
                        }
                    ],
                }
            ]
        }

        findings = CHECKER.check_web_evidence([block], evidence)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].parameter, "SLA1")
        self.assertEqual(findings[0].severity, "WARN")

    def test_missing_pathway_is_flagged_instead_of_skipped(self):
        block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="test00", start_line=1)
        findings = physiology_findings(block)
        self.assertEqual([finding.parameter for finding in findings], ["ICTYP"])
        self.assertEqual(findings[0].category, "physiology")

    def test_excessive_maize_enzyme_allocation_is_flagged(self):
        block = c4_block(
            {
                "VCMX": 45,
                "VOMX": 12,
                "XKCO2": 30,
                "XKO2": 810,
                "RUBP": 0.10,
                "ETMX": 1400,
                "VCMX4": 400,
                "XKCO24": 3,
                "PEPC": 0.12,
                "CHL": 0.20,
                "fCHLMESO": 0.60,
                "FCO2": 0.40,
                "CNWL": 2.7,
                "CNLF": 0.04,
                "CPWL": 27,
                "CPLF": 0.004,
                "ALBR": 0.20,
                "TAUR": 0.20,
                "ALBP": 0.075,
                "TAUP": 0.075,
            }
        )
        flagged = {finding.parameter for finding in physiology_findings(block)}
        self.assertEqual(
            flagged,
            {
                "PEPC/leaf-protein-pool",
                "VCMX4*PEPC/VCMX*RUBP",
                "ETMX*CHL/VCMX*RUBP",
            },
        )

    def test_published_maize_kinetic_families_are_not_rejected(self):
        # Cousins2010 Table2: both the modern and legacy assay families
        # must pass the broad screen. Absolute VCMX is only a test scale.
        for kc, ko, vo_vc in [(16.2, 183, 0.11), (34, 810, 0.30)]:
            values = {
                "VCMX":100,"VOMX":100*vo_vc,"XKCO2":kc,"XKO2":ko,
                "RUBP":.10,"ETMX":1500,"VCMX4":400,"XKCO24":3,
                "PEPC":.035,"CHL":.20,"fCHLMESO":.60,"FCO2":.40,
                "CNWL":2.7,"CNLF":.04,"CPWL":27,"CPLF":.004,
                "ALBR":.20,"TAUR":.20,"ALBP":.075,"TAUP":.075,
            }
            with self.subTest(kc=kc,ko=ko):
                self.assertEqual(physiology_findings(c4_block(values)), [])

    def test_measured_maize_capacity_ratios_above_two_point_five(self):
        # Perdomo2016 Table3, contrasting measurement temperatures/treatments.
        # Ratios are constraints, not direct enzyme-specific activity values.
        for ratio in [133.8/29.9,219.5/31.7]:
            values = {
                "VCMX":100,"VOMX":11,"XKCO2":16.2,"XKO2":183,
                "RUBP":.10,"ETMX":1500,"VCMX4":ratio*10/.035,"XKCO24":3,
                "PEPC":.035,"CHL":.20,"fCHLMESO":.60,"FCO2":.40,
                "CNWL":2.7,"CNLF":.04,"CPWL":27,"CPLF":.004,
                "ALBR":.20,"TAUR":.20,"ALBP":.075,"TAUP":.075,
            }
            with self.subTest(ratio=ratio):
                self.assertEqual(physiology_findings(c4_block(values)), [])

    def test_extreme_maize_kinetics_still_fail(self):
        values = {
            "VCMX":100,"VOMX":200,"XKCO2":30,"XKO2":2000,
            "RUBP":.10,"ETMX":1500,"VCMX4":400,"XKCO24":3,
            "PEPC":.035,"CHL":.20,"fCHLMESO":.60,"FCO2":.40,
            "CNWL":2.7,"CNLF":.04,"CPWL":27,"CPLF":.004,
            "ALBR":.20,"TAUR":.20,"ALBP":.075,"TAUP":.075,
        }
        flags={f.parameter for f in physiology_findings(c4_block(values))}
        self.assertIn('XKO2',flags)
        self.assertIn('VCMX/VOMX',flags)

    def test_balanced_c4_parameterization_passes(self):
        block = c4_block(
            {
                "VCMX": 100,
                "VOMX": 12,
                "XKCO2": 16,
                "XKO2": 183,
                "RUBP": 0.065,
                "ETMX": 1000,
                "VCMX4": 150,
                "XKCO24": 3,
                "PEPC": 0.04,
                "CHL": 0.20,
                "fCHLMESO": 0.60,
                "FCO2": 0.35,
                "CNWL": 2.7,
                "CNLF": 0.04,
                "CPWL": 27,
                "CPLF": 0.004,
                "ALBR": 0.20,
                "TAUR": 0.20,
                "ALBP": 0.09,
                "TAUP": 0.04,
            }
        )
        self.assertEqual(physiology_findings(block), [])

    def test_float32_boundary_values_are_accepted(self):
        block = c3_block(
            {
                "VCMX": 60,
                "VOMX": 30,
                "XKCO2": 10,
                "XKO2": 500,
                "RUBP": 0.34999999403953552,
                "ETMX": 550,
                "CHL": 0.30000001192092896,
                "FCO2": 0.70,
                "CNWL": 2.8,
                "CNLF": 0.04,
                "CPWL": 28,
                "CPLF": 0.004,
                "ALBR": 0.20,
                "TAUR": 0.20,
                "ALBP": 0.075,
                "TAUP": 0.075,
            }
        )
        self.assertEqual(physiology_findings(block), [])
        self.assertTrue(CHECKER.in_range(0.30000001192092896, (0.08, 0.30)))

    def test_phosphorus_limited_leaf_pool_controls_enzyme_n_allocation(self):
        block = c4_block(
            {
                "VCMX": 100,
                "VOMX": 12,
                "XKCO2": 16,
                "XKO2": 183,
                "RUBP": 0.10,
                "ETMX": 1000,
                "VCMX4": 150,
                "XKCO24": 3,
                "PEPC": 0.12,
                "CHL": 0.20,
                "fCHLMESO": 0.60,
                "FCO2": 0.35,
                "CNWL": 2.7,
                "CNLF": 0.04,
                "CPWL": 20,
                "CPLF": 0.001,
                "ALBR": 0.20,
                "TAUR": 0.20,
                "ALBP": 0.09,
                "TAUP": 0.04,
            }
        )
        flagged = {finding.parameter for finding in physiology_findings(block)}
        self.assertNotIn("PEPC/leaf-protein-pool", flagged)

    def test_active_structural_protein_pools_accept_balanced_leaf_and_non_tree_root(self):
        block = c4_block(
            {
                "CNWL": 2.7,
                "CNLF": 0.04,
                "CPWL": 27,
                "CPLF": 0.004,
                "CNWR": 2.3,
                "CNRT": 0.025,
                "CPWR": 23,
                "CPRT": 0.0025,
            }
        )
        self.assertEqual(active_protein_pool_findings(block), [])

    def test_active_leaf_protein_pool_cannot_exceed_structural_carbon(self):
        block = c4_block(
            {
                "CNWL": 3.0,
                "CNLF": 0.40,
                "CPWL": 60,
                "CPLF": 0.02,
                "CNWR": 2.3,
                "CNRT": 0.025,
                "CPWR": 23,
                "CPRT": 0.0025,
            }
        )
        findings = active_protein_pool_findings(block)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertIn("total leaf structural C", findings[0].message)

    def test_tree_root_pool_excludes_heartwood_without_separate_concentrations(self):
        block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="ndlf35", start_line=1)
        values = {
            "CNWL": 2.6,
            "CNLF": 0.03,
            "CPWL": 26,
            "CPLF": 0.003,
            "CNWR": 3.0,
            "CNRT": 0.40,
            "CPWR": 20,
            "CPRT": 0.02,
        }
        block.parameters = [
            parameter(variable, value, line)
            for line, (variable, value) in enumerate(values.items(), start=1)
        ]
        findings = active_protein_pool_findings(block)
        self.assertEqual(len(findings), 1)
        self.assertIn("excluding lignified heartwood", findings[0].message)

    def test_woody_form_does_not_require_klgmax(self):
        block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="ndlf35", start_line=1)
        values = {
            "ROOTMAGE": 1500,
            "PhiMIN": 0.4,
            "PhiMAX": 0.7,
            "R95MAT": 0.05,
        }
        block.parameters = [
            parameter(variable, value, line)
            for line, (variable, value) in enumerate(values.items(), start=1)
        ]
        self.assertEqual(woody_form_findings(block), [])

    def test_strict_mode_only_promotes_physiology_warnings(self):
        findings = [
            CHECKER.Finding("WARN", "maiz41", 1, 1, 1, 2, "XKO2", "high", "physiology"),
            CHECKER.Finding("WARN", "maiz41", 1, 1, 1, 3, "RRAD2M", "large", "general"),
            CHECKER.Finding(
                "WARN",
                "maiz41",
                1,
                1,
                1,
                4,
                "ETMX*CHL/VCMX*RUBP",
                "model-dependent ratio",
                "physiology",
            ),
        ]
        promoted = CHECKER.enforce_strict_physiology(findings)
        severities = {finding.parameter: finding.severity for finding in promoted}
        self.assertEqual(
            severities,
            {
                "XKO2": "ERROR",
                "RRAD2M": "WARN",
                "ETMX*CHL/VCMX*RUBP": "WARN",
            },
        )


if __name__ == "__main__":
    unittest.main()
