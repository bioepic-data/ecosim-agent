#!/usr/bin/env python3
"""Read-only namelist-driven validation of EcoSIM annual weather NetCDF inputs."""
from __future__ import annotations

import argparse
import calendar
import hashlib
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys

import netCDF4 as nc
import numpy as np

AGENT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(AGENT))
from Tools.namelist_parser import parse_namelist

# Units are the INPUT units used by ClimReadMod, before its hourly conversions.
HOURLY = {
    'TMPH': (-100., 65., ('oc', 'degc', 'degreecelsius', 'degreescelsius', 'celsius', 'c')),
    'WINDH': (0., 100., ('ms-1', 'm/s')),
    'RAINH': (0., 500., ('mmhr-1', 'mmh-1', 'mm/hour', 'mmm-2hr-1')),
    'DWPTH': (0., 20., ('kpa',)),
    'SRADH': (0., 1500., ('wm-2', 'w/m2')),
}
DAILY = {
    'TMPX': HOURLY['TMPH'], 'TMPN': HOURLY['TMPH'],
    'WIND': (0., 360000., ('mhr-1', 'mh-1', 'm/hour')),
    'RAIN': (0., 12., ('mday-1', 'md-1', 'm/day')),
    'DWPT': HOURLY['DWPTH'],
    'SRAD': (0., 130., ('mjm-2day-1', 'mj/m2/day')),
}
AUXILIARY = ('Z0G', 'ZNOONG', 'IFLGW', 'PHRG', 'CN4RIG', 'CNORIG', 'CPORG',
             'CALRG', 'CFERG', 'CCARG', 'CMGRG', 'CNARG', 'CKARG', 'CSORG', 'CCLRG')
OPTIONAL = {'PATM': (30., 115., ('kpa',)), 'XRADH': (0., 1000., ('wm-2', 'w/m2')),
            'DRYDEPOC': (0., None, ())}


def norm_unit(value):
    return re.sub(r'[\s_^*{}()]', '', str(value).lower()).replace('−', '-')


def add(report, severity, code, message, **evidence):
    report['findings'].append(dict(severity=severity, code=code, message=message, **evidence))


def integer(value, name, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def local_path(value, cwd):
    if not isinstance(value, str):
        raise ValueError('Input path must be a quoted string')
    # Fortran trims trailing blanks; do not interpret shell variables or ~.
    return (cwd / value.rstrip()).resolve()


def configured_path(values, key, cwd):
    value = values.get(key, '')
    if isinstance(value, str) and value.strip().upper() == 'NO':
        raise ValueError(f"{key}='NO' is not a disabled input in this reader; omit an unused climate path or use an empty string")
    return None if isinstance(value, str) and not value.rstrip() else local_path(value, cwd)


def parse_date(value):
    return datetime.strptime(str(value), '%Y%m%d%H%M%S')


def checkpoint_date(path):
    if not path.is_file():
        raise ValueError(f'Restart checkpoint not found: {path}')
    m = re.search(r'\.r\.(\d{4}-\d{2}-\d{2})-(\d{5,6})\.nc$', path.name)
    if not m or int(m[2]) >= 86400:
        raise ValueError(f'Cannot establish checkpoint date from {path.name}')
    return datetime.strptime(m[1], '%Y-%m-%d') + timedelta(seconds=int(m[2]))


def schedule(groups, cwd, report, all_periods=False):
    eco = groups.get('ecosim', {})
    timer = groups.get('ecosim_time', {})
    start = parse_date(eco.get('start_date', '18000101000000'))
    origin = datetime(start.year, 1, 1)
    supplied = eco.get('forc_periods', [])
    if not isinstance(supplied, list) or len(supplied) % 3 or len(supplied) > 15:
        raise ValueError('forc_periods must contain up to five complete year/year/cycle triplets')
    # Fortran array assignments overwrite only the supplied prefix. Unspecified
    # entries retain the standalone driver's initialized forcing periods.
    defaults = [1980,1980,1,1981,1988,2,1989,2008,1,0,0,0,0,0,0]
    periods = supplied + defaults[len(supplied):]
    report['effective_forc_periods'] = periods
    if len(supplied) < 9:
        add(report, 'warning', 'inherited_forcing_periods', 'Unspecified array entries retain active driver defaults', effective_forc_periods=periods)
    years = []
    for lo, hi, cycles in zip(periods[::3], periods[1::3], periods[2::3]):
        integer(cycles, 'forcing cycles')
        if cycles == 0:
            continue
        for value in (lo, hi):
            integer(value, 'forcing year', 1)
        if len(years) + (abs(hi-lo)+1)*cycles > 20000:
            raise ValueError('Forcing schedule exceeds 20,000 model years')
        step = 1 if hi >= lo else -1
        years.extend(list(range(lo, hi+step, step))*cycles)
    if not years:
        raise ValueError('No active forcing periods')
    end = datetime(start.year+len(years), 1, 1)
    stop_n = integer(timer.get('stop_n', 2), 'stop_n', 1)
    stop_opt = timer.get('stop_option', 'nyears')
    dt = timer.get('delta_time', 1800.)
    if isinstance(dt, bool) or not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt <= 0 or not math.isclose(3600/dt, round(3600/dt), rel_tol=0., abs_tol=1e-8):
        raise ValueError('delta_time must be positive and divide one hour exactly')
    if stop_opt == 'nyears':
        stop_end = datetime(start.year+min(stop_n, len(years)), 1, 1)
        if stop_n > len(years):
            add(report, 'warning', 'duration_capped', 'Year-based stop is capped by the forcing schedule',
                stop_n=stop_n, scheduled_years=len(years))
    elif stop_opt == 'ndays':
        stop_end = origin + timedelta(days=stop_n)
    elif stop_opt == 'nmonths':
        stop_end = datetime(start.year+stop_n//12, stop_n % 12+1, 1)
    elif stop_opt == 'nsteps':
        stop_end = origin + timedelta(seconds=stop_n*dt)
    else:
        raise ValueError(f'Unsupported stop_option: {stop_opt}')
    if not all_periods:
        end = min(end, stop_end)
    continuing = eco.get('continue_run', False)
    if not isinstance(continuing, bool):
        raise ValueError('continue_run must be logical')
    if all_periods:
        start = origin
    elif continuing:
        pointer = local_path(eco.get('rpntdir', '.'), cwd) / eco.get('rpntfil', 'rpointer.esim')
        lines = pointer.read_text().splitlines()
        if not lines or not lines[0].strip():
            raise ValueError(f'Empty restart pointer: {pointer}')
        checkpoint = local_path(lines[0], cwd)
        start = checkpoint_date(checkpoint)
        report['restart'] = dict(pointer=str(pointer), checkpoint=str(checkpoint), date=start.isoformat())
    else:
        seed = configured_path(eco, 'finidat', cwd)
        if seed:
            if checkpoint_date(seed) != start:
                raise ValueError('Branch start_date differs from finidat checkpoint date; resolve the branch clock before validation')
            report['restart'] = dict(checkpoint=str(seed), date=start.isoformat(), mode='branch')
    if (start.minute or start.second or end.minute or end.second):
        add(report, 'warning', 'partial_hour', 'The entire intersecting weather hour is checked for sub-hourly boundaries')
    rest_opt = timer.get('rest_opt', 'never')
    if rest_opt not in ('never', 'nsteps', 'nhours', 'ndays', 'nweeks', 'nmonths', 'nyears'):
        add(report, 'error', 'invalid_rest_opt', f'Unsupported rest_opt={rest_opt!r}; the model silently disables periodic checkpoints')
    report['window'] = dict(start=start.isoformat(), end_exclusive=end.isoformat(),
                            scope='all_scheduled_periods' if all_periods else 'remaining_run',
                            scheduled_years=len(years))
    if not origin <= start < end:
        raise ValueError('Effective start/restart is outside the requested simulation window')
    spans = []
    for index, forcing_year in enumerate(years):
        model_year = origin.year+index
        lower, upper = max(start, datetime(model_year, 1, 1)), min(end, datetime(model_year+1, 1, 1))
        if lower < upper:
            first = int((lower-datetime(model_year, 1, 1)).total_seconds()//3600)
            last = math.ceil((upper-datetime(model_year, 1, 1)).total_seconds()/3600)
            # Non-leap forcing's day 366 is copied from day 365 by ClimReadMod.
            spans.append(dict(model_year=model_year, forcing_year=forcing_year,
                              first=lower.isoformat(), end_exclusive=upper.isoformat(),
                              first_hour=first, last_hour_exclusive=last))
    return spans


def invalid_values(var, values):
    raw = np.asarray(np.ma.getdata(values), dtype=float)
    bad = np.ma.getmaskarray(values) | ~np.isfinite(raw) | (np.abs(raw) >= 1e20)
    # Explicit markers are needed even when a malformed missing_value is ignored by netCDF4.
    for attr in ('_FillValue', 'missing_value'):
        for marker in np.asarray(getattr(var, attr, [])).ravel():
            try:
                bad |= raw == float(marker)
            except (TypeError, ValueError):
                continue
    return raw, bad


def locations(year, days, hours, bad, limit=8):
    indices = np.flatnonzero(bad)
    examples = []
    for i in indices[:limit]:
        row = dict(forcing_date=(datetime(year, 1, 1)+timedelta(days=int(days[i]))).strftime('%Y-%m-%d'),
                   day_of_year=int(days[i])+1)
        if hours is not None:
            row['hour_index_1based'] = int(hours[i])+1
        examples.append(row)
    return examples


def inspect_values(report, var, values, bounds, path, year, days=None, hours=None):
    raw, missing = invalid_values(var, values)
    evidence = dict(file=str(path), variable=var.name, forcing_year=year)
    if missing.any():
        add(report, 'error', 'missing_values', 'Missing, nonfinite, or sentinel values in required forcing records',
            count=int(missing.sum()), examples=locations(year, days, hours, missing) if days is not None else [], **evidence)
    lo, hi, units = bounds
    outside = ~missing & ((raw < lo) if lo is not None else np.zeros(raw.shape, bool))
    if hi is not None:
        outside |= ~missing & (raw > hi)
    if outside.any():
        add(report, 'error', 'physical_range', 'Values outside broad physical screening limits in reader input units',
            lower=lo, upper=hi, count=int(outside.sum()),
            examples=locations(year, days, hours, outside) if days is not None else [], **evidence)
    return int(raw.size), int((missing | outside).sum())


def inspect_file(path, stack, report, mode):
    ds = stack.enter_context(nc.Dataset(path, 'r'))
    # EcoSIM's ncdio uses raw nf90_get_var values, without CF unpacking.
    # Check markers explicitly instead of relying on netCDF4's mask coercion.
    ds.set_auto_maskandscale(False)
    if 'year' not in ds.variables or ds['year'].dimensions != ('year',):
        raise ValueError(f'{path}: expected year(year) coordinate')
    for obj in [ds] + [ds.variables[k] for k in ('year', 'day', 'time') if k in ds.variables]:
        declared = str(getattr(obj, 'calendar', '')).lower().strip()
        if declared and declared not in ('standard', 'gregorian', 'proleptic_gregorian'):
            raise ValueError(f'{path}: unsupported calendar {declared!r}; reader uses Gregorian model/forcing years')
    raw, bad = invalid_values(ds['year'], ds['year'][:])
    if bad.any() or np.any(raw != np.floor(raw)) or np.any(raw < 1) or np.any(raw > 9998):
        raise ValueError(f'{path}: invalid year coordinate')
    years = raw.astype(int).tolist()
    if not years or years != sorted(set(years)):
        raise ValueError(f'{path}: duplicate or nonmonotonic year coordinate')
    expected = {'ngrid': 1, 'day': 366}
    if mode == 'hourly':
        expected['hour'] = 24
    for name, size in expected.items():
        if name not in ds.dimensions or len(ds.dimensions[name]) != size:
            raise ValueError(f'{path}: reader requires {name}={size}')
    entry = dict(path=str(path), mode=mode, year_coordinates=years,
                 size_bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
                 variables={}, required_years=[], checked_values=0, invalid_values=0)
    report['files'].append(entry)
    return ds, years, entry


def variable_schema(ds, name, dims, bounds, entry, report):
    if name in entry['variables']:
        return entry['variables'][name]['schema_valid']
    var = ds.variables.get(name)
    valid = var is not None and var.dimensions == dims and np.dtype(var.dtype).kind in 'iuf'
    entry['variables'][name] = dict(schema_valid=valid, expected_dimensions=list(dims),
                                     units=str(getattr(var, 'units', '')))
    if not valid:
        add(report, 'error', 'variable_schema', f'Required variable {name} missing or incompatible with reader schema',
            file=entry['path'], variable=name, expected_dimensions=list(dims))
        return False
    if any(np.asarray(getattr(var, key, default)).size != 1 or
           not np.all(np.asarray(getattr(var, key, default)) == default)
           for key, default in [('scale_factor', 1), ('add_offset', 0)]):
        entry['variables'][name]['schema_valid'] = False
        add(report, 'error', 'packed_variable', 'Packed variables are not unpacked by the supported EcoSIM reader',
            file=entry['path'], variable=name)
        return False
    allowed = bounds[2]
    units = str(getattr(var, 'units', ''))
    if allowed and not units.strip():
        add(report, 'warning', 'units_unverified', 'Units metadata missing; values screened using reader input units',
            file=entry['path'], variable=name)
    elif allowed and norm_unit(units) not in allowed:
        add(report, 'error', 'units_mismatch', 'Units metadata does not match the selected reader input convention',
            file=entry['path'], variable=name, units=units, accepted_units=list(allowed))
    return True


def validate(namelist, run_cwd=None, all_periods=False):
    path = Path(namelist).resolve()
    report = dict(schema_version=1, assessed_at=datetime.now(timezone.utc).isoformat(),
                  namelist=str(path), run_cwd=str(Path(run_cwd).resolve() if run_cwd else path.parent),
                  findings=[], files=[], scope='Meteorological forcing and required annual climate auxiliaries; not a whole-case readiness certificate')
    try:
        content = path.read_bytes()
        report['namelist_sha256'] = hashlib.sha256(content).hexdigest()
        groups = parse_namelist(content.decode())
        eco = groups.get('ecosim', {})
        report['case_name'] = str(eco.get('case_name', path.stem))
        cwd = Path(report['run_cwd'])
        spans = schedule(groups, cwd, report, all_periods)
        report['mapping'] = [{k: v for k, v in span.items() if k not in ('first_hour', 'last_hour_exclusive')} for span in spans]
        with ExitStack() as stack:
            datasets = {}
            for mode, key in [('daily', 'clm_day_file_in'), ('hourly', 'clm_hour_file_in')]:
                forcing = configured_path(eco, key, cwd)
                if forcing:
                    datasets[mode] = (forcing, *inspect_file(forcing, stack, report, mode))
            if not datasets:
                raise ValueError('No meteorological NetCDF input selected by the namelist')
            # Match get_forc_step_type: daily only before first hourly year;
            # a daily-only configuration has yearf2=-999 and selects hourly.
            if 'hourly' not in datasets:
                raise ValueError('This standalone reader requires clm_hour_file_in; daily-only selection is unsupported by get_forc_step_type')
            hourly_first = datasets['hourly'][2][0]
            requested = {}
            daily_neighbors = {}
            for span in spans:
                year = span['forcing_year']
                mode = 'daily' if 'daily' in datasets and year < hourly_first else 'hourly'
                file, ds, years, entry = datasets[mode]
                if year not in years:
                    add(report, 'error', 'missing_year', 'Scheduled forcing year absent from reader-selected climate file',
                        file=str(file), forcing_year=year, model_year=span['model_year'], mode=mode)
                    continue
                bucket = requested.setdefault((mode, year), set())
                hours = np.arange(span['first_hour'], span['last_hour_exclusive'])
                days = np.minimum(hours//24, 365 if calendar.isleap(year) else 364)
                bucket.update(zip(days.tolist(), (hours % 24).tolist()))
                if mode == 'daily':
                    # DayMod interpolates with previous-day max temperature,
                    # next-day min temperature, and both neighboring dewpoints.
                    model_days = np.unique(hours//24)
                    last_day = 365 if calendar.isleap(span['model_year']) else 364
                    forcing_last = 365 if calendar.isleap(year) else 364
                    previous = np.minimum(np.maximum(model_days-1, 0), forcing_last)
                    following = np.minimum(np.minimum(model_days+1, last_day), forcing_last)
                    for name, extra in [('TMPX', previous), ('TMPN', following), ('DWPT', np.union1d(previous, following))]:
                        daily_neighbors.setdefault((mode, year, name), set()).update(extra.tolist())
            for (mode, year), selected in sorted(requested.items()):
                file, ds, years, entry = datasets[mode]
                entry['required_years'].append(year)
                pairs = np.asarray(sorted(selected))
                days, hours = pairs[:, 0], pairs[:, 1]
                fields = dict(HOURLY if mode == 'hourly' else DAILY)
                if mode == 'hourly':
                    fields.update({k: v for k, v in OPTIONAL.items() if k in ds.variables})
                for name, bounds in fields.items():
                    daily_field = mode == 'daily' or name == 'DRYDEPOC'
                    dims = ('year', 'day', 'ngrid') if daily_field else ('year', 'day', 'hour', 'ngrid')
                    if not variable_schema(ds, name, dims, bounds, entry, report):
                        continue
                    var = ds[name]
                    # Read one annual slab at a time, then use paired hour indices
                    # in NumPy (netCDF4 integer indexing is orthogonal).
                    if daily_field:
                        dd, hh = np.asarray(sorted(set(days.tolist()) | daily_neighbors.get((mode, year, name), set())), dtype=int), None
                        values = var[years.index(year), :, 0][dd]
                    else:
                        dd, hh = days, hours
                        values = var[years.index(year), :, :, 0][dd, hh]
                    count, invalid = inspect_values(report, var, values, bounds, file, year, dd, hh)
                    entry['checked_values'] += count
                    entry['invalid_values'] += invalid
                for name in AUXILIARY:
                    bounds = (0., 14., ()) if name == 'PHRG' else (0., 24., ()) if name == 'ZNOONG' else (0., 1., ()) if name == 'IFLGW' else (0., None, ())
                    if not variable_schema(ds, name, ('year', 'ngrid'), bounds, entry, report):
                        continue
                    count, invalid = inspect_values(report, ds[name], ds[name][years.index(year), :], bounds, file, year)
                    entry['checked_values'] += count
                    entry['invalid_values'] += invalid
        add(report, 'warning', 'clock_unverified', 'Timestamp timezone/interval registration is not established by year/day/hour indices; verify provenance separately')
    except (OSError, ValueError, TypeError, IndexError, KeyError, OverflowError) as exc:
        add(report, 'error', 'configuration_or_schema', str(exc))
    report['status'] = 'invalid' if any(f['severity'] == 'error' for f in report['findings']) else 'ready_with_warnings' if report['findings'] else 'ready'
    return report


def markdown(report):
    lines = ['# EcoSIM climate readiness', '', f"Status: **{report['status']}**", '',
             f"Namelist: `{report['namelist']}`", f"Run directory: `{report['run_cwd']}`", '', report['scope'], '']
    if 'window' in report:
        lines.extend([f"Window: {report['window']['start']} to {report['window']['end_exclusive']} (exclusive)", ''])
    for finding in report['findings']:
        lines.append(f"- **{finding['severity']} / {finding['code']}**: {finding['message']}")
        detail = {k: v for k, v in finding.items() if k not in ('severity', 'code', 'message')}
        if detail:
            lines.append('  ' + json.dumps(detail, ensure_ascii=False))
    lines += ['', 'The checker does not repair data, launch EcoSIM, validate all case inputs, or certify scientific correctness.', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('namelist', type=Path)
    parser.add_argument('--run-cwd', type=Path, help='Launch directory for relative input/pointer paths (default: namelist directory)')
    parser.add_argument('--all-periods', action='store_true', help='Audit the full forcing schedule, ignoring stop_n and restart/start-date truncation')
    parser.add_argument('--output-dir', type=Path, help='New directory for report.json and report.md; refuses overwrite')
    args = parser.parse_args(argv)
    report = validate(args.namelist, args.run_cwd, args.all_periods)
    name = re.sub(r'[^A-Za-z0-9_.-]', '_', report.get('case_name', args.namelist.stem))
    out = args.output_dir or AGENT / 'result/climate_readiness' / (name+'_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    try:
        out.mkdir(parents=True, exist_ok=False)
        (out/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        (out/'report.md').write_text(markdown(report))
    except OSError as exc:
        parser.exit(2, f'Cannot write new report directory: {exc}\n')
    errors = sum(f['severity'] == 'error' for f in report['findings'])
    print(f"{report['status'].upper()}: {errors} error(s); {out.resolve()/'report.md'}")
    for f in report['findings']:
        if f['severity'] == 'error':
            print(f"  {f['code']}: {f.get('variable', '')} {f.get('forcing_year', '')} {f['message']}")
    return int(report['status'] == 'invalid')


if __name__ == '__main__':
    raise SystemExit(main())
