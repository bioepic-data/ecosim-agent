#!/usr/bin/env python3
"""Convert EcoSIM plant-management NetCDF, Excel, and JSON files."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import netCDF4 as nc
import numpy as np


CONTROL_SHEET = "control"
TOPO_SHEET = "topo_units"
PFT_SHEET = "pft_years"
MGMT_SHEET = "management"

PFT_COLUMNS = [
    "year",
    "topou",
    "pft_slot",
    "pft_type",
    "planting_DDMMYYYY",
    "Planting_population",
    "Planting_depth",
    "nmgnts",
]

MGMT_COLUMNS = [
    "year",
    "topou",
    "pft_slot",
    "event_index",
    "DDMMYYYY",
    "iHarvType",
    "jHarvType",
    "CutHeight",
    "FractionCut",
    "FineFractionLeafHarvested_pft",
    "FineFractionNonleafHarvested_pft",
    "StalkFractionHarvested_pft",
    "StandeadFractionHarvested_pft",
    "FineFractionLeafHarvested_col",
    "FineFractionNonleafHarvested_col",
    "StalkFractionHarvested_col",
    "StandeadFractionHarvested_col",
]


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


def number_value(value: Any, field: str) -> int | float:
    s = text_value(value)
    if s == "":
        raise ValueError(f"Missing numeric value for {field}")
    val = float(s)
    return int(val) if val.is_integer() else val


def real_value(value: Any, field: str) -> float:
    s = text_value(value)
    if s == "":
        raise ValueError(f"Missing real value for {field}")
    return float(s)


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


def parse_planting(value: str) -> dict[str, Any]:
    s = text_value(value)
    if not s:
        return {}
    parts = [p for p in re.split(r"[\s,]+", s) if p]
    if len(parts) != 3:
        raise ValueError(f"Planting string must have 3 fields: {value!r}")
    return {
        "DDMMYYYY": date_value(parts[0]),
        "Planting_population": number_value(parts[1], "Planting_population"),
        "Planting_depth": number_value(parts[2], "Planting_depth"),
    }


def parse_mgmt(value: str) -> dict[str, Any]:
    s = text_value(value)
    if not s:
        return {}
    parts = [p for p in re.split(r"[\s,]+", s) if p]
    if len(parts) != 13:
        raise ValueError(f"Management string must have 13 fields: {value!r}")
    return {
        "DDMMYYYY": date_value(parts[0]),
        "iHarvType": int_value(parts[1], "iHarvType"),
        "jHarvType": int_value(parts[2], "jHarvType"),
        "CutHeight": real_value(parts[3], "CutHeight"),
        "FractionCut": real_value(parts[4], "FractionCut"),
        "FineFractionLeafHarvested_pft": real_value(parts[5], "FineFractionLeafHarvested_pft"),
        "FineFractionNonleafHarvested_pft": real_value(parts[6], "FineFractionNonleafHarvested_pft"),
        "StalkFractionHarvested_pft": real_value(parts[7], "StalkFractionHarvested_pft"),
        "StandeadFractionHarvested_pft": real_value(parts[8], "StandeadFractionHarvested_pft"),
        "FineFractionLeafHarvested_col": real_value(parts[9], "FineFractionLeafHarvested_col"),
        "FineFractionNonleafHarvested_col": real_value(parts[10], "FineFractionNonleafHarvested_col"),
        "StalkFractionHarvested_col": real_value(parts[11], "StalkFractionHarvested_col"),
        "StandeadFractionHarvested_col": real_value(parts[12], "StandeadFractionHarvested_col"),
    }


def build_planting(planting: dict[str, Any]) -> tuple[Any, Any, Any]:
    if not planting:
        return "", "", ""
    return (
        date_value(planting.get("DDMMYYYY", "")),
        clean_value(planting.get("Planting_population", "")),
        clean_value(planting.get("Planting_depth", "")),
    )


def decode_chars(var: nc.Variable) -> np.ndarray:
    data = var[:]
    decoded = nc.chartostring(data).astype(str)
    return np.asarray(decoded)


def masked_int(value: Any, default: int = 0) -> int:
    if np.ma.is_masked(value):
        return default
    return int(value)


def nc_to_config(path: Path) -> dict[str, Any]:
    with nc.Dataset(path) as ds:
        years = [int(y) for y in ds.variables["year"][:]]
        pft_dflag = int(ds.variables["pft_dflag"][:])
        nmgnts = ds.variables["nmgnts"][:]
        pft_type = decode_chars(ds.variables["pft_type"])
        pft_pltinfo = decode_chars(ds.variables["pft_pltinfo"])
        pft_mgmt = decode_chars(ds.variables["pft_mgmt"])

        topo_units = []
        for itu in range(len(ds.dimensions["ntopou"])):
            nz = int(ds.variables["NZ"][itu])
            topo = {
                "NH1": int(ds.variables["NH1"][itu]),
                "NV1": int(ds.variables["NV1"][itu]),
                "NH2": int(ds.variables["NH2"][itu]),
                "NV2": int(ds.variables["NV2"][itu]),
                "NZ": nz,
                "years": {},
            }

            for iy, year in enumerate(years):
                pfts = []
                for ipft in range(nz):
                    mgmt_count = masked_int(nmgnts[iy, itu, ipft], default=0)
                    pft = {
                        "pft_type": text_value(pft_type[iy, itu, ipft]),
                        "planting": parse_planting(text_value(pft_pltinfo[iy, itu, ipft])),
                        "mgmt": [],
                    }
                    for im in range(max(0, mgmt_count)):
                        entry = text_value(pft_mgmt[iy, itu, ipft, im])
                        if entry:
                            pft["mgmt"].append(parse_mgmt(entry))
                    pfts.append(pft)
                topo["years"][str(year)] = {"pfts": pfts}
            topo_units.append(topo)

        cfg: dict[str, Any] = {
            "pft_dflag": pft_dflag,
            "years": years,
            "topo_units": topo_units,
        }
        if "description" in ds.ncattrs():
            cfg["description"] = str(ds.getncattr("description"))
        return cfg


def config_to_sheet_rows(cfg: dict[str, Any]) -> dict[str, list[list[Any]]]:
    control = [["key", "value"], ["pft_dflag", cfg.get("pft_dflag", 1)]]
    if cfg.get("description"):
        control.append(["description", cfg["description"]])

    topo_rows = [["topou", "NH1", "NV1", "NH2", "NV2", "NZ"]]
    pft_rows = [PFT_COLUMNS]
    mgmt_rows = [MGMT_COLUMNS]

    for itu, topo in enumerate(cfg.get("topo_units", []), start=1):
        topo_rows.append([itu, topo["NH1"], topo["NV1"], topo["NH2"], topo["NV2"], topo["NZ"]])
        year_blocks = topo.get("years", {})
        for year in sorted((int(y) for y in year_blocks.keys())):
            pfts = year_blocks[str(year)].get("pfts", [])
            for ipft, pft in enumerate(pfts, start=1):
                planting_date, planting_pop, planting_depth = build_planting(pft.get("planting", {}))
                mgmts = pft.get("mgmt", [])
                pft_rows.append(
                    [
                        year,
                        itu,
                        ipft,
                        pft.get("pft_type", ""),
                        planting_date,
                        planting_pop,
                        planting_depth,
                        len(mgmts),
                    ]
                )
                for im, mgmt in enumerate(mgmts, start=1):
                    mgmt_rows.append(
                        [
                            year,
                            itu,
                            ipft,
                            im,
                            date_value(mgmt.get("DDMMYYYY", "")),
                            mgmt.get("iHarvType", ""),
                            mgmt.get("jHarvType", ""),
                            mgmt.get("CutHeight", ""),
                            mgmt.get("FractionCut", ""),
                            mgmt.get("FineFractionLeafHarvested_pft", ""),
                            mgmt.get("FineFractionNonleafHarvested_pft", ""),
                            mgmt.get("StalkFractionHarvested_pft", ""),
                            mgmt.get("StandeadFractionHarvested_pft", ""),
                            mgmt.get("FineFractionLeafHarvested_col", ""),
                            mgmt.get("FineFractionNonleafHarvested_col", ""),
                            mgmt.get("StalkFractionHarvested_col", ""),
                            mgmt.get("StandeadFractionHarvested_col", ""),
                        ]
                    )

    return {
        CONTROL_SHEET: control,
        TOPO_SHEET: topo_rows,
        PFT_SHEET: pft_rows,
        MGMT_SHEET: mgmt_rows,
    }


def rows_to_dicts(rows: list[list[Any]], sheet: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    headers = [text_value(h) for h in rows[0]]
    out = []
    for row in rows[1:]:
        if not any(text_value(v) for v in row):
            continue
        item = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
        out.append(item)
    if not headers:
        raise ValueError(f"Sheet {sheet!r} has no header row")
    return out


def sheet_rows_to_config(sheets: dict[str, list[list[Any]]]) -> dict[str, Any]:
    for required in [CONTROL_SHEET, TOPO_SHEET, PFT_SHEET, MGMT_SHEET]:
        if required not in sheets:
            raise ValueError(f"Workbook is missing required sheet: {required}")

    control_rows = rows_to_dicts(sheets[CONTROL_SHEET], CONTROL_SHEET)
    control = {text_value(r.get("key")): r.get("value", "") for r in control_rows}
    pft_dflag = int_value(control.get("pft_dflag", 1), "pft_dflag")

    topo_by_index: dict[int, dict[str, Any]] = {}
    for row in rows_to_dicts(sheets[TOPO_SHEET], TOPO_SHEET):
        topou = int_value(row.get("topou"), "topou")
        topo_by_index[topou] = {
            "NH1": int_value(row.get("NH1"), "NH1"),
            "NV1": int_value(row.get("NV1"), "NV1"),
            "NH2": int_value(row.get("NH2"), "NH2"),
            "NV2": int_value(row.get("NV2"), "NV2"),
            "NZ": int_value(row.get("NZ"), "NZ"),
            "years": {},
        }

    pft_by_key: dict[tuple[int, int, int], dict[str, Any]] = {}
    pft_slots_by_year: dict[tuple[int, int], set[int]] = {}
    years = set()
    for row in rows_to_dicts(sheets[PFT_SHEET], PFT_SHEET):
        year = int_value(row.get("year"), "year")
        topou = int_value(row.get("topou"), "topou")
        pft_slot = int_value(row.get("pft_slot"), "pft_slot")
        if topou not in topo_by_index:
            raise ValueError(f"pft_years references missing topou {topou}")
        years.add(year)
        pft_slots_by_year.setdefault((topou, year), set()).add(pft_slot)
        planting = {}
        if date_value(row.get("planting_DDMMYYYY", "")):
            planting = {
                "DDMMYYYY": date_value(row.get("planting_DDMMYYYY", "")),
                "Planting_population": number_value(row.get("Planting_population"), "Planting_population"),
                "Planting_depth": number_value(row.get("Planting_depth"), "Planting_depth"),
            }
        pft_by_key[(topou, year, pft_slot)] = {
            "pft_type": text_value(row.get("pft_type")),
            "planting": planting,
            "mgmt": [],
        }

    mgmt_rows = rows_to_dicts(sheets[MGMT_SHEET], MGMT_SHEET)
    mgmt_by_key: dict[tuple[int, int, int], list[tuple[int, dict[str, Any]]]] = {}
    for row in mgmt_rows:
        year = int_value(row.get("year"), "year")
        topou = int_value(row.get("topou"), "topou")
        pft_slot = int_value(row.get("pft_slot"), "pft_slot")
        event_index = int_value(row.get("event_index"), "event_index")
        key = (topou, year, pft_slot)
        if key not in pft_by_key:
            raise ValueError(f"management references missing pft_years row: {key}")
        mgmt_by_key.setdefault(key, []).append(
            (
                event_index,
                {
                    "DDMMYYYY": date_value(row.get("DDMMYYYY")),
                    "iHarvType": int_value(row.get("iHarvType"), "iHarvType"),
                    "jHarvType": int_value(row.get("jHarvType"), "jHarvType"),
                    "CutHeight": real_value(row.get("CutHeight"), "CutHeight"),
                    "FractionCut": real_value(row.get("FractionCut"), "FractionCut"),
                    "FineFractionLeafHarvested_pft": real_value(row.get("FineFractionLeafHarvested_pft"), "FineFractionLeafHarvested_pft"),
                    "FineFractionNonleafHarvested_pft": real_value(row.get("FineFractionNonleafHarvested_pft"), "FineFractionNonleafHarvested_pft"),
                    "StalkFractionHarvested_pft": real_value(row.get("StalkFractionHarvested_pft"), "StalkFractionHarvested_pft"),
                    "StandeadFractionHarvested_pft": real_value(row.get("StandeadFractionHarvested_pft"), "StandeadFractionHarvested_pft"),
                    "FineFractionLeafHarvested_col": real_value(row.get("FineFractionLeafHarvested_col"), "FineFractionLeafHarvested_col"),
                    "FineFractionNonleafHarvested_col": real_value(row.get("FineFractionNonleafHarvested_col"), "FineFractionNonleafHarvested_col"),
                    "StalkFractionHarvested_col": real_value(row.get("StalkFractionHarvested_col"), "StalkFractionHarvested_col"),
                    "StandeadFractionHarvested_col": real_value(row.get("StandeadFractionHarvested_col"), "StandeadFractionHarvested_col"),
                },
            )
        )

    for key, events in mgmt_by_key.items():
        pft_by_key[key]["mgmt"] = [m for _, m in sorted(events)]

    for topou, topo in sorted(topo_by_index.items()):
        nz = topo["NZ"]
        for year in sorted(years):
            slots = sorted(pft_slots_by_year.get((topou, year), set()))
            if slots and slots != list(range(1, max(slots) + 1)):
                raise ValueError(f"PFT slots must be contiguous for topou {topou}, year {year}: {slots}")
            if len(slots) > nz:
                raise ValueError(f"topou {topou}, year {year} has {len(slots)} PFT rows but NZ={nz}")
            topo["years"][str(year)] = {"pfts": [pft_by_key[(topou, year, s)] for s in slots]}

    cfg: dict[str, Any] = {
        "pft_dflag": pft_dflag,
        "years": sorted(years),
        "topo_units": [topo_by_index[i] for i in sorted(topo_by_index)],
    }
    if text_value(control.get("description")):
        cfg["description"] = text_value(control["description"])
    return cfg


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
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
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
                text = escape(str(value))
                out.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
        out.append("</row>")
    out.extend(["</sheetData>", "</worksheet>"])
    return "".join(out)


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    safe_names = []
    for name in sheets:
        safe = re.sub(r"[][/*?:\\\\]", "_", name)[:31] or "Sheet"
        safe_names.append(safe)

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for i in range(len(sheets)):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    workbook = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>',
    ]
    for i, name in enumerate(safe_names, start=1):
        workbook.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>')
    workbook.append("</sheets></workbook>")

    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for i in range(len(sheets)):
        workbook_rels.append(
            f'<Relationship Id="rId{i + 1}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i + 1}.xml"/>'
        )
    workbook_rels.append(
        f'<Relationship Id="rId{len(sheets) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    workbook_rels.append("</Relationships>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
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
    strings = []
    for si in root:
        if ns_name(si.tag) != "si":
            continue
        strings.append("".join(t.text or "" for t in si.iter() if ns_name(t.tag) == "t"))
    return strings


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
    if ctype == "b":
        return int(value) == 1
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
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
            target = targets[rid]
            root = ET.fromstring(zf.read(target))
            rows: list[list[Any]] = []
            for row in root.iter():
                if ns_name(row.tag) != "row":
                    continue
                row_values: list[Any] = []
                for cell in row:
                    if ns_name(cell.tag) != "c":
                        continue
                    col = cell_col_index(cell.attrib.get("r", f"A{len(rows) + 1}"))
                    while len(row_values) <= col:
                        row_values.append("")
                    row_values[col] = read_cell(cell, shared_strings)
                while row_values and row_values[-1] == "":
                    row_values.pop()
                rows.append(row_values)
            sheets[name] = rows
        return sheets


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(cfg: dict[str, Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def locate_writer(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.exists():
            return path
        raise FileNotFoundError(path)

    candidates = []
    cwd = Path.cwd().resolve()
    for base in [cwd, *cwd.parents]:
        candidates.append(base / "applications/notebooks/scripts/PlantMgmtWriter.py")
    script_path = Path(__file__).resolve()
    for base in script_path.parents:
        candidates.append(base / "applications/notebooks/scripts/PlantMgmtWriter.py")

    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("Could not locate applications/notebooks/scripts/PlantMgmtWriter.py")


def write_nc_with_plant_mgmt_writer(cfg: dict[str, Any], out_nc: Path, writer: str | None) -> None:
    writer_path = locate_writer(writer)
    spec = importlib.util.spec_from_file_location("PlantMgmtWriter", writer_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not import {writer_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "create_nc"):
        module.create_nc(cfg, out_nc)
        return
    if hasattr(module, "PlantMgmtWriter"):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(cfg, tmp)
            tmp_path = tmp.name
        try:
            module.PlantMgmtWriter(tmp_path, out_nc)
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        return
    raise AttributeError(f"{writer_path} does not define create_nc or PlantMgmtWriter")


def cmd_nc_to_xlsx(args: argparse.Namespace) -> None:
    cfg = nc_to_config(Path(args.input_nc))
    write_xlsx(Path(args.output_xlsx), config_to_sheet_rows(cfg))
    if args.json_output:
        write_json(cfg, Path(args.json_output))


def cmd_nc_to_json(args: argparse.Namespace) -> None:
    write_json(nc_to_config(Path(args.input_nc)), Path(args.output_json))


def cmd_json_to_xlsx(args: argparse.Namespace) -> None:
    cfg = load_json(Path(args.input_json))
    write_xlsx(Path(args.output_xlsx), config_to_sheet_rows(cfg))


def cmd_xlsx_to_json(args: argparse.Namespace) -> None:
    cfg = sheet_rows_to_config(read_xlsx(Path(args.input_xlsx)))
    write_json(cfg, Path(args.output_json))


def cmd_xlsx_to_nc(args: argparse.Namespace) -> None:
    cfg = sheet_rows_to_config(read_xlsx(Path(args.input_xlsx)))
    if args.json_output:
        write_json(cfg, Path(args.json_output))
    write_nc_with_plant_mgmt_writer(cfg, Path(args.output_nc), args.writer)


def cmd_json_to_nc(args: argparse.Namespace) -> None:
    write_nc_with_plant_mgmt_writer(load_json(Path(args.input_json)), Path(args.output_nc), args.writer)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("nc-to-xlsx", help="Export plant-management NetCDF to editable XLSX")
    p.add_argument("input_nc")
    p.add_argument("output_xlsx")
    p.add_argument("--json-output")
    p.set_defaults(func=cmd_nc_to_xlsx)

    p = sub.add_parser("nc-to-json", help="Export plant-management NetCDF to PlantMgmtWriter JSON")
    p.add_argument("input_nc")
    p.add_argument("output_json")
    p.set_defaults(func=cmd_nc_to_json)

    p = sub.add_parser("json-to-xlsx", help="Export PlantMgmtWriter JSON to editable XLSX")
    p.add_argument("input_json")
    p.add_argument("output_xlsx")
    p.set_defaults(func=cmd_json_to_xlsx)

    p = sub.add_parser("xlsx-to-json", help="Convert edited XLSX to PlantMgmtWriter JSON")
    p.add_argument("input_xlsx")
    p.add_argument("output_json")
    p.set_defaults(func=cmd_xlsx_to_json)

    p = sub.add_parser("xlsx-to-nc", help="Convert edited XLSX to NetCDF through PlantMgmtWriter.py")
    p.add_argument("input_xlsx")
    p.add_argument("output_nc")
    p.add_argument("--json-output")
    p.add_argument("--writer", help="Path to PlantMgmtWriter.py")
    p.set_defaults(func=cmd_xlsx_to_nc)

    p = sub.add_parser("json-to-nc", help="Convert PlantMgmtWriter JSON to NetCDF")
    p.add_argument("input_json")
    p.add_argument("output_nc")
    p.add_argument("--writer", help="Path to PlantMgmtWriter.py")
    p.set_defaults(func=cmd_json_to_nc)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
