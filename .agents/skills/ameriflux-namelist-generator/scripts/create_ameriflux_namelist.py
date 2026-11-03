#!/usr/bin/env python3
"""Create an EcoSIM namelist for an AmeriFlux site."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from string import Template
from typing import Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "assets" / "ameriflux_namelist.template"

DEFAULT_PFT_FILE = Path("/Users/jinyuntang/work/github/ecosim_workspace/croots/input_data/ecosim_pftpar_20260520.nc")
DEFAULT_ATM_GHG_FILE = Path("/Users/jinyuntang/work/github/ecosim_workspace/croots/input_data/fatm_hist_GHGs_1750-2023.nc")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-id", required=True, help="AmeriFlux site ID, e.g. US-Ha1")
    parser.add_argument("--case-name", help="EcoSIM case_name; default is site ID")
    parser.add_argument("--output", type=Path, help="Output namelist path; default result/<SITE_ID>/<SITE_ID>.namelist")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="EcoSIM_python_tools repository root")
    parser.add_argument("--run-dir", type=Path, help="EcoSIM run directory; input paths are made relative to this directory")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="Namelist template")
    parser.add_argument("--grid-file", type=Path, help="Site grid NetCDF")
    parser.add_argument("--pft-mgmt-file", type=Path, help="Plant management NetCDF")
    parser.add_argument("--climate-file", type=Path, help="Hourly climate forcing NetCDF")
    parser.add_argument("--pft-file", type=Path, default=DEFAULT_PFT_FILE, help="EcoSIM PFT parameter NetCDF")
    parser.add_argument("--atm-ghg-file", type=Path, default=DEFAULT_ATM_GHG_FILE, help="Atmospheric GHG forcing NetCDF")
    parser.add_argument("--micpar-file", type=Path, help="Optional microbial parameter NetCDF")
    parser.add_argument("--clm-factor-in", default="NO")
    parser.add_argument("--soil-mgmt-in", default="NO")
    parser.add_argument("--lignification", choices=("true", "false"), default="false")
    parser.add_argument("--start-year", type=int, help="Simulation start year used in start_date; default force_start_year minus spinup years")
    parser.add_argument("--force-start-year", type=int, help="First climate forcing year")
    parser.add_argument("--spinup-end-year", type=int, help="Last climate forcing year used for spinup")
    parser.add_argument("--force-end-year", type=int, help="Last climate forcing year")
    parser.add_argument("--ecosystem-type", choices=("grassland", "cropland", "other"),
                        help="Land cover; otherwise read igbp_type from staged site JSON")
    parser.add_argument("--observation-end-year", type=int,
                        help="Verified last observation year; required for grassland/cropland")
    parser.add_argument("--spinup-cycles", type=int)
    parser.add_argument("--regular-cycles", type=int, default=1)
    parser.add_argument("--final-cycles", type=int, default=0)
    parser.add_argument("--stop-n", type=int, help="ecosim_time stop_n; default spinup years plus regular/final forcing years")
    parser.add_argument("--hist-mfilt", type=int, default=7300)
    parser.add_argument("--hist-nhtfrq", default="-24")
    parser.add_argument("--npxs", default="30,30,30")
    parser.add_argument("--npys", default="10,10,10")
    parser.add_argument("--ncyc-litr", type=int, default=30)
    parser.add_argument("--ncyc-snow", type=int, default=20)
    parser.add_argument("--grid-mode", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="Print namelist instead of writing it")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    output = args.output or repo_root / "result" / args.site_id / f"{args.site_id}.namelist"
    grid_file = args.grid_file or discover_site_file(repo_root, args.site_id, ("grid",), (".nc",))
    pft_mgmt_file = args.pft_mgmt_file or discover_site_file(repo_root, args.site_id, ("pft_mgmt", "mgmt"), (".nc",))
    climate_file = args.climate_file or discover_site_file(repo_root, args.site_id, ("climate", "clim"), (".nc",))

    missing = []
    for label, path in (
        ("grid_file_in", grid_file),
        ("pft_mgmt_in", pft_mgmt_file),
        ("clm_hour_file_in", climate_file),
        ("pft_file_in", args.pft_file),
        ("atm_ghg_in", args.atm_ghg_file),
    ):
        if path is None:
            missing.append(f"{label}: not found")
        elif path != Path("NO") and not path.exists():
            missing.append(f"{label}: {path}")
    if args.micpar_file is not None and not args.micpar_file.exists():
        missing.append(f"micpar_file_in: {args.micpar_file}")
    if missing:
        print("ERROR: missing required namelist input files:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 2

    force_start, force_end = resolve_forcing_years(args, climate_file, repo_root)
    ecosystem = args.ecosystem_type or infer_ecosystem_type(
        args.site_id, (grid_file.parent, climate_file.parent,
                       repo_root / "result" / args.site_id / "input",
                       repo_root / "result" / args.site_id))
    start_year, stop_n, forc_periods = resolve_chronology(args, force_start, force_end, ecosystem)

    relative_base = args.run_dir.resolve() if args.run_dir else None
    context = {
        "case_name": args.case_name or args.site_id,
        "pft_file_in": namelist_path(args.pft_file, relative_base),
        "grid_file_in": namelist_path(grid_file, relative_base),
        "pft_mgmt_in": namelist_path(pft_mgmt_file, relative_base),
        "clm_hour_file_in": namelist_path(climate_file, relative_base),
        "micpar_line": "" if args.micpar_file is None else f"micpar_file_in='{namelist_path(args.micpar_file, relative_base)}'\n",
        "clm_factor_in": args.clm_factor_in,
        "soil_mgmt_in": args.soil_mgmt_in,
        "llignification": fortran_bool(args.lignification == "true"),
        "atm_ghg_in": namelist_path(args.atm_ghg_file, relative_base),
        "lsoilCompaction": ".false.",
        "lverbose": ".false.",
        "plantOM4Heat": ".true.",
        "disp_planttrait": ".true.",
        "plant_model": ".true.",
        "microbial_model": ".true.",
        "soichem_model": ".true.",
        "start_date": f"{start_year:04d}0101000000",
        "grid_mode": str(args.grid_mode),
        "continue_run": ".false.",
        "forc_periods": forc_periods,
        "NPXS": args.npxs,
        "NPYS": args.npys,
        "NCYC_LITR": str(args.ncyc_litr),
        "NCYC_SNOW": str(args.ncyc_snow),
        "hist_mfilt": str(args.hist_mfilt),
        "hist_nhtfrq": args.hist_nhtfrq,
        "rest_opt": "nyears",
        "rest_frq": "1",
        "delta_time": "3600.",
        "stop_n": str(stop_n),
        "stop_option": "nyears",
        "diag_frq": "1",
        "diag_opt": "nsteps",
    }

    template = Template(args.template.read_text(encoding="utf-8"))
    namelist = template.safe_substitute(context)
    if args.dry_run:
        print(namelist, end="")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(namelist, encoding="utf-8")
    print(output)
    return 0


def infer_ecosystem_type(site_id: str, directories: Iterable[Path]) -> str:
    """Use land-cover metadata, never IXTYP1 (a litter flag)."""
    for directory in dict.fromkeys(directories):
        path = directory / f"{site_id}_ecosim_site.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("_raw", {})
        code = str(raw.get("igbp_type") or data.get("igbp_type") or data.get("IGBP") or "").strip().upper()
        if code in ("GRA", "GRASSLAND", "GRASSLANDS"):
            return "grassland"
        if code in ("CRO", "CROPLAND", "CROPLANDS"):
            return "cropland"
    return "other"


def resolve_chronology(args: argparse.Namespace, first: int, available_last: int,
                       ecosystem: str) -> tuple[int, int, str]:
    """Derive a calendar-aligned spinup plus transient forcing plan."""
    short_spinup = ecosystem in ("grassland", "cropland")
    last = args.observation_end_year if args.observation_end_year is not None else available_last
    if short_spinup and args.observation_end_year is None:
        raise SystemExit("ERROR: grassland/cropland requires --observation-end-year from verified observation coverage")
    if not first <= last <= available_last:
        raise SystemExit("ERROR: observation endpoint must be within available/selected forcing years; extend inputs first")
    spinup_end = args.spinup_end_year if args.spinup_end_year is not None else min(available_last, first + (4 if short_spinup else 3))
    cycles = args.spinup_cycles if args.spinup_cycles is not None else (4 if short_spinup else 18)
    if not first <= spinup_end <= available_last or min(cycles, args.regular_cycles, args.final_cycles) < 0:
        raise SystemExit("ERROR: invalid spinup window or negative forcing cycle count")
    if short_spinup and (spinup_end != first + 4 or cycles != 4 or
                         args.regular_cycles != 1 or args.final_cycles != 0):
        raise SystemExit("ERROR: grassland/cropland requires five complete forcing years x four cycles, then one transient period")
    spinup_years = (spinup_end - first + 1) * cycles
    duration = spinup_years + (last - first + 1) * (args.regular_cycles + args.final_cycles)
    start = first - spinup_years if args.start_year is None else args.start_year
    stop = max(1, duration) if args.stop_n is None else args.stop_n
    if short_spinup and (start != first - 20 or stop != duration or start + stop - 1 != last):
        raise SystemExit("ERROR: start-year and stop-n must match the 20-year spinup plus transient through the observation endpoint")
    periods = f"{first},{spinup_end},{cycles},{first},{last},{args.regular_cycles},{first},{last},{args.final_cycles}"
    return start, stop, periods


def discover_site_file(repo_root: Path, site_id: str, tokens: tuple[str, ...], suffixes: tuple[str, ...]) -> Optional[Path]:
    search_roots = [
        repo_root / "result" / site_id / "input",
        repo_root / "data",
        repo_root / "result" / site_id,
        repo_root / "result",
        repo_root / "inputs" / site_id,
        repo_root / "inputs",
        repo_root,
    ]
    patterns = []
    for token in tokens:
        for suffix in suffixes:
            patterns.append(f"*{site_id}*{token}*{suffix}")
            patterns.append(f"*{site_id}*{token.upper()}*{suffix}")

    matches: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in patterns:
            matches.extend(path for path in root.glob(pattern) if path.is_file())
    if not matches:
        return None
    return sorted(set(matches), key=lambda path: (len(path.parts), str(path)))[0].resolve()


def resolve_forcing_years(args: argparse.Namespace, climate_file: Path, repo_root: Path) -> tuple[int, int]:
    inferred = (
        infer_years_from_netcdf(climate_file)
        or infer_years_from_name(climate_file)
        or infer_years_from_site_files(repo_root, args.site_id)
    )
    force_start = args.force_start_year or (inferred[0] if inferred else None)
    force_end = args.force_end_year or (inferred[1] if inferred else None)
    if force_start is None or force_end is None:
        raise SystemExit("ERROR: could not infer climate forcing years; pass --force-start-year and --force-end-year")
    if inferred and (force_start < inferred[0] or force_end > inferred[1]):
        raise SystemExit("ERROR: selected forcing years exceed the available climate range")
    if force_end < force_start:
        raise SystemExit("ERROR: force-end-year must be >= force-start-year")
    return force_start, force_end


def infer_years_from_name(path: Path) -> Optional[tuple[int, int]]:
    match = re.search(r"((?:19|20)\d{2})\D+((?:19|20)\d{2})", path.name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def infer_years_from_site_files(repo_root: Path, site_id: str) -> Optional[tuple[int, int]]:
    search_roots = [repo_root / "data", repo_root / "result" / site_id, repo_root / "result"]
    for preferred_token in ("ERA5", "FULLSET"):
        ranges = []
        for root in search_roots:
            if not root.exists():
                continue
            for path in root.rglob(f"*{site_id}*{preferred_token}*"):
                if not path.is_file():
                    continue
                years = infer_years_from_name(path)
                if years is not None:
                    ranges.append(years)
        if ranges:
            return max(ranges, key=lambda years: (years[1] - years[0], years[1]))
    return None


def infer_years_from_netcdf(path: Path) -> Optional[tuple[int, int]]:
    try:
        from netCDF4 import Dataset  # type: ignore
    except Exception:
        return None
    try:
        with Dataset(path) as dataset:
            if "year" not in dataset.variables:
                return None
            raw = dataset.variables["year"][:]
            values = []
            for value in raw.ravel():
                try:
                    year = int(value)
                except Exception:
                    continue
                if math.isfinite(float(year)) and 1700 <= year <= 2300:
                    values.append(year)
            if not values:
                return None
            return min(values), max(values)
    except Exception:
        return None


def namelist_path(path: Path, relative_base: Optional[Path]) -> str:
    if str(path) == "NO":
        return "NO"
    resolved = path.resolve()
    if relative_base is None:
        return str(resolved)
    return os.path.relpath(resolved, relative_base)


def fortran_bool(value: bool) -> str:
    return ".true." if value else ".false."


if __name__ == "__main__":
    raise SystemExit(main())
