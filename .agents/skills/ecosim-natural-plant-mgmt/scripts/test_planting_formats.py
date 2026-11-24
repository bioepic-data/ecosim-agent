"""Check legacy and cultivar planting fields through JSON/Excel/NetCDF tools."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import plant_mgmt_excel_bridge as bridge

spec = importlib.util.spec_from_file_location('writer', bridge.locate_writer(None))
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


class PlantingFormats(unittest.TestCase):
    def test_roundtrip_formats(self):
        strings = ['15052016 37.1 0.03', '15052016 37.1 0.03 999',
                   '15052016 37.1 0.03 1 12', '15052016 37.1 0.03 0 99']
        plantings = [bridge.parse_planting(s) for s in strings]
        for planting in plantings:
            record = writer.build_pft_pltinfo({'planting': planting})
            self.assertEqual(bridge.parse_planting(record), planting)
        cfg = {'pft_dflag': 1, 'years': [2016], 'topo_units': [
            {'NH1': 1, 'NV1': 1, 'NH2': 1, 'NV2': 1, 'NZ': 4,
             'years': {'2016': {'pfts': [
                 {'pft_type': 'soyb41', 'planting': p, 'mgmt': []} for p in plantings]}}}]}
        with tempfile.TemporaryDirectory(prefix='planting-formats-') as td:
            td = Path(td)
            first, second, xlsx = td/'first.nc', td/'second.nc', td/'planting.xlsx'
            bridge.write_nc_with_plant_mgmt_writer(cfg, first, None)
            decoded = bridge.nc_to_config(first)
            bridge.write_xlsx(xlsx, bridge.config_to_sheet_rows(decoded))
            rebuilt = bridge.sheet_rows_to_config(bridge.read_xlsx(xlsx))
            bridge.write_nc_with_plant_mgmt_writer(rebuilt, second, None)
            again = bridge.nc_to_config(second)
            for actual in (decoded, rebuilt, again):
                self.assertEqual([p['planting'] for p in actual['topo_units'][0]['years']['2016']['pfts']], plantings)

    def test_old_workbook_columns(self):
        planting = bridge.parse_planting('15052016 37.1 0.03')
        cfg = {'pft_dflag': 1, 'years': [2016], 'topo_units': [
            {'NH1': 1, 'NV1': 1, 'NH2': 1, 'NV2': 1, 'NZ': 1,
             'years': {'2016': {'pfts': [{'pft_type': 'soyb41', 'planting': planting, 'mgmt': []}]}}}]}
        rows = bridge.config_to_sheet_rows(cfg)
        sheet = rows[bridge.PFT_SHEET]
        keep = [i for i, n in enumerate(sheet[0]) if n not in ('Maximum_population', 'Read_GROUPX', 'GROUPX')]
        rows[bridge.PFT_SHEET] = [[row[i] for i in keep] for row in sheet]
        result = bridge.sheet_rows_to_config(rows)
        self.assertEqual(result['topo_units'][0]['years']['2016']['pfts'][0]['planting'], planting)

    def test_reject_invalid_override(self):
        for extra in ['2 12', '1.5 12', '1 -1', '1 NaN', '1 Infinity', '1 12 99']:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                bridge.parse_planting('15052016 37.1 0.03 '+extra)
        p = bridge.parse_planting('15052016 37.1 0.03 1 12')
        for values in [dict(Read_GROUPX=2), dict(Read_GROUPX=1.5),
                       dict(GROUPX=-1), dict(GROUPX=float('nan')),
                       dict(GROUPX=float('inf')), dict(Maximum_population=999)]:
            bad = copy.deepcopy(p)
            bad.update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                writer.build_pft_pltinfo({'planting': bad})


if __name__ == '__main__':
    unittest.main()
