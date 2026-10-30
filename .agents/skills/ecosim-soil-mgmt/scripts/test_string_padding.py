"""Generated selectors must satisfy the consuming Fortran string comparisons."""
from pathlib import Path
import tempfile
import unittest
import netCDF4 as nc
import numpy as np
import soil_mgmt_excel_bridge as bridge


class StringPaddingTests(unittest.TestCase):
    def test_inactive_and_named_selectors_have_fortran_space_padding(self):
        cfg={'years':[2001,2002], 'topo_units':[{
            'NH1':1,'NV1':1,'NH2':1,'NV2':1,
            'years':{'2001':{'tillf':'short'},'2002':{}}}],
            'tillage_files':{'short':[]}}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'mgmt.nc'
            bridge.config_to_nc(cfg,path)
            with nc.Dataset(path) as ds:
                for name in ('fertf','tillf','irrigf'):
                    v=ds[name];v.set_auto_mask(False)
                    for row in v[:].reshape(-1,10):
                        raw=row.tobytes()
                        self.assertNotIn(b'\x00',raw)
                        selected=raw.rstrip(b' ').decode('ascii')
                        self.assertTrue(selected=='NO' or selected in ds.variables)
                self.assertEqual(ds['tillf'][1,0,:].tobytes(),b'NO        ')

    def test_overlong_selector_is_rejected_without_silent_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with nc.Dataset(Path(tmp)/'test.nc','w') as ds:
                ds.createDimension('row',1);ds.createDimension('char',10)
                v=ds.createVariable('selector','S1',('row','char'))
                with self.assertRaises(ValueError):
                    bridge.write_fixed_strlen(v,np.array(['too_long_selector']),10)


if __name__=='__main__':
    unittest.main()
