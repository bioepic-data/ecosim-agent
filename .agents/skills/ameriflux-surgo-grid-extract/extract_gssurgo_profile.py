#!/usr/bin/env python3
"""
Extract a dominant-component soil profile from a CONUS gSSURGO file geodatabase
for a given longitude/latitude and interpolate selected properties to the vertical
layers used in a template CDL/netCDF text file.

If gSSURGO is unavailable or has missing values, the script can fall back to FAO
HWSD v2.0 data under data/FAO_HWSD2.  The FAO fallback samples the HWSD2 raster
to identify the soil mapping unit, reads the HWSD2_LAYERS attribute table, and
uses the dominant soil sequence to fill missing EcoSIM soil variables.

The script uses vector lookup through MUPOLYGON to avoid relying on GDAL's
OpenFileGDB raster support. It is written to be robust to field-name casing in
file geodatabases, because some drivers expose columns such as MUKEY/COKEY/CHKEY
in upper case while others expose mukey/cokey/chkey.

Example
-------
python extract_gssurgo_profile.py \
  --gdb /path/to/gSSURGO_CONUS.gdb \
  --lon -121.85 --lat 39.0 \
  --template /path/to/template.nc.template \
  --out profile.json
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

try:
    from pyproj import CRS, Transformer
except ImportError:  # pragma: no cover - exercised only in lightweight FAO-only installs.
    CRS = None
    Transformer = None

try:
    from shapely.geometry import Point
except ImportError:  # pragma: no cover - exercised only in lightweight FAO-only installs.
    Point = None

FILL = -999.9
EPS = 1e-6
HWSD2_DEFAULT_DIR = Path("data/FAO_HWSD2")
HWSD2_LAYER_DEPTHS_CM = {
    "D1": (0.0, 20.0),
    "D2": (20.0, 40.0),
    "D3": (40.0, 60.0),
    "D4": (60.0, 80.0),
    "D5": (80.0, 100.0),
    "D6": (100.0, 150.0),
    "D7": (150.0, 200.0),
}
SOIL_VARIABLES = (
    "CDPTH",
    "BKDSI",
    "FC",
    "WP",
    "SCNV",
    "SCNH",
    "CSAND",
    "CSILT",
    "ROCK",
    "PH",
    "CEC",
    "CORGC",
    "OM_percent",
)

TEXTURE_WATER_DEFAULTS = {
    "sand": (0.10, 0.04),
    "loamy sand": (0.12, 0.05),
    "sandy loam": (0.18, 0.08),
    "loam": (0.27, 0.12),
    "silt loam": (0.33, 0.13),
    "silt": (0.36, 0.12),
    "sandy clay loam": (0.27, 0.17),
    "clay loam": (0.32, 0.20),
    "silty clay loam": (0.37, 0.21),
    "sandy clay": (0.32, 0.23),
    "silty clay": (0.40, 0.25),
    "clay": (0.40, 0.27),
}
TEXTURE_KSAT_MM_H = {
    "sand": 120.0,
    "loamy sand": 60.0,
    "sandy loam": 25.0,
    "loam": 10.0,
    "silt loam": 7.0,
    "silt": 6.0,
    "sandy clay loam": 5.0,
    "clay loam": 3.0,
    "silty clay loam": 2.0,
    "sandy clay": 1.5,
    "silty clay": 1.0,
    "clay": 0.5,
}


@dataclass
class Horizon:
    top_m: float
    bottom_m: float
    chkey: str
    values: Dict[str, float]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--gdb", help="Path to gSSURGO_CONUS.gdb")
    p.add_argument("--lon", required=True, type=float)
    p.add_argument("--lat", required=True, type=float)
    p.add_argument(
        "--template",
        required=True,
        help="Path to template CDL text file containing CDPTH values",
    )
    p.add_argument("--out", required=True, help="Output JSON path")
    p.add_argument(
        "--extend-last",
        action="store_true",
        help="Extend deepest horizon downward to cover deeper template layers",
    )
    p.add_argument(
        "--fao-hwsd2-dir",
        default=str(HWSD2_DEFAULT_DIR),
        help="Path to FAO HWSD v2.0 directory used as fallback when gSSURGO data are missing",
    )
    p.add_argument(
        "--no-fao-fallback",
        action="store_true",
        help="Disable FAO HWSD v2.0 fallback and fail/use only gSSURGO output",
    )
    p.add_argument(
        "--fao-sequence",
        default=1,
        type=int,
        help="HWSD2 soil sequence to use for fallback; 1 is the dominant soil",
    )
    return p.parse_args()


def import_pyogrio():
    try:
        import pyogrio  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pyogrio is required for gSSURGO extraction and for reading HWSD2.mdb. "
            "Install pyogrio/GDAL or provide HWSD2_LAYERS as CSV/SQLite."
        ) from exc
    return pyogrio


def read_template_depths(template_path: str) -> np.ndarray:
    text = Path(template_path).read_text()
    m = re.search(r"CDPTH\s*=\s*(.*?);", text, flags=re.S)
    if not m:
        raise ValueError("Could not find CDPTH = ... ; block in template")
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", m.group(1))
    depths = np.array([float(x) for x in nums], dtype=float)
    depths = depths[depths > 0]
    if depths.size == 0:
        raise ValueError("No positive CDPTH values found in template")
    if not np.all(np.diff(depths) > 0):
        raise ValueError("Template CDPTH values must be strictly increasing")
    return depths


def get_layer_crs(gdb_path: str, layer: str) -> CRS:
    if CRS is None:
        raise RuntimeError("pyproj is required for gSSURGO coordinate transformation")
    pyogrio = import_pyogrio()
    info = pyogrio.read_info(gdb_path, layer=layer)
    crs = info.get("crs")
    if crs is None:
        raise ValueError(f"No CRS found for layer {layer}")
    return CRS.from_user_input(crs)


def point_to_layer_crs(lon: float, lat: float, layer_crs: CRS) -> Tuple[float, float]:
    if Transformer is None:
        raise RuntimeError("pyproj is required for gSSURGO coordinate transformation")
    transformer = Transformer.from_crs("EPSG:4326", layer_crs, always_xy=True)
    return transformer.transform(lon, lat)


def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with lower-case column names, preserving geometry."""
    out = df.copy()
    rename_map = {c: c.lower() for c in out.columns if isinstance(c, str)}
    return out.rename(columns=rename_map)


def first_present(mapping: Dict[str, str], candidates: Sequence[str]) -> Optional[str]:
    for name in candidates:
        if name.lower() in mapping:
            return mapping[name.lower()]
    return None


def field_map(gdb_path: str, layer: str) -> Dict[str, str]:
    """Map lower-case field name -> actual field name in the GDB layer."""
    pyogrio = import_pyogrio()
    info = pyogrio.read_info(gdb_path, layer=layer)
    fields = info.get("fields")
    if fields is None:
        raise ValueError(f"Could not inspect fields for layer {layer}")
    return {str(f).lower(): str(f) for f in fields}


def actual_columns(gdb_path: str, layer: str, requested: Sequence[str]) -> List[str]:
    fmap = field_map(gdb_path, layer)
    cols: List[str] = []
    missing: List[str] = []
    for req in requested:
        actual = fmap.get(req.lower())
        if actual is None:
            missing.append(req)
        else:
            cols.append(actual)
    if missing:
        raise ValueError(
            f"Layer {layer} is missing required field(s): {', '.join(missing)}. "
            f"Available fields include: {', '.join(list(fmap.values())[:30])}"
        )
    return cols


def read_point_mapunit(gdb_path: str, x: float, y: float) -> pd.Series:
    if Point is None:
        raise RuntimeError("shapely is required for gSSURGO polygon lookup")
    pyogrio = import_pyogrio()
    delta = 1.0
    bbox = (x - delta, y - delta, x + delta, y + delta)
    gdf = pyogrio.read_dataframe(gdb_path, layer="MUPOLYGON", bbox=bbox)
    if gdf.empty:
        raise ValueError("No MUPOLYGON features found at the requested location")
    gdf = canonicalize_columns(gdf)
    pt = Point(x, y)
    hits = gdf[gdf.geometry.contains(pt) | gdf.geometry.touches(pt)]
    if hits.empty:
        hits = gdf.assign(_dist=gdf.geometry.distance(pt)).sort_values("_dist").head(1)
    return hits.iloc[0]


def sql_string_list(values: Iterable[str]) -> str:
    return "(" + ",".join(["'" + str(v).replace("'", "''") + "'" for v in values]) + ")"


def choose_component(gdb_path: str, mukey: str) -> pd.Series:
    pyogrio = import_pyogrio()
    fmap = field_map(gdb_path, "component")
    actual_mukey = first_present(fmap, ["mukey"])
    if actual_mukey is None:
        raise ValueError("Layer component does not contain MUKEY/mukey")

    requested = ["mukey", "cokey", "compname", "comppct_r", "majcompflag"]
    available_requested = [r for r in requested if r.lower() in fmap]
    escaped_mukey = str(mukey).replace("'", "''")
    comp = pyogrio.read_dataframe(
        gdb_path,
        layer="component",
        where=f"{actual_mukey} = '{escaped_mukey}'",
        columns=actual_columns(gdb_path, "component", available_requested),
        read_geometry=False,
    )
    if comp.empty:
        raise ValueError(f"No component records found for mukey={mukey}")
    comp = canonicalize_columns(comp)

    if "majcompflag" in comp.columns:
        major = comp[comp["majcompflag"].astype(str).str.upper().eq("YES")]
        if not major.empty:
            comp = major

    sort_cols = [c for c in ["comppct_r", "cokey"] if c in comp.columns]
    ascending = [False if c == "comppct_r" else True for c in sort_cols]
    if sort_cols:
        comp = comp.sort_values(sort_cols, ascending=ascending)
    return comp.iloc[0]


def load_horizons(gdb_path: str, cokey: str) -> pd.DataFrame:
    pyogrio = import_pyogrio()
    hz_requested = [
        "cokey",
        "chkey",
        "hzdept_r",
        "hzdepb_r",
        "om_r",
        "dbovendry_r",
        "dbthirdbar_r",
        "dbfifteenbar_r",
        "wthirdbar_r",
        "wfifteenbar_r",
        "ksat_r",
        "sandtotal_r",
        "silttotal_r",
        "ph1to1h2o_r",
        "cec7_r",
    ]
    fmap = field_map(gdb_path, "chorizon")
    actual_cokey = first_present(fmap, ["cokey"])
    if actual_cokey is None:
        raise ValueError("Layer chorizon does not contain COKEY/cokey")

    escaped_cokey = str(cokey).replace("'", "''")
    hz = pyogrio.read_dataframe(
        gdb_path,
        layer="chorizon",
        where=f"{actual_cokey} = '{escaped_cokey}'",
        columns=actual_columns(gdb_path, "chorizon", hz_requested),
        read_geometry=False,
    )
    if hz.empty:
        raise ValueError(f"No chorizon records found for cokey={cokey}")
    hz = canonicalize_columns(hz)
    hz = hz.sort_values(["hzdept_r", "hzdepb_r", "chkey"]).reset_index(drop=True)

    chkeys = [str(x) for x in hz["chkey"].tolist()]
    try:
        fr_fmap = field_map(gdb_path, "chfrags")
        if "chkey" not in fr_fmap or "fragvol_r" not in fr_fmap:
            hz["fragvol_r"] = np.nan
            return hz
        fr = pyogrio.read_dataframe(
            gdb_path,
            layer="chfrags",
            where=f"{fr_fmap['chkey']} IN {sql_string_list(chkeys)}",
            columns=actual_columns(gdb_path, "chfrags", ["chkey", "fragvol_r"]),
            read_geometry=False,
        )
        if not fr.empty:
            fr = canonicalize_columns(fr)
            rock = fr.groupby("chkey", as_index=False)["fragvol_r"].sum()
            rock["fragvol_r"] = rock["fragvol_r"].clip(lower=0, upper=100)
            hz = hz.merge(rock, on="chkey", how="left")
        else:
            hz["fragvol_r"] = np.nan
    except Exception:
        hz["fragvol_r"] = np.nan

    return hz


def safe_float(x: object) -> float:
    try:
        if x is None or (isinstance(x, str) and x.strip() == ""):
            return np.nan
        return float(x)
    except Exception:
        return np.nan


def convert_horizons(hz: pd.DataFrame) -> List[Horizon]:
    out: List[Horizon] = []
    for _, r in hz.iterrows():
        top_m = float(r["hzdept_r"]) / 100.0
        bot_m = float(r["hzdepb_r"]) / 100.0
        if not np.isfinite(top_m) or not np.isfinite(bot_m) or bot_m <= top_m:
            continue

        dbod = safe_float(r.get("dbovendry_r"))
        db33 = safe_float(r.get("dbthirdbar_r"))
        db15 = safe_float(r.get("dbfifteenbar_r"))
        w33 = safe_float(r.get("wthirdbar_r"))
        w15 = safe_float(r.get("wfifteenbar_r"))
        om = safe_float(r.get("om_r"))
        ksat = safe_float(r.get("ksat_r"))
        sand = safe_float(r.get("sandtotal_r"))
        silt = safe_float(r.get("silttotal_r"))

        fc = np.nan
        wp = np.nan
        if np.isfinite(w33):
            bd_for_fc = db33 if np.isfinite(db33) else dbod
            if np.isfinite(bd_for_fc):
                fc = (w33 / 100.0) * bd_for_fc
        if np.isfinite(w15):
            bd_for_wp = db15 if np.isfinite(db15) else dbod
            if np.isfinite(bd_for_wp):
                wp = (w15 / 100.0) * bd_for_wp

        rock = safe_float(r.get("fragvol_r"))
        if np.isfinite(rock):
            rock /= 100.0

        corgc = np.nan
        if np.isfinite(om):
            corgc = om * 10.0 * 0.58

        vals = {
            "BKDSI": dbod,
            "FC": fc,
            "WP": wp,
            "SCNV": ksat * 3.6 if np.isfinite(ksat) else np.nan,
            "SCNH": ksat * 3.6 if np.isfinite(ksat) else np.nan,
            "CSAND": sand * 10.0 if np.isfinite(sand) else np.nan,
            "CSILT": silt * 10.0 if np.isfinite(silt) else np.nan,
            "ROCK": rock,
            "PH": safe_float(r.get("ph1to1h2o_r")),
            "CEC": safe_float(r.get("cec7_r")),
            "CORGC": corgc,
            "OM": om,
        }
        out.append(Horizon(top_m=top_m, bottom_m=bot_m, chkey=str(r["chkey"]), values=vals))
    if not out:
        raise ValueError("No usable horizon intervals found")
    return out


def interpolate_profile(
    horizons: Sequence[Horizon],
    target_bottoms: Sequence[float],
    varname: str,
    log_interp: bool = False,
    extend_last: bool = False,
) -> List[float]:
    target_bottoms = np.asarray(target_bottoms, dtype=float)
    target_tops = np.concatenate(([0.0], target_bottoms[:-1]))
    results: List[float] = []

    src = [(h.top_m, h.bottom_m, safe_float(h.values.get(varname))) for h in horizons]
    if extend_last and src:
        last_top, last_bot, last_val = src[-1]
        if target_bottoms[-1] > last_bot and np.isfinite(last_val):
            src = list(src) + [(last_bot, float(target_bottoms[-1]), last_val)]

    for ttop, tbot in zip(target_tops, target_bottoms):
        overlaps = []
        weights = []
        for stop, sbot, sval in src:
            if not np.isfinite(sval):
                continue
            overlap = min(tbot, sbot) - max(ttop, stop)
            if overlap > 0:
                overlaps.append(sval)
                weights.append(overlap)
        if not weights:
            results.append(FILL)
            continue
        w = np.asarray(weights, dtype=float)
        v = np.asarray(overlaps, dtype=float)
        if log_interp:
            vv = np.maximum(v, EPS)
            agg = float(np.exp(np.average(np.log(vv), weights=w)))
        else:
            agg = float(np.average(v, weights=w))
        results.append(agg)
    return results


def finite_or_none(x: float) -> Optional[float]:
    x = safe_float(x)
    return None if not np.isfinite(x) else float(x)


def valid_ecosim_value(varname: str, value: object) -> bool:
    value = safe_float(value)
    if not np.isfinite(value) or value <= FILL + 1.0:
        return False

    limits = {
        "CDPTH": (0.0, None),
        "BKDSI": (0.1, 2.65),
        "FC": (0.0, 0.85),
        "WP": (0.0, 0.85),
        "SCNV": (0.0, None),
        "SCNH": (0.0, None),
        "CSAND": (0.0, 1000.0),
        "CSILT": (0.0, 1000.0),
        "ROCK": (0.0, 1.0),
        "PH": (0.0, 14.0),
        "CEC": (0.0, None),
        "CORGC": (0.0, None),
        "OM_percent": (0.0, None),
    }
    lower, upper = limits.get(varname, (None, None))
    if lower is not None and value < lower:
        return False
    if upper is not None and value > upper:
        return False
    return True


def parse_hwsd2_header(header_path: Path) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for line in header_path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].upper()
        raw_value = parts[1]
        try:
            value: Any = float(raw_value)
        except ValueError:
            value = raw_value
        fields[key] = value

    required = ["NROWS", "NCOLS", "NBITS", "ULXMAP", "ULYMAP", "XDIM", "YDIM"]
    missing = [key for key in required if key not in fields]
    if missing:
        raise ValueError(f"HWSD2 header missing required field(s): {', '.join(missing)}")
    return fields


def find_hwsd2_raster_paths(hwsd2_dir: Path) -> Tuple[Path, Path]:
    candidates = [
        hwsd2_dir / "HWSD2_RASTER" / "HWSD2.bil",
        hwsd2_dir / "HWSD2.bil",
    ]
    for bil_path in candidates:
        if bil_path.exists():
            hdr_path = bil_path.with_suffix(".hdr")
            if hdr_path.exists():
                return bil_path, hdr_path
    raise FileNotFoundError(f"Could not find HWSD2.bil/HWSD2.hdr under {hwsd2_dir}")


def sample_hwsd2_smu_id(hwsd2_dir: Path, lon: float, lat: float) -> int:
    bil_path, hdr_path = find_hwsd2_raster_paths(hwsd2_dir)
    hdr = parse_hwsd2_header(hdr_path)
    nrows = int(hdr["NROWS"])
    ncols = int(hdr["NCOLS"])
    nbits = int(hdr["NBITS"])
    if nbits != 16:
        raise ValueError(f"Expected 16-bit HWSD2 raster, found NBITS={nbits}")

    xdim = float(hdr["XDIM"])
    ydim = float(hdr["YDIM"])
    ulx = float(hdr["ULXMAP"])
    uly = float(hdr["ULYMAP"])
    lon = ((lon + 180.0) % 360.0) - 180.0

    col = int(round((lon - ulx) / xdim))
    row = int(round((uly - lat) / ydim))
    if row < 0 or row >= nrows or col < 0 or col >= ncols:
        raise ValueError(f"Location lon={lon}, lat={lat} is outside the HWSD2 raster extent")

    total_row_bytes = int(hdr.get("TOTALROWBYTES", ncols * 2))
    byteorder = str(hdr.get("BYTEORDER", "I")).upper()
    endian = "little" if byteorder == "I" else "big"
    offset = row * total_row_bytes + col * 2
    with bil_path.open("rb") as handle:
        handle.seek(offset)
        raw = handle.read(2)
    if len(raw) != 2:
        raise ValueError(f"Could not read HWSD2 raster cell row={row}, col={col}")

    value = int.from_bytes(raw, byteorder=endian, signed=False)
    nodata = int(hdr.get("NODATA", 65535))
    if value == nodata or value <= 0:
        raise ValueError(f"HWSD2 raster returned NoData for lon={lon}, lat={lat}")
    return value


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip().upper() for c in out.columns]
    return out


def hwsd_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    columns = {str(c).upper(): str(c) for c in df.columns}
    for candidate in candidates:
        actual = columns.get(candidate.upper())
        if actual is not None:
            return actual
    return None


def read_hwsd2_layers_csv(hwsd2_dir: Path) -> Optional[pd.DataFrame]:
    patterns = ["HWSD2_LAYERS.csv", "HWSD2_LAYERS.txt", "HWSD2_LAYERS.tsv"]
    for pattern in patterns:
        for path in hwsd2_dir.rglob(pattern):
            sep = "\t" if path.suffix.lower() == ".tsv" else None
            return pd.read_csv(path, sep=sep, engine="python")
    return None


def read_hwsd2_layers_sqlite(hwsd2_dir: Path, smu_id: int) -> Optional[pd.DataFrame]:
    sqlite_paths = list(hwsd2_dir.rglob("*.sqlite")) + list(hwsd2_dir.rglob("*.db"))
    for path in sqlite_paths:
        with sqlite3.connect(path) as conn:
            tables = pd.read_sql_query(
                "select name from sqlite_master where type in ('table', 'view')",
                conn,
            )["name"].tolist()
            table = next((name for name in tables if name.lower() == "hwsd2_layers"), None)
            if table is None:
                continue
            query = f'SELECT * FROM "{table}" WHERE HWSD2_SMU_ID = ?'
            return pd.read_sql_query(query, conn, params=(smu_id,))
    return None


def read_hwsd2_layers_mdb(hwsd2_dir: Path, smu_id: int) -> Optional[pd.DataFrame]:
    mdb_paths = list(hwsd2_dir.rglob("*.mdb"))
    if not mdb_paths:
        return None

    pyogrio = import_pyogrio()
    mdb_path = mdb_paths[0]
    layers = pyogrio.list_layers(mdb_path)
    layer_names = [str(row[0]) for row in layers]

    selected_layer = next((name for name in layer_names if name.lower() == "hwsd2_layers"), None)
    if selected_layer is None:
        for layer in layer_names:
            try:
                fmap = field_map(str(mdb_path), layer)
            except Exception:
                continue
            required = {"hwsd2_smu_id", "layer", "topdep", "botdep"}
            if required.issubset(set(fmap)):
                selected_layer = layer
                break

    if selected_layer is None:
        raise ValueError(f"Could not find HWSD2_LAYERS table in {mdb_path}")

    fmap = field_map(str(mdb_path), selected_layer)
    smu_col = first_present(fmap, ["HWSD2_SMU_ID", "SMU_ID"])
    if smu_col is None:
        raise ValueError(f"Table {selected_layer} has no HWSD2_SMU_ID field")

    return pyogrio.read_dataframe(
        mdb_path,
        layer=selected_layer,
        where=f"{smu_col} = {int(smu_id)}",
        read_geometry=False,
    )


def read_hwsd2_layers(hwsd2_dir: Path, smu_id: int) -> pd.DataFrame:
    readers = (
        lambda: read_hwsd2_layers_csv(hwsd2_dir),
        lambda: read_hwsd2_layers_sqlite(hwsd2_dir, smu_id),
        lambda: read_hwsd2_layers_mdb(hwsd2_dir, smu_id),
    )
    errors: List[str] = []
    for reader in readers:
        try:
            df = reader()
        except Exception as exc:
            errors.append(str(exc))
            continue
        if df is not None and not df.empty:
            return standardize_columns(df)

    detail = "; ".join(errors) if errors else "no HWSD2_LAYERS CSV, SQLite, or MDB table found"
    raise ValueError(f"Could not read HWSD2_LAYERS from {hwsd2_dir}: {detail}")


def select_hwsd2_rows(df: pd.DataFrame, smu_id: int, sequence: int) -> Tuple[pd.DataFrame, int]:
    smu_col = hwsd_column(df, ["HWSD2_SMU_ID", "SMU_ID", "HWSD2_ID"])
    if smu_col is None:
        raise ValueError("HWSD2_LAYERS table has no HWSD2_SMU_ID column")
    rows = df[pd.to_numeric(df[smu_col], errors="coerce").eq(float(smu_id))].copy()
    if rows.empty:
        raise ValueError(f"No HWSD2_LAYERS rows found for HWSD2_SMU_ID={smu_id}")

    seq_col = hwsd_column(rows, ["SEQUENCE", "SEQ"])
    selected_sequence = sequence
    if seq_col is not None:
        seq_values = pd.to_numeric(rows[seq_col], errors="coerce")
        selected = rows[seq_values.eq(float(sequence))]
        if selected.empty:
            share_col = hwsd_column(rows, ["SHARE"])
            if share_col is not None:
                share_values = pd.to_numeric(rows[share_col], errors="coerce")
                idx = share_values.idxmax()
                selected_sequence = int(safe_float(rows.loc[idx, seq_col]))
            else:
                selected_sequence = int(seq_values.min())
            selected = rows[seq_values.eq(float(selected_sequence))]
        rows = selected.copy()

    layer_col = hwsd_column(rows, ["LAYER"])
    top_col = hwsd_column(rows, ["TOPDEP"])
    if layer_col is not None:
        rows["_layer_order"] = rows[layer_col].astype(str).str.upper().str.extract(r"(\d+)").astype(float)
        rows = rows.sort_values("_layer_order")
    elif top_col is not None:
        rows = rows.sort_values(top_col)
    return rows, selected_sequence


def hwsd_value(row: pd.Series, candidates: Sequence[str], lower: Optional[float] = None, upper: Optional[float] = None) -> float:
    for candidate in candidates:
        if candidate.upper() not in row.index:
            continue
        value = safe_float(row[candidate.upper()])
        if not np.isfinite(value) or value < 0:
            continue
        if lower is not None and value < lower:
            continue
        if upper is not None and value > upper:
            continue
        return value
    return np.nan


def usda_texture_class(sand: float, silt: float, clay: float) -> str:
    sand = safe_float(sand)
    silt = safe_float(silt)
    clay = safe_float(clay)
    if not all(np.isfinite(x) for x in [sand, silt, clay]):
        return "loam"

    if clay >= 40:
        if sand >= 45:
            return "sandy clay"
        if silt >= 40:
            return "silty clay"
        return "clay"
    if clay >= 27:
        if sand >= 45:
            return "sandy clay loam"
        if silt >= 40:
            return "silty clay loam"
        return "clay loam"
    if clay >= 20:
        if sand >= 52:
            return "sandy clay loam"
        if silt >= 50:
            return "silt loam"
        return "loam"
    if sand >= 85 and clay < 10:
        return "sand"
    if sand >= 70 and clay < 15:
        return "loamy sand"
    if sand >= 52 and clay < 20:
        return "sandy loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if silt >= 50:
        return "silt loam"
    return "loam"


def derive_water_retention(sand: float, silt: float, clay: float, awc: float, bulk_density: float) -> Tuple[float, float, str]:
    texture = usda_texture_class(sand, silt, clay)
    default_fc, default_wp = TEXTURE_WATER_DEFAULTS.get(texture, TEXTURE_WATER_DEFAULTS["loam"])
    fc = default_fc
    wp = default_wp

    awc = safe_float(awc)
    if np.isfinite(awc) and 20.0 <= awc <= 300.0:
        wp = default_wp
        fc = wp + awc / 1000.0

    bulk_density = safe_float(bulk_density)
    if np.isfinite(bulk_density) and bulk_density > 0:
        porosity = 1.0 - bulk_density / 2.65
        if np.isfinite(porosity) and porosity > 0:
            fc = min(fc, porosity * 0.95)

    fc = float(np.clip(fc, 0.02, 0.85))
    wp = float(np.clip(wp, 0.01, min(fc - EPS, 0.80)))
    if wp >= fc:
        wp = max(0.01, fc * 0.6)
    return fc, wp, texture


def convert_hwsd2_rows_to_horizons(rows: pd.DataFrame, smu_id: int, sequence: int) -> List[Horizon]:
    horizons: List[Horizon] = []
    for _, row in rows.iterrows():
        layer = str(row.get("LAYER", "")).upper()
        if layer in HWSD2_LAYER_DEPTHS_CM:
            default_top, default_bottom = HWSD2_LAYER_DEPTHS_CM[layer]
        else:
            default_top, default_bottom = np.nan, np.nan

        top_cm = hwsd_value(row, ["TOPDEP", "TOP_DEPTH"], lower=0.0)
        bottom_cm = hwsd_value(row, ["BOTDEP", "BOT_DEPTH", "BOTDEPTH"], lower=0.0)
        if not np.isfinite(top_cm):
            top_cm = default_top
        if not np.isfinite(bottom_cm):
            bottom_cm = default_bottom

        top_m = top_cm / 100.0
        bottom_m = bottom_cm / 100.0
        if not np.isfinite(top_m) or not np.isfinite(bottom_m) or bottom_m <= top_m:
            continue

        bulk = hwsd_value(row, ["BULK", "BULK_DENSITY", "REF_BULK", "REF_BULK_DENSITY"], lower=0.1, upper=2.65)
        sand = hwsd_value(row, ["SAND"], lower=0.0, upper=100.0)
        silt = hwsd_value(row, ["SILT"], lower=0.0, upper=100.0)
        clay = hwsd_value(row, ["CLAY"], lower=0.0, upper=100.0)
        coarse = hwsd_value(row, ["COARSE", "COARSE_FRAGMENTS"], lower=0.0, upper=100.0)
        org_carbon = hwsd_value(row, ["ORG_CARBON", "ORGANIC_CARBON"], lower=0.0)
        ph_water = hwsd_value(row, ["PH_WATER", "PH"], lower=0.0, upper=14.0)
        cec = hwsd_value(row, ["CEC_SOIL", "CECSOIL", "CEC"], lower=0.0)
        awc = hwsd_value(row, ["AWC"], lower=0.0)
        fc, wp, texture = derive_water_retention(sand, silt, clay, awc, bulk)
        ksat = TEXTURE_KSAT_MM_H.get(texture, TEXTURE_KSAT_MM_H["loam"])

        vals = {
            "BKDSI": bulk,
            "FC": fc,
            "WP": wp,
            "SCNV": ksat,
            "SCNH": ksat,
            "CSAND": sand * 10.0 if np.isfinite(sand) else np.nan,
            "CSILT": silt * 10.0 if np.isfinite(silt) else np.nan,
            "ROCK": coarse / 100.0 if np.isfinite(coarse) else np.nan,
            "PH": ph_water,
            "CEC": cec,
            "CORGC": org_carbon,
            "OM": org_carbon / 10.0 if np.isfinite(org_carbon) else np.nan,
            "TEXTURE_USDA_DERIVED": texture,
        }
        horizons.append(
            Horizon(
                top_m=top_m,
                bottom_m=bottom_m,
                chkey=f"hwsd2:{smu_id}:{sequence}:{layer or len(horizons) + 1}",
                values=vals,
            )
        )

    if not horizons:
        raise ValueError("No usable FAO HWSD2 layer intervals found")
    return horizons


def interpolated_from_horizons(target_depths: np.ndarray, horizons: Sequence[Horizon], extend_last: bool) -> Dict[str, List[float]]:
    return {
        "CDPTH": target_depths.tolist(),
        "BKDSI": interpolate_profile(horizons, target_depths, "BKDSI", extend_last=extend_last),
        "FC": interpolate_profile(horizons, target_depths, "FC", extend_last=extend_last),
        "WP": interpolate_profile(horizons, target_depths, "WP", extend_last=extend_last),
        "SCNV": interpolate_profile(horizons, target_depths, "SCNV", extend_last=extend_last),
        "SCNH": interpolate_profile(horizons, target_depths, "SCNH", extend_last=extend_last),
        "CSAND": interpolate_profile(horizons, target_depths, "CSAND", extend_last=extend_last),
        "CSILT": interpolate_profile(horizons, target_depths, "CSILT", extend_last=extend_last),
        "ROCK": interpolate_profile(horizons, target_depths, "ROCK", extend_last=extend_last),
        "PH": interpolate_profile(horizons, target_depths, "PH", extend_last=extend_last),
        "CEC": interpolate_profile(horizons, target_depths, "CEC", extend_last=extend_last),
        "CORGC": interpolate_profile(horizons, target_depths, "CORGC", log_interp=True, extend_last=extend_last),
        "OM_percent": interpolate_profile(horizons, target_depths, "OM", log_interp=True, extend_last=extend_last),
    }


def build_gssurgo_output(args: argparse.Namespace, target_depths: np.ndarray) -> Dict[str, Any]:
    if not args.gdb:
        raise ValueError("No gSSURGO geodatabase was provided")

    layer_crs = get_layer_crs(args.gdb, "MUPOLYGON")
    x, y = point_to_layer_crs(args.lon, args.lat, layer_crs)
    poly = read_point_mapunit(args.gdb, x, y)

    if "mukey" not in poly.index:
        raise ValueError(
            "MUPOLYGON lookup succeeded, but MUKEY/mukey was not found in the returned fields. "
            f"Fields returned: {', '.join(map(str, poly.index.tolist()))}"
        )
    mukey = str(poly["mukey"])

    comp = choose_component(args.gdb, mukey)
    if "cokey" not in comp.index:
        raise ValueError(
            "Component lookup succeeded, but COKEY/cokey was not found in the returned fields. "
            f"Fields returned: {', '.join(map(str, comp.index.tolist()))}"
        )
    cokey = str(comp["cokey"])

    hzdf = load_horizons(args.gdb, cokey)
    horizons = convert_horizons(hzdf)
    interpolated = interpolated_from_horizons(target_depths, horizons, args.extend_last)

    output = {
        "input": {
            "gdb": str(args.gdb),
            "lon": args.lon,
            "lat": args.lat,
            "template": str(args.template),
            "extend_last": bool(args.extend_last),
            "fao_hwsd2_dir": str(args.fao_hwsd2_dir),
            "fao_fallback_enabled": not bool(args.no_fao_fallback),
        },
        "selection": {
            "mukey": mukey,
            "cokey": cokey,
            "component_name": str(comp.get("compname", "")),
            "component_pct_r": finite_or_none(comp.get("comppct_r")),
        },
        "source_horizons": [
            {
                "top_m": h.top_m,
                "bottom_m": h.bottom_m,
                "chkey": h.chkey,
                **{k: finite_or_none(v) for k, v in h.values.items()},
            }
            for h in horizons
        ],
        "template_depths_m": target_depths.tolist(),
        "interpolated": interpolated,
        "notes": {
            "primary_soil_source": "gSSURGO",
            "BKDSI_source": "dbovendry_r",
            "FC_formula": "(wthirdbar_r / 100) * dbthirdbar_r",
            "WP_formula": "(wfifteenbar_r / 100) * dbfifteenbar_r",
            "SCN_formula": "ksat_r [um/s] * 3.6 => mm/h",
            "CSAND_CSILT_formula": "percent * 10 => kg/Mg",
            "ROCK_source": "sum(chfrags.fragvol_r) / 100",
            "CORGC_formula": "om_r [%] * 10 * 0.58 => kg C / Mg soil",
            "CORGC_interpolation": "overlap-weighted geometric mean on target layers",
        },
        "sources_by_variable": {
            varname: [
                "gssurgo" if valid_ecosim_value(varname, value) else "missing"
                for value in interpolated.get(varname, [])
            ]
            for varname in SOIL_VARIABLES
        },
    }
    return output


def build_fao_hwsd2_output(args: argparse.Namespace, target_depths: np.ndarray) -> Dict[str, Any]:
    hwsd2_dir = Path(args.fao_hwsd2_dir)
    if not hwsd2_dir.exists():
        raise FileNotFoundError(f"FAO HWSD2 directory not found: {hwsd2_dir}")

    smu_id = sample_hwsd2_smu_id(hwsd2_dir, args.lon, args.lat)
    rows = read_hwsd2_layers(hwsd2_dir, smu_id)
    rows, selected_sequence = select_hwsd2_rows(rows, smu_id, args.fao_sequence)
    horizons = convert_hwsd2_rows_to_horizons(rows, smu_id, selected_sequence)
    interpolated = interpolated_from_horizons(target_depths, horizons, args.extend_last)

    return {
        "input": {
            "gdb": str(args.gdb) if args.gdb else None,
            "lon": args.lon,
            "lat": args.lat,
            "template": str(args.template),
            "extend_last": bool(args.extend_last),
            "fao_hwsd2_dir": str(hwsd2_dir),
        },
        "selection": {
            "source": "FAO_HWSD2",
            "hwsd2_smu_id": smu_id,
            "sequence": selected_sequence,
        },
        "source_horizons": [
            {
                "top_m": h.top_m,
                "bottom_m": h.bottom_m,
                "chkey": h.chkey,
                **{k: finite_or_none(v) if k != "TEXTURE_USDA_DERIVED" else v for k, v in h.values.items()},
            }
            for h in horizons
        ],
        "template_depths_m": target_depths.tolist(),
        "interpolated": interpolated,
        "notes": {
            "primary_soil_source": "FAO_HWSD2",
            "BKDSI_source": "HWSD2_LAYERS.BULK, falling back to REF_BULK when needed",
            "FC_WP_formula": "texture-class defaults with HWSD2 AWC adjustment where AWC is a plausible mm/m value",
            "SCN_formula": "texture-class saturated hydraulic conductivity estimate from HWSD2 sand/silt/clay",
            "CSAND_CSILT_formula": "SAND/SILT percent * 10 => kg/Mg",
            "ROCK_source": "COARSE [% volume] / 100",
            "CORGC_formula": "ORG_CARBON [g/kg] => kg C / Mg soil",
            "CORGC_interpolation": "overlap-weighted geometric mean on target layers",
        },
        "sources_by_variable": {
            varname: [
                "fao_hwsd2" if valid_ecosim_value(varname, value) else "missing"
                for value in interpolated.get(varname, [])
            ]
            for varname in SOIL_VARIABLES
        },
    }


def merge_fao_fallback(primary: Dict[str, Any], fallback: Dict[str, Any]) -> Dict[str, Any]:
    primary_interp = primary.setdefault("interpolated", {})
    fallback_interp = fallback.get("interpolated", {})
    sources = primary.setdefault("sources_by_variable", {})
    fallback_counts: Dict[str, int] = {}

    for varname in SOIL_VARIABLES:
        if varname == "CDPTH" or varname not in fallback_interp:
            continue
        primary_values = list(primary_interp.get(varname, []))
        fallback_values = list(fallback_interp.get(varname, []))
        if not primary_values:
            primary_values = [FILL] * len(fallback_values)

        var_sources = list(sources.get(varname, []))
        if len(var_sources) < len(primary_values):
            var_sources.extend(["missing"] * (len(primary_values) - len(var_sources)))

        filled = 0
        for idx, fallback_value in enumerate(fallback_values):
            if idx >= len(primary_values):
                break
            if valid_ecosim_value(varname, primary_values[idx]):
                continue
            if not valid_ecosim_value(varname, fallback_value):
                continue
            primary_values[idx] = fallback_value
            var_sources[idx] = "fao_hwsd2_fallback"
            filled += 1

        primary_interp[varname] = primary_values
        sources[varname] = var_sources
        if filled:
            fallback_counts[varname] = filled

    primary.setdefault("fallbacks", {})["fao_hwsd2"] = {
        "selection": fallback.get("selection", {}),
        "filled_values_by_variable": fallback_counts,
    }
    primary.setdefault("notes", {})["fao_hwsd2_fallback"] = (
        "FAO HWSD2 values were used only where gSSURGO interpolated values were missing or invalid."
    )
    return primary


def main() -> None:
    args = parse_args()
    target_depths = read_template_depths(args.template)
    errors: List[str] = []

    output: Optional[Dict[str, Any]] = None
    try:
        output = build_gssurgo_output(args, target_depths)
    except Exception as exc:
        errors.append(f"gSSURGO extraction failed: {exc}")

    fallback_output: Optional[Dict[str, Any]] = None
    if not args.no_fao_fallback:
        try:
            fallback_output = build_fao_hwsd2_output(args, target_depths)
        except Exception as exc:
            errors.append(f"FAO HWSD2 fallback failed: {exc}")

    if output is not None and fallback_output is not None:
        output = merge_fao_fallback(output, fallback_output)
    elif output is None and fallback_output is not None:
        output = fallback_output
        output.setdefault("fallbacks", {})["gssurgo_failure"] = errors
    elif output is None:
        raise RuntimeError("; ".join(errors) if errors else "No soil data source was available")
    elif errors:
        # gSSURGO succeeded but the FAO fallback could not run; keep the reason so
        # remaining missing layers are not mistaken for "no fallback needed".
        output.setdefault("fallbacks", {})["fao_hwsd2_failure"] = errors
        for message in errors:
            print(f"WARNING: {message}", file=sys.stderr)

    missing = {
        varname: [index + 1 for index, source in enumerate(sources) if source == "missing"]
        for varname, sources in output.get("sources_by_variable", {}).items()
    }
    missing = {varname: layers for varname, layers in missing.items() if layers}
    if missing:
        output["missing_layers_by_variable"] = missing
        print(f"WARNING: soil layers still missing after fallback: {missing}", file=sys.stderr)

    Path(args.out).write_text(json.dumps(output, indent=2))
    print(
        json.dumps(
            {
                "selection": output["selection"],
                "fallbacks": output.get("fallbacks", {}),
                "out": str(args.out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
