#!/usr/bin/env python3
"""Read-only EcoSIM configuration evidence and Markdown report. Requires numpy/netCDF4."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import netCDF4 as nc
import numpy as np
import hydrology

SKILL = Path(__file__).resolve().parents[1]
AGENT = SKILL.parents[2]
BOUNDS = ('NH1', 'NH2', 'NV1', 'NV2')
EVENT_FIELDS = ('DDMMYYYY', 'ICUT', 'JCUT', 'HCUT', 'PCUT', 'ECUT11',
                'ECUT12', 'ECUT13', 'ECUT14', 'ECUT21', 'ECUT22', 'ECUT23', 'ECUT24')


# Keep this public name for existing summary callers and tests.
import sys
sys.path.insert(0, str(AGENT))
from Tools.namelist_parser import parse_namelist


def clean(value):
    if isinstance(value, (np.ndarray, np.ma.MaskedArray)):
        return clean(value.tolist())
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace').rstrip('\x00 ')
    if isinstance(value, str):
        return value.rstrip('\x00 ')
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)
    return value


def read_var(var):
    var.set_auto_chartostring(False)
    a = var[:]
    if a.dtype.kind == 'S' and a.dtype.itemsize == 1 and a.ndim:
        a = nc.chartostring(np.ma.filled(a, b'\x00'))
    return clean(a)


def inspect_nc(path, role):
    """Metadata plus bounded values; never load meteorological/history cubes."""
    with nc.Dataset(path) as ds:
        variables = {}
        for name, var in ds.variables.items():
            row = {'dimensions': list(var.dimensions), 'shape': list(var.shape),
                   'units': str(getattr(var, 'units', 'unspecified')),
                   'long_name': str(getattr(var, 'long_name', ''))}
            small = role in ('grid_file_in', 'pft_mgmt_in', 'soil_mgmt_in', 'clm_factor_in')
            coordinate = name.lower() in ('year', 'time', 'pfts', 'pfts_short', 'pfts_long')
            count = var.size // var.shape[-1] if var.dtype == np.dtype('S1') and var.ndim else var.size
            if (small or coordinate) and count <= 100000 and var.size <= 16000000:
                row['values'] = read_var(var)
            variables[name] = row
        return {'dimensions': {k: len(v) for k, v in ds.dimensions.items()},
                'attributes': {k: clean(ds.getncattr(k)) for k in ds.ncattrs()},
                'variables': variables}


def val(dataset, name, default=None):
    return (dataset or {}).get('variables', {}).get(name, {}).get('values', default)


def topo_rows(grid, first, warnings):
    if not grid:
        return []
    rows = []
    nt = grid['dimensions'].get('ntopou', 0)
    for t in range(min(nt, 1) if first else nt):
        row = {'unit': t + 1}
        for key in (*BOUNDS, 'topo_grid', 'ASPX', 'SL0', 'NUI', 'NJ', 'NL1', 'NL2'):
            v = val(grid, key)
            row[key] = v[t] if isinstance(v, list) else None
        if any(row[k] is None or row[k] <= 0 for k in BOUNDS):
            warnings.append(f'Grid unit {t+1}: nonpositive/missing bounds; later units may be inactive.')
            break
        if row['NH1'] > row['NH2'] or row['NV1'] > row['NV2']:
            raise ValueError(f'Invalid rectangle for grid unit {t+1}')
        g = row['topo_grid']
        zones = val(grid, 'IETYPG', [])
        row['Koppen'] = zones[g-1] if g and 0 < g <= len(zones) else None
        dh, dv = val(grid, 'DHI'), val(grid, 'DVI')
        if dh and dv and g:
            widths = dh[g-1][row['NH1']-1:row['NH2']]
            heights = dv[g-1][row['NV1']-1:row['NV2']]
            if len(widths) != row['NH2']-row['NH1']+1 or len(heights) != row['NV2']-row['NV1']+1:
                raise ValueError(f'Width arrays do not cover grid unit {t+1}')
            row['footprint_area_m2'] = sum(widths) * sum(heights)
        depths = val(grid, 'CDPTH', [])
        if depths and all(row[k] is not None for k in ('NUI', 'NJ', 'NL1', 'NL2')):
            supplied_end = row['NJ'] + row['NL1']
            end = supplied_end + row['NL2']
            row['supplied_layer_end'] = supplied_end
            row['configured_layer_end'] = end
            row['root_bottom_m'] = depths[t][row['NJ']-1]
            row['stored_bottom_at_configured_end_m'] = depths[t][end-1]
            active = depths[t][row['NUI']-1:supplied_end]
            if any(v is None or v <= 0 for v in active) or any(b <= a for a, b in zip(active, active[1:])):
                warnings.append(f'Grid unit {t+1}: supplied active depths are missing/nonpositive/nonmonotonic.')
            row['profiles'] = {k: v['values'][t][row['NUI']-1:supplied_end]
                               for k, v in grid['variables'].items()
                               if v['dimensions'] == ['ntopou', 'nlevs'] and 'values' in v}
        rows.append(row)
    return rows


def enums(source):
    path = source / 'f90src/Modelconfig/ElmIDMod.F90'
    if not path.is_file():
        return {}, {}
    matches = re.findall(r'(?im)^\s*integer\s*,\s*parameter\s*::\s*([ij]harvtyp_\w+)\s*=\s*(\d+)', path.read_text())
    return ({int(v): k for k, v in matches if k.startswith('iharv')},
            {int(v): k for k, v in matches if k.startswith('jharv')})


def numbers(text):
    return re.split(r'[\s,]+', text.strip())


def vegetation(mgmt, topos, parameters, first, source, warnings):
    if not mgmt:
        return []
    flag = val(mgmt, 'pft_dflag')
    if flag == -1:
        return []
    if flag is None:
        raise ValueError('Plant file is missing pft_dflag')
    years = val(mgmt, 'year')
    if flag != 0 and years is None:
        raise ValueError('Transient plant file is missing year variable')
    records = [0] if flag == 0 else range(len(years))
    types = val(mgmt, 'pft_type')
    plant = val(mgmt, 'pft_pltinfo')
    counts = val(mgmt, 'nmgnts')
    events = val(mgmt, 'pft_mgmt')
    if types is None or counts is None or plant is None:
        raise ValueError('Missing/oversized plant arrays; inspect file manually')
    catalog = val(parameters, 'pfts')
    names = dict(zip(val(parameters, 'pfts_short', []), val(parameters, 'pfts_long', [])))
    itypes, jtypes = enums(source)
    rows = []
    nt = mgmt['dimensions']['ntopou']
    for t in range(min(nt, 1) if first else nt):
        bounds = {k: val(mgmt, k)[t] for k in BOUNDS}
        if any(v is None or v <= 0 for v in bounds.values()):
            break
        nz = val(mgmt, 'NZ')[t]
        if not 0 <= nz <= mgmt['dimensions']['maxpfts']:
            raise ValueError(f'Plant unit {t+1}: invalid NZ={nz}')
        covered = [(x, y) for x in range(bounds['NH1'], bounds['NH2']+1)
                   for y in range(bounds['NV1'], bounds['NV2']+1)]
        zones = set()
        for x, y in covered:
            matches = [r for r in topos if r['NH1'] <= x <= r['NH2'] and r['NV1'] <= y <= r['NV2']]
            if len(matches) != 1 or matches[0]['Koppen'] is None:
                zones.add(None)
            else:
                zones.add(matches[0]['Koppen'])
        if None in zones:
            warnings.append(f'Plant unit {t+1}: grid/Koppen mapping incomplete or overlapping.')
        if any(z is not None and z < 0 for z in zones):
            warnings.append(f'Plant unit {t+1}: negative Koppen code requires reader-specific inspection.')
        for iy in records:
            for p in range(nz):
                raw = types[iy][t][p]
                effective = sorted({raw[:4] + f'{z:02d}' if z > 0 else raw for z in zones if z is not None and z >= 0})
                if catalog is not None:
                    for code in effective:
                        if code not in catalog:
                            warnings.append(f'Plant unit {t+1} slot {p+1}: effective PFT {code} absent from selected parameters.')
                fields = numbers(plant[iy][t][p])
                planting = {'raw': plant[iy][t][p]}
                if len(fields) in (3, 4):
                    planting.update(DDMMYYYY=fields[0], population_m2=float(fields[1]), depth_m=float(fields[2]))
                    if len(fields) == 4:
                        planting['maximum_population_m2'] = float(fields[3])
                else:
                    warnings.append(f'Plant unit {t+1} slot {p+1} record {iy+1}: blank/invalid planting string.')
                count = counts[iy][t][p]
                if count is None or not 0 <= count <= mgmt['dimensions'].get('maxpmgt', 0):
                    raise ValueError(f'Plant unit {t+1} slot {p+1}: invalid active nmgnts={count}')
                schedule = []
                for e in range(count):
                    raw_event = events[iy][t][p][e] if events is not None else ''
                    parts = numbers(raw_event)
                    if len(parts) != 13:
                        raise ValueError(f'Invalid active event: unit {t+1}, slot {p+1}, event {e+1}: {raw_event!r}')
                    event = dict(zip(EVENT_FIELDS, [parts[0], int(parts[1]), int(parts[2]), *map(float, parts[3:])]))
                    event['harvest_type'] = itypes.get(event['ICUT'], 'unverified enum')
                    event['termination_type'] = jtypes.get(event['JCUT'], 'unverified enum')
                    schedule.append(event)
                rows.append({'unit': t+1, 'bounds': bounds, 'record': iy+1,
                             'year': years[iy] if years else None, 'raw_code': raw,
                             'effective_codes': effective, 'parameter_catalog_checked': catalog is not None,
                             'name': names.get(raw[:4], 'unverified'), 'slot': p+1,
                             'planting': planting, 'nmgnts': count, 'events': schedule})
    return rows


def table(headers, rows):
    def cell(v):
        if v is None:
            return 'unverified'
        if isinstance(v, float):
            return f'{v:.7g}'
        return str(v).replace('|', '\\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
                       '| ' + ' | '.join('---' for _ in headers) + ' |',
                       *['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]])


def render(report):
    cfg = report['namelist'].get('ecosim', {})
    codes = sorted({r['raw_code'] for r in report['vegetation']})
    lines = [f"# EcoSIM run-folder summary: {cfg.get('case_name', 'unnamed')}", '',
             f"{len(report['topography'])} enabled topographic units; active vegetation codes: {', '.join(codes) or 'none decoded'}.", '',
             f"Namelist: `{report['namelist_path']}`. Launch directory: `{report['run_cwd']}`.", '',
             'Configuration inspection only. Runtime defaults, initialized extra layers, and successful simulation completion are not established.', '',
             '## Inputs', '', table(['Setting', 'Configured path', 'Status'],
             [(k, v['configured'], v['status']) for k, v in report['inputs'].items()]), '',
             '## Run controls', '']
    for group in ('ecosim', 'ecosim_time'):
        lines.extend([f'### {group}', '', table(['Setting', 'Explicit value'],
                     [(k, json.dumps(v)) for k, v in report['namelist'].get(group, {}).items()]), ''])
    lines.extend(['Forcing triplets are `(first year, last year, repeats)`. Zero-repeat periods are inactive; model calendar years differ from forcing years.', '',
                  '## Topography and soil', '',
                  table(['Unit', 'Columns', 'Rows', 'Aspect °', 'Slope °', 'Footprint m²', 'Koppen', 'NUI/NJ/NL1/NL2', 'Root bottom m'],
                        [(r['unit'], f"{r['NH1']}:{r['NH2']}", f"{r['NV1']}:{r['NV2']}", r['ASPX'], r['SL0'], r.get('footprint_area_m2'),
                          r['Koppen'], '/'.join(str(r[k]) for k in ('NUI','NJ','NL1','NL2')), r.get('root_bottom_m')) for r in report['topography']]), '',
                  'Footprints use DHI × DVI in meters per readimod.F90. Extra NL2 layers are initialized by the model; stored values there are not verified final layer depths. Full grid values, units, boundary conditions and litter pools are in evidence.json.', '',
                  'Soil tables show raw inputs. Negative FC/WP and organic N/P can be estimation sentinels. THW=1 selects field capacity, THW=0 selects wilting point, and THW>1 selects saturation; THI also uses special values and available pore space. Water-table rules can override these selections. Do not interpret THW=1 as 100% volumetric water or THI=0 as necessarily zero initialized ice. See the selected SoilHydroParaMod.F90.', ''])
    grid = report['inputs'].get('grid_file_in', {}).get('netcdf', {})
    site = [(k, v['values'], v['units']) for k, v in grid.get('variables', {}).items()
            if v['dimensions'] == ['ngrid'] and 'values' in v]
    lines.extend([table(['Site/boundary variable', 'Values', 'Declared units'], site), ''])
    for r in report['topography']:
        profiles = r.get('profiles', {})
        keys = [k for k in ('CDPTH','BKDSI','CSAND','CSILT','FC','WP','SCNV','PH','CORGC','CORGN','CORGP','THW','THI') if k in profiles]
        lines.extend([f"### Unit {r['unit']} supplied soil layers", '',
                      table(['Layer'] + [f"{k} ({grid['variables'][k]['units']})" for k in keys],
                            [[r['NUI']+i] + [profiles[k][i] for k in keys] for i in range(len(profiles.get('CDPTH', [])))]), ''])
    lines.extend(hydrology.render(report['hydrology'], table))
    lines.extend(['## Vegetation and plant management', '',
                  f"pft_dflag = {report.get('pft_dflag')}; only active NZ slots and selected records are decoded.", '',
                  'Dates below are raw DDMMYYYY strings. Planting population is per m²; depth is in m. Establishment information does not imply a mature initial stand.', ''])
    grouped = {}
    for row in report['vegetation']:
        key = json.dumps({k:v for k,v in row.items() if k not in ('unit','bounds')}, sort_keys=True)
        grouped.setdefault(key, []).append(row['unit'])
    for key, units in grouped.items():
        r = json.loads(key)
        lines.extend([f"### {r['raw_code']} — {r['name']}; units {', '.join(map(str,units))}; record {r['record']}", '',
                      f"Effective codes: {', '.join(r['effective_codes']) or 'unverified'}. Record year: {r['year'] if r['year'] is not None else 'not supplied (constant file)'}. Planting: `{r['planting']['raw']}`. Events: {r['nmgnts']}.", ''])
        if r['events']:
            lines.extend([table(EVENT_FIELDS, [[e[k] for k in EVENT_FIELDS] for e in r['events']]), '',
                          'Event types: ' + ', '.join(sorted({e['harvest_type'] + ' / ' + e['termination_type'] for e in r['events']})) + '.', ''])
    lines.extend(['PCUT is the thinning fraction. ECUT11–14 select PFT component removal; ECUT21–24 select conditional export from the ecosystem. Zero export fractions retain removed material in the ecosystem. ICUT=0 alone does not exclude thinning.', '',
                  '## Soil management', '', report['soil_management_note'], '',
                  '## Forcing coverage', '', table(['Input', 'Coordinate years', 'Notes'], report['coverage']), '',
                  'Coordinate/header checks only: individual hourly values, missing steps, chemistry values, and full CF compliance were not validated.', '',
                  '## Missing or unverified details', '',
                  *['- ' + w for w in report['warnings']], '',
                  'All input paths, variable metadata and bounded input arrays are retained in `evidence.json`. Large forcing arrays and model history files are not loaded.', ''])
    return '\n'.join(lines)


def summarize(namelist, run_cwd, source):
    groups = parse_namelist(namelist.read_text())
    cfg = groups.get('ecosim', {})
    warnings = ['Unspecified namelist values and runtime plant-selection defaults require the selected executable/API; they are not inferred.']
    inputs = {}
    for key, value in cfg.items():
        if not (key.endswith('_in') or key in ('microbe_file', 'microbe_file_in')):
            continue
        if not isinstance(value, str):
            warnings.append(f'{key}: non-string input setting; inspect manually.')
            continue
        entry = inputs[key] = {'configured': value}
        if not value.strip() or value.strip().upper() == 'NO':
            entry['status'] = 'disabled'
            continue
        path = Path(value.strip())
        path = (run_cwd / path).resolve() if not path.is_absolute() else path
        entry['resolved'] = str(path)
        entry['status'] = 'found' if path.is_file() else 'missing'
        if entry['status'] == 'missing':
            warnings.append(f'{key}: missing referenced file {path}')
            continue
        entry['size_bytes'] = path.stat().st_size
        entry['mtime_ns'] = path.stat().st_mtime_ns
        try:
            entry['netcdf'] = inspect_nc(path, key)
        except (OSError, ValueError) as exc:
            entry['status'] = 'unreadable as NetCDF'
            warnings.append(f'{key}: {exc}')
    def ds(key):
        return inputs.get(key, {}).get('netcdf')
    for key in ('grid_file_in', 'pft_mgmt_in', 'pft_file_in'):
        if key not in inputs:
            warnings.append(f'{key}: omitted; effective input/default unverified.')
    topos = topo_rows(ds('grid_file_in'), cfg.get('first_topou', False), warnings)
    veg = vegetation(ds('pft_mgmt_in'), topos, ds('pft_file_in'), cfg.get('first_topou', False), source, warnings)
    periods = cfg.get('forc_periods', [])
    if not isinstance(periods, list) or len(periods) % 3:
        warnings.append('forc_periods missing or not complete triplets; forcing-year coverage unverified.')
        periods = []
    required_years = set()
    scheduled_years = 0
    for a, b, n in zip(periods[::3], periods[1::3], periods[2::3]):
        if n > 0:
            required_years.update(range(min(a,b), max(a,b)+1))
            scheduled_years += (abs(b-a)+1)*n
    timer = groups.get('ecosim_time', {})
    if timer.get('stop_option') == 'nyears' and timer.get('stop_n') != scheduled_years and periods:
        warnings.append(f"Forcing schedule totals {scheduled_years} years but stop_n={timer.get('stop_n')}. The current standalone driver calls update_sim_len, which caps a nyears stop at the scheduled length; verify the executable's source version before assigning an end date.")
    coverage = []
    for key in ('clm_hour_file_in','clm_day_file_in','atm_ghg_in','clm_factor_in','soil_mgmt_in'):
        if key not in inputs:
            continue
        years = val(ds(key), 'year')
        note = inputs[key]['status']
        label = 'unverified'
        if years:
            label = f'{min(years)}–{max(years)} ({len(years)} records)'
            if key.startswith('clm_') and key != 'clm_factor_in':
                missing = sorted(required_years - set(years))
                note = 'missing forcing years: ' + str(missing) if missing else 'all explicitly scheduled forcing years present'
                if missing:
                    warnings.append(f'{key}: {note}')
        coverage.append([key, label, note])
    soil = inputs.get('soil_mgmt_in', {})
    soil_note = ('Disabled explicitly by soil_mgmt_in=NO; this does not disable plant-management disturbances.'
                 if soil.get('status') == 'disabled' else
                 'See selector arrays and event-table strings in evidence.json; decode with ecosim-soil-mgmt. Omitted or missing files are unverified, not unmanaged.')
    if ds('soil_mgmt_in'):
        soil_ds = ds('soil_mgmt_in')
        for key in ('fertf', 'tillf', 'irrigf'):
            values = val(soil_ds, key)
            if values is None:
                warnings.append(f'Soil management: {key} missing or not decoded.')
                continue
            for selector in np.asarray(values).flat:
                if selector and selector.upper() != 'NO' and not selector.lower().startswith('auto') and selector not in soil_ds['variables']:
                    warnings.append(f'Soil management: missing selector target {selector}.')
        warnings.append('Soil event fields/units and schedule-year coverage require ecosim-soil-mgmt inspection; helper preserves raw tables only.')
    return {'schema_version': 2, 'generated_at': datetime.now().astimezone().isoformat(),
            'namelist_path': str(namelist), 'namelist_sha256': hashlib.sha256(namelist.read_bytes()).hexdigest(),
            'run_cwd': str(run_cwd), 'source_root': str(source), 'namelist': groups, 'inputs': inputs,
            'topography': topos, 'hydrology': hydrology.describe(ds('grid_file_in'), topos, cfg, source),
            'pft_dflag': val(ds('pft_mgmt_in'),'pft_dflag'),
            'vegetation': veg, 'coverage': coverage, 'soil_management_note': soil_note,
            'warnings': list(dict.fromkeys(warnings))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runfolder', type=Path)
    parser.add_argument('--namelist', type=Path)
    parser.add_argument('--run-cwd', type=Path)
    parser.add_argument('--source-root', type=Path, default=AGENT.parent)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    candidates = [args.namelist.resolve()] if args.namelist else sorted(args.runfolder.resolve().rglob('*.namelist'))
    if len(candidates) != 1:
        parser.error('Select exactly one namelist with --namelist; candidates: ' + ', '.join(map(str,candidates)))
    nml = candidates[0]
    try:
        report = summarize(nml, (args.run_cwd or nml.parent).resolve(), args.source_root.resolve())
        markdown = render(report)
    except (ValueError, IndexError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'Inspection incomplete: {exc}\n')
    out = args.output_dir or AGENT / 'result/runfolder_summary' / (args.runfolder.name + '_' + datetime.now().strftime('%Y%m%dT%H%M%S%f'))
    # Refuse existing destinations and any output inside the source run folder.
    out = out.resolve()
    if out == args.runfolder.resolve() or args.runfolder.resolve() in out.parents:
        parser.error('Output must be outside the inspected run folder')
    out.mkdir(parents=True, exist_ok=False)
    (out / 'evidence.json').write_text(json.dumps(clean(report), indent=2, allow_nan=False) + '\n')
    (out / 'summary.md').write_text(markdown)
    print(out / 'summary.md')
    print(out / 'evidence.json')
    print(f"{len(report['topography'])} topographic units; {len(report['vegetation'])} active PFT records; {len(report['warnings'])} notices")


if __name__ == '__main__':
    main()
