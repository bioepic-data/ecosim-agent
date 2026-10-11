#!/usr/bin/env python3
"""
Create EcoSIM grid forcing NetCDF file for AmeriFlux sites.

This script combines two skills:
1. ameriflux_site_info: Extracts site metadata (location, climate, vegetation)
2. ameriflux_surgo_grid_extract: Extracts soil profile data from gSSURGO

The resulting NetCDF file follows the Blodget_grid template structure.
"""

import json
import os
import sys
import shutil
import subprocess
import netCDF4 as nc
import numpy as np
from pathlib import Path

SOIL_FILL_VALUE = -999.9
SKILLS_DIR = Path(__file__).resolve().parents[1] / ".agents" / "skills"

# EcoSIM estimates these when the input is negative (SoilHydroParaMod
# ComputeSoilHydroPars for FC/WP/Ksat; readimod for CEC), so missing layers
# are written as -1 instead of an invented value.
MODEL_ESTIMATED_IF_NEGATIVE = ('FC', 'WP', 'SCNV', 'SCNH', 'CEC')
# EcoSIM uses these as read (readimod readTopoNC); a fill value corrupts the run,
# e.g. CORGR*1000 becomes a negative POC pool and FHOL rescales Ksat.
REQUIRED_SOIL_RANGES = {
    'CDPTH': (0.0, None),
    'BKDSI': (0.1, 2.65),
    'CSAND': (0.0, 1000.0),
    'CSILT': (0.0, 1000.0),
    'ROCK': (0.0, 1.0),
    'PH': (0.0, 14.0),
    'CORGC': (0.0, None),
    'FHOL': (0.0, 1.0),
    'AEC': (0.0, None),
    'CORGR': (0.0, None),
}
ORGANIC_CORGC_THRESHOLD = 120.0  # kg C/Mg (12% OC, USDA organic soil material)
ORGANIC_BULK_DENSITY = 0.15      # Mg m-3, forest-floor organic horizon
# Defaults for variables gSSURGO cannot provide, following the Blodgett example grid.
DEFAULT_FHOL = 0.15              # macropore fraction above DEFAULT_FHOL_DEPTH
DEFAULT_FHOL_DEPTH = 0.38        # m
DEFAULT_AEC = 1.0                # cmol kg-1
DEFAULT_CORGR_FRACTION = 0.015   # POC as a fraction of CORGC


def in_range(varname, value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(value) or value <= SOIL_FILL_VALUE + 1.0:
        return False
    lower, upper = REQUIRED_SOIL_RANGES.get(varname, (None, None))
    if lower is not None and value < lower:
        return False
    if upper is not None and value > upper:
        return False
    return True


def nearest_valid_index(valid, index):
    """Nearest layer with a valid value; ties go to the deeper layer."""
    candidates = [i for i, ok in enumerate(valid) if ok]
    if not candidates:
        return None
    return min(candidates, key=lambda i: (abs(i - index), -i))


def gap_fill_soil_profile(interp, nlevs):
    """Fill missing layer values so EcoSIM never reads the -999.9 fill value.

    Returns a filled copy of the interpolated profile and a list of notes
    describing every substitution.
    """
    filled = {key: list(values) for key, values in interp.items()}
    notes = []
    depths = filled.get('CDPTH') or []
    nlayers = min(len(depths), nlevs)
    if nlayers == 0 or not all(in_range('CDPTH', d) for d in depths[:nlayers]):
        raise ValueError("Soil profile has missing layer depths (CDPTH); cannot build grid.")

    corgc = [float(v) if in_range('CORGC', v) else None for v in filled.get('CORGC', [])[:nlayers]]
    organic = [c is not None and c >= ORGANIC_CORGC_THRESHOLD for c in corgc]

    for varname in ('BKDSI', 'ROCK', 'CSAND', 'CSILT', 'PH', 'CORGC'):
        values = (filled.get(varname) or [SOIL_FILL_VALUE] * nlayers)[:nlayers]
        values += [SOIL_FILL_VALUE] * (nlayers - len(values))
        valid = [in_range(varname, v) for v in values]
        for i in range(nlayers):
            if valid[i]:
                continue
            if organic[i] and varname == 'BKDSI':
                values[i] = ORGANIC_BULK_DENSITY
                notes.append(f"{varname} layer {i + 1}: organic horizon default {ORGANIC_BULK_DENSITY}")
            elif organic[i] and varname == 'ROCK':
                values[i] = 0.0
                notes.append(f"{varname} layer {i + 1}: organic horizon, set to 0")
            else:
                source = nearest_valid_index(valid, i)
                if source is None:
                    raise ValueError(f"No valid {varname} value in any soil layer; cannot gap-fill.")
                values[i] = values[source]
                notes.append(f"{varname} layer {i + 1}: copied from layer {source + 1}")
        filled[varname] = values

    for varname in MODEL_ESTIMATED_IF_NEGATIVE:
        values = (filled.get(varname) or [])[:nlayers]
        values += [SOIL_FILL_VALUE] * (nlayers - len(values))
        for i, value in enumerate(values):
            try:
                ok = np.isfinite(float(value)) and float(value) >= 0.0
            except (TypeError, ValueError):
                ok = False
            if not ok:
                values[i] = -1.0
                notes.append(f"{varname} layer {i + 1}: set to -1 for EcoSIM to estimate")
        filled[varname] = values

    filled['FHOL'] = [DEFAULT_FHOL if d <= DEFAULT_FHOL_DEPTH + 1.0e-6 else 0.0 for d in depths[:nlayers]]
    filled['AEC'] = [DEFAULT_AEC] * nlayers
    filled['CORGR'] = [DEFAULT_CORGR_FRACTION * c for c in filled['CORGC']]
    notes.append(
        f"FHOL={DEFAULT_FHOL} to {DEFAULT_FHOL_DEPTH} m then 0, AEC={DEFAULT_AEC}, "
        f"CORGR={DEFAULT_CORGR_FRACTION}*CORGC (template defaults; not in gSSURGO)"
    )
    return filled, notes


def validate_grid_layers(output_file):
    """Return problems in the soil layers EcoSIM reads (1..NJ+NL2)."""
    problems = []
    with nc.Dataset(output_file) as ds:
        nread = int(np.ravel(ds['NJ'][:])[0]) + int(np.ravel(ds['NL2'][:])[0])
        for varname in REQUIRED_SOIL_RANGES:
            if varname not in ds.variables:
                problems.append(f"{varname}: variable missing")
                continue
            values = np.ma.filled(ds[varname][:].astype(float), SOIL_FILL_VALUE).reshape(-1)[:nread]
            bad = [i + 1 for i, v in enumerate(values) if not in_range(varname, v)]
            if bad:
                problems.append(f"{varname}: invalid in layers {bad}")
        for varname in MODEL_ESTIMATED_IF_NEGATIVE:
            if varname in ds.variables:
                values = np.ma.filled(ds[varname][:].astype(float), SOIL_FILL_VALUE).reshape(-1)[:nread]
                bad = [i + 1 for i, v in enumerate(values) if not np.isfinite(v)]
                if bad:
                    problems.append(f"{varname}: non-finite in layers {bad}")
    return problems


def classify_series(values, missing_markers=None):
    markers = set([SOIL_FILL_VALUE, -1])
    if missing_markers:
        markers.update(missing_markers)
    vals = list(values) if values is not None else []
    for value in vals:
        if value is None:
            continue
        try:
            fval = float(value)
        except Exception:
            return True
        if np.isfinite(fval) and fval not in markers:
            return True
    return False

def load_site_data(site_id, result_dir="result"):
    """Load previously extracted site data or run extraction if missing."""
    preferred_file = os.path.join(result_dir, f"{site_id}_ecosim_site.json")
    candidate_files = [
        preferred_file,
        os.path.join(result_dir, "input", f"{site_id}_ecosim_site.json"),
        os.path.join("result", f"{site_id}_ecosim_site.json"),
    ]

    for json_file in candidate_files:
        if os.path.exists(json_file):
            with open(json_file, 'r') as f:
                data = json.load(f)
            if json_file != preferred_file:
                os.makedirs(result_dir, exist_ok=True)
                shutil.copy2(json_file, preferred_file)
            print(f"Loaded existing site data from {json_file}")
            return data

    # If file doesn't exist, try to extract it
    script_path = str(SKILLS_DIR / "ameriflux-site-info" / "extract_ameriflux_site_data.py")
    print(f"Extracting site information for {site_id}...")
    result = subprocess.run([
        sys.executable, script_path, site_id, result_dir
    ], capture_output=True, text=True, cwd=os.getcwd())

    if result.returncode != 0:
        print(f"Error extracting site info: {result.stderr}")
        return None

    if not os.path.exists(preferred_file):
        print(f"Site info JSON file not found: {preferred_file}")
        return None

    with open(preferred_file, 'r') as f:
        site_data = json.load(f)

    print(f"Successfully extracted site data for {site_id}")
    return site_data

def load_soil_data(lon, lat, template_path, site_id, result_dir="result"):
    """Load previously extracted soil data or run extraction if missing."""
    output_json = os.path.join(result_dir, f"profile_{site_id}.json")

    candidate_files = [
        output_json,
        os.path.join(result_dir, "input", f"profile_{site_id}.json"),
        os.path.join("result", f"profile_{site_id}.json"),
    ]

    for soil_file in candidate_files:
        if os.path.exists(soil_file):
            with open(soil_file, 'r') as f:
                data = json.load(f)
            if soil_file != output_json:
                os.makedirs(result_dir, exist_ok=True)
                shutil.copy2(soil_file, output_json)
            print(f"Loaded existing soil profile from {soil_file}")
            return data

    # If file doesn't exist, try to extract it
    script_path = str(SKILLS_DIR / "ameriflux-surgo-grid-extract" / "extract_gssurgo_profile.py")
    gdb_path = "data/gSSURGO_CONUS.gdb"

    print(f"Extracting soil profile for lon={lon}, lat={lat}...")

    cmd = [
        sys.executable, script_path,
        "--gdb", gdb_path,
        "--lon", str(lon),
        "--lat", str(lat),
        "--template", template_path,
        "--out", output_json,
        "--extend-last"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd(), timeout=120)

    if result.returncode != 0:
        print(f"Warning: Soil profile extraction failed: {result.stderr}")
        return None

    if not os.path.exists(output_json):
        print(f"Soil profile JSON file not found: {output_json}")
        return None

    with open(output_json, 'r') as f:
        soil_data = json.load(f)

    print(f"Successfully extracted soil profile for {site_id}")
    return soil_data

def create_grid_netcdf(site_data, soil_data, template_path, output_file):
    """Create NetCDF file following the template structure."""

    print(f"Creating NetCDF file: {output_file}")

    # Read template to get dimensions and structure
    with open(template_path, 'r') as f:
        template_content = f.read()

    # Extract dimensions from template
    nlevs = 20  # From template
    ngrid = 1
    nrow = 1
    ncol = 1
    ntopou = 1

    report = {
        "soil_source": "gssurgo" if (soil_data and 'interpolated' in soil_data) else "template_defaults",
        "underived_or_fill_only_variables": [],
        "notes": [],
    }

    # Create NetCDF file
    ds = nc.Dataset(output_file, 'w', format='NETCDF4')

    # Define dimensions
    ds.createDimension('ncol', ncol)
    ds.createDimension('nrow', nrow)
    ds.createDimension('ngrid', ngrid)
    ds.createDimension('ntopou', ntopou)
    ds.createDimension('nlevs', nlevs)

    # Grid-level variables from site_data
    alatg = ds.createVariable('ALATG', 'f4', ('ngrid',))
    alatg.long_name = "Latitude"
    alatg.units = "degrees north"
    alatg[:] = site_data['ALATG']

    altig = ds.createVariable('ALTIG', 'f4', ('ngrid',))
    altig.long_name = "Altitude above sea-level"
    altig.units = "m"
    altig[:] = site_data['ALTIG']

    atcag = ds.createVariable('ATCAG', 'f4', ('ngrid',))
    atcag.long_name = "Mean annual temperature"
    atcag.units = "oC"
    atcag[:] = site_data['ATCAG']

    # Water table and climate variables (using defaults or site data)
    idtblg = ds.createVariable('IDTBLG', 'b', ('ngrid',))
    idtblg.long_name = "Water table flag"
    idtblg.units = "none"
    idtblg.flags = "0=No water table,1=Natural stationary water table,2=Natural mobile water table,3=Artificial stationary water table,4=Artificial mobile water table"
    idtblg[:] = 1  # Default: Natural stationary water table

    ietypg = ds.createVariable('IETYPG', 'b', ('ngrid',))
    ietypg.long_name = "Koppen climate zone"
    ietypg.units = "none"
    ietypg[:] = site_data['IETYPG']

    # Water table depths (defaults)
    dtblig = ds.createVariable('DTBLIG', 'f4', ('ngrid',))
    dtblig.long_name = "Depth of natural water table"
    dtblig.units = "m"
    dtblig[:] = 20.0

    dtbldig = ds.createVariable('DTBLDIG', 'f4', ('ngrid',))
    dtbldig.long_name = "Depth of artificial water table"
    dtbldig.units = "m"
    dtbldig[:] = 100.0

    dtblgg = ds.createVariable('DTBLGG', 'f4', ('ngrid',))
    dtblgg.long_name = "Slope of natural water table relative to landscape surface"
    dtblgg.units = "none"
    dtblgg[:] = 0.0

    # Boundary condition variables (defaults)
    rchqng = ds.createVariable('RCHQNG', 'f4', ('ngrid',))
    rchqng.long_name = "Boundary condition for North surface runoff"
    rchqng.units = "none"
    rchqng.flags = "varying between 0 and 1"
    rchqng[:] = 0.0

    rchqeg = ds.createVariable('RCHQEG', 'f4', ('ngrid',))
    rchqeg.long_name = "Boundary condition for East surface runoff"
    rchqeg.units = "none"
    rchqeg.flags = "varying between 0 and 1"
    rchqeg[:] = 1.0

    rchqsg = ds.createVariable('RCHQSG', 'f4', ('ngrid',))
    rchqsg.long_name = "Boundary condition for S surface runoff"
    rchqsg.units = "none"
    rchqsg.flags = "varying between 0 and 1"
    rchqsg[:] = 0.0

    rchqwg = ds.createVariable('RCHQWG', 'f4', ('ngrid',))
    rchqwg.long_name = "Boundary condition for W surface runoff"
    rchqwg.units = "none"
    rchqwg.flags = "varying between 0 and 1"
    rchqwg[:] = 0.0

    # Subsurface flow boundary conditions
    rchgnug = ds.createVariable('RCHGNUG', 'f4', ('ngrid',))
    rchgnug.long_name = "Bound condition for N subsurf flow"
    rchgnug.units = "none"
    rchgnug[:] = 0.0

    rchgeug = ds.createVariable('RCHGEUG', 'f4', ('ngrid',))
    rchgeug.long_name = "Bound condition for E subsurf flow"
    rchgeug.units = "none"
    rchgeug[:] = 1.0

    rchgsug = ds.createVariable('RCHGSUG', 'f4', ('ngrid',))
    rchgsug.long_name = "Bound condition for S subsurf flow"
    rchgsug.units = "none"
    rchgsug[:] = 0.0

    rchgwug = ds.createVariable('RCHGWUG', 'f4', ('ngrid',))
    rchgwug.long_name = "Bound condition for W subsurf flow"
    rchgwug.units = "none"
    rchgwug[:] = 0.0

    # Water table edge distances
    rchgntg = ds.createVariable('RCHGNTG', 'f4', ('ngrid',))
    rchgntg.long_name = "North edge distance to water table"
    rchgntg.units = "m"
    rchgntg[:] = 0.0

    rchgetg = ds.createVariable('RCHGETG', 'f4', ('ngrid',))
    rchgetg.long_name = "East edge distance to water table"
    rchgetg.units = "m"
    rchgetg[:] = 0.0

    rchgstg = ds.createVariable('RCHGSTG', 'f4', ('ngrid',))
    rchgstg.long_name = "South edge distance to water table"
    rchgstg.units = "m"
    rchgstg[:] = 0.0

    rchgwtg = ds.createVariable('RCHGWTG', 'f4', ('ngrid',))
    rchgwtg.long_name = "West edge distance to water table"
    rchgwtg.units = "m"
    rchgwtg[:] = 0.0

    rchgdg = ds.createVariable('RCHGDG', 'f4', ('ngrid',))
    rchgdg.long_name = "Lower boundary conditions for water flow"
    rchgdg.units = "none"
    rchgdg.flags = "varying between 0 and 1"
    rchgdg[:] = 0.0

    # Grid size variables
    dhi = ds.createVariable('DHI', 'f4', ('ngrid', 'ncol'))
    dhi.long_name = "grid size in the W-E direction"
    dhi.units = "m"
    dhi[:] = [[1.0]]

    dvi = ds.createVariable('DVI', 'f4', ('ngrid', 'nrow'))
    dvi.long_name = "grid size in the N-S direction"
    dvi.units = "m"
    dvi[:] = [[1.0]]

    # Topographic unit variables
    topo_grid = ds.createVariable('topo_grid', 'i4', ('ntopou',))
    topo_grid.long_name = "grid ID of the topo unit"
    topo_grid.units = "none"
    topo_grid[:] = [1]

    # Grid boundaries (defaults)
    nh1 = ds.createVariable('NH1', 'b', ('ntopou',))
    nh1.long_name = "Starting column from the west"
    nh1.units = "none"
    nh1[:] = [1]

    nh2 = ds.createVariable('NH2', 'b', ('ntopou',))
    nh2.long_name = "Ending column at the east"
    nh2.units = "none"
    nh2[:] = [1]

    nv1 = ds.createVariable('NV1', 'b', ('ntopou',))
    nv1.long_name = "Starting row from the north"
    nv1.units = "none"
    nv1[:] = [1]

    nv2 = ds.createVariable('NV2', 'b', ('ntopou',))
    nv2.long_name = "Ending row at the south"
    nv2.units = "none"
    nv2[:] = [1]

    # Topography (defaults - could be enhanced with DEM data)
    aspx = ds.createVariable('ASPX', 'f4', ('ntopou',))
    aspx.long_name = "Aspect angle in compass heading convention"
    aspx.units = "degrees clockwise from north"
    aspx[:] = [90.0]

    sl0 = ds.createVariable('SL0', 'f4', ('ntopou',))
    sl0.long_name = "Slope"
    sl0.units = "degrees"
    sl0[:] = [10.0]

    # Snow and surface properties (defaults)
    dpthsx = ds.createVariable('DPTHSX', 'f4', ('ntopou',))
    dpthsx.long_name = "Initial snowpack depth"
    dpthsx.units = "m"
    dpthsx[:] = [0.0]

    psifc = ds.createVariable('PSIFC', 'f4', ('ntopou',))
    psifc.long_name = "Water potential at field capacity"
    psifc.units = "MPa"
    psifc[:] = [-0.01]

    psiwp = ds.createVariable('PSIWP', 'f4', ('ntopou',))
    psiwp.long_name = "Water potential at wilting point"
    psiwp.units = "MPa"
    psiwp[:] = [-1.5]

    albs = ds.createVariable('ALBS', 'f4', ('ntopou',))
    albs.long_name = "Wet soil albedo"
    albs.units = "none"
    albs[:] = [0.15]

    ph0 = ds.createVariable('PH0', 'f4', ('ntopou',))
    ph0.long_name = "Litter pH"
    ph0.units = "none"
    ph0[:] = [5.0]

    # Surface litter pools (defaults)
    rscf = ds.createVariable('RSCf', 'f4', ('ntopou',))
    rscf.long_name = "C in surface fine litter"
    rscf.units = "gC m-2"
    rscf[:] = [210.0]

    rsnf = ds.createVariable('RSNf', 'f4', ('ntopou',))
    rsnf.long_name = "N in surface fine litter"
    rsnf.units = "gN m-2"
    rsnf[:] = [7.0]

    rspf = ds.createVariable('RSPf', 'f4', ('ntopou',))
    rspf.long_name = "P in surface fine litter"
    rspf.units = "gP m-2"
    rspf[:] = [0.7]

    rscw = ds.createVariable('RSCw', 'f4', ('ntopou',))
    rscw.long_name = "C in surface woody litter"
    rscw.units = "gC m-2"
    rscw[:] = [0.0]

    rsnw = ds.createVariable('RSNw', 'f4', ('ntopou',))
    rsnw.long_name = "N in surface woody litter"
    rsnw.units = "gN m-2"
    rsnw[:] = [0.0]

    rspw = ds.createVariable('RSPw', 'f4', ('ntopou',))
    rspw.long_name = "P in surface woody litter"
    rspw.units = "gP m-2"
    rspw[:] = [0.0]

    rscm = ds.createVariable('RSCm', 'f4', ('ntopou',))
    rscm.long_name = "C in manure"
    rscm.units = "gC m-2"
    rscm[:] = [0.0]

    rsnm = ds.createVariable('RSNm', 'f4', ('ntopou',))
    rsnm.long_name = "N in manure"
    rsnm.units = "gN m-2"
    rsnm[:] = [0.0]

    rspm = ds.createVariable('RSPm', 'f4', ('ntopou',))
    rspm.long_name = "P in manure"
    rspm.units = "gP m-2"
    rspm[:] = [0.0]

    # Vegetation and litter type flags
    ixtyp1 = ds.createVariable('IXTYP1', 'b', ('ntopou',))
    ixtyp1.long_name = "plant surface fine litter type"
    ixtyp1.units = "none"
    ixtyp1.flags = "1=maize,2=wheat,3=soybean,4=new straw,5=old straw,6=compost,7=green manure,8=new deciduos forest,9=new coniferous forest,10=old deciduous forest,11=old coniferous forest,12=default"
    ixtyp1[:] = [site_data['IXTYP1']]

    ixtyp2 = ds.createVariable('IXTYP2', 'b', ('ntopou',))
    ixtyp2.long_name = "manure surface litter type"
    ixtyp2.units = "none"
    ixtyp2.flags = "1=ruminant,2=non ruminant,3=others"
    ixtyp2[:] = [0]

    # Soil layer indices
    nui = ds.createVariable('NUI', 'b', ('ntopou',))
    nui.long_name = "Initial layer number of soil surface layer"
    nui.units = "none"
    nui.flags = "usually is 1"
    nui[:] = [1]

    nj = ds.createVariable('NJ', 'b', ('ntopou',))
    nj.long_name = "Layer number of maximum rooting layer"
    nj.units = "none"
    nj[:] = [15]

    nl1 = ds.createVariable('NL1', 'b', ('ntopou',))
    nl1.long_name = "Number of additional layers below NJ with data in file"
    nl1.units = "none"
    nl1[:] = [0]

    nl2 = ds.createVariable('NL2', 'b', ('ntopou',))
    nl2.long_name = "Number of additional layers below NJ without data in file"
    nl2.units = "none"
    nl2[:] = [1]

    isoilr = ds.createVariable('ISOILR', 'b', ('ntopou',))
    isoilr.long_name = "Flag for soil profile type"
    isoilr.units = "none"
    isoilr.flags = "0=natural,1=reconstructed"
    isoilr[:] = [0]

    # Soil profile variables from gSSURGO data
    if soil_data and 'interpolated' in soil_data:
        interp, gap_notes = gap_fill_soil_profile(soil_data['interpolated'], nlevs)
        report["gap_filled"] = gap_notes

        def fit_levels(values, fill_value):
            vals = list(values) if values is not None else []
            if len(vals) >= nlevs:
                return vals[:nlevs]
            return vals + [fill_value] * (nlevs - len(vals))

        # Depth to bottom of layer
        cdpth = ds.createVariable('CDPTH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        cdpth.long_name = "Depth to bottom of soil layer"
        cdpth.units = "m"
        cdpth[:] = [fit_levels(interp.get('CDPTH'), -999.9)]

        # Bulk density
        bkdsi = ds.createVariable('BKDSI', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        bkdsi.long_name = "Initial bulk density"
        bkdsi.units = "Mg m-3"
        bkdsi.flags = "0 for water"
        bkdsi[:] = [fit_levels(interp.get('BKDSI'), -999.9)]

        # Field capacity
        fc = ds.createVariable('FC', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        fc.long_name = "Field capacity"
        fc.units = "m3 m-3"
        fc[:] = [fit_levels(interp.get('FC'), -999.9)]

        # Wilting point
        wp = ds.createVariable('WP', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        wp.long_name = "Wilting point"
        wp.units = "m3 m-3"
        wp[:] = [fit_levels(interp.get('WP'), -999.9)]

        # Hydraulic conductivity
        scnv = ds.createVariable('SCNV', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        scnv.long_name = "Vertical hydraulic conductivity Ksat"
        scnv.units = "mm h-1"
        scnv[:] = [fit_levels(interp.get('SCNV'), -999.9)]

        scnh = ds.createVariable('SCNH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        scnh.long_name = "Lateral hydraulic conductivity Ksat"
        scnh.units = "mm h-1"
        scnh[:] = [fit_levels(interp.get('SCNH'), -999.9)]

        # Texture
        csand = ds.createVariable('CSAND', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        csand.long_name = "Sand content"
        csand.units = "kg Mg-1"
        csand[:] = [fit_levels(interp.get('CSAND'), -999.9)]

        csilt = ds.createVariable('CSILT', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        csilt.long_name = "Silt content"
        csilt.units = "kg Mg-1"
        csilt[:] = [fit_levels(interp.get('CSILT'), -999.9)]

        # Macroporosity
        fhol = ds.createVariable('FHOL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        fhol.long_name = "Macropore fraction in the non-rock fraction of soil"
        fhol.units = "none"
        fhol.flags = "0-1"
        fhol[:] = [fit_levels(interp.get('FHOL'), -999.9)]

        # Rock fraction
        rock = ds.createVariable('ROCK', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        rock.long_name = "Rock fraction of the whole soil"
        rock.units = "none"
        rock.flags = "0-1"
        rock[:] = [fit_levels(interp.get('ROCK'), -999.9)]

        # Soil chemistry
        ph = ds.createVariable('PH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        ph.long_name = "depth-resolved pH"
        ph.units = "none"
        ph[:] = [fit_levels(interp.get('PH'), -999.9)]

        cec = ds.createVariable('CEC', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        cec.long_name = "Cation exchange capacity"
        cec.units = "cmol kg soil-1"
        cec[:] = [fit_levels(interp.get('CEC'), -999.9)]

        aec = ds.createVariable('AEC', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        aec.long_name = "Anion exchange capacity"
        aec.units = "cmol kg soil-1"
        aec[:] = [fit_levels(interp.get('AEC'), -999.9)]

        corgc = ds.createVariable('CORGC', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        corgc.long_name = "Total soil organic carbon"
        corgc.units = "kg C/Mg soil"
        corgc[:] = [fit_levels(interp.get('CORGC'), -999.9)]

        corgr = ds.createVariable('CORGR', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        corgr.long_name = "POC (part of SOC)"
        corgr.units = "kg C/Mg soil"
        corgr[:] = [fit_levels(interp.get('CORGR'), -999.9)]

        for var_name in ['CDPTH', 'BKDSI', 'FC', 'WP', 'SCNV', 'SCNH', 'CSAND', 'CSILT', 'FHOL', 'ROCK', 'PH', 'CEC', 'CORGC']:
            if not classify_series(interp.get(var_name)):
                report["underived_or_fill_only_variables"].append(var_name)
        report["underived_or_fill_only_variables"].extend(['AEC', 'CORGR', 'FHOL'])

    else:
        print("Warning: No soil data available, using template defaults")
        report["notes"].append("gSSURGO extraction failed or returned no interpolated profile; template defaults were used.")
        report["underived_or_fill_only_variables"].extend(['CDPTH', 'BKDSI', 'FC', 'WP', 'SCNV', 'SCNH', 'CSAND', 'CSILT', 'FHOL', 'ROCK', 'PH', 'CEC', 'CORGC', 'AEC', 'CORGR'])
        # Use template defaults if soil extraction failed
        cdpth = ds.createVariable('CDPTH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        cdpth.long_name = "Depth to bottom of soil layer"
        cdpth.units = "m"
        cdpth[:] = [[0.01, 0.04, 0.07, 0.14, 0.25, 0.38, 0.51, 0.85, 1.2, 1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0]]

        bkdsi = ds.createVariable('BKDSI', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        bkdsi.long_name = "Initial bulk density"
        bkdsi.units = "Mg m-3"
        bkdsi[:] = [[0.5, 0.5, 0.6, 0.7, 0.98, 1.1, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 1.4, 0]]

        # Set other soil variables to -1 (no data) or template defaults
        for var_name in ['FC', 'WP', 'SCNV', 'SCNH', 'CSAND', 'CSILT', 'FHOL', 'ROCK', 'PH', 'CEC', 'CORGC']:
            var = ds.createVariable(var_name, 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
            if var_name in ['FC', 'WP', 'SCNV', 'SCNH']:
                var[:] = [[-1] * nlevs]
            elif var_name == 'FHOL':
                var[:] = [[0.15, 0.15, 0.15, 0.15, 0.15, 0.15, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]
            elif var_name == 'ROCK':
                var[:] = [[0] * nlevs]
            elif var_name == 'PH':
                var[:] = [[4.6, 4.6, 4.6, 4.6, 4.6, 4.6, 4.8, 4.8, 4.8, 4.8, 4.8, 4.35, 4.35, 4.35, 4.35, 0, 0, 0, 0, 0]]
            elif var_name == 'CEC':
                var[:] = [[4.6, 4.6, 4.6, 4.6, 4.6, 4.6, 4.8, 4.8, 4.8, 4.8, 4.8, 4.35, 4.35, 4.35, 4.35, 0, 0, 0, 0, 0]]
            elif var_name == 'CSAND':
                var[:] = [[390, 390, 390, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 234, 0]]
            elif var_name == 'CSILT':
                var[:] = [[200, 200, 200, 200, 200, 200, 300, 300, 300, 300, 300, 300, 300, 300, 300, 300, 300, 300, 300, 0]]
            elif var_name == 'CORGC':
                var[:] = [[150, 200, 140, 100, 40, 20, 10, 7, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 0]]

        aec = ds.createVariable('AEC', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        aec.long_name = "Anion exchange capacity"
        aec.units = "cmol kg soil-1"
        aec[:] = [[DEFAULT_AEC] * nlevs]

        corgr = ds.createVariable('CORGR', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
        corgr.long_name = "POC (part of SOC)"
        corgr.units = "kg C/Mg soil"
        corgr[:] = [[DEFAULT_CORGR_FRACTION * c for c in [150, 200, 140, 100, 40, 20, 10, 7, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 0]]]

    # Continue with remaining variables that have defaults or are not extracted
    # Nutrient pools (defaults - not extracted from gSSURGO)
    corgn = ds.createVariable('CORGN', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    corgn.long_name = "Total soil organic nitrogen"
    corgn.units = "g N/Mg soil"
    corgn[:] = [[-1] * nlevs]

    corgp = ds.createVariable('CORGP', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    corgp.long_name = "Total soil organic phosphorus"
    corgp.units = "g P/Mg soil"
    corgp[:] = [[-1] * nlevs]

    cnh4 = ds.createVariable('CNH4', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cnh4.long_name = "Total soil NH4 concentration"
    cnh4.units = "gN/Mg soil"
    cnh4[:] = [[1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0]]

    cno3 = ds.createVariable('CNO3', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cno3.long_name = "Total soil NO3 concentration"
    cno3.units = "gN/Mg soil"
    cno3[:] = [[10, 10, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]

    cpo4 = ds.createVariable('CPO4', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cpo4.long_name = "Total soil H2PO4 concentration"
    cpo4.units = "gP/Mg soil"
    cpo4[:] = [[5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 0, 0, 0]]

    # Exchangeable cations (defaults)
    cal = ds.createVariable('CAL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cal.long_name = "Soluble soil Al content"
    cal.units = "g Al/Mg soil"
    cal[:] = [[-1] * nlevs]

    cfe = ds.createVariable('CFE', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cfe.long_name = "Soluble soil Fe content"
    cfe.units = "g Fe/Mg soil"
    cfe[:] = [[-1] * nlevs]

    cca = ds.createVariable('CCA', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cca.long_name = "Soluble soil Ca content"
    cca.units = "g Ca/Mg soil"
    cca[:] = [[40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 40, 0, 0, 0]]

    cmg = ds.createVariable('CMG', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cmg.long_name = "Soluble soil MG content"
    cmg.units = "g MG/Mg soil"
    cmg[:] = [[15.5, 15.5, 15.5, 48, 48, 48, 48, 48, 48, 48, 48, 48, 48, 48, 48, 48, 0, 0, 0, 0]]

    cna = ds.createVariable('CNA', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cna.long_name = "Soluble soil Na content"
    cna.units = "g Na/Mg soil"
    cna[:] = [[10.4, 10.4, 10.4, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0, 0, 0]]

    cka = ds.createVariable('CKA', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cka.long_name = "Soluble soil K content"
    cka.units = "g K/Mg soil"
    cka[:] = [[33.6, 33.6, 33.6, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 39, 0, 0, 0, 0]]

    cso4 = ds.createVariable('CSO4', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cso4.long_name = "Soluble soil SO4 content"
    cso4.units = "g S/Mg soil"
    cso4[:] = [[9.3, 9.3, 9.3, 48, 48, 48, 48, 48, 48, 48, 48, 48, 24, 24, 24, 24, 24, 0, 0, 0]]

    ccl = ds.createVariable('CCL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    ccl.long_name = "Soluble soil Cl content"
    ccl.units = "g Cl/Mg soil"
    ccl[:] = [[7.91, 7.91, 7.91, 7.1, 7.1, 7.1, 7.1, 7.1, 7.1, 7.1, 7.1, 7.1, 0, 0, 0, 0, 0, 0, 0, 0]]

    # Phosphate minerals (defaults)
    calpo = ds.createVariable('CALPO', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    calpo.long_name = "Soil AlPO4 content"
    calpo.units = "g P/Mg soil"
    calpo[:] = [[100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0]]

    cfepo = ds.createVariable('CFEPO', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cfepo.long_name = "Soil FePO4 content"
    cfepo.units = "g P/Mg soil"
    cfepo[:] = [[0] * nlevs]

    ccapd = ds.createVariable('CCAPD', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    ccapd.long_name = "Soil CaHPO4 content"
    ccapd.units = "g P/Mg soil"
    ccapd[:] = [[0] * nlevs]

    ccaph = ds.createVariable('CCAPH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    ccaph.long_name = "Soil apatite content"
    ccaph.units = "g P/Mg soil"
    ccaph[:] = [[100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0]]

    # Hydroxides (defaults)
    caloh = ds.createVariable('CALOH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    caloh.long_name = "Soil Al(OH)3 content"
    caloh.units = "g Al/Mg soil"
    caloh[:] = [[100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0]]

    cfeoh = ds.createVariable('CFEOH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    cfeoh.long_name = "Soil Fe(OH)3 content"
    cfeoh.units = "g Fe/Mg soil"
    cfeoh[:] = [[0] * nlevs]

    # Carbonates (defaults)
    ccaco = ds.createVariable('CCACO', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    ccaco.long_name = "Soil CaCO3 content"
    ccaco.units = "g Ca/Mg soil"
    ccaco[:] = [[100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0]]

    ccaso = ds.createVariable('CCASO', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    ccaso.long_name = "Soil CaSO4 content"
    ccaso.units = "g Ca/Mg soil"
    ccaso[:] = [[0] * nlevs]

    # Gapon selectivity coefficients (defaults)
    gkc4 = ds.createVariable('GKC4', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    gkc4.long_name = "Ca-NH4 Gapon selectivity coefficient"
    gkc4.units = "none"
    gkc4[:] = [[0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0]]

    gkch = ds.createVariable('GKCH', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    gkch.long_name = "Ca-H Gapon selectivity coefficient"
    gkch.units = "none"
    gkch[:] = [[0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0, 0, 0, 0, 0, 0, 0, 0]]

    gkca = ds.createVariable('GKCA', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    gkca.long_name = "Ca-Al Gapon selectivity coefficient"
    gkca.units = "none"
    gkca[:] = [[0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25, 0, 0, 0, 0]]

    gkcm = ds.createVariable('GKCM', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    gkcm.long_name = "Ca-Mg Gapon selectivity coefficient"
    gkcm.units = "none"
    gkcm[:] = [[0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0, 0, 0, 0]]

    gkcn = ds.createVariable('GKCN', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    gkcn.long_name = "Ca-Na Gapon selectivity coefficient"
    gkcn.units = "none"
    gkcn[:] = [[0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0.16, 0, 0, 0]]

    gkck = ds.createVariable('GKCK', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    gkck.long_name = "Ca-K Gapon selectivity coefficient"
    gkck.units = "none"
    gkck[:] = [[3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0]]

    # Initial water content
    thw = ds.createVariable('THW', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    thw.long_name = "Initial soil water content"
    thw.units = "m3/m3"
    thw[:] = [[1] * nlevs]

    thi = ds.createVariable('THI', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    thi.long_name = "Initial soil ice content"
    thi.units = "m3/m3"
    thi[:] = [[-1] * nlevs]

    # Litter pools by layer (defaults)
    rscfl = ds.createVariable('RSCfL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rscfl.long_name = "Initial fine litter C"
    rscfl.units = "gC m-2"
    rscfl[:] = [[120, 240, 240, 240, 120, 120, 60, 60, 30, 30, 30, 20, 20, 20, 20, 20, 0, 0, 0, 0]]

    rsnfl = ds.createVariable('RSNfL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rsnfl.long_name = "Initial fine litter N"
    rsnfl.units = "gN m-2"
    rsnfl[:] = [[4, 8, 8, 8, 4, 4, 2, 2, 1, 1, 1, 0.5, 0.5, 0.5, 0.5, 0.5, 0, 0, 0, 0]]

    rspfl = ds.createVariable('RSPfL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rspfl.long_name = "Initial fine litter P"
    rspfl.units = "gP m-2"
    rspfl[:] = [[0.5, 1, 1, 1, 0.5, 0.5, 0.25, 0.25, 0.12, 0.12, 0.12, 0.06, 0.06, 0.06, 0.06, 0, 0, 0, 0, 0]]

    # Woody litter (zeros)
    rscwl = ds.createVariable('RSCwL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rscwl.long_name = "Initial woody litter C"
    rscwl.units = "gC m-2"
    rscwl[:] = [[0] * nlevs]

    rsnwl = ds.createVariable('RSNwL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rsnwl.long_name = "Initial woody litter N"
    rsnwl.units = "gN m-2"
    rsnwl[:] = [[0] * nlevs]

    rspwl = ds.createVariable('RSPwL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rspwl.long_name = "Initial woody litter P"
    rspwl.units = "gP m-2"
    rspwl[:] = [[0] * nlevs]

    # Manure litter (zeros)
    rscml = ds.createVariable('RSCmL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rscml.long_name = "Initial manure liter C"
    rscml.units = "gC m-2"
    rscml[:] = [[0] * nlevs]

    rsnml = ds.createVariable('RSNmL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rsnml.long_name = "Initial manure litter N"
    rsnml.units = "gN m-2"
    rsnml[:] = [[0] * nlevs]

    rspml = ds.createVariable('RSPmL', 'f4', ('ntopou', 'nlevs'), fill_value=-999.9)
    rspml.long_name = "Initial manure litter P"
    rspml.units = "gP m-2"
    rspml[:] = [[0] * nlevs]

    # Grid extent variables
    nhw = ds.createVariable('NHW', 'i4', ())
    nhw[:] = 1

    nhe = ds.createVariable('NHE', 'i4', ())
    nhe[:] = 1

    nvn = ds.createVariable('NVN', 'i4', ())
    nvn[:] = 1

    nvs = ds.createVariable('NVS', 'i4', ())
    nvs[:] = 1

    # Global attributes
    ds.description = f"Grid input data created for {site_data['site_name']} on {np.datetime64('today', 'D')}"

    ds.close()
    print(f"Successfully created NetCDF file: {output_file}")
    report["underived_or_fill_only_variables"] = sorted(set(report["underived_or_fill_only_variables"]))
    return report

def main():
    if len(sys.argv) != 2:
        print("Usage: python create_ecosim_grid_forcing.py <SITE_ID>")
        print("Example: python create_ecosim_grid_forcing.py US-Ha1")
        sys.exit(1)

    site_id = sys.argv[1]
    result_root = "result"
    result_dir = os.path.join(result_root, site_id)
    template_path = "templates/Blodget_grid_20251115_modified.nc.template"
    output_file = os.path.join(result_dir, f"{site_id}_ecosim_grid.nc")

    # Ensure result directory exists
    os.makedirs(result_dir, exist_ok=True)

    print(f"Creating EcoSIM grid forcing for site: {site_id}\n")

    # Step 1: Load/extract site information
    site_data = load_site_data(site_id, result_dir)
    if not site_data:
        print("Failed to extract site information")
        sys.exit(1)

    # Step 2: Load/extract soil profile data
    lon = site_data['ALONG']
    lat = site_data['ALATG']
    soil_data = load_soil_data(lon, lat, template_path, site_id, result_dir)

    # Step 3: Create NetCDF file
    report = create_grid_netcdf(site_data, soil_data, template_path, output_file)
    problems = validate_grid_layers(output_file)
    report["layer_validation_problems"] = problems

    report_path = f"{result_dir}/{site_id}_grid_derivation_report.json"
    with open(report_path, 'w') as f:
        json.dump({
            "site_id": site_id,
            "output_file": output_file,
            "report": report
        }, f, indent=2)

    # Print summary
    print(f"\n✓ Grid forcing file created successfully!")
    print(f"  Output: {output_file}")
    print(f"  Site: {site_data['site_name']}")
    print(f"  Location: {lat:.4f}°N, {lon:.4f}°E")
    print(f"  Elevation: {site_data['ALTIG']} m")
    print(f"  MAT: {site_data['ATCAG']:.2f}°C")
    print(f"  Climate zone: {site_data['IETYPG']} (Koppen-Geiger)")
    print(f"  Vegetation type: {site_data['IXTYP1']} (IGBP code)")
    print(f"  Variables not derived from source data: {report['underived_or_fill_only_variables']}")
    print(f"  Derivation report: {report_path}")
    if problems:
        print("ERROR: grid soil layers read by EcoSIM are invalid:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
