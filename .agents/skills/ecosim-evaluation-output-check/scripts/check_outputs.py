#!/usr/bin/env python3
"""Prepare, execute and verify an isolated one-year EcoSIM history check."""
from __future__ import annotations
import argparse
import calendar
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

import netCDF4 as nc
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from Tools.namelist_parser import parse_namelist
from Tools.case_preflight import enforce as enforce_preflight


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    year = data.get('year')
    if type(year) is not int or not 1 <= year <= 9998:
        raise ValueError('Manifest year must be a Gregorian calendar year')
    if not data.get('site_id') or not data.get('year_reason') or not data.get('targets'):
        raise ValueError('Manifest needs site_id, year_reason and nonempty targets')
    ids, definitions = set(), {}
    for target in data['targets']:
        ident = target.get('id')
        if not ident or ident in ids:
            raise ValueError('Target IDs must be nonempty and unique')
        ids.add(ident)
        if target.get('status') not in ('mapped', 'blocked'):
            raise ValueError(f'{ident}: status must be mapped or blocked')
        if target['status'] == 'blocked' and not target.get('reason'):
            raise ValueError(f'{ident}: blocked target needs a reason')
        if target['status'] == 'mapped' and (not target.get('comparison') or not target.get('variables')):
            raise ValueError(f'{ident}: mapped target needs comparison and variables')
        if not target.get('observations'):
            raise ValueError(f'{ident}: observation inventory evidence is required')
        for obs in target['observations']:
            source = Path(obs['path'])
            source = source if source.is_absolute() else path.parent / source
            if not source.is_file() or not obs.get('variable') or type(obs.get('valid_count')) is not int or obs['valid_count'] <= 0:
                raise ValueError(f'{ident}: existing observation file, variable and positive valid_count required')
            obs['path'] = str(source.resolve())
        for var in target.get('variables', []):
            name = var.get('name', '')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
                raise ValueError(f'{ident}: invalid history name {name!r}')
            if not var.get('units') or not isinstance(var['units'], list) or not var.get('dimensions'):
                raise ValueError(f'{name}: explicit source units and ordered dimensions required')
            if var.get('temporal') not in ('mean', 'point', 'sum', 'static'):
                raise ValueError(f'{name}: temporal metadata expectation required')
            if var['temporal'] != 'static' and var['dimensions'][0] != 'time':
                raise ValueError(f'{name}: dynamic output must have leading time dimension')
            if name in definitions and definitions[name] != var:
                raise ValueError(f'Conflicting requirements for shared field {name}')
            definitions[name] = var
    if not definitions:
        raise ValueError('No model variables to check; resolve at least one target mapping first')
    return data, definitions


def fortran(value):
    if isinstance(value, list):
        return ', '.join(fortran(x) for x in value)
    if isinstance(value, bool):
        return '.true.' if value else '.false.'
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (int, float)) and math.isfinite(value):
        return repr(value)
    raise ValueError(f'Cannot serialize namelist value {value!r}')


def render(groups):
    return '\n\n'.join('&' + group + '\n' + '\n'.join(
        f'  {key}={fortran(value)}' for key, value in values.items()) + '\n/'
        for group, values in groups.items()) + '\n'


def include_required_history(eco, required):
    """Explicitly enable required fields, including registry-inactive fields."""
    base = lambda item: str(item).split(':', 1)[0].strip()
    previous = eco.get('hist_fincl1', [])
    previous = previous if isinstance(previous, list) else [previous]
    selected, seen = [], set()
    for item in previous:
        if base(item) and base(item) not in seen:
            selected.append(item)
            seen.add(base(item))
    added = [name for name in required if name not in seen]
    selected = selected + added
    eco['hist_fincl1'] = selected if len(selected) > 1 else selected[0]
    # Inclusion wins in HistFileMod, but remove contradictory required exclusions
    # so the namelist expresses its intent without changing unrelated exclusions.
    excluded = eco.get('hist_fexcl1', [])
    excluded = excluded if isinstance(excluded, list) else [excluded]
    removed = [item for item in excluded if base(item) in required]
    retained = [item for item in excluded if base(item) and base(item) not in required]
    if retained:
        eco['hist_fexcl1'] = retained if len(retained) > 1 else retained[0]
    else:
        eco.pop('hist_fexcl1', None)
    return dict(added_to_hist_fincl1=added, removed_from_hist_fexcl1=removed,
                selected_hist_fincl1=selected)


def prepare(namelist_path, manifest_path, scratch, ecosim_root):
    source = Path(namelist_path).resolve()
    scratch, ecosim_root = Path(scratch).resolve(), Path(ecosim_root).resolve()
    if scratch.exists():
        raise ValueError('Scratch path already exists; use a new directory')
    data, definitions = manifest(manifest_path)
    groups = parse_namelist(source.read_text())
    eco, timing = groups['ecosim'], groups['ecosim_time']
    if timing.get('delta_time') != 3600 or not eco.get('clm_hour_file_in'):
        raise ValueError('Only standalone hourly-forcing cases with delta_time=3600 are supported')
    if any(str(v).upper() not in ('NO', '') for k, v in eco.items() if 'finidat' in k):
        raise ValueError('Explicit restart initialization is unsupported; prepare a reviewed cold-start case')
    registry = set()
    for path in (ecosim_root / 'f90src/IOutils/HistData').glob('HistRegister*.F90'):
        registry.update(re.findall(r"fname\s*=\s*['\"]([^'\"]+)['\"]", path.read_text(), re.I))
    missing = sorted(set(definitions) - registry)
    if missing:
        raise ValueError('Required names absent from history registry: ' + ', '.join(missing))
    copies = []
    for key, value in list(eco.items()):
        if not key.endswith('_in') or not isinstance(value, str) or value.upper() in ('NO', ''):
            continue
        path = Path(value)
        path = path if path.is_absolute() else source.parent / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f'Missing {key}: {path}')
        if key in ('clm_hour_file_in', 'pft_mgmt_in', 'soil_mgmt_in'):
            with nc.Dataset(path) as ds:
                if 'year' not in ds.variables or data['year'] not in np.asarray(ds['year'][:]):
                    raise ValueError(f'{key} lacks selected year {data["year"]}')
        destination = scratch / 'input' / (key + path.suffix)
        copies.append(dict(key=key, source=str(path), destination=str(destination), sha256=digest(path)))
        eco[key] = str(destination)
    year = data['year']
    eco.update(case_name='output_check_' + re.sub(r'[^A-Za-z0-9_]', '_', str(data['site_id'])),
               start_date=f'{year:04d}0101000000', continue_run=False,
               forc_periods=[year, year, 1, year, year, 0, year, year, 0],
               hist_nhtfrq=-24, hist_mfilt=366)
    activation = include_required_history(eco, definitions)
    timing.update(stop_n=1, stop_option='nyears', rest_opt='nyears', rest_frq=1)
    # All validation above is read-only. Mutations are confined to a new scratch tree.
    (scratch / 'run_dir').mkdir(parents=True)
    (scratch / 'input').mkdir()
    for entry in copies:
        shutil.copy2(entry['source'], entry['destination'])
    output = scratch / 'run_dir/output_check.namelist'
    output.write_text(render(groups))
    assert parse_namelist(output.read_text()) == groups
    write_json(scratch / 'manifest.json', data)
    plan = dict(site_id=data['site_id'], year=year, source_namelist=str(source),
                source_namelist_sha256=digest(source), namelist=str(output),
                namelist_sha256=digest(output), manifest_sha256=digest(scratch / 'manifest.json'),
                ecosim_root=str(ecosim_root), input_files=copies,
                history_activation=activation,
                required_variables=list(definitions), expected_days=366 if calendar.isleap(year) else 365,
                scope='One-year cold-start output coverage, not scientific validation')
    write_json(scratch / 'plan.json', plan)
    return plan


def intact(scratch, plan):
    if digest(plan['namelist']) != plan['namelist_sha256'] or digest(scratch / 'manifest.json') != plan['manifest_sha256']:
        raise ValueError('Prepared namelist/manifest changed; prepare a new check')
    for entry in plan['input_files']:
        if digest(entry['destination']) != entry['sha256']:
            raise ValueError('Scratch input changed: ' + entry['destination'])


def run(scratch, exe, timeout_seconds=7200, allow_stale=False):
    scratch, exe = Path(scratch).resolve(), Path(exe).resolve()
    plan = json.loads((scratch / 'plan.json').read_text())
    intact(scratch, plan)
    if not exe.is_file() or not os.access(exe, os.X_OK):
        raise ValueError('Executable is missing or not executable')
    run_dir = scratch / 'run_dir'
    if (scratch / 'execution.json').exists() or (run_dir / 'run.log').exists() or list(run_dir.glob('*.ecosim.*.nc')):
        raise ValueError('Scratch case has already been run; use a fresh directory')
    stale = [str(p) for p in (Path(plan['ecosim_root']) / 'f90src').rglob('*.F90') if p.stat().st_mtime > exe.stat().st_mtime]
    if stale and not allow_stale:
        raise ValueError('Executable predates Fortran sources; rebuild or explicitly use --allow-stale-executable')
    source = Path(plan['source_namelist'])
    if not source.is_file() or digest(source) != plan['source_namelist_sha256']:
        raise ValueError('Production namelist changed; prepare a new output-check case')
    for entry in plan['input_files']:
        if not Path(entry['source']).is_file() or digest(entry['source']) != entry['sha256']:
            raise ValueError('Production input changed; prepare a new output-check case: ' + entry['source'])
    fresh_text = re.sub(r'^(\s*)continue_run\s*=.*$', r'\g<1>continue_run=.false.', source.read_text(), flags=re.MULTILINE)
    enforce_preflight(source, text=fresh_text, site_id=plan['site_id'], model_root=plan['ecosim_root'],
                      output=scratch / 'production.preflight.json')
    enforce_preflight(plan['namelist'], site_id=plan['site_id'], model_root=plan['ecosim_root'],
                      output=scratch / 'case.preflight.json')
    climate = load_module(ROOT / '.agents/skills/ecosim-climate-readiness/scripts/check_climate_readiness.py', 'output_check_climate')
    readiness = climate.validate(plan['namelist'])
    write_json(scratch / 'climate_readiness.json', readiness)
    if any(f['severity'] == 'error' for f in readiness['findings']):
        raise ValueError('Climate readiness failed; see climate_readiness.json')
    execution = dict(executable=str(exe), executable_sha256=digest(exe), stale_sources=stale,
                     stale_exception=allow_stale, started=datetime.now(timezone.utc).isoformat(),
                     timeout_seconds=timeout_seconds, timed_out=False)
    with (run_dir / 'run.log').open('w') as log:
        process = subprocess.Popen([str(exe), 'output_check.namelist'], cwd=run_dir,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            execution['returncode'] = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            execution.update(returncode=process.wait(), timed_out=True)
    execution['finished'] = datetime.now(timezone.utc).isoformat()
    write_json(scratch / 'execution.json', execution)
    return check(scratch)


def check(scratch):
    scratch = Path(scratch).resolve()
    plan = json.loads((scratch / 'plan.json').read_text())
    intact(scratch, plan)
    data, definitions = manifest(scratch / 'manifest.json')
    execution = json.loads((scratch / 'execution.json').read_text())
    failures, fields, bounds, supports = [], {}, [], {}
    run_dir = scratch / 'run_dir'
    if execution['returncode'] != 0 or execution.get('timed_out'):
        failures.append('Model failed or timed out')
    text = (run_dir / 'run.log').read_text(errors='replace')
    steps = re.findall(r'wrote out restart data at nstep\s*=\s*(\d+)', text)
    expected_steps = plan['expected_days'] * 24
    if not steps or int(steps[-1]) != expected_steps:
        failures.append(f'Expected {expected_steps} hourly steps, found {steps[-1] if steps else "none"}')
    if not list(run_dir.glob(f'*.ecosim.r.{plan["year"]+1:04d}-01-01-*.nc')):
        failures.append('No restart at the end of the selected year')
    paths = sorted(run_dir.glob('*.ecosim.h0.*.nc'))
    if not paths:
        failures.append('No h0 history written')
    normalize = lambda x: re.sub(r'\s+', '', x).lower()
    for path in paths:
        with nc.Dataset(path) as ds:
            if 'time_bounds' not in ds.variables:
                failures.append(path.name + ': time_bounds missing')
            else:
                bound = np.ma.asarray(ds['time_bounds'][:])
                if bound.ndim != 2 or bound.shape[1] != 2 or np.ma.getmaskarray(bound).any() or not np.isfinite(bound).all():
                    failures.append(path.name + ': invalid time bounds')
                elif getattr(ds['time_bounds'], 'units', ''):
                    failures.append(path.name + ': unsupported bounds units; review time convention')
                else:
                    bounds.extend(np.asarray(bound, dtype=float).tolist())
            for name, variable in ds.variables.items():
                if np.issubdtype(variable.dtype, np.number):
                    values = np.ma.asarray(variable[:])
                    if not np.isfinite(values.compressed()).all():
                        failures.append(path.name + ': unmasked NaN/Inf in ' + name)
            for name, definition in definitions.items():
                entry = fields.setdefault(name, dict(files=[], failures=[]))
                if name not in ds.variables:
                    entry['failures'].append(path.name + ': missing variable')
                    continue
                var = ds[name]
                entry['files'].append(path.name)
                units = str(getattr(var, 'units', ''))
                temporal = str(getattr(var, 'cell_method', getattr(var, 'cell_methods', '')))
                if normalize(units) not in {normalize(x) for x in definition['units']}:
                    entry['failures'].append(path.name + ': incompatible/missing units ' + units)
                if list(var.dimensions) != definition['dimensions']:
                    entry['failures'].append(path.name + ': dimensions ' + str(var.dimensions))
                method = definition['temporal']
                if method != 'static' and not re.search(r'\btime\s*:\s*' + method + r'\b', temporal):
                    entry['failures'].append(path.name + ': temporal metadata ' + temporal)
                values = np.ma.asarray(var[:])
                good = ~np.ma.getmaskarray(values) & np.isfinite(values.data)
                if method != 'static' and (values.ndim == 0 or not np.any(np.all(good, axis=0))):
                    entry['failures'].append(path.name + ': no consistent support with valid values at every time')
                if method != 'static' and values.ndim:
                    support = np.all(good, axis=0)
                    if name in supports and supports[name].shape != support.shape:
                        entry['failures'].append(path.name + ': spatial support dimensions changed')
                    else:
                        supports[name] = support if name not in supports else supports[name] & support
                valid = values.compressed()
                if valid.size == 0:
                    entry['failures'].append(path.name + ': entirely fill/masked')
                if definition.get('nonzero') and not np.any(valid != 0):
                    entry['failures'].append(path.name + ': all valid values are zero')
                if definition.get('varying') and (valid.size < 2 or np.ptp(valid) == 0):
                    entry['failures'].append(path.name + ': no variation')
                entry.update(units=units, dimensions=list(var.dimensions), temporal=temporal,
                             valid_values=int(valid.size))
    expected = np.column_stack((np.arange(plan['expected_days']), np.arange(1, plan['expected_days']+1)))
    ordered = np.asarray(sorted(bounds), dtype=float)
    if ordered.shape != expected.shape or not np.allclose(ordered, expected, atol=1e-6, rtol=0):
        failures.append('History does not cover exactly one complete daily calendar year')
    for name, support in supports.items():
        if not np.any(support):
            fields[name]['failures'].append('No consistent cell/PFT/layer is valid across all history files')
    targets = []
    for target in data['targets']:
        problems = [f'{v["name"]}: {reason}' for v in target.get('variables', [])
                    for reason in fields.get(v['name'], {}).get('failures', [])]
        if target['status'] == 'blocked':
            problems.append(target['reason'])
        targets.append(dict(id=target['id'], status='blocked' if problems else 'passed', reasons=problems,
                            comparison=target.get('comparison', 'Unresolved mapping')))
    status = 'failed' if failures else ('incomplete' if any(t['status'] != 'passed' for t in targets) else 'passed')
    report = dict(status=status, scope=plan['scope'], year=plan['year'], failures=failures,
                  history_activation=plan['history_activation'],
                  targets=targets, variables=fields, history=[str(p) for p in paths], execution=execution,
                  time_convention='Daily day-offset bounds inferred from this controlled scratch namelist; no CF time units emitted',
                  limitations=['A manifest is a reviewed mapping contract, not automated discovery of every observation.',
                               'No model-observation skill score, spin-up adequacy or scientific equivalence is established.'])
    write_json(scratch / 'report.json', report)
    lines = [f'# One-year evaluation output check: {status}', '', f'Year: {plan["year"]}. {plan["scope"]}.', '',
             '| Target | Status | Issues |', '|---|---|---|']
    lines += [f'| {t["id"]} | {t["status"]} | {"; ".join(t["reasons"]).replace(chr(124), "/")} |' for t in targets]
    lines += ['', *['- ' + x for x in failures], '', report['time_convention'], '', *report['limitations']]
    (scratch / 'report.md').write_text('\n'.join(lines) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    prep = subs.add_parser('prepare')
    prep.add_argument('--namelist', type=Path, required=True)
    prep.add_argument('--manifest', type=Path, required=True)
    prep.add_argument('--scratch', type=Path, required=True)
    prep.add_argument('--ecosim-root', type=Path, default=ROOT.parent)
    runner = subs.add_parser('run')
    runner.add_argument('--scratch', type=Path, required=True)
    runner.add_argument('--exe', type=Path, required=True)
    runner.add_argument('--timeout-seconds', type=int, default=7200)
    runner.add_argument('--allow-stale-executable', action='store_true')
    checker = subs.add_parser('check')
    checker.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.namelist, args.manifest, args.scratch, args.ecosim_root)
        elif args.command == 'run':
            if args.timeout_seconds <= 0:
                raise ValueError('Timeout must be positive')
            result = run(args.scratch, args.exe, args.timeout_seconds, args.allow_stale_executable)
        else:
            result = check(args.scratch)
        print(json.dumps({k: result[k] for k in ('status', 'year', 'scope') if k in result}, indent=2))
        return 0 if result.get('status', 'passed') == 'passed' else 1
    except (ValueError, KeyError, OSError, TypeError, IndexError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
