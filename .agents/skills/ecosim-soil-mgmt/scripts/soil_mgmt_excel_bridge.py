#!/usr/bin/env python3
"""Convert and inspect EcoSIM soil-management NetCDF, Excel, and JSON files."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import netCDF4 as nc
import numpy as np


CONTROL_SHEET = "control"
TOPO_SHEET = "topo_units"
SELECTOR_SHEET = "year_selectors"
FILES_SHEET = "event_files"
FERT_SHEET = "fertilizer"
TILL_SHEET = "tillage"
IRRIG_SHEET = "irrigation"

SELECTOR_COLUMNS = ["year", "topou", "fertf", "tillf", "irrigf"]
TOPO_COLUMNS = ["topou", "NH1", "NV1", "NH2", "NV2"]
FILES_COLUMNS = ["category", "file"]

FERT_FIELDS = [
    "DDMMYYYY",
    "NH4Soil",
    "NH3Soil",
    "UreaSoil",
    "NO3Soil",
    "NH4Band",
    "NH3Band",
    "UreaBand",
    "NO3Band",
    "MonocalciumPhosphateSoil",
    "MonocalciumPhosphateBand",
    "hydroxyapatite",
    "LimeStone",
    "Gypsum",
    "PlantResC",
    "PlantResN",
    "PlantResP",
    "ManureC",
    "ManureN",
    "ManureP",
    "AppDepth",
    "BandWidth",
    "PO4Soil",
    "PO4Band",
    "IsAmendtypFert",
    "IsAmendtypResidual",
    "IsAmendtypManure",
]

FERT_REAL_FIELDS = set(FERT_FIELDS[1:24])
FERT_INT_FIELDS = set(FERT_FIELDS[24:])
FERT_N_FIELDS = [
    "NH4Soil",
    "NH3Soil",
    "UreaSoil",
    "NO3Soil",
    "NH4Band",
    "NH3Band",
    "UreaBand",
    "NO3Band",
]
FERT_P_FIELDS = [
    "MonocalciumPhosphateSoil",
    "MonocalciumPhosphateBand",
    "hydroxyapatite",
    "PlantResP",
    "ManureP",
    "PO4Soil",
    "PO4Band",
]

TILL_FIELDS = ["DDMMYYYY", "iSoilDisturbType", "DepzCorp"]
IRRIG_SCHEDULED_FIELDS = [
    "DDMMYYYY",
    "RR",
    "JST",
    "JEN",
    "WDPTH",
    "PHQ",
    "NH4",
    "NO3",
    "H2PO4",
    "Al",
    "Fe",
    "Ca",
    "Mg",
    "Na",
    "K",
    "SO4",
    "Cl",
]
IRRIG_AUTO_FIELDS = [
    "DST",
    "DEN",
    "iIrrigOpt",
    "FIRRA",
    "CIRRA",
    "DIRRA",
    "WDPTH",
    "PHQ",
    "NH4",
    "NO3",
    "H2PO4",
    "Al",
    "Fe",
    "Ca",
    "Mg",
    "Na",
    "K",
    "SO4",
    "Cl",
]

IRRIG_COLUMNS = [
    "file",
    "event_index",
    "mode",
    "DDMMYYYY",
    "DST",
    "DEN",
    "RR",
    "JST",
    "JEN",
    "iIrrigOpt",
    "FIRRA",
    "CIRRA",
    "DIRRA",
    "WDPTH",
    "PHQ",
    "NH4",
    "NO3",
    "H2PO4",
    "Al",
    "Fe",
    "Ca",
    "Mg",
    "Na",
    "K",
    "SO4",
    "Cl",
]

STRING10 = 10
STRING24 = 24
STRING128 = 128


def clean_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def text_value(value: Any) -> str:
    value = clean_value(value)
    if value is None:
        return ""
    return str(value).strip()


def int_value(value: Any, field: str) -> int:
    s = text_value(value)
    if s == "":
        raise ValueError(f"Missing integer value for {field}")
    return int(float(s))


def real_value(value: Any, field: str) -> float:
    s = text_value(value)
    if s == "":
        raise ValueError(f"Missing real value for {field}")
    return float(s)


def number_value(value: Any, field: str) -> int | float:
    val = real_value(value, field)
    return int(val) if val.is_integer() else val


def date_value(value: Any) -> str:
    s = text_value(value)
    if s == "":
        return ""
    if re.fullmatch(r"\d+(\.0+)?", s):
        s = str(int(float(s)))
    s = s.zfill(8)
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError(f"Invalid DDMMYYYY value: {value!r}")
    return s


def fmt_number(value: Any) -> str:
    value = clean_value(value)
    if value == "":
        return "0"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".15g")
    return text_value(value)


def pad_string(value: Any, width: int) -> str:
    s = text_value(value)
    if len(s) > width:
        raise ValueError(f"String exceeds {width} characters: {s!r}")
    return s.ljust(width)


def decode_chars(var: nc.Variable) -> np.ndarray:
    return np.asarray(nc.chartostring(var[:]).astype(str))


def write_fixed_strlen(var: nc.Variable, data: np.ndarray, strlen: int) -> None:
    # Fortran TRIM removes spaces, not NULs. In particular, NO must compare
    # equal to the inactive selector rather than become a variable name.
    values = np.asarray(data)
    padded = [pad_string(value, strlen) for value in values.reshape(-1)]
    arr = np.asarray(padded, dtype=f"S{strlen}").reshape(values.shape)
    var[:] = arr.view("S1").reshape(arr.shape + (strlen,))


def parse_line(line: str, fields: list[str], real_fields: set[str], int_fields: set[str]) -> dict[str, Any]:
    parts = [p for p in re.split(r"[\s,]+", text_value(line)) if p]
    if len(parts) != len(fields):
        raise ValueError(f"Expected {len(fields)} fields, found {len(parts)} in {line!r}")
    record: dict[str, Any] = {}
    for field, value in zip(fields, parts):
        if field == "DDMMYYYY" or field in {"DST", "DEN"}:
            record[field] = date_value(value)
        elif field in int_fields:
            record[field] = int_value(value, field)
        elif field in real_fields:
            record[field] = real_value(value, field)
        else:
            record[field] = number_value(value, field)
    return record


def parse_fertilizer(line: str) -> dict[str, Any]:
    return parse_line(line, FERT_FIELDS, FERT_REAL_FIELDS, FERT_INT_FIELDS)


def parse_tillage(line: str) -> dict[str, Any]:
    return parse_line(line, TILL_FIELDS, {"DepzCorp"}, {"iSoilDisturbType"})


def parse_irrigation(line: str, mode: str) -> dict[str, Any]:
    mode = mode.lower()
    if mode == "auto":
        record = parse_line(
            line,
            IRRIG_AUTO_FIELDS,
            set(IRRIG_AUTO_FIELDS) - {"DST", "DEN", "iIrrigOpt"},
            {"iIrrigOpt"},
        )
        record["mode"] = "auto"
        return record
    record = parse_line(
        line,
        IRRIG_SCHEDULED_FIELDS,
        set(IRRIG_SCHEDULED_FIELDS) - {"DDMMYYYY", "JST", "JEN"},
        {"JST", "JEN"},
    )
    record["mode"] = "scheduled"
    return record


def build_fertilizer(record: dict[str, Any]) -> str:
    return " ".join(date_value(record[field]) if field == "DDMMYYYY" else fmt_number(record.get(field, 0)) for field in FERT_FIELDS)


def build_tillage(record: dict[str, Any]) -> str:
    return " ".join(date_value(record[field]) if field == "DDMMYYYY" else fmt_number(record.get(field, 0)) for field in TILL_FIELDS)


def build_irrigation(record: dict[str, Any], file_name: str = "") -> str:
    mode = text_value(record.get("mode")).lower()
    if not mode:
        mode = "auto" if file_name.startswith("auto") else "scheduled"
    fields = IRRIG_AUTO_FIELDS if mode == "auto" else IRRIG_SCHEDULED_FIELDS
    return " ".join(date_value(record[field]) if field in {"DDMMYYYY", "DST", "DEN"} else fmt_number(record.get(field, 0)) for field in fields)


def get_years(cfg: dict[str, Any]) -> list[int]:
    if cfg.get("years"):
        return [int(y) for y in cfg["years"]]
    years = set()
    for topo in cfg.get("topo_units", []):
        years.update(int(y) for y in topo.get("years", {}).keys())
    if not years:
        raise ValueError("No years found")
    return sorted(years)


def is_no_selector(value: Any) -> bool:
    return text_value(value).upper() in {"", "NO"}


def nc_to_config(path: Path) -> dict[str, Any]:
    with nc.Dataset(path) as ds:
        years = [int(y) for y in ds.variables["year"][:]]
        fertf = decode_chars(ds.variables["fertf"])
        tillf = decode_chars(ds.variables["tillf"])
        irrigf = decode_chars(ds.variables["irrigf"])

        topo_units = []
        fert_refs: set[str] = set()
        till_refs: set[str] = set()
        irrig_refs: set[str] = set()
        for itu in range(len(ds.dimensions["ntopou"])):
            topo = {
                "NH1": int(ds.variables["NH1"][itu]),
                "NV1": int(ds.variables["NV1"][itu]),
                "NH2": int(ds.variables["NH2"][itu]),
                "NV2": int(ds.variables["NV2"][itu]),
                "years": {},
            }
            for iy, year in enumerate(years):
                selectors = {
                    "fertf": text_value(fertf[iy, itu]),
                    "tillf": text_value(tillf[iy, itu]),
                    "irrigf": text_value(irrigf[iy, itu]),
                }
                topo["years"][str(year)] = selectors
                if not is_no_selector(selectors["fertf"]):
                    fert_refs.add(selectors["fertf"])
                if not is_no_selector(selectors["tillf"]):
                    till_refs.add(selectors["tillf"])
                if not is_no_selector(selectors["irrigf"]):
                    irrig_refs.add(selectors["irrigf"])
            topo_units.append(topo)

        fertilizer_files: dict[str, list[dict[str, Any]]] = {}
        tillage_files: dict[str, list[dict[str, Any]]] = {}
        irrigation_files: dict[str, list[dict[str, Any]]] = {}

        for name, var in ds.variables.items():
            lname = text_value(getattr(var, "long_name", "")).lower()
            if name in {"fertf", "tillf", "irrigf"}:
                continue
            if name in fert_refs or name.startswith("fertf_") or ("fertilization file" in lname):
                fertilizer_files[name] = [parse_fertilizer(s) for s in decode_chars(var) if text_value(s)]
            elif name in till_refs or name.startswith("tillf_") or ("tillage file" in lname):
                tillage_files[name] = [parse_tillage(s) for s in decode_chars(var) if text_value(s)]
            elif name in irrig_refs or name.startswith("irrigf_") or name.startswith("auto") or ("irrigation file" in lname):
                mode = "auto" if name.startswith("auto") else "scheduled"
                irrigation_files[name] = [parse_irrigation(s, mode) for s in decode_chars(var) if text_value(s)]

        cfg: dict[str, Any] = {
            "years": years,
            "topo_units": topo_units,
            "fertilizer_files": fertilizer_files,
            "tillage_files": tillage_files,
            "irrigation_files": irrigation_files,
        }
        if "description" in ds.ncattrs():
            cfg["description"] = str(ds.getncattr("description"))
        return cfg


def collect_referenced_files(cfg: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    fert_refs: set[str] = set()
    till_refs: set[str] = set()
    irrig_refs: set[str] = set()
    for topo in cfg.get("topo_units", []):
        for selectors in topo.get("years", {}).values():
            if not is_no_selector(selectors.get("fertf")):
                fert_refs.add(text_value(selectors.get("fertf")))
            if not is_no_selector(selectors.get("tillf")):
                till_refs.add(text_value(selectors.get("tillf")))
            if not is_no_selector(selectors.get("irrigf")):
                irrig_refs.add(text_value(selectors.get("irrigf")))
    return fert_refs, till_refs, irrig_refs


def validate_selector_maps(cfg: dict[str, Any]) -> None:
    fert_refs, till_refs, irrig_refs = collect_referenced_files(cfg)
    missing = []
    for name in sorted(fert_refs - set(cfg.get("fertilizer_files", {}))):
        missing.append(f"fertf selector references missing fertilizer file variable {name!r}")
    for name in sorted(till_refs - set(cfg.get("tillage_files", {}))):
        missing.append(f"tillf selector references missing tillage file variable {name!r}")
    for name in sorted(irrig_refs - set(cfg.get("irrigation_files", {}))):
        missing.append(f"irrigf selector references missing irrigation file variable {name!r}")
    if missing:
        raise ValueError("; ".join(missing))


def config_to_nc(cfg: dict[str, Any], path: Path) -> None:
    validate_selector_maps(cfg)
    years = get_years(cfg)
    topo_units = cfg["topo_units"]
    fert_files = cfg.get("fertilizer_files", {})
    till_files = cfg.get("tillage_files", {})
    irrig_files = cfg.get("irrigation_files", {})

    nfert = max(12, *(len(v) for v in fert_files.values())) if fert_files else 12
    ntill = max(12, *(len(v) for v in till_files.values())) if till_files else 12
    nirri = max(24, *(len(v) for v in irrig_files.values())) if irrig_files else 0

    with nc.Dataset(path, "w", format="NETCDF3_64BIT_OFFSET") as ds:
        ds.createDimension("ntopou", len(topo_units))
        ds.createDimension("year", len(years))
        ds.createDimension("string10", STRING10)
        ds.createDimension("nfert", nfert)
        ds.createDimension("string128", STRING128)
        ds.createDimension("ntill", ntill)
        ds.createDimension("string24", STRING24)
        if nirri:
            ds.createDimension("nirri", nirri)

        ds.description = cfg.get("description", "soil management data created by ecosim-soil-mgmt\n")

        for name, long_name in {
            "NH1": "starting column from the west for a topo unit",
            "NV1": "ending column at the east for a topo unit",
            "NH2": "starting row from the north for a topo unit",
            "NV2": "ending row at the south for a topo unit",
        }.items():
            var = ds.createVariable(name, "i4", ("ntopou",))
            var.units = "None"
            var.long_name = long_name
            var[:] = np.array([int(t[name]) for t in topo_units], dtype=np.int32)

        year_var = ds.createVariable("year", "i4", ("year",))
        year_var.long_name = "year AD"
        year_var[:] = np.array(years, dtype=np.int32)

        selector_data = {
            "fertf": np.full((len(years), len(topo_units)), "NO", dtype=f"U{STRING10}"),
            "tillf": np.full((len(years), len(topo_units)), "NO", dtype=f"U{STRING10}"),
            "irrigf": np.full((len(years), len(topo_units)), "NO", dtype=f"U{STRING10}"),
        }
        for itu, topo in enumerate(topo_units):
            year_blocks = topo.get("years", {})
            for iy, year in enumerate(years):
                selectors = year_blocks.get(str(year), {})
                for key in selector_data:
                    selector_data[key][iy, itu] = text_value(selectors.get(key, "NO")) or "NO"

        for name, long_name in {
            "fertf": "Fertilization info for a topo unit",
            "tillf": "Tillage info for a topo unit",
            "irrigf": "Irrigation info for a topo unit",
        }.items():
            var = ds.createVariable(name, "S1", ("year", "ntopou", "string10"))
            var.units = "None"
            var.long_name = long_name
            write_fixed_strlen(var, selector_data[name], STRING10)

        for name, records in fert_files.items():
            var = ds.createVariable(name, "S1", ("nfert", "string128"))
            var.long_name = "fertilization file"
            lines = [pad_string(build_fertilizer(r), STRING128) for r in records]
            lines.extend([" " * STRING128] * (nfert - len(lines)))
            write_fixed_strlen(var, np.asarray(lines, dtype=f"U{STRING128}"), STRING128)

        for name, records in till_files.items():
            var = ds.createVariable(name, "S1", ("ntill", "string24"))
            var.long_name = "tillage file"
            lines = [pad_string(build_tillage(r), STRING24) for r in records]
            lines.extend([" " * STRING24] * (ntill - len(lines)))
            write_fixed_strlen(var, np.asarray(lines, dtype=f"U{STRING24}"), STRING24)

        for name, records in irrig_files.items():
            if not nirri:
                continue
            var = ds.createVariable(name, "S1", ("nirri", "string128"))
            var.long_name = "irrigation file"
            lines = [pad_string(build_irrigation(r, name), STRING128) for r in records]
            lines.extend([" " * STRING128] * (nirri - len(lines)))
            write_fixed_strlen(var, np.asarray(lines, dtype=f"U{STRING128}"), STRING128)


def config_to_sheet_rows(cfg: dict[str, Any]) -> dict[str, list[list[Any]]]:
    sheets: dict[str, list[list[Any]]] = {}
    sheets[CONTROL_SHEET] = [["key", "value"], ["description", cfg.get("description", "")]]

    topo_rows = [TOPO_COLUMNS]
    selector_rows = [SELECTOR_COLUMNS]
    for itu, topo in enumerate(cfg.get("topo_units", []), start=1):
        topo_rows.append([itu, topo["NH1"], topo["NV1"], topo["NH2"], topo["NV2"]])
        for year in sorted(int(y) for y in topo.get("years", {}).keys()):
            selectors = topo["years"][str(year)]
            selector_rows.append([year, itu, selectors.get("fertf", "NO"), selectors.get("tillf", "NO"), selectors.get("irrigf", "NO")])
    sheets[TOPO_SHEET] = topo_rows
    sheets[SELECTOR_SHEET] = selector_rows

    file_rows = [FILES_COLUMNS]
    for category, mapping in [
        ("fertilizer", cfg.get("fertilizer_files", {})),
        ("tillage", cfg.get("tillage_files", {})),
        ("irrigation", cfg.get("irrigation_files", {})),
    ]:
        for file_name in sorted(mapping):
            file_rows.append([category, file_name])
    sheets[FILES_SHEET] = file_rows

    fert_rows = [["file", "event_index", *FERT_FIELDS]]
    for file_name, records in sorted(cfg.get("fertilizer_files", {}).items()):
        for i, record in enumerate(records, start=1):
            fert_rows.append([file_name, i, *[record.get(field, "") for field in FERT_FIELDS]])
    sheets[FERT_SHEET] = fert_rows

    till_rows = [["file", "event_index", *TILL_FIELDS]]
    for file_name, records in sorted(cfg.get("tillage_files", {}).items()):
        for i, record in enumerate(records, start=1):
            till_rows.append([file_name, i, *[record.get(field, "") for field in TILL_FIELDS]])
    sheets[TILL_SHEET] = till_rows

    irrig_rows = [IRRIG_COLUMNS]
    for file_name, records in sorted(cfg.get("irrigation_files", {}).items()):
        for i, record in enumerate(records, start=1):
            irrig_rows.append([file_name, i, record.get("mode", "auto" if file_name.startswith("auto") else "scheduled"), *[record.get(c, "") for c in IRRIG_COLUMNS[3:]]])
    sheets[IRRIG_SHEET] = irrig_rows
    return sheets


def rows_to_dicts(rows: list[list[Any]], sheet: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    headers = [text_value(h) for h in rows[0]]
    if not headers:
        raise ValueError(f"Sheet {sheet!r} has no header row")
    out = []
    for row in rows[1:]:
        if not any(text_value(v) for v in row):
            continue
        out.append({headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))})
    return out


def sheet_rows_to_config(sheets: dict[str, list[list[Any]]]) -> dict[str, Any]:
    for required in [CONTROL_SHEET, TOPO_SHEET, SELECTOR_SHEET]:
        if required not in sheets:
            raise ValueError(f"Workbook is missing required sheet: {required}")

    control = {text_value(r.get("key")): r.get("value", "") for r in rows_to_dicts(sheets[CONTROL_SHEET], CONTROL_SHEET)}

    topo_by_index: dict[int, dict[str, Any]] = {}
    for row in rows_to_dicts(sheets[TOPO_SHEET], TOPO_SHEET):
        topou = int_value(row.get("topou"), "topou")
        topo_by_index[topou] = {
            "NH1": int_value(row.get("NH1"), "NH1"),
            "NV1": int_value(row.get("NV1"), "NV1"),
            "NH2": int_value(row.get("NH2"), "NH2"),
            "NV2": int_value(row.get("NV2"), "NV2"),
            "years": {},
        }

    years = set()
    for row in rows_to_dicts(sheets[SELECTOR_SHEET], SELECTOR_SHEET):
        year = int_value(row.get("year"), "year")
        topou = int_value(row.get("topou"), "topou")
        if topou not in topo_by_index:
            raise ValueError(f"year_selectors references missing topou {topou}")
        years.add(year)
        topo_by_index[topou]["years"][str(year)] = {
            "fertf": text_value(row.get("fertf")) or "NO",
            "tillf": text_value(row.get("tillf")) or "NO",
            "irrigf": text_value(row.get("irrigf")) or "NO",
        }

    fertilizer_files: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    tillage_files: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    irrigation_files: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for row in rows_to_dicts(sheets.get(FILES_SHEET, []), FILES_SHEET):
        category = text_value(row.get("category")).lower()
        file_name = text_value(row.get("file"))
        if not file_name:
            continue
        if category in {"fertilizer", "fertf"}:
            fertilizer_files.setdefault(file_name, [])
        elif category in {"tillage", "tillf"}:
            tillage_files.setdefault(file_name, [])
        elif category in {"irrigation", "irrigf"}:
            irrigation_files.setdefault(file_name, [])
        else:
            raise ValueError(f"Unknown event_files category {category!r} for {file_name!r}")

    for row in rows_to_dicts(sheets.get(FERT_SHEET, []), FERT_SHEET):
        file_name = text_value(row.get("file"))
        if not file_name:
            continue
        record = {}
        for field in FERT_FIELDS:
            if field == "DDMMYYYY":
                record[field] = date_value(row.get(field))
            elif field in FERT_REAL_FIELDS:
                record[field] = real_value(row.get(field), field)
            else:
                record[field] = int_value(row.get(field), field)
        fertilizer_files.setdefault(file_name, []).append((int_value(row.get("event_index"), "event_index"), record))

    for row in rows_to_dicts(sheets.get(TILL_SHEET, []), TILL_SHEET):
        file_name = text_value(row.get("file"))
        if not file_name:
            continue
        record = {
            "DDMMYYYY": date_value(row.get("DDMMYYYY")),
            "iSoilDisturbType": int_value(row.get("iSoilDisturbType"), "iSoilDisturbType"),
            "DepzCorp": real_value(row.get("DepzCorp"), "DepzCorp"),
        }
        tillage_files.setdefault(file_name, []).append((int_value(row.get("event_index"), "event_index"), record))

    for row in rows_to_dicts(sheets.get(IRRIG_SHEET, []), IRRIG_SHEET):
        file_name = text_value(row.get("file"))
        if not file_name:
            continue
        mode = text_value(row.get("mode")).lower() or ("auto" if file_name.startswith("auto") else "scheduled")
        fields = IRRIG_AUTO_FIELDS if mode == "auto" else IRRIG_SCHEDULED_FIELDS
        record = {"mode": mode}
        for field in fields:
            if field in {"DDMMYYYY", "DST", "DEN"}:
                record[field] = date_value(row.get(field))
            elif field in {"JST", "JEN", "iIrrigOpt"}:
                record[field] = int_value(row.get(field), field)
            else:
                record[field] = real_value(row.get(field), field)
        irrigation_files.setdefault(file_name, []).append((int_value(row.get("event_index"), "event_index"), record))

    cfg: dict[str, Any] = {
        "years": sorted(years),
        "topo_units": [topo_by_index[i] for i in sorted(topo_by_index)],
        "fertilizer_files": {k: [r for _, r in sorted(v)] for k, v in fertilizer_files.items()},
        "tillage_files": {k: [r for _, r in sorted(v)] for k, v in tillage_files.items()},
        "irrigation_files": {k: [r for _, r in sorted(v)] for k, v in irrigation_files.items()},
    }
    if control.get("description", "") != "":
        cfg["description"] = str(clean_value(control["description"]))
    return cfg


def load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text())
    return raw.get("processed_soil_management", raw)


def write_json(cfg: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def col_name(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def cell_ref(row: int, col: int) -> str:
    return f"{col_name(col)}{row + 1}"


def sheet_xml(rows: list[list[Any]]) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for r_idx, row in enumerate(rows):
        out.append(f'<row r="{r_idx + 1}">')
        for c_idx, raw in enumerate(row):
            value = clean_value(raw)
            ref = cell_ref(r_idx, c_idx)
            if value == "" or value is None:
                out.append(f'<c r="{ref}"/>')
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                out.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                text = str(value)
                space = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
                out.append(f'<c r="{ref}" t="inlineStr"><is><t{space}>{escape(text)}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    safe_names = [re.sub(r"[][/*?:\\\\]", "_", name)[:31] or "Sheet" for name in sheets]
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for i in range(len(sheets)):
        content_types.append(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    content_types.append("</Types>")

    workbook = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>',
    ]
    for i, name in enumerate(safe_names, start=1):
        workbook.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
    workbook.append("</sheets></workbook>")

    workbook_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i in range(len(sheets)):
        workbook_rels.append(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>')
    workbook_rels.append(f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    workbook_rels.append("</Relationships>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "".join(content_types))
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", "".join(workbook))
        zf.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        zf.writestr("xl/styles.xml", styles)
        for i, (_, rows) in enumerate(sheets.items(), start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows))


def ns_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter() if ns_name(t.tag) == "t") for si in root if ns_name(si.tag) == "si"]


def rel_targets(zf: zipfile.ZipFile) -> dict[str, str]:
    root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    out = {}
    for rel in root:
        if ns_name(rel.tag) == "Relationship":
            target = rel.attrib["Target"]
            out[rel.attrib["Id"]] = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
    return out


def cell_col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch) - 64
    return value - 1


def read_cell(cell: ET.Element, shared_strings: list[str]) -> Any:
    ctype = cell.attrib.get("t", "")
    if ctype == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if ns_name(t.tag) == "t")
    value_el = next((x for x in cell if ns_name(x.tag) == "v"), None)
    if value_el is None or value_el.text is None:
        return ""
    value = value_el.text
    if ctype == "s":
        return shared_strings[int(value)]
    try:
        val = float(value)
        return int(val) if val.is_integer() else val
    except ValueError:
        return value


def read_xlsx(path: Path) -> dict[str, list[list[Any]]]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        targets = rel_targets(zf)
        workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets: dict[str, list[list[Any]]] = {}
        for sheet in workbook_root.iter():
            if ns_name(sheet.tag) != "sheet":
                continue
            name = sheet.attrib["name"]
            rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            root = ET.fromstring(zf.read(targets[rid]))
            rows: list[list[Any]] = []
            for row in root.iter():
                if ns_name(row.tag) != "row":
                    continue
                values: list[Any] = []
                for cell in row:
                    if ns_name(cell.tag) != "c":
                        continue
                    col = cell_col_index(cell.attrib.get("r", f"A{len(rows) + 1}"))
                    while len(values) <= col:
                        values.append("")
                    values[col] = read_cell(cell, shared_strings)
                while values and values[-1] == "":
                    values.pop()
                rows.append(values)
            sheets[name] = rows
        return sheets


def load_config_from_path(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".nc":
        return nc_to_config(path)
    if suffix == ".xlsx":
        return sheet_rows_to_config(read_xlsx(path))
    if suffix == ".json":
        return load_json(path)
    raise ValueError(f"Unsupported input type: {path}")


def date_parts(ddmmyyyy: str) -> tuple[int, int, int]:
    s = date_value(ddmmyyyy)
    return int(s[:2]), int(s[2:4]), int(s[4:8])


def inspect_config(cfg: dict[str, Any]) -> str:
    lines = []
    years = get_years(cfg)
    lines.append(f"years: {years[0]}-{years[-1]} ({len(years)} records)" if years else "years: none")
    lines.append(f"topo_units: {len(cfg.get('topo_units', []))}")
    fert_refs, till_refs, irrig_refs = collect_referenced_files(cfg)
    lines.append(f"fertilizer_files referenced: {', '.join(sorted(fert_refs)) if fert_refs else 'none'}")
    lines.append(f"tillage_files referenced: {', '.join(sorted(till_refs)) if till_refs else 'none'}")
    lines.append(f"irrigation_files referenced: {', '.join(sorted(irrig_refs)) if irrig_refs else 'none'}")

    issues = []
    for kind, refs, mapping in [
        ("fertf", fert_refs, cfg.get("fertilizer_files", {})),
        ("tillf", till_refs, cfg.get("tillage_files", {})),
        ("irrigf", irrig_refs, cfg.get("irrigation_files", {})),
    ]:
        for missing in sorted(refs - set(mapping)):
            issues.append(f"{kind} selector references missing variable {missing}")

    file_to_years: dict[str, set[int]] = defaultdict(set)
    for topo in cfg.get("topo_units", []):
        for y, selectors in topo.get("years", {}).items():
            year = int(y)
            for key in ["fertf", "tillf", "irrigf"]:
                selector = text_value(selectors.get(key))
                if selector and selector.upper() != "NO":
                    file_to_years[selector].add(year)

    annual_n = defaultdict(float)
    annual_p = defaultdict(float)
    event_count = 0
    for file_name, records in cfg.get("fertilizer_files", {}).items():
        expected_years = file_to_years.get(file_name, set())
        for i, rec in enumerate(records, start=1):
            event_count += 1
            dd, mm, yyyy = date_parts(rec["DDMMYYYY"])
            try:
                date(max(1, yyyy), mm, dd)
            except Exception as exc:
                issues.append(f"{file_name} event {i}: invalid date {rec['DDMMYYYY']} ({exc})")
            if expected_years and yyyy not in expected_years and yyyy != 0:
                issues.append(f"{file_name} event {i}: date year {yyyy} does not match selector year(s) {sorted(expected_years)}")
            for field in FERT_REAL_FIELDS:
                if float(rec.get(field, 0)) < 0:
                    issues.append(f"{file_name} event {i}: negative {field}")
            for year in expected_years or [yyyy]:
                annual_n[year] += sum(float(rec.get(field, 0)) for field in FERT_N_FIELDS)
                annual_p[year] += sum(float(rec.get(field, 0)) for field in FERT_P_FIELDS)

    lines.append(f"fertilizer_events: {event_count}")
    if annual_n:
        lines.append("annual fertilizer totals:")
        for year in sorted(annual_n):
            lines.append(f"  {year}: N={annual_n[year]:g} g m-2 ({annual_n[year] * 10:g} kg ha-1), P_raw={annual_p[year]:g} g m-2")

    lines.append("issues:")
    if issues:
        lines.extend(f"  - {issue}" for issue in issues)
    else:
        lines.append("  none")
    return "\n".join(lines)


def cmd_nc_to_xlsx(args: argparse.Namespace) -> None:
    cfg = nc_to_config(Path(args.input_nc))
    write_xlsx(Path(args.output_xlsx), config_to_sheet_rows(cfg))
    if args.json_output:
        write_json(cfg, Path(args.json_output))


def cmd_nc_to_json(args: argparse.Namespace) -> None:
    write_json(nc_to_config(Path(args.input_nc)), Path(args.output_json))


def cmd_json_to_xlsx(args: argparse.Namespace) -> None:
    write_xlsx(Path(args.output_xlsx), config_to_sheet_rows(load_json(Path(args.input_json))))


def cmd_xlsx_to_json(args: argparse.Namespace) -> None:
    write_json(sheet_rows_to_config(read_xlsx(Path(args.input_xlsx))), Path(args.output_json))


def cmd_xlsx_to_nc(args: argparse.Namespace) -> None:
    cfg = sheet_rows_to_config(read_xlsx(Path(args.input_xlsx)))
    if args.json_output:
        write_json(cfg, Path(args.json_output))
    config_to_nc(cfg, Path(args.output_nc))


def cmd_json_to_nc(args: argparse.Namespace) -> None:
    config_to_nc(load_json(Path(args.input_json)), Path(args.output_nc))


def cmd_inspect(args: argparse.Namespace) -> None:
    print(inspect_config(load_config_from_path(Path(args.input))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("nc-to-xlsx", help="Export soil-management NetCDF to editable XLSX")
    p.add_argument("input_nc")
    p.add_argument("output_xlsx")
    p.add_argument("--json-output")
    p.set_defaults(func=cmd_nc_to_xlsx)

    p = sub.add_parser("nc-to-json", help="Export soil-management NetCDF to JSON")
    p.add_argument("input_nc")
    p.add_argument("output_json")
    p.set_defaults(func=cmd_nc_to_json)

    p = sub.add_parser("json-to-xlsx", help="Export soil-management JSON to editable XLSX")
    p.add_argument("input_json")
    p.add_argument("output_xlsx")
    p.set_defaults(func=cmd_json_to_xlsx)

    p = sub.add_parser("xlsx-to-json", help="Convert edited XLSX to soil-management JSON")
    p.add_argument("input_xlsx")
    p.add_argument("output_json")
    p.set_defaults(func=cmd_xlsx_to_json)

    p = sub.add_parser("xlsx-to-nc", help="Convert edited XLSX to EcoSIM soil-management NetCDF")
    p.add_argument("input_xlsx")
    p.add_argument("output_nc")
    p.add_argument("--json-output")
    p.set_defaults(func=cmd_xlsx_to_nc)

    p = sub.add_parser("json-to-nc", help="Convert soil-management JSON to NetCDF")
    p.add_argument("input_json")
    p.add_argument("output_nc")
    p.set_defaults(func=cmd_json_to_nc)

    p = sub.add_parser("inspect", help="Summarize and sanity-check a soil-management file")
    p.add_argument("input")
    p.set_defaults(func=cmd_inspect)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
