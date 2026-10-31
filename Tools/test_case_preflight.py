"""Real NetCDF preflight tests; no model execution or user data required."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import netCDF4 as nc
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Tools import case_preflight as pre
from Tools.test_driving_data_conflicts import write_grid


class PreflightTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.nml = self.root / 'US-Tst.namelist'
        self.model = pre.ROOT.parent
        weather = pre.module('.agents/skills/ecosim-climate-readiness/tests/test_climate_readiness.py', 'weather_fixture')
        weather.weather(self.root / 'weather.nc', [2001, 2002])
        write_grid(self.root / 'grid-seed.nc')
        with nc.Dataset(self.root/'grid-seed.nc') as src, nc.Dataset(self.root/'grid.nc','w') as dst:
            dst.createDimension('ntopou',1)
            dst.createDimension('nlevs',20)
            for name,var in src.variables.items():
                new=dst.createVariable(name,var.dtype,var.dimensions,fill_value=getattr(var,'_FillValue',None))
                new.setncatts({k:var.getncattr(k) for k in var.ncattrs() if k!='_FillValue'})
                if var.ndim==2:new[:,:4]=var[:]
                else:new[:]=var[:]
        with nc.Dataset(self.root / 'grid.nc', 'a') as ds:
            for name in pre.required_reads(self.model / 'f90src/IOutils/readimod.F90', 'grid_nfid', ['fAeroScalar']):
                if name not in ds.variables:
                    ds.createVariable(name, 'f8', ('ntopou',))[:] = 1
            ds['IETYPG'][:] = 0
        with nc.Dataset(self.root / 'pft.nc', 'w') as ds:
            for dim, size in [('npfts',1),('npft',1),('nkopenclms',1),('char',6),('JLI',4)]:
                ds.createDimension(dim,size)
            char = dict(pfts='npfts', pfts_short='npft', pfts_long='npft',
                        koppen_clim_no='nkopenclms', koppen_clim_short='nkopenclms', koppen_clim_long='nkopenclms')
            schema = pre.required_reads(self.model / 'f90src/IOutils/PlantInfoMod.F90', 'pft_nfid', ['IROOTJK','CANHT'], procedure='ReadPlantTraitTable')
            for name in schema:
                if name in char:
                    var=ds.createVariable(name,'S1',(char[name],'char'))
                    var[:]=np.array(list('GRAS00'),dtype='S1')
                else:
                    ds.createVariable(name,'f8',('npfts','JLI') if name=='CLASS' else ('npfts',))[:]=1
        with nc.Dataset(self.root / 'ghg.nc', 'w') as ds:
            ds.createDimension('time',24)
            ds.createVariable('year','f8',('time',))[:]=2001+np.arange(1,25)/12
            for name,value,unit in [('CO2',380,'ppm'),('CH4',1800,'ppb'),('N2O',320,'ppb')]:
                v=ds.createVariable(name,'f8',('time',));v[:]=value;v.units=unit
        with nc.Dataset(self.root / 'plant.nc','w') as ds:
            for name,size in [('year',2),('ntopou',1),('maxpfts',5),('maxpmgt',24),('char',128)]:
                ds.createDimension(name,size)
            ds.createVariable('year','i4',('year',))[:]=[2001,2002]
            for name in ('NH1','NV1','NH2','NV2','NZ'):
                ds.createVariable(name,'i4',('ntopou',))[:]=1
            ds.createVariable('pft_dflag','i4')[:]=1
            v=ds.createVariable('nmgnts','i4',('year','ntopou','maxpfts'),fill_value=-9999)
            v[:,:,0]=0  # inactive slot deliberately remains masked
            for name in ('pft_type','pft_pltinfo','pft_mgmt'):
                dims=('year','ntopou','maxpfts')+(('maxpmgt',) if name=='pft_mgmt' else ())+('char',)
                v=ds.createVariable(name,'S1',dims);v[:]=b' '
                if name=='pft_type':
                    v[:,:,0,:6]=np.array(list('GRAS00'),dtype='S1')
                elif name=='pft_pltinfo':
                    for i,year in enumerate([2001,2002]):
                        value=f'0101{year} 1 0.01'
                        v[i,0,0,:len(value)]=np.array(list(value),dtype='S1')
        soil=pre.module('.agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py','soil_fixture')
        soil.config_to_nc(dict(years=[2001,2002],topo_units=[dict(NH1=1,NV1=1,NH2=1,NV2=1,years={str(y):dict(fertf='NO',tillf='NO',irrigf='NO') for y in [2001,2002]})]),self.root/'soil.nc')
        self.nml.write_text("""&ecosim
case_name='US-Tst'
start_date='20010101000000'
continue_run=.false.
forc_periods=2001,2002,1,0,0,0,0,0,0
pft_file_in='pft.nc'
grid_file_in='grid.nc'
pft_mgmt_in='plant.nc'
soil_mgmt_in='soil.nc'
atm_ghg_in='ghg.nc'
clm_hour_file_in='weather.nc'
clm_factor_in='NO'
/
&ecosim_time
delta_time=3600.
stop_n=2
stop_option='nyears'
/
""")

    def report(self):
        return pre.validate(self.nml)

    def errors(self, report=None):
        return [f for f in (report or self.report())['findings'] if f['severity']=='error']

    def edit(self, filename, name, index, value):
        with nc.Dataset(self.root/filename,'a') as ds:
            ds[name][index]=value

    def test_complete_inputs_pass_with_scientific_warnings_and_unused_padding(self):
        report=self.report()
        self.assertEqual(self.errors(report),[])
        self.assertEqual(report['status'],'ready_with_warnings')
        self.assertEqual(report['model_years'],[2001,2002])
        self.assertEqual(len(report['inputs']['grid_file_in']['sha256']),64)

    def test_namelist_typo_and_wrong_types_block(self):
        for extra in ('grid_mod=1','continue_run=1','hist_mfilt=-2'):
            with self.subTest(extra=extra):
                text=self.nml.read_text().replace('continue_run=.false.',extra)
                self.assertEqual(pre.validate(self.nml,text=text)['status'],'invalid')

    def test_strict_unsupported_syntax_blocks(self):
        text=self.nml.read_text().replace("case_name='US-Tst'", "hist_fincl1(1)='ECO_GPP_col'")
        self.assertTrue(self.errors(pre.validate(self.nml,text=text)))

    def test_missing_file_and_unsupported_factor_block(self):
        for key,value in [('pft_file_in','absent.nc'),('clm_factor_in','pft.nc')]:
            text=self.nml.read_text()
            import re
            text=re.sub(key+r"='[^']*'",key+"='"+value+"'",text)
            self.assertEqual(pre.validate(self.nml,text=text)['status'],'invalid')

    def test_full_period_climate_gap_and_short_scope(self):
        self.edit('weather.nc','TMPH',(1,200,0,0),1e30)
        self.assertTrue(self.errors())
        self.assertFalse(self.errors(pre.validate(self.nml,text=self.nml.read_text().replace('stop_n=2','stop_n=1'))))

    def test_ghg_uses_model_year_not_cycled_weather_year(self):
        text=self.nml.read_text().replace("start_date='20010101000000'","start_date='19990101000000'")
        errors=self.errors(pre.validate(self.nml,text=text))
        self.assertTrue(any(f['category']=='greenhouse_gases' for f in errors))

    def test_ghg_wrong_units_and_missing_month_block(self):
        with nc.Dataset(self.root/'ghg.nc','a') as ds:ds['CH4'].units='ppm'
        self.assertTrue(any(f['category']=='greenhouse_gases' for f in self.errors()))
        with nc.Dataset(self.root/'ghg.nc','a') as ds:
            ds['CH4'].units='ppb';ds['year'][10]=2001.5
        self.assertTrue(any(f['category']=='greenhouse_gases' for f in self.errors()))

    def test_active_management_fill_and_excess_count_block(self):
        self.edit('plant.nc','nmgnts',(0,0,0),-9999)
        self.assertTrue(any(f['category']=='plant_management' for f in self.errors()))
        self.edit('plant.nc','nmgnts',(0,0,0),25)
        self.assertTrue(any(f['category']=='plant_management' for f in self.errors()))

    def test_pft_identifier_and_topographic_mismatch_block(self):
        self.edit('pft.nc','pfts',(0,slice(0,4)),np.array(list('TREE'),dtype='S1'))
        self.assertTrue(any(f['category']=='plant_management' for f in self.errors()))
        self.edit('plant.nc','NH2',0,2)
        self.assertTrue(any('bounds' in f['message'] for f in self.errors()))

    def test_missing_soil_selector_target_blocks(self):
        with nc.Dataset(self.root/'soil.nc','a') as ds:
            ds['fertf'][0,0,:]=np.array(list('missing   '),dtype='S1')
        self.assertTrue(any(f['category']=='soil_management' for f in self.errors()))

    def test_input_change_invalidates_fresh_assessment_and_persists_failure(self):
        before=self.report()['inputs']['pft_file_in']['sha256']
        self.edit('pft.nc','VCMX',0,float('nan'))
        report=self.report()
        self.assertNotEqual(before,report['inputs']['pft_file_in']['sha256'])
        with redirect_stdout(io.StringIO()),self.assertRaises(pre.PreflightError):
            pre.enforce(self.nml)
        saved=json.loads(self.nml.with_suffix('.preflight.json').read_text())
        self.assertEqual(saved['status'],'invalid')


if __name__=='__main__':unittest.main()
