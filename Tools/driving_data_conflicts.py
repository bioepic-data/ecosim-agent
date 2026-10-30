"""Offline site-evidence checks shared by grid/climate preparation and assembly.

This supplements domain validators; it does not certify all driving data.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def explain_findings(findings, volume_audit=None):
    """Keep stable codes while adding physical meaning and numerical evidence."""
    layers = {r['layer']: r for r in (volume_audit or {}).get('layers', [])}
    for finding in findings:
        code = finding['code']
        row = layers.get(finding.get('layer'))
        evidence = {k: finding[k] for k in ('source', 'actual', 'expected', 'minimum', 'maximum',
                                           'missing_years', 'tolerance', 'uppermost_or_scalar_values',
                                           'parameter_source') if k in finding}
        kind = 'invalid_input' if finding['severity'] == 'error' else 'unverified_evidence'
        explanation = finding['message']
        consequence = ('This input cannot be accepted for the checked preparation scope.' if finding['severity'] == 'error'
                       else 'The available checks do not establish site-specific correctness.')
        resolution = 'Review the cited evidence, document the resolution and rerun the assessment.'
        if code == 'aspect_unverified':
            consequence = ('Aspect affects radiation geometry, terrain gradients and directional runoff; '
                           'a valid angle or matching runoff boundary does not establish the field direction.')
            resolution = ('Review site BIF, field survey, literature or a site-scale DEM. Record the actual '
                          'source and spatial support in ASPX.source, retain uncertainty, and review runoff '
                          'boundaries together. Gentle-slope text does not supply an aspect.')
        if row:
            evidence.update(row)
        if code == 'density_volume_basis_conflict' and row:
            expected, actual = row['source_bulk_density'], row['soil_mass_per_total_volume']
            change = 100*(actual/expected-1) if expected else None
            evidence['soil_mass_change_percent'] = change
            kind = 'volume_basis_mass_mismatch'
            explanation = (f"Layer {row['layer']} ({row['top_m']:.3g}–{row['bottom_m']:.3g} m): "
                           f"BKDSI={row['matrix_density']:.6g} Mg/m3 and FHOL={row['macropore_fraction']:.6g} "
                           f"initialize {actual:.6g} Mg soil per m3 of total layer, versus source bulk density "
                           f"{expected:.6g}; the mass differs by {change:.3g}%. "
                           'The source includes sampled macropore volume, while the model applies BKDSI to the smaller matrix domain.')
            consequence = 'Initial soil mass and concentration-to-stock C/N/P quantities change; model pore space no longer represents the source bulk density.'
            resolution = 'Reconcile bulk/matrix density, FHOL and FC/WP together. A density-only rescaling can exceed matrix water capacity; do not hide the mismatch by relabeling the source.'
            if row.get('source_comparison_is_conditional'):
                kind = 'conditional_volume_basis'
                explanation = (f"Layer {row['layer']} ({row['top_m']:.3g}–{row['bottom_m']:.3g} m): "
                               f"model mass per total volume = BKDSI*(1-ROCK)*(1-FHOL) = {actual:.6g} Mg/m3. "
                               f"Treating source density {expected:.6g} Mg/m3 as total-volume density would imply "
                               f"a {change:.3g}% difference, but that pore-volume equivalence is unverified. "
                               'Fine-earth density and gSSURGO identity alone do not establish coverage of model macropores; coated-clod oven-dry density excludes desiccation cracks.')
                consequence = 'This is a conditional input-domain comparison, not a confirmed soil-mass deficit, measured field loss or runtime conservation failure.'
                resolution = 'Match measurement method, moisture/volume state and sampled pore coverage to FHOL. Compare texture/organic-matter PTFs on explicit domains; retain matrix-capacity checks and do not rescale density solely to erase this scenario.'

        elif code == 'source_water_capacity_drained_macropores' and row:
            name = 'WP' if 'Source WP' in finding['message'] else 'FC'
            requested = row['source_'+name]+row['macropore_fraction']
            available = row['source_total_porosity']
            evidence.update(water_property=name, required_pore_fraction=requested,
                            available_pore_fraction=available, excess_pore_fraction=requested-available)
            kind = 'conditional_pore_capacity'
            explanation = (f"Layer {row['layer']}: source {name}={row['source_'+name]:.6g} plus "
                           f"FHOL={row['macropore_fraction']:.6g} requires {requested:.6g} m3/m3 of pores "
                           f"if macropores are drained, but source density and model particle density allow only "
                           f"{available:.6g}. Declared macropore occupancy: {row['retention_macropore_occupancy']}; source density includes model macropores: {row.get('source_density_includes_model_macropores')}.")
            consequence = 'Under that assumption, water and empty macropores require more volume than exists. Unknown occupancy or density pore coverage keeps this conditional; it is not proof that the field observations themselves conflict.'
            resolution = 'Establish water-volume denominators and macropore occupancy at the retention pressure, then check matrix FC/WP, total pore space and cold-start allocation together.'
        elif code in ('matrix_water_capacity', 'water_volume_basis_conflict') and row:
            name = 'WP' if finding['message'].startswith('WP') or 'Grid WP' in finding['message'] else 'FC'
            kind = 'water_capacity' if code == 'matrix_water_capacity' else 'water_volume_basis_mismatch'
            explanation = (f"Layer {row['layer']}: grid matrix {name}={row['matrix_'+name]} m3/m3, "
                           f"matrix porosity={row['matrix_porosity']:.6g}, FHOL={row['macropore_fraction']:.6g}. "
                           + finding['message'])
            consequence = 'The input either exceeds available matrix pore space or fails to preserve the stated source water volume.'
            resolution = 'Reconcile source and matrix water volumes with the declared macropore occupancy; do not simply clip FC/WP.'
        elif code == 'particle_density_conflict' and row:
            kind = 'solid_pore_volume'
            explanation = (f"Layer {row['layer']}: BKDSI={row['matrix_density']:.6g} Mg/m3, "
                           f"organic-adjusted particle density={row['particle_density']:.6g} Mg/m3, "
                           f"so 1−BKDSI/particle_density={row['matrix_porosity']:.6g} lies outside [0,1].")
            consequence = 'Solid material occupies an impossible fraction of the matrix; clipping can conceal invalid pore geometry.'
            resolution = 'Check density units, organic-carbon units, particle-density constants and source volume bases.'
        elif code == 'site_constraint_conflict':
            kind = 'field_evidence_contradiction'
            explanation = f"{finding['message']} Actual={finding.get('actual')}; allowed minimum={finding.get('minimum')}, maximum={finding.get('maximum')}. Evidence: {finding['source']}."
            consequence = 'A selected model input contradicts the field constraint; for terrain this changes runoff and lateral-flow forcing.'
            resolution = 'Use the source bound and proper units. If an exact value is unavailable, identify the adopted value as a constrained modeling assumption rather than a measurement.'
        elif code == 'field_capacity_pressure':
            kind = 'retention_pressure_definition'
            explanation = (f"The grid associates its supplied FC with PSIFC={finding.get('actual')} MPa, "
                           f"but gSSURGO wthirdbar defines it near {finding.get('expected')} MPa. "
                           'The same water content is being assigned to a different suction.')
            consequence = 'The retention-curve anchor is inconsistent with the source definition, affecting inferred matric potential and hydraulic behavior.'
            resolution = 'Use matching pressure and water-retention definitions, and recheck the pore-volume basis; changing pressure alone does not fix density/macropore conflicts.'
        elif code == 'retention_volume_basis_unverified' and row:
            kind = 'undeclared_water_partition'
            explanation = (f"Layer {row['layer']}: FHOL={row['macropore_fraction']:.6g}, "
                           f"matrix FC={row['matrix_FC']} and WP={row['matrix_WP']}; water assigned to "
                           'macropores at the source retention pressures is not declared. A unique bulk-to-matrix conversion cannot be established.')
            consequence = 'Matrix water may fit its pores while total layer water still differs from source FC/WP; THW=1 alone does not prove bulk-volume field-capacity initialization.'
            resolution = 'Document macropore occupancy and source water denominators, and evaluate SetColdRunSoilStates allocation explicitly.'
        elif code == 'duration_conflict':
            kind = 'simulation_duration'
            explanation = f"stop_n requests {finding.get('actual')} years, while the selected forcing cycles define {finding.get('expected')} years."
            consequence = 'The requested model duration and forcing sequence do not describe the same chronology.'
            resolution = 'Reconcile start_date, cycle counts and stop_n with the intended simulation period.'
        elif code == 'forcing_years_missing':
            kind = 'forcing_coverage'
            explanation = f"{finding['message']} Missing years: {finding.get('missing_years')}."
            consequence = 'Selected climate or management years lack matching annual records; filename coverage is insufficient.'
            resolution = 'Prepare the missing records or revise the selected forcing period with documented assumptions.'
        elif code == 'boundary_priors':
            kind = 'unverified_parameterization'
            consequence = 'Plausible numbers and valid units do not establish field support for hydrology, chemistry or exchange coefficients.'
            resolution = 'Link each value to measurements, an applicable estimate or a documented modeling prior; preserve the remaining uncertainty.'
        finding.update(conflict_type=kind, explanation=explanation, consequence=consequence,
                       resolution=resolution, evidence=evidence)


def assess(site_id, input_dir, grid=None, site_data=None, namelist=None):
    directory = Path(input_dir)
    findings, inputs = [], {}
    volume_audit = None

    def finding(severity, code, message, source, **details):
        findings.append(dict(severity=severity, code=code, message=message,
                             source=str(source), **details))

    def read(name):
        path = directory / name
        if not path.exists():
            return None
        inputs[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        return json.loads(path.read_text())

    cached_site = read(f'{site_id}_ecosim_site.json')
    site = site_data if site_data is not None else cached_site
    if site is None:
        finding('warning', 'site_metadata_missing', 'Site identity/location cannot be verified without site metadata.', directory)
        site = {}
    else:
        if site.get('site_name', site_id) != site_id:
            finding('error', 'site_identity', 'Metadata site ID differs from requested site.', directory,
                    actual=site.get('site_name'), expected=site_id)
        for name, bounds in {'ALATG': (-90, 90), 'ALONG': (-180, 180),
                             'ALTIG': (-500, 9000), 'ATCAG': (-80, 60)}.items():
            try:
                value = float(site[name])
                valid = math.isfinite(value) and bounds[0] <= value <= bounds[1]
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                finding('error', 'site_value', f'Missing/invalid site {name}.', directory)
        if not any(f['code'] == 'site_value' for f in findings):
            # Recheck cached metadata, rather than trusting a stored "passed" flag.
            path = ROOT / '.agents/skills/ameriflux-site-info/extract_ameriflux_site_data.py'
            spec = importlib.util.spec_from_file_location('site_location_checks', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = module.validate_site_location(site_id, site)
            for severity, key in [('error', 'errors'), ('warning', 'warnings')]:
                for message in result[key]:
                    finding(severity, 'site_location', message, path)
            for check, status in result['checks'].items():
                if status == 'not_available' or status.startswith('skipped'):
                    finding('warning', 'site_evidence_unavailable', f'{check}: {status}', path)

    constraints = []
    records = read(f'{site_id}_BIF_records.json')
    if records is None:
        finding('warning', 'field_records_missing', 'No reconstructed BIF records; field terrain/history review remains unverified.', directory)
    else:
        ids = {row.get('SITE_ID') for row in records}
        if ids != {site_id}:
            finding('error', 'field_site_identity', 'BIF records must contain only the requested site.', directory,
                    actual=sorted(str(x) for x in ids), expected=site_id)
        else:
            for row in records:
                text = row.get('TERRAIN', '')
                match = re.search(r'(?:<|less than)\s*(\d+(?:\.\d+)?)\s*(%|percent|degrees?|°)', text, re.I)
                if match:
                    value = float(match[1])
                    degrees = math.degrees(math.atan(value/100)) if match[2].lower() in ('%', 'percent') else value
                    constraints.append(dict(variable='SL0', maximum=degrees, exclusive_maximum=True,
                                            source=f'BIF group {row.get("GROUP_ID")}: {text}'))
                # Do not infer numerical constraints from ambiguous narrative.
                elif text:
                    finding('warning', 'terrain_manual_review', 'Terrain record needs manual numeric interpretation.', text)
                for field, var, tolerance in [('LOCATION_LAT', 'ALATG', 0.0001),
                                              ('LOCATION_LONG', 'ALONG', 0.0001),
                                              ('LOCATION_ELEV', 'ALTIG', 1.0)]:
                    if field in row and var in site:
                        try:
                            value = float(row[field])
                        except (TypeError, ValueError):
                            continue
                        if value <= -999:
                            continue
                        if abs(value - float(site[var])) > tolerance:
                            finding('error', 'field_location_conflict', f'{field} differs from site metadata.',
                                    f'BIF group {row.get("GROUP_ID")}', actual=site[var], expected=value)
    extra = read(f'{site_id}_site_constraints.json')
    if extra is not None:
        if extra.get('site_id') != site_id:
            finding('error', 'constraint_site_identity', 'Constraint file site ID differs.', directory)
        else:
            constraints.extend(extra.get('constraints', []))

    if grid is not None:
        grid = Path(grid)
        inputs[str(grid.resolve())] = hashlib.sha256(grid.read_bytes()).hexdigest()
        from Tools.create_ecosim_grid_forcing import validate_grid_layers
        for message in validate_grid_layers(grid):
            finding('error', 'soil_layers', message, grid)
        with nc.Dataset(grid) as ds:
            scalar = lambda n: float(np.ma.asarray(ds[n][:]).reshape(-1)[0])
            for name, tolerance in [('ALATG', 0.0001), ('ALONG', 0.0001),
                                    ('ALTIG', 1.0), ('ATCAG', 0.05), ('IETYPG', 0)]:
                if name in site and name in ds.variables:
                    actual = scalar(name)
                    if not math.isfinite(actual) or abs(actual-float(site[name])) > tolerance:
                        finding('error', 'grid_site_conflict', f'Grid {name} differs from selected site metadata.',
                                grid, actual=actual, expected=site[name], tolerance=tolerance)
            for constraint in constraints:
                name = constraint['variable']
                if not constraint.get('source'):
                    finding('error', 'constraint_source_missing', f'{name} constraint needs a source.', directory)
                    continue
                if name not in ds.variables:
                    finding('warning', 'constraint_unchecked', f'{name} absent from grid.', constraint['source'])
                    continue
                actual = scalar(name)
                low, high = constraint.get('minimum'), constraint.get('maximum')
                conflict = not math.isfinite(actual)
                conflict |= low is not None and actual < low
                conflict |= high is not None and (actual >= high if constraint.get('exclusive_maximum') else actual > high)
                if conflict:
                    finding('error', 'site_constraint_conflict', f'Grid {name} contradicts site evidence.',
                            constraint['source'], actual=actual, minimum=low, maximum=high)
            if not any(c['variable'] == 'SL0' for c in constraints):
                finding('warning', 'slope_unverified', 'No sourced terrain constraint; grid slope remains unverified.', grid)
            if 'ASPX' not in ds.variables:
                finding('warning', 'aspect_unverified', 'Grid aspect is absent; site orientation remains unverified.', grid)
            else:
                aspect = scalar('ASPX')
                aspect_evidence = aspect if math.isfinite(aspect) else str(aspect)
                source = str(getattr(ds['ASPX'], 'source', '')).strip()
                if not math.isfinite(aspect) or not 0 <= aspect <= 360:
                    finding('error', 'aspect_range', 'ASPX must be finite compass degrees in [0, 360].',
                            grid, actual=aspect_evidence)
                if (not source or source.lower() == 'site_metadata' or
                        any(word in source.lower() for word in ('default', 'template', 'unverified', 'unknown'))):
                    finding('warning', 'aspect_unverified',
                            'ASPX has missing or provisional provenance; do not treat the angle as field evidence.',
                            grid, actual=aspect_evidence, parameter_source=source or 'not recorded')
            profile = read(f'profile_{site_id}_source.json') or read(f'profile_{site_id}.json') or {}
            from Tools.soil_volume_checks import audit_soil_volumes, CONSTANTS
            volume_audit = audit_soil_volumes(ds, profile)
            findings.extend(volume_audit['findings'])
            inputs[str(CONSTANTS.resolve())] = hashlib.sha256(CONSTANTS.read_bytes()).hexdigest()
            fc_sources = profile.get('sources_by_variable', {}).get('FC', [])
            gssurgo_fc = ('gssurgo' in fc_sources or
                          (not fc_sources and profile.get('input', {}).get('gdb')))
            if gssurgo_fc and 'PSIFC' in ds.variables:
                # gSSURGO wthirdbar has a known pressure basis. Mixed/fallback
                # sources still require manual review in the preparation skill.
                if not math.isclose(scalar('PSIFC'), -0.033, abs_tol=0.0005):
                    finding('error', 'field_capacity_pressure', 'gSSURGO one-third-bar FC conflicts with grid PSIFC.',
                            'profile input.gdb / gSSURGO wthirdbar', actual=scalar('PSIFC'), expected=-0.033)
            if 'THI' in ds.variables:
                supplied = int(scalar('NJ') + scalar('NL1'))
                ice = np.ma.asarray(ds['THI'][:]).reshape(-1)[:supplied]
                if np.any(ice >= 0):
                    finding('warning', 'initial_ice_semantics', 'THI >= 0 may initialize ice; THI=0 means wilting-point ice, not zero. Review cold-start intent.', grid)
            priors = {n: scalar(n) for n in ds.variables if
                      n in ('IDTBLG', 'DTBLIG', 'DTBLDIG', 'FHOL', 'AEC', 'CNH4', 'CNO3', 'CPO4')
                      or n.startswith(('RCH', 'GKC'))}
            finding('warning', 'boundary_priors', 'Water-table modes/depths, lateral boundaries, macropores, nutrient/mineral pools and Gapon coefficients require site rationale; numerical validity does not establish field support.', grid,
                    uppermost_or_scalar_values=priors)

    if namelist is not None:
        path = Path(namelist)
        inputs[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        text = re.sub(r'!.*', '', path.read_text())
        match = re.search(r'forc_periods\s*=\s*([\d,\s]+)', text)
        start = re.search(r"start_date\s*=\s*['\"](\d{4})", text)
        stop = re.search(r'stop_n\s*=\s*(\d+)', text)
        annual = re.search(r"stop_option\s*=\s*['\"]nyears['\"]", text, re.I)
        if not (match and start and stop and annual):
            finding('warning', 'chronology_unverified', 'Annual chronology could not be parsed; check duration manually.', path)
        else:
            periods = [int(v) for v in re.findall(r'\d+', match[1])]
            if len(periods) % 3 or any(b < a for a, b, _ in zip(periods[::3], periods[1::3], periods[2::3])):
                finding('error', 'invalid_forcing_plan', 'Expected valid (start,end,cycles) triples.', path)
            else:
                blocks = list(zip(periods[::3], periods[1::3], periods[2::3]))
                duration = sum((b-a+1)*cycles for a, b, cycles in blocks)
                if duration != int(stop[1]):
                    finding('error', 'duration_conflict', 'stop_n differs from forcing-plan duration.', path,
                            actual=int(stop[1]), expected=duration,
                            forcing_plan_end_year=int(start[1])+duration-1)
                required = {y for a, b, cycles in blocks if cycles > 0 for y in range(a, b+1)}
                for field in ('clm_hour_file_in', 'pft_mgmt_in', 'soil_mgmt_in'):
                    value = re.search(rf"\b{field}\s*=\s*['\"]([^'\"]+)", text)
                    if value and value[1].upper() != 'NO':
                        source = (path.parent / value[1]).resolve()
                        with nc.Dataset(source) as data:
                            if 'year' not in data.variables:
                                finding('warning', 'coverage_unverified', f'{field}: no year variable.', source)
                                continue
                            years = set(int(y) for y in np.asarray(data['year'][:]).reshape(-1))
                        missing = sorted(required - years)
                        if missing:
                            finding('error', 'forcing_years_missing', f'{field} lacks selected forcing years.', source, missing_years=missing)
                        # Year-axis hashes are inexpensive and deliberately do not
                        # imply that a potentially large climate array was validated.
                        inputs[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()

    explain_findings(findings, volume_audit)
    status = 'failed' if any(f['severity'] == 'error' for f in findings) else ('warning' if findings else 'passed')
    return dict(site_id=site_id, assessed_at=datetime.now(timezone.utc).isoformat(), status=status,
                scope='Site evidence and grid conflicts; meteorology/management domain checks are separate.',
                input_sha256=inputs, findings=findings, soil_volume_audit=volume_audit)


def write_report(site_id, input_dir, grid=None, site_data=None, output=None, namelist=None):
    try:
        report = assess(site_id, input_dir, grid, site_data, namelist)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        report = dict(site_id=site_id, status='failed',
                      assessed_at=datetime.now(timezone.utc).isoformat(), input_sha256={},
                      findings=[dict(severity='error', code='unreadable_input',
                                     message=str(exc), source=str(input_dir))])
        explain_findings(report['findings'])
    path = Path(output) if output else Path(input_dir) / f'{site_id}_driving_data_conflicts.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('site_id')
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--grid', type=Path)
    parser.add_argument('--site-only', action='store_true')
    parser.add_argument('--namelist', type=Path, help='Also check annual duration and selected forcing-year coverage')
    args = parser.parse_args()
    grid = None if args.site_only else (args.grid or args.input_dir / f'{args.site_id}_ecosim_grid.nc')
    report = write_report(args.site_id, args.input_dir, grid, namelist=args.namelist)
    print(json.dumps(report, indent=2))
    return 1 if report['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
