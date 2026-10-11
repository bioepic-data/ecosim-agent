#!/usr/bin/env python3
"""Download NLDAS File A point time series through Giovanni and convert for EcoSIM."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests


COLLECTION = "NLDAS_FORA0125_H.2.0"
COLLECTION_CONCEPT_ID = "C2033151148-GES_DISC"
GIOVANNI_ENDPOINT = "https://api.giovanni.earthdata.nasa.gov/proxy-timeseries"
FILL_VALUE = 1.0e30
PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
NLDAS_VARIABLES = ["Tair", "Wind_E", "Wind_N", "Rainf", "Qair", "SWdown", "PSurf"]
DATAFIELD_IDS = {var: f"NLDAS_FORA0125_H_2_0_{var}" for var in NLDAS_VARIABLES}
UNITS = {
    "Tair": "K",
    "Wind_E": "m s-1",
    "Wind_N": "m s-1",
    "Rainf": "kg m-2",
    "Qair": "kg kg-1",
    "SWdown": "W m-2",
    "PSurf": "Pa",
    "TMPH": "degC",
    "WINDH": "m s-1",
    "RAINH": "mm h-1",
    "DWPTH": "kPa",
    "SRADH": "W m-2",
    "PATM": "kPa",
}
ECOSIM_LONG_NAMES = {
    "TMPH": "hourly air temperature",
    "WINDH": "hourly wind speed",
    "RAINH": "hourly precipitation",
    "DWPTH": "hourly atmospheric vapor pressure",
    "SRADH": "hourly incident shortwave radiation",
    "PATM": "hourly surface atmospheric pressure",
}
CHEMISTRY_VARIABLES = [
    "PHRG",
    "CN4RIG",
    "CNORIG",
    "CPORG",
    "CALRG",
    "CFERG",
    "CCARG",
    "CMGRG",
    "CNARG",
    "CKARG",
    "CSORG",
    "CCLRG",
]
CHEMISTRY_METADATA = {
    "PHRG": ("pH in precipitation", "pH"),
    "CN4RIG": ("NH4 conc in precip", "gN m^-3"),
    "CNORIG": ("NO3 conc in precip", "gN m^-3"),
    "CPORG": ("H2PO4 conc in precip", "gP m^-3"),
    "CALRG": ("Al conc in precip", "gAl m^-3"),
    "CFERG": ("Fe conc in precip", "gFe m^-3"),
    "CCARG": ("Ca conc in precip", "gCa m^-3"),
    "CMGRG": ("Mg conc in precip", "gMg m^-3"),
    "CNARG": ("Na conc in precip", "gNa m^-3"),
    "CKARG": ("K conc in precip", "gK m^-3"),
    "CSORG": ("SO4 conc in precip", "gS m^-3"),
    "CCLRG": ("Cl conc in precip", "gCl m^-3"),
}
NATIVE_VALIDITY_LIMITS = {
    "Tair": {
        "min": 183.15,
        "max": 333.15,
        "units": "K",
        "reason": "Equivalent to -90 to 60 degC.",
    },
    "Wind_E": {
        "min": -75.0,
        "max": 75.0,
        "units": "m s-1",
        "reason": "Broad physical screen for 10 m wind components.",
    },
    "Wind_N": {
        "min": -75.0,
        "max": 75.0,
        "units": "m s-1",
        "reason": "Broad physical screen for 10 m wind components.",
    },
    "Rainf": {
        "min": 0.0,
        "max": 500.0,
        "units": "kg m-2 h-1",
        "reason": "Hourly precipitation accumulation cannot be negative; upper bound is intentionally broad.",
    },
    "Qair": {
        "min": 0.0,
        "max": 0.05,
        "units": "kg kg-1",
        "reason": "Broad physical screen for near-surface specific humidity.",
    },
    "SWdown": {
        "min": 0.0,
        "max": 1400.0,
        "units": "W m-2",
        "reason": "Incident shortwave radiation should be non-negative and below clear-sky extreme values.",
    },
    "PSurf": {
        "min": 50000.0,
        "max": 110000.0,
        "units": "Pa",
        "reason": "Broad physical screen for surface atmospheric pressure.",
    },
}
ECOSIM_VALIDITY_LIMITS = {
    "TMPH": {
        "min": -90.0,
        "max": 60.0,
        "units": "degC",
        "reason": "EcoSIM air temperature physical screen.",
    },
    "WINDH": {
        "min": 0.0,
        "max": 75.0,
        "units": "m s-1",
        "reason": "Scalar wind speed must be non-negative.",
    },
    "RAINH": {
        "min": 0.0,
        "max": 500.0,
        "units": "mm h-1",
        "reason": "Hourly precipitation accumulation cannot be negative; upper bound is intentionally broad.",
    },
    "DWPTH": {
        "min": 0.0,
        "max": 15.0,
        "units": "kPa",
        "reason": "Actual vapor pressure must be non-negative; upper bound is broad for near-surface air.",
    },
    "SRADH": {
        "min": 0.0,
        "max": 1400.0,
        "units": "W m-2",
        "reason": "Incident shortwave radiation should be non-negative and below clear-sky extreme values.",
    },
    "PATM": {
        "min": 50.0,
        "max": 110.0,
        "units": "kPa",
        "reason": "Broad physical screen for surface atmospheric pressure.",
    },
}
ANNUAL_VALIDITY_LIMITS = {
    "Z0G": {"min": 0.01, "max": 100.0, "units": "m", "reason": "Wind measurement height must be positive."},
    "IFLGW": {"allowed": [0, 1], "units": "flag", "reason": "EcoSIM vegetation-height wind flag."},
    "ZNOONG": {"min": 0.0, "max": 24.0, "units": "hour", "reason": "Solar noon must fall within a UTC day."},
    "PHRG": {"min": 0.0, "max": 14.0, "units": "pH", "reason": "Precipitation pH physical screen."},
    "CN4RIG": {"min": 0.0, "max": None, "units": "gN m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CNORIG": {"min": 0.0, "max": None, "units": "gN m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CPORG": {"min": 0.0, "max": None, "units": "gP m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CALRG": {"min": 0.0, "max": None, "units": "gAl m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CFERG": {"min": 0.0, "max": None, "units": "gFe m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CCARG": {"min": 0.0, "max": None, "units": "gCa m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CMGRG": {"min": 0.0, "max": None, "units": "gMg m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CNARG": {"min": 0.0, "max": None, "units": "gNa m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CKARG": {"min": 0.0, "max": None, "units": "gK m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CSORG": {"min": 0.0, "max": None, "units": "gS m^-3", "reason": "Precipitation solute concentration cannot be negative."},
    "CCLRG": {"min": 0.0, "max": None, "units": "gCl m^-3", "reason": "Precipitation solute concentration cannot be negative."},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download NLDAS_FORA0125_H point climate forcing for EcoSIM."
    )
    parser.add_argument("--site-id", default="US-UMB")
    parser.add_argument("--lon", type=float, default=-84.7138)
    parser.add_argument("--lat", type=float, default=45.5598)
    parser.add_argument("--start-year", type=int, default=1980)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--chunk-years", type=int, default=10)
    parser.add_argument("--output-dir", default="result/nldas")
    parser.add_argument("--site-output-dir", default="result/US-UMB")
    parser.add_argument(
        "--template-climate-file",
        help=(
            "Optional existing EcoSIM climate NetCDF to copy annual chemistry "
            "forcing variables from. Defaults to <site-output-dir>/<site-id>_ecosim_climate.nc when present."
        ),
    )
    parser.add_argument(
        "--add-us-chemistry",
        choices=["auto", "always", "never"],
        default="auto",
        help=(
            "Add annual EcoSIM precipitation chemistry variables from NADP. "
            "Auto adds them for NLDAS/US-domain coordinates."
        ),
    )
    parser.add_argument(
        "--chemistry-input",
        default="data/nadp_data_grids",
        help="NADP raster directory used for US precipitation chemistry variables.",
    )
    parser.add_argument(
        "--chemistry-output",
        help="Optional JSON path for extracted NADP chemistry before NetCDF insertion.",
    )
    parser.add_argument("--credential-profile", default=os.path.expanduser("~/.bashrc"))
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-invalid",
        action="store_true",
        help="Write outputs even if validity checks find out-of-range values.",
    )
    return parser.parse_args()


def read_profile_credentials(profile_file: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    path = Path(profile_file).expanduser()
    if not path.exists():
        return None, None, None
    command = (
        'source "$CREDENTIAL_PROFILE" >/dev/null 2>&1; '
        'printf "%s\\0%s\\0" "${USR_NLDAS-}" "${PASSWD_NLDAS-}"'
    )
    env = os.environ.copy()
    env["CREDENTIAL_PROFILE"] = str(path)
    result = subprocess.run(
        ["bash", "-lc", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        check=False,
    )
    parts = result.stdout.split(b"\0")
    if len(parts) < 2:
        return None, None, None
    username = parts[0].decode("utf-8") or None
    password = parts[1].decode("utf-8") or None
    if username or password:
        return username, password, f"credential-profile:{path}"
    return None, None, None


def resolve_credentials(args: argparse.Namespace) -> Tuple[str, str, str]:
    if args.username or args.password:
        if not args.username or not args.password:
            raise ValueError("Both --username and --password are required when either is supplied.")
        return args.username, args.password, "cli"
    if os.environ.get("USR_NLDAS") or os.environ.get("PASSWD_NLDAS"):
        username = os.environ.get("USR_NLDAS")
        password = os.environ.get("PASSWD_NLDAS")
        if not username or not password:
            raise ValueError("Both USR_NLDAS and PASSWD_NLDAS are required when either is supplied.")
        return username, password, "env:USR_NLDAS/PASSWD_NLDAS"
    username, password, source = read_profile_credentials(args.credential_profile)
    if not username or not password:
        raise ValueError("Could not find USR_NLDAS and PASSWD_NLDAS in the environment or credential profile.")
    return username, password, source or "credential-profile"


def chunk_years(start_year: int, end_year: int, chunk_year_count: int) -> List[Tuple[dt.datetime, dt.datetime]]:
    chunks = []
    year = start_year
    while year <= end_year:
        chunk_end_year = min(end_year, year + chunk_year_count - 1)
        start = dt.datetime(year, 1, 1, 0)
        end = dt.datetime(chunk_end_year, 12, 31, 23)
        chunks.append((start, end))
        year = chunk_end_year + 1
    return chunks


def expected_hours(start_year: int, end_year: int) -> pd.DatetimeIndex:
    return pd.date_range(
        f"{start_year}-01-01T00:00:00Z",
        f"{end_year}-12-31T23:00:00Z",
        freq="h",
    )


def cache_path(cache_dir: Path, variable: str, start: dt.datetime, end: dt.datetime) -> Path:
    return cache_dir / variable / f"{variable}_{start:%Y%m%d%H}_{end:%Y%m%d%H}.csv"


def download_chunk(
    session: requests.Session,
    auth: Tuple[str, str],
    variable: str,
    lat: float,
    lon: float,
    start: dt.datetime,
    end: dt.datetime,
    path: Path,
    overwrite: bool,
) -> None:
    if path.exists() and path.stat().st_size > 0 and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    params = {
        "data": DATAFIELD_IDS[variable],
        "location": f"[{lat},{lon}]",
        "time": f"{start:%Y-%m-%dT%H:%M:%S}/{end:%Y-%m-%dT%H:%M:%S}",
    }
    response = session.get(GIOVANNI_ENDPOINT, params=params, auth=auth, timeout=300)
    response.raise_for_status()
    text = response.text
    if text.lstrip().startswith("<") or "Earthdata Login" in text[:1000]:
        raise RuntimeError(f"Giovanni returned an authentication page for {variable} {start} {end}.")
    if "Timestamp (UTC),Data" not in text:
        raise RuntimeError(f"Unexpected Giovanni response for {variable} {start} {end}: {text[:500]}")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_giovanni_csv(path: Path, variable: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    metadata: Dict[str, str] = {}
    lines = text.splitlines()
    data_start = None
    for index, line in enumerate(lines):
        if line.strip() == "Timestamp (UTC),Data":
            data_start = index
            break
        if "," in line:
            key, value = line.split(",", 1)
            metadata[key.strip()] = value.strip()
    if data_start is None:
        raise ValueError(f"No Giovanni data table found in {path}")
    table_text = "\n".join(lines[data_start:])
    df = pd.read_csv(io.StringIO(table_text))
    df = df.rename(columns={"Timestamp (UTC)": "timestamp_utc", "Data": variable})
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df[variable] = pd.to_numeric(df[variable], errors="coerce")
    df.loc[df[variable] <= -9990, variable] = np.nan
    return df, metadata


def download_variable(
    session: requests.Session,
    auth: Tuple[str, str],
    variable: str,
    chunks: List[Tuple[dt.datetime, dt.datetime]],
    lat: float,
    lon: float,
    cache_dir: Path,
    overwrite: bool,
) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    frames = []
    metadata_records = []
    for start, end in chunks:
        path = cache_path(cache_dir, variable, start, end)
        print(f"{variable}: {start:%Y-%m-%dT%H} to {end:%Y-%m-%dT%H}")
        download_chunk(session, auth, variable, lat, lon, start, end, path, overwrite)
        frame, metadata = parse_giovanni_csv(path, variable)
        metadata["cache_path"] = str(path)
        metadata_records.append(metadata)
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True).drop_duplicates("timestamp_utc")
    combined = combined.sort_values("timestamp_utc").reset_index(drop=True)
    return combined, metadata_records


def build_native_table(variable_frames: Dict[str, pd.DataFrame], expected_index: pd.DatetimeIndex) -> pd.DataFrame:
    native = pd.DataFrame({"timestamp_utc": expected_index})
    for variable in NLDAS_VARIABLES:
        native = native.merge(variable_frames[variable], on="timestamp_utc", how="left")
    return native


def derive_ecosim(native: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["timestamp_utc"] = native["timestamp_utc"]
    out["year"] = native["timestamp_utc"].dt.year.astype(int)
    out["day"] = native["timestamp_utc"].dt.dayofyear.astype(int)
    out["hour"] = native["timestamp_utc"].dt.hour.astype(int)
    out["TMPH"] = native["Tair"] - 273.15
    out["WINDH"] = np.sqrt(native["Wind_E"] ** 2 + native["Wind_N"] ** 2)
    out["RAINH"] = native["Rainf"]
    qair = native["Qair"]
    psurf = native["PSurf"]
    out["DWPTH"] = (qair * psurf / (0.622 + 0.378 * qair)) / 1000.0
    out["SRADH"] = native["SWdown"]
    out["PATM"] = native["PSurf"] / 1000.0
    return out


def series_range(series: pd.Series) -> Dict[str, Optional[float]]:
    clean = series.dropna()
    return {
        "min": float(clean.min()) if len(clean) else None,
        "max": float(clean.max()) if len(clean) else None,
        "mean": float(clean.mean()) if len(clean) else None,
    }


def check_numeric_bounds(
    frame: pd.DataFrame,
    variable: str,
    limits: Dict[str, object],
    timestamp_column: Optional[str] = "timestamp_utc",
    sample_count: int = 5,
) -> Dict[str, object]:
    series = pd.to_numeric(frame[variable], errors="coerce")
    invalid = pd.Series(False, index=frame.index)
    minimum = limits.get("min")
    maximum = limits.get("max")
    allowed = limits.get("allowed")
    if allowed is not None:
        allowed_values = set(allowed)
        invalid = ~series.isin(allowed_values)
    else:
        invalid = series.isna()
        if minimum is not None:
            invalid = invalid | (series < float(minimum))
        if maximum is not None:
            invalid = invalid | (series > float(maximum))

    samples = []
    if bool(invalid.any()):
        invalid_rows = frame.loc[invalid, [variable]].head(sample_count)
        for index, row in invalid_rows.iterrows():
            sample = {"value": None if pd.isna(row[variable]) else float(row[variable])}
            if timestamp_column and timestamp_column in frame.columns:
                sample["timestamp_utc"] = str(frame.loc[index, timestamp_column])
            elif "year" in frame.columns:
                sample["year"] = int(frame.loc[index, "year"])
            samples.append(sample)

    return {
        "min_allowed": minimum,
        "max_allowed": maximum,
        "allowed_values": allowed,
        "units": limits.get("units"),
        "reason": limits.get("reason"),
        "observed": series_range(series),
        "invalid_count": int(invalid.sum()),
        "sample_invalid": samples,
        "passed": int(invalid.sum()) == 0,
    }


def annual_forcing_table(
    years: np.ndarray,
    lon: float,
    annual_info: Dict[str, object],
    template_climate_file: Optional[Path],
) -> pd.DataFrame:
    table = pd.DataFrame({"year": years.astype(int)})
    table["Z0G"] = 1.0
    table["IFLGW"] = 0
    table["ZNOONG"] = [calculate_solar_noon_utc(int(year), 6, 1, lon) for year in years]

    template_values, _ = read_template_annual_variables(template_climate_file, years)
    for variable in CHEMISTRY_VARIABLES:
        if variable in template_values:
            table[variable] = template_values[variable]["values"]
        else:
            table[variable] = 0.0
    annual_info["annual_validity_variables"] = ["Z0G", "IFLGW", "ZNOONG", *CHEMISTRY_VARIABLES]
    return table


def validity_report(
    native: pd.DataFrame,
    ecosim: pd.DataFrame,
    annual: pd.DataFrame,
) -> Dict[str, object]:
    native_checks = {
        variable: check_numeric_bounds(native, variable, limits)
        for variable, limits in NATIVE_VALIDITY_LIMITS.items()
    }
    ecosim_checks = {
        variable: check_numeric_bounds(ecosim, variable, limits)
        for variable, limits in ECOSIM_VALIDITY_LIMITS.items()
    }
    annual_checks = {
        variable: check_numeric_bounds(annual, variable, limits, timestamp_column=None)
        for variable, limits in ANNUAL_VALIDITY_LIMITS.items()
        if variable in annual.columns
    }
    all_checks = [*native_checks.values(), *ecosim_checks.values(), *annual_checks.values()]
    failed = [
        {"group": group, "variable": variable, "invalid_count": check["invalid_count"]}
        for group, checks in (
            ("native", native_checks),
            ("ecosim", ecosim_checks),
            ("annual", annual_checks),
        )
        for variable, check in checks.items()
        if not check["passed"]
    ]
    return {
        "all_passed": all(check["passed"] for check in all_checks),
        "failed": failed,
        "native": native_checks,
        "ecosim": ecosim_checks,
        "annual": annual_checks,
    }


def calculate_solar_noon_utc(year: int, month: int, day: int, longitude: float) -> float:
    current = dt.datetime(year, month, day)
    doy = current.timetuple().tm_yday
    b_rad = np.deg2rad((360.0 / 365.24) * (doy - 81))
    equation_of_time = 9.87 * np.sin(2 * b_rad) - 7.53 * np.cos(b_rad) - 1.5 * np.sin(b_rad)
    return float((720.0 - (4.0 * longitude) - equation_of_time) / 60.0)


def nearest_year_value(source_years: np.ndarray, source_values: np.ndarray, target_year: int) -> float:
    index = int(np.argmin(np.abs(source_years.astype(int) - int(target_year))))
    return float(source_values[index])


def resolve_template_climate_file(args: argparse.Namespace, output: Path) -> Optional[Path]:
    if args.template_climate_file:
        path = Path(args.template_climate_file)
    else:
        site_dir = Path(args.site_output_dir)
        # Prefer the year-named AmeriFlux climate file (<SITE_ID>_ecosim_climate_<first>-<last>.nc).
        named = sorted(site_dir.glob(f"{args.site_id}_ecosim_climate_[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9].nc"))
        path = named[-1] if named else site_dir / f"{args.site_id}_ecosim_climate.nc"
    if path.exists() and path.resolve() != output.resolve():
        return path
    return None


def read_template_annual_variables(
    template_file: Optional[Path],
    target_years: np.ndarray,
) -> Tuple[Dict[str, Dict[str, object]], Optional[str]]:
    if template_file is None:
        return {}, None

    import netCDF4 as nc

    annual: Dict[str, Dict[str, object]] = {}
    with nc.Dataset(template_file) as ds:
        if "year" not in ds.variables:
            return annual, str(template_file)
        source_years = np.asarray(ds.variables["year"][:], dtype=int)
        if source_years.size == 0:
            return annual, str(template_file)
        for variable in CHEMISTRY_VARIABLES:
            if variable not in ds.variables:
                continue
            src = ds.variables[variable]
            values = np.asarray(src[:], dtype=float)
            if values.ndim == 2:
                values = values[:, 0]
            elif values.ndim != 1:
                continue
            target = np.array(
                [nearest_year_value(source_years, values, int(year)) for year in target_years],
                dtype=np.float32,
            )
            default_long_name, default_units = CHEMISTRY_METADATA[variable]
            annual[variable] = {
                "values": target,
                "long_name": getattr(src, "long_name", default_long_name),
                "units": getattr(src, "units", default_units),
            }
    return annual, str(template_file)


def write_ecosim_netcdf(
    ecosim: pd.DataFrame,
    output: Path,
    lon: float,
    lat: float,
    start_year: int,
    end_year: int,
    template_climate_file: Optional[Path] = None,
) -> Dict[str, object]:
    import netCDF4 as nc

    output.parent.mkdir(parents=True, exist_ok=True)
    years = np.arange(start_year, end_year + 1, dtype=np.int32)
    annual_template, annual_template_source = read_template_annual_variables(template_climate_file, years)
    ds = nc.Dataset(output, "w", format="NETCDF4")
    try:
        ds.createDimension("year", len(years))
        ds.createDimension("day", 366)
        ds.createDimension("hour", 24)
        ds.createDimension("ngrid", 1)

        year_var = ds.createVariable("year", "i4", ("year",))
        day_var = ds.createVariable("day", "i4", ("day",))
        hour_var = ds.createVariable("hour", "i4", ("hour",))
        lat_var = ds.createVariable("ALATG", "f4", ("ngrid",))
        lon_var = ds.createVariable("ALONG", "f4", ("ngrid",))
        year_var.units = "year"
        day_var.units = "day of year"
        hour_var.units = "hour of day UTC"
        lat_var.units = "degrees_north"
        lon_var.units = "degrees_east"
        lat_var.long_name = "selected NLDAS grid-cell latitude"
        lon_var.long_name = "selected NLDAS grid-cell longitude"
        year_var[:] = years
        day_var[:] = np.arange(1, 367, dtype=np.int32)
        hour_var[:] = np.arange(24, dtype=np.int32)
        lat_var[:] = lat
        lon_var[:] = lon

        ds.title = "US-UMB NLDAS_FORA0125_H point climate forcing for EcoSIM"
        ds.source = "NASA GES DISC NLDAS_FORA0125_H.2.0 via Giovanni time-series API"
        ds.history = f"Created {dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
        ds.Conventions = "CF-1.8"
        if annual_template_source:
            ds.annual_forcing_source = annual_template_source

        year_to_index = {year: idx for idx, year in enumerate(years)}
        for variable in ["TMPH", "WINDH", "RAINH", "DWPTH", "SRADH", "PATM"]:
            arr = np.full((len(years), 366, 24, 1), FILL_VALUE, dtype=np.float32)
            valid = ecosim[["year", "day", "hour", variable]].dropna()
            for row in valid.itertuples(index=False):
                arr[year_to_index[int(row.year)], int(row.day) - 1, int(row.hour), 0] = float(getattr(row, variable))
            ncvar = ds.createVariable(variable, "f4", ("year", "day", "hour", "ngrid"), fill_value=FILL_VALUE)
            ncvar.units = UNITS[variable]
            ncvar.long_name = ECOSIM_LONG_NAMES[variable]
            ncvar[:] = arr

        z0g_var = ds.createVariable("Z0G", "f4", ("year", "ngrid"), fill_value=FILL_VALUE)
        z0g_var.long_name = "windspeed measurement height"
        z0g_var.units = "m"
        z0g_var[:, 0] = 1.0

        iflgw_var = ds.createVariable("IFLGW", "i4", ("year", "ngrid"))
        iflgw_var.long_name = "flag for raising Z0G with vegeation"
        iflgw_var[:, 0] = 0

        znoong_var = ds.createVariable("ZNOONG", "f4", ("year", "ngrid"), fill_value=FILL_VALUE)
        znoong_var.long_name = "time of solar noon"
        znoong_var.units = "hour"
        znoong_var[:, 0] = np.array(
            [calculate_solar_noon_utc(int(year), 6, 1, lon) for year in years],
            dtype=np.float32,
        )

        for variable in CHEMISTRY_VARIABLES:
            long_name, units = CHEMISTRY_METADATA[variable]
            ncvar = ds.createVariable(variable, "f4", ("year", "ngrid"), fill_value=FILL_VALUE)
            ncvar.long_name = annual_template.get(variable, {}).get("long_name", long_name)
            ncvar.units = annual_template.get(variable, {}).get("units", units)
            if variable in annual_template:
                ncvar[:, 0] = annual_template[variable]["values"]
            else:
                ncvar[:, 0] = 0.0
    finally:
        ds.close()
    return {
        "annual_template_source": annual_template_source,
        "annual_template_variables": sorted(annual_template),
        "annual_default_variables": ["Z0G", "IFLGW", "ZNOONG"],
        "annual_missing_template_variables_filled_zero": [
            variable for variable in CHEMISTRY_VARIABLES if variable not in annual_template
        ],
    }


def is_us_auxiliary_climate_domain(lon: float, lat: float) -> bool:
    return -125.0 <= lon <= -67.0 and 25.0 <= lat <= 53.0


def add_us_chemistry_if_requested(
    netcdf_path: Path,
    site_id: str,
    lon: float,
    lat: float,
    years: np.ndarray,
    args: argparse.Namespace,
) -> Dict[str, object]:
    in_us_auxiliary_domain = is_us_auxiliary_climate_domain(lon, lat)
    should_add = args.add_us_chemistry == "always" or (
        args.add_us_chemistry == "auto" and in_us_auxiliary_domain
    )
    report: Dict[str, object] = {
        "requested_mode": args.add_us_chemistry,
        "us_auxiliary_domain": in_us_auxiliary_domain,
        "added_to_netcdf": False,
        "source": "NADP precipitation chemistry rasters",
        "chemistry_input": str(Path(args.chemistry_input).resolve()),
        "chemistry_output": None,
        "notes": [],
    }
    if not should_add:
        if args.add_us_chemistry == "never":
            report["notes"].append("Skipped because --add-us-chemistry=never was requested.")
        else:
            report["notes"].append("Skipped because coordinate is outside the US/NLDAS auxiliary climate domain.")
        return report

    from Tools.create_ecosim_climate_forcing import add_chemistry_to_netcdf, extract_chemistry

    chemistry_output = args.chemistry_output
    if not chemistry_output:
        chemistry_output = str(Path(args.site_output_dir) / f"{site_id}_nadp_chemistry.json")
    report["chemistry_output"] = str(Path(chemistry_output).resolve())

    chemistry_data = None
    if Path(args.chemistry_input).exists():
        Path(chemistry_output).parent.mkdir(parents=True, exist_ok=True)
        chemistry_data = extract_chemistry(
            lat=lat,
            lon=lon,
            years=[int(year) for year in years],
            output_file=chemistry_output,
            chem_dir=args.chemistry_input,
        )
        if chemistry_data is None:
            report["notes"].append(
                "NADP extraction returned no usable chemistry; template defaults/gap policy were applied."
            )
    else:
        report["notes"].append("NADP chemistry directory not found; template defaults/gap policy were applied.")

    chemistry_report = add_chemistry_to_netcdf(str(netcdf_path), chemistry_data, years)
    report["added_to_netcdf"] = True
    report["chemistry_report"] = chemistry_report
    return report


def quality_report(
    native: pd.DataFrame,
    ecosim: pd.DataFrame,
    annual: pd.DataFrame,
    metadata: Dict[str, List[Dict[str, str]]],
) -> Dict[str, object]:
    report: Dict[str, object] = {
        "collection": COLLECTION,
        "collection_concept_id": COLLECTION_CONCEPT_ID,
        "row_count": int(len(native)),
        "native_missing_counts": {var: int(native[var].isna().sum()) for var in NLDAS_VARIABLES},
        "ecosim_missing_counts": {
            var: int(ecosim[var].isna().sum()) for var in ["TMPH", "WINDH", "RAINH", "DWPTH", "SRADH", "PATM"]
        },
        "metadata_by_variable": metadata,
        "units": UNITS,
        "ranges": {},
        "validity_checks": validity_report(native, ecosim, annual),
    }
    for variable in ["TMPH", "WINDH", "RAINH", "DWPTH", "SRADH", "PATM"]:
        report["ranges"][variable] = series_range(ecosim[variable])
    return report


def main() -> int:
    args = parse_args()
    username, password, credential_source = resolve_credentials(args)
    auth = (username, password)
    session = requests.Session()
    session.headers.update({"User-Agent": "EcoSIM-NLDAS-Giovanni-downloader/1.0"})

    output_dir = Path(args.output_dir)
    site_output_dir = Path(args.site_output_dir)
    cache_dir = output_dir / "cache_giovanni" / args.site_id
    output_dir.mkdir(parents=True, exist_ok=True)
    site_output_dir.mkdir(parents=True, exist_ok=True)

    chunks = chunk_years(args.start_year, args.end_year, args.chunk_years)
    expected_index = expected_hours(args.start_year, args.end_year)

    variable_frames: Dict[str, pd.DataFrame] = {}
    metadata: Dict[str, List[Dict[str, str]]] = {}
    for variable in NLDAS_VARIABLES:
        frame, records = download_variable(
            session=session,
            auth=auth,
            variable=variable,
            chunks=chunks,
            lat=args.lat,
            lon=args.lon,
            cache_dir=cache_dir,
            overwrite=args.overwrite,
        )
        variable_frames[variable] = frame
        metadata[variable] = records

    native = build_native_table(variable_frames, expected_index)
    if len(native) != len(expected_index):
        raise RuntimeError("Native table length does not match expected hourly index.")
    ecosim = derive_ecosim(native)

    grid_lat = float(metadata["Tair"][0].get("lat", args.lat))
    grid_lon = float(metadata["Tair"][0].get("lon", args.lon))
    native.insert(1, "requested_lon", args.lon)
    native.insert(2, "requested_lat", args.lat)
    native.insert(3, "grid_lon", grid_lon)
    native.insert(4, "grid_lat", grid_lat)

    stem = f"{args.site_id}_nldas_fora_primary_{args.start_year}_{args.end_year}"
    native_csv = output_dir / f"{stem}_native.csv"
    ecosim_csv = output_dir / f"{stem}_ecosim_hourly.csv"
    manifest_json = output_dir / f"{stem}_download_manifest.json"
    report_json = output_dir / f"{stem}_quality_report.json"
    netcdf_path = site_output_dir / f"{args.site_id}_nldas_ecosim_climate.nc"
    template_climate_file = resolve_template_climate_file(args, netcdf_path)
    years = np.arange(args.start_year, args.end_year + 1, dtype=np.int32)

    native.to_csv(native_csv, index=False)
    ecosim.to_csv(ecosim_csv, index=False)
    annual_info = write_ecosim_netcdf(
        ecosim,
        netcdf_path,
        grid_lon,
        grid_lat,
        args.start_year,
        args.end_year,
        template_climate_file=template_climate_file,
    )
    chemistry_info = add_us_chemistry_if_requested(
        netcdf_path=netcdf_path,
        site_id=args.site_id,
        lon=grid_lon,
        lat=grid_lat,
        years=years,
        args=args,
    )
    annual_info["auxiliary_climate_variables"] = chemistry_info
    # Name the file by the forcing years it contains (e.g. *_nldas_ecosim_climate_2001-2020.nc).
    from Tools.climate_naming import finalize_climate_file_name

    netcdf_path = finalize_climate_file_name(netcdf_path)
    annual = annual_forcing_table(years, grid_lon, annual_info, netcdf_path)

    report = quality_report(native, ecosim, annual, metadata)
    report.update(
        {
            "site_id": args.site_id,
            "requested_lon": args.lon,
            "requested_lat": args.lat,
            "selected_grid_lon": grid_lon,
            "selected_grid_lat": grid_lat,
            "start_utc": f"{args.start_year}-01-01T00:00:00Z",
            "end_utc": f"{args.end_year}-12-31T23:00:00Z",
            "credential_source": credential_source,
            "allow_invalid": bool(args.allow_invalid),
            "annual_forcing": annual_info,
            "outputs": {
                "native_csv": str(native_csv),
                "ecosim_csv": str(ecosim_csv),
                "ecosim_netcdf": str(netcdf_path),
                "quality_report": str(report_json),
                "manifest": str(manifest_json),
            },
        }
    )
    report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = {
        "site_id": args.site_id,
        "collection": COLLECTION,
        "collection_concept_id": COLLECTION_CONCEPT_ID,
        "giovanni_endpoint": GIOVANNI_ENDPOINT,
        "datafield_ids": DATAFIELD_IDS,
        "chunks": [
            {"start_utc": f"{start:%Y-%m-%dT%H:00:00Z}", "end_utc": f"{end:%Y-%m-%dT%H:00:00Z}"}
            for start, end in chunks
        ],
        "row_count": int(len(native)),
        "credential_source": credential_source,
        "requested_lon": args.lon,
        "requested_lat": args.lat,
        "selected_grid_lon": grid_lon,
        "selected_grid_lat": grid_lat,
        "allow_invalid": bool(args.allow_invalid),
        "annual_forcing": annual_info,
        "validity_checks_passed": report["validity_checks"]["all_passed"],
        "outputs": report["outputs"],
        "created_utc": f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
    }
    manifest_json.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if not report["validity_checks"]["all_passed"] and not args.allow_invalid:
        failed = ", ".join(
            f"{item['group']}:{item['variable']}({item['invalid_count']})"
            for item in report["validity_checks"]["failed"]
        )
        raise RuntimeError(
            f"NLDAS validity checks failed: {failed}. "
            f"Inspect {report_json} or rerun with --allow-invalid to allow the command to succeed."
        )

    print(f"Wrote native CSV: {native_csv}")
    print(f"Wrote EcoSIM hourly CSV: {ecosim_csv}")
    print(f"Wrote EcoSIM NetCDF: {netcdf_path}")
    print(f"Wrote quality report: {report_json}")
    print(f"Wrote manifest: {manifest_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
