#!/usr/bin/env python3
"""Extract AmeriFlux BIF management records into EcoSIM-editable workbooks."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


REQUIRED_BIF_COLUMNS = ["SITE_ID", "GROUP_ID", "VARIABLE_GROUP", "VARIABLE", "DATAVALUE"]

PLANT_CONTROL_SHEET = "control"
PLANT_TOPO_SHEET = "topo_units"
PLANT_PFT_SHEET = "pft_years"
PLANT_MGMT_SHEET = "management"

PLANT_PFT_COLUMNS = [
    "year",
    "topou",
    "pft_slot",
    "pft_type",
    "planting_DDMMYYYY",
    "Planting_population",
    "Planting_depth",
    "nmgnts",
]

PLANT_MGMT_COLUMNS = [
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

SOIL_CONTROL_SHEET = "control"
SOIL_TOPO_SHEET = "topo_units"
SOIL_SELECTOR_SHEET = "year_selectors"
SOIL_FILES_SHEET = "event_files"
SOIL_FERT_SHEET = "fertilizer"
SOIL_TILL_SHEET = "tillage"
SOIL_IRRIG_SHEET = "irrigation"

SOIL_SELECTOR_COLUMNS = ["year", "topou", "fertf", "tillf", "irrigf"]
SOIL_TOPO_COLUMNS = ["topou", "NH1", "NV1", "NH2", "NV2"]
SOIL_FILES_COLUMNS = ["category", "file"]

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

TILL_FIELDS = ["DDMMYYYY", "iSoilDisturbType", "DepzCorp"]
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

DEFAULT_CROP_PFT_MAP = {
    "corn": "maiz41",
    "maize": "maiz41",
    "soy": "soyb41",
    "soybean": "soyb41",
    "soybeans": "soyb41",
    "wheat": "swhe43",
}

LB_PER_ACRE_TO_G_PER_M2 = 453.59237 / 4046.8564224
PLANTS_PER_ACRE_TO_PLANTS_PER_M2 = 1.0 / 4046.8564224

CROP_SHORT_MAP = {
    "corn": "maiz",
    "maize": "maiz",
    "soy": "soyb",
    "soybean": "soyb",
    "soybeans": "soyb",
    "wheat": "swhe",
    "spring wheat": "swhe",
    "rice": "rice",
    "barley": "barl",
    "oats": "oats",
    "alfalfa": "alfa",
    "clover": "clva",
}


def clean_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    return str(value).strip()


def number_or_blank(value: Any) -> int | float | str:
    if value == "" or value is None:
        return ""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return clean_value(value)
    return int(f) if f.is_integer() else f


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
            if value == "":
                out.append(f'<c r="{ref}"/>')
            elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                out.append(f'<c r="{ref}"><v>{raw}</v></c>')
            else:
                space = ' xml:space="preserve"' if value != value.strip() or "\n" in value else ""
                out.append(f'<c r="{ref}" t="inlineStr"><is><t{space}>{escape(value)}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_workbook(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            if target.startswith("/"):
                out[rel.attrib["Id"]] = target.lstrip("/")
            elif target.startswith("xl/"):
                out[rel.attrib["Id"]] = target
            else:
                out[rel.attrib["Id"]] = "xl/" + target
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
        f = float(value)
        return int(f) if f.is_integer() else f
    except ValueError:
        return value


def read_workbook(path: Path) -> dict[str, list[list[Any]]]:
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


def read_xlsx(path: Path, sheet_name: str | None) -> tuple[list[dict[str, str]], str]:
    wb = read_workbook(path)
    if sheet_name:
        if sheet_name not in wb:
            raise SystemExit(f"Sheet {sheet_name!r} not found. Available sheets: {', '.join(wb)}")
        rows = wb[sheet_name]
    elif "AMF-BIF" in wb:
        sheet_name = "AMF-BIF"
        rows = wb[sheet_name]
    else:
        sheet_name = next(iter(wb))
        rows = wb[sheet_name]

    if not rows:
        return [], sheet_name
    headers = [clean_value(v) for v in rows[0]]
    check_bif_headers(headers)

    out = []
    for row in rows[1:]:
        item = {headers[i]: clean_value(row[i]) if i < len(row) else "" for i in range(len(headers))}
        if any(item.get(col, "") for col in REQUIRED_BIF_COLUMNS):
            out.append(item)
    return out, sheet_name


def read_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        check_bif_headers(reader.fieldnames or [])
        return [{k: clean_value(v) for k, v in row.items()} for row in reader], path.name


def check_bif_headers(headers: list[str]) -> None:
    missing = [c for c in REQUIRED_BIF_COLUMNS if c not in headers]
    if missing:
        raise SystemExit(f"Input is missing required BIF columns: {', '.join(missing)}")


def read_bif(path: Path, sheet_name: str | None) -> tuple[list[dict[str, str]], str]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx(path, sheet_name)
    if suffix in {".csv", ".txt"}:
        return read_csv(path)
    raise SystemExit(f"Unsupported input suffix {path.suffix!r}; use .xlsx, .xlsm, .csv, or .txt")


def append_value(record: dict[str, str], key: str, value: str) -> None:
    if not key:
        return
    if key not in record or record[key] == "":
        record[key] = value
    elif value and value not in record[key].split(" | "):
        record[key] = f"{record[key]} | {value}"


def reconstruct_records(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, str], dict[str, str]] = {}
    order: dict[tuple[str, str, str], int] = {}
    for idx, row in enumerate(rows):
        key = (row["SITE_ID"], row["VARIABLE_GROUP"], row["GROUP_ID"])
        if key not in grouped:
            grouped[key] = {
                "SITE_ID": row["SITE_ID"],
                "VARIABLE_GROUP": row["VARIABLE_GROUP"],
                "GROUP_ID": row["GROUP_ID"],
            }
            order[key] = idx
        append_value(grouped[key], row["VARIABLE"], row["DATAVALUE"])
    records = []
    for key, record in grouped.items():
        record["_SOURCE_ORDER"] = str(order[key])
        records.append(record)
    return records


def date_parts_from_yyyymmdd(value: str) -> tuple[int, int, int] | None:
    value = clean_value(value)
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        yyyy = int(digits[:4])
        mm = int(digits[4:6])
        dd = int(digits[6:8])
        return yyyy, mm, dd
    return None


def choose_date(record: dict[str, str]) -> tuple[str, int | None]:
    for field in ["DM_DATE_START", "DM_DATE", "DM_DATE_END"]:
        parts = date_parts_from_yyyymmdd(record.get(field, ""))
        if parts:
            yyyy, mm, dd = parts
            return f"{dd:02d}{mm:02d}{yyyy:04d}", yyyy
    return "", None


def earliest_ddmmyyyy(values: list[str]) -> str:
    parsed = []
    for value in values:
        if not value:
            continue
        dd = int(value[:2])
        mm = int(value[2:4])
        yyyy = int(value[4:8])
        parsed.append((yyyy, mm, dd, value))
    if not parsed:
        return ""
    return sorted(parsed)[0][3]


def parse_float(value: str) -> float | None:
    value = clean_value(value).replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_surface_fraction(record: dict[str, str]) -> float | None:
    value = parse_float(record.get("DM_SURF", ""))
    if value is None:
        return None
    if value > 1.0:
        return value / 100.0
    return value


def infer_crop(record: dict[str, str]) -> str:
    text = " ".join([record.get("DM_COMMENT", ""), record.get("DM_PLANTING", ""), record.get("DM_AGRICULTURE", "")]).lower()
    if "soy" in text or "asgrow" in text:
        return "soybean"
    if "corn" in text or "maize" in text or "dekalb" in text or re.search(r"\bdk\d", text):
        return "corn"
    if "wheat" in text:
        return "wheat"
    return ""


def parse_seed_rate(comment: str) -> tuple[float | None, str]:
    text = clean_value(comment)
    patterns = [
        r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:seeds?|seed|plants?)\s*/\s*(?:acre|ac)\b",
        r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:seeds?|seed|plants?)\s+per\s+(?:acre|ac)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1).replace(",", ""))
        if match.group(2):
            value *= 1000.0
        return value, match.group(0)
    return None, ""


def parse_n_rate_lb_ac(comment: str) -> tuple[float | None, str, str]:
    text = clean_value(comment)
    explicit = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:lbs?|pounds?)\s*N\s*/?\s*(?:acre|ac)\b",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        return float(explicit.group(1).replace(",", "")), explicit.group(0), "explicit lb N/acre"
    loose = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(?:lbs?|pounds?)\s*/\s*(?:acre|ac)\b",
        text,
        flags=re.IGNORECASE,
    )
    if loose:
        return float(loose.group(1).replace(",", "")), loose.group(0), "assumed N because DM_FERT_M=N"
    return None, "", "missing numeric N rate"


def distribute_n(total_n_g_m2: float, comment: str) -> tuple[dict[str, float], str]:
    text = comment.lower()
    parts = {"NH4Soil": 0.0, "NH3Soil": 0.0, "UreaSoil": 0.0, "NO3Soil": 0.0}
    if "uan" in text or "urea ammonium nitrate" in text:
        parts["NH4Soil"] = total_n_g_m2 * 0.25
        parts["NO3Soil"] = total_n_g_m2 * 0.25
        parts["UreaSoil"] = total_n_g_m2 * 0.50
        return parts, "split UAN as 25% NH4-N, 25% NO3-N, 50% urea-N"
    if "anhydrous" in text or "nh3" in text:
        parts["NH3Soil"] = total_n_g_m2
        return parts, "mapped NH3/anhydrous ammonia to NH3Soil"
    if "urea" in text:
        parts["UreaSoil"] = total_n_g_m2
        return parts, "mapped urea to UreaSoil"
    parts["NH4Soil"] = total_n_g_m2
    return parts, "mapped unresolved N product to NH4Soil placeholder"


def decode_nc_strings(var: Any) -> list[str]:
    try:
        import netCDF4 as nc
    except ImportError as exc:
        raise SystemExit("netCDF4 is required for --pftpar-nc; use the repo venv.") from exc
    return [str(s).strip() for s in nc.chartostring(var[:]).astype(str)]


def load_pft_scheme(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        import netCDF4 as nc
    except ImportError as exc:
        raise SystemExit("netCDF4 is required for --pftpar-nc; use the repo venv.") from exc
    p = Path(path).expanduser().resolve()
    with nc.Dataset(p) as ds:
        pfts = [s for s in decode_nc_strings(ds.variables["pfts"]) if s]
        shorts = [s for s in decode_nc_strings(ds.variables["pfts_short"]) if s]
        longs = [s for s in decode_nc_strings(ds.variables["pfts_long"]) if s]
    available_by_short: dict[str, list[str]] = defaultdict(list)
    for code in pfts:
        available_by_short[code[:4]].append(code)
    for short in available_by_short:
        available_by_short[short] = sorted(available_by_short[short])
    return {
        "path": str(p),
        "pfts": set(pfts),
        "available_by_short": dict(available_by_short),
        "short_to_long": {s: longs[i] if i < len(longs) else "" for i, s in enumerate(shorts)},
    }


def parse_crop_pft_map(values: list[str], pft_scheme: dict[str, Any] | None, koppen_code: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        if "=" not in item:
            raise SystemExit(f"--crop-pft-map values must look like crop=pftcode, got {item!r}")
        crop, pft = item.split("=", 1)
        out[crop.strip().lower()] = resolve_pft_type(pft.strip(), pft_scheme, koppen_code)[0]
    return out


def resolve_pft_type(value: str, pft_scheme: dict[str, Any] | None, koppen_code: str) -> tuple[str, str]:
    value = clean_value(value)
    if not value:
        return "", "empty pft type"
    if not pft_scheme:
        return value, "not validated; no --pftpar-nc supplied"
    pfts = pft_scheme["pfts"]
    available_by_short = pft_scheme["available_by_short"]
    short = value[:4]
    candidate = f"{short}{koppen_code}" if len(value) == 4 else value
    if candidate in pfts:
        return candidate, f"validated in {Path(pft_scheme['path']).name}"
    if len(value) == 4 and candidate not in pfts:
        available = available_by_short.get(short, [])
        if available:
            return available[0], f"{candidate} missing in pftpar; used available {available[0]}"
    if len(value) >= 6 and value not in pfts:
        available = available_by_short.get(short, [])
        if available:
            return available[0], f"{value} missing in pftpar; used available {available[0]}"
    return value, f"WARNING: {value} not found in pftpar"


def pft_for_crop(
    crop: str,
    crop_pft_map: dict[str, str],
    fallback_pft: str,
    pft_scheme: dict[str, Any] | None,
    koppen_code: str,
) -> tuple[str, str]:
    crop_key = crop.lower()
    if crop_key in crop_pft_map:
        return resolve_pft_type(crop_pft_map[crop_key], pft_scheme, koppen_code)
    short = CROP_SHORT_MAP.get(crop_key)
    if short and pft_scheme:
        return resolve_pft_type(short, pft_scheme, koppen_code)
    if crop_key in DEFAULT_CROP_PFT_MAP:
        return resolve_pft_type(DEFAULT_CROP_PFT_MAP[crop_key], pft_scheme, koppen_code)
    return resolve_pft_type(fallback_pft, pft_scheme, koppen_code)


def filter_year(year: int | None, start_year: int | None, end_year: int | None) -> bool:
    if year is None:
        return False
    if start_year is not None and year < start_year:
        return False
    if end_year is not None and year > end_year:
        return False
    return True


def harvest_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for record in records:
        if record.get("VARIABLE_GROUP") != "GRP_DM_AGRICULTURE":
            continue
        text = " ".join([record.get("DM_AGRICULTURE", ""), record.get("DM_COMMENT", "")]).lower()
        if "harvest" in text:
            out.append(record)
    return out


def build_plant_outputs(
    records: list[dict[str, str]],
    args: argparse.Namespace,
    crop_pft_map: dict[str, str],
    pft_scheme: dict[str, Any] | None,
    topo: tuple[int, int, int, int],
) -> tuple[dict[str, Any], dict[str, list[list[Any]]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    planting_rows = []
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    notes = []

    for record in records:
        if record.get("VARIABLE_GROUP") != "GRP_DM_PLANTING":
            continue
        ddmmyyyy, year = choose_date(record)
        if not filter_year(year, args.start_year, args.end_year):
            continue
        comment = record.get("DM_COMMENT", "")
        crop = infer_crop(record) or args.fallback_crop
        pft_type, pft_note = pft_for_crop(crop, crop_pft_map, args.fallback_pft_type, pft_scheme, args.koppen_code)
        seed_acre, seed_source = parse_seed_rate(comment)
        if seed_acre is None:
            pop_m2 = args.default_planting_population_m2
            note = "missing seed-rate; used default planting population"
        else:
            pop_m2 = seed_acre * PLANTS_PER_ACRE_TO_PLANTS_PER_M2
            note = f"parsed seed rate from {seed_source}"
        surf = parse_surface_fraction(record)
        item = {
            "year": year,
            "DDMMYYYY": ddmmyyyy,
            "crop": crop,
            "pft_type": pft_type,
            "seed_rate_acre": seed_acre if seed_acre is not None else "",
            "Planting_population": pop_m2,
            "Planting_depth": args.planting_depth_m,
            "surface_fraction": surf if surf is not None else "",
            "GROUP_ID": record.get("GROUP_ID", ""),
            "DM_COMMENT": comment,
            "note": f"{note}; {pft_note}",
        }
        by_year[year].append(item)
        planting_rows.append(item)

    harvest_by_year: dict[int, list[dict[str, str]]] = defaultdict(list)
    harvest_review = []
    for record in harvest_records(records):
        ddmmyyyy, year = choose_date(record)
        if not filter_year(year, args.start_year, args.end_year):
            continue
        record["_DDMMYYYY"] = ddmmyyyy
        record["_YEAR"] = str(year)
        harvest_by_year[year].append(record)

    years = sorted(set(by_year) | set(harvest_by_year))
    if not years:
        raise SystemExit("No planting or harvest records found for the requested year range.")

    pft_rows = [PLANT_PFT_COLUMNS]
    mgmt_rows = [PLANT_MGMT_COLUMNS]
    year_blocks: dict[str, Any] = {}
    inferred_crop_by_year: dict[int, str] = {}

    for year in years:
        plants = by_year.get(year, [])
        if plants:
            crops = sorted(set(p["crop"] for p in plants))
            if len(crops) > 1:
                notes.append(f"{year}: multiple planted crops {crops}; using first crop by source order")
            crop = plants[0]["crop"]
            pft_type = plants[0]["pft_type"]
            date_value = earliest_ddmmyyyy([p["DDMMYYYY"] for p in plants])
            weighted = []
            weights = []
            for p in plants:
                if p["Planting_population"] != "":
                    weighted.append(float(p["Planting_population"]))
                    w = p["surface_fraction"] if p["surface_fraction"] != "" else 1.0
                    weights.append(float(w))
            if weighted:
                total_weight = sum(weights) or float(len(weighted))
                pop = sum(v * w for v, w in zip(weighted, weights)) / total_weight
            else:
                pop = args.default_planting_population_m2
            planting = {
                "DDMMYYYY": date_value,
                "Planting_population": pop,
                "Planting_depth": args.planting_depth_m,
            }
        else:
            crop = inferred_crop_by_year.get(year - 1, args.fallback_crop)
            pft_type, pft_note = pft_for_crop(crop, crop_pft_map, args.fallback_pft_type, pft_scheme, args.koppen_code)
            planting = {
                "DDMMYYYY": f"0101{year:04d}",
                "Planting_population": args.default_planting_population_m2,
                "Planting_depth": args.planting_depth_m,
            }
            notes.append(f"{year}: harvest present without planting record; inserted editable default planting row; {pft_note}")

        inferred_crop_by_year[year] = crop

        mgmt = []
        harvests = harvest_by_year.get(year, [])
        missing_surface = [h for h in harvests if parse_surface_fraction(h) is None]
        equal_fraction = 1.0 / len(harvests) if harvests else 1.0
        for index, hrec in enumerate(harvests, start=1):
            surf = parse_surface_fraction(hrec)
            if surf is None:
                fraction_cut = equal_fraction if len(harvests) > 1 else 1.0
                note = "missing DM_SURF; split equally across same-year harvest records" if len(harvests) > 1 else "missing DM_SURF; used FractionCut=1"
            else:
                fraction_cut = surf
                note = "used DM_SURF as FractionCut"
            event = {
                "DDMMYYYY": hrec["_DDMMYYYY"],
                "iHarvType": args.harvest_i_type,
                "jHarvType": args.harvest_j_type,
                "CutHeight": args.harvest_cut_height_m,
                "FractionCut": fraction_cut,
                "FineFractionLeafHarvested_pft": args.harvest_leaf_pft,
                "FineFractionNonleafHarvested_pft": args.harvest_nonleaf_pft,
                "StalkFractionHarvested_pft": args.harvest_stalk_pft,
                "StandeadFractionHarvested_pft": args.harvest_standead_pft,
                "FineFractionLeafHarvested_col": args.harvest_leaf_col,
                "FineFractionNonleafHarvested_col": args.harvest_nonleaf_col,
                "StalkFractionHarvested_col": args.harvest_stalk_col,
                "StandeadFractionHarvested_col": args.harvest_standead_col,
            }
            mgmt.append(event)
            harvest_review.append(
                {
                    "year": year,
                    "DDMMYYYY": hrec["_DDMMYYYY"],
                    "GROUP_ID": hrec.get("GROUP_ID", ""),
                    "pft_type": pft_type,
                    "FractionCut": fraction_cut,
                    "DM_SURF": hrec.get("DM_SURF", ""),
                    "DM_AGRICULTURE": hrec.get("DM_AGRICULTURE", ""),
                    "DM_COMMENT": hrec.get("DM_COMMENT", ""),
                    "note": note,
                }
            )

        pft = {"pft_type": pft_type, "planting": planting, "mgmt": mgmt}
        year_blocks[str(year)] = {"pfts": [pft]}
        pft_rows.append([year, 1, 1, pft_type, planting["DDMMYYYY"], planting["Planting_population"], planting["Planting_depth"], len(mgmt)])
        for event_index, event in enumerate(mgmt, start=1):
            mgmt_rows.append([year, 1, 1, event_index, *[event[c] for c in PLANT_MGMT_COLUMNS[4:]]])

        if missing_surface:
            notes.append(f"{year}: {len(missing_surface)} harvest record(s) lacked DM_SURF")

    nh1, nv1, nh2, nv2 = topo
    cfg = {
        "pft_dflag": 1,
        "years": years,
        "topo_units": [
            {
                "NH1": nh1,
                "NV1": nv1,
                "NH2": nh2,
                "NV2": nv2,
                "NZ": 1,
                "years": year_blocks,
            }
        ],
        "description": "plant management extracted from AmeriFlux BIF management records; review assumptions before NetCDF conversion",
    }
    sheets = {
        PLANT_CONTROL_SHEET: [["key", "value"], ["pft_dflag", 1], ["description", cfg["description"]]],
        PLANT_TOPO_SHEET: [["topou", "NH1", "NV1", "NH2", "NV2", "NZ"], [1, nh1, nv1, nh2, nv2, 1]],
        PLANT_PFT_SHEET: pft_rows,
        PLANT_MGMT_SHEET: mgmt_rows,
    }
    return cfg, sheets, planting_rows, harvest_review, notes


def build_fertilizer_record(record: dict[str, str], args: argparse.Namespace) -> tuple[int, dict[str, Any], dict[str, Any]]:
    ddmmyyyy, year = choose_date(record)
    comment = record.get("DM_COMMENT", "")
    lb_ac, source, assumption = parse_n_rate_lb_ac(comment)
    if lb_ac is None:
        total_n = args.default_fertilizer_n_g_m2
    else:
        total_n = lb_ac * LB_PER_ACRE_TO_G_PER_M2
    n_parts, distribution_note = distribute_n(total_n, comment)
    fert = {field: 0.0 for field in FERT_FIELDS}
    fert["DDMMYYYY"] = ddmmyyyy
    for field, value in n_parts.items():
        fert[field] = value
    fert["AppDepth"] = args.fert_app_depth_m
    fert["BandWidth"] = args.fert_band_width_m
    fert["IsAmendtypFert"] = 1
    fert["IsAmendtypResidual"] = 0
    fert["IsAmendtypManure"] = 0
    review = {
        "year": year,
        "DDMMYYYY": ddmmyyyy,
        "GROUP_ID": record.get("GROUP_ID", ""),
        "DM_FERT_M": record.get("DM_FERT_M", ""),
        "rate_lb_acre": lb_ac if lb_ac is not None else "",
        "total_N_g_m2": total_n,
        "NH4Soil": fert["NH4Soil"],
        "NH3Soil": fert["NH3Soil"],
        "UreaSoil": fert["UreaSoil"],
        "NO3Soil": fert["NO3Soil"],
        "source_text": source,
        "DM_COMMENT": comment,
        "note": f"{assumption}; {distribution_note}",
    }
    return year or 0, fert, review


def build_soil_outputs(
    records: list[dict[str, str]],
    args: argparse.Namespace,
    topo: tuple[int, int, int, int],
    plant_years: list[int],
) -> tuple[dict[str, Any], dict[str, list[list[Any]]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    fert_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    till_by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    fert_review = []
    till_review = []
    unconverted = []
    notes = []

    for record in records:
        group = record.get("VARIABLE_GROUP", "")
        ddmmyyyy, year = choose_date(record)
        if not filter_year(year, args.start_year, args.end_year):
            continue

        if group == "GRP_DM_FERT_M":
            event_year, fert, review = build_fertilizer_record(record, args)
            if event_year:
                fert_by_year[event_year].append(fert)
                fert_review.append(review)
        elif group == "GRP_DM_TILL":
            till = {
                "DDMMYYYY": ddmmyyyy,
                "iSoilDisturbType": args.tillage_type,
                "DepzCorp": args.tillage_depth_m,
            }
            till_by_year[year].append(till)
            till_review.append(
                {
                    "year": year,
                    "DDMMYYYY": ddmmyyyy,
                    "GROUP_ID": record.get("GROUP_ID", ""),
                    "DM_TILL": record.get("DM_TILL", ""),
                    "DM_COMMENT": record.get("DM_COMMENT", ""),
                    "iSoilDisturbType": args.tillage_type,
                    "DepzCorp": args.tillage_depth_m,
                    "note": "used default tillage type/depth; edit before NetCDF conversion if known",
                }
            )
        elif group.startswith("GRP_DM_") and group not in {"GRP_DM_PLANTING", "GRP_DM_AGRICULTURE"}:
            unconverted.append(
                {
                    "year": year,
                    "DDMMYYYY": ddmmyyyy,
                    "GROUP_ID": record.get("GROUP_ID", ""),
                    "VARIABLE_GROUP": group,
                    "DM_COMMENT": record.get("DM_COMMENT", ""),
                    "note": "not converted to EcoSIM soil management table",
                }
            )

    years = sorted(set(plant_years) | set(fert_by_year) | set(till_by_year))
    if args.start_year is not None or args.end_year is not None:
        years = [y for y in years if filter_year(y, args.start_year, args.end_year)]
    if not years:
        raise SystemExit("No soil management years found for the requested year range.")

    fertilizer_files = {f"fertf_{year}": fert_by_year[year] for year in sorted(fert_by_year)}
    tillage_files = {f"tillf_{year}": till_by_year[year] for year in sorted(till_by_year)}

    for year, events in fert_by_year.items():
        if len(events) > 12:
            notes.append(f"{year}: {len(events)} fertilizer events exceeds EcoSIM reader limit of 12")
    for year, events in till_by_year.items():
        if len(events) > 367:
            notes.append(f"{year}: {len(events)} tillage events exceeds EcoSIM reader limit of 367")

    nh1, nv1, nh2, nv2 = topo
    selectors = {}
    for year in years:
        selectors[str(year)] = {
            "fertf": f"fertf_{year}" if year in fert_by_year else "NO",
            "tillf": f"tillf_{year}" if year in till_by_year else "NO",
            "irrigf": "NO",
        }

    cfg = {
        "years": years,
        "topo_units": [
            {
                "NH1": nh1,
                "NV1": nv1,
                "NH2": nh2,
                "NV2": nv2,
                "years": selectors,
            }
        ],
        "fertilizer_files": fertilizer_files,
        "tillage_files": tillage_files,
        "irrigation_files": {},
        "description": "soil management extracted from AmeriFlux BIF management records; review assumptions before NetCDF conversion",
    }

    sheets = soil_config_to_sheets(cfg)
    return cfg, sheets, fert_review, till_review, unconverted, notes


def soil_config_to_sheets(cfg: dict[str, Any]) -> dict[str, list[list[Any]]]:
    sheets: dict[str, list[list[Any]]] = {
        SOIL_CONTROL_SHEET: [["key", "value"], ["description", cfg.get("description", "")]],
    }
    topo = cfg["topo_units"][0]
    sheets[SOIL_TOPO_SHEET] = [SOIL_TOPO_COLUMNS, [1, topo["NH1"], topo["NV1"], topo["NH2"], topo["NV2"]]]
    selector_rows = [SOIL_SELECTOR_COLUMNS]
    for year in cfg["years"]:
        sel = topo["years"][str(year)]
        selector_rows.append([year, 1, sel["fertf"], sel["tillf"], sel["irrigf"]])
    sheets[SOIL_SELECTOR_SHEET] = selector_rows

    file_rows = [SOIL_FILES_COLUMNS]
    for name in sorted(cfg.get("fertilizer_files", {})):
        file_rows.append(["fertilizer", name])
    for name in sorted(cfg.get("tillage_files", {})):
        file_rows.append(["tillage", name])
    sheets[SOIL_FILES_SHEET] = file_rows

    fert_rows = [["file", "event_index", *FERT_FIELDS]]
    for name, events in sorted(cfg.get("fertilizer_files", {}).items()):
        for idx, event in enumerate(events, start=1):
            fert_rows.append([name, idx, *[event.get(field, 0) for field in FERT_FIELDS]])
    sheets[SOIL_FERT_SHEET] = fert_rows

    till_rows = [["file", "event_index", *TILL_FIELDS]]
    for name, events in sorted(cfg.get("tillage_files", {}).items()):
        for idx, event in enumerate(events, start=1):
            till_rows.append([name, idx, *[event.get(field, "") for field in TILL_FIELDS]])
    sheets[SOIL_TILL_SHEET] = till_rows
    sheets[SOIL_IRRIG_SHEET] = [IRRIG_COLUMNS]
    return sheets


def dict_rows_to_sheet(headers: list[str], rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [headers, *[[row.get(h, "") for h in headers] for row in rows]]


def build_review_sheets(
    site_id: str,
    source: Path,
    plant_cfg: dict[str, Any],
    soil_cfg: dict[str, Any],
    planting_review: list[dict[str, Any]],
    harvest_review: list[dict[str, Any]],
    fert_review: list[dict[str, Any]],
    till_review: list[dict[str, Any]],
    unconverted: list[dict[str, Any]],
    notes: list[str],
) -> dict[str, list[list[Any]]]:
    summary = [
        ["key", "value"],
        ["site_id", site_id],
        ["source", str(source)],
        ["plant_years", ",".join(str(y) for y in plant_cfg["years"])],
        ["soil_years", ",".join(str(y) for y in soil_cfg["years"])],
        ["planting_records", len(planting_review)],
        ["harvest_records", len(harvest_review)],
        ["fertilizer_records", len(fert_review)],
        ["tillage_records", len(till_review)],
        ["unconverted_management_records", len(unconverted)],
    ]
    note_rows = [["note"], *[[n] for n in notes]]
    return {
        "summary": summary,
        "notes": note_rows,
        "planting_review": dict_rows_to_sheet(
            [
                "year",
                "DDMMYYYY",
                "crop",
                "pft_type",
                "seed_rate_acre",
                "Planting_population",
                "Planting_depth",
                "surface_fraction",
                "GROUP_ID",
                "note",
                "DM_COMMENT",
            ],
            planting_review,
        ),
        "harvest_review": dict_rows_to_sheet(
            ["year", "DDMMYYYY", "GROUP_ID", "pft_type", "FractionCut", "DM_SURF", "DM_AGRICULTURE", "note", "DM_COMMENT"],
            harvest_review,
        ),
        "fertilizer_review": dict_rows_to_sheet(
            [
                "year",
                "DDMMYYYY",
                "GROUP_ID",
                "DM_FERT_M",
                "rate_lb_acre",
                "total_N_g_m2",
                "NH4Soil",
                "NH3Soil",
                "UreaSoil",
                "NO3Soil",
                "source_text",
                "note",
                "DM_COMMENT",
            ],
            fert_review,
        ),
        "tillage_review": dict_rows_to_sheet(
            ["year", "DDMMYYYY", "GROUP_ID", "DM_TILL", "iSoilDisturbType", "DepzCorp", "note", "DM_COMMENT"],
            till_review,
        ),
        "unconverted_dm": dict_rows_to_sheet(
            ["year", "DDMMYYYY", "GROUP_ID", "VARIABLE_GROUP", "note", "DM_COMMENT"],
            unconverted,
        ),
    }


def infer_site_id(rows: list[dict[str, str]], fallback: str) -> str:
    ids = sorted({row.get("SITE_ID", "") for row in rows if row.get("SITE_ID", "")})
    if len(ids) == 1:
        return ids[0]
    if ids:
        raise SystemExit("Multiple SITE_ID values found; select one with --site-id.")
    return fallback


def parse_topo(value: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise SystemExit("--topo must have four comma-separated integers: NH1,NV1,NH2,NV2")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_bif", help="AmeriFlux BIF workbook or CSV")
    parser.add_argument("--out-dir", default="result/ameriflux_bif_ecosim_mgmt", help="Output directory")
    parser.add_argument("--sheet", help="BIF sheet name; defaults to AMF-BIF when present")
    parser.add_argument("--site-id", help="Select SITE_ID rows before extraction; required for multi-site input")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--topo", default="1,1,1,1", help="EcoSIM topo bounds NH1,NV1,NH2,NV2")
    parser.add_argument("--pftpar-nc", help="EcoSIM PFT parameter NetCDF used to validate pft_type values")
    parser.add_argument("--koppen-code", default="41", help="Two-digit EcoSIM Koppen climate number used with pfts_short")
    parser.add_argument("--crop-pft-map", action="append", default=[], help="Override crop-to-PFT map, e.g. corn=maiz41")
    parser.add_argument("--fallback-crop", default="corn")
    parser.add_argument("--fallback-pft-type", default="maiz41")
    parser.add_argument("--planting-depth-m", type=float, default=0.05)
    parser.add_argument("--default-planting-population-m2", type=float, default=0.0)
    parser.add_argument("--harvest-i-type", type=int, default=1)
    parser.add_argument("--harvest-j-type", type=int, default=1)
    parser.add_argument("--harvest-cut-height-m", type=float, default=0.1)
    parser.add_argument("--harvest-leaf-pft", type=float, default=0.0)
    parser.add_argument("--harvest-nonleaf-pft", type=float, default=0.0)
    parser.add_argument("--harvest-stalk-pft", type=float, default=0.0)
    parser.add_argument("--harvest-standead-pft", type=float, default=0.0)
    parser.add_argument("--harvest-leaf-col", type=float, default=0.0)
    parser.add_argument("--harvest-nonleaf-col", type=float, default=0.0)
    parser.add_argument("--harvest-stalk-col", type=float, default=0.0)
    parser.add_argument("--harvest-standead-col", type=float, default=0.0)
    parser.add_argument("--default-fertilizer-n-g-m2", type=float, default=0.0)
    parser.add_argument("--fert-app-depth-m", type=float, default=0.02)
    parser.add_argument("--fert-band-width-m", type=float, default=0.76)
    parser.add_argument("--tillage-type", type=int, default=1)
    parser.add_argument("--tillage-depth-m", type=float, default=0.15)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input_bif).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    topo = parse_topo(args.topo)
    pft_scheme = load_pft_scheme(args.pftpar_nc)
    crop_pft_map = parse_crop_pft_map(args.crop_pft_map, pft_scheme, args.koppen_code)

    rows, sheet = read_bif(input_path, args.sheet)
    site_id = args.site_id.strip() if args.site_id else infer_site_id(rows, input_path.stem)
    rows = [row for row in rows if row.get("SITE_ID", "").strip() == site_id]
    if not rows:
        raise SystemExit(f"No BIF rows found for SITE_ID {site_id!r}.")
    records = reconstruct_records(rows)
    if not records:
        raise SystemExit("No BIF records found.")

    plant_cfg, plant_sheets, planting_review, harvest_review, plant_notes = build_plant_outputs(records, args, crop_pft_map, pft_scheme, topo)
    soil_cfg, soil_sheets, fert_review, till_review, unconverted, soil_notes = build_soil_outputs(records, args, topo, plant_cfg["years"])
    notes = [f"read sheet: {sheet}", f"selected SITE_ID {site_id}: {len(rows)} source rows", *plant_notes, *soil_notes]
    if pft_scheme:
        notes.insert(1, f"validated pft_type values against {pft_scheme['path']} with Koppen code {args.koppen_code}")

    stem = site_id.replace("/", "_")
    plant_xlsx = out_dir / f"{stem}_plant_mgmt.xlsx"
    soil_xlsx = out_dir / f"{stem}_soil_mgmt.xlsx"
    review_xlsx = out_dir / f"{stem}_management_extraction_review.xlsx"
    plant_json = out_dir / f"{stem}_plant_mgmt.json"
    soil_json = out_dir / f"{stem}_soil_mgmt.json"

    write_workbook(plant_xlsx, plant_sheets)
    write_workbook(soil_xlsx, soil_sheets)
    write_workbook(
        review_xlsx,
        build_review_sheets(
            site_id,
            input_path,
            plant_cfg,
            soil_cfg,
            planting_review,
            harvest_review,
            fert_review,
            till_review,
            unconverted,
            notes,
        ),
    )
    write_json(plant_json, plant_cfg)
    write_json(soil_json, soil_cfg)

    print(json.dumps(
        {
            "site_id": site_id,
            "plant_mgmt_xlsx": str(plant_xlsx),
            "soil_mgmt_xlsx": str(soil_xlsx),
            "review_xlsx": str(review_xlsx),
            "plant_mgmt_json": str(plant_json),
            "soil_mgmt_json": str(soil_json),
            "plant_years": plant_cfg["years"],
            "soil_years": soil_cfg["years"],
            "planting_records": len(planting_review),
            "harvest_records": len(harvest_review),
            "fertilizer_records": len(fert_review),
            "tillage_records": len(till_review),
            "unconverted_management_records": len(unconverted),
            "notes": notes,
            "pftpar_nc": pft_scheme["path"] if pft_scheme else "",
            "koppen_code": args.koppen_code,
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
