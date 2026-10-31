#!/usr/bin/env python3
"""Run the full EcoSIM simulation for a site run folder; continue once from restart on failure.

Runs result/<SITE_ID>/run_dir/<SITE_ID>.namelist. The run succeeds when EcoSIM
exits 0, the last restart reports every hourly step of the planned years, and
history output has no NaN/Inf. If the run fails partway, the script tries one
continued run from the last restart before reporting an error: it sets
continue_run=.true. in the run namelist and runs it again. EcoSIM then reads the
restart named in run_dir/rpointer.esim, walks the forc_periods plan from the
original start skipping years before the restart date (drivers/ecosim/ecosim.F90),
and ends at the end of the plan, so stop_n is unchanged. The elapsed-step count
is not restored on continuation, so the continued run's last nstep may count from
the restart; success also requires the restart file for the planned end date.

Logs are run_dir/<SITE_ID>.run.log and <SITE_ID>.continue.log; after a
continuation the namelist keeps continue_run=.true. (a fresh run resets it). A summary is written to
run_dir/full_run_summary.json and the progress tracker is refreshed. Run it
detached for long simulations, e.g.

    nohup python run_site_full.py --site-id US-UMB > /dev/null 2>&1 &
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]
sys.path.insert(0, str(SCRIPT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import create_site_smoke_run as smoke  # noqa: E402  (shared executable, nstep, and history helpers)
from Tools.run_progress import update_progress  # noqa: E402
from Tools.case_preflight import enforce as enforce_preflight, PreflightError  # noqa: E402

RESTART_DATE = re.compile(r"\.ecosim\.r\.(\d{4})-(\d{2})-(\d{2})")
LOG_TAIL_LINES = 25


def namelist_setting(text: str, name: str) -> Optional[str]:
    match = re.search(rf"^\s*{name}\s*=\s*'?([^'!\s]+)'?", text, flags=re.MULTILINE)
    return match.group(1) if match else None


def planned_nstep(start_year: int, stop_n: int, stop_option: str) -> Optional[int]:
    if stop_option == "nyears":
        return smoke.expected_nstep(start_year, stop_n)
    if stop_option == "ndays":
        return 24 * stop_n
    if stop_option == "nsteps":
        return stop_n
    return None  # nmonths: not checked


def restart_from_pointer(run_dir: Path) -> Optional[Path]:
    pointer = run_dir / "rpointer.esim"
    if not pointer.exists():
        return None
    name = pointer.read_text().strip().splitlines()[0].strip() if pointer.read_text().strip() else ""
    if not name:
        return None
    path = (run_dir / name).resolve()
    return path if path.exists() else None


def completed_years(restart: Path, start_year: int) -> Optional[int]:
    match = RESTART_DATE.search(restart.name)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    if (month, day) != (1, 1):
        return None  # yearly restarts are written at 1 January
    return year - start_year


def set_continue_run(text: str, value: bool) -> str:
    flag = ".true." if value else ".false."
    return re.sub(r"^(\s*)continue_run\s*=.*$", rf"\g<1>continue_run={flag}", text, flags=re.MULTILINE)


def end_restart_exists(run_dir: Path, start_year: int, stop_n: int) -> bool:
    """Yearly restart written at the planned end, <case>.ecosim.r.<start+stop_n>-01-01-*.nc."""
    return any(run_dir.glob(f"*.ecosim.r.{start_year + stop_n:04d}-01-01-*.nc"))


def run_once(exe: Path, run_dir: Path, namelist: str, log_name: str) -> Dict[str, Any]:
    enforce_preflight(run_dir / namelist)
    started = time.time()
    log_path = run_dir / log_name
    with open(log_path, "w") as log:
        result = subprocess.run([str(exe), namelist], cwd=run_dir, stdout=log, stderr=subprocess.STDOUT)
    text = log_path.read_text(errors="replace")
    return {
        "namelist": namelist,
        "log": str(log_path),
        "returncode": result.returncode,
        "nstep": smoke.last_nstep(text),
        "wall_seconds": round(time.time() - started, 1),
        "log_tail": text.splitlines()[-LOG_TAIL_LINES:],
    }


def judge(attempt: Dict[str, Any], expected: Optional[int], run_dir: Path,
          accepted_nsteps: Optional[set] = None, end_restart: Optional[bool] = None) -> None:
    reasons = []
    if attempt["returncode"] != 0:
        reasons.append(f"exit {attempt['returncode']}")
    accepted = accepted_nsteps if accepted_nsteps is not None else ({expected} if expected is not None else None)
    if accepted is not None and attempt["nstep"] not in accepted:
        reasons.append(f"nstep {attempt['nstep']} not in planned {sorted(accepted)}")
    if end_restart is False:
        reasons.append("no restart at the planned end date")
    if not reasons:
        history = smoke.scan_history(run_dir)
        attempt["history_scan"] = history
        if history["status"] == "failed":
            reasons.append(f"history scan failed: {history['detail']}")
    attempt["status"] = "failed" if reasons else "passed"
    attempt["reasons"] = reasons


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--result-dir", type=Path, default=PROJECT_ROOT / "result")
    parser.add_argument("--exe", type=Path, help=f"EcoSIM executable; default ${smoke.EXE_ENV} or newest build")
    parser.add_argument("--no-continue", action="store_true", help="Report a failure without trying a continued run")
    parser.add_argument("--run-dir", type=Path,
                        help="Run directory holding <SITE_ID>.namelist (e.g. a multi-site project's run_dir/<SITE_ID>); "
                             "default result/<SITE_ID>/run_dir")
    args = parser.parse_args(argv)

    site = args.site_id
    site_root = args.result_dir.resolve() / site
    run_dir = args.run_dir.resolve() if args.run_dir else site_root / "run_dir"
    namelist_path = run_dir / f"{site}.namelist"
    if not namelist_path.exists():
        print(f"ERROR: {namelist_path} not found; create the run folder first.", file=sys.stderr)
        return 2
    exe = smoke.find_executable(args.exe)
    if exe is None:
        print("ERROR: EcoSIM executable not found; build EcoSIM or pass --exe.", file=sys.stderr)
        return 2

    text = namelist_path.read_text()
    fresh_text = set_continue_run(text, False)
    try:
        enforce_preflight(namelist_path, text=fresh_text, site_id=site)
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    start_year = smoke.start_year(text)
    stop_n = int(namelist_setting(text, "stop_n") or 0)
    stop_option = namelist_setting(text, "stop_option") or "nyears"
    expected = planned_nstep(start_year, stop_n, stop_option)
    summary: Dict[str, Any] = {
        "site_id": site, "executable": str(exe), "stale_sources": smoke.stale_sources(exe)[:20],
        "start_year": start_year, "stop_n": stop_n, "stop_option": stop_option, "expected_nstep": expected,
        "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "attempts": [],
    }
    if re.search(r"^\s*continue_run\s*=\s*\.true\.", text, flags=re.MULTILINE):
        text = set_continue_run(text, False)  # a fresh run starts from start_date
        namelist_path.write_text(text)

    print(f"Full run: {site} {start_year} + {stop_n} {stop_option} with {exe}")
    attempt = run_once(exe, run_dir, namelist_path.name, f"{site}.run.log")
    attempt["kind"] = "fresh"
    judge(attempt, expected, run_dir)
    summary["attempts"].append(attempt)
    print(f"  fresh run: {attempt['status']} (exit {attempt['returncode']}, nstep {attempt['nstep']}/{expected})")

    if attempt["status"] == "failed" and not args.no_continue:
        restart = restart_from_pointer(run_dir)
        done = completed_years(restart, start_year) if restart else None
        if stop_option != "nyears":
            summary["continue_skipped"] = f"continuation implemented for stop_option='nyears' only ({stop_option})"
        elif restart is None or done is None or done <= 0:
            summary["continue_skipped"] = "no usable yearly restart in rpointer.esim (failed before the first year end)"
        elif done >= stop_n:
            summary["continue_skipped"] = f"restart {restart.name} already at the planned end"
        else:
            continued_text = set_continue_run(text, True)
            try:
                enforce_preflight(namelist_path, text=continued_text, site_id=site,
                                  output=run_dir / 'continue.preflight.json')
            except PreflightError as exc:
                summary['continue_skipped'] = str(exc)
                (run_dir / 'full_run_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            namelist_path.write_text(continued_text)
            hours_done = smoke.expected_nstep(start_year, done)
            print(f"  continuing from {restart.name} with continue_run=.true. ({done} year(s) done)")
            again = run_once(exe, run_dir, namelist_path.name, f"{site}.continue.log")
            again.update({"kind": "continued", "restart": restart.name, "completed_years_before": done})
            accepted = {expected, expected - hours_done} if expected is not None else None
            judge(again, expected, run_dir, accepted_nsteps=accepted,
                  end_restart=end_restart_exists(run_dir, start_year, stop_n))
            summary["attempts"].append(again)
            print(f"  continued run: {again['status']} (exit {again['returncode']}, nstep {again['nstep']}/{expected})")

    final = summary["attempts"][-1]
    summary["status"] = "passed" if final["status"] == "passed" else "failed"
    summary["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if summary["status"] == "failed":
        summary["error"] = "; ".join(
            f"{a['kind']}: {', '.join(a['reasons'])}" for a in summary["attempts"]
        ) + (f"; continue skipped: {summary['continue_skipped']}" if "continue_skipped" in summary else "")
    (run_dir / "full_run_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    if summary["status"] == "failed":
        print(f"ERROR: full run failed: {summary['error']}", file=sys.stderr)
        print(f"Last {LOG_TAIL_LINES} lines of {final['log']}:", file=sys.stderr)
        print("\n".join(final["log_tail"]), file=sys.stderr)
    progress = update_progress(site, args.result_dir) if args.run_dir is None else None
    if progress:
        print(f"Progress tracker: {progress}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
