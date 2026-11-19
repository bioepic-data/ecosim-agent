"""Soybean angle preference stays separate from physiological validity."""
import unittest
from test_physiological_parameterization import CHECKER, parameter


class SoybeanAngleDefaultTests(unittest.TestCase):
    def findings(self, code, value):
        block = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code=code, start_line=1)
        block.parameters = [parameter('ANGSH', value, 2)]
        found = []

        def add(b, severity, name, line, message, category='general'):
            found.append(CHECKER.Finding(severity, b.code, b.nz, b.ny, b.nx,
                                         line, name, message, category))

        CHECKER.check_petiole_sheath_angle(block, CHECKER.by_var(block), add)
        return found

    def test_all_soybean_climates_accept_45(self):
        for code in ('soyb31', 'soyb33', 'soyb41'):
            with self.subTest(code=code):
                self.assertEqual(self.findings(code, 45), [])

    def test_deviations_remain_warnings_in_strict_mode(self):
        for value in (0, 60):
            with self.subTest(value=value):
                found = CHECKER.enforce_strict_physiology(self.findings('soyb41', value))
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].category, 'user-default')
                self.assertEqual(found[0].severity, 'WARN')

    def test_other_petiole_bearing_pfts_keep_their_own_angles(self):
        for code in ('maiz41', 'clva34', 'gr3a34'):
            with self.subTest(code=code):
                self.assertEqual(self.findings(code, 60), [])

    def test_organ_absence_rule_still_applies(self):
        found = self.findings('ndlf35', 45)
        self.assertTrue(any(f.severity == 'ERROR' for f in found))
        self.assertEqual(self.findings('ndlf35', 0), [])


if __name__ == '__main__':
    unittest.main()
