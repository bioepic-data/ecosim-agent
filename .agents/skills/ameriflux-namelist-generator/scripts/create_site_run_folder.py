#!/usr/bin/env python3
"""Assemble an EcoSIM run folder for a site under result/<SITE_ID>/.

Layout:

    result/<SITE_ID>/
        input/     all site artifacts (grid, climate, plant/soil management
                   NetCDF, Excel and JSON sidecars, metadata, reports) plus
                   copies of the PFT parameter, GHG, and microbial parameter files
        run_dir/   <SITE_ID>.namelist, with input paths relative to run_dir

Site artifacts generated directly in result/<SITE_ID>/ are moved into input/.
The namelist is written by create_ameriflux_namelist.py.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import create_ameriflux_namelist as namelist_generator  # noqa: E402
from Tools.climate_naming import finalize_climate_file_name  # noqa: E402
from Tools.run_progress import update_progress  # noqa: E402

CLIMATE_YEAR_GLOB = "{site}_ecosim_climate_[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9].nc"

# Required site NetCDF inputs: (namelist field, preferred file name pattern).
# The climate file is found by find_climate_file (year-named).
REQUIRED_SITE_FILES = {
    "grid_file_in": "{site}_ecosim_grid.nc",
    "pft_mgmt_in": "{site}_pft_mgmt.nc",
}
OPTIONAL_SITE_FILES = {"soil_mgmt_in": "{site}_soil_mgmt.nc"}
# Progress-tracker files (ecosim-site-run-tracker) stay at the site root.
TRACKER_FILES = ("progress.json", "PROGRESS.md")
NAMELIST_FILE_FIELDS = (
    "pft_file_in", "grid_file_in", "pft_mgmt_in", "clm_hour_file_in",
    "micpar_file_in", "atm_ghg_in", "soil_mgmt_in", "clm_factor_in",
)


def ecosim_root(repo_root: Path) -> Path:
    """EcoSIM repository containing this python-tools submodule."""
    return repo_root.parent


def newest(directory: Path, pattern: str) -> Optional[Path]:
    matches = sorted(directory.glob(pattern)) if directory.exists() else []
    return matches[-1] if matches else None


def default_static_file(repo_root: Path, pattern: str) -> Optional[Path]:
    return newest(ecosim_root(repo_root) / "input_data", pattern)


def collect_site_artifacts(site_root: Path, input_dir: Path) -> list[str]:
    """Move files generated directly in result/<SITE_ID>/ into input/."""
    moved = []
    for path in sorted(site_root.iterdir()):
        if not path.is_file() or path.name.startswith(".") or path.name in TRACKER_FILES:
            continue
        target = input_dir / path.name
        if target.exists():
            target.unlink()
        shutil.move(str(path), str(target))
        moved.append(path.name)
    return moved


def copy_static(path: Optional[Path], input_dir: Path, label: str) -> Optional[Path]:
    if path is None:
        return None
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    target = input_dir / path.name
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
    return target


def find_site_file(input_dir: Path, site_id: str, pattern: str, explicit: Optional[Path]) -> Optional[Path]:
    if explicit is not None:
        return copy_static(explicit, input_dir, pattern.format(site=site_id))
    candidate = input_dir / pattern.format(site=site_id)
    return candidate if candidate.exists() else None


def find_climate_file(input_dir: Path, site_id: str, explicit: Optional[Path]) -> Optional[Path]:
    """Climate NetCDF named by its forcing years, <SITE_ID>_ecosim_climate_<first>-<last>.nc.

    A legacy <SITE_ID>_ecosim_climate.nc (or an explicit file) is renamed in
    input/ to carry the years read from its ``year`` variable.
    """
    if explicit is not None:
        return finalize_climate_file_name(copy_static(explicit, input_dir, "climate file"))
    named = sorted(input_dir.glob(CLIMATE_YEAR_GLOB.format(site=site_id)))
    if len(named) > 1:
        raise ValueError(
            f"Several climate files in {input_dir}: {[p.name for p in named]}; choose one with --climate-file."
        )
    if named:
        return named[0]
    legacy = input_dir / f"{site_id}_ecosim_climate.nc"
    return finalize_climate_file_name(legacy) if legacy.exists() else None


def check_namelist_paths(namelist_file: Path) -> list[str]:
    """Problems with namelist input paths resolved from the run directory."""
    problems = []
    text = namelist_file.read_text(encoding="utf-8")
    for field in NAMELIST_FILE_FIELDS:
        match = re.search(rf"^\s*{field}\s*=\s*'([^']*)'", text, flags=re.MULTILINE)
        if not match or match.group(1) == "NO":
            continue
        value = match.group(1)
        resolved = (namelist_file.parent / value).resolve()
        if not resolved.exists():
            problems.append(f"{field}: {value} does not exist from {namelist_file.parent}")
        elif Path(value).is_absolute() or not value.startswith("../input/"):
            problems.append(f"{field}: {value} is not inside ../input/")
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    """Assemble the run folder, then refresh the site's progress tracker whatever the outcome."""
    status = assemble(argv)
    locator = argparse.ArgumentParser(add_help=False)
    locator.add_argument("--site-id")
    locator.add_argument("--repo-root", type=Path, default=Path.cwd())
    locator.add_argument("--result-dir", type=Path)
    known, _ = locator.parse_known_args(argv)
    if known.site_id:
        result_dir = known.result_dir or known.repo_root / "result"
        progress = update_progress(known.site_id, result_dir)
        if progress:
            print(f"Progress tracker: {progress}")
    return status


def assemble(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-id", required=True, help="AmeriFlux site ID, e.g. US-UMB")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Python-tools repository root")
    parser.add_argument("--result-dir", type=Path, help="Result root; default <repo-root>/result")
    parser.add_argument("--grid-file", type=Path, help="Grid NetCDF; default input/<SITE_ID>_ecosim_grid.nc")
    parser.add_argument(
        "--climate-file",
        type=Path,
        help="Climate NetCDF; default input/<SITE_ID>_ecosim_climate_<first>-<last>.nc",
    )
    parser.add_argument("--pft-mgmt-file", type=Path, help="Plant management NetCDF; default input/<SITE_ID>_pft_mgmt.nc")
    parser.add_argument("--soil-mgmt-file", type=Path, help="Soil management NetCDF; default input/<SITE_ID>_soil_mgmt.nc if present")
    parser.add_argument("--pft-file", type=Path, help="PFT parameter NetCDF; default newest EcoSIM input_data/ecosim_pftpar_*.nc")
    parser.add_argument("--atm-ghg-file", type=Path, help="GHG NetCDF; default newest EcoSIM input_data/fatm_hist_GHGs_*.nc")
    parser.add_argument("--micpar-file", type=Path, help="Microbial parameter NetCDF; default newest EcoSIM input_data/MicrobePars.*.nc")
    parser.add_argument("--no-micpar", action="store_true", help="Do not write micpar_file_in")
    parser.add_argument("--case-name", help="EcoSIM case_name; default site ID")
    for option in ("--start-year", "--force-start-year", "--spinup-end-year", "--force-end-year",
                   "--spinup-cycles", "--regular-cycles", "--final-cycles", "--stop-n"):
        parser.add_argument(option, type=int)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    site = args.site_id
    site_root = (args.result_dir.resolve() if args.result_dir else repo_root / "result") / site
    input_dir = site_root / "input"
    run_dir = site_root / "run_dir"
    if not site_root.exists():
        print(f"ERROR: {site_root} does not exist; generate the site inputs first.", file=sys.stderr)
        return 2
    input_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    moved = collect_site_artifacts(site_root, input_dir)

    try:
        # A site PFT file holds literature-backed trait corrections (create_site_smoke_run.py); keep using it.
        site_pft = input_dir / f"{site}_ecosim_pftpar.nc"
        if args.pft_file is None and site_pft.exists():
            pft_file = site_pft
        else:
            pft_file = copy_static(args.pft_file or default_static_file(repo_root, "ecosim_pftpar_*.nc"), input_dir, "PFT parameter file")
        ghg_file = copy_static(args.atm_ghg_file or default_static_file(repo_root, "fatm_hist_GHGs_*.nc"), input_dir, "GHG file")
        micpar_file = None if args.no_micpar else copy_static(
            args.micpar_file or default_static_file(repo_root, "MicrobePars.*.nc"), input_dir, "microbial parameter file"
        )
        site_files = {
            field: find_site_file(input_dir, site, pattern, explicit)
            for (field, pattern), explicit in zip(
                REQUIRED_SITE_FILES.items(), (args.grid_file, args.pft_mgmt_file)
            )
        }
        site_files["clm_hour_file_in"] = find_climate_file(input_dir, site, args.climate_file)
        soil_mgmt = find_site_file(input_dir, site, OPTIONAL_SITE_FILES["soil_mgmt_in"], args.soil_mgmt_file)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    missing = [field for field, path in site_files.items() if path is None]
    if pft_file is None:
        missing.append("pft_file_in")
    if ghg_file is None:
        missing.append("atm_ghg_in")
    if missing:
        print(f"ERROR: missing required inputs in {input_dir}: {', '.join(missing)}", file=sys.stderr)
        return 2

    namelist_file = run_dir / f"{site}.namelist"
    generator_args = [
        "--site-id", site,
        "--repo-root", str(repo_root),
        "--output", str(namelist_file),
        "--run-dir", str(run_dir),
        "--grid-file", str(site_files["grid_file_in"]),
        "--climate-file", str(site_files["clm_hour_file_in"]),
        "--pft-mgmt-file", str(site_files["pft_mgmt_in"]),
        "--pft-file", str(pft_file),
        "--atm-ghg-file", str(ghg_file),
    ]
    if micpar_file is not None:
        generator_args += ["--micpar-file", str(micpar_file)]
    if soil_mgmt is not None:
        generator_args += ["--soil-mgmt-in", f"../input/{soil_mgmt.name}"]
    if args.case_name:
        generator_args += ["--case-name", args.case_name]
    for name in ("start_year", "force_start_year", "spinup_end_year", "force_end_year",
                 "spinup_cycles", "regular_cycles", "final_cycles", "stop_n"):
        value = getattr(args, name)
        if value is not None:
            generator_args += [f"--{name.replace('_', '-')}", str(value)]

    status = namelist_generator.main(generator_args)
    if status != 0:
        return status

    problems = check_namelist_paths(namelist_file)
    extra = [path.name for path in run_dir.iterdir() if path.name != namelist_file.name]
    if extra:
        # Expected after a simulation (EcoSIM writes output into run_dir/).
        print(f"Note: run_dir also holds {len(extra)} other item(s), e.g. {extra[:3]}", file=sys.stderr)
    if problems:
        print("ERROR: run folder validation failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"Run folder: {site_root}")
    print(f"  run_dir/{namelist_file.name}")
    print(f"  input/ ({len(list(input_dir.iterdir()))} files; moved {len(moved)} from {site_root.name}/)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
