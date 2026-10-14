#!/usr/bin/env python3
"""Assemble finished single-site EcoSIM run folders into one multi-site project.

Each site is prepared with the single-site workflow first (result/<SITE_ID>/
with input/ and run_dir/, smoke run and plant-trait check done). This script
then sorts them into a project tree:

    result/projects/<PROJECT>/
        input/<SITE_ID>/      copy of result/<SITE_ID>/input/
        run_dir/<SITE_ID>/    <SITE_ID>.namelist with ../input/... rewritten to
                              ../../input/<SITE_ID>/...
        project.json          manifest: sites, inputs, chronology, smoke/trait/tracker status
        PROJECT.md            the manifest as a readable table

Re-running refreshes the listed sites (their project input/ and run_dir/ are
replaced), so the project follows changes made in the single-site folders.
Model output already in a project run_dir/<SITE_ID>/ is kept unless
--replace-runs is given. Sites that fail a requirement are reported and left out.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[4]
NAMELIST_FILE_FIELDS = (
    "pft_file_in", "grid_file_in", "pft_mgmt_in", "clm_hour_file_in",
    "micpar_file_in", "atm_ghg_in", "soil_mgmt_in", "clm_factor_in",
)
SKIP_INPUT = re.compile(r"\.before_|\.orig_|\.prev_|\.DS_Store$")


def rewrite_namelist(text: str, site: str) -> str:
    """Point ../input/<file> paths at ../../input/<SITE_ID>/<file>."""
    return re.sub(r"'\.\./input/", f"'../../input/{site}/", text)


def namelist_paths(namelist: Path) -> Dict[str, str]:
    text = namelist.read_text()
    paths = {}
    for field in NAMELIST_FILE_FIELDS:
        match = re.search(rf"^\s*{field}\s*=\s*'([^']*)'", text, flags=re.MULTILINE)
        if match and match.group(1) != "NO":
            paths[field] = match.group(1)
    return paths


def check_case(run_dir: Path, site: str) -> List[str]:
    namelist = run_dir / f"{site}.namelist"
    problems = []
    for field, value in namelist_paths(namelist).items():
        if not value.startswith(f"../../input/{site}/"):
            problems.append(f"{field}={value} is not under ../../input/{site}/")
        elif not (run_dir / value).resolve().exists():
            problems.append(f"{field}={value} does not exist")
    return problems


def chronology(text: str) -> Dict[str, Any]:
    start = re.search(r"start_date\s*=\s*'(\d{4})", text)
    stop = re.search(r"stop_n\s*=\s*(\d+)", text)
    periods = re.search(r"forc_periods\s*=\s*([\d,\s]+)", text)
    climate = re.search(r"clm_hour_file_in\s*=\s*'[^']*?(\d{4})-(\d{4})\.nc'", text)
    return {
        "start_year": int(start.group(1)) if start else None,
        "stop_n": int(stop.group(1)) if stop else None,
        "forc_periods": re.sub(r"\s", "", periods.group(1)).strip(",") if periods else None,
        "climate_years": f"{climate.group(1)}-{climate.group(2)}" if climate else None,
    }


def site_status(site_root: Path) -> Dict[str, Any]:
    status: Dict[str, Any] = {}
    progress = site_root / "progress.json"
    if progress.exists():
        state = json.loads(progress.read_text())
        status["stages"] = {}
        for name, stage in state["stages"].items():
            steps = [s["status"] for s in stage["steps"].values()]
            active = [s for s in steps if s != "skipped"]
            if "failed" in active:
                value = "failed"
            elif active and all(s in ("done", "warning") for s in active):
                value = "warning" if "warning" in active else "done"
            elif not active or all(s == "pending" for s in active):
                value = "pending" if active else "done"
            else:
                value = "in_progress"
            status["stages"][name] = value
    smoke = site_root / "smoke_run" / "smoke_summary.json"
    if smoke.exists():
        summary = json.loads(smoke.read_text())
        traits = summary.get("trait_check", {})
        status["smoke_run"] = summary.get("status")
        status["plant_traits"] = {
            "errors": traits.get("errors"), "warnings": traits.get("warnings"),
            "corrections": summary.get("trait_fix_status"),
        }
    pft = json.loads((site_root / "input" / f"{site_root.name}_pft_mgmt.json").read_text()) \
        if (site_root / "input" / f"{site_root.name}_pft_mgmt.json").exists() else None
    if pft:
        status["pfts"] = [
            p["pft_type"] for unit in pft.get("topo_units", []) for year in unit.get("years", {}).values()
            for p in year.get("pfts", [])
        ]
    return status


def requirement_problems(site_root: Path, site: str, require_smoke: bool) -> List[str]:
    problems = []
    if not (site_root / "input").is_dir():
        problems.append(f"{site_root}/input missing (run create_site_run_folder.py)")
    if not (site_root / "run_dir" / f"{site}.namelist").exists():
        problems.append(f"{site_root}/run_dir/{site}.namelist missing")
    if require_smoke:
        smoke = site_root / "smoke_run" / "smoke_summary.json"
        if not smoke.exists():
            problems.append("no smoke run (run create_site_smoke_run.py)")
        elif json.loads(smoke.read_text()).get("status") != "passed":
            problems.append("smoke run did not pass")
    return problems


def assemble_site(site: str, result_dir: Path, project: Path, replace_runs: bool) -> Dict[str, Any]:
    site_root = result_dir / site
    target_input = project / "input" / site
    target_run = project / "run_dir" / site
    if target_input.exists():
        shutil.rmtree(target_input)
    target_input.mkdir(parents=True)
    copied = []
    for path in sorted((site_root / "input").iterdir()):
        if path.is_file() and not SKIP_INPUT.search(path.name):
            shutil.copy2(path, target_input / path.name)
            copied.append(path.name)
    if replace_runs and target_run.exists():
        shutil.rmtree(target_run)
    target_run.mkdir(parents=True, exist_ok=True)
    text = (site_root / "run_dir" / f"{site}.namelist").read_text()
    namelist = target_run / f"{site}.namelist"
    namelist.write_text(rewrite_namelist(text, site))
    entry = {
        "site_id": site,
        "source": str(site_root),
        "input": f"input/{site}",
        "run_dir": f"run_dir/{site}",
        "namelist": f"run_dir/{site}/{site}.namelist",
        "input_files": copied,
        "chronology": chronology(text),
        "status": site_status(site_root),
    }
    entry["problems"] = check_case(target_run, site)
    return entry


def render(manifest: Dict[str, Any]) -> str:
    lines = [
        f"# EcoSIM project: {manifest['project']}", "",
        f"Assembled {manifest['assembled']} from `{manifest['result_dir']}`.", "",
        "| Site | PFTs | Start | stop_n | Climate | Data prep | Namelist | Smoke run | Plant traits | Problems |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for site in manifest["sites"]:
        c, s = site["chronology"], site["status"]
        stages = s.get("stages", {})
        traits = s.get("plant_traits", {})
        trait_text = (f"{traits.get('errors')} ERROR, {traits.get('warnings')} WARN"
                      + (f" ({traits['corrections']})" if traits.get("corrections") else "")) if traits else "-"
        lines.append(
            f"| {site['site_id']} | {', '.join(s.get('pfts', [])) or '-'} | {c['start_year']} | {c['stop_n']} | "
            f"{c['climate_years'] or '-'} | {stages.get('data_preparation', '-')} | {stages.get('namelist_creation', '-')} | "
            f"{s.get('smoke_run', '-')} | {trait_text} | {'; '.join(site['problems']) or 'none'} |"
        )
    if manifest["skipped"]:
        lines += ["", "## Not included", ""] + [f"- {k}: {'; '.join(v)}" for k, v in manifest["skipped"].items()]
    lines += ["", "Run a case from its run directory, e.g. "
              "`run_site_full.py --site-id <SITE> --run-dir result/projects/<PROJECT>/run_dir/<SITE>`."]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Project name (directory under result/projects/)")
    parser.add_argument("--sites", nargs="+", required=True, help="Site IDs prepared in result/<SITE_ID>/")
    parser.add_argument("--result-dir", type=Path, default=PROJECT_ROOT / "result")
    parser.add_argument("--project-dir", type=Path, help="Project directory; default <result-dir>/projects/<PROJECT>")
    parser.add_argument("--allow-no-smoke", action="store_true", help="Include sites without a passed smoke run")
    parser.add_argument("--replace-runs", action="store_true", help="Also clear existing output in project run dirs")
    args = parser.parse_args(argv)

    result_dir = args.result_dir.resolve()
    project = (args.project_dir or result_dir / "projects" / args.project).resolve()
    (project / "input").mkdir(parents=True, exist_ok=True)
    (project / "run_dir").mkdir(parents=True, exist_ok=True)

    manifest_path = project / "project.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    sites = {s["site_id"]: s for s in previous.get("sites", [])}
    skipped: Dict[str, List[str]] = {}
    for site in args.sites:
        problems = requirement_problems(result_dir / site, site, not args.allow_no_smoke)
        if problems:
            skipped[site] = problems
            print(f"SKIP {site}: {'; '.join(problems)}", file=sys.stderr)
            continue
        entry = assemble_site(site, result_dir, project, args.replace_runs)
        sites[site] = entry
        flag = "ok" if not entry["problems"] else "PROBLEMS: " + "; ".join(entry["problems"])
        print(f"{site}: {len(entry['input_files'])} input files, namelist {entry['namelist']} ({flag})")

    manifest = {
        "project": args.project,
        "assembled": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "result_dir": str(result_dir),
        "layout": {"input": "input/<SITE_ID>/", "run_dir": "run_dir/<SITE_ID>/<SITE_ID>.namelist"},
        "sites": [sites[k] for k in sorted(sites)],
        "skipped": skipped,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (project / "PROJECT.md").write_text(render(manifest))
    print(f"Project: {project} ({len(manifest['sites'])} site(s); manifest PROJECT.md)")
    bad = [s for s in manifest["sites"] if s["problems"]]
    return 1 if bad or skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
