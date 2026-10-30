"""Regression checks for isolating one site's management in network BIF data."""
import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

import extract_bif_mgmt_to_ecosim_excel as extractor


class SiteSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = []
        for site, year, seeds in [('US-Aa1', 2017, 32000), ('US-Bb1', 2018, 40000)]:
            # Shared group IDs across sites exercise isolation before aggregation.
            for variable, value in [('DM_PLANTING', 'Sowing crop seeds'),
                                    ('DM_DATE_START', f'{year}05010000'),
                                    ('DM_COMMENT', f'Corn, {seeds} seeds/acre')]:
                self.rows.append(dict(SITE_ID=site, GROUP_ID='1',
                    VARIABLE_GROUP='GRP_DM_PLANTING', VARIABLE=variable, DATAVALUE=value))

    def write_csv(self, rows):
        path = self.root / 'network.csv'
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=extractor.REQUIRED_BIF_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_extract(self, path, *args):
        output = self.root / 'output'
        with redirect_stdout(io.StringIO()):
            code = extractor.main([str(path), '--out-dir', str(output), *args])
        self.assertEqual(code, 0)
        return output

    def test_csv_and_xlsx_select_only_requested_site(self):
        csv_path = self.write_csv(self.rows)
        xlsx_path = self.root / 'network.xlsx'
        headers = extractor.REQUIRED_BIF_COLUMNS
        extractor.write_workbook(xlsx_path, {'AMF-BIF': [headers] + [
            [row[k] for k in headers] for row in self.rows]})
        for path in [csv_path, xlsx_path]:
            with self.subTest(format=path.suffix):
                out = self.run_extract(path, '--site-id', 'US-Aa1')
                cfg = json.loads((out / 'US-Aa1_plant_mgmt.json').read_text())
                self.assertEqual(cfg['years'], [2017])
                crop = cfg['topo_units'][0]['years']['2017']['pfts'][0]
                self.assertEqual(crop['planting']['DDMMYYYY'], '01052017')
                self.assertAlmostEqual(crop['planting']['Planting_population'], 32000 / 4046.8564224)
                self.assertFalse((out / 'US-Bb1_plant_mgmt.json').exists())

    def test_multi_site_input_requires_selection(self):
        with self.assertRaisesRegex(SystemExit, 'Multiple SITE_ID'):
            self.run_extract(self.write_csv(self.rows))
        self.assertFalse((self.root / 'output').exists())

    def test_missing_site_cannot_rename_another_sites_data(self):
        with self.assertRaisesRegex(SystemExit, 'No BIF rows'):
            self.run_extract(self.write_csv(self.rows), '--site-id', 'US-No1')
        self.assertFalse((self.root / 'output').exists())

    def test_single_site_is_inferred(self):
        out = self.run_extract(self.write_csv(self.rows[:3]))
        self.assertTrue((out / 'US-Aa1_plant_mgmt.json').is_file())


if __name__ == '__main__':
    unittest.main()
