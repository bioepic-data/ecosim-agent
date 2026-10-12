#!/usr/bin/env python3
"""Plan and apply literature-backed corrections for plant-trait sanity-check findings.

Corrections are written to a PFT-parameter NetCDF through ParEditor
(ecosim-pftpar-editor); plant_trait.*.desc files are never edited.

Severe findings are every ERROR, every value outside a web-evidence range, and
every categorical identity mismatch. The model-formulation-sensitive Jmax:Vcmax
diagnostic and other warnings are reported, not corrected.

Every numeric correction comes from a literature range in the web-evidence JSON
(``numeric_ranges``, plus ``derived_ranges`` for constraints such as Rubisco
specificity that combine several parameters); the range's source, URL,
evidence level, and conversion note are copied into the correction record.
A severe finding without such a range is left unresolved rather than given an
invented value. Rules:

- SLA1: 0.25 x the typical value (midpoint of the literature range).
- Other numeric parameters: the midpoint ("medium value") of the literature range.
- Rubisco kinetics (VCMX/VOMX/XKCO2/XKO2): keep VCMX, XKCO2, and XKO2 and set
  VOMX so VCMX*XKO2/(VOMX*XKCO2) equals the midpoint of the literature
  RUBISCO_SPECIFICITY range.
- Categorical identity (IEBTYP, ISNTYP, ...): the EcoSIM enum code the checker
  expects (ElmIDMod), with the categorical web evidence as supporting source.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

SLA_TYPICAL_FRACTION = 0.25
RULE_TOLERANCE = 0.01
SPECIFICITY_KEY = "RUBISCO_SPECIFICITY"
RUBISCO_PARAMETER = "VCMX/VOMX/XKCO2/XKO2"
EXPECTED_CODE = re.compile(r"is expected to use .*\((\d+)\); found")
PROJECT_ROOT = Path(__file__).resolve().parents[4]


def is_severe(finding: Dict[str, Any]) -> bool:
    message = finding.get("message", "")
    if finding.get("severity") == "ERROR":
        return True
    return (
        "outside web-evidence range" in message
        or EXPECTED_CODE.search(message) is not None
        or "does not contain web-evidence expectation" in message
    )


def plant_evidence(evidence: Dict[str, Any], pft: str) -> Dict[str, Any]:
    for plant in evidence.get("plants", []):
        if plant.get("pft_code") == pft:
            return plant
    return {}


def literature_ranges(plant: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    ranges = {}
    for key in ("numeric_ranges", "derived_ranges"):
        for entry in plant.get(key, []):
            ranges[entry["variable"]] = entry
    return ranges


def literature_target(variable: str, entry: Dict[str, Any]) -> Tuple[float, str]:
    typical = (float(entry["min"]) + float(entry["max"])) / 2.0
    if variable == "SLA1":
        return SLA_TYPICAL_FRACTION * typical, f"{SLA_TYPICAL_FRACTION} x typical {typical:.4g} (literature midpoint)"
    return typical, f"midpoint of literature range [{entry['min']}, {entry['max']}]"


def citation(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not entry:
        return {}
    keys = ("source", "url", "evidence_level", "conversion_note", "unit")
    return {key: entry[key] for key in keys if key in entry}


def plan_corrections(
    report: Dict[str, Any], evidence: Dict[str, Any], current: Dict[str, Dict[str, float]]
) -> Dict[str, Any]:
    """Return {corrections, unresolved, satisfied_by_rule} for the report's findings.

    ``current`` maps pft code -> {parameter: current value in the PFT file}.
    """
    corrections: Dict[Tuple[str, str], Dict[str, Any]] = {}
    unresolved: List[Dict[str, Any]] = []
    satisfied: List[Dict[str, Any]] = []
    severe = [f for f in report.get("findings", []) if is_severe(f)]

    # Categorical codes first, so web-evidence label mismatches they resolve are not unresolved.
    for finding in severe:
        match = EXPECTED_CODE.search(finding["message"])
        if not match:
            continue
        pft, variable = finding["pft_code"], finding["parameter"]
        categorical = next(
            (c for c in plant_evidence(evidence, pft).get("categorical_expectations", []) if c["variable"] == variable),
            None,
        )
        corrections[(pft, variable)] = {
            "pft": pft, "variable": variable, "old": current.get(pft, {}).get(variable), "new": int(match.group(1)),
            "rule": "EcoSIM enum code expected by the checker (ElmIDMod)",
            "evidence": citation(categorical) or {"source": "ElmIDMod enum via plant-trait checker"},
        }

    for finding in severe:
        pft, variable, message = finding["pft_code"], finding["parameter"], finding["message"]
        values = current.get(pft, {})
        ranges = literature_ranges(plant_evidence(evidence, pft))
        if EXPECTED_CODE.search(message):
            continue
        if "does not contain web-evidence expectation" in message:
            if (pft, variable) not in corrections:
                unresolved.append({"finding": message, "pft": pft, "variable": variable,
                                   "reason": "no EcoSIM enum code identified for this category"})
            continue
        if variable == RUBISCO_PARAMETER:
            entry = ranges.get(SPECIFICITY_KEY)
            if entry is None:
                unresolved.append({"finding": message, "pft": pft, "variable": variable,
                                   "reason": f"no literature {SPECIFICITY_KEY} range in web evidence"})
                continue
            if not all(key in values for key in ("VCMX", "XKO2", "XKCO2")):
                unresolved.append({"finding": message, "pft": pft, "variable": variable,
                                   "reason": "VCMX, XKO2, or XKCO2 not readable from the PFT parameter file"})
                continue
            target, rule = literature_target(SPECIFICITY_KEY, entry)
            vomx = values["VCMX"] * values["XKO2"] / (target * values["XKCO2"])
            corrections[(pft, "VOMX")] = {
                "pft": pft, "variable": "VOMX", "old": values.get("VOMX"), "new": vomx,
                "rule": f"VOMX so VCMX*XKO2/(VOMX*XKCO2) = {target:.4g} ({rule}); VCMX, XKCO2, XKO2 kept",
                "evidence": citation(entry),
            }
            continue
        entry = ranges.get(variable)
        if entry is None:
            unresolved.append({"finding": message, "pft": pft, "variable": variable,
                               "reason": "no literature-backed range for this parameter in web evidence"})
            continue
        target, rule = literature_target(variable, entry)
        old = values.get(variable)
        if variable == "SLA1" and old is not None and abs(old - target) <= RULE_TOLERANCE * target:
            satisfied.append({"pft": pft, "variable": variable, "value": old, "rule": rule})
            continue
        corrections[(pft, variable)] = {
            "pft": pft, "variable": variable, "old": old, "new": target, "rule": rule, "evidence": citation(entry),
        }

    return {"corrections": list(corrections.values()), "unresolved": unresolved, "satisfied_by_rule": satisfied}


def read_pft_values(pftpar_file: Path, pft: str, variables: List[str]) -> Dict[str, float]:
    from netCDF4 import Dataset

    with Dataset(str(pftpar_file)) as dataset:
        codes = ["".join(c.decode() if isinstance(c, bytes) else c for c in row).strip()
                 for row in dataset.variables["pfts"][:].filled(b" ")]
        index = codes.index(pft)
        return {v: float(dataset.variables[v][index]) for v in variables if v in dataset.variables}


@contextlib.contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def apply_corrections(pftpar_file: Path, corrections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Write corrections with ParEditor (its JSONL change log lands beside the file) and read them back."""
    sys.path.insert(0, str(PROJECT_ROOT / "applications" / "notebooks"))
    from scripts.ParamEditor import ParEditor

    pftpar_file = Path(pftpar_file).resolve()
    by_pft: Dict[str, Dict[str, Any]] = {}
    for item in corrections:
        by_pft.setdefault(item["pft"], {})[item["variable"]] = item["new"]
    with working_directory(pftpar_file.parent), contextlib.redirect_stdout(open(os.devnull, "w")):
        editor = ParEditor(pftparfile=str(pftpar_file))
        for pft, pars in by_pft.items():
            editor.PlantParamModify(pft, pars, iscale=False, verbose=False)
    applied = []
    for item in corrections:
        readback = read_pft_values(pftpar_file, item["pft"], [item["variable"]]).get(item["variable"])
        applied.append({**item, "readback": readback})
    return applied
