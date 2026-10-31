#!/usr/bin/env python3
"""Mandatory, read-only preflight for standalone site cases (CLI exits 1 on failure)."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from Tools.namelist_parser import parse_namelist
from Tools.driving_data_conflicts import assess


class PreflightError(ValueError):
    pass


def module(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def source_text(path):
    # Model declarations/read calls contain no quoted exclamation marks.
    return '\n'.join(line.split('!')[0] for line in path.read_text().splitlines())


def namelist_contract(model):
    files = [model / 'drivers/ecosim/EcoSIMAPI.F90',
             model / 'f90src/Utils/ecosim_time_mod.F90',
             model / 'f90src/Utils/TestMod.F90']
    result = {}
    for path in files:
        text = re.sub(r'&\s*\n\s*&?', '', source_text(path))
        for match in re.finditer(r'namelist\s*/\s*(\w+)\s*/([^\n]+)', text, re.I):
            names = set(re.findall(r'\b[a-z]\w*\b', match[2].lower()))
            result.setdefault(match[1].lower(), set()).update(names)
    if not {'ecosim', 'ecosim_time', 'regression_test'} <= result.keys():
        raise ValueError('Cannot establish standalone model namelist contract')
    return result, files


def declaration_types(model, contract_files):
    """Read primitive types from the standalone driver's configuration modules."""
    files = contract_files + [model / name for name in (
        'f90src/Modelconfig/EcoSIMCtrlMod.F90', 'f90src/Modelconfig/EcoSIMConfig.F90',
        'f90src/IOutils/HistFileMod.F90', 'f90src/IOutils/ForcWriterMod.F90')]
    result = {}
    for file in files:
        text = re.sub(r'&\s*\n\s*&?', '', source_text(file))
        for match in re.finditer(r'(?im)^\s*(logical|integer|real|character)\b[^:\n]*::([^\n]+)', text):
            # Only commas outside array bounds, constructors and strings separate declarations.
            pieces, start, depth, quote = [], 0, 0, None
            tail = match[2]
            for i, char in enumerate(tail):
                if quote:
                    if char == quote: quote = None
                elif char in "'\"": quote = char
                elif char == '(': depth += 1
                elif char == ')': depth -= 1
                elif char == ',' and depth == 0:
                    pieces.append(tail[start:i]); start = i+1
            pieces.append(tail[start:])
            for piece in pieces:
                name = re.match(r'\s*(\w+)', piece)
                if name:
                    result.setdefault(name[1].lower(), set()).add(match[1].lower())
    return result


def required_reads(path, file_id, optional=(), procedure=None):
    text = source_text(path)
    if procedure:
        match = re.search(r'(?is)\bsubroutine\s+' + procedure + r'\s*\(.*?end\s*subroutine', text)
        if not match:
            raise ValueError(f'Cannot establish reader procedure {procedure}')
        text = match[0]
    names = set(re.findall(r"call\s+ncd_getvar\(\s*" + file_id + r"\s*,\s*['\"]([^'\"]+)['\"]", text, re.I))
    if not names:
        raise ValueError(f'Cannot establish input schema from {path}')
    return names - set(optional)


def checked_numbers(var, selection=None):
    data = var[:] if selection is None else var[selection]
    if np.any(np.ma.getmaskarray(data)) or not np.all(np.isfinite(np.ma.getdata(data))):
        raise ValueError(f'{var.name}: masked/fill or non-finite numeric value')
    return np.asarray(data)


def strings(var):
    var.set_auto_mask(False)
    return np.asarray(nc.chartostring(var[:])).astype(str)


def require(ds, names):
    missing = sorted(set(names) - ds.variables.keys())
    if missing:
        raise ValueError('Missing reader variables: ' + ', '.join(missing))


def years(ds, requested):
    require(ds, ['year'])
    values = checked_numbers(ds['year'])
    if values.ndim != 1 or not np.all(values == values.astype(int)) or len(set(values.tolist())) != values.size:
        raise ValueError('year must be a unique one-dimensional integer axis')
    values = values.astype(int).tolist()
    if values != sorted(values) or any(b != a + 1 for a, b in zip(values, values[1:])):
        raise ValueError('year axis must be contiguous and increasing for the model index lookup')
    missing = sorted(set(requested) - set(values))
    if missing:
        raise ValueError(f'Missing requested years: {missing}')
    return values


def bounds(ds, grid_bounds=None):
    require(ds, ['NH1', 'NV1', 'NH2', 'NV2'])
    arrays = [checked_numbers(ds[k]).reshape(-1) for k in ('NH1', 'NV1', 'NH2', 'NV2')]
    if len({a.size for a in arrays}) != 1:
        raise ValueError('Inconsistent topographic bounds dimensions')
    result = []
    for a, b, c, d in zip(*arrays):
        if not all(float(v).is_integer() for v in (a,b,c,d)) or min(a,b) < 1 or c < a or d < b:
            raise ValueError(f'Invalid one-based topographic bounds {(a,b,c,d)}')
        result.append(tuple(map(int, (a,b,c,d))))
    if grid_bounds is not None and result != grid_bounds:
        raise ValueError(f'Management topographic bounds {result} differ from grid {grid_bounds}')
    return result


def validate_events(obj):
    if isinstance(obj, list):
        for item in obj:
            validate_events(item)
    elif isinstance(obj, dict):
        for key, val in obj.items():
            if key in ('DDMMYYYY', 'DST', 'DEN') and val:
                datetime.strptime(str(val).zfill(8), '%d%m%Y')
            elif isinstance(val, (float, int)) and not np.isfinite(val):
                raise ValueError(f'Non-finite management value {key}')
            else:
                validate_events(val)


def validate(namelist, *, run_cwd=None, text=None, site_id=None, model_root=None):
    path = Path(namelist).resolve()
    cwd = Path(run_cwd).resolve() if run_cwd else path.parent
    model = Path(model_root).resolve() if model_root else ROOT.parent
    report = dict(schema_version=1, assessed=datetime.now(timezone.utc).isoformat(),
                  namelist=str(path), run_cwd=str(cwd), inputs={}, findings=[],
                  scope='Selected standalone inputs over the requested run; scientific provenance limitations remain warnings')

    def finding(severity, category, message):
        report['findings'].append(dict(severity=severity, category=category, message=str(message)))

    def check(category, action):
        try:
            return action()
        except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError, OverflowError) as exc:
            finding('error', category, exc)
            return None

    def identity(key, file):
        report['inputs'][key] = dict(path=str(file), sha256=sha256(file), size=file.stat().st_size)

    content = check('namelist', lambda: path.read_text() if text is None else text)
    if content is None:
        report['status'] = 'invalid'
        return report
    report['namelist_sha256'] = hashlib.sha256(content.encode()).hexdigest()
    groups = check('namelist', lambda: parse_namelist(content))
    if groups is None:
        report['status'] = 'invalid'
        return report
    eco, timer = groups.get('ecosim', {}), groups.get('ecosim_time', {})
    contract = check('namelist_contract', lambda: namelist_contract(model))
    if contract:
        known, sources = contract
        for source in sources:
            identity('contract:' + source.name, source)
        types = check('namelist_types', lambda: declaration_types(model, sources)) or {}
        for group, values in groups.items():
            if group not in known:
                finding('error', 'namelist', f'Unsupported standalone namelist group {group}')
            elif set(values) - known[group]:
                finding('error', 'namelist', f'Unknown /{group}/ keys: {sorted(set(values)-known[group])}')
            for key, value in values.items():
                array_keys = {'forc_periods', 'npxs', 'npys', 'hist_nhtfrq', 'hist_mfilt',
                              'hist_fincl1', 'hist_fincl2', 'hist_fincl3'}
                if isinstance(value, list) and key not in array_keys:
                    finding('error', 'namelist', f'{group}/{key}: array assignment to a scalar or unsupported array')
                kind = types.get(key, set())
                if len(kind) != 1:
                    finding('error', 'namelist', f'Cannot establish unambiguous primitive type for {group}/{key}')
                    continue
                kind = next(iter(kind))
                for element in value if isinstance(value, list) else [value]:
                    valid = (isinstance(element, bool) if kind == 'logical' else
                             isinstance(element, str) if kind == 'character' else
                             isinstance(element, int) and not isinstance(element, bool) if kind == 'integer' else
                             isinstance(element, (int, float)) and not isinstance(element, bool) and np.isfinite(element))
                    if not valid:
                        finding('error', 'namelist', f'{group}/{key} requires {kind} values')

    if not eco or not timer:
        finding('error', 'namelist', 'Both ecosim and ecosim_time groups are required')
    for key in ('plant_model', 'microbial_model', 'soichem_model', 'continue_run', 'soil_mgmt_use_model_year'):
        if key in eco and not isinstance(eco[key], bool):
            finding('error', 'namelist', f'{key} must be logical')
    for key in ('npxs', 'npys', 'ncyc_litr', 'ncyc_snow', 'hist_mfilt'):
        vals = eco.get(key, [])
        for value in vals if isinstance(vals, list) else [vals]:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                finding('error', 'namelist', f'{key} must contain positive integers')
    if timer.get('delta_time') != 3600:
        finding('error', 'namelist', 'Site run workflow requires explicit delta_time=3600 (hourly step accounting)')
    if not isinstance(eco.get('case_name'), str) or not eco.get('case_name', '').strip():
        finding('error', 'namelist', 'Explicit non-empty case_name is required')
    if not re.fullmatch(r'\d{14}', str(eco.get('start_date', ''))):
        finding('error', 'namelist', 'Explicit start_date must be YYYYMMDDHHMMSS')
    for key in ('hist_nhtfrq',):
        vals = eco.get(key, [])
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (vals if isinstance(vals, list) else [vals])):
            finding('error', 'namelist', f'{key} must contain integer frequencies')

    def array_capacities():
        hist = source_text(model / 'f90src/IOutils/HistFileMod.F90')
        def size(name):
            match = re.search(r'(?i)\b' + name + r'\s*=\s*(\d+)', hist)
            if not match:
                raise ValueError(f'Cannot establish history capacity {name}')
            return int(match[1])
        capacities = dict(forc_periods=15, npxs=5, npys=5, hist_nhtfrq=size('max_tapes'),
                          hist_mfilt=size('max_tapes'))
        capacities.update({f'hist_fincl{i}':size('max_flds') for i in (1,2,3)})
        for key, limit in capacities.items():
            value = eco.get(key, [])
            if isinstance(value, list) and len(value) > limit:
                raise ValueError(f'{key} has {len(value)} values, exceeding reader capacity {limit}')
    check('namelist_capacity', array_capacities)

    registry = set()
    for source in (model / 'f90src/IOutils/HistData').glob('HistRegister*.F90'):
        registry.update(re.findall(r"fname\s*=\s*['\"]([^'\"]+)['\"]", source.read_text(), re.I))
    for key in ('hist_fincl1', 'hist_fincl2', 'hist_fincl3'):
        values = eco.get(key, [])
        for value in values if isinstance(values, list) else [values]:
            if not isinstance(value, str) or value.split(':')[0] not in registry:
                finding('error', 'history', f'{key}: unregistered history field {value!r}')

    selected = {}
    inputs = {'grid_file_in', 'pft_file_in', 'pft_mgmt_in', 'soil_mgmt_in',
              'micpar_file_in', 'atm_ghg_in', 'clm_hour_file_in', 'clm_day_file_in', 'clm_factor_in', 'finidat'}
    required = {'grid_file_in', 'pft_file_in', 'pft_mgmt_in', 'atm_ghg_in'}
    for key in sorted(inputs):
        val = eco.get(key, '')
        # NO is recognized by these two optional readers only.
        if key in ('soil_mgmt_in', 'clm_factor_in') and isinstance(val, str) and val.strip() == 'NO':
            continue
        if not val:
            if key in required:
                finding('error', key, 'Required input path is absent')
            continue
        if not isinstance(val, str):
            finding('error', key, 'Input path must be a quoted scalar string')
            continue
        file = (cwd / val.rstrip()).resolve()
        if not file.is_file():
            finding('error', key, f'Input file not found: {file}')
            continue
        selected[key] = file
        check(key, lambda key=key, file=file: identity(key, file))
    if 'clm_factor_in' in selected:
        finding('error', 'clm_factor_in', 'Climate perturbation file validation is unsupported; add a reader-specific validator before running')
    if eco.get('restartfilefullpath'):
        finding('error', 'restart', 'Explicit restartFileFullPath validation is unsupported in this site workflow')
    if eco.get('fixclime') or eco.get('warming_exp'):
        finding('error', 'climate_mode', 'Fixed climate and warming experiments need a mode-specific preflight')
    if 'finidat' in selected or eco.get('continue_run'):
        finding('warning', 'restart', 'Restart clock is checked; restart state compatibility requires the restart-run workflow')
    climate = module('.agents/skills/ecosim-climate-readiness/scripts/check_climate_readiness.py', 'case_climate')
    # The climate validator accepts a path. A same-directory temporary file preserves relative paths
    # when callers validate a proposed namelist before mutating the production file.
    def climate_check():
        if text is None:
            return climate.validate(path, run_cwd=cwd)
        with tempfile.TemporaryDirectory(prefix='ecosim-preflight-') as temp:
            candidate = Path(temp) / path.name
            candidate.write_text(content)
            return climate.validate(candidate, run_cwd=cwd)
    weather = check('climate', climate_check)
    if weather:
        report['climate'] = weather
        for f in weather['findings']:
            finding('error' if f['code'] == 'duration_capped' else f['severity'], 'climate:' + f['code'], f['message'])
    if weather and weather.get('restart'):
        checkpoint = Path(weather['restart']['checkpoint'])
        def restart_check():
            identity('restart_checkpoint', checkpoint)
            with nc.Dataset(checkpoint) as ds:
                if not ds.variables:
                    raise ValueError('Restart checkpoint has no variables')
            if weather['restart'].get('pointer'):
                identity('restart_pointer', Path(weather['restart']['pointer']))
        check('restart_format', restart_check)
    spans = check('schedule', lambda: climate.schedule(groups, cwd, {'findings': []})) or []
    model_years = sorted({s['model_year'] for s in spans})
    forcing_years = sorted({s['forcing_year'] for s in spans})
    report['model_years'], report['forcing_years'] = model_years, forcing_years
    grid_bounds, pft_names, koppen = None, None, None

    def grid_check():
        nonlocal grid_bounds, koppen
        with nc.Dataset(selected['grid_file_in']) as ds:
            schema = required_reads(model / 'f90src/IOutils/readimod.F90', 'grid_nfid', ['fAeroScalar'])
            require(ds, schema)
            layers = {int(v) for v in re.findall(r'(?im)^\s*JZ\s*=\s*(\d+)', source_text(model / 'f90src/Mesh/GridMod.F90'))}
            if len(layers) != 1 or 'nlevs' not in ds.dimensions or len(ds.dimensions['nlevs']) != next(iter(layers)):
                raise ValueError(f'nlevs must match the standalone GridMod JZ setting {sorted(layers)}')
            grid_bounds = bounds(ds)
            koppen = checked_numbers(ds['IETYPG']).reshape(-1).astype(int)
            for name in schema:
                if ds[name].dtype.kind in 'iuf':
                    var = ds[name]
                    if 'nlevs' in var.dimensions:
                        if var.dimensions != ('ntopou', 'nlevs'):
                            raise ValueError(f'{name}: unsupported layer dimensions {var.dimensions}')
                        for it in range(len(grid_bounds)):
                            # NL2 is the derived bottom layer; its input padding is unused.
                            active = int(ds['NJ'][it] + ds['NL1'][it])
                            if not 1 <= active <= var.shape[1]:
                                raise ValueError('Active soil layers exceed grid capacity')
                            first = int(ds['NUI'][it]) - 1
                            if not 0 <= first < active:
                                raise ValueError('NUI is outside supplied soil layers')
                            checked_numbers(var, (it, slice(first, active)))
                    else:
                        checked_numbers(var)
        evidence = assess(site_id or path.stem, selected['grid_file_in'].parent, grid=selected['grid_file_in'])
        report['site_conflicts'] = evidence
        for name, digest in evidence.get('input_sha256', {}).items():
            report['inputs']['site_evidence:' + name] = dict(path=name, sha256=digest)
        for f in evidence['findings']:
            finding(f['severity'], 'site:' + f['code'], f['message'])
    if 'grid_file_in' in selected:
        check('grid', grid_check)
    if grid_bounds:
        cells = sum((c-a+1)*(d-b+1) for a,b,c,d in grid_bounds)
        for key in ('clm_hour_file_in', 'clm_day_file_in'):
            if key in selected:
                def check_grid_support():
                    with nc.Dataset(selected[key]) as ds:
                        if 'ngrid' not in ds.dimensions or len(ds.dimensions['ngrid']) != cells:
                            raise ValueError(f'{key}: climate ngrid does not match the grid cell count {cells}')
                check('spatial_support', check_grid_support)

    def pft_check():
        nonlocal pft_names
        with nc.Dataset(selected['pft_file_in']) as ds:
            schema = required_reads(model / 'f90src/IOutils/PlantInfoMod.F90', 'pft_nfid', ['IROOTJK', 'CANHT'], procedure='ReadPlantTraitTable')
            require(ds, schema)
            for key, dim in [('pfts', 'npfts'), ('pfts_long', 'npft'), ('pfts_short', 'npft'),
                             ('koppen_clim_no', 'nkopenclms'), ('koppen_clim_short', 'nkopenclms'),
                             ('koppen_clim_long', 'nkopenclms')]:
                if ds[key].dtype.kind != 'S' or ds[key].ndim != 2 or ds[key].dimensions[0] != dim:
                    raise ValueError(f'{key}: expected character table over {dim}')
            pft_names = set(strings(ds['pfts']).reshape(-1).tolist())
            pft_names = {v.strip().rstrip('\x00') for v in pft_names}
            if not pft_names or '' in pft_names:
                raise ValueError('PFT identifier table contains empty identifiers')
            for name in schema:
                if ds[name].dtype.kind in 'iuf':
                    checked_numbers(ds[name])
                    if ds[name].dimensions != (('npfts', 'JLI') if name == 'CLASS' else ('npfts',)):
                        raise ValueError(f'{name}: missing npfts dimension')
    if 'pft_file_in' in selected:
        check('pft_parameters', pft_check)

    def microbe_check():
        with nc.Dataset(selected['micpar_file_in']) as ds:
            require(ds, required_reads(model / 'f90src/Modelpars/NitroPars.F90', 'ncfid', ['SPOSC']))
            for var in ds.variables.values():
                if var.dtype.kind in 'iuf':
                    checked_numbers(var)
                    expected = ('nactbioms',) if var.name in ('SPORC', 'SPOMC') else ()
                    if var.dimensions != expected:
                        raise ValueError(f'{var.name}: expected dimensions {expected}')
    if 'micpar_file_in' in selected:
        check('microbial_parameters', microbe_check)
    else:
        finding('warning', 'microbial_parameters', 'No microbial parameter file selected; compiled defaults are used')

    def ghg_check():
        with nc.Dataset(selected['atm_ghg_in']) as ds:
            if all(eco.get('atm_' + gas + '_fix', -1) > 0 for gas in ('co2', 'ch4', 'n2o')):
                return  # The reader opens the file but does not read monthly records.
            require(ds, ['year'])
            stamps = checked_numbers(ds['year'])
            if stamps.ndim != 1 or not stamps.size:
                raise ValueError('GHG year must be a non-empty monthly axis')
            first = int(stamps[0])
            expected = first + np.arange(1, stamps.size+1)/12
            if stamps.size % 12 or not np.allclose(stamps, expected, atol=2e-4, rtol=0):
                raise ValueError('GHG stamps must be contiguous Jan-Dec monthly year+month/12 records')
            if model_years and (min(model_years) < first or max(model_years) >= first + stamps.size//12):
                raise ValueError(f'GHG coverage {first}-{first+stamps.size//12-1} misses model years {model_years[0]}-{model_years[-1]}')
            for name, unit in [('CO2', 'ppm'), ('CH4', 'ppb'), ('N2O', 'ppb')]:
                if eco.get('atm_' + name.lower() + '_fix', -1) > 0:
                    continue
                require(ds, [name])
                var = ds[name]
                if var.shape != stamps.shape or np.any(checked_numbers(var) <= 0):
                    raise ValueError(f'{name}: expected positive monthly concentrations')
                actual = str(getattr(var, 'units', '')).strip().lower()
                if actual and actual not in (unit, unit + 'v'):
                    raise ValueError(f'{name}: expected {unit}, found {actual}')
                if not actual:
                    finding('warning', 'ghg_units', f'{name} units absent; reader assumes {unit}')
    if 'atm_ghg_in' in selected:
        check('greenhouse_gases', ghg_check)

    def plant_check():
        bridge = module('.agents/skills/ecosim-natural-plant-mgmt/scripts/plant_mgmt_excel_bridge.py', 'case_plant')
        with nc.Dataset(selected['pft_mgmt_in']) as ds:
            require(ds, ['NZ', 'nmgnts', 'pft_type', 'pft_pltinfo', 'pft_mgmt', 'pft_dflag'])
            years(ds, forcing_years)
            bounds(ds, grid_bounds)
            for key, dims in {'NZ': ('ntopou',), 'nmgnts': ('year','ntopou','maxpfts'),
                              'pft_type': ('year','ntopou','maxpfts'),
                              'pft_pltinfo': ('year','ntopou','maxpfts'),
                              'pft_mgmt': ('year','ntopou','maxpfts','maxpmgt')}.items():
                expected = dims if key in ('NZ', 'nmgnts') else dims + (ds[key].dimensions[-1],)
                if ds[key].dimensions != expected:
                    raise ValueError(f'{key}: unexpected dimensions {ds[key].dimensions}')
            if ds['pft_dflag'].ndim:
                raise ValueError('pft_dflag must be scalar')
            jp_match = re.search(r'(?i)\bJP\s*=\s*(\d+)', source_text(model / 'f90src/Mesh/GridConsts.F90'))
            if not jp_match:
                raise ValueError('Cannot establish model PFT capacity JP')
            jp = int(jp_match[1])
            if len(ds.dimensions['maxpfts']) < jp or len(ds.dimensions['maxpmgt']) < 24:
                raise ValueError(f'Management storage must allow model reads of JP={jp} PFTs and 24 event slots')
            for name, width in [('pft_type',10), ('pft_pltinfo',128), ('pft_mgmt',128)]:
                if ds[name].dtype.kind != 'S' or ds[name].shape[-1] < width:
                    raise ValueError(f'{name}: model reads character strings of width {width}')
            flag = int(checked_numbers(ds['pft_dflag']))
            if flag != 1:
                raise ValueError(f'pft_dflag={flag}: only annual management schema is validated by this workflow')
            nz = checked_numbers(ds['NZ'])
            if np.any(nz < 0) or np.any(nz > jp) or np.any(nz != nz.astype(int)):
                raise ValueError('NZ exceeds PFT capacity or is not a nonnegative integer')
            for it, active in enumerate(nz):
                vals = checked_numbers(ds['nmgnts'], (slice(None), it, slice(0, int(active))))
                if np.any(vals < 0) or np.any(vals > 24) or np.any(vals != vals.astype(int)):
                    raise ValueError('nmgnts exceeds event capacity or is not a nonnegative integer')
        cfg = bridge.nc_to_config(selected['pft_mgmt_in'])
        validate_events(cfg)
        enum_text = source_text(model / 'f90src/Modelconfig/ElmIDMod.F90')
        def enum(prefix):
            return {int(v) for v in re.findall(r'(?im)integer,\s*parameter\s*::\s*' + prefix + r'\w*\s*=\s*(-?\d+)', enum_text)}
        for it, topo in enumerate(cfg['topo_units']):
            for year, entry in topo['years'].items():
                for pft in entry['pfts']:
                    code = pft['pft_type'].strip().rstrip('\x00')
                    if koppen is not None and koppen[it] > 0:
                        code = code[:4] + f'{koppen[it]:2d}'
                    if pft_names is None or code[:6] not in pft_names:
                        raise ValueError(f'PFT {code!r} absent from selected parameter table')
                    planting = pft['planting']
                    if planting and (planting['Planting_population'] < 0 or planting['Planting_depth'] < 0):
                        raise ValueError('Negative planting population or depth')
                    for event in pft['mgmt']:
                        if event['iHarvType'] not in enum('iharvtyp_') or event['jHarvType'] not in enum('jharvtyp_'):
                            raise ValueError('Harvest code is not defined in ElmIDMod.F90')
                        if datetime.strptime(event['DDMMYYYY'], '%d%m%Y').year != int(year):
                            raise ValueError('Plant management event year differs from its selected annual record')
                        for key, value in event.items():
                            if 'Fraction' in key and not 0 <= value <= 1:
                                raise ValueError(f'{key} outside [0,1]')

    if 'pft_mgmt_in' in selected:
        check('plant_management', plant_check)

    def soil_check():
        bridge = module('.agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py', 'case_soil')
        with nc.Dataset(selected['soil_mgmt_in']) as ds:
            years(ds, model_years if eco.get('soil_mgmt_use_model_year', False) else forcing_years)
            bounds(ds, grid_bounds)
            require(ds, ['fertf', 'tillf', 'irrigf'])
            for name in ('fertf', 'tillf', 'irrigf'):
                var = ds[name]
                var.set_auto_mask(False)
                if np.any(np.asarray(var[:]) == b''):
                    raise ValueError(f'{name}: selectors must use space padding, not NUL')
                if var.dimensions[:2] != ('year', 'ntopou') or var.ndim != 3:
                    raise ValueError(f'{name}: expected year/topographic-unit/character dimensions')
                for val in strings(var).reshape(-1):
                    key = val.rstrip()
                    if key != 'NO' and key not in ds.variables:
                        raise ValueError(f'{name}: selector {key!r} is neither NO nor a table variable')
        cfg = bridge.nc_to_config(selected['soil_mgmt_in'])
        bridge.validate_selector_maps(cfg)
        validate_events(cfg)
        for topo in cfg['topo_units']:
            for year, selectors in topo['years'].items():
                for name, category in [('fertf','fertilizer_files'), ('tillf','tillage_files'), ('irrigf','irrigation_files')]:
                    selector = selectors[name]
                    if selector == 'NO':
                        continue
                    events = cfg[category][selector]
                    limit = 367 if name == 'tillf' else 12 if name == 'fertf' else 24
                    if len(events) > limit:
                        raise ValueError(f'{selector}: more than {limit} reader event slots')
                    for event in events:
                        if event.get('DDMMYYYY') and datetime.strptime(event['DDMMYYYY'],'%d%m%Y').year != int(year):
                            raise ValueError(f'{selector}: event date differs from selected management year {year}')
                        if name == 'fertf' and any(event[k] < 0 for k in bridge.FERT_REAL_FIELDS):
                            raise ValueError(f'{selector}: negative fertilizer amount/depth/width')
                        if name == 'tillf' and event['DepzCorp'] < 0:
                            raise ValueError(f'{selector}: negative tillage depth')
                        if name == 'irrigf' and event['mode'] == 'scheduled':
                            if event['RR'] < 0 or not 1 <= event['JST'] <= event['JEN'] <= 24:
                                raise ValueError(f'{selector}: invalid irrigation amount or hourly window')
    if 'soil_mgmt_in' in selected:
        check('soil_management', soil_check)
    finding('warning', 'scientific_scope', 'Automated input checks do not establish field representativeness, management budgets, or literature agreement; retain the sourced site review and trait smoke test')
    report['status'] = 'invalid' if any(f['severity'] == 'error' for f in report['findings']) else 'ready_with_warnings' if report['findings'] else 'ready'
    return report


def enforce(namelist, *, output=None, **kwargs):
    report = validate(namelist, **kwargs)
    target = Path(output) if output else Path(namelist).with_suffix('.preflight.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    errors = [f for f in report['findings'] if f['severity'] == 'error']
    print(f"Case preflight: {report['status']} ({len(errors)} errors); {target}")
    if errors:
        raise PreflightError('Mandatory case preflight failed: ' + '; '.join(f"{f['category']}: {f['message']}" for f in errors))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('namelist', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        enforce(args.namelist, output=args.output)
    except PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
