#!/usr/bin/env python3
"""Smoke-run a site run folder for a few simulated years, then evaluate its plant traits.

Runs in result/<SITE_ID>/smoke_run/, a sibling of run_dir/, so the namelist's
../input/ paths resolve unchanged and run_dir/ stays clean for the full run:

    result/<SITE_ID>/smoke_run/
        <SITE_ID>.namelist                    run_dir namelist with stop_option='nyears', stop_n=YEARS
        smoke.log                             model output
        *.ecosim.h0.*.nc, *.ecosim.r.*.nc     history and restart output
        <case>.plant_trait.desc.<year>        trait table written by EcoSIM
        <SITE_ID>_plant_trait_check.{json,md} ecosim-plant-trait-sanity-check result
        smoke_summary.json                    run and trait-check summary

The run passes when EcoSIM exits 0, reaches every hourly step of the requested
years, and writes history without NaN/Inf. The trait check uses strict physiology
and, when present, <SITE_ID>_plant_trait_web_evidence.json from smoke_run/ or
input/. Severe trait findings (errors, values outside literature ranges, wrong
categories) are corrected in input/<SITE_ID>_ecosim_pftpar.nc from literature
ranges in that evidence file (trait_corrections.py) and the smoke run is
repeated until none remain, two consecutive iterations change no parameter,
or --max-rounds is reached; findings without a literature range stay unresolved. The exit status reflects the run.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
ECOSIM_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Tools.run_progress import update_progress  # noqa: E402
from Tools.case_preflight import enforce as enforce_preflight, PreflightError  # noqa: E402

TRAIT_CHECKER = PROJECT_ROOT / ".agents" / "skills" / "ecosim-plant-trait-sanity-check" / "scripts" / "check_plant_trait_desc.py"
HISTORY_CHECKER = ECOSIM_ROOT / ".agents" / "skills" / "ecosim-smoke-run" / "scripts" / "check_history.py"
EXE_ENV = "ECOSIM_EXE"
DEFAULT_YEARS = 2
DEFAULT_CPU_SECONDS = 3600
DEFAULT_MAX_ROUNDS = 4
# Stop correcting after this many consecutive iterations that change no parameter.
NO_CHANGE_LIMIT = 2


def find_executable(explicit: Optional[Path]) -> Optional[Path]:
    """--exe, $ECOSIM_EXE, or the newest build/*/{local/bin,bin}/ecosim.f90.x."""
    if explicit:
        return explicit if explicit.exists() else None
    if os.environ.get(EXE_ENV):
        path = Path(os.environ[EXE_ENV])
        return path if path.exists() else None
    candidates = [*ECOSIM_ROOT.glob("build/*/local/bin/ecosim.f90.x"), *ECOSIM_ROOT.glob("build/*/bin/ecosim.f90.x")]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def stale_sources(exe: Path) -> List[str]:
    """Fortran sources modified after the executable was built."""
    built = exe.stat().st_mtime
    sources = ECOSIM_ROOT / "f90src"
    if not sources.exists():
        return []
    return sorted(str(p.relative_to(ECOSIM_ROOT)) for p in sources.rglob("*.F90") if p.stat().st_mtime > built)


def smoke_namelist(text: str, years: int) -> str:
    """Shorten a run namelist to `years` simulated years from its start_date."""
    text = re.sub(r"^(\s*)stop_n\s*=.*$", rf"\g<1>stop_n={years}", text, flags=re.MULTILINE)
    text = re.sub(r"^(\s*)stop_option\s*=.*$", r"\g<1>stop_option='nyears'", text, flags=re.MULTILINE)
    return re.sub(r"^(\s*)continue_run\s*=.*$", r"\g<1>continue_run=.false.", text, flags=re.MULTILINE)


def start_year(text: str) -> int:
    match = re.search(r"start_date\s*=\s*'(\d{4})", text)
    if not match:
        raise ValueError("start_date not found in namelist")
    return int(match.group(1))


def case_name(text: str, default: str) -> str:
    match = re.search(r"^\s*case_name\s*=\s*'([^']+)'", text, flags=re.MULTILINE)
    return match.group(1) if match else default


def expected_nstep(first_year: int, years: int) -> int:
    """Hourly steps in `years` calendar years, counting leap days as EcoSIM does."""
    return 24 * sum(366 if calendar.isleap(year) else 365 for year in range(first_year, first_year + years))


def last_nstep(log_text: str) -> int:
    steps = re.findall(r"wrote out restart data at nstep\s*=\s*(\d+)", log_text)
    return int(steps[-1]) if steps else 0


def clear_outputs(smoke_dir: Path, site: str) -> None:
    """Remove model output from a previous smoke run, keeping trait web evidence."""
    keep = {f"{site}_plant_trait_web_evidence.json"}
    for path in smoke_dir.iterdir():
        if path.name in keep:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def run_model(exe: Path, smoke_dir: Path, namelist: str, cpu_seconds: int) -> Dict[str, Any]:
    def limit_cpu() -> None:  # macOS has no `timeout`; RLIMIT_CPU caps runaway runs
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))

    started = time.time()
    with open(smoke_dir / "smoke.log", "w") as log:
        result = subprocess.run([str(exe), namelist], cwd=smoke_dir, stdout=log, stderr=subprocess.STDOUT,
                                preexec_fn=limit_cpu)
    return {"returncode": result.returncode, "wall_seconds": round(time.time() - started, 1)}


def scan_history(smoke_dir: Path) -> Dict[str, Any]:
    if not HISTORY_CHECKER.exists():
        return {"status": "unavailable", "detail": f"{HISTORY_CHECKER} not found"}
    result = subprocess.run([sys.executable, str(HISTORY_CHECKER), str(smoke_dir)], capture_output=True, text=True)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {"status": "clean" if result.returncode == 0 else "failed", "detail": lines[-1] if lines else ""}


def find_web_evidence(site_root: Path, site: str, explicit: Optional[Path]) -> Optional[Path]:
    if explicit:
        return explicit
    for folder in ("smoke_run", "input"):
        path = site_root / folder / f"{site}_plant_trait_web_evidence.json"
        if path.exists():
            return path
    return None


def check_traits(smoke_dir: Path, site: str, case: str, evidence: Optional[Path]) -> Dict[str, Any]:
    """Run the strict plant-trait sanity check on the .desc file EcoSIM wrote."""
    descs = sorted(smoke_dir.glob(f"{case}.plant_trait.desc.*"))
    if not descs:
        return {"status": "missing", "detail": f"No {case}.plant_trait.desc.* written by the smoke run"}
    reports = []
    for desc in descs:
        base = [sys.executable, str(TRAIT_CHECKER), str(desc), "--strict-physiology"]
        if evidence:
            base += ["--web-evidence", str(evidence)]
        result = subprocess.run(base + ["--json"], capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"status": "checker_failed", "detail": result.stderr.strip()[-500:], "desc_file": str(desc)}
        reports.append(data)
    # Rotation years may activate different crops. Keep every year's findings,
    # including a later crop that does not occur in the first description file.
    report = dict(reports[0])
    report["source_files"] = [str(p) for p in descs]
    report["findings"] = [finding for data in reports for finding in data.get("findings", [])]
    report["plants"] = [plant for data in reports for plant in data.get("plants", [])]
    report["finding_counts"] = {severity: sum(data.get("finding_counts", {}).get(severity, 0) for data in reports)
                                for severity in ("ERROR", "WARN")}
    report["total_blocks"] = sum(data.get("total_blocks", 0) for data in reports)
    report["checked_blocks"] = sum(data.get("checked_blocks", 0) for data in reports)
    report_json = smoke_dir / f"{site}_plant_trait_check.json"
    report_md = smoke_dir / f"{site}_plant_trait_check.md"
    report_json.write_text(json.dumps(report, indent=2) + "\n")
    report_md.write_text("# Plant-trait review across smoke years\n\n" +
                         "\n".join(f"- {f['severity']} {f.get('pft_code', '?')} {f['parameter']}: {f['message']}"
                                   for f in report['findings']) + "\n")
    counts = report["finding_counts"]
    return {
        "status": "errors" if counts.get("ERROR") else ("warnings" if counts.get("WARN") else "clean"),
        "desc_file": str(descs[0]),
        "desc_files": [str(p) for p in descs],
        "errors": counts.get("ERROR", 0),
        "warnings": counts.get("WARN", 0),
        "web_evidence": str(evidence) if evidence else None,
        "report": str(report_md),
        "findings": [
            f"{f['severity']} {f['parameter']}: {f['message'].split(' (taxon=')[0]}" for f in report.get("findings", [])
        ],
    }


def value_changed(old: Any, new: Any) -> bool:
    if old is None or new is None:
        return old != new
    return abs(float(new) - float(old)) > 1.0e-9 * max(1.0, abs(float(old)))


def load_corrections_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("trait_corrections", TRAIT_CHECKER.parent / "trait_corrections.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def namelist_pft_file(namelist: Path) -> Optional[Path]:
    match = re.search(r"^\s*pft_file_in\s*=\s*'([^']+)'", namelist.read_text(), flags=re.MULTILINE)
    return (namelist.parent / match.group(1)).resolve() if match else None


def ensure_site_pftpar(site_root: Path, site: str) -> Path:
    """Site-specific PFT parameter copy that trait corrections edit; namelists point to it.

    The shared input_data file and other sites are never edited.
    """
    run_namelist = site_root / "run_dir" / f"{site}.namelist"
    site_file = site_root / "input" / f"{site}_ecosim_pftpar.nc"
    if not site_file.exists():
        source = namelist_pft_file(run_namelist)
        if source is None or not source.exists():
            raise FileNotFoundError(f"pft_file_in of {run_namelist} not found")
        shutil.copy2(source, site_file)
    for namelist in (run_namelist, site_root / "smoke_run" / f"{site}.namelist"):
        if namelist.exists():
            text = re.sub(r"^(\s*)pft_file_in\s*=.*$", rf"\g<1>pft_file_in='../input/{site_file.name}'",
                          namelist.read_text(), flags=re.MULTILINE)
            namelist.write_text(text)
    return site_file


def smoke_round(args, site: str, site_root: Path, smoke_dir: Path, text: str, case: str) -> Dict[str, Any]:
    """One smoke run plus trait check."""
    exe = find_executable(args.exe)
    if exe is None:
        raise FileNotFoundError("EcoSIM executable not found; build EcoSIM or pass --exe.")
    production = site_root / 'run_dir' / f'{site}.namelist'
    fresh = re.sub(r'^(\s*)continue_run\s*=.*$', r'\g<1>continue_run=.false.', text, flags=re.MULTILINE)
    enforce_preflight(production, text=fresh, site_id=site,
                      output=site_root / 'production.preflight.json')
    enforce_preflight(smoke_dir / f'{site}.namelist', text=smoke_namelist(text, args.years),
                      run_cwd=smoke_dir, site_id=site, output=site_root / 'smoke.preflight.json')
    smoke_dir.mkdir(parents=True, exist_ok=True)
    clear_outputs(smoke_dir, site)
    (smoke_dir / f"{site}.namelist").write_text(smoke_namelist(text, args.years))
    first = start_year(text)
    expected = expected_nstep(first, args.years)
    stale = stale_sources(exe)
    if stale:
        print(f"WARNING: {len(stale)} Fortran source(s) newer than {exe}, e.g. {stale[:3]}", file=sys.stderr)
    print(f"Smoke run: {args.years} year(s) from {first} with {exe}")
    run = run_model(exe, smoke_dir, f"{site}.namelist", args.cpu_seconds)
    nstep = last_nstep((smoke_dir / "smoke.log").read_text(errors="replace"))
    history = scan_history(smoke_dir)
    passed = run["returncode"] == 0 and nstep == expected and history["status"] != "failed"
    summary = {
        "site_id": site, "status": "passed" if passed else "failed", "executable": str(exe),
        "stale_sources": stale[:20], "years": args.years, "first_year": first,
        "returncode": run["returncode"], "nstep": nstep, "expected_nstep": expected,
        "wall_seconds": run["wall_seconds"], "history_scan": history, "log": str(smoke_dir / "smoke.log"),
        "pft_file": str(namelist_pft_file(site_root / "run_dir" / f"{site}.namelist")),
    }
    summary["trait_check"] = check_traits(smoke_dir, site, case, find_web_evidence(site_root, site, args.web_evidence))
    return summary


def report(summary: Dict[str, Any]) -> None:
    print(
        f"Run: {summary['status']} (exit {summary['returncode']}, nstep {summary['nstep']}/{summary['expected_nstep']}, "
        f"history {summary['history_scan']['status']}, {summary['wall_seconds']} s)"
    )
    traits = summary["trait_check"]
    print(
        f"Plant traits: {traits['status']}"
        + (f" ({traits['errors']} ERROR, {traits['warnings']} WARN; "
           f"web evidence {'used' if traits.get('web_evidence') else 'not provided'})" if "errors" in traits else "")
    )
    for finding in traits.get("findings", []):
        print(f"  {finding}")


def correction_round(site: str, site_root: Path, smoke_dir: Path, summary: Dict[str, Any], evidence: Optional[Path]):
    """Plan literature-backed corrections for the latest trait check; apply them to the site PFT file."""
    corrections_module = load_corrections_module()
    report_data = json.loads((smoke_dir / f"{site}_plant_trait_check.json").read_text())
    evidence_data = json.loads(evidence.read_text()) if evidence else {}
    pft_file = Path(summary["pft_file"]) if summary.get("pft_file") else None
    pfts = sorted({f["pft_code"] for f in report_data.get("findings", [])})
    if pft_file is None or not pft_file.exists():
        severe = [f for f in report_data.get("findings", []) if corrections_module.is_severe(f)]
        return {"corrections": [], "satisfied_by_rule": [], "unresolved": [
            {"finding": f["message"], "pft": f["pft_code"], "variable": f["parameter"],
             "reason": f"PFT parameter file {pft_file} not found"} for f in severe]}
    variables = ["VCMX", "VOMX", "XKCO2", "XKO2", "SLA1", "CHL", "ETMX", "RUBP", "ICTYP"] + sorted(
        {f["parameter"] for f in report_data.get("findings", []) if "/" not in f["parameter"] and "*" not in f["parameter"]}
    )
    current = {}
    for pft in pfts:
        try:
            current[pft] = corrections_module.read_pft_values(pft_file, pft, variables)
        except ValueError:  # PFT code not in the parameter file
            current[pft] = {}
    plan = corrections_module.plan_corrections(report_data, evidence_data, current)
    if plan["corrections"]:
        site_file = ensure_site_pftpar(site_root, site)
        plan["pft_file"] = str(site_file)
        plan["applied"] = corrections_module.apply_corrections(site_file, plan["corrections"])
    return plan


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--result-dir", type=Path, default=PROJECT_ROOT / "result")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help="Simulated years (default 2)")
    parser.add_argument("--exe", type=Path, help=f"EcoSIM executable; default ${EXE_ENV} or newest build")
    parser.add_argument("--cpu-seconds", type=int, default=DEFAULT_CPU_SECONDS, help="CPU-time cap for the run")
    parser.add_argument("--web-evidence", type=Path, help="Trait web-evidence JSON (default: auto-discover)")
    parser.add_argument("--trait-only", action="store_true",
                        help="Re-evaluate the existing smoke-run .desc without running or correcting")
    parser.add_argument("--no-fix-traits", action="store_true",
                        help="Report trait findings without correcting the site PFT parameter file")
    parser.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                        help="Maximum smoke-run/correction rounds (default 4)")
    args = parser.parse_args(argv)

    site = args.site_id
    site_root = args.result_dir.resolve() / site
    run_namelist = site_root / "run_dir" / f"{site}.namelist"
    smoke_dir = site_root / "smoke_run"
    if not run_namelist.exists():
        print(f"ERROR: {run_namelist} not found; create the run folder first.", file=sys.stderr)
        return 2
    case = case_name(run_namelist.read_text(), site)
    summary_file = smoke_dir / "smoke_summary.json"
    corrections_log = site_root / "input" / f"{site}_plant_trait_corrections.json"

    if args.trait_only:
        if not summary_file.exists():
            print("ERROR: no previous smoke run to re-evaluate.", file=sys.stderr)
            return 2
        summary = json.loads(summary_file.read_text())
        summary["trait_check"] = check_traits(smoke_dir, site, case, find_web_evidence(site_root, site, args.web_evidence))
        rounds = summary.get("trait_rounds", [])
    else:
        rounds = []
        try:
            summary = smoke_round(args, site, site_root, smoke_dir, run_namelist.read_text(), case)
        except (FileNotFoundError, PreflightError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        report(summary)
        no_change_streak = 0
        while not args.no_fix_traits and summary["status"] == "passed" and "errors" in summary["trait_check"]:
            evidence = find_web_evidence(site_root, site, args.web_evidence)
            plan = correction_round(site, site_root, smoke_dir, summary, evidence)
            changed = [a for a in plan.get("applied", []) if value_changed(a["old"], a["readback"])]
            plan["changed"] = len(changed)
            rounds.append({"round": len(rounds) + 1, **plan})
            for item in plan.get("applied", []):
                print(f"Corrected {item['pft']} {item['variable']}: {item['old']} -> {item['readback']} ({item['rule']}; "
                      f"{item['evidence'].get('source', 'no source')})")
            for item in plan["unresolved"]:
                print(f"UNRESOLVED {item['pft']} {item['variable']}: {item['reason']}")
            if not plan["corrections"] and not plan["unresolved"]:
                break  # every severe finding fixed
            no_change_streak = 0 if changed else no_change_streak + 1
            if no_change_streak >= NO_CHANGE_LIMIT:
                print(f"No parameter changed in {NO_CHANGE_LIMIT} consecutive iterations; stopping.")
                break
            if len(rounds) >= args.max_rounds:
                break
            if changed:
                try:
                    summary = smoke_round(args, site, site_root, smoke_dir, run_namelist.read_text(), case)
                except PreflightError as exc:
                    print(f"ERROR: {exc}", file=sys.stderr)
                    update_progress(site, args.result_dir)
                    return 2
                report(summary)
            # Unchanged parameters give an identical smoke run, so the next iteration only
            # re-plans (picking up any evidence added to the web-evidence file meanwhile).
        if rounds:
            last = rounds[-1]
            fixed = not last["corrections"] and not last["unresolved"]
            summary["trait_fix_status"] = (
                "fixed" if fixed else ("unresolved" if no_change_streak >= NO_CHANGE_LIMIT else "max_rounds")
            )
            history = json.loads(corrections_log.read_text()) if corrections_log.exists() else []
            history.append({"site_id": site, "rounds": rounds})
            corrections_log.write_text(json.dumps(history, indent=2) + "\n")
        summary["trait_rounds"] = rounds

    summary_file.write_text(json.dumps(summary, indent=2) + "\n")
    if args.trait_only:
        report(summary)
    if summary.get("trait_fix_status"):
        print(f"Trait corrections: {summary['trait_fix_status']} after {len(rounds)} round(s); log {corrections_log}")
    if "report" in summary["trait_check"]:
        print(f"Trait report: {summary['trait_check']['report']}")

    progress = update_progress(site, args.result_dir)
    if progress:
        print(f"Progress tracker: {progress}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
