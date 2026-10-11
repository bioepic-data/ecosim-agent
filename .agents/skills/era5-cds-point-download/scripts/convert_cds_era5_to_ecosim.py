#!/usr/bin/env python3
"""Convert CDS ERA5 point time-series NetCDF/ZIP output to EcoSIM climate forcing."""

import argparse
import json
import math
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from netCDF4 import Dataset


FILL_VALUE = 1.0e30
REQUIRED_VARS = ("t2m", "u10", "v10", "d2m", "sp", "ssrd", "tp")
ECOSIM_VARS = ("TMPH", "WINDH", "RAINH", "DWPTH", "SRADH", "PATM")

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert CDS ERA5 point time-series NetCDF or ZIP output to EcoSIM NetCDF."
    )
    parser.add_argument("--input", required=True, help="CDS ERA5 NetCDF file or ZIP containing one NetCDF.")
    parser.add_argument("--output", required=True, help="Output EcoSIM climate forcing NetCDF.")
    parser.add_argument("--site-id", required=True, help="Site ID for metadata and forcing YAML.")
    parser.add_argument("--lon", type=float, required=True, help="Site longitude, east positive.")
    parser.add_argument("--lat", type=float, required=True, help="Site latitude.")
    parser.add_argument("--quality-report", help="Optional JSON quality report path.")
    parser.add_argument("--forcing-yaml", help="Optional YAML path to record clm_hour_file_in.")
    parser.add_argument(
        "--include-incomplete-years",
        action="store_true",
        help="Keep partial first or last years. By default, only complete UTC calendar years are written.",
    )
    parser.add_argument(
        "--add-us-chemistry",
        choices=["auto", "always", "never"],
        default="auto",
        help=(
            "Add annual EcoSIM precipitation chemistry variables from NADP. "
            "Auto adds them only for US/NADP-domain coordinates."
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
    return parser.parse_args()


def find_netcdf_input(input_path):
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(input_path)

    if zipfile.is_zipfile(path):
        tmpdir = tempfile.TemporaryDirectory(prefix="era5_cds_zip_")
        with zipfile.ZipFile(path) as archive:
            nc_names = [
                name for name in archive.namelist()
                if name.lower().endswith((".nc", ".nc4", ".netcdf"))
            ]
            if len(nc_names) != 1:
                raise ValueError(f"expected exactly one NetCDF in ZIP, found {len(nc_names)}")
            archive.extract(nc_names[0], tmpdir.name)
            return Path(tmpdir.name) / nc_names[0], tmpdir

    return path, None


def calculate_solar_noon_utc(year, longitude):
    d = datetime(int(year), 6, 1)
    doy = d.timetuple().tm_yday
    b_rad = math.radians((360.0 / 365.24) * (doy - 81))
    eot = 9.87 * math.sin(2 * b_rad) - 7.53 * math.cos(b_rad) - 1.5 * math.sin(b_rad)
    return (720.0 - (4.0 * longitude) - eot) / 60.0


def dewpoint_k_to_vapor_pressure_kpa(dewpoint_k):
    dewpoint_c = dewpoint_k - 273.15
    return 0.6112 * np.exp((17.67 * dewpoint_c) / (dewpoint_c + 243.5))


def load_era5_dataframe(input_path):
    nc_path, tmpdir = find_netcdf_input(input_path)
    try:
        ds = xr.open_dataset(nc_path)
        missing = [name for name in REQUIRED_VARS if name not in ds]
        if missing:
            raise ValueError(f"missing required ERA5 variables: {', '.join(missing)}")

        time_name = "valid_time" if "valid_time" in ds.coords else "time"
        if time_name not in ds.coords:
            raise ValueError("expected a valid_time or time coordinate")

        data = {
            "timestamp": pd.to_datetime(ds[time_name].values),
            "TMPH": np.asarray(ds["t2m"].values, dtype=np.float64) - 273.15,
            "WINDH": np.hypot(
                np.asarray(ds["u10"].values, dtype=np.float64),
                np.asarray(ds["v10"].values, dtype=np.float64),
            ),
            "RAINH": np.asarray(ds["tp"].values, dtype=np.float64) * 1000.0,
            "DWPTH": dewpoint_k_to_vapor_pressure_kpa(np.asarray(ds["d2m"].values, dtype=np.float64)),
            "SRADH": np.asarray(ds["ssrd"].values, dtype=np.float64) / 3600.0,
            "PATM": np.asarray(ds["sp"].values, dtype=np.float64) / 1000.0,
        }
        df = pd.DataFrame(data).sort_values("timestamp")
    finally:
        if "ds" in locals():
            ds.close()
        if tmpdir is not None:
            tmpdir.cleanup()

    if df.empty:
        raise ValueError("ERA5 input has no time records")

    df["year"] = df["timestamp"].dt.year
    df["day"] = df["timestamp"].dt.dayofyear
    df["hour"] = df["timestamp"].dt.hour
    repair_report = repair_accumulated_fields(df)
    df.attrs["repair_report"] = repair_report
    return df


def fill_by_hour_nearest_edge(df, variable):
    repaired = df[variable].copy()
    for hour in range(24):
        mask = df["hour"] == hour
        repaired.loc[mask] = repaired.loc[mask].interpolate(
            method="linear",
            limit_direction="both",
        )
    return repaired


def repair_accumulated_fields(df):
    report = {}
    for variable in ("RAINH", "SRADH"):
        values = df[variable]
        missing_before = int((~np.isfinite(values)).sum())
        negative_before = int((np.isfinite(values) & (values < 0.0)).sum())

        if missing_before:
            df[variable] = fill_by_hour_nearest_edge(df, variable)
        if variable in ("RAINH", "SRADH"):
            df[variable] = df[variable].clip(lower=0.0)

        values_after = df[variable]
        report[variable] = {
            "missing_filled_by_same_hour_interpolation": missing_before,
            "negative_values_clamped_to_zero": negative_before,
            "missing_after": int((~np.isfinite(values_after)).sum()),
            "negative_after": int((np.isfinite(values_after) & (values_after < 0.0)).sum()),
        }
    return report


def expected_hours_for_year(year):
    return 8784 if pd.Timestamp(year=int(year), month=12, day=31).is_leap_year else 8760


def complete_calendar_years(df):
    status = {}
    for year, group in df.groupby("year"):
        year = int(year)
        timestamps = pd.to_datetime(group["timestamp"])
        expected_start = pd.Timestamp(year=year, month=1, day=1, hour=0)
        expected_end = pd.Timestamp(year=year, month=12, day=31, hour=23)
        unique_count = int(timestamps.nunique())
        status[str(year)] = {
            "complete": bool(
                unique_count == expected_hours_for_year(year)
                and timestamps.min() == expected_start
                and timestamps.max() == expected_end
            ),
            "record_count": int(len(group)),
            "unique_timestamp_count": unique_count,
            "expected_hour_count": expected_hours_for_year(year),
            "time_start": timestamps.min().isoformat(),
            "time_end": timestamps.max().isoformat(),
        }
    return status


def drop_incomplete_years(df):
    status = complete_calendar_years(df)
    keep_years = {int(year) for year, item in status.items() if item["complete"]}
    dropped_years = [int(year) for year, item in status.items() if not item["complete"]]
    if not keep_years:
        raise ValueError("no complete calendar years are available in the ERA5 input")
    filtered = df[df["year"].isin(keep_years)].copy()
    filtered.attrs.update(df.attrs)
    return filtered, status, dropped_years


def quality_checks(df):
    checks = {
        "TMPH": {"min": -90.0, "max": 60.0, "units": "degC"},
        "WINDH": {"min": 0.0, "max": 75.0, "units": "m s^-1"},
        "RAINH": {"min": 0.0, "max": 500.0, "units": "mm h^-1"},
        "DWPTH": {"min": 0.0, "max": 15.0, "units": "kPa"},
        "SRADH": {"min": 0.0, "max": 1400.0, "units": "W m^-2"},
        "PATM": {"min": 50.0, "max": 110.0, "units": "kPa"},
    }
    result = {}
    all_passed = True
    for var, spec in checks.items():
        values = pd.to_numeric(df[var], errors="coerce")
        finite = np.isfinite(values)
        out_of_range = finite & ((values < spec["min"]) | (values > spec["max"]))
        missing = ~finite
        passed = int(out_of_range.sum()) == 0 and int(missing.sum()) == 0
        all_passed = all_passed and passed
        result[var] = {
            "units": spec["units"],
            "min": float(np.nanmin(values)) if finite.any() else None,
            "max": float(np.nanmax(values)) if finite.any() else None,
            "missing_count": int(missing.sum()),
            "out_of_range_count": int(out_of_range.sum()),
            "passed": passed,
        }
    return {"all_passed": all_passed, "variables": result}


def create_output_arrays(df):
    years = np.array(sorted(df["year"].unique()), dtype=np.int32)
    data = {
        name: np.full((len(years), 366, 24, 1), FILL_VALUE, dtype=np.float32)
        for name in ECOSIM_VARS
    }
    year_index = {year: i for i, year in enumerate(years)}

    for row in df.itertuples(index=False):
        y = year_index[int(row.year)]
        d = int(row.day) - 1
        h = int(row.hour)
        for name in ECOSIM_VARS:
            value = getattr(row, name)
            if np.isfinite(value):
                data[name][y, d, h, 0] = np.float32(value)
    return years, data


def write_ecosim_netcdf(df, output_path, site_id, lon, lat):
    years, data = create_output_arrays(df)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with Dataset(output_path, "w", format="NETCDF4") as nc:
        nc.createDimension("year", len(years))
        nc.createDimension("day", 366)
        nc.createDimension("hour", 24)
        nc.createDimension("ngrid", 1)

        nc.title = f"EcoSIM hourly climate forcing for {site_id}"
        nc.source = "Copernicus CDS ERA5 single-level point time-series"
        nc.history = f"Created {datetime.now(timezone.utc).isoformat()} by convert_cds_era5_to_ecosim.py"
        nc.Conventions = "CF-1.8"
        nc.site_id = site_id
        nc.latitude = float(lat)
        nc.longitude = float(lon)

        year_var = nc.createVariable("year", "i4", ("year",))
        year_var.long_name = "year AD"
        year_var[:] = years

        specs = {
            "TMPH": ("hourly air temperature", "degC", "air_temperature"),
            "WINDH": ("horizontal wind speed", "m s^-1", "wind_speed"),
            "RAINH": ("hourly precipitation", "mm h^-1", "precipitation_amount"),
            "DWPTH": ("hourly atmospheric vapor pressure", "kPa", "water_vapor_pressure"),
            "SRADH": ("incident shortwave solar radiation", "W m^-2", "surface_downwelling_shortwave_flux_in_air"),
            "PATM": ("surface atmospheric pressure", "kPa", "surface_air_pressure"),
        }
        for name, (long_name, units, standard_name) in specs.items():
            var = nc.createVariable(name, "f4", ("year", "day", "hour", "ngrid"), fill_value=FILL_VALUE)
            var.long_name = long_name
            var.units = units
            var.standard_name = standard_name
            var[:] = data[name]

        z0g = nc.createVariable("Z0G", "f4", ("year", "ngrid"), fill_value=FILL_VALUE)
        z0g.long_name = "windspeed measurement height"
        z0g.units = "m"
        z0g[:, 0] = 10.0

        iflgw = nc.createVariable("IFLGW", "i4", ("year", "ngrid"))
        iflgw.long_name = "flag for raising Z0G with vegetation"
        iflgw[:, 0] = 0

        znoong = nc.createVariable("ZNOONG", "f4", ("year", "ngrid"), fill_value=FILL_VALUE)
        znoong.long_name = "time of solar noon"
        znoong.units = "hour"
        znoong[:, 0] = [calculate_solar_noon_utc(year, lon) for year in years]

    return years


def write_forcing_yaml(path, output_path):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml

        existing = {}
        if path.exists():
            existing = yaml.safe_load(path.read_text()) or {}
        existing["clm_hour_file_in"] = str(Path(output_path).resolve())
        path.write_text(yaml.safe_dump(existing, sort_keys=False))
    except ModuleNotFoundError:
        path.write_text(f"clm_hour_file_in: {Path(output_path).resolve()}\n")


def is_us_auxiliary_climate_domain(lon, lat):
    return -125.0 <= lon <= -67.0 and 25.0 <= lat <= 53.0


def add_us_chemistry_if_requested(output_path, site_id, lon, lat, years, args):
    should_add = args.add_us_chemistry == "always" or (
        args.add_us_chemistry == "auto" and is_us_auxiliary_climate_domain(lon, lat)
    )
    report = {
        "requested_mode": args.add_us_chemistry,
        "us_auxiliary_domain": is_us_auxiliary_climate_domain(lon, lat),
        "added_to_netcdf": False,
        "source": "NADP precipitation chemistry rasters",
        "chemistry_input": str(Path(args.chemistry_input).resolve()),
        "chemistry_output": None,
        "notes": [],
    }
    if not should_add:
        report["notes"].append("Skipped because coordinate is outside the US/NADP auxiliary climate domain.")
        return report

    from Tools.create_ecosim_climate_forcing import add_chemistry_to_netcdf, extract_chemistry

    chemistry_output = args.chemistry_output
    if not chemistry_output:
        chemistry_output = str(Path("result") / site_id / f"{site_id}_nadp_chemistry.json")
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

    chemistry_report = add_chemistry_to_netcdf(str(output_path), chemistry_data, years)
    report["added_to_netcdf"] = True
    report["chemistry_report"] = chemistry_report
    return report


def main():
    args = parse_args()
    df = load_era5_dataframe(args.input)
    source_record_count = int(len(df))
    source_time_start = df["timestamp"].min().isoformat()
    source_time_end = df["timestamp"].max().isoformat()
    source_completeness = complete_calendar_years(df)
    dropped_incomplete_years = []
    if not args.include_incomplete_years:
        df, source_completeness, dropped_incomplete_years = drop_incomplete_years(df)

    checks = quality_checks(df)
    years = write_ecosim_netcdf(df, args.output, args.site_id, args.lon, args.lat)
    chemistry_report = add_us_chemistry_if_requested(
        args.output,
        args.site_id,
        args.lon,
        args.lat,
        years,
        args,
    )
    # Name the file by the forcing years it contains (e.g. *_1940-2024.nc).
    from Tools.climate_naming import finalize_climate_file_name

    args.output = str(finalize_climate_file_name(args.output))
    write_forcing_yaml(args.forcing_yaml, args.output)

    report = {
        "site_id": args.site_id,
        "input": str(Path(args.input).resolve()),
        "output": str(Path(args.output).resolve()),
        "lon": args.lon,
        "lat": args.lat,
        "include_incomplete_years": bool(args.include_incomplete_years),
        "source_time_start": source_time_start,
        "source_time_end": source_time_end,
        "source_record_count": source_record_count,
        "time_start": df["timestamp"].min().isoformat(),
        "time_end": df["timestamp"].max().isoformat(),
        "record_count": int(len(df)),
        "years": [int(year) for year in years],
        "dropped_incomplete_years": dropped_incomplete_years,
        "source_complete_calendar_years": source_completeness,
        "complete_calendar_years": complete_calendar_years(df),
        "repairs": df.attrs.get("repair_report", {}),
        "auxiliary_climate_variables": chemistry_report,
        "validity_checks": checks,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if args.quality_report:
        report_path = Path(args.quality_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"Wrote EcoSIM climate forcing: {args.output}")
    if args.quality_report:
        print(f"Wrote quality report: {args.quality_report}")
    if not checks["all_passed"]:
        print("Warning: one or more validity checks failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
