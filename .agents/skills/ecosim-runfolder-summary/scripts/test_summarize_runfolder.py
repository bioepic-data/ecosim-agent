"""Behavior checks using small NetCDF fixtures; no EcoSIM executable required."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import netCDF4 as nc
import numpy as np

import summarize_runfolder as summary


def variable(values, dimensions=()):
    return {'values': values, 'dimensions': list(dimensions), 'units': 'unspecified'}


def plant_fixture():
    v = {k: variable([1]) for k in summary.BOUNDS}
    v.update(pft_dflag=variable(0), NZ=variable([1]),
             pft_type=variable([[['gr3s99', 'UNUSED']], [['BAD', 'BAD']]]),
             pft_pltinfo=variable([[['01019999 50 0.001 100', '']], [['', '']]]),
             nmgnts=variable([[[0, None]], [[None, None]]]))
    return {'dimensions': {'ntopou': 1, 'maxpfts': 2, 'maxpmgt': 1}, 'variables': v}


class SummaryChecks(unittest.TestCase):
    def test_namelist_preserves_quoted_paths_and_comments(self):
        nml = summary.parse_namelist("&ecosim grid_file_in='a/it''s!ok.nc ', !comment\n NPXS=3*30, hist_fincl1='A','B' / &ecosim_time delta_time=3.6d3 /")
        self.assertEqual(nml['ecosim']['grid_file_in'], "a/it's!ok.nc ")
        self.assertEqual(nml['ecosim']['npxs'], [30, 30, 30])
        self.assertEqual(nml['ecosim']['hist_fincl1'], ['A', 'B'])
        self.assertEqual(nml['ecosim_time']['delta_time'], 3600)

    def test_unsupported_namelists_fail_instead_of_truncating(self):
        for text in ('&ecosim NPXS(1)=30 /', '&ecosim NPXS=1,,3 /',
                     '&ecosim NPXS=1 NPXS=2 /', '&ecosim NPXS=1', '&ecosim NPXS=2* /',
                     "&ecosim case_name='unclosed /"):
            with self.subTest(text=text), self.assertRaises((ValueError, IndexError)):
                summary.parse_namelist(text)

    def test_constant_management_without_year_and_unused_records(self):
        mgmt = plant_fixture()
        topo = [{'NH1':1,'NH2':1,'NV1':1,'NV2':1,'Koppen':35}]
        parameters = {'variables': {'pfts': variable(['gr3s35'])}}
        rows = summary.vegetation(mgmt, topo, parameters, False, Path('/absent'), [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['effective_codes'], ['gr3s35'])
        self.assertTrue(rows[0]['parameter_catalog_checked'])
        self.assertEqual(rows[0]['planting']['maximum_population_m2'], 100)
        self.assertEqual(rows[0]['events'], [])

    def test_transient_management_requires_year(self):
        mgmt = plant_fixture()
        mgmt['variables']['pft_dflag']['values'] = 1
        with self.assertRaisesRegex(ValueError, 'missing year'):
            summary.vegetation(mgmt, [], None, False, Path('/absent'), [])

    def test_disabled_plants_need_no_other_arrays(self):
        mgmt = {'variables': {'pft_dflag': variable(-1)}}
        self.assertEqual(summary.vegetation(mgmt, [], None, False, Path('/absent'), []), [])

    def test_missing_or_malformed_active_event_is_rejected(self):
        mgmt = plant_fixture()
        mgmt['variables']['nmgnts']['values'][0][0][0] = 1
        with self.assertRaisesRegex(ValueError, 'Invalid active event'):
            summary.vegetation(mgmt, [], None, False, Path('/absent'), [])

    def test_plant_rectangle_checks_multiple_grid_climates(self):
        mgmt = plant_fixture()
        mgmt['variables']['NH2']['values'] = [2]
        topo = [{'NH1':i,'NH2':i,'NV1':1,'NV2':1,'Koppen':z} for i,z in ((1,35),(2,34))]
        rows = summary.vegetation(mgmt, topo, None, False, Path('/absent'), [])
        self.assertEqual(rows[0]['effective_codes'], ['gr3s34','gr3s35'])

    def test_nc_decode_masking_and_forcing_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'data.nc'
            with nc.Dataset(path, 'w') as ds:
                ds.createDimension('year', 2)
                ds.createDimension('nchars', 4)
                ds.createVariable('year', 'i4', ('year',))[:] = [2001,2002]
                ds.createVariable('label', 'S1', ('year','nchars'))[:] = nc.stringtochar(np.array(['a   ','b   '], dtype='S4'))
                ds.createVariable('TMPH', 'f4', ('year',))[:] = [10,20]
                ds.createVariable('missing', 'f4', ('year',), fill_value=-999.)[:] = [-999.,1]
            climate = summary.inspect_nc(path, 'clm_hour_file_in')
            self.assertEqual(summary.val(climate, 'year'), [2001,2002])
            self.assertNotIn('values', climate['variables']['TMPH'])
            grid = summary.inspect_nc(path, 'grid_file_in')
            self.assertEqual(summary.val(grid, 'label'), ['a','b'])
            self.assertEqual(summary.val(grid, 'missing'), [None,1])

    def test_cli_rejects_ambiguous_selection_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.namelist','b.namelist'):
                (root/name).write_text('&ecosim /')
            result = subprocess.run([sys.executable, str(Path(summary.__file__)), str(root)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['a.namelist','b.namelist'])

    def test_missing_inputs_are_distinct_from_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'test.namelist'
            path.write_text("&ecosim grid_file_in='missing.nc ',soil_mgmt_in='NO' /")
            report = summary.summarize(path, path.parent, Path('/absent'))
            self.assertEqual(report['inputs']['grid_file_in']['status'], 'missing')
            self.assertEqual(report['inputs']['soil_mgmt_in']['status'], 'disabled')

    def test_unused_depth_slots_do_not_expand_active_profile(self):
        v = {k: variable([1]) for k in summary.BOUNDS}
        v.update(topo_grid=variable([1]), IETYPG=variable([35]),
                 DHI=variable([[40]]), DVI=variable([[40]]),
                 NUI=variable([1]), NJ=variable([2]), NL1=variable([0]), NL2=variable([1]),
                 CDPTH=variable([[0.1,0.3,0.8,0,-999]], ['ntopou','nlevs']))
        warnings = []
        rows = summary.topo_rows({'dimensions':{'ntopou':1},'variables':v}, False, warnings)
        self.assertEqual(rows[0]['profiles']['CDPTH'], [0.1,0.3])
        self.assertEqual(rows[0]['configured_layer_end'], 3)
        self.assertEqual(rows[0]['footprint_area_m2'], 1600)
        self.assertEqual(warnings, [])


if __name__ == '__main__':
    unittest.main()
