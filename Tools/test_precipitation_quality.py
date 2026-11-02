"""Budget behavior tests using complete, partial and leap-year NetCDF data."""
from pathlib import Path
import tempfile
import unittest
import netCDF4 as nc
import numpy as np
from Tools.precipitation_quality import precipitation_budget, rainfall_hash


class PrecipitationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'climate.nc'

    def write(self, years=(2001,), annual=800, reference=800):
        with nc.Dataset(self.path,'w') as ds:
            for name,size in [('year',len(years)),('day',366),('hour',24),('ngrid',1)]:ds.createDimension(name,size)
            ds.createVariable('year','i4',('year',))[:]=years
            rain=ds.createVariable('RAINH','f4',('year','day','hour','ngrid'),fill_value=1e30);rain.units='mm h-1'
            import calendar
            for j,y in enumerate(years):
                days=366 if calendar.isleap(y) else 365;rain[j,:days,:,0]=annual/(days*24)
            if reference is not None:
                ds.precip_climatology_mm=reference;ds.precip_climatology_source='test reference'
            ds.precip_tower_validation_status='compared';ds.precip_tower_validation_rain_sha256=rainfall_hash(ds)

    def test_excessive_annual_sum_despite_valid_hourly_values(self):
        self.write(annual=3200)
        r=precipitation_budget(self.path)
        self.assertIn('precipitation_annual_climatology_mismatch',[x['code'] for x in r['findings']])
        self.assertAlmostEqual(r['annual'][0]['annual_mm'],3200,places=3)
        with nc.Dataset(self.path) as ds:self.assertLess(float(ds['RAINH'][:].max()),500)

    def test_climatology_mean_detects_consistent_twofold_bias(self):
        self.write(years=range(2001,2006),annual=1800)
        codes=[x['code'] for x in precipitation_budget(self.path)['findings']]
        self.assertIn('precipitation_multiyear_climatology_mismatch',codes)
        self.assertNotIn('precipitation_annual_climatology_mismatch',codes)

    def test_wet_site_is_not_compared_to_a_universal_rainfall_limit(self):
        self.write(annual=3200,reference=3000)
        self.assertEqual(precipitation_budget(self.path)['status'],'screened')

    def test_missing_climatology_is_unverified(self):
        self.write(reference=None)
        self.assertIn('precipitation_climatology_unverified',[x['code'] for x in precipitation_budget(self.path)['findings']])

    def test_partial_year_is_not_annualized_and_nonleap_padding_ignored(self):
        self.write()
        r=precipitation_budget(self.path);self.assertTrue(r['annual'][0]['complete'])
        self.assertAlmostEqual(sum(r['annual'][0]['monthly_mm']),800,places=3)
        with nc.Dataset(self.path,'a') as ds:ds['RAINH'][0,100,0,0]=1e30
        r=precipitation_budget(self.path);self.assertIsNone(r['annual'][0]['annual_mm']);self.assertIsNone(r['annual'][0]['seasonal_mm']['MAM'])

    def test_leap_day_and_stale_tower_status(self):
        self.write(years=[2004])
        self.assertEqual(precipitation_budget(self.path)['annual'][0]['valid_hours'],8784)
        with nc.Dataset(self.path,'a') as ds:ds['RAINH'][0,365,0,0]=2
        self.assertEqual(precipitation_budget(self.path)['measured_tower_validation_status'],'stale_or_unverified_identity')

    def test_reference_requires_provenance(self):
        self.write()
        with self.assertRaises(ValueError):precipitation_budget(self.path,mean_annual_mm=800)
