"""Native reader contracts for multi-column landscapes and recurring events."""
from Tools.test_case_preflight import PreflightTests
from Tools import case_preflight as pre
from Tools.create_ecosim_grid_forcing import validate_grid_layers
import netCDF4 as nc
import numpy as np
import unittest


class LegacyReaderTests(PreflightTests):
    def expand(self, filename, dimension, count, site_koppen=False):
        source=self.root/filename
        scratch=source.with_suffix('.expanded.nc')
        with nc.Dataset(source) as src,nc.Dataset(scratch,'w') as dst:
            for name,dim in src.dimensions.items():
                dst.createDimension(name,count if name==dimension else len(dim))
            if site_koppen:
                dst.createDimension('ngrid',1)
            for name,var in src.variables.items():
                dims=('ngrid',) if site_koppen and name=='IETYPG' else var.dimensions
                out=dst.createVariable(name,var.dtype,dims,fill_value=getattr(var,'_FillValue',None))
                out.setncatts({k:var.getncattr(k) for k in var.ncattrs() if k!='_FillValue'})
                if site_koppen and name=='IETYPG':
                    out[...]=var[:1]
                else:
                    out[...]=np.repeat(var[...],count,axis=var.dimensions.index(dimension)) if dimension in var.dimensions else var[...]
        scratch.replace(source)

    def test_recurring_date_contract(self):
        pre.validate_events({'DDMMYYYY':'31010000'})
        pre.validate_events({'DDMMYYYY':'29020000'})
        with self.assertRaises(ValueError):pre.validate_events({'DDMMYYYY':'30020000'})
        with self.assertRaises(ValueError):pre.validate_events({'DDMMYYYY':'29022001'})

    def test_all_topographic_units_are_checked(self):
        self.expand('grid.nc','ntopou',2)
        self.assertEqual(validate_grid_layers(self.root/'grid.nc'),[])
        self.edit('grid.nc','FC',(1,0),0.01)
        self.edit('grid.nc','WP',(1,0),0.2)
        self.assertTrue(any('unit 2: FC <= WP' in msg for msg in validate_grid_layers(self.root/'grid.nc')))

    def test_shared_weather_is_valid_for_multiple_cells(self):
        self.expand('grid.nc','ntopou',2)
        self.edit('grid.nc','NH1',1,2)
        self.edit('grid.nc','NH2',1,2)
        findings=self.report()['findings']
        self.assertFalse(any(f['category']=='spatial_support' and f['severity']=='error' for f in findings))

    def test_site_koppen_applies_to_all_topographic_units(self):
        self.expand('grid.nc','ntopou',2,site_koppen=True)
        self.expand('plant.nc','ntopou',2)
        self.expand('soil.nc','ntopou',2)
        for filename in ['grid.nc','plant.nc','soil.nc']:
            self.edit(filename,'NH1',1,2)
            self.edit(filename,'NH2',1,2)
        self.assertEqual(self.errors(),[])

    def test_multiple_weather_series_rejected(self):
        self.expand('weather.nc','ngrid',2)
        self.assertTrue(any(f['category']=='spatial_support' and 'ngrid=1' in f['message'] for f in self.errors()))


if __name__=='__main__':unittest.main()
