"""Behavioral checks for site evidence, layer selection and physical conflicts."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import netCDF4 as nc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Tools.driving_data_conflicts import assess, write_report
from Tools.create_ecosim_grid_forcing import validate_grid_layers

SITE = 'US-Tst'


def write_grid(path):
    """Small physically consistent column with three supplied/one derived layer."""
    with nc.Dataset(path, 'w') as ds:
        ds.createDimension('ntopou', 1)
        ds.createDimension('nlevs', 4)
        scalars = dict(NJ=2, NL1=1, NL2=1, NUI=1, ALATG=45, ALTIG=200,
                       ATCAG=6, IETYPG=42, SL0=0.5, PSIFC=-0.033)
        for name, value in scalars.items():
            ds.createVariable(name, 'f8', ('ntopou',))[:] = value
        profiles = dict(CDPTH=[.01, .1, .3], BKDSI=1.3, CSAND=200, CSILT=500,
                        ROCK=0, PH=6, CORGC=15, FHOL=.05, AEC=1, CORGR=.2,
                        FC=.3, WP=.1, SCNV=10, SCNH=10, CEC=20, THI=-1)
        for name, values in profiles.items():
            v = ds.createVariable(name, 'f8', ('ntopou', 'nlevs'), fill_value=-999.9)
            v[0, :3] = values


def write_site(directory, site=SITE):
    data = dict(site_name=site, ALATG=45, ALONG=-85, ALTIG=200, ATCAG=6, IETYPG=42)
    (directory / f'{site}_ecosim_site.json').write_text(json.dumps(data))


class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.grid = self.directory / f'{SITE}_ecosim_grid.nc'
        write_grid(self.grid)
        write_site(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, name, index, value):
        with nc.Dataset(self.grid, 'a') as ds:
            ds[name][index] = value

    def codes(self):
        return {f['code'] for f in assess(SITE, self.directory, self.grid)['findings']}

    def test_derived_fill_not_treated_as_supplied(self):
        self.assertEqual(validate_grid_layers(self.grid), [])

    def test_nl1_layer_is_checked(self):
        self.edit('BKDSI', (0, 2), -999.9)
        self.assertTrue(any('BKDSI' in p for p in validate_grid_layers(self.grid)))

    def test_depth_texture_porosity_conflicts(self):
        self.edit('CDPTH', (0, 2), .05)
        self.edit('CSAND', (0, 0), 800)
        self.edit('FC', (0, 0), .8)
        problems = validate_grid_layers(self.grid)
        self.assertTrue(any('increasing' in p for p in problems))
        self.assertTrue(any('CSAND + CSILT' in p for p in problems))
        self.assertTrue(any('porosity' in p for p in problems))

    def test_estimation_code_distinct_from_fill(self):
        self.edit('FC', (0, 0), -1)
        self.assertEqual(validate_grid_layers(self.grid), [])
        self.edit('FC', (0, 0), -999.9)
        self.assertTrue(validate_grid_layers(self.grid))

    def test_site_slope_percent_converted_to_degrees(self):
        records = [dict(SITE_ID=SITE, GROUP_ID=1, TERRAIN='Gentle slope (<2 %)')]
        (self.directory / f'{SITE}_BIF_records.json').write_text(json.dumps(records))
        self.assertNotIn('site_constraint_conflict', self.codes())
        self.edit('SL0', 0, 10)
        self.assertIn('site_constraint_conflict', self.codes())

    def test_mixed_site_records_rejected(self):
        (self.directory / f'{SITE}_BIF_records.json').write_text(json.dumps([
            dict(SITE_ID=SITE), dict(SITE_ID='US-Ne2')]))
        self.assertIn('field_site_identity', self.codes())

    def test_wrong_location_and_cached_sign_rechecked(self):
        path = self.directory / f'{SITE}_ecosim_site.json'
        data = json.loads(path.read_text())
        data['ALATG'] = -45
        data['_location_validation'] = {'status': 'passed'}
        path.write_text(json.dumps(data))
        self.assertIn('site_location', self.codes())
        self.assertIn('grid_site_conflict', self.codes())

    def test_pressure_basis_and_ice_semantics(self):
        (self.directory / f'profile_{SITE}_source.json').write_text(json.dumps(
            {'input': {'gdb': 'gSSURGO.gdb'}, 'sources_by_variable': {'FC': ['gssurgo']}}))
        self.edit('PSIFC', 0, -.01)
        self.edit('THI', (0, 0), 0)
        self.assertIn('field_capacity_pressure', self.codes())
        self.assertIn('initial_ice_semantics', self.codes())

    def test_missing_evidence_is_not_passed(self):
        result = assess(SITE, self.directory, self.grid)
        self.assertEqual(result['status'], 'warning')
        self.assertTrue(result['input_sha256'])

    def test_malformed_input_reported_as_failure(self):
        (self.directory / f'{SITE}_ecosim_site.json').write_text('{')
        result = write_report(SITE, self.directory, self.grid)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue((self.directory / f'{SITE}_driving_data_conflicts.json').exists())

    def test_chronology_and_missing_management_year_detected(self):
        data = self.directory / 'management.nc'
        with nc.Dataset(data, 'w') as ds:
            ds.createDimension('year', 1)
            ds.createVariable('year', 'i4', ('year',))[:] = [2018]
        namelist = self.directory / 'case.namelist'
        namelist.write_text("start_date='20180101000000'\nstop_option='nyears'\n"
                            "stop_n=3\nforc_periods=2018,2019,1\npft_mgmt_in='management.nc'\n")
        result = assess(SITE, self.directory, self.grid, namelist=namelist)
        codes = {f['code'] for f in result['findings']}
        self.assertIn('duration_conflict', codes)
        self.assertIn('forcing_years_missing', codes)

    def volume_case(self, fraction, corrected=False, water_corrected=False):
        profile = {'input': {'gdb': 'gSSURGO.gdb'},
                   'volume_basis': {'BKDSI': 'fine_earth_bulk', 'FC': 'fine_earth_bulk',
                                    'WP': 'fine_earth_bulk', 'macropores_at_retention': 'drained',
                                    'density_includes_model_macropores': True,
                                    'density_pore_basis_evidence': 'Synthetic intact total-volume reference, including the explicit macro domain.'},
                   'interpolated': {'CDPTH': [.01, .1, .3], 'BKDSI': [1.65]*3,
                                    'FC': [.351]*3, 'WP': [.24]*3}}
        (self.directory / f'profile_{SITE}_source.json').write_text(json.dumps(profile))
        with nc.Dataset(self.grid, 'a') as ds:
            ds['BKDSI'][0, :3] = 1.65/(1-fraction) if corrected else 1.65
            ds['FHOL'][0, :3] = fraction
            ds['CORGC'][0, :3] = 7.25
            ds['FC'][0, :3] = .351/(1-fraction) if water_corrected else .351
            ds['WP'][0, :3] = .24/(1-fraction) if water_corrected else .24
        return assess(SITE, self.directory, self.grid)

    def test_source_bulk_density_not_preserved_by_direct_insertion(self):
        r = self.volume_case(.15)
        codes = {f['code'] for f in r['findings']}
        self.assertIn('density_volume_basis_conflict', codes)
        self.assertIn('source_water_capacity_drained_macropores', codes)
        row = r['soil_volume_audit']['layers'][0]
        self.assertAlmostEqual(row['soil_mass_per_total_volume'], 1.4025)
        self.assertAlmostEqual(row['particle_density'], 2.64207272727)
        self.assertAlmostEqual(row['pore_fraction_total_layer'], .46916677756)
        finding = next(f for f in r['findings'] if f['code']=='density_volume_basis_conflict')
        self.assertEqual(finding['conflict_type'], 'volume_basis_mass_mismatch')
        self.assertAlmostEqual(finding['evidence']['soil_mass_change_percent'], -15)
        self.assertIn('1.4025', finding['explanation'])
        self.assertIn('C/N/P', finding['consequence'])
        self.assertIn('density-only', finding['resolution'])

    def test_density_only_correction_exceeds_matrix_capacity(self):
        r = self.volume_case(.15, corrected=True)
        codes = {f['code'] for f in r['findings']}
        self.assertNotIn('density_volume_basis_conflict', codes)
        self.assertIn('matrix_water_capacity', codes)

    def test_one_percent_still_needs_mass_correction(self):
        r = self.volume_case(.01)
        self.assertIn('density_volume_basis_conflict', {f['code'] for f in r['findings']})
        self.assertAlmostEqual(r['soil_volume_audit']['layers'][0]['soil_mass_per_total_volume'], 1.6335)

    def test_consistent_one_percent_case_passes_volume_constraints(self):
        r = self.volume_case(.01, corrected=True, water_corrected=True)
        self.assertFalse(r['soil_volume_audit']['findings'])
        row = r['soil_volume_audit']['layers'][0]
        self.assertAlmostEqual(row['soil_mass_per_total_volume'], 1.65)
        self.assertAlmostEqual(row['pore_fraction_total_layer'], 1-1.65/row['particle_density'])

    def test_unknown_density_pore_support_is_conditional_even_if_drained(self):
        self.volume_case(.15)
        p = self.directory / f'profile_{SITE}_source.json'
        source = json.loads(p.read_text())
        del source['volume_basis']['density_includes_model_macropores']
        p.write_text(json.dumps(source))
        r = assess(SITE, self.directory, self.grid)
        findings = r['soil_volume_audit']['findings']
        self.assertTrue(findings)
        self.assertFalse(any(f['severity']=='error' for f in findings))
        f = next(f for f in r['findings'] if f['code']=='density_volume_basis_conflict')
        self.assertEqual(f['conflict_type'], 'conditional_volume_basis')

    def test_density_pore_declaration_requires_evidence(self):
        self.volume_case(.15)
        p = self.directory / f'profile_{SITE}_source.json'
        source = json.loads(p.read_text())
        del source['volume_basis']['density_pore_basis_evidence']
        p.write_text(json.dumps(source))
        r = assess(SITE, self.directory, self.grid)
        self.assertFalse(any(f['severity']=='error' for f in r['soil_volume_audit']['findings']))

    def test_documented_matrix_source_has_no_total_mass_conflict(self):
        self.volume_case(.15)
        p = self.directory / f'profile_{SITE}_source.json'
        source = json.loads(p.read_text())
        source['volume_basis']['density_includes_model_macropores'] = False
        source['volume_basis']['density_pore_basis_evidence'] = 'Synthetic matrix clod excluding the model external structural voids.'
        p.write_text(json.dumps(source))
        r = assess(SITE, self.directory, self.grid)
        self.assertFalse(r['soil_volume_audit']['findings'])
        # A real matrix-capacity violation must still fail regardless of source basis.
        with nc.Dataset(self.grid, 'a') as ds:
            ds['FC'][0, :3] = .9
        r = assess(SITE, self.directory, self.grid)
        self.assertTrue(any(f['code']=='matrix_water_capacity' and f['severity']=='error'
                            for f in r['soil_volume_audit']['findings']))

    def test_organic_adjusted_particle_density_used(self):
        with nc.Dataset(self.grid, 'a') as ds:
            ds['CORGC'][0, :3] = 200
            ds['BKDSI'][0, :3] = 2.4
            ds['FC'][0, :3] = .05
            ds['WP'][0, :3] = .01
        self.assertTrue(any('porosity' in p for p in validate_grid_layers(self.grid)))
        self.assertIn('particle_density_conflict', self.codes())

    def test_unknown_occupancy_does_not_become_drained_measurement(self):
        self.volume_case(.15)
        p = self.directory / f'profile_{SITE}_source.json'
        source = json.loads(p.read_text())
        del source['volume_basis']['macropores_at_retention']
        p.write_text(json.dumps(source))
        r = assess(SITE, self.directory, self.grid)
        conditional = [f for f in r['findings'] if f['code']=='source_water_capacity_drained_macropores']
        self.assertTrue(conditional)
        self.assertTrue(all(f['severity']=='warning' for f in conditional))
        self.assertTrue(all(f['conflict_type']=='conditional_pore_capacity' for f in conditional))
        self.assertTrue(all(f['evidence']['excess_pore_fraction']>0 for f in conditional))

    def test_all_reported_findings_explain_impact_and_next_step(self):
        r = assess(SITE, self.directory, self.grid)
        for f in r['findings']:
            for field in ('conflict_type', 'explanation', 'consequence', 'resolution', 'evidence'):
                self.assertTrue(f[field], (f['code'], field))


if __name__ == '__main__':
    unittest.main()
