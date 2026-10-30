"""Legacy planting delimiters must preserve the three Fortran input values."""
from pathlib import Path
import tempfile
import unittest

import netCDF4 as nc
import numpy as np
import plant_mgmt_excel_bridge as bridge


class LegacyPlantingTests(unittest.TestCase):
    def test_delimiters_preserve_date_population_depth(self):
        expected = {'DDMMYYYY': '10052001', 'Planting_population': 8.2, 'Planting_depth': .05}
        for text in ['10052001,8.200000,0.05', '10052001 8.200000 0.05',
                     ' 10052001, 8.200000\t0.05 ']:
            with self.subTest(text=text):
                self.assertEqual(bridge.parse_planting(text), expected)

    def test_wrong_field_count_is_rejected(self):
        for text in ['10052001,8.2', '10052001,8.2,.05,999']:
            with self.assertRaises(ValueError):
                bridge.parse_planting(text)

    def test_legacy_netcdf_decodes_without_source_rewrite(self):
        cfg = {'pft_dflag': 1, 'years': [2001], 'topo_units': [
            {'NH1': 1, 'NV1': 1, 'NH2': 1, 'NV2': 1, 'NZ': 1, 'years': {
                '2001': {'pfts': [{'pft_type': 'maize', 'planting': {
                    'DDMMYYYY': '10052001', 'Planting_population': 8.2, 'Planting_depth': .05}, 'mgmt': []}]}}}]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'legacy.nc'
            bridge.write_nc_with_plant_mgmt_writer(cfg, path, None)
            with nc.Dataset(path, 'r+') as dataset:
                value = np.asarray(['10052001,8.200000,0.05'.ljust(128)], dtype='S128')
                dataset['pft_pltinfo'][0, 0, 0, :] = value.view('S1').reshape(128)
            before = path.read_bytes()
            result = bridge.nc_to_config(path)
            self.assertEqual(result['topo_units'][0]['years']['2001']['pfts'][0]['planting'],
                             cfg['topo_units'][0]['years']['2001']['pfts'][0]['planting'])
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
