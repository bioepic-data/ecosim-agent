#!/usr/bin/env python3
"""Site vegetation and site trait evidence for plant-trait checks.

Trait evidence for a site run must follow the site's actual vegetation:

    extract        read species composition and site trait measurements from the
                   AmeriFlux BADM/BIF (site BIF, or the network AMF_AA-Flx/AA-Net
                   BIF filtered to the site) -> <SITE_ID>_site_vegetation.json
    site-evidence  turn site measurements (LMA -> SLA1, foliage N:C -> CNLF) into
                   site-level, per-species evidence in the trait web-evidence JSON
    weight         combine per-species evidence (`species_values`) into each range
                   by canopy share
    lint           check every evidence entry's level, taxa, and citation against
                   the site vegetation

Evidence priority: site measurements > species > genus > functional type. Ranges
built from species values are canopy-weighted; evidence that is not about the
site's taxa must be labelled genus or functional-type.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_LEVELS = ("site", "species", "genus", "functional-type")
DEFAULT_LEAF_C_FRACTION = 0.48
MIN_WEIGHT_COVERAGE = 0.5
WOOD_COMMENT = re.compile(r"sapwood|heartwood|bark|wood", re.IGNORECASE)
NRCS = re.compile(r"^([A-Z]{2,4})([A-Z]{2})(\d*)\s*\(NRCS plant code\)$")
BADM_URL = "https://ameriflux.lbl.gov/data/badm/"


# --- Locating and reading the BIF -------------------------------------------

def find_bif(site: str, explicit: Optional[Path]) -> Optional[Path]:
    if explicit:
        return explicit
    # Site BIF first, then the network BIFs (AA-Net is the superset of AA-Flx); the top
    # level of data/ is checked before a recursive search. Otherwise pass --bif.
    roots = [PROJECT_ROOT / "data", PROJECT_ROOT / "result"]
    names = [f"AMF_{site}_BIF_*.xlsx", f"AMF_{site}_BIF_*.csv", "AMF_AA-Net_BIF_*.xlsx", "AMF_AA-Flx_BIF_*.xlsx"]
    for name in names:
        for prefix in ("", "**/"):
            for root in roots:
                matches = sorted(p for p in glob.glob(str(root / f"{prefix}{name}"), recursive=True) if "/.git/" not in p)
                if matches:
                    return Path(matches[-1])
    return None


def read_bif(path: Path, site: str):
    import pandas as pd

    table = pd.read_csv(path) if path.suffix == ".csv" else pd.read_excel(path)
    table = table[table["SITE_ID"] == site]
    return table


def groups(table, group: str) -> List[Dict[str, Any]]:
    """Rows of one BADM variable group as {VARIABLE: DATAVALUE} records."""
    subset = table[table["VARIABLE_GROUP"] == group]
    records = []
    for _, rows in subset.groupby("GROUP_ID"):
        records.append({str(r.VARIABLE): r.DATAVALUE for r in rows.itertuples()})
    return records


def as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


# --- Taxonomy ------------------------------------------------------------------

def normalize_taxon(name: str) -> str:
    name = re.sub(r"\s+", " ", str(name)).strip()
    fixes = {"Acer rubra": "Acer rubrum"}  # BADM spelling seen at US-UMB
    return fixes.get(name, name)


def nrcs_match(code_text: str, taxa: List[str]) -> Optional[str]:
    """Map an NRCS code such as 'POGR4 (NRCS plant code)' to a site taxon."""
    match = NRCS.match(str(code_text).strip())
    if not match:
        return None
    genus_part, epithet_part = match.group(1), match.group(2)
    for taxon in taxa:
        parts = taxon.split()
        if len(parts) >= 2 and parts[0].upper().startswith(genus_part[:2]) and parts[1].upper().startswith(epithet_part):
            if parts[0].upper().startswith(genus_part):
                return taxon
    for taxon in taxa:
        parts = taxon.split()
        if len(parts) >= 2 and parts[0].upper()[:2] == genus_part[:2] and parts[1].upper()[:2] == epithet_part:
            return taxon
    return None


def taxon_shares(vegetation: Dict[str, Any], taxon: str) -> float:
    """Canopy share covered by a species or genus name."""
    name = normalize_taxon(taxon).lower()
    total = 0.0
    for species in vegetation.get("species", []):
        full = species["taxon"].lower()
        if full == name or full.split()[0] == name:
            total += species["canopy_share"]
    return total


# --- extract -------------------------------------------------------------------

def extract(site: str, bif: Path) -> Dict[str, Any]:
    table = read_bif(bif, site)
    if table.empty:
        raise ValueError(f"{site} not found in {bif}")

    composition = [g for g in groups(table, "GRP_SPP_O") if as_float(g.get("SPP_O_PERC")) is not None]
    dates = sorted({str(g.get("SPP_DATE", "")) for g in composition})
    latest = dates[-1] if dates else ""
    species = [
        {"taxon": normalize_taxon(g["SPP_O"]), "percent": as_float(g["SPP_O_PERC"]), "date": str(g.get("SPP_DATE", ""))}
        for g in composition if str(g.get("SPP_DATE", "")) == latest
    ]
    basis = "SPP_O_PERC (overstory percent)"
    if not species:  # fall back to basal area by species
        basal = [g for g in groups(table, "GRP_BASAL_AREA") if as_float(g.get("BASAL_AREA")) and g.get("BASAL_AREA_SPP")]
        dates = sorted({str(g.get("BASAL_AREA_DATE", "")) for g in basal})
        latest = dates[-1] if dates else ""
        species = [{"taxon": normalize_taxon(g["BASAL_AREA_SPP"]), "percent": as_float(g["BASAL_AREA"]),
                    "date": str(g.get("BASAL_AREA_DATE", ""))} for g in basal if str(g.get("BASAL_AREA_DATE", "")) == latest]
        basis = "BASAL_AREA by species"
    total = sum(s["percent"] for s in species) or 1.0
    for s in species:
        s["canopy_share"] = round(s["percent"] / total, 4)
    species.sort(key=lambda s: -s["canopy_share"])
    taxa = [s["taxon"] for s in species]

    leaf_chem = []
    for g in groups(table, "GRP_BIOMASS_CHEM"):
        carbon, nitrogen = as_float(g.get("BIOMASS_C")), as_float(g.get("BIOMASS_N"))
        if carbon is None or nitrogen is None or carbon <= 0:
            continue
        comment = str(g.get("BIOMASS_COMMENT", "") if g.get("BIOMASS_COMMENT") == g.get("BIOMASS_COMMENT") else "")
        leaf_chem.append({
            "taxon": nrcs_match(g.get("BIOMASS_SPP", ""), taxa) or str(g.get("BIOMASS_SPP")),
            "date": str(g.get("BIOMASS_DATE", "")), "organ": str(g.get("BIOMASS_ORGAN", "")), "comment": comment,
            "BIOMASS_C": carbon, "BIOMASS_N": nitrogen, "N_to_C": round(nitrogen / carbon, 5),
            "foliage": not WOOD_COMMENT.search(comment),
        })
    leaf_c = {}
    for row in leaf_chem:
        if row["foliage"]:
            leaf_c.setdefault(row["taxon"], []).append(row["BIOMASS_C"])

    lma = []
    for g in groups(table, "GRP_LMA"):
        value = as_float(g.get("LMA"))
        if value:
            taxon = nrcs_match(g.get("LMA_SPP", ""), taxa) or str(g.get("LMA_SPP"))
            lma.append({"taxon": taxon, "date": str(g.get("LMA_DATE", "")), "LMA_g_m2": value})

    extras = {}
    offset = groups(table, "GRP_UTC_OFFSET")
    if offset:
        extras["utc_offset_hours"] = as_float(offset[0].get("UTC_OFFSET"))
    snags = [{"snag_mass": as_float(g.get("SNAG_MASS")), "unit": g.get("SNAG_MASS_UNIT"), "date": str(g.get("SNAG_MASS_DATE", "")),
              "comment": g.get("SNAG_MASS_COMMENT")} for g in groups(table, "GRP_SNAG_MASS")]
    if snags:
        extras["snag_mass"] = snags

    return {
        "site_id": site,
        "source_file": str(bif),
        "source_url": BADM_URL,
        "composition_basis": basis,
        "composition_date": latest,
        "species": species,
        "site_traits": {"leaf_chemistry": leaf_chem, "lma": lma, "leaf_c_scale_by_taxon": leaf_c},
        "site_context": extras,
        "notes": [
            "BIOMASS_C and BIOMASS_N share units, so N_to_C is a unit-free gN gC-1 ratio; rows whose comment names "
            "sapwood, heartwood, bark, or wood are wood, the rest are treated as foliage.",
            "LMA is assumed to be leaf dry mass per area (g m-2).",
        ],
    }


# --- site-evidence ----------------------------------------------------------------

def site_species_values(vegetation: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    traits = vegetation["site_traits"]
    source = f"AmeriFlux BADM/BIF for {vegetation['site_id']} ({Path(vegetation['source_file']).name})"
    values: Dict[str, List[Dict[str, Any]]] = {}

    by_taxon: Dict[str, List[float]] = {}
    for row in traits["leaf_chemistry"]:
        if row["foliage"]:
            by_taxon.setdefault(row["taxon"], []).append(row["N_to_C"])
    for taxon, ratios in by_taxon.items():
        values.setdefault("CNLF", []).append({
            "taxon": taxon, "min": min(ratios), "max": max(ratios), "n": len(ratios), "evidence_level": "site",
            "source": f"{source}: GRP_BIOMASS_CHEM foliage BIOMASS_N/BIOMASS_C", "url": vegetation["source_url"],
            "conversion_note": "Unit-free N:C mass ratio of site foliage samples (growing-season dates).",
        })

    lma_by_taxon: Dict[str, List[float]] = {}
    for row in traits["lma"]:
        lma_by_taxon.setdefault(row["taxon"], []).append(row["LMA_g_m2"])
    for taxon, lmas in lma_by_taxon.items():
        carbon = traits["leaf_c_scale_by_taxon"].get(taxon)
        # Site BIOMASS_C is on a percent/10 scale (~4.9 for ~49 % C); fall back to 0.48.
        cfrac = (sum(carbon) / len(carbon)) / 10.0 if carbon else DEFAULT_LEAF_C_FRACTION
        if not 0.35 <= cfrac <= 0.6:
            cfrac = DEFAULT_LEAF_C_FRACTION
        sla = [1.0 / (lma * cfrac) for lma in lmas]
        values.setdefault("SLA1", []).append({
            "taxon": taxon, "min": min(sla), "max": max(sla), "n": len(sla), "evidence_level": "site",
            "source": f"{source}: GRP_LMA", "url": vegetation["source_url"],
            "conversion_note": f"SLA1 = 1/(LMA g DM m-2 x leaf C fraction {cfrac:.3f}); LMA assumed dry mass per area.",
        })
    return values


def apply_site_evidence(evidence: Dict[str, Any], vegetation: Dict[str, Any], pft: str) -> List[str]:
    plant = next((p for p in evidence.setdefault("plants", []) if p.get("pft_code") == pft), None)
    if plant is None:
        plant = {"pft_code": pft, "nz": 1, "ny": 1, "nx": 1, "numeric_ranges": []}
        evidence["plants"].append(plant)
    plant["site_vegetation"] = {
        "source": vegetation["source_file"], "basis": vegetation["composition_basis"], "date": vegetation["composition_date"],
        "species": [{"taxon": s["taxon"], "canopy_share": s["canopy_share"]} for s in vegetation["species"]],
    }
    changed = []
    for variable, species_values in site_species_values(vegetation).items():
        ranges = plant.setdefault("numeric_ranges", [])
        existing = next((r for r in ranges if r["variable"] == variable), None)
        entry = {"variable": variable, "evidence_level": "site", "species_values": species_values,
                 "source": species_values[0]["source"], "url": species_values[0]["url"],
                 "unit": "m2 gC-1" if variable == "SLA1" else "gN gC-1"}
        if existing is not None:
            entry["superseded"] = {k: v for k, v in existing.items() if k != "superseded"}
            ranges[ranges.index(existing)] = entry
        else:
            ranges.append(entry)
        changed.append(variable)
    return changed


# --- weight ----------------------------------------------------------------------

def weight_range(entry: Dict[str, Any], vegetation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Canopy-weighted min/max from per-taxon values; None if coverage is too low."""
    pairs = []
    for value in entry.get("species_values", []):
        share = taxon_shares(vegetation, value["taxon"])
        if share > 0:
            pairs.append((share, float(value["min"]), float(value["max"]), value["taxon"]))
    coverage = sum(p[0] for p in pairs)
    if coverage < MIN_WEIGHT_COVERAGE:
        return {"coverage": round(coverage, 3), "applied": False}
    low = sum(s * lo for s, lo, _, _ in pairs) / coverage
    high = sum(s * hi for s, _, hi, _ in pairs) / coverage
    return {"coverage": round(coverage, 3), "applied": True, "min": low, "max": high,
            "weights": {t: round(s / coverage, 4) for s, _, _, t in pairs}}


def apply_weights(evidence: Dict[str, Any], vegetation: Dict[str, Any]) -> List[str]:
    report = []
    for plant in evidence.get("plants", []):
        for entry in plant.get("numeric_ranges", []) + plant.get("derived_ranges", []):
            if not entry.get("species_values"):
                continue
            result = weight_range(entry, vegetation)
            entry["weighting"] = result
            if result["applied"]:
                entry["min"], entry["max"] = result["min"], result["max"]
                entry["conversion_note"] = (
                    f"Canopy-weighted ({vegetation['composition_basis']}, {vegetation['composition_date']}) "
                    f"over {result['coverage']:.0%} of the canopy from per-taxon values; see species_values."
                )
            report.append(f"{entry['variable']}: coverage {result['coverage']:.0%}"
                          + (f" -> [{entry['min']:.4g}, {entry['max']:.4g}]" if result["applied"] else " (not weighted; kept pooled range)"))
    return report


# --- lint ------------------------------------------------------------------------

def lint(evidence: Dict[str, Any], vegetation: Optional[Dict[str, Any]]) -> List[str]:
    problems = []
    site_taxa = {s["taxon"].lower() for s in (vegetation or {}).get("species", [])}
    site_genera = {t.split()[0] for t in site_taxa}
    site_measured = set(site_species_values(vegetation)) if vegetation else set()
    for plant in evidence.get("plants", []):
        entries = plant.get("numeric_ranges", []) + plant.get("derived_ranges", []) + plant.get("categorical_expectations", [])
        for entry in entries:
            name = entry.get("variable")
            level = entry.get("evidence_level")
            if level not in EVIDENCE_LEVELS:
                problems.append(f"{name}: evidence_level {level!r} not one of {EVIDENCE_LEVELS}")
            if not entry.get("source") or not entry.get("url"):
                problems.append(f"{name}: missing source or url")
            if "min" in entry and not entry.get("conversion_note"):
                problems.append(f"{name}: numeric range without conversion_note")
            for value in entry.get("species_values", []):
                taxon = normalize_taxon(value["taxon"]).lower()
                if site_taxa and taxon not in site_taxa and taxon.split()[0] not in site_genera:
                    problems.append(f"{name}: species value for {value['taxon']} is not a site taxon or genus")
            if name in site_measured and level != "site":
                problems.append(f"{name}: site measurements exist; use `site-evidence` instead of {level} evidence")
    return problems


# --- CLI -------------------------------------------------------------------------

def site_paths(result_dir: Path, site: str) -> Tuple[Path, Path, Path]:
    root = result_dir / site
    folder = root / "input" if (root / "input").exists() else root
    return folder / f"{site}_site_vegetation.json", folder / f"{site}_plant_trait_web_evidence.json", folder


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("extract", "site-evidence", "weight", "lint"))
    parser.add_argument("--site-id", required=True)
    parser.add_argument("--result-dir", type=Path, default=PROJECT_ROOT / "result")
    parser.add_argument("--bif", type=Path, help="Site or network BIF (.xlsx/.csv); default: search data/ and the workspace")
    parser.add_argument("--pft", help="PFT code for site-evidence (e.g. bdlf42); default: the evidence file's first plant")
    args = parser.parse_args(argv)

    vegetation_file, evidence_file, folder = site_paths(args.result_dir.resolve(), args.site_id)
    folder.mkdir(parents=True, exist_ok=True)

    if args.command == "extract":
        bif = find_bif(args.site_id, args.bif)
        if bif is None:
            print("ERROR: no BIF found; pass --bif (site BIF or AMF_AA-Flx BIF).", file=sys.stderr)
            return 2
        vegetation = extract(args.site_id, bif)
        vegetation_file.write_text(json.dumps(vegetation, indent=2, default=str) + "\n")
        print(f"Wrote {vegetation_file} from {bif}")
        for s in vegetation["species"]:
            print(f"  {s['taxon']:28s} {s['canopy_share']:.1%}")
        print(f"  site traits: {len(vegetation['site_traits']['lma'])} LMA, "
              f"{sum(r['foliage'] for r in vegetation['site_traits']['leaf_chemistry'])} foliage chemistry rows; "
              f"context: {sorted(vegetation['site_context'])}")
        return 0

    if not vegetation_file.exists():
        print(f"ERROR: {vegetation_file} not found; run extract first.", file=sys.stderr)
        return 2
    vegetation = json.loads(vegetation_file.read_text())
    evidence = json.loads(evidence_file.read_text()) if evidence_file.exists() else {"plants": []}

    if args.command == "site-evidence":
        pft = args.pft or (evidence["plants"][0]["pft_code"] if evidence["plants"] else None)
        if not pft:
            print("ERROR: pass --pft for a new evidence file.", file=sys.stderr)
            return 2
        changed = apply_site_evidence(evidence, vegetation, pft)
        report = apply_weights(evidence, vegetation)
        evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
        print(f"Site-level evidence for {pft}: {changed}")
        print("\n".join(f"  {line}" for line in report))
    elif args.command == "weight":
        report = apply_weights(evidence, vegetation)
        evidence_file.write_text(json.dumps(evidence, indent=2) + "\n")
        print("\n".join(report) or "No species_values to weight.")
    else:
        problems = lint(evidence, vegetation)
        print("\n".join(f"LINT {p}" for p in problems) or "Evidence lint: clean")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
