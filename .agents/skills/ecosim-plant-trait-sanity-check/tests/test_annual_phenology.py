"""Annual life cycle alone does not imply human-seeded crop phenology."""
import unittest
from test_physiological_parameterization import CHECKER, parameter


class AnnualPhenologyTests(unittest.TestCase):
    def block(self, code='gr3a34', phenology='Drought deciduous 2'):
        b = CHECKER.PlantBlock(nz=1, ny=1, nx=1, code=code, start_line=1)
        b.parameters = [parameter('ISTYP', 0, 1, 'Annual 0')]
        if phenology is not None:
            b.parameters.append(parameter('IWTYP', int(phenology.split()[-1]), 2, phenology))
        return b

    def findings(self, block, rule=None):
        found = []
        def add(*args, **kwargs): found.append(args)
        if rule is None:
            CHECKER.check_class_information_conventions(block, CHECKER.by_var(block), add)
        else:
            CHECKER.check_web_categorical(block, CHECKER.by_var(block), rule, add)
        return found

    def test_natural_annual_grass_and_clover_allow_drought_deciduous(self):
        for code in ('gr3a34', 'clva34'):
            with self.subTest(code=code):
                self.assertEqual(self.findings(self.block(code)), [])

    def test_unknown_management_context_is_not_assumed_from_crop_code(self):
        self.assertEqual(self.findings(self.block('maiz41')), [])

    def test_explicit_annual_crop_evergreen_expectation_is_checked(self):
        rule = dict(variable='IWTYP', contains='Evergreen', source='Human-seeded annual crop configuration')
        self.assertEqual(len(self.findings(self.block('maiz41'), rule)), 1)
        self.assertEqual(self.findings(self.block('maiz41', 'Evergreen 0'), rule), [])

    def test_natural_annual_web_evidence_is_not_silently_filtered(self):
        rule = dict(variable='IWTYP', contains='Drought deciduous', source='Self-seeding Mediterranean annual vegetation')
        self.assertEqual(self.findings(self.block(), rule), [])
        self.assertEqual(len(self.findings(self.block(phenology='Evergreen 0'), rule)), 1)

    def test_missing_phenology_requests_context_without_universal_rule(self):
        f = self.findings(self.block(phenology=None))
        self.assertEqual(len(f), 1)
        self.assertNotIn('treats annuals as evergreen', f[0][4])


if __name__ == '__main__':
    unittest.main()
