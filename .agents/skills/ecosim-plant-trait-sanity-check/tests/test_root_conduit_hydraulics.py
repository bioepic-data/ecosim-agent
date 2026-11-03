#!/usr/bin/env python3

import importlib.util
import math
import struct
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_plant_trait_desc.py"
SPEC = importlib.util.spec_from_file_location("trait_checker_root_hydraulics", SCRIPT)
CHECKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = CHECKER
SPEC.loader.exec_module(CHECKER)


def parameter(variable, value, line):
    return CHECKER.Parameter(
        index=line,
        variable=variable,
        description="test",
        raw_value=str(value),
        numeric_values=[float(value)],
        section="ROOT CHARACTERISTICS",
        line=line,
    )


def hydraulic_findings(code, values, include_default_rsrr=True):
    values = dict(values)
    if include_default_rsrr:
        values.setdefault("RSRR", 5000.0)
    block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code=code, start_line=1)
    block.parameters = [
        parameter(variable, value, line)
        for line, (variable, value) in enumerate(values.items(), start=1)
    ]
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

    params = CHECKER.by_var(block)
    CHECKER.check_variable_ranges(block, params, add)
    CHECKER.check_root_radial_hydraulics(block, params, add)
    CHECKER.check_root_conduit_hydraulics(block, params, add)
    return findings


class RootConduitHydraulicsTests(unittest.TestCase):
    def test_plant_summary_always_reports_rsrr_conversion(self):
        block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="ndlf35", start_line=1)
        block.parameters = [parameter("RSRR", 5000.0, 1)]
        summary = CHECKER.block_summary(block, [])
        self.assertEqual(summary["rsrr_mpa_h_per_m"], 5000.0)
        self.assertAlmostEqual(summary["root_radial_conductivity_m_per_s_mpa"], 5.555555555555556e-8)

    def test_defensible_conifer_rsrr_passes(self):
        findings = hydraulic_findings(
            "ndlf35",
            {"RSRR": 5000.0, "RRAD2M": 2.5e-4, "RVSR": 15.2e-6, "ARSRA": 2.0},
        )
        self.assertEqual(findings, [])

    def test_high_conifer_rsrr_reports_implied_conductivity(self):
        findings = hydraulic_findings(
            "ndlf35",
            {"RSRR": 27777.78, "RRAD2M": 2.5e-4, "RVSR": 15.2e-6, "ARSRA": 2.0},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].parameter, "RSRR")
        self.assertEqual(findings[0].category, "hydraulics")
        self.assertIn("1e-08 m s-1 MPa-1", findings[0].message)
        self.assertIn("micropores dry", findings[0].message)

    def test_missing_rsrr_is_an_error(self):
        findings = hydraulic_findings(
            "ndlf35",
            {"RRAD2M": 2.5e-4, "RVSR": 15.2e-6, "ARSRA": 2.0},
            include_default_rsrr=False,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertEqual(findings[0].parameter, "RSRR")

    def test_nonconifer_rsrr_outside_broad_conductivity_screen_warns(self):
        findings = hydraulic_findings(
            "maiz41",
            {"RSRR": 100000.0, "RRAD2M": 2.5e-4, "RVSR": 10.0e-6, "ARSRA": 2.0},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].parameter, "RSRR")
        self.assertEqual(findings[0].severity, "WARN")

    def test_ponderosa_root_conduit_radius_passes(self):
        findings = hydraulic_findings(
            "ndlf35",
            {"RRAD2M": 2.5e-4, "RVSR": 15.2e-6, "ARSRA": 2.0},
        )
        self.assertEqual(findings, [])

    def test_small_conifer_radius_is_flagged_as_literal_anatomy(self):
        findings = hydraulic_findings(
            "ndlf35",
            {"RRAD2M": 2.5e-4, "RVSR": 1.413889e-6, "ARSRA": 2.0},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].parameter, "RVSR")
        self.assertEqual(findings[0].category, "hydraulics")
        self.assertIn("diameter", findings[0].message)
        self.assertIn("use ARSRA", findings[0].message)

    def test_impossible_conduit_count_is_an_error(self):
        findings = hydraulic_findings(
            "maiz41",
            {"RRAD2M": 1.0e-4, "RVSR": 6.0e-5, "ARSRA": 2.0},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertEqual(findings[0].parameter, "RVSR/RRAD2M")
        self.assertIn("expected at least one conduit", findings[0].message)

    def test_us_ne1_radius_relationship_and_worked_limit(self):
        findings = hydraulic_findings(
            "maiz41", {"RRAD2M": 5e-5, "RVSR": 2.5e-5, "ARSRA": 2.0}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertIn("0.8 conduits", findings[0].message)
        self.assertIn("RVSR <= sqrt(0.2)*RRAD2M = 2.23607e-05 m", findings[0].message)
        self.assertIn("do not automatically change either radius", findings[0].message)

    def test_one_conduit_boundary_accepts_float32_rounding_but_rejects_excess(self):
        def single(value):
            return struct.unpack('f', struct.pack('f', value))[0]
        radius = 5e-5
        limit = math.sqrt(.2) * radius
        for vessel, expected in [(1.25e-5, False), (limit, False),
                                 (single(limit), False), (limit * 1.0001, True)]:
            with self.subTest(vessel=vessel):
                findings = hydraulic_findings(
                    "maiz41", {"RRAD2M": single(radius), "RVSR": vessel, "ARSRA": 2.0}
                )
                self.assertEqual(any(f.parameter == "RVSR/RRAD2M" for f in findings), expected)

    def test_geometry_is_reported_for_pass_and_failure_without_universal_count_limit(self):
        for vessel, expected in [(1.388889e-6, .2*(5e-5/1.388889e-6)**2),
                                 (1.25e-5, 3.2), (2.5e-5, .8)]:
            with self.subTest(vessel=vessel):
                block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code="maiz41", start_line=1)
                block.parameters = [parameter("RRAD2M", 5e-5, 1), parameter("RVSR", vessel, 2)]
                summary = CHECKER.block_summary(block, [])
                self.assertAlmostEqual(summary['implied_root_conduit_count'], expected)
                self.assertAlmostEqual(summary['max_root_conduit_radius_for_one_m'], math.sqrt(.2)*5e-5)
                self.assertEqual(summary['root_lumen_area_fraction'], .2)
                rendered = CHECKER.render_markdown(Path('read_only.desc'), [block], [block], [], 1, 1, None, True)
                self.assertIn('N_conduits', rendered)
                self.assertIn('RVSR limit for N>=1', rendered)
        # No root-class-specific evidence justifies a universal count ceiling.
        self.assertEqual(hydraulic_findings(
            "maiz41", {"RRAD2M": 5e-5, "RVSR": 1.388889e-6, "ARSRA": 2.0}), [])

    def test_incomplete_or_invalid_geometry_has_no_derived_count(self):
        for values in [{"RVSR": 1e-5}, {"RRAD2M": 5e-5},
                       {"RRAD2M": 0, "RVSR": 1e-5},
                       {"RRAD2M": 5e-5, "RVSR": float('nan')}]:
            with self.subTest(values=values):
                params = {k: [parameter(k, v, i)] for i, (k, v) in enumerate(values.items(), 1)}
                self.assertIsNone(CHECKER.root_conduit_geometry(params)['implied_root_conduit_count'])

    def test_arsra_below_one_is_flagged(self):
        findings = hydraulic_findings(
            "maiz41",
            {"RRAD2M": 2.5e-4, "RVSR": 10.0e-6, "ARSRA": 0.8},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].parameter, "ARSRA")
        self.assertEqual(findings[0].category, "hydraulics")
        self.assertIn("expected ARSRA >= 1", findings[0].message)

    def test_missing_rvsr_is_an_error(self):
        findings = hydraulic_findings("ndlf35", {"RRAD2M": 2.5e-4, "ARSRA": 2.0})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertEqual(findings[0].parameter, "RVSR")

    def test_missing_arsra_is_an_error(self):
        findings = hydraulic_findings("ndlf35", {"RRAD2M": 2.5e-4, "RVSR": 15.2e-6})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "ERROR")
        self.assertEqual(findings[0].parameter, "ARSRA")


if __name__ == "__main__":
    unittest.main()
