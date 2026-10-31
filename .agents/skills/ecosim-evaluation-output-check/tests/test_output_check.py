"""Behavioral tests with synthetic NetCDFs and an isolated fake executable."""
from pathlib import Path
import importlib.util
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import netCDF4 as nc
import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_outputs.py'
spec = importlib.util.spec_from_file_location('evaluation_check', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class OutputCheckTests(unittest.TestCase):
    def setUp(self):
        # Output tests use a year-only input; case preflight is tested separately.
        self.preflight = patch.object(mod, 'enforce_preflight', return_value={'status': 'ready'}).start()
        self.addCleanup(patch.stopall)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ecosim = self.root / 'EcoSIM'
        registry = self.ecosim / 'f90src/IOutils/HistData'
        registry.mkdir(parents=True)
        (registry / 'HistRegisterTest.F90').write_text("call hist_addfld1d(fname='ECO_LAI_col')\ncall hist_addfld1d(fname='ECO_GPP_col')\n")
        self.production = self.root / 'production'
        self.production.mkdir()
        self.input = self.root / 'original.nc'
        with nc.Dataset(self.input, 'w') as ds:
            ds.createDimension('year', 2)
            ds.createVariable('year', 'i4', ('year',))[:] = [2000, 2001]
        self.obs = self.root / 'observations.csv'
        self.obs.write_text('date,lai\n20000601,2\n')
        self.namelist = self.production / 'SITE.namelist'
        self.namelist.write_text(f"""&ecosim
case_name='SITE'
pft_file_in='../original.nc'
grid_file_in='../original.nc'
clm_hour_file_in='../original.nc'
pft_mgmt_in='../original.nc'
soil_mgmt_in='NO'
start_date='19770101000000'
forc_periods=2000,2001,2,2000,2001,1,2000,2001,0
continue_run=.true.
hist_fincl1='ECO_GPP_col'
/
&ecosim_time
stop_n=10
stop_option='nyears'
delta_time=3600.
/
""")
        self.data = dict(site_id='SITE', year=2000, year_reason='Test available leap year', targets=[dict(
            id='lai', status='mapped', comparison='Matching leaf area definition',
            observations=[dict(path=str(self.obs), variable='lai', valid_count=1)],
            variables=[dict(name='ECO_LAI_col', units=['m2/m2'], dimensions=['time', 'column'], temporal='mean', nonzero=True)])])
        self.manifest = self.root / 'manifest.json'
        self.save_manifest()
        self.scratch = self.root / 'scratch'
        self.source_bytes = self.namelist.read_bytes(), self.input.read_bytes()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.data))

    def prepare(self):
        return mod.prepare(self.namelist, self.manifest, self.scratch, self.ecosim)

    def history(self, count=366, start=0, filename=None, fill=False, units='m2/m2', nan=False):
        path = self.scratch / 'run_dir' / (filename or 'output_check_SITE.ecosim.h0.2000-01-01-00000.nc')
        with nc.Dataset(path, 'w') as ds:
            ds.createDimension('time', count)
            ds.createDimension('column', 2)
            ds.createDimension('hist_interval', 2)
            ds.createVariable('time_bounds', 'f8', ('time', 'hist_interval'))[:] = np.column_stack((np.arange(start,start+count),np.arange(start+1,start+count+1)))
            v = ds.createVariable('ECO_LAI_col', 'f4', ('time','column'), fill_value=1e30)
            v.units = units
            v.cell_method = 'time: mean'
            if not fill:
                v[:,0] = 2
                if nan:v[10,0] = float('nan')
        return path

    def execution(self, steps=8784, returncode=0):
        (self.scratch/'run_dir/run.log').write_text(f'wrote out restart data at nstep = {steps}\n')
        (self.scratch/'run_dir/output_check_SITE.ecosim.r.2001-01-01-00000.nc').write_bytes(b'fixture')
        mod.write_json(self.scratch/'execution.json',dict(returncode=returncode,timed_out=False))

    def test_prepare_preserves_production_and_forces_one_selected_year(self):
        plan=self.prepare()
        groups=mod.parse_namelist(Path(plan['namelist']).read_text())
        self.assertEqual(groups['ecosim']['start_date'],'20000101000000')
        self.assertEqual(groups['ecosim']['forc_periods'],[2000,2000,1,2000,2000,0,2000,2000,0])
        self.assertEqual(groups['ecosim_time']['stop_n'],1)
        self.assertEqual(groups['ecosim']['hist_fincl1'],['ECO_GPP_col','ECO_LAI_col'])
        self.assertFalse(groups['ecosim']['continue_run'])
        self.assertEqual(self.source_bytes,(self.namelist.read_bytes(),self.input.read_bytes()))
        self.assertTrue(all(Path(x['destination']).is_file() and not Path(x['destination']).is_symlink() for x in plan['input_files']))
        with self.assertRaises(ValueError):self.prepare()

    def test_nonexistent_registry_name_fails_before_writing(self):
        self.data['targets'][0]['variables'][0]['name']='invented_field'
        self.save_manifest()
        with self.assertRaisesRegex(ValueError,'absent from history registry'):self.prepare()
        self.assertFalse(self.scratch.exists())

    def test_required_inactive_field_is_enabled_through_namelist(self):
        registry=self.ecosim/'f90src/IOutils/HistData/HistRegisterTest.F90'
        registry.write_text(registry.read_text()+"call hist_addfld1d(fname='LEAF_C_pft',default='inactive')\n")
        self.data['targets'][0]['variables'].append(dict(name='LEAF_C_pft',units=['gC/m2'],dimensions=['time','pft'],temporal='mean'))
        self.save_manifest()
        self.namelist.write_text(self.namelist.read_text().replace("hist_fincl1='ECO_GPP_col'", "hist_empty_htapes=.true.\nhist_fincl1='ECO_GPP_col'\nhist_fexcl1='LEAF_C_pft','ECO_GPP_col'"))
        before=self.namelist.read_bytes(),registry.read_bytes()
        plan=self.prepare();eco=mod.parse_namelist(Path(plan['namelist']).read_text())['ecosim']
        self.assertIn('LEAF_C_pft',eco['hist_fincl1'])
        self.assertTrue(eco['hist_empty_htapes'])
        self.assertEqual(eco['hist_fexcl1'],'ECO_GPP_col')
        self.assertEqual(plan['history_activation']['removed_from_hist_fexcl1'],['LEAF_C_pft'])
        self.assertIn('LEAF_C_pft',plan['history_activation']['added_to_hist_fincl1'])
        self.assertEqual(before,(self.namelist.read_bytes(),registry.read_bytes()))

    def test_history_averaging_qualifier_not_duplicated_or_silently_changed(self):
        eco=dict(hist_fincl1=['ECO_LAI_col:A','ECO_LAI_col','ECO_GPP_col'])
        activation=mod.include_required_history(eco,{'ECO_LAI_col':{}})
        self.assertEqual(eco['hist_fincl1'],['ECO_LAI_col:A','ECO_GPP_col'])
        self.assertEqual(activation['added_to_hist_fincl1'],[])

    def test_missing_forcing_year_fails(self):
        self.data['year']=2002
        self.save_manifest()
        with self.assertRaisesRegex(ValueError,'lacks selected year'):self.prepare()

    def test_unsupported_namelist_syntax_is_not_silently_repaired(self):
        self.namelist.write_text(self.namelist.read_text().replace("hist_fincl1='ECO_GPP_col'", "hist_fincl1='ECO_GPP_col',&\n'ECO_LAI_col'"))
        with self.assertRaisesRegex(ValueError,'Unsupported namelist value'):self.prepare()
        self.assertFalse(self.scratch.exists())

    def test_manifest_cannot_drop_observation_evidence(self):
        self.data['targets'][0]['observations'][0]['valid_count']=0
        self.save_manifest()
        with self.assertRaises(ValueError):self.prepare()

    def test_complete_leap_year_with_inactive_column_passes(self):
        self.prepare();self.history();self.execution()
        self.assertEqual(mod.check(self.scratch)['status'],'passed')

    def test_incomplete_year_cannot_pass(self):
        self.prepare();self.history(count=365);self.execution()
        r=mod.check(self.scratch)
        self.assertEqual(r['status'],'failed')
        self.assertTrue(any('complete daily calendar year' in x for x in r['failures']))

    def test_good_history_cannot_hide_failed_or_incomplete_execution(self):
        self.prepare();self.history();self.execution(returncode=1)
        self.assertEqual(mod.check(self.scratch)['status'],'failed')
        self.execution(steps=8759)
        self.assertEqual(mod.check(self.scratch)['status'],'failed')
        self.execution()
        next((self.scratch/'run_dir').glob('*.ecosim.r.*.nc')).unlink()
        self.assertIn('No restart at the end of the selected year',mod.check(self.scratch)['failures'])

    def test_missing_fill_and_wrong_units_block_target(self):
        self.prepare();path=self.history(fill=True,units='kg');self.execution()
        r=mod.check(self.scratch)
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(any('entirely fill' in x for x in r['targets'][0]['reasons']))
        with nc.Dataset(path,'a') as ds:ds.renameVariable('ECO_LAI_col','not_requested')
        self.assertTrue(any('missing variable' in x for x in mod.check(self.scratch)['targets'][0]['reasons']))

    def test_unmasked_nan_cannot_pass(self):
        self.prepare();self.history(nan=True);self.execution()
        self.assertEqual(mod.check(self.scratch)['status'],'failed')

    def test_blocked_observation_mapping_prevents_overall_pass(self):
        self.data['targets'].append(dict(id='yield',status='blocked',reason='No verified harvested-production mapping',observations=[dict(path=str(self.obs),variable='production',valid_count=1)],variables=[]))
        self.save_manifest();self.prepare();self.history();self.execution()
        r=mod.check(self.scratch)
        self.assertEqual(r['status'],'incomplete')
        self.assertEqual(r['targets'][0]['status'],'passed')
        self.assertEqual(r['targets'][1]['status'],'blocked')

    def test_consistent_spatial_support_required_across_files(self):
        self.prepare();first=self.history(count=183)
        second=self.history(count=183,start=183,filename='output_check_SITE.ecosim.h0.2000-07-02-00000.nc')
        with nc.Dataset(second,'a') as ds:
            ds['ECO_LAI_col'][:,0]=np.ma.masked
            ds['ECO_LAI_col'][:,1]=2
        self.execution()
        self.assertEqual(mod.check(self.scratch)['status'],'incomplete')

    def test_changed_scratch_input_is_rejected(self):
        plan=self.prepare()
        Path(plan['input_files'][0]['destination']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Scratch input changed'):mod.check(self.scratch)

    def test_fake_executable_end_to_end(self):
        self.prepare()
        exe=self.root/'fake_model'
        exe.write_text(f'''#!{sys.executable}
import json,calendar
from pathlib import Path
import netCDF4 as nc
import numpy as np
m=json.loads(Path('../manifest.json').read_text());year=m['year'];n=366 if calendar.isleap(year) else 365
with nc.Dataset(f'output_check_SITE.ecosim.h0.{{year}}-01-01-00000.nc','w') as d:
 d.createDimension('time',n);d.createDimension('column',1);d.createDimension('hist_interval',2)
 d.createVariable('time_bounds','f8',('time','hist_interval'))[:]=np.column_stack((np.arange(n),np.arange(1,n+1)))
 v=d.createVariable('ECO_LAI_col','f4',('time','column'));v.units='m2/m2';v.cell_method='time: mean';v[:]=2
Path(f'output_check_SITE.ecosim.r.{{year+1}}-01-01-00000.nc').write_bytes(b'fixture')
print('wrote out restart data at nstep =',n*24)
''')
        exe.chmod(0o755)
        with patch.object(mod,'load_module',return_value=SimpleNamespace(validate=lambda path:dict(findings=[]))):
            r=mod.run(self.scratch,exe,timeout_seconds=20)
        self.assertEqual(r['status'],'passed')
        self.assertEqual(r['execution']['executable_sha256'],mod.digest(exe))
        with self.assertRaisesRegex(ValueError,'already been run'):mod.run(self.scratch,exe)
        self.assertEqual(self.source_bytes,(self.namelist.read_bytes(),self.input.read_bytes()))

    def test_climate_error_prevents_execution(self):
        self.prepare();exe=self.root/'fake_model';exe.write_text('#!/bin/sh\nexit 0\n');exe.chmod(0o755)
        with patch.object(mod,'load_module',return_value=SimpleNamespace(validate=lambda path:dict(findings=[dict(severity='error')]))):
            with self.assertRaisesRegex(ValueError,'Climate readiness failed'):mod.run(self.scratch,exe)
        self.assertFalse((self.scratch/'execution.json').exists())


    def test_whole_production_preflight_blocks_one_year_execution(self):
        self.prepare()
        exe=self.root/'fake_model'
        exe.write_text('#!/bin/sh\nexit 0\n');exe.chmod(0o755)
        self.preflight.side_effect=ValueError('invalid production forcing year')
        with patch.object(mod.subprocess,'Popen') as launch:
            with self.assertRaisesRegex(ValueError,'invalid production forcing year'):
                mod.run(self.scratch,exe)
        launch.assert_not_called()
        self.assertFalse((self.scratch/'execution.json').exists())
        self.assertEqual(Path(self.preflight.call_args.args[0]),self.namelist.resolve())


if __name__=='__main__':unittest.main()
