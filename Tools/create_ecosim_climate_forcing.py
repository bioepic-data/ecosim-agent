#!/usr/bin/env python3
"""
Create EcoSIM climate forcing NetCDF for an AmeriFlux site.

This script combines multiple skills to generate a complete EcoSIM climate forcing file:
1. Extract site information (lat/lon, etc.) from AmeriFlux
2. Convert ERA5 climate data to EcoSIM format
3. Extract atmospheric chemistry data
4. Combine everything into a single NetCDF file
"""

import os
import sys
import json
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
import pandas as pd
from netCDF4 import Dataset
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Tools.climate_naming import finalize_climate_file_name
from Tools.run_progress import update_progress

# Historical NADP grids in data/nadp_data_grids use mg L-1 for the major
# anions, NH4, Ca, and Cl, but ug L-1 for Mg, Na, and K. Numerically,
# 1 mg L-1 = 1 g m-3 and 1 ug L-1 = 0.001 g m-3.
CHEM_TEMPLATE_VARS = {
    'PHRG': {"units": "pH", "long_name": "pH in precipitation", "source_key": "ph", "source_keys": ["ph", "phlab_mg_l"], "factor": 1.0},
    'CN4RIG': {"units": "gN m^-3", "long_name": "NH4 conc in precip", "source_key": "nh4_mg_l", "factor": 0.7765},
    'CNORIG': {"units": "gN m^-3", "long_name": "NO3 conc in precip", "source_key": "no3_mg_l", "factor": 0.2259},
    'CPORG': {"units": "gP m^-3", "long_name": "H2PO4 conc in precip", "source_key": None, "factor": None},
    'CALRG': {"units": "gAl m^-3", "long_name": "Al conc in precip", "source_key": None, "factor": None},
    'CFERG': {"units": "gFe m^-3", "long_name": "Fe conc in precip", "source_key": None, "factor": None},
    'CCARG': {"units": "gCa m^-3", "long_name": "Ca conc in precip", "source_key": "ca_mg_l", "factor": 1.0},
    'CMGRG': {"units": "gMg m^-3", "long_name": "Mg conc in precip", "source_key": "mg_mg_l", "factor": 1.0 / 1000},
    'CNARG': {"units": "gNa m^-3", "long_name": "Na conc in precip", "source_key": "na_mg_l", "factor": 1.0 / 1000},
    'CKARG': {"units": "gK m^-3", "long_name": "K conc in precip", "source_key": "k_mg_l", "factor": 1.0 / 1000},
    'CSORG': {"units": "gS m^-3", "long_name": "SO4 conc in precip", "source_key": "so4_mg_l", "factor": 0.3338},
    'CCLRG': {"units": "gCl m^-3", "long_name": "Cl conc in precip", "source_key": "cl_mg_l", "factor": 1.0},
}

CHEM_DEFAULT_VALUES = {
    'PHRG': 7.0,
    'CN4RIG': 0.0,
    'CNORIG': 0.0,
    'CPORG': 0.0,
    'CALRG': 0.0,
    'CFERG': 0.0,
    'CCARG': 0.0,
    'CMGRG': 0.0,
    'CNARG': 0.0,
    'CKARG': 0.0,
    'CSORG': 0.0,
    'CCLRG': 0.0,
}

CHEM_VALID_RANGES = {
    'PHRG': (0.0, 14.0),
    'CN4RIG': (0.0, None),
    'CNORIG': (0.0, None),
    'CPORG': (0.0, None),
    'CALRG': (0.0, None),
    'CFERG': (0.0, None),
    'CCARG': (0.0, None),
    'CMGRG': (0.0, None),
    'CNARG': (0.0, None),
    'CKARG': (0.0, None),
    'CSORG': (0.0, None),
    'CCLRG': (0.0, None),
}

def find_skill_script(skill_name, script_name):
    """Find a skill script under the active local skill roots."""
    normalized_name = skill_name.replace("_", "-")
    for root in (".agents/skills", ".claude/skills"):
        for candidate_name in (normalized_name, skill_name):
            candidate = os.path.join(root, candidate_name, script_name)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(".agents/skills", normalized_name, script_name)

def run_site_info(site_id, output_dir="result"):
    """Run the site info extraction."""
    preferred_file = os.path.join(output_dir, f"{site_id}_ecosim_site.json")
    candidate_files = [
        preferred_file,
        os.path.join(output_dir, "input", f"{site_id}_ecosim_site.json"),
        os.path.join("result", f"{site_id}_ecosim_site.json"),
    ]
    for site_file in candidate_files:
        if os.path.exists(site_file):
            with open(site_file, 'r') as f:
                data = json.load(f)
            if site_file != preferred_file:
                os.makedirs(output_dir, exist_ok=True)
                shutil.copy2(site_file, preferred_file)
            return data

    script_path = find_skill_script("ameriflux-site-info", "extract_ameriflux_site_data.py")
    cmd = [sys.executable, script_path, site_id, output_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    if result.returncode != 0:
        print(f"Error running site info: {result.stderr}")
        return None

    if os.path.exists(preferred_file):
        with open(preferred_file, 'r') as f:
            return json.load(f)
    return None

def find_era5_file(site_id, data_dir="data"):
    """Find the ERA5 CSV file for the site."""
    # Look for directories starting with AMF_<site_id>. Local archives nest
    # them under container folders such as data/amf300 or
    # data/AMF300SiteData/Data, so descend into any folder whose name mentions
    # AmeriFlux or data, but not into unrelated large trees (gSSURGO, tDEP).
    for root, dirs, files in os.walk(data_dir, followlinks=True):
        dirs[:] = [
            directory for directory in dirs
            if directory.startswith("AMF_")
            or "amf" in directory.lower()
            or directory.lower() == "data"
        ]
        if not os.path.basename(root).startswith(f"AMF_{site_id}_"):
            continue
        for file in files:
            if ("ERA5_HR" in file or "ERA5_HH" in file) and file.endswith(".csv"):
                return os.path.join(root, file)
    return None

def run_era5_conversion(era5_file, output_file, site_id, quality_report_file=None):
    """Run the ERA5 to EcoSIM conversion."""
    script_path = find_skill_script("ameriflux-era5-to-ecosim", "era5_to_ecosim_converter.py")
    cmd = [sys.executable, script_path, "--input", era5_file, "--output", output_file, "--site-id", site_id]
    if quality_report_file:
        cmd.extend(["--quality-report", quality_report_file])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    if result.returncode != 0:
        print(f"Error running ERA5 conversion: {result.stderr}", file=sys.stderr)
        return False
    if not os.path.exists(output_file):
        print(f"ERA5 conversion reported success but did not write {output_file}", file=sys.stderr)
        return False
    # Relay converter warnings (solar noon, clock shift, large biases).
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return True

def extract_chemistry(lat, lon, years, output_file, chem_dir="data/nadp_data_grids"):
    """Extract atmospheric chemistry data."""
    # Check if chemistry data directory exists
    if not os.path.exists(chem_dir):
        print(f"  Error: NADP data directory not found: {chem_dir}")
        return None

    script_path = find_skill_script("ameriflux-atmchem-info", "extract_nadp_range.py")
    start_year = min(years)
    end_year = max(years)
    cmd = [sys.executable, script_path,
           "--input", chem_dir,
           "--output", output_file,
           "--longitude", str(lon),
           "--latitude", str(lat),
           "--year1", str(start_year),
           "--year2", str(end_year)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    if result.returncode != 0:
        print(f"  Error running chemistry extraction: {result.stderr}")
        return None

    if os.path.exists(output_file):
        with open(output_file, 'r') as f:
            chem_data = json.load(f)
            # Validate that data was actually extracted
            if 'data_by_year' in chem_data and len(chem_data['data_by_year']) > 0:
                # Count how many years have data
                years_with_data = sum(1 for year_data in chem_data['data_by_year'].values()
                                     if year_data.get('raw_ion_conc', {}) or year_data.get('elemental_conc', {}))
                if years_with_data > 0:
                    print(f"  Chemistry: Successfully extracted data for {years_with_data} out of {len(chem_data['data_by_year'])} years")
                    return chem_data
                else:
                    print(f"  Chemistry: No meaningful ion concentration data found for any year")
                    return None
            else:
                print(f"  Chemistry: No year data in extraction results")
                return None
    return None

def valid_chemistry_value(nc_var, value):
    """Return a finite in-range chemistry value, or None if it is invalid."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value) or abs(value) >= 1e20:
        return None

    lower, upper = CHEM_VALID_RANGES.get(nc_var, (None, None))
    if lower is not None and value < lower:
        return None
    if upper is not None and value > upper:
        return None
    return value

def source_keys_for_spec(spec):
    if "source_keys" in spec:
        return spec["source_keys"]
    if spec["source_key"] is None:
        return []
    return [spec["source_key"]]

def convert_chemistry_value(nc_var, source_key, raw_value, factor):
    if raw_value is None:
        return None
    value = raw_value if nc_var == "PHRG" or factor is None else raw_value * factor
    return valid_chemistry_value(nc_var, value)

def fill_annual_values(nc_var, target, file_years, available_data, report_meta):
    """Fill annual chemistry by linear interpolation, edge fill, or defaults."""
    if available_data:
        available_indices = np.array(sorted(available_data.keys()), dtype=float)
        available_values = np.array([available_data[int(idx)] for idx in available_indices], dtype=float)
        all_indices = np.arange(len(file_years), dtype=float)
        filled_values = np.interp(all_indices, available_indices, available_values)

        for i, value in enumerate(filled_values):
            target[i, 0] = value
            if i not in available_data:
                report_meta["filled_by_gap_strategy_years"] += 1
        report_meta["interpolation_method"] = "linear annual interpolation with nearest-edge fill"
        return

    default_value = CHEM_DEFAULT_VALUES.get(nc_var)
    if default_value is not None:
        for i in range(len(file_years)):
            target[i, 0] = default_value
        report_meta["filled_only"] = True
        report_meta["defaulted_by_policy_years"] = len(file_years)
        if nc_var == "PHRG":
            report_meta["notes"].append("No valid NADP pH values found; set PHRG to neutral pH 7 by policy.")
        else:
            report_meta["notes"].append("No valid source values found; set to template default by policy.")
    else:
        report_meta["filled_only"] = True
        report_meta["notes"].append("Template variable created but no valid source values were found.")

def add_chemistry_to_netcdf(netcdf_file, chemistry_data, years):
    """Add chemistry variables to the NetCDF file with gap filling and completeness reporting."""
    report = {
        "template_chemistry_variables": sorted(CHEM_TEMPLATE_VARS.keys()),
        "source_available": chemistry_data is not None,
        "variables": {},
    }

    with Dataset(netcdf_file, 'a') as nc:
        # Get years from the file
        file_years = nc.variables['year'][:]

        for nc_var, spec in CHEM_TEMPLATE_VARS.items():
            if nc_var not in nc.variables:
                var = nc.createVariable(nc_var, 'f4', ('year', 'ngrid'), fill_value=1e30)
                var.long_name = spec["long_name"]
                var.units = spec["units"]

            report["variables"][nc_var] = {
                "derived_years": 0,
                "filled_by_gap_strategy_years": 0,
                "defaulted_by_policy_years": 0,
                "filled_only": False,
                "source_key": spec["source_key"],
                "source_keys": source_keys_for_spec(spec),
                "invalid_source_values": 0,
                "notes": [],
            }

            if not chemistry_data or spec["source_key"] is None:
                if spec["source_key"] is None:
                    report["variables"][nc_var]["notes"].append("No source mapping implemented for this template chemistry variable.")
                else:
                    report["variables"][nc_var]["notes"].append("No chemistry source data available.")
                fill_annual_values(nc_var, nc.variables[nc_var], file_years, {}, report["variables"][nc_var])
                continue

            # Collect available data for this variable
            available_data = {}
            for i, year in enumerate(file_years):
                year_str = str(int(year))
                if year_str in chemistry_data.get('data_by_year', {}):
                    year_data = chemistry_data['data_by_year'][year_str]
                    raw_data = year_data.get('raw_ion_conc', {})
                    for source_key in source_keys_for_spec(spec):
                        if source_key not in raw_data:
                            continue
                        raw_value = raw_data[source_key]
                        value = convert_chemistry_value(nc_var, source_key, raw_value, spec["factor"])
                        if value is None:
                            report["variables"][nc_var]["invalid_source_values"] += 1
                            continue
                        available_data[i] = value
                        break
            report["variables"][nc_var]["derived_years"] = len(available_data)

            # Fill gaps using interpolation and edge fill; defaults only if no valid source values exist.
            fill_annual_values(nc_var, nc.variables[nc_var], file_years, available_data, report["variables"][nc_var])

    return report

def get_years_from_era5(era5_file):
    """Extract years from ERA5 CSV file."""
    df = pd.read_csv(era5_file, dtype={'TIMESTAMP_START': str})
    df['year'] = df['TIMESTAMP_START'].str[:4].astype(int)
    return sorted(df['year'].unique())

def main():
    parser = argparse.ArgumentParser(description='Create EcoSIM climate forcing NetCDF for AmeriFlux site')
    parser.add_argument('site_id', help='AmeriFlux site ID (e.g., US-Ha1)')
    parser.add_argument('--output', '-o', help='Output NetCDF file path')
    parser.add_argument('--data-dir', default='data', help='Data directory')
    parser.add_argument('--result-dir', default='result', help='Result directory')

    args = parser.parse_args()

    site_id = args.site_id
    data_dir = args.data_dir
    result_root = args.result_dir
    result_dir = os.path.join(result_root, site_id)

    # Ensure result directory exists
    os.makedirs(result_dir, exist_ok=True)

    # Default output file
    if not args.output:
        args.output = f"{result_dir}/{site_id}_ecosim_climate.nc"

    print(f"Processing site: {site_id}")

    # Step 1: Get site information
    print("Step 1: Extracting site information...")
    site_data = run_site_info(site_id, result_dir)
    if not site_data:
        print("Failed to extract site information", file=sys.stderr)
        sys.exit(1)

    from Tools.driving_data_conflicts import write_report
    site_check = write_report(site_id, result_dir, site_data=site_data,
                              output=Path(result_dir) / f'{site_id}_site_information_check.json')
    if site_check['status'] == 'failed':
        print('ERROR: site information conflicts; see site_information_check report.', file=sys.stderr)
        sys.exit(1)
    if site_check['status'] == 'warning':
        print('WARNING: some site evidence remains unverified; see site_information_check report.', file=sys.stderr)

    lat = site_data['ALATG']
    lon = site_data['ALONG']
    print(f"Site location: {lat}, {lon}")

    # Step 2: Find ERA5 file
    print("Step 2: Finding ERA5 data file...")
    era5_file = find_era5_file(site_id, data_dir)
    if not era5_file:
        print(f"ERA5 file not found for site {site_id} under {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found ERA5 file: {era5_file}")

    # Get years from ERA5 file
    years = get_years_from_era5(era5_file)
    print(f"Years in data: {years}")

    # Step 3: Convert ERA5 to NetCDF
    print("Step 3: Converting ERA5 data to EcoSIM format...")
    temp_nc = args.output + '.temp'
    era5_quality_report_file = f"{result_dir}/{site_id}_era5_quality_report.json"
    if not run_era5_conversion(era5_file, temp_nc, site_id, era5_quality_report_file):
        print("Failed to convert ERA5 data", file=sys.stderr)
        sys.exit(1)
    era5_quality_report = None
    if os.path.exists(era5_quality_report_file):
        with open(era5_quality_report_file, "r") as f:
            era5_quality_report = json.load(f)

    # Step 4: Extract chemistry data
    print("Step 4: Extracting atmospheric chemistry...")
    chem_file = f"{result_dir}/{site_id}_chemistry.json"
    chem_dir = f"{data_dir}/nadp_data_grids"

    # Check if chemistry directory exists and has content
    if not os.path.exists(chem_dir):
        print(f"  Warning: Chemistry data directory not found: {chem_dir}")
        print("  Continuing without atmospheric chemistry data...")
        chemistry_data = None
    else:
        chemistry_data = extract_chemistry(lat, lon, years, chem_file, chem_dir)
        if not chemistry_data:
            print("  Chemistry extraction did not return usable data")
            print("  Continuing without atmospheric chemistry data...")
        else:
            print("  Chemistry data will be added to NetCDF")

    # Step 5: Add chemistry to NetCDF
    print("Step 5: Adding chemistry to NetCDF...")
    chemistry_report = None
    if chemistry_data:
        chemistry_report = add_chemistry_to_netcdf(temp_nc, chemistry_data, years)
    else:
        print("No chemistry data available, skipping chemistry addition")
        chemistry_report = add_chemistry_to_netcdf(temp_nc, None, years)

    # Rename temp file to final output, named by its forcing years
    # (e.g. US-UMB_ecosim_climate_1981-2021.nc).
    os.rename(temp_nc, args.output)
    args.output = str(finalize_climate_file_name(args.output))

    report_path = f"{result_dir}/{site_id}_climate_derivation_report.json"
    underived = sorted(
        key for key, meta in chemistry_report["variables"].items()
        if meta["filled_only"]
    )
    with open(report_path, "w") as f:
        json.dump(
            {
                "site_id": site_id,
                "output_file": args.output,
                "underived_or_fill_only_variables": underived,
                "era5_quality_report": era5_quality_report,
                "chemistry_report": chemistry_report,
            },
            f,
            indent=2,
        )

    print(f"Successfully created EcoSIM climate file: {args.output}")
    print(f"Variables not derived from source data: {underived}")
    print(f"Derivation report: {report_path}")
    progress = update_progress(site_id, result_root)
    if progress:
        print(f"Progress tracker: {progress}")

if __name__ == "__main__":
    main()
