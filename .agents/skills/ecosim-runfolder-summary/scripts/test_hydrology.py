"""Hydrology interpretation checks independent of local SOAP/ERT availability."""
from pathlib import Path
import unittest

import hydrology


def fixture(aspect=90, relative_slope=0):
    data = {'NHW':1,'NHE':2,'NVN':1,'NVS':1,'DHI':[[40,40]],'DVI':[[40]],
            'IDTBLG':[1],'DTBLIG':[0.25],'DTBLDIG':[100],'DTBLGG':[relative_slope],
            'RCHQNG':[0],'RCHQEG':[0.1],'RCHQSG':[0.1],'RCHQWG':[0],
            'RCHGNUG':[0],'RCHGEUG':[0.1],'RCHGSUG':[0.1],'RCHGWUG':[0],
            'RCHGNTG':[0],'RCHGETG':[20],'RCHGSTG':[20],'RCHGWTG':[0],'RCHGDG':[0]}
    grid = {'dimensions':{'ngrid':1}, 'variables':{k:{'values':v} for k,v in data.items()}}
    topo = [{'unit':i,'topo_grid':1,'NH1':i,'NH2':i,'NV1':1,'NV2':1,'ASPX':aspect,'SL0':45,
             'profiles':{'BKDSI':[1],'SCNH':[2],'SCNV':[2],'FHOL':[0],'THW':[1],'THI':[0]}} for i in (1,2)]
    return grid,topo


class HydrologyChecks(unittest.TestCase):
    def describe(self,grid,topo,mode=1):
        return hydrology.describe(grid,topo,{'grid_mode':mode},Path('/absent'))

    def test_east_slope_has_downhill_neighbors_and_variable_water_table_depth(self):
        grid,topo = fixture()
        h = self.describe(grid,topo)
        self.assertEqual(h['internal_soil_neighbor_faces'],{'west_east':1,'north_south':0})
        a,b = h['initial_cells']
        self.assertAlmostEqual(a['relative_center_elevation_m'],40)
        self.assertAlmostEqual(a['initial_natural_water_table_depth_m'],40.25)
        self.assertAlmostEqual(b['initial_natural_water_table_depth_m'],0.25)
        self.assertEqual(a['surface_partition_ew'],1)

    def test_south_facing_strip_has_no_surface_partition_toward_neighbors(self):
        grid,topo = fixture(aspect=180)
        h = self.describe(grid,topo)
        for c in h['initial_cells']:
            self.assertEqual(c['surface_partition_ew'],0)
            self.assertEqual(c['surface_partition_ns'],1)
            self.assertAlmostEqual(c['initial_natural_water_table_depth_m'],0.25)
        self.assertEqual(h['internal_soil_neighbor_faces']['west_east'],1)

    def test_terrain_following_water_table_has_constant_local_depth(self):
        grid,topo = fixture(relative_slope=1)
        h = self.describe(grid,topo)
        self.assertTrue(all(c['initial_natural_water_table_depth_m']==0.25 for c in h['initial_cells']))

    def test_no_external_table_does_not_invent_local_depths(self):
        grid,topo = fixture()
        grid['variables']['IDTBLG']['values'] = [0]
        h = self.describe(grid,topo)
        self.assertTrue(all('initial_natural_water_table_depth_m' not in c for c in h['initial_cells']))

    def test_missing_coefficient_is_not_zero_and_bottom_closure_is_explicit(self):
        grid,topo = fixture()
        del grid['variables']['RCHGEUG']
        h = self.describe(grid,topo)
        b = {r['direction']:r for r in h['boundaries']}
        self.assertEqual(b['East']['subsurface_status'],'unverified')
        self.assertEqual(b['Bottom']['subsurface_status'],'disabled (zero)')
        self.assertIsNone(b['East']['subsurface_multiplier'])
        self.assertEqual(b['South']['external_distance_m'],20)

    def test_soil_modes_gate_faces_without_discarding_configured_coefficients(self):
        grid,topo = fixture()
        for mode in (2,3):
            h = self.describe(grid,topo,mode)
            self.assertEqual(h['internal_soil_neighbor_faces']['west_east'],0)
            east = next(b for b in h['boundaries'] if b['direction']=='East')
            self.assertEqual(east['subsurface_multiplier'],0.1)
            self.assertEqual(east['subsurface_status'],'inactive in explicit grid_mode')

    def test_incomplete_or_overlapping_coverage_skips_geometry(self):
        grid,topo = fixture()
        for selected in (topo[:1],topo+topo):
            cells,issue = hydrology.initial_geometry(grid,selected)
            self.assertEqual(cells,[])
            self.assertIsNotNone(issue)

    def test_omitted_mode_is_not_silently_vertical(self):
        grid,topo = fixture()
        h = hydrology.describe(grid,topo,{},Path('/absent'))
        self.assertIsNone(h['grid_mode'])
        self.assertIsNone(h['internal_soil_neighbor_faces']['west_east'])
        self.assertTrue(all(b['subsurface_status']=='grid_mode unverified' for b in h['boundaries']))

    def test_rectangular_two_dimensional_terrain_integration(self):
        grid,topo = fixture()
        grid['variables']['NVS']['values']=2
        grid['variables']['DVI']['values']=[[40,40]]
        for t in topo:t['NV2']=2
        h = self.describe(grid,topo)
        self.assertEqual(h['internal_soil_neighbor_faces'],{'west_east':2,'north_south':2})
        heights = {(c['column'],c['row']):c['relative_center_elevation_m'] for c in h['initial_cells']}
        self.assertAlmostEqual(heights[1,1],heights[1,2])
        self.assertAlmostEqual(heights[2,1],heights[2,2])


if __name__ == '__main__':
    unittest.main()
