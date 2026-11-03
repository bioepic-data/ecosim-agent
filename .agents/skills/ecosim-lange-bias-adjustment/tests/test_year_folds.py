import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/make_year_folds.py'
spec = importlib.util.spec_from_file_location('year_folds', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class YearFoldTests(unittest.TestCase):
    def test_complete_disjoint_validation_and_training(self):
        plan = module.make_folds(2001, 2024, 6)
        heldout = []
        for fold in plan['folds']:
            train, val = set(fold['training_years']), set(fold['validation_years'])
            self.assertFalse(train & val)
            self.assertEqual(train | val, set(plan['years']))
            self.assertEqual(sorted(val), list(range(min(val), max(val) + 1)))
            self.assertEqual(len(val), 6)
            heldout.extend(fold['validation_years'])
        self.assertEqual(sorted(heldout), plan['years'])

    def test_partial_final_block(self):
        plan = module.make_folds(2001, 2024, 5)
        self.assertEqual(plan['folds'][-1]['validation_years'], [2021, 2022, 2023, 2024])
        self.assertEqual(len(plan['folds'][-1]['training_years']), 20)

    def test_invalid_windows(self):
        for args in [(2001, 2000, 2), (2001, 2001, 1), (2001, 2024, 24),
                     (2001, 2024, 0), (0, 2024, 5), (2001, 10000, 5)]:
            with self.assertRaises(ValueError):
                module.make_folds(*args)

    def test_cli_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'folds.json'
            command = [sys.executable, str(SCRIPT), '--start-year', '2001',
                       '--end-year', '2024', '--block-years', '6', '--output', str(out)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(out.read_text()), module.make_folds(2001, 2024, 6))
            original = out.read_bytes()
            retry = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(out.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
