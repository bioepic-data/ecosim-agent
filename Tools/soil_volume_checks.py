"""Cold-start soil volume audit; never converts or edits grid inputs."""
import re
from pathlib import Path

import numpy as np

CONSTANTS = Path(__file__).resolve().parents[2] / 'f90src/Utils/EcoSimConst.F90'


def particle_density(corgc):
    """Mirror current StartsMod mixing rule; CORGC input is kg C/Mg soil."""
    text = CONSTANTS.read_text()
    constants = {}
    for name in ('OMCMassFrac', 'DensitySolidOM', 'DensitySolidMineral'):
        match = re.search(rf'\b{name}\s*=\s*([\d.Ee+-]+)_r8', text, re.I)
        if not match:
            raise ValueError(f'Cannot read model constant {name}')
        constants[name] = float(match[1])
    fraction = np.clip(np.asarray(corgc)/1000/constants['OMCMassFrac'], 0, 1)
    return (constants['DensitySolidOM']*fraction +
            constants['DensitySolidMineral']*(1-fraction))


def source_average(profile, name, top, bottom):
    data = profile.get('interpolated', {})
    if name not in data or 'CDPTH' not in data:
        return None
    depths = np.asarray(data['CDPTH'], dtype=float)
    values = np.asarray(data[name], dtype=float)
    if (len(depths) != len(values) or not np.all(np.isfinite(depths)) or
            np.any(depths <= 0) or np.any(np.diff(depths) <= 0)):
        raise ValueError('Invalid source-profile layer geometry')
    weights = np.maximum(0, np.minimum(depths, bottom)-np.maximum(np.r_[0, depths[:-1]], top))
    used = weights > 0
    if (abs(weights.sum()-(bottom-top)) > 1.e-6 or
            not np.all(np.isfinite(values[used])) or np.any(values[used] < 0)):
        return None  # missing/estimated values and uncovered depths are unverified
    return float(np.dot(weights[used], values[used])/(bottom-top))


def audit_soil_volumes(ds, source=None):
    source = source or {}
    findings, rows = [], []

    def flag(severity, code, message, layer=None):
        findings.append(dict(severity=severity, code=code, message=message,
                             source='EcoSIM cold-start volume equations / source-profile basis', layer=layer))

    required = ('NUI', 'NJ', 'NL1', 'CDPTH', 'BKDSI', 'CORGC', 'FHOL', 'ROCK', 'FC', 'WP')
    if any(n not in ds.variables for n in required) or len(ds.dimensions.get('ntopou', [])) != 1:
        flag('warning', 'soil_volume_unverified', 'Volume audit requires the single-unit grid variables and layer selectors.')
        return dict(layers=rows, findings=findings)
    first = int(np.ravel(ds['NUI'][:])[0])-1
    count = int(np.ravel(ds['NJ'][:])[0]+np.ravel(ds['NL1'][:])[0])
    if first < 0 or count <= first or count > len(ds.dimensions['nlevs']):
        flag('error', 'soil_volume_selectors', 'Invalid supplied-layer selectors.')
        return dict(layers=rows, findings=findings)
    data = {n:np.ma.filled(ds[n][:].astype(float), np.nan).reshape(-1)[:count]
            for n in required if n not in ('NUI', 'NJ', 'NL1')}
    densities = particle_density(data['CORGC'])
    basis = source.get('volume_basis', {})
    inferred = bool(source.get('input', {}).get('gdb'))
    density_basis = basis.get('BKDSI', 'fine_earth_bulk' if inferred else 'unknown')
    occupancy = basis.get('macropores_at_retention', 'unknown')
    pore_evidence = basis.get('density_pore_basis_evidence', '')
    includes_macro = basis.get('density_includes_model_macropores')
    # Fine-earth specifies rock exclusion, not coverage of structural voids.
    if not isinstance(includes_macro, bool) or not isinstance(pore_evidence, str) or not pore_evidence.strip():
        includes_macro = None
    for i in range(first, count):
        layer = i+1
        bd, f, rock, rho = (data['BKDSI'][i], data['FHOL'][i], data['ROCK'][i], densities[i])
        if not all(np.isfinite(v) for v in (bd, f, rock, rho)) or not (bd > 0 and 0 <= f < 1 and 0 <= rock < 1):
            flag('error', 'soil_volume_invalid', 'Invalid density or pore/rock fractions.', layer)
            continue
        phi = 1-bd/rho  # do not hide negative pore space with the model's clipping
        row = dict(layer=layer, top_m=0.0 if i == 0 else float(data['CDPTH'][i-1]), bottom_m=float(data['CDPTH'][i]),
                   particle_density=float(rho), matrix_density=float(bd),
                   matrix_porosity=float(phi), macropore_fraction=float(f),
                   pore_fraction_total_layer=float(phi*(1-rock)*(1-f)+f),
                   soil_mass_per_total_volume=float(bd*(1-rock)*(1-f)),
                   source_density_basis=density_basis, retention_macropore_occupancy=occupancy,
                   source_density_includes_model_macropores=includes_macro,
                   density_pore_basis_evidence=pore_evidence,
                   source_comparison_is_conditional=includes_macro is not True)
        for name in ('FC', 'WP'):
            row['matrix_'+name] = float(data[name][i]) if np.isfinite(data[name][i]) else None
        rows.append(row)
        if not 0 <= phi <= 1:
            flag('error', 'particle_density_conflict', 'Matrix density is incompatible with model particle density.', layer)
        for name in ('FC', 'WP'):
            value = data[name][i]
            if np.isfinite(value) and value >= 0 and value > phi+1.e-6:
                flag('error', 'matrix_water_capacity', f'{name} exceeds model matrix porosity.', layer)
        if rock != 0:
            flag('warning', 'rock_volume_basis_unverified', 'Source-volume reconciliation with nonzero ROCK needs a separate rock/macropore geometry review.', layer)
            continue
        top = 0 if i == 0 else data['CDPTH'][i-1]
        original = source_average(source, 'BKDSI', top, data['CDPTH'][i])
        if density_basis == 'fine_earth_bulk' and original is not None:
            row['source_bulk_density'] = original
            row['source_total_porosity'] = float(1-original/rho)
            row['mass_preserving_matrix_density'] = original/(1-f)
            row['mass_preserving_matrix_porosity'] = float(1-original/((1-f)*rho))
            if includes_macro is not False and not np.isclose(bd*(1-f), original, rtol=1.e-4, atol=1.e-4):
                flag('error' if includes_macro is True else 'warning', 'density_volume_basis_conflict',
                     'Grid mass differs from the source-density total-volume scenario; a confirmed conflict requires documented matching macropore coverage.', layer)
            for name in ('FC', 'WP'):
                value = source_average(source, name, top, data['CDPTH'][i])
                if value is None:
                    continue
                water_basis = basis.get(name, 'fine_earth_bulk' if inferred else 'unknown')
                if water_basis != 'fine_earth_bulk':
                    continue
                row['source_'+name] = value
                # Conditional test, not an assumption that macropores must be dry.
                row[name+'_matrix_if_macropores_drained'] = value/(1-f)
                if includes_macro is False:
                    continue  # Source matrix density does not define total-volume pore capacity.
                if value+f > 1-original/rho+1.e-6:
                    flag('error' if occupancy == 'drained' and includes_macro is True else 'warning',
                         'source_water_capacity_drained_macropores',
                         f'Source {name} plus FHOL exceeds source total porosity under the drained-macropore assumption.', layer)
                if occupancy == 'drained' and includes_macro is True and not np.isclose(data[name][i]*(1-f), value, rtol=1.e-4, atol=1.e-4):
                    flag('error', 'water_volume_basis_conflict', f'Grid {name} does not preserve source bulk-volume water under declared drained macropores.', layer)
        elif f > 0:
            flag('warning', 'density_volume_basis_unverified', 'Independent bulk-volume density or an applicable source basis is unavailable.', layer)
        if f > 0 and occupancy == 'unknown':
            flag('warning', 'retention_volume_basis_unverified', 'Macropore water occupancy at FC/WP is undeclared; volume conversion and cold-start water require review.', layer)
    return dict(constants_source=str(CONSTANTS),
                scope='Selected supplied layers; current StartsMod mixture and mass equations. No input conversion. Source reconciliation supports ROCK=0.',
                layers=rows, findings=findings)
