"""Behavior tests against real small NetCDF fixtures and a namelist-driven CLI."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

import netCDF4 as nc
import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE/'scripts'))
import check_climate_readiness as checker


def weather(path, years, mode='hourly'):
    with nc.Dataset(path, 'w') as d:
        for name, size in [('year', len(years)), ('day', 366), ('ngrid', 1)]:
            d.createDimension(name, size)
        if mode == 'hourly':
            d.createDimension('hour', 24)
        d.createVariable('year', 'i4', ('year',))[:] = years
        for name, (lo, hi, units) in (checker.HOURLY if mode == 'hourly' else checker.DAILY).items():
            dims = ('year', 'day', 'hour', 'ngrid') if mode == 'hourly' else ('year', 'day', 'ngrid')
            v = d.createVariable(name, 'f4', dims, fill_value=1e30)
            v[:] = 1. if name != 'TMPN' else 0.
            v.units = units[0]
        for name in checker.AUXILIARY:
            d.createVariable(name, 'f4', ('year', 'ngrid'), fill_value=1e30)[:] = 1.


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.climate = self.root/'weather.nc'
        weather(self.climate, [2024, 2025])
        self.namelist = self.root/'case.namelist'
        self.write_nml()

    def write_nml(self, periods='2024,2025,1', start='20240101000000',
                  timer="stop_option='nyears', stop_n=2", extra=''):
        self.namelist.write_text(f"""&ecosim
case_name='test!case' ! quoted comment character
start_date='{start}'
clm_hour_file_in='weather.nc '
forc_periods={periods}
NPXS=2*30
{extra}
/ &ecosim_time
{timer}
delta_time=3600.d0
/""")

    def run_check(self, **kwargs):
        return checker.validate(self.namelist, **kwargs)

    def change(self, name, index, value):
        with nc.Dataset(self.climate, 'r+') as d:
            d[name][index] = value

    def codes(self, report):
        return [f['code'] for f in report['findings']]

    def test_complete_years_and_calendar_padding(self):
        # Non-leap day 366 is unused; leap-year day 366 is required.
        self.change('TMPH', (1,365,slice(None),0), 1e30)
        self.assertEqual(self.run_check()['status'], 'ready_with_warnings')
        self.change('TMPH', (0,365,0,0), 1e30)
        findings = [f for f in self.run_check()['findings'] if f['code']=='missing_values']
        self.assertEqual(findings[0]['examples'][0]['forcing_date'], '2024-12-31')

    def test_soap_missing_last_real_day(self):
        self.change('TMPH', (1,364,slice(None),0), 1e30)
        report = self.run_check()
        finding = next(f for f in report['findings'] if f['code']=='missing_values')
        self.assertEqual(report['status'], 'invalid')
        self.assertEqual(finding['count'],24)
        self.assertEqual(finding['examples'][0], dict(forcing_date='2025-12-31', day_of_year=365, hour_index_1based=1))

    def test_partial_forcing_array_retains_driver_defaults(self):
        self.write_nml(periods='2024,2024,1',timer="stop_option='nyears',stop_n=100")
        report=self.run_check()
        self.assertIn('inherited_forcing_periods',self.codes(report))
        self.assertEqual(report['window']['scheduled_years'],37)
        self.assertEqual(report['mapping'][1]['forcing_year'],1981)

    def test_no_literal_is_not_an_optional_climate_disable(self):
        self.write_nml(extra="clm_day_file_in='NO'")
        self.assertEqual(self.run_check()['status'],'invalid')

    def test_nan_inf_and_explicit_sentinel(self):
        self.change('TMPH', (0,0,0,0), np.nan)
        self.change('WINDH', (0,0,0,0), np.inf)
        self.change('RAINH', (0,0,0,0), -9999.)
        with nc.Dataset(self.climate,'r+') as d:
            d['RAINH'].missing_value=-9999.
        fields = {f.get('variable') for f in self.run_check()['findings'] if f['code']=='missing_values'}
        self.assertEqual(fields, {'TMPH','WINDH','RAINH'})

    def test_range_and_units(self):
        self.change('WINDH', (0,0,0,0), -1.)
        with nc.Dataset(self.climate,'r+') as d:
            d['DWPTH'].units='Pa'
        codes=self.codes(self.run_check())
        self.assertIn('physical_range', codes)
        self.assertIn('units_mismatch', codes)

    def test_packed_values_rejected_like_raw_fortran_reader(self):
        with nc.Dataset(self.climate,'r+') as d:
            d['TMPH'].scale_factor=0.1
        self.assertIn('packed_variable',self.codes(self.run_check()))

    def test_optional_pressure_checked_if_present(self):
        with nc.Dataset(self.climate,'r+') as d:
            v=d.createVariable('PATM','f4',('year','day','hour','ngrid'),fill_value=1e30)
            v[:]=101.325;v.units='kPa';v[0,0,0,0]=1e30
        self.assertTrue(any(f.get('variable')=='PATM' and f['code']=='missing_values' for f in self.run_check()['findings']))

    def test_partial_run_excludes_future_bad_data(self):
        self.write_nml(timer="stop_option='ndays',stop_n=1")
        self.change('TMPH',(0,1,0,0),1e30)
        self.assertNotEqual(self.run_check()['status'],'invalid')
        self.assertEqual(self.run_check(all_periods=True)['status'],'invalid')

    def test_midnight_and_hour_window(self):
        self.write_nml(timer="stop_option='nsteps',stop_n=1")
        self.change('TMPH',(0,0,1,0),1e30)
        self.assertNotEqual(self.run_check()['status'],'invalid')
        self.change('TMPH',(0,0,0,0),1e30)
        self.assertEqual(self.run_check()['status'],'invalid')

    def test_continuation_uses_pointer_date(self):
        seed=self.root/'old.ecosim.r.2025-12-01-000000.nc';seed.touch()
        (self.root/'rpointer.esim').write_text('./'+seed.name+'   \n')
        self.write_nml(extra='continue_run=.true.')
        self.change('TMPH',(0,0,0,0),1e30)
        report=self.run_check()
        self.assertNotEqual(report['status'],'invalid')
        self.assertEqual(report['window']['start'],'2025-12-01T00:00:00')
        self.assertEqual(report['files'][0]['required_years'],[2025])
        self.assertEqual(self.run_check(all_periods=True)['status'],'invalid')

    def test_restart_past_end_rejected(self):
        seed=self.root/'old.ecosim.r.2026-01-01-000000.nc';seed.touch()
        (self.root/'rpointer.esim').write_text(seed.name+'\n')
        self.write_nml(extra='continue_run=.true.')
        self.assertIn('outside', self.run_check()['findings'][-1]['message'])

    def test_descending_repeated_and_disabled_periods(self):
        self.write_nml(periods='2025,2024,2,1990,1999,0',timer="stop_option='nyears',stop_n=4")
        report=self.run_check()
        self.assertEqual([x['forcing_year'] for x in report['mapping']],[2025,2024,2025,2024])
        self.assertNotEqual(report['status'],'invalid')

    def test_nonleap_forcing_day366_comes_from_day365(self):
        self.write_nml(periods='2025,2025,1',timer="stop_option='nyears',stop_n=1")
        self.change('TMPH',(1,365,slice(None),0),1e30)
        self.assertNotEqual(self.run_check()['status'],'invalid')
        self.change('TMPH',(1,364,0,0),1e30)
        self.assertEqual(self.run_check()['status'],'invalid')

    def test_daily_hourly_transition(self):
        weather(self.root/'daily.nc',[2023],mode='daily')
        self.write_nml(periods='2023,2024,1',start='20230101000000',extra="clm_day_file_in='daily.nc'")
        report=self.run_check()
        self.assertNotEqual(report['status'],'invalid')
        self.assertEqual(report['files'][0]['required_years'],[2023])
        self.assertEqual(report['files'][1]['required_years'],[2024])

    def test_daily_temperature_interpolation_requires_neighbor_day(self):
        weather(self.root/'daily.nc',[2023],mode='daily')
        self.write_nml(periods='2023,2023,1,2023,2023,0,2023,2023,0',start='20230101000000',
                       timer="stop_option='nsteps',stop_n=1",extra="clm_day_file_in='daily.nc'")
        with nc.Dataset(self.root/'daily.nc','r+') as d:
            d['TMPN'][0,1,0]=1e30
        finding=next(f for f in self.run_check()['findings'] if f['code']=='missing_values')
        self.assertEqual(finding['examples'][0]['forcing_date'],'2023-01-02')

    def test_missing_year_and_auxiliary_fill(self):
        self.write_nml(periods='2025,2026,1')
        self.change('PHRG',(1,0),1e30)
        codes=self.codes(self.run_check())
        self.assertIn('missing_year',codes)
        self.assertIn('missing_values',codes)

    def test_duplicate_years_and_bad_schema_fail(self):
        with nc.Dataset(self.climate,'r+') as d:
            d['year'][:]=[2024,2024]
        self.assertEqual(self.run_check()['status'],'invalid')
        other=self.root/'bad.nc'
        with nc.Dataset(other,'w') as d:
            d.createDimension('year',1);d.createVariable('year','i4',('year',))[:]=[2024]
        self.namelist.write_text(self.namelist.read_text().replace('weather.nc','bad.nc'))
        self.assertEqual(self.run_check()['status'],'invalid')

    def test_unsupported_calendar_rejected(self):
        with nc.Dataset(self.climate,'r+') as d:
            d.calendar='360_day'
        self.assertEqual(self.run_check()['status'],'invalid')

    def test_missing_or_text_meteorology_is_a_structured_failure(self):
        with nc.Dataset(self.climate,'r+') as d:
            d.renameVariable('TMPH','OTHER')
        self.assertIn('variable_schema',self.codes(self.run_check()))
        with nc.Dataset(self.climate,'r+') as d:
            d.createVariable('TMPH',str,('year','day','hour','ngrid'))
        self.assertIn('variable_schema',self.codes(self.run_check()))

    def test_branch_date_must_match_seed(self):
        seed=self.root/'parent.ecosim.r.2024-02-01-000000.nc';seed.touch()
        self.write_nml(extra="finidat='"+seed.name+"'")
        self.assertEqual(self.run_check()['status'],'invalid')

    def test_month_stop_excludes_next_month(self):
        self.write_nml(timer="stop_option='nmonths',stop_n=1")
        self.change('TMPH',(0,31,0,0),1e30)
        report=self.run_check()
        self.assertNotEqual(report['status'],'invalid')
        self.assertEqual(report['window']['end_exclusive'],'2024-02-01T00:00:00')

    def test_invalid_namelist_and_checkpoint_typo_reported(self):
        self.namelist.write_text('&ecosim forc_periods(1)=2024 /')
        self.assertEqual(self.run_check()['status'],'invalid')
        self.write_nml(timer="stop_option='nyears',stop_n=125,rest_opt='ndayss'")
        codes=self.codes(self.run_check())
        self.assertIn('invalid_rest_opt',codes)
        self.assertIn('duration_capped',codes)

    def test_run_cwd_and_cli_reports_without_mutation(self):
        folder=self.root/'configs';folder.mkdir()
        nml=folder/'case.namelist';nml.write_text(self.namelist.read_text())
        self.assertEqual(checker.validate(nml)['status'],'invalid')
        self.assertNotEqual(checker.validate(nml,self.root)['status'],'invalid')
        before=self.climate.read_bytes()
        out=self.root/'report'
        command=[sys.executable,str(HERE/'scripts/check_climate_readiness.py'),str(nml),'--run-cwd',str(self.root),'--output-dir',str(out)]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads((out/'report.json').read_text())['status'],'ready_with_warnings')
        self.assertTrue((out/'report.md').is_file())
        self.assertEqual(self.climate.read_bytes(),before)
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,2)
        self.change('TMPH',(0,0,0,0),1e30)
        command[-1]=str(self.root/'invalid_report')
        self.assertEqual(subprocess.run(command,capture_output=True).returncode,1)


if __name__=='__main__':
    unittest.main()
