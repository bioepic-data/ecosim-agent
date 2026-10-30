#!/usr/bin/env python3
"""Track progress of an EcoSIM site run request in result/<SITE_ID>/.

Stages: data preparation, namelist creation, model simulation, and output
evaluation. `scan` derives step status from the files and quality reports on
disk; `set` records what files cannot show (executable, run completion,
evaluation verdicts). State lives in result/<SITE_ID>/progress.json and is
rendered to result/<SITE_ID>/PROGRESS.md.

    python site_run_tracker.py init --site-id US-UMB
    python site_run_tracker.py scan --site-id US-UMB
    python site_run_tracker.py set  --site-id US-UMB --step full_run --status done --note "exit 0"
    python site_run_tracker.py show --site-id US-UMB
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SKILLS_DIR = PROJECT_ROOT / ".agents" / "skills"
for _path in (PROJECT_ROOT, SKILLS_DIR / "ameriflux-site-info"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

STATUSES = ("pending", "in_progress", "done", "warning", "failed", "skipped")
TRACKER_FILES = ("progress.json", "PROGRESS.md")

STAGES: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    ("data_preparation", "Data preparation", [
        ("site_metadata", "Site metadata (lat/lon, elevation, MAT, Koppen, IGBP)"),
        ("climate_forcing", "Hourly climate forcing NetCDF"),
        ("atmospheric_chemistry", "Precipitation chemistry in climate forcing"),
        ("soil_grid", "Soil/grid NetCDF"),
        ("plant_management", "Plant management NetCDF"),
        ("soil_management", "Soil management NetCDF (managed sites only)"),
    ]),
    ("namelist_creation", "Namelist creation", [
        ("run_folder", "Run folder result/<SITE_ID>/{input,run_dir}"),
        ("namelist", "Namelist with resolvable ../input/ paths"),
        ("chronology", "Chronology: start_date, forc_periods, stop_n"),
    ]),
    ("model_simulation", "Model simulation", [
        ("executable", "EcoSIM executable built and identified"),
        ("smoke_test", "Smoke run (2 simulated years in smoke_run/)"),
        ("plant_traits", "Plant traits (.desc from smoke run) sanity check"),
        ("full_run", "Full simulation"),
    ]),
    ("output_evaluation", "Output evaluation", [
        ("target_extraction", "Targets extracted from h0 output"),
        ("benchmark_comparison", "Comparison with observations/benchmarks"),
        ("evaluation_report", "Evaluation report"),
    ]),
]
STEP_TO_STAGE = {step: stage for stage, _, steps in STAGES for step, _ in steps}

CLIMATE_YEAR_PATTERN = "{site}_ecosim_climate_[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9].nc"
NAMELIST_FILE_FIELDS = (
    "pft_file_in", "grid_file_in", "pft_mgmt_in", "clm_hour_file_in",
    "micpar_file_in", "atm_ghg_in", "soil_mgmt_in", "clm_factor_in",
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Site:
    """Paths of one site's run folder."""

    def __init__(self, result_dir: Path, site_id: str) -> None:
        self.id = site_id
        self.root = result_dir / site_id
        self.input = self.root / "input"
        self.run_dir = self.root / "run_dir"
        self.evaluation = self.root / "evaluation"
        self.smoke = self.root / "smoke_run"
        self.state_file = self.root / "progress.json"
        self.markdown_file = self.root / "PROGRESS.md"

    def find(self, name: str) -> Optional[Path]:
        """A site artifact in input/ (run folder built) or the site root (still preparing)."""
        for folder in (self.input, self.root):
            path = folder / name
            if path.exists():
                return path
        return None

    def glob(self, pattern: str) -> List[Path]:
        matches: List[Path] = []
        for folder in (self.input, self.root):
            if folder.exists():
                matches.extend(sorted(folder.glob(pattern)))
        return matches

    def read_json(self, name: str) -> Optional[Dict[str, Any]]:
        path = self.find(name)
        if path is None:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None


Result = Tuple[str, str, List[str]]  # status, detail, evidence paths


# --- Data preparation -------------------------------------------------------

def scan_site_metadata(site: Site) -> Result:
    path = site.find(f"{site.id}_ecosim_site.json")
    if path is None:
        return "pending", "No site metadata JSON.", []
    data = json.loads(path.read_text())
    detail = (
        f"lat {data.get('ALATG')}, lon {data.get('ALONG')}, elev {data.get('ALTIG')} m, "
        f"MAT {data.get('ATCAG')} degC, Koppen {data.get('IETYPG')}"
    )
    validation = data.get("_location_validation") or live_location_validation(site.id, data)
    if validation.get("status") == "failed":
        return "failed", f"{detail}; location checks failed: {'; '.join(validation.get('errors', []))}", [str(path)]
    if validation.get("status") == "warnings":
        return "warning", f"{detail}; {'; '.join(validation.get('warnings', []))}", [str(path)]
    if not validation:
        return "done", f"{detail} (location checks unavailable)", [str(path)]
    return "done", f"{detail}; location checks passed", [str(path)]


def live_location_validation(site_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Run the ameriflux-site-info location checks on metadata saved without them."""
    try:
        import extract_ameriflux_site_data as site_info

        return site_info.validate_site_location(site_id, data, site_info.find_koppen_raster())
    except Exception:  # pragma: no cover - checks are advisory here
        return {}


def scan_climate_forcing(site: Site) -> Result:
    named = site.glob(CLIMATE_YEAR_PATTERN.format(site=site.id))
    legacy = site.find(f"{site.id}_ecosim_climate.nc")
    if not named and legacy is None:
        return "pending", "No climate NetCDF.", []
    if len(named) > 1:
        return "warning", f"Several year-named climate files: {[p.name for p in named]}.", [str(p) for p in named]
    path = named[0] if named else legacy
    detail = f"{path.name}" + ("" if named else " (name lacks forcing years)")
    status = "done" if named else "warning"
    evidence = [str(path)]

    report = site.read_json(f"{site.id}_era5_quality_report.json")
    if report:
        evidence.append(str(site.find(f"{site.id}_era5_quality_report.json")))
        notes = []
        registration = report.get("time_registration") or {}
        if registration.get("status") in ("misregistered", "ambiguous"):
            status = "warning"
            notes.append(f"time registration {registration['status']}")
        elif registration.get("status") == "corrected":
            notes.append(f"ERA5 shifted {registration.get('applied_shift_hours'):+g} h onto tower clock")
        solar_noon = report.get("solar_noon") or {}
        if solar_noon.get("warning"):
            status = "warning"
            notes.append("ZNOONG mismatch")
        warnings = (report.get("in_situ_comparison") or {}).get("warnings") or []
        if warnings:
            status = "warning"
            notes.append(f"{len(warnings)} tower-comparison warning(s): {', '.join(w['variable'] for w in warnings)}")
        comparison = (report.get("in_situ_comparison") or {}).get("status")
        if comparison:
            notes.append(f"tower comparison {comparison}")
        if notes:
            detail += "; " + "; ".join(notes)
    else:
        detail += "; no ERA5 quality report"
    return status, detail, evidence


def scan_atmospheric_chemistry(site: Site) -> Result:
    report = site.read_json(f"{site.id}_climate_derivation_report.json")
    if report is None:
        return "pending", "No climate derivation report.", []
    evidence = [str(site.find(f"{site.id}_climate_derivation_report.json"))]
    underived = report.get("underived_or_fill_only_variables") or []
    variables = (report.get("chemistry_report") or {}).get("variables") or {}
    derived = sorted(name for name, meta in variables.items() if not meta.get("filled_only"))
    if not derived:
        return "warning", f"No chemistry derived from source data; fill-only: {underived}", evidence
    return "done", f"Derived {', '.join(derived)}; fill-only {underived or 'none'}", evidence


def scan_soil_grid(site: Site) -> Result:
    path = site.find(f"{site.id}_ecosim_grid.nc")
    if path is None:
        return "pending", "No grid NetCDF.", []
    evidence = [str(path)]
    wrapper = site.read_json(f"{site.id}_grid_derivation_report.json") or {}
    report = wrapper.get("report", wrapper)
    detail = f"{path.name}; soil source {report.get('soil_source', 'unknown')}"
    problems = report.get("layer_validation_problems")
    if problems is None:
        try:
            from Tools.create_ecosim_grid_forcing import validate_grid_layers

            problems = validate_grid_layers(str(path))
        except Exception:  # pragma: no cover - validation is advisory here
            problems = None
    edits = site.find(f"{site.id}_grid_manual_edits.json")
    if edits:
        evidence.append(str(edits))
        detail += "; manual edits recorded"
    if problems:
        return "failed", f"{detail}; invalid layers: {'; '.join(problems)}", evidence
    conflict_path = site.find(f'{site.id}_driving_data_conflicts.json')
    conflicts = site.read_json(f'{site.id}_driving_data_conflicts.json') or {}
    if conflict_path:
        evidence.append(str(conflict_path))
    hashes = conflicts.get('input_sha256') or {}
    grid_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    recorded_hash = hashes.get(str(path.resolve()))
    fresh = recorded_hash == grid_hash and all(
        Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
        for p, h in hashes.items())
    if conflicts.get('status') == 'failed':
        return 'failed', f'{detail}; unresolved driving-data conflicts (rerun checks after corrections)', evidence
    if not fresh or conflicts.get('status') not in ('passed', 'warning'):
        return 'warning', f'{detail}; site conflict assessment missing or stale', evidence
    if conflicts.get('status') == 'warning':
        return 'warning', f'{detail}; site evidence / model priors need review', evidence
    if problems is None:
        return "warning", f"{detail} (layer validation unavailable)", evidence
    return "done", f"{detail}; soil layers valid", evidence


def scan_plant_management(site: Site) -> Result:
    path = site.find(f"{site.id}_pft_mgmt.nc")
    if path is None:
        return "pending", "No plant management NetCDF.", []
    detail = path.name
    sidecar = site.read_json(f"{site.id}_pft_mgmt.json")
    if sidecar:
        pfts = []
        for unit in sidecar.get("topo_units", []):
            for year in (unit.get("years") or {}).values():
                pfts += [f"{p['pft_type']} ({p['planting']['Planting_population']} m-2)" for p in year.get("pfts", [])]
        if pfts:
            detail += f"; PFTs {', '.join(pfts)}"
    return "done", detail, [str(path)]


def scan_soil_management(site: Site) -> Result:
    path = site.find(f"{site.id}_soil_mgmt.nc")
    if path is None:
        return "pending", "No soil management NetCDF; set 'skipped' for unmanaged sites.", []
    return "done", path.name, [str(path)]


# --- Namelist creation ------------------------------------------------------

def namelist_file(site: Site) -> Path:
    return site.run_dir / f"{site.id}.namelist"


def scan_run_folder(site: Site) -> Result:
    if not site.input.exists() or not site.run_dir.exists():
        return "pending", "input/ and run_dir/ not created yet.", []
    loose = [p.name for p in site.root.iterdir()
             if p.is_file() and p.name not in TRACKER_FILES and not p.name.startswith(".")]
    if loose:
        return "warning", f"Files outside input/: {loose}", [str(site.root)]
    return "done", f"{len(list(site.input.iterdir()))} files in input/", [str(site.input), str(site.run_dir)]


def scan_namelist(site: Site) -> Result:
    path = namelist_file(site)
    if not path.exists():
        return "pending", "No namelist in run_dir/.", []
    text = path.read_text()
    problems = []
    for field in NAMELIST_FILE_FIELDS:
        match = re.search(rf"^\s*{field}\s*=\s*'([^']*)'", text, flags=re.MULTILINE)
        if not match or match.group(1) == "NO":
            continue
        if not (path.parent / match.group(1)).resolve().exists():
            problems.append(f"{field}={match.group(1)} missing")
    if problems:
        return "failed", "; ".join(problems), [str(path)]
    climate = re.search(r"^\s*clm_hour_file_in\s*=\s*'([^']*)'", text, flags=re.MULTILINE)
    return "done", f"{path.name}; climate {climate.group(1) if climate else '?'}", [str(path)]


def parse_chronology(text: str) -> Optional[Dict[str, Any]]:
    start = re.search(r"start_date\s*=\s*'(\d{4})", text)
    periods = re.search(r"forc_periods\s*=\s*([\d,\s]+)", text)
    stop = re.search(r"stop_n\s*=\s*(\d+)", text)
    if not (start and periods and stop):
        return None
    values = [int(v) for v in periods.group(1).replace(" ", "").strip(",").split(",") if v]
    # EcoSIM get_sim_len: sum over (start, end, cycles) triples of (end - start + 1) * cycles.
    sim_len = sum((abs(values[i + 1] - values[i]) + 1) * values[i + 2] for i in range(0, len(values) - 2, 3))
    return {"start_year": int(start.group(1)), "forc_periods": values, "stop_n": int(stop.group(1)), "sim_len": sim_len}


def scan_chronology(site: Site) -> Result:
    path = namelist_file(site)
    if not path.exists():
        return "pending", "No namelist.", []
    chronology = parse_chronology(path.read_text())
    if chronology is None:
        return "failed", "Could not parse start_date, forc_periods, or stop_n.", [str(path)]
    end_year = chronology["start_year"] + chronology["stop_n"] - 1
    detail = (
        f"{chronology['start_year']}-{end_year}, stop_n {chronology['stop_n']}, "
        f"forc_periods {','.join(map(str, chronology['forc_periods']))}"
    )
    if chronology["stop_n"] != chronology["sim_len"]:
        return "warning", f"{detail}; stop_n differs from forc_periods length {chronology['sim_len']}", [str(path)]
    return "done", detail, [str(path)]


# --- Model simulation and output evaluation ----------------------------------

def history_files(site: Site) -> List[Path]:
    return sorted(site.run_dir.rglob("*.ecosim.h0.*.nc")) if site.run_dir.exists() else []


def smoke_summary(site: Site) -> Optional[Dict[str, Any]]:
    path = site.smoke / "smoke_summary.json"
    return json.loads(path.read_text()) if path.exists() else None


def scan_executable(site: Site) -> Result:
    summary = smoke_summary(site)
    if summary and summary.get("executable"):
        stale = summary.get("stale_sources") or []
        status = "warning" if stale else "done"
        detail = f"{summary['executable']} (used by smoke run)" + (f"; {len(stale)} newer Fortran source(s)" if stale else "")
        return status, detail, [summary["executable"]]
    candidates = [p for p in site.run_dir.glob("*") if p.is_file() and p.stat().st_mode & 0o111] if site.run_dir.exists() else []
    if candidates:
        return "done", f"Executable in run_dir: {candidates[0].name}", [str(candidates[0])]
    return "pending", "Record the executable with: set --step executable --status done --note <path>", []


def scan_smoke_test(site: Site) -> Result:
    summary = smoke_summary(site)
    if summary is None:
        return "pending", "Run create_site_smoke_run.py (2 simulated years in smoke_run/).", []
    detail = (
        f"{summary['years']} yr from {summary['first_year']}: exit {summary['returncode']}, "
        f"nstep {summary['nstep']}/{summary['expected_nstep']}, history {summary['history_scan']['status']}, "
        f"{summary['wall_seconds']} s"
    )
    evidence = [str(site.smoke / "smoke_summary.json")]
    return ("done" if summary["status"] == "passed" else "failed"), detail, evidence


def scan_plant_traits(site: Site) -> Result:
    summary = smoke_summary(site)
    traits = (summary or {}).get("trait_check")
    if not traits:
        return "pending", "No trait check yet; it runs after the smoke run.", []
    if "errors" not in traits:
        return "failed", f"Trait check {traits.get('status')}: {traits.get('detail', '')}", []
    detail = f"{traits['errors']} ERROR, {traits['warnings']} WARN"
    evidence = [traits["report"]] if traits.get("report") else []
    fix_status = summary.get("trait_fix_status")
    rounds = summary.get("trait_rounds") or []
    if fix_status:
        applied = sum(len(r.get("applied", [])) for r in rounds)
        unresolved = rounds[-1].get("unresolved", []) if rounds else []
        detail += f"; corrections {fix_status} ({applied} applied over {len(rounds)} round(s))"
        if unresolved:
            detail += "; unresolved: " + ", ".join(f"{u['variable']} ({u['reason']})" for u in unresolved)
        if fix_status == "fixed":
            return "done", detail, evidence
        return "failed", detail, evidence
    if not traits.get("web_evidence"):
        detail += "; deterministic only, add web evidence and rerun with --trait-only"
    if traits["errors"]:
        return "failed", detail, evidence
    if traits["warnings"] or not traits.get("web_evidence"):
        return "warning", detail, evidence
    return "done", detail, evidence


def scan_full_run(site: Site) -> Result:
    summary_path = site.run_dir / "full_run_summary.json"
    if summary_path.exists():  # written by run_site_full.py
        summary = json.loads(summary_path.read_text())
        attempts = summary.get("attempts", [])
        kinds = " + ".join(a["kind"] for a in attempts)
        last = attempts[-1] if attempts else {}
        detail = (f"{summary['start_year']}+{summary['stop_n']} {summary['stop_option']}: {kinds}; "
                  f"exit {last.get('returncode')}, nstep {last.get('nstep')}")
        if summary.get("status") == "passed":
            return "done", detail, [str(summary_path)]
        return "failed", f"{detail}; {summary.get('error', '')}", [str(summary_path)]
    files = history_files(site)
    if not files:
        return "pending", "No *.ecosim.h0.*.nc history output in run_dir/.", []
    years = sorted({m.group(1) for f in files if (m := re.search(r"\.h0\.(\d{4})-", f.name))})
    span = f" ({years[0]}-{years[-1]} file starts)" if years else ""
    return "in_progress", (
        f"{len(files)} h0 file(s){span}; confirm the exit status, then set --step full_run --status done"
    ), [str(f) for f in files[-3:]]


def scan_evaluation_step(site: Site, patterns: Tuple[str, ...], what: str) -> Result:
    if not site.evaluation.exists():
        return "pending", f"No evaluation/ folder for {what}.", []
    found = sorted({p for pattern in patterns for p in site.evaluation.rglob(pattern)})
    if not found:
        return "pending", f"No {what} in evaluation/.", []
    return "done", ", ".join(p.name for p in found[:4]), [str(p) for p in found]


SCANNERS: Dict[str, Callable[[Site], Result]] = {
    "site_metadata": scan_site_metadata,
    "climate_forcing": scan_climate_forcing,
    "atmospheric_chemistry": scan_atmospheric_chemistry,
    "soil_grid": scan_soil_grid,
    "plant_management": scan_plant_management,
    "soil_management": scan_soil_management,
    "run_folder": scan_run_folder,
    "namelist": scan_namelist,
    "chronology": scan_chronology,
    "executable": scan_executable,
    "smoke_test": scan_smoke_test,
    "plant_traits": scan_plant_traits,
    "full_run": scan_full_run,
    "target_extraction": lambda s: scan_evaluation_step(s, ("*targets*",), "extracted targets"),
    "benchmark_comparison": lambda s: scan_evaluation_step(s, ("*comparison*", "*benchmark*"), "benchmark comparison"),
    "evaluation_report": lambda s: scan_evaluation_step(s, ("*report*",), "evaluation report"),
}


# --- State -------------------------------------------------------------------

def empty_state(site_id: str, request: str = "") -> Dict[str, Any]:
    return {
        "site_id": site_id,
        "request": request,
        "created": now(),
        "updated": now(),
        "stages": {
            stage: {"title": title, "steps": {
                step: {"title": step_title, "status": "pending", "source": "auto",
                       "detail": "", "evidence": [], "note": "", "updated": now()}
                for step, step_title in steps
            }}
            for stage, title, steps in STAGES
        },
    }


def load_state(site: Site) -> Dict[str, Any]:
    """Saved state, with any stages or steps added to the tracker since it was written."""
    template = empty_state(site.id)
    if not site.state_file.exists():
        return template
    state = json.loads(site.state_file.read_text())
    for stage_name, stage in template["stages"].items():
        saved = state["stages"].setdefault(stage_name, stage)
        ordered = {}
        for step_name, step in stage["steps"].items():
            ordered[step_name] = saved["steps"].get(step_name, step)
            ordered[step_name]["title"] = step["title"]
        saved["steps"] = ordered
        saved["title"] = stage["title"]
    state["stages"] = {name: state["stages"][name] for name in template["stages"]}
    return state


def stage_status(steps: Dict[str, Dict[str, Any]]) -> str:
    statuses = [step["status"] for step in steps.values()]
    if "failed" in statuses:
        return "failed"
    active = [s for s in statuses if s != "skipped"]
    if not active or all(s in ("done", "warning") for s in active):
        return "warning" if "warning" in active else "done"
    if all(s == "pending" for s in active):
        return "pending"
    return "in_progress"


def scan(site: Site, state: Dict[str, Any]) -> Dict[str, Any]:
    for stage in state["stages"].values():
        for name, step in stage["steps"].items():
            try:
                status, detail, evidence = SCANNERS[name](site)
            except Exception as exc:  # an unreadable artifact fails its step, not the scan
                status, detail, evidence = "failed", f"Could not check: {type(exc).__name__}: {exc}", []
            if step.get("source") == "manual":
                # Keep the manual status, but show what the files currently say.
                step.update({"auto_status": status, "detail": detail, "evidence": evidence})
                if name == 'soil_grid':
                    # A historical manual approval must not hide a new conflict
                    # or an unverified/stale site-evidence assessment.
                    step.setdefault('manual_status', step['status'])
                    step['status'] = status if status in ('failed', 'warning') else step['manual_status']
                continue
            if (status, detail) != (step["status"], step["detail"]):
                step["updated"] = now()
            step.update({"status": status, "detail": detail, "evidence": evidence})
    state["last_scan"] = now()
    return state


def set_step(state: Dict[str, Any], step_name: str, status: Optional[str], note: Optional[str], auto: bool) -> None:
    if step_name not in STEP_TO_STAGE:
        raise SystemExit(f"Unknown step {step_name}; choose from {sorted(STEP_TO_STAGE)}")
    step = state["stages"][STEP_TO_STAGE[step_name]]["steps"][step_name]
    if auto:
        step["source"] = "auto"
    if status:
        step.update({"status": status, "source": "manual"})
    if note is not None:
        step["note"] = note
    step["updated"] = now()


def next_actions(state: Dict[str, Any]) -> List[str]:
    actions = []
    for stage in state["stages"].values():
        for name, step in stage["steps"].items():
            if step["status"] in ("failed", "pending", "in_progress", "warning"):
                actions.append(f"{stage['title']} / {step['title']}: {step['status']} - {step['detail'] or step['note']}")
    return actions


def render(state: Dict[str, Any]) -> str:
    lines = [
        f"# EcoSIM run progress: {state['site_id']}",
        "",
        f"Request: {state.get('request') or '(not recorded)'}  ",
        f"Updated: {state['updated']} (last scan {state.get('last_scan', 'never')})",
        "",
        "| Stage | Status |",
        "| --- | --- |",
    ]
    for stage in state["stages"].values():
        lines.append(f"| {stage['title']} | {stage_status(stage['steps'])} |")
    for stage in state["stages"].values():
        lines += ["", f"## {stage['title']}: {stage_status(stage['steps'])}", "",
                  "| Step | Status | Detail | Note |", "| --- | --- | --- | --- |"]
        for step in stage["steps"].values():
            source = " (manual)" if step.get("source") == "manual" else ""
            detail = (step.get("detail") or "").replace("|", "/")
            note = (step.get("note") or "").replace("|", "/")
            lines.append(f"| {step['title']} | {step['status']}{source} | {detail} | {note} |")
    actions = next_actions(state)
    lines += ["", "## Open items", ""] + ([f"- {a}" for a in actions] if actions else ["- None"])
    return "\n".join(lines) + "\n"


def save(site: Site, state: Dict[str, Any]) -> None:
    state["updated"] = now()
    site.root.mkdir(parents=True, exist_ok=True)
    site.state_file.write_text(json.dumps(state, indent=2) + "\n")
    site.markdown_file.write_text(render(state))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "scan", "set", "show"):
        command = sub.add_parser(name)
        command.add_argument("--site-id", required=True)
        command.add_argument("--result-dir", type=Path, default=Path("result"))
        if name == "init":
            command.add_argument("--request", default="", help="The user request being tracked")
        if name == "set":
            command.add_argument("--step", required=True, choices=sorted(STEP_TO_STAGE))
            command.add_argument("--status", choices=STATUSES)
            command.add_argument("--note")
            command.add_argument("--auto", action="store_true", help="Return the step to automatic detection")
        if name == "show":
            command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    site = Site(args.result_dir.resolve(), args.site_id)
    if args.command == "init":
        state = load_state(site) if site.state_file.exists() else empty_state(site.id, args.request)
        if args.request:
            state["request"] = args.request
        state = scan(site, state)
    elif args.command == "scan":
        state = scan(site, load_state(site))
    elif args.command == "set":
        state = load_state(site)
        set_step(state, args.step, args.status, args.note, args.auto)
        if args.auto:
            state = scan(site, state)
    else:
        if not site.state_file.exists():
            print(f"No tracker for {site.id}; run init first.", file=sys.stderr)
            return 1
        state = load_state(site)
        print(json.dumps(state, indent=2) if args.json else render(state), end="")
        return 0

    save(site, state)
    print(render(state), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
