#!/usr/bin/env python3
"""Unified AmeriFlux site extraction script.

Combines four functionalities:
1. Extract site metadata (lat, lon, elevation, MAT, climate code, IGBP type) from the AmeriFlux website.
2. Extract NADP atmospheric chemistry data for a range of years.
3. Extract tDEP atmospheric deposition data for a range of years.
4. Extract a dominant-component soil profile from a gSSURGO geodatabase (via ameriflux-surgo-grid-extract skill).

The script produces a single JSON file containing site information and per‑year NADP, tDEP, and gSSURGO data.
"""

import glob
import os
import re
import sys
import json
import argparse
import subprocess
import math
import importlib.util
from typing import Dict, Any, Optional

# --- Site metadata extraction (from ameriflux-site-info) ---

def _load_site_info_module():
    skill_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidates = [
        os.path.join(skill_root, "ameriflux-site-info", "extract_ameriflux_site_data.py"),
        os.path.join(skill_root, "ameriflux_site_info", "extract_ameriflux_site_data.py"),
        os.path.join(os.getcwd(), ".agents", "skills", "ameriflux-site-info", "extract_ameriflux_site_data.py"),
    ]
    script_path = next((path for path in candidates if os.path.exists(path)), None)
    if script_path is None:
        raise FileNotFoundError("Could not locate ameriflux-site-info extractor script.")

    spec = importlib.util.spec_from_file_location("ameriflux_site_info_extractor", script_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load site metadata extractor from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_site_info(site_id: str, output_dir: str = "result") -> Optional[Dict[str, Any]]:
    """Resolve EcoSIM site metadata with the structured AmeriFlux extractor."""
    module = _load_site_info_module()
    site_output_dir = os.path.join(output_dir, site_id)
    return module.extract_site_info(site_id, output_dir=site_output_dir)

# --- NADP extraction (from extract_nadp_range.py) ---
import rasterio
from pyproj import Transformer

ELEMENTAL_CONVERSIONS = {
    "so4": 0.3338,
    "no3": 0.2259,
    "nh4": 0.7765,
}
ION_LIST = ["phlab", "so4", "no3", "nh4", "ca", "mg", "na", "k", "cl"]
VALID_EXT = [".tif", ".asc", ".TIF", ".ASC"]


def is_valid_nadp_value(ion: str, value: Any) -> bool:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value) or value <= -900 or abs(value) >= 1e20:
        return False
    if ion == "phlab":
        return 0.0 <= value <= 14.0
    return value >= 0.0


def extract_nadp_range(lat: float, lon: float, base_dir: str, start_year: int, end_year: int) -> Dict[str, Any]:
    results = {
        "metadata": {
            "requested_lat": lat,
            "requested_lon": lon,
            "years": list(range(start_year, end_year + 1)),
        },
        "data_by_year": {},
    }
    for year in range(start_year, end_year + 1):
        year_str = str(year)
        year_root = os.path.join(base_dir, year_str)
        if not os.path.isdir(year_root):
            continue
        year_data = {"raw_ion_conc": {}, "elemental_conc": {}}
        for ion in ION_LIST:
            folder_variants = ["pH"] if ion == "phlab" else [ion.upper(), ion.capitalize()]
            grid_file = None
            for variant in folder_variants:
                sub_folder = f"{variant}_conc_{year_str}"
                file_prefix = f"conc_{ion.lower()}_{year_str}"
                for ext in VALID_EXT:
                    candidate = os.path.join(year_root, sub_folder, f"{file_prefix}{ext}")
                    if os.path.isfile(candidate):
                        grid_file = candidate
                        break
                if grid_file:
                    break
            if not grid_file:
                continue
            try:
                with rasterio.open(grid_file) as src:
                    if src.crs and not src.crs.is_geographic:
                        transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                        tx, ty = transformer.transform(lon, lat)
                    else:
                        tx, ty = lon, lat
                    row, col = src.index(tx, ty)
                    if 0 <= row < src.height and 0 <= col < src.width:
                        val = src.read(1)[row, col]
                        if is_valid_nadp_value(ion, val):
                            key = f"{ion}_mg_l" if ion != "phlab" else "ph"
                            year_data["raw_ion_conc"][key] = float(val)
                            if ion in ELEMENTAL_CONVERSIONS:
                                element_val = val * ELEMENTAL_CONVERSIONS[ion]
                                element_key = f"{ion}_as_element_mg_l"
                                year_data["elemental_conc"][element_key] = float(element_val)
            except Exception as e:
                print(f"NADP extraction error year {year} ion {ion}: {e}", file=sys.stderr)
        results["data_by_year"][year_str] = year_data
    return results

# --- tDEP extraction (from extract_tdep_from_dir.py) ---

TDEP_VAR_MAP = {
    "CN4RIG": "nh4_ww",
    "CNORIG": "no3_ww",
    "CSORG": "s_ww",
    "CCARG": "ca_ww",
    "CMGRG": "mg_ww",
    "CNARG": "na_ww",
    "CKARG": "k_ww",
    "CCLRG": "cl_ww",
    "RAINH": "precip_ww",
}

TDEP_CRS = (
    "+proj=aea +lat_1=29.5 +lat_2=45.5 +lat_0=23 +lon_0=-96 "
    "+x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs"
)


def extract_tdep_range(lat: float, lon: float, base_dir: str, start_year: int, end_year: int) -> Dict[str, Any]:
    results = {
        "metadata": {
            "requested_lat": lat,
            "requested_lon": lon,
            "years": list(range(start_year, end_year + 1)),
        },
        "data_by_year": {},
    }
    try:
        transformer = Transformer.from_crs("EPSG:4326", TDEP_CRS, always_xy=True)
        tx, ty = transformer.transform(lon, lat)
    except Exception as e:
        print(f"Coordinate transform error for tDEP: {e}", file=sys.stderr)
        return results

    for year in range(start_year, end_year + 1):
        year_str = str(year)
        year_dir = os.path.join(base_dir, f"tDEP-{year_str}")
        if not os.path.isdir(year_dir):
            continue
        year_data = {"raw_values": {}, "converted_concentrations": {}}
        files = os.listdir(year_dir)
        # Precipitation first (RAINH)
        precip_file = next((f for f in files if f.startswith("precip_ww") and f.endswith('.tif')), None)
        precip_m = None
        if precip_file:
            with rasterio.open(os.path.join(year_dir, precip_file)) as src:
                precip_val = next(src.sample([(tx, ty)]))[0]
                if 0 <= precip_val < 1e10:
                    year_data["raw_values"]["RAINH"] = float(precip_val)
                    precip_m = precip_val / 100.0  # cm -> m
        # Other variables
        for tmpl_var, tdep_prefix in TDEP_VAR_MAP.items():
            if tmpl_var == "RAINH":
                continue
            target_file = next((f for f in files if f.startswith(tdep_prefix) and f.endswith('.tif')), None)
            if not target_file:
                continue
            with rasterio.open(os.path.join(year_dir, target_file)) as src:
                val = next(src.sample([(tx, ty)]))[0]
                year_data["raw_values"][tmpl_var] = float(val)
                if precip_m and precip_m > 0:
                    conc = (val * 0.1) / precip_m  # kg/ha -> g/m3 (approx)
                    year_data["converted_concentrations"][tmpl_var] = float(conc)
        results["data_by_year"][year_str] = year_data
    return results

# --- gSSURGO extraction wrapper ---

def run_gssurgo_extraction(gdb_path: str, lon: float, lat: float, template_path: str, out_path: str, extend_last: bool = False) -> Optional[Dict[str, Any]]:
    """Execute the ameriflux-surgo-grid-extract skill script and return parsed JSON.

    Parameters
    ----------
    gdb_path: Path to the gSSURGO_CONUS.gdb file.
    lon, lat: Coordinates for the site.
    template_path: Path to the template NetCDF file containing CDPTH values.
    out_path: Destination JSON output file.
    extend_last: Whether to extend the deepest horizon.

    Returns
    -------
    Parsed JSON dict from the skill output, or ``None`` if the extraction fails.
    """
    cmd = [sys.executable, ".agents/skills/ameriflux-surgo-grid-extract/extract_gssurgo_profile.py",
           "--gdb", gdb_path,
           "--lon", str(lon), "--lat", str(lat),
           "--template", template_path,
           "--out", out_path]
    if extend_last:
        cmd.append("--extend-last")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"gSSURGO extraction failed: {result.stderr}", file=sys.stderr)
        return None
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    try:
        with open(out_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Failed to read gSSURGO output JSON: {e}", file=sys.stderr)
        return None


def run_namelist_generation(
    site_id: str,
    output_path: str,
    result_dir: str = "result",
    case_name: str = "",
    grid_file: str = "",
    pft_mgmt_file: str = "",
    climate_file: str = "",
    pft_file: str = "",
    atm_ghg_file: str = "",
    micpar_file: str = "",
    run_dir: str = "",
    clm_factor_in: str = "NO",
    soil_mgmt_in: str = "NO",
    lignification: str = "false",
) -> Optional[str]:
    """Create an EcoSIM namelist for a site package.

    The namelist generator performs the final existence checks for climate,
    grid, plant-management, PFT-parameter, and atmospheric GHG files. Missing
    core forcing files should fail instead of being silently set to ``NO``.
    """
    if not site_id:
        print("Namelist generation requires --site-id.", file=sys.stderr)
        return None

    namelist_script_path = os.path.join(
        os.getcwd(),
        ".agents",
        "skills",
        "ameriflux-namelist-generator",
        "scripts",
        "create_ameriflux_namelist.py",
    )
    if not output_path:
        output_path = os.path.join(result_dir, site_id, f"{site_id}.namelist")

    cmd = [
        sys.executable,
        namelist_script_path,
        "--site-id",
        site_id,
        "--repo-root",
        os.getcwd(),
        "--output",
        output_path,
        "--clm-factor-in",
        clm_factor_in,
        "--soil-mgmt-in",
        soil_mgmt_in,
        "--lignification",
        lignification,
    ]
    optional_args = [
        ("--case-name", case_name),
        ("--grid-file", grid_file),
        ("--pft-mgmt-file", pft_mgmt_file),
        ("--climate-file", climate_file),
        ("--pft-file", pft_file),
        ("--atm-ghg-file", atm_ghg_file),
        ("--micpar-file", micpar_file),
        ("--run-dir", run_dir),
    ]
    for flag, value in optional_args:
        if value:
            cmd.extend([flag, value])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"EcoSIM namelist generation failed: {result.stderr}", file=sys.stderr)
        return None

    generated_path = output_path
    stdout_path = result.stdout.strip().splitlines()
    if stdout_path:
        generated_path = stdout_path[-1]
    print(f"EcoSIM namelist generated: {generated_path}")
    return os.path.abspath(generated_path)


def existing_site_artifact(result_dir: str, site_id: str, filename: str) -> str:
    for folder in (os.path.join(result_dir, site_id), os.path.join(result_dir, site_id, "input")):
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            return path
    return ""


CLIMATE_NAME_WITH_YEARS = re.compile(r"_ecosim_climate_(\d{4})-(\d{4})\.nc$")


def existing_climate_artifact(result_dir: str, site_id: str) -> str:
    """Year-named climate file (<SITE_ID>_ecosim_climate_<first>-<last>.nc), else the legacy name."""
    for folder in (os.path.join(result_dir, site_id), os.path.join(result_dir, site_id, "input")):
        named = sorted(
            path for path in glob.glob(os.path.join(folder, f"{site_id}_ecosim_climate_*.nc"))
            if CLIMATE_NAME_WITH_YEARS.search(path)
        )
        if named:
            return named[-1]
    return existing_site_artifact(result_dir, site_id, f"{site_id}_ecosim_climate.nc")


def created_climate_path(stdout: str) -> str:
    """Final climate path reported by Tools/create_ecosim_climate_forcing.py."""
    match = re.search(r"Successfully created EcoSIM climate file: (.+)", stdout)
    return match.group(1).strip() if match else ""

# --- Unified workflow ---

def run_unified_extraction(
    site_id: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    nadp_dir: str = "",
    tdep_dir: str = "",
    start_year: int = 0,
    end_year: int = 0,
    output_file: str = "result/unified_site_data.json",
    gssurgo_gdb: str = "",
    gssurgo_template: str = "",
    gssurgo_out: str = "",
    gssurgo_extend_last: bool = False,
    climate_output: str = "",
    grid_output: str = "",
    climate_data_dir: str = "data",
    result_dir: str = "result",
    create_namelist: bool = False,
    namelist_output: str = "",
    namelist_run_dir: str = "",
    case_name: str = "",
    pft_mgmt_file: str = "",
    pft_file: str = "",
    atm_ghg_file: str = "",
    micpar_file: str = "",
    clm_factor_in: str = "NO",
    soil_mgmt_in: str = "NO",
    lignification: str = "false",
) -> None:
    # Resolve coordinates
    if site_id:
        site_info = extract_site_info(site_id)
        if not site_info:
            sys.exit(1)
        lat = site_info["ALATG"]
        lon = site_info["ALONG"]
    else:
        if lat is None or lon is None:
            print("Latitude and longitude required when no site_id is provided.", file=sys.stderr)
            sys.exit(1)
        site_info = {}
    # NADP extraction
    nadp_results = {}
    if nadp_dir:
        nadp_results = extract_nadp_range(lat, lon, nadp_dir, start_year, end_year)
    # tDEP extraction
    tdep_results = {}
    if tdep_dir:
        tdep_results = extract_tdep_range(lat, lon, tdep_dir, start_year, end_year)
    # gSSURGO extraction if provided
    gssurgo_result = {}
    if gssurgo_gdb and gssurgo_template and gssurgo_out:
        gssurgo_result = run_gssurgo_extraction(
            gssurgo_gdb, lon, lat, gssurgo_template, gssurgo_out, gssurgo_extend_last
        ) or {}
    # Merge all
    unified = {
        "site_id": site_id or "custom_coordinates",
        "site_metadata": site_info,
        "nadp": nadp_results,
        "tdep": tdep_results,
        "gssurgo": gssurgo_result,
    }
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(unified, f, indent=4)
    print(f"Unified extraction complete. Output written to {output_file}")
    # Generate EcoSIM climate forcing NetCDF if requested
    generated_climate_path = ""
    if climate_output:
        climate_script_path = os.path.join(os.getcwd(), "Tools", "create_ecosim_climate_forcing.py")
        climate_cmd = [sys.executable, climate_script_path, site_id or ""]
        if climate_output:
            climate_cmd.extend(["--output", climate_output])
        climate_cmd.extend(["--data-dir", climate_data_dir, "--result-dir", result_dir])
        result = subprocess.run(climate_cmd, capture_output=True, text=True)
        # The climate tool renames its output to carry the forcing years.
        output_path = created_climate_path(result.stdout) or climate_output
        if result.returncode != 0 or not os.path.exists(output_path):
            print(
                f"EcoSIM climate forcing generation failed (exit {result.returncode}); "
                f"{output_path} {'exists' if os.path.exists(output_path) else 'was not written'}.\n"
                f"{result.stdout}{result.stderr}",
                file=sys.stderr,
            )
        else:
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            print(f"EcoSIM climate forcing generated: {output_path}")
            generated_climate_path = output_path
    # Generate EcoSIM grid forcing NetCDF if requested
    if grid_output:
        grid_script_path = os.path.join(os.getcwd(), "Tools", "create_ecosim_grid_forcing.py")
        grid_cmd = [sys.executable, grid_script_path, site_id or ""]
        result = subprocess.run(grid_cmd, capture_output=True, text=True)
        default_grid_path = os.path.join(result_dir, site_id or "", f"{site_id}_ecosim_grid.nc")
        if result.returncode != 0 or not os.path.exists(default_grid_path):
            print(
                f"EcoSIM grid forcing generation failed (exit {result.returncode}); "
                f"{default_grid_path} {'exists' if os.path.exists(default_grid_path) else 'was not written'}.\n"
                f"{result.stdout}{result.stderr}",
                file=sys.stderr,
            )
        else:
            if result.stderr.strip():
                print(result.stderr.strip(), file=sys.stderr)
            if os.path.abspath(grid_output) != os.path.abspath(default_grid_path):
                try:
                    os.rename(default_grid_path, grid_output)
                    print(f"EcoSIM grid forcing moved to {grid_output}")
                except Exception as e:
                    print(f"Failed to move grid output: {e}", file=sys.stderr)
            else:
                print(f"EcoSIM grid forcing generated: {default_grid_path}")

    namelist_path = ""
    if create_namelist or namelist_output:
        if not site_id:
            print("EcoSIM namelist generation skipped: --site-id is required.", file=sys.stderr)
        else:
            climate_for_namelist = generated_climate_path or existing_climate_artifact(result_dir, site_id)
            grid_for_namelist = grid_output or existing_site_artifact(
                result_dir, site_id, f"{site_id}_ecosim_grid.nc"
            )
            pft_mgmt_for_namelist = pft_mgmt_file or existing_site_artifact(
                result_dir, site_id, f"{site_id}_pft_mgmt.nc"
            )
            namelist_path = run_namelist_generation(
                site_id=site_id,
                output_path=namelist_output,
                result_dir=result_dir,
                case_name=case_name,
                grid_file=grid_for_namelist,
                pft_mgmt_file=pft_mgmt_for_namelist,
                climate_file=climate_for_namelist,
                pft_file=pft_file,
                atm_ghg_file=atm_ghg_file,
                micpar_file=micpar_file,
                run_dir=namelist_run_dir,
                clm_factor_in=clm_factor_in,
                soil_mgmt_in=soil_mgmt_in,
                lignification=lignification,
            ) or ""

    # Record forcing files in YAML
    forcing_data = {}
    if generated_climate_path:
        forcing_data["clm_hour_file_in"] = os.path.abspath(generated_climate_path)
    if grid_output:
        actual_grid_path = grid_output if grid_output else os.path.join(result_dir, site_id, f"{site_id}_ecosim_grid.nc")
        forcing_data["grid_file_in"] = os.path.abspath(actual_grid_path)
    if namelist_path:
        forcing_data["namelist_file"] = os.path.abspath(namelist_path)

    if forcing_data:
        import yaml
        yaml_dir = os.path.join(result_dir, site_id) if site_id else result_dir
        os.makedirs(yaml_dir, exist_ok=True)
        yaml_path = os.path.join(yaml_dir, f"{site_id}_forcing.yaml")
        with open(yaml_path, "w") as f:
            yaml.dump(forcing_data, f, default_flow_style=False)
        print(f"Forcing file paths recorded in {yaml_path}")


def main():
    parser = argparse.ArgumentParser(description="Unified AmeriFlux site and EcoSIM forcing data extraction.")
    parser.add_argument("--site-id", help="AmeriFlux site identifier (e.g., US-XXX).")
    parser.add_argument("--latitude", type=float, help="Latitude (used if site-id not provided).")
    parser.add_argument("--longitude", type=float, help="Longitude (used if site-id not provided).")
    parser.add_argument("--nadp-input", required=True, help="Base directory for NADP year folders.")
    parser.add_argument("--tdep-input", required=True, help="Base directory for tDEP year folders.")
    parser.add_argument("--year1", type=int, required=True, help="Start year (inclusive).")
    parser.add_argument("--year2", type=int, required=True, help="End year (inclusive).")
    parser.add_argument("--gssurgo-gdb", help="Path to gSSURGO_CONUS.gdb file.")
    parser.add_argument("--gssurgo-template", help="Path to template NetCDF file for CDPTH values.")
    parser.add_argument("--gssurgo-out", help="Destination JSON output for gSSURGO extraction.")
    parser.add_argument("--gssurgo-extend-last", action="store_true", help="Extend deepest horizon in gSSURGO extraction.")
    parser.add_argument("--output", default="result/unified_site_data.json", help="Path to final JSON output.")
    parser.add_argument("--climate-output", help="Path to EcoSIM climate forcing NetCDF output.")
    parser.add_argument("--grid-output", help="Path to EcoSIM grid forcing NetCDF output.")
    parser.add_argument("--climate-data-dir", default="data", help="Data directory for climate forcing (contains ERA5 files).")
    parser.add_argument("--result-dir", default="result", help="Directory for intermediate results and output files.")
    parser.add_argument("--create-namelist", action="store_true", help="Generate an EcoSIM namelist after site files are available.")
    parser.add_argument("--namelist-output", help="Path to EcoSIM namelist output. Defaults to result/<SITE_ID>/<SITE_ID>.namelist when --create-namelist is used.")
    parser.add_argument("--namelist-run-dir", help="EcoSIM run directory; namelist input paths are written relative to this directory.")
    parser.add_argument("--case-name", help="EcoSIM case_name for the namelist; default is site ID.")
    parser.add_argument("--pft-mgmt-file", help="Plant management NetCDF for namelist generation; defaults to discovery under result/<SITE_ID>.")
    parser.add_argument("--pft-file", help="EcoSIM PFT parameter NetCDF for namelist generation.")
    parser.add_argument("--atm-ghg-file", help="Atmospheric GHG forcing NetCDF for namelist generation.")
    parser.add_argument("--micpar-file", help="Optional microbial parameter NetCDF for namelist generation.")
    parser.add_argument("--clm-factor-in", default="NO", help="Optional climate factor input for namelist generation.")
    parser.add_argument("--soil-mgmt-in", default="NO", help="Optional soil management input for namelist generation.")
    parser.add_argument("--lignification", choices=("true", "false"), default="false", help="Set namelist llignification flag.")
    args = parser.parse_args()

    run_unified_extraction(
        site_id=args.site_id,
        lat=args.latitude,
        lon=args.longitude,
        nadp_dir=args.nadp_input,
        tdep_dir=args.tdep_input,
        start_year=args.year1,
        end_year=args.year2,
        output_file=args.output,
        gssurgo_gdb=args.gssurgo_gdb or "",
        gssurgo_template=args.gssurgo_template or "",
        gssurgo_out=args.gssurgo_out or "",
        gssurgo_extend_last=args.gssurgo_extend_last,
        climate_output=args.climate_output or "",
        grid_output=args.grid_output or "",
        climate_data_dir=args.climate_data_dir,
        result_dir=args.result_dir,
        create_namelist=args.create_namelist,
        namelist_output=args.namelist_output or "",
        namelist_run_dir=args.namelist_run_dir or "",
        case_name=args.case_name or "",
        pft_mgmt_file=args.pft_mgmt_file or "",
        pft_file=args.pft_file or "",
        atm_ghg_file=args.atm_ghg_file or "",
        micpar_file=args.micpar_file or "",
        clm_factor_in=args.clm_factor_in,
        soil_mgmt_in=args.soil_mgmt_in,
        lignification=args.lignification,
    )

if __name__ == "__main__":
    main()
