"""Read-only precipitation budgets and reference-based plausibility screens."""
from __future__ import annotations
import calendar
import hashlib
import math
from pathlib import Path
import netCDF4 as nc
import numpy as np


def precipitation_budget(path, *, mean_annual_mm=None, reference_source=None, years=None):
    """Audit calendar-complete hourly RAINH, excluding non-leap padding.

    Thresholds are broad diagnostic screens, not correction rules. Partial years
    are never extrapolated. The source reference must accompany a positive MAP.
    """
    result = dict(file=str(Path(path).resolve()), annual=[], findings=[],
                  interpretation='Climate precipitation only; excludes irrigation. Seasonal sums use months in each calendar year.',
                  thresholds=dict(annual_ratio_low=.1, annual_ratio_high=3.,
                                  multiyear_ratio_low=.5, multiyear_ratio_high=2., minimum_mean_years=5))
    def warn(code, message, **evidence):
        result['findings'].append(dict(severity='warning', code=code, message=message, **evidence))
    with nc.Dataset(path) as ds:
        if mean_annual_mm is None:
            mean_annual_mm = getattr(ds, 'precip_climatology_mm', None)
            reference_source = reference_source or getattr(ds, 'precip_climatology_source', None)
        reference_source = reference_source or ''
        if mean_annual_mm is not None:
            mean_annual_mm = float(mean_annual_mm)
            if not math.isfinite(mean_annual_mm) or mean_annual_mm <= 0:
                raise ValueError('Precipitation climatology must be a finite positive mm/year value')
            if not str(reference_source).strip():
                raise ValueError('Precipitation climatology needs a source citation/path')
        result['reference'] = dict(mean_annual_mm=mean_annual_mm, source=str(reference_source))
        var = ds.variables.get('RAINH')
        if var is None or var.dimensions != ('year', 'day', 'hour', 'ngrid') or var.shape[2] != 24:
            warn('precipitation_budget_unavailable', 'Annual precipitation budget requires hourly RAINH(year,day,hour,ngrid).')
            result['status'] = 'unverified'
            return result
        # This audit sums mm over one-hour intervals, not arbitrary input units.
        units = str(getattr(var, 'units', '')).lower().replace(' ', '').replace('^', '').replace('_', '')
        if units not in ('mmhr-1', 'mmh-1', 'mm/hour', 'mmm-2hr-1'):
            warn('precipitation_budget_units_unverified', 'Cannot sum precipitation budgets in unsupported or missing units.', units=units)
            result['status'] = 'unverified'
            return result
        for j, year in enumerate(np.asarray(ds['year'][:], int)):
            if years is not None and int(year) not in years:
                continue
            days = 366 if calendar.isleap(int(year)) else 365
            values = np.ma.filled(np.ma.asarray(var[j, :days, :, :], dtype=float), np.nan)
            for cell in range(values.shape[-1]):
                data = values[:, :, cell]
                valid = np.isfinite(data) & (data >= 0) & (data < 1e29)
                complete = data.shape == (days, 24) and bool(valid.all())
                monthly = []
                start = 0
                for month in range(1, 13):
                    end = start + calendar.monthrange(int(year), month)[1]
                    chunk = data[start:end]
                    ok = chunk.shape == (end-start, 24) and bool(valid[start:end].all())
                    monthly.append(float(chunk.sum()) if ok else None)
                    start = end
                seasons = {name: sum(monthly[m-1] for m in months) if all(monthly[m-1] is not None for m in months) else None
                           for name, months in {'DJF': [12,1,2], 'MAM': [3,4,5], 'JJA': [6,7,8], 'SON': [9,10,11]}.items()}
                total = float(data.sum()) if complete else None
                result['annual'].append(dict(year=int(year), gridcell=cell, complete=complete,
                    valid_hours=int(valid.sum()), expected_hours=days*24, annual_mm=total,
                    partial_valid_sum_mm=float(data[valid].sum()), monthly_mm=monthly, seasonal_mm=seasons))
                if not complete:
                    warn('precipitation_budget_incomplete', f'{year} cell {cell}: no full-year precipitation total; partial data are not annualized.', year=int(year), gridcell=cell)
                elif mean_annual_mm is not None:
                    ratio = total/mean_annual_mm
                    if ratio > 3 or ratio < .1:
                        warn('precipitation_annual_climatology_mismatch', f'{year} cell {cell}: precipitation {total:.1f} mm is {ratio:.2f} times site MAP {mean_annual_mm:g} mm; investigate units/source and representativeness, do not auto-rescale.', year=int(year), gridcell=cell, ratio=ratio)
    cells = sorted({row['gridcell'] for row in result['annual']})
    result['summaries'] = []
    for cell in cells:
        totals = [r['annual_mm'] for r in result['annual'] if r['gridcell']==cell and r['complete']]
        if not totals:
            continue
        mean = float(np.mean(totals))
        result['summaries'].append(dict(gridcell=cell, complete_years=len(totals), mean_annual_mm=mean, min_annual_mm=min(totals), max_annual_mm=max(totals)))
        if mean_annual_mm is not None and len(totals) >= 5:
            ratio = mean/mean_annual_mm
            if ratio > 2 or ratio < .5:
                warn('precipitation_multiyear_climatology_mismatch', f'Cell {cell}: {len(totals)} complete-year mean {mean:.1f} mm is {ratio:.2f} times site MAP {mean_annual_mm:g} mm; climatological plausibility unresolved.', gridcell=cell, ratio=ratio)
    if mean_annual_mm is None:
        warn('precipitation_climatology_unverified', 'No sourced site MAP: precipitation budgets are computed but climatological plausibility is unverified.', summaries=result['summaries'])
    observation_status = str(getattr_from_file(path, 'precip_tower_validation_status', 'unverified'))
    if observation_status == 'compared':
        with nc.Dataset(path) as dataset:
            expected = getattr(dataset, 'precip_tower_validation_rain_sha256', '')
            if expected != rainfall_hash(dataset):
                observation_status = 'stale_or_unverified_identity'
    result['measured_tower_validation_status'] = observation_status
    if observation_status != 'compared':
        warn('precipitation_tower_unverified', 'Precipitation has no sufficient measured-tower validation; reanalysis-filled P_F must not substitute for measured rain.', observation_status=observation_status)
    result['status'] = 'review_required' if result['findings'] else 'screened'
    return result


def getattr_from_file(path, name, default):
    with nc.Dataset(path) as ds:
        return getattr(ds, name, default)


def rainfall_hash(dataset):
    values = np.ma.filled(np.ma.asarray(dataset['RAINH'][:], dtype=float), np.nan)
    return hashlib.sha256(values.tobytes()).hexdigest()


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('climate_file')
    parser.add_argument('--mean-annual-mm', type=float)
    parser.add_argument('--reference-source')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = precipitation_budget(args.climate_file, mean_annual_mm=args.mean_annual_mm, reference_source=args.reference_source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        print(f"Precipitation budget: {report['status']}; {len(report['findings'])} findings; {args.output}")
    else:
        print(json.dumps(report, indent=2, allow_nan=False))
