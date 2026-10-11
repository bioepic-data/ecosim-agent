"""Refresh the ecosim-site-run-tracker progress files from preprocessing scripts.

Scripts that produce site run inputs call ``update_progress`` when they finish,
so result/<SITE_ID>/progress.json and PROGRESS.md always reflect what is on
disk. The tracker is created if it does not exist. Failures are reported as a
warning and never fail the calling script. Set ECOSIM_PROGRESS_TRACKER=0 to
disable.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional

TRACKER_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".agents" / "skills" / "ecosim-site-run-tracker" / "scripts" / "site_run_tracker.py"
)
DISABLE_ENV = "ECOSIM_PROGRESS_TRACKER"


def _load_tracker():
    spec = importlib.util.spec_from_file_location("site_run_tracker", TRACKER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update_progress(site_id: Optional[str], result_dir: os.PathLike = "result") -> Optional[Path]:
    """Scan the site's run folder and save the tracker; return PROGRESS.md or None."""
    if not site_id or os.environ.get(DISABLE_ENV, "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    try:
        tracker = _load_tracker()
        site = tracker.Site(Path(result_dir).resolve(), site_id)
        if not site.root.exists():
            return None
        state = tracker.scan(site, tracker.load_state(site))
        tracker.save(site, state)
        return site.markdown_file
    except Exception as exc:  # the tracker must never break data preparation
        print(f"WARNING: progress tracker not updated for {site_id}: {exc}", file=sys.stderr)
        return None
