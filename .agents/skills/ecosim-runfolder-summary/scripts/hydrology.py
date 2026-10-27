"""Configuration interpretations of readimod/StartsMod/Watsub; not a flow solver."""
import hashlib
import math


MODES = {1: '3D: west–east, north–south, and vertical',
         2: '2D: north–south and vertical; west–east disabled',
         3: '1D: vertical only; lateral soil/boundary flow disabled'}
WATER_TABLES = {0: 'No external water table', 1: 'Natural stationary external water table',
                2: 'Natural mobile external water table', 3: 'Artificial stationary external water table',
                4: 'Artificial mobile external water table'}
SOURCES = ('f90src/IOutils/readimod.F90', 'f90src/Ecosim_mods/StartsMod.F90',
           'f90src/HydroTherm/SoilPhys/WatsubMod.F90',
           'f90src/HydroTherm/SurfPhys/SurfPhysMod.F90',
           'f90src/HydroTherm/SurfPhys/SurfLitterPhysMod.F90',
           'f90src/HydroTherm/SoilPhys/SoilHydroParaMod.F90')


def value(grid, key, default=None):
    return (grid or {}).get('variables', {}).get(key, {}).get('values', default)


def finite(x):
    return isinstance(x, (int, float)) and math.isfinite(x)


def initial_geometry(grid, topos):
    """Reproduce initial terrain/reference-depth equations only for complete single grids."""
    if not grid or grid['dimensions'].get('ngrid') != 1:
        return [], 'Initial terrain derivation requires one grid with complete rectangle coverage.'
    limits = [value(grid, k) for k in ('NHW','NHE','NVN','NVS')]
    if not all(isinstance(x, int) for x in limits):
        return [], 'Initial terrain derivation lacks explicit landscape bounds.'
    west, east, north, south = limits
    if west < 1 or north < 1 or east < west or south < north or (east-west+1)*(south-north+1) > 10000:
        return [], 'Initial terrain derivation has unsupported bounds or more than 10000 cells.'
    dh, dv = value(grid, 'DHI'), value(grid, 'DVI')
    if not dh or not dv or len(dh[0]) < east or len(dv[0]) < south:
        return [], 'Initial terrain derivation lacks cell widths.'
    cells = {}
    for x in range(west, east+1):
        for y in range(north, south+1):
            matches = [t for t in topos if t['NH1'] <= x <= t['NH2'] and t['NV1'] <= y <= t['NV2']]
            if len(matches) != 1 or matches[0].get('topo_grid') != 1:
                return [], 'Initial terrain derivation skipped: incomplete/overlapping selected grid units.'
            t = matches[0]
            if not all(finite(v) for v in (t.get('ASPX'), t.get('SL0'), dh[0][x-1], dv[0][y-1])):
                return [], 'Initial terrain derivation has missing/nonfinite aspect, slope, or width.'
            if not 0 <= t['ASPX'] <= 360 or not 0 <= t['SL0'] < 90 or min(dh[0][x-1], dv[0][y-1]) <= 0:
                return [], 'Initial terrain derivation has unsupported aspect/slope/width values.'
            phi = math.radians((450-t['ASPX']) % 360)
            slope = max(1.745e-4, math.tan(math.radians(t['SL0'])))
            sx, sy = -slope*math.cos(phi), slope*math.sin(phi)
            sx = 0.0 if abs(sx) < 1e-12 else sx
            sy = 0.0 if abs(sy) < 1e-12 else sy
            fraction_ew = abs(sx)/(abs(sx)+abs(sy)) if sx or sy else 0.5
            wx, wy = dh[0][x-1], dv[0][y-1]
            if x == west and y == north:
                z = 0.5*(wx*sx+wy*sy)
            elif x == west:
                b = cells[x,y-1]
                z = b['z']+0.5*(dv[0][y-2]*b['slope_ns']+wy*sy)
            elif y == north:
                a = cells[x-1,y]
                z = a['z']+0.5*(dh[0][x-2]*a['slope_ew']+wx*sx)
            else:
                a, b = cells[x-1,y], cells[x,y-1]
                z = 0.5*(a['z']+b['z']+0.5*(dh[0][x-2]*a['slope_ew']+wx*sx+dv[0][y-2]*b['slope_ns']+wy*sy))
            cells[x,y] = {'column':x,'row':y,'unit':t['unit'],'z':z,
                          'slope_ew':sx,'slope_ns':sy,'surface_partition_ew':fraction_ew,
                          'surface_partition_ns':1-fraction_ew}
    zmin = min(c['z'] for c in cells.values())
    depth, relative_slope, mode = (value(grid,k,[None])[0] for k in ('DTBLIG','DTBLGG','IDTBLG'))
    for c in cells.values():
        c['relative_center_elevation_m'] = c.pop('z')-zmin
        # Formula applies to soil cells. Avoid extrapolating to pond units.
        t = next(t for t in topos if t['unit'] == c['unit'])
        bd = t.get('profiles', {}).get('BKDSI', [])
        if mode in (1,2) and finite(depth) and finite(relative_slope) and bd and finite(bd[0]) and bd[0] > 0:
            c['initial_natural_water_table_depth_m'] = depth+c['relative_center_elevation_m']*(1-relative_slope)
    return list(cells.values()), None


def describe(grid, topos, cfg, source):
    mode = cfg.get('grid_mode')
    result = {'grid_mode': mode, 'flow_directions': MODES.get(mode, 'unverified: absent/unknown grid_mode'),
              'explicit_controls': {k:cfg[k] for k in ('grid_mode','fixwaterlevel','lmacporeoff',
                  'use_ecosys_drainage','lsoilcompaction','snowredist_model','npxs','ncyc_snow') if k in cfg},
              'boundaries': [], 'water_tables': [], 'soil_hydrology': [], 'notes': [], 'source_sha256': {}}
    for name in SOURCES:
        path = source/name
        if path.is_file():
            result['source_sha256'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if len(result['source_sha256']) != len(SOURCES):
        result['notes'].append('Some reader/process source files are unavailable; verify mappings against the selected model version.')
    result['notes'].append('Interpretations follow the documented reader equations; source hashes identify inspected files, not the executable that produced existing outputs.')
    omitted = [k for k in ('fixwaterlevel','lmacporeoff','use_ecosys_drainage') if k not in cfg]
    if omitted:
        result['notes'].append('Runtime defaults not resolved for omitted controls: '+', '.join(omitted)+'.')
    if not grid:
        result['notes'].append('Grid unavailable: hydrology settings are unverified.')
        return result
    for g in range(grid['dimensions'].get('ngrid',0)):
        def at(key):
            data = value(grid,key,[])
            return data[g] if isinstance(data,list) and g < len(data) else None
        wt = {k:at(k) for k in ('IDTBLG','DTBLIG','DTBLDIG','DTBLGG')}
        wt.update(grid=g+1, mode=WATER_TABLES.get(wt['IDTBLG'],'unverified mode'))
        result['water_tables'].append(wt)
        for direction, letter in (('North','N'),('East','E'),('South','S'),('West','W'),('Bottom','D')):
            vertical = direction == 'Bottom'
            surface = None if vertical else at('RCHQ'+letter+'G')
            sub = at('RCHGDG' if vertical else 'RCHG'+letter+'UG')
            distance = None if vertical else at('RCHG'+letter+'TG')
            enabled = mode in (1,2,3) if vertical else mode == 1 or (mode == 2 and letter in ('N','S'))
            def status(v):
                if not finite(v):return 'unverified'
                if not enabled:return 'inactive in explicit grid_mode' if mode in MODES else 'grid_mode unverified'
                return 'disabled (zero)' if v == 0 else 'nonzero multiplier; flux conditional'
            result['boundaries'].append({'grid':g+1,'direction':direction,
                'surface_multiplier':surface,'subsurface_multiplier':sub,'external_distance_m':distance,
                'subsurface_status':status(sub), 'surface_setting': 'not applicable' if vertical else
                    ('unverified' if not finite(surface) else 'disabled (zero)' if surface == 0 else 'nonzero; check slope partition and runoff flags')})
    for t in topos:
        row = {'unit':t['unit']}
        for key in ('SCNH','SCNV','FHOL','ROCK','FC','WP','THW','THI'):
            row[key] = {'values':t.get('profiles',{}).get(key),
                        'declared_units':grid['variables'].get(key,{}).get('units','unspecified')}
        result['soil_hydrology'].append(row)
    result['initial_cells'], issue = initial_geometry(grid,topos)
    if issue:result['notes'].append(issue)
    if result['initial_cells']:
        coords = {(c['column'],c['row']) for c in result['initial_cells']}
        result['internal_soil_neighbor_faces'] = {
            'west_east': sum((x+1,y) in coords for x,y in coords) if mode == 1 else 0 if mode in MODES else None,
            'north_south': sum((x,y+1) in coords for x,y in coords) if mode in (1,2) else 0 if mode == 3 else None}
    result['notes'].extend([
        'External multipliers scale computed fluxes; 0.1 is neither a fixed flow rate nor removal of 10% of stored water. They do not scale internal cell-to-cell exchange.',
        'Internal soil flow uses matric + gravitational + osmotic potential differences and conductivity, with donor-water/receiver-pore-space limits; direction is not prescribed by slope alone.',
        'Surface runoff uses aspect-dependent directional flags and slope partitions. A nonzero external coefficient alone does not guarantee runoff. grid_mode gates soil/external loops; verify surface routing separately in the selected version.',
        'Initial terrain and natural-water-table depths are derived for complete single-grid soil landscapes only; they exclude restarts, erosion, and later mobile-water-table evolution. DTBLIG is a reference depth, not necessarily the depth beneath every cell.',
        'THW>1 requests saturation, THW=1 field capacity, THW=0 wilting point, THW<0 dry; interior values are volumetric water. THI has analogous selectors limited by remaining pore space. Water-table conditions can override initialization; THI=0 need not initialize zero ice.',
        'Negative FC/WP/conductivity can request estimation. SCNH/SCNV are input saturated conductivities, not realized fluxes; zero FHOL supplies no macropore fraction. A true LMacPoreOff also suppresses macropore flow.',
    ])
    return result


def render(hydro, table):
    lines = ['## Hydrology configuration', '', 'Flow directions: '+hydro['flow_directions']+'.', '',
             table(['Explicit control','Value'], hydro['explicit_controls'].items()), '',
             table(['Grid','Boundary','Surface multiplier','Surface interpretation','Subsurface multiplier','External distance m','Subsurface interpretation'],
                   [(b['grid'],b['direction'],b['surface_multiplier'],b['surface_setting'],b['subsurface_multiplier'],b['external_distance_m'],b['subsurface_status']) for b in hydro['boundaries']]), '',
             table(['Grid','Water-table mode (IDTBLG)','Natural reference depth m','Artificial reference depth m','Relative water-table slope'],
                   [(w['grid'],f"{w['IDTBLG']}: {w['mode']}",w['DTBLIG'],w['DTBLDIG'],w['DTBLGG']) for w in hydro['water_tables']]), '']
    cells = hydro.get('initial_cells',[])
    if cells:
        faces = hydro['internal_soil_neighbor_faces']
        lines.extend([f"Enabled internal soil neighbor pairs: west–east {faces['west_east']}; north–south {faces['north_south']}. Actual layer faces depend on active soil layers.", ''])
        lines.extend(['### Derived initial geometry', '',
            table(['Column','Row','Unit','Relative center elevation m','Downhill direction','Surface partition EW','Surface partition NS','Natural water-table depth m'],
                  [(c['column'],c['row'],c['unit'],c['relative_center_elevation_m'],
                    '/'.join(([('east' if c['slope_ew'] < 0 else 'west')] if c['slope_ew'] else [])+
                             ([('south' if c['slope_ns'] < 0 else 'north')] if c['slope_ns'] else [])) or 'flat',
                    c['surface_partition_ew'],c['surface_partition_ns'],c.get('initial_natural_water_table_depth_m')) for c in cells]), ''])
    lines.extend(['### Supplied soil hydraulic inputs', '',
        table(['Unit','SCNH values','SCNV values','FHOL values','THW values','THI values'],
              [(r['unit'],*[str(r[k]['values'])+' ('+r[k]['declared_units']+')' for k in ('SCNH','SCNV','FHOL','THW','THI')]) for r in hydro['soil_hydrology']]), '',
        'SCNH/SCNV units come from each input variable (see JSON); only supplied active layers are shown. Full FC/WP/ROCK values and units are in the hydrology evidence.', '',
        *['- '+note for note in hydro['notes']], ''])
    return lines
