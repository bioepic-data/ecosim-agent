#!/usr/bin/env python3
"""
Convert Ameriflux ERA5 half-hourly climate forcing data to ECOSIM hourly format.

This script reads half-hourly ERA5 climate data from Ameriflux format and
converts it to the ECOSIM hourly climate forcing format as described in
the Blodget.clim.2012-2022.template file.
"""

import pandas as pd
import numpy as np
from netCDF4 import Dataset
import glob
import os
import sys
import json
import subprocess
import argparse
import math
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Tools.climate_naming import finalize_climate_file_name
from Tools.climate_quality import sanitize_era5_dataframe


FILL_VALUE_THRESHOLD = 1.0e29
DEFAULT_COMPARISON_MIN_PAIRS = 720
# Warn when ZNOONG and the radiation-weighted diurnal centroid disagree by more
# than this many hours; EcoSIM caps hourly SRADH by the sun angle from ZNOONG.
SOLAR_NOON_MISMATCH_WARNING_HOURS = 0.75
# Warn when the forcing and tower SW_IN diurnal centroids differ by more than this.
CLOCK_LAG_WARNING_HOURS = 0.5
# ERA5-to-tower time registration: lags searched (hours), minimum measured
# pairs, and the correlation gain a non-zero lag needs over zero lag.
TIME_REGISTRATION_MAX_LAG_HOURS = 3.0
TIME_REGISTRATION_MIN_PAIRS = 2000
TIME_REGISTRATION_MIN_CORRELATION_GAIN = 0.01
TIME_REGISTRATION_MODES = ("auto", "check", "off")


def saturation_vapor_pressure_kpa(temp_c):
    """Saturation vapor pressure (kPa), matching EcoSIM MiniMathMod vapsat0."""
    temp_k = np.asarray(temp_c, dtype=float) + 273.15
    return 0.61 * np.exp(5360.0 * (3.661e-3 - 1.0 / temp_k))


def vapor_pressure_from_vpd_kpa(temp_c, vpd_hpa):
    """Actual vapor pressure (kPa) from air temperature (degC) and VPD (hPa).

    EcoSIM reads DWPTH as atmospheric vapor pressure (WthrMod: VPK=min(DWPTH,VPS)),
    so AmeriFlux VPD must be converted rather than written directly.
    """
    es = saturation_vapor_pressure_kpa(temp_c)
    return np.clip(es - np.asarray(vpd_hpa, dtype=float) * 0.1, 0.0, es)

# These are broad operational screens for detecting forcing failures, not
# universal performance targets for ERA5 at flux-tower scale.
IN_SITU_COMPARISON_SPECS = {
    "TMPH": {
        "candidates": (("TA_F", "TA_F_QC"), ("TA", None)),
        "aggregation": "mean",
        "scale": 1.0,
        "units": "degC",
        "valid_range": (-90.0, 60.0),
        "max_abs_mean_bias": 2.0,
        "max_rmse": 5.0,
    },
    "WINDH": {
        "candidates": (("WS_F", "WS_F_QC"), ("WS", None)),
        "aggregation": "mean",
        "scale": 1.0,
        "units": "m s-1",
        "valid_range": (0.0, 75.0),
        "max_abs_mean_bias": 1.5,
        "max_rmse": 3.0,
        "max_abs_relative_mean_bias": 0.50,
        "relative_bias_min_abs_bias": 0.5,
    },
    "RAINH": {
        "candidates": (("P_F", "P_F_QC"), ("P", None)),
        "aggregation": "sum",
        "scale": 1.0,
        "units": "mm h-1",
        "valid_range": (0.0, 500.0),
        "max_abs_relative_total_bias": 0.30,
        "relative_total_min_observed": 10.0,
    },
    "DWPTH": {
        # Observed VPD (hPa) is converted to vapor pressure with measured TA.
        "candidates": (("VPD_F", "VPD_F_QC"), ("VPD", None)),
        "temperature_candidates": (("TA_F", "TA_F_QC"), ("TA", None)),
        "aggregation": "mean",
        "scale": 1.0,
        "units": "kPa",
        "valid_range": (0.0, 100.0),
        "max_abs_mean_bias": 0.30,
        "max_rmse": 0.75,
        "max_abs_relative_mean_bias": 0.40,
        "relative_bias_min_abs_bias": 0.15,
    },
    "SRADH": {
        "candidates": (("SW_IN_F", "SW_IN_F_QC"), ("SW_IN", None)),
        "aggregation": "mean",
        "scale": 1.0,
        "units": "W m-2",
        "valid_range": (0.0, 1400.0),
        "max_abs_mean_bias": 40.0,
        "max_rmse": 150.0,
        "max_abs_relative_mean_bias": 0.30,
        "relative_bias_min_abs_bias": 20.0,
    },
    "PATM": {
        "candidates": (("PA_F", "PA_F_QC"), ("PA", None)),
        "aggregation": "mean",
        "scale": 1.0,
        "units": "kPa",
        "valid_range": (45.0, 110.0),
        "max_abs_mean_bias": 2.0,
        "max_rmse": 4.0,
    },
}

def get_site_metadata(site_id):
    """Get site metadata using ameriflux_site_info skill."""
    result_root = "result"
    result_dir = os.path.join(result_root, site_id)
    os.makedirs(result_dir, exist_ok=True)

    candidate_files = [
        os.path.join(result_dir, f"{site_id}_ecosim_site.json"),
        os.path.join(result_root, f"{site_id}_ecosim_site.json"),
    ]
    for site_file in candidate_files:
        if os.path.exists(site_file):
            with open(site_file, 'r') as f:
                return json.load(f)

    skill_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    script_candidates = [
        os.path.join(skill_root, "ameriflux-site-info", "extract_ameriflux_site_data.py"),
        os.path.join(skill_root, "ameriflux_site_info", "extract_ameriflux_site_data.py"),
    ]
    script_path = next((path for path in script_candidates if os.path.exists(path)), script_candidates[0])
    cmd = [sys.executable, script_path, site_id, result_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    if result.returncode != 0:
        print(f"Error running site info: {result.stderr}")
        return None

    for site_file in candidate_files:
        if os.path.exists(site_file):
            with open(site_file, 'r') as f:
                return json.load(f)
    return None

def get_site_longitude(site_id):
    """Get longitude for the site using ameriflux_site_info skill."""
    site_data = get_site_metadata(site_id)
    if not site_data:
        return None
    return site_data.get('ALONG')

def parse_timestamps(timestamp_str):
    """Parse timestamp string from Ameriflux data."""
    # Format: YYYYMMDDHHMM (e.g., 198101010000)
    # Convert to string first to ensure it's a string
    timestamp_str = str(timestamp_str)
    year = int(timestamp_str[:4])
    month = int(timestamp_str[4:6])
    day = int(timestamp_str[6:8])
    hour = int(timestamp_str[8:10])
    minute = int(timestamp_str[10:12])
    return datetime(year, month, day, hour, minute)

def infer_source_frequency(timestamps):
    """Infer the AmeriFlux source timestep for QC reindexing."""
    deltas = timestamps.sort_values().diff().dropna().dt.total_seconds()
    if deltas.empty:
        return "30min"
    median_seconds = float(deltas.median())
    if 3300 <= median_seconds <= 3900:
        return "1h"
    if 1500 <= median_seconds <= 2100:
        return "30min"
    inferred = pd.infer_freq(timestamps.sort_values())
    if inferred:
        return inferred
    return "30min"


def discover_in_situ_file(era5_file, site_id=None):
    """Find a matching AmeriFlux FULLSET HH/HR file beside an ERA5 CSV."""

    source_dir = os.path.dirname(os.path.abspath(era5_file))
    source_name = os.path.basename(era5_file)
    resolved_site_id = site_id
    if not resolved_site_id and "_FLUXNET_ERA5_" in source_name:
        prefix = source_name.split("_FLUXNET_ERA5_", 1)[0]
        if prefix.startswith("AMF_"):
            resolved_site_id = prefix[4:]

    cadence_order = []
    for cadence in ("HH", "HR"):
        if f"_ERA5_{cadence}_" in source_name:
            cadence_order.append(cadence)
    cadence_order.extend(cadence for cadence in ("HH", "HR") if cadence not in cadence_order)

    # FLUXNET v1.3 releases ship the gap-filled tower meteorology as FLUXMET
    # instead of FULLSET, with the same columns.
    patterns = []
    for cadence in cadence_order:
        for product in ("FULLSET", "FLUXMET"):
            if resolved_site_id:
                patterns.append(f"AMF_{resolved_site_id}_FLUXNET_{product}_{cadence}_*.csv")
            else:
                patterns.append(f"*_FLUXNET_{product}_{cadence}_*.csv")

    for pattern in patterns:
        candidates = sorted(glob.glob(os.path.join(source_dir, pattern)))
        if candidates:
            return os.path.abspath(candidates[-1])
    return None


def load_tower_shortwave(in_situ_file):
    """Measured-only tower SW_IN (W m-2) indexed by TIMESTAMP_START, or None."""

    header = set(pd.read_csv(in_situ_file, nrows=0).columns)
    value_column, qc_column = _choose_observation_column(
        header, IN_SITU_COMPARISON_SPECS["SRADH"]
    )
    if "TIMESTAMP_START" not in header or value_column is None:
        return None, None
    columns = ["TIMESTAMP_START", value_column] + ([qc_column] if qc_column else [])
    tower = pd.read_csv(in_situ_file, usecols=columns, dtype={"TIMESTAMP_START": str})
    timestamps = pd.to_datetime(tower["TIMESTAMP_START"], format="%Y%m%d%H%M", errors="coerce")
    values = pd.to_numeric(tower[value_column], errors="coerce")
    valid = timestamps.notna() & np.isfinite(values) & values.between(0.0, 1400.0)
    if qc_column:
        valid &= pd.to_numeric(tower[qc_column], errors="coerce").eq(0)
    series = pd.Series(values[valid].to_numpy(), index=timestamps[valid]).sort_index()
    return series[~series.index.duplicated()], value_column


def estimate_time_registration(era5_shortwave, tower_shortwave, frequency):
    """Lag (hours) of the ERA5 clock relative to the tower clock.

    A positive lag means an ERA5 value stamped t + lag belongs to tower time t,
    i.e. the ERA5 file is late. Lags are searched in source-timestep steps by
    maximising the Pearson correlation of shortwave radiation, and checked
    against the difference of the radiation-weighted diurnal centroids.
    """

    step = pd.Timedelta(frequency)
    step_hours = step.total_seconds() / 3600.0
    max_steps = int(round(TIME_REGISTRATION_MAX_LAG_HOURS / step_hours))
    era5_shortwave = era5_shortwave[~era5_shortwave.index.duplicated()]

    correlations = {}
    pairs = {}
    for k in range(-max_steps, max_steps + 1):
        shifted = pd.Series(era5_shortwave.to_numpy(), index=era5_shortwave.index - k * step)
        joined = pd.concat([shifted.rename("era5"), tower_shortwave.rename("tower")], axis=1, join="inner").dropna()
        pairs[k] = int(len(joined))
        if len(joined) >= 2 and joined["era5"].std() > 0 and joined["tower"].std() > 0:
            correlations[k] = float(np.corrcoef(joined["era5"], joined["tower"])[0, 1])

    result = {
        "step_hours": step_hours,
        "pairs_at_zero_lag": pairs.get(0, 0),
        "correlation_by_lag_hours": {f"{k * step_hours:+g}": round(r, 4) for k, r in correlations.items()},
    }
    if 0 not in correlations or pairs[0] < TIME_REGISTRATION_MIN_PAIRS:
        result["status"] = "insufficient_overlap"
        return result

    best = max(correlations, key=correlations.get)
    result.update(
        {
            "status": "estimated",
            "lag_hours": best * step_hours,
            "correlation_at_zero_lag": round(correlations[0], 4),
            "correlation_at_lag": round(correlations[best], 4),
        }
    )

    overlap = tower_shortwave.index.intersection(era5_shortwave.index)
    era5_centroid = radiation_centroid_hour(
        pd.DataFrame({"hour": overlap.hour, "SRADH": era5_shortwave.reindex(overlap).to_numpy()})
    )
    tower_centroid = radiation_centroid_hour(
        pd.DataFrame({"hour": overlap.hour, "SRADH": tower_shortwave.reindex(overlap).to_numpy()})
    )
    if era5_centroid is not None and tower_centroid is not None:
        result["centroid_lag_hours"] = round(wrap_hours(era5_centroid - tower_centroid), 4)
    return result


def register_era5_to_tower(df, frequency, in_situ_file=None, mode="auto", shift_hours=None):
    """Shift ERA5 timestamps onto the tower clock.

    Returns the (possibly shifted) frame and a report. A manual ``shift_hours``
    (ERA5 lag relative to the tower) is applied as given. Otherwise, in "auto"
    mode a detected lag is applied only when it improves the correlation by
    TIME_REGISTRATION_MIN_CORRELATION_GAIN and agrees with the centroid lag to
    within half an hour; "check" only reports; "off" skips the check.
    """

    if mode not in TIME_REGISTRATION_MODES:
        raise ValueError(f"time registration mode must be one of {TIME_REGISTRATION_MODES}")
    report = {"mode": mode, "in_situ_file": os.path.abspath(in_situ_file) if in_situ_file else None,
              "applied_shift_hours": 0.0, "warning": None}

    lag = None
    if shift_hours is not None:
        lag = float(shift_hours)
        report["status"] = "manual"
    elif mode == "off":
        report["status"] = "skipped"
    elif not in_situ_file:
        report["status"] = "unverified"
        report["reason"] = "No AmeriFlux FULLSET file to register ERA5 against."
    else:
        tower, column = load_tower_shortwave(in_situ_file)
        if tower is None or tower.empty:
            report["status"] = "unverified"
            report["reason"] = "Tower file has no measured shortwave radiation."
        else:
            era5 = pd.Series(
                pd.to_numeric(df["SW_IN_ERA"], errors="coerce").to_numpy(),
                index=pd.DatetimeIndex(df["timestamp_start"]),
            )
            era5 = era5[np.isfinite(era5) & era5.between(0.0, 1400.0)]
            estimate = estimate_time_registration(era5, tower, frequency)
            estimate["tower_column"] = column
            report.update(estimate)
            if estimate["status"] == "estimated" and estimate["lag_hours"] != 0:
                gain = estimate["correlation_at_lag"] - estimate["correlation_at_zero_lag"]
                centroid_lag = estimate.get("centroid_lag_hours")
                consistent = centroid_lag is not None and abs(centroid_lag - estimate["lag_hours"]) <= 0.5
                if gain >= TIME_REGISTRATION_MIN_CORRELATION_GAIN and consistent:
                    report["status"] = "misregistered"
                    if mode == "auto":
                        lag = estimate["lag_hours"]
                    else:
                        report["warning"] = (
                            f"ERA5 clock is {estimate['lag_hours']:+g} h relative to the tower "
                            "(not corrected: time registration mode 'check')."
                        )
                else:
                    report["status"] = "ambiguous"
                    report["warning"] = (
                        f"Best ERA5-tower lag {estimate['lag_hours']:+g} h was not applied: "
                        f"correlation gain {gain:.4f}, centroid lag {centroid_lag}."
                    )
            elif estimate["status"] == "estimated":
                report["status"] = "registered"

    if lag:
        start, end = df["timestamp_start"].min(), df["timestamp_start"].max()
        df = df.copy()
        offset = pd.Timedelta(hours=lag)
        df["timestamp_start"] = df["timestamp_start"] - offset
        if "timestamp_end" in df:
            df["timestamp_end"] = df["timestamp_end"] - offset
        report["applied_shift_hours"] = -lag
        report["record_span"] = [start.isoformat(), end.isoformat()]
        report["note"] = (
            f"ERA5 timestamps shifted by {-lag:+g} h onto the tower clock; the record "
            "span is kept and edge steps are filled from the nearest valid value."
        )
        if report["status"] == "misregistered":
            report["status"] = "corrected"
    return df, report


def read_ecosim_hourly_forcing(netcdf_file):
    """Read the generated EcoSIM NetCDF back into an hourly table."""

    frames = []
    with Dataset(netcdf_file, "r") as nc_file:
        years = np.asarray(nc_file.variables["year"][:], dtype=int)
        for year_index, year in enumerate(years):
            timestamps = pd.date_range(
                start=f"{int(year):04d}-01-01 00:00",
                periods=366 * 24,
                freq="h",
            )
            data = {"timestamp_start": timestamps}
            for variable in IN_SITU_COMPARISON_SPECS:
                raw = nc_file.variables[variable][year_index, :, :, 0]
                values = np.asarray(np.ma.filled(raw, np.nan), dtype=float).reshape(-1)
                values[np.abs(values) >= FILL_VALUE_THRESHOLD] = np.nan
                data[variable] = values
            frame = pd.DataFrame(data)
            frame = frame.dropna(subset=tuple(IN_SITU_COMPARISON_SPECS), how="all")
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=("timestamp_start",) + tuple(IN_SITU_COMPARISON_SPECS))
    return pd.concat(frames, ignore_index=True)


def _choose_observation_column(columns, spec):
    for value_column, qc_column in spec["candidates"]:
        if value_column in columns and (qc_column is None or qc_column in columns):
            return value_column, qc_column
    return None, None


def load_in_situ_hourly(in_situ_file):
    """Load measured-only AmeriFlux records and aggregate complete hours."""

    header = set(pd.read_csv(in_situ_file, nrows=0).columns)
    if "TIMESTAMP_START" not in header:
        raise ValueError(f"In situ file lacks TIMESTAMP_START: {in_situ_file}")

    selected = {"TIMESTAMP_START"}
    choices = {}
    temperature_choices = {}
    for forcing_variable, spec in IN_SITU_COMPARISON_SPECS.items():
        value_column, qc_column = _choose_observation_column(header, spec)
        if value_column and "temperature_candidates" in spec:
            temp_column, temp_qc_column = _choose_observation_column(
                header, {"candidates": spec["temperature_candidates"]}
            )
            if temp_column is None:
                value_column, qc_column = None, None
            else:
                temperature_choices[forcing_variable] = (temp_column, temp_qc_column)
                selected.add(temp_column)
                if temp_qc_column:
                    selected.add(temp_qc_column)
        choices[forcing_variable] = (value_column, qc_column)
        if value_column:
            selected.add(value_column)
        if qc_column:
            selected.add(qc_column)

    observations = pd.read_csv(
        in_situ_file,
        usecols=sorted(selected),
        dtype={"TIMESTAMP_START": str},
    )
    observations["timestamp_start"] = pd.to_datetime(
        observations["TIMESTAMP_START"],
        format="%Y%m%d%H%M",
        errors="coerce",
    )
    observations = observations.dropna(subset=["timestamp_start"])
    observations = observations.sort_values("timestamp_start")
    observations = observations.drop_duplicates("timestamp_start", keep="first")

    deltas = observations["timestamp_start"].diff().dropna().dt.total_seconds()
    median_seconds = float(deltas.median()) if not deltas.empty else 3600.0
    expected_records_per_hour = max(1, int(round(3600.0 / median_seconds)))
    observations["hour"] = observations["timestamp_start"].dt.floor("h")

    hourly = {}
    for forcing_variable, spec in IN_SITU_COMPARISON_SPECS.items():
        value_column, qc_column = choices[forcing_variable]
        if not value_column:
            hourly[forcing_variable] = {
                "status": "variable_not_available",
                "observed_column": None,
                "qc_column": None,
            }
            continue

        values = pd.to_numeric(observations[value_column], errors="coerce")
        lower, upper = spec["valid_range"]
        valid = np.isfinite(values) & values.between(lower, upper, inclusive="both")
        if qc_column:
            qc = pd.to_numeric(observations[qc_column], errors="coerce")
            valid &= qc.eq(0)
        if forcing_variable in temperature_choices:
            temp_column, temp_qc_column = temperature_choices[forcing_variable]
            temperature = pd.to_numeric(observations[temp_column], errors="coerce")
            valid &= np.isfinite(temperature) & temperature.between(-90.0, 60.0)
            if temp_qc_column:
                temp_qc = pd.to_numeric(observations[temp_qc_column], errors="coerce")
                valid &= temp_qc.eq(0)
            values = pd.Series(
                vapor_pressure_from_vpd_kpa(temperature, values),
                index=values.index,
            )

        measured = pd.DataFrame(
            {
                "timestamp_start": observations.loc[valid, "hour"],
                "value": values.loc[valid] * spec["scale"],
            }
        )
        grouped = measured.groupby("timestamp_start")["value"]
        if spec["aggregation"] == "sum":
            values_hourly = grouped.sum(min_count=1)
        else:
            values_hourly = grouped.mean()
        counts = grouped.count()
        values_hourly = values_hourly[counts >= expected_records_per_hour]

        hourly[forcing_variable] = {
            "status": "available",
            "observed_column": value_column,
            "qc_column": qc_column,
            "expected_records_per_hour": expected_records_per_hour,
            "data": values_hourly.rename("observed").reset_index(),
        }
    return hourly


def _finite_float(value):
    value = float(value)
    return value if math.isfinite(value) else None


def _large_difference_reasons(metrics, spec):
    reasons = []
    abs_bias = abs(metrics["mean_bias"])
    rmse = metrics["root_mean_square_error"]

    threshold = spec.get("max_abs_mean_bias")
    if threshold is not None and abs_bias > threshold:
        reasons.append(f"absolute mean bias {abs_bias:.3g} > {threshold:g} {spec['units']}")

    threshold = spec.get("max_rmse")
    if threshold is not None and rmse > threshold:
        reasons.append(f"RMSE {rmse:.3g} > {threshold:g} {spec['units']}")

    threshold = spec.get("max_abs_relative_mean_bias")
    minimum_bias = spec.get("relative_bias_min_abs_bias", 0.0)
    relative_bias = metrics.get("relative_mean_bias_fraction")
    if (
        threshold is not None
        and relative_bias is not None
        and abs(relative_bias) > threshold
        and abs_bias >= minimum_bias
    ):
        reasons.append(f"relative mean bias {100.0 * relative_bias:.1f}% exceeds +/-{100.0 * threshold:g}%")

    threshold = spec.get("max_abs_relative_total_bias")
    minimum_total = spec.get("relative_total_min_observed", 0.0)
    relative_total_bias = metrics.get("relative_total_bias_fraction")
    if (
        threshold is not None
        and relative_total_bias is not None
        and metrics.get("observed_total", 0.0) >= minimum_total
        and abs(relative_total_bias) > threshold
    ):
        reasons.append(f"relative total bias {100.0 * relative_total_bias:.1f}% exceeds +/-{100.0 * threshold:g}%")
    return reasons


def compare_forcing_with_in_situ(netcdf_file, in_situ_file, min_pairs=DEFAULT_COMPARISON_MIN_PAIRS):
    """Compare generated forcing against measured-only AmeriFlux observations."""

    min_pairs = max(1, int(min_pairs))
    report = {
        "status": "not_available",
        "in_situ_file": os.path.abspath(in_situ_file) if in_situ_file else None,
        "minimum_paired_hours_for_warning": min_pairs,
        "screening_note": (
            "Thresholds are broad operational screens for forcing failures, not universal "
            "acceptance limits for ERA5 at tower scale."
        ),
        "variables": {},
        "warnings": [],
    }
    if not in_situ_file:
        report["reason"] = "No matching AmeriFlux FULLSET HH/HR file was found."
        return report
    if not os.path.exists(in_situ_file):
        raise FileNotFoundError(f"In situ comparison file not found: {in_situ_file}")

    forcing = read_ecosim_hourly_forcing(netcdf_file)
    observations = load_in_situ_hourly(in_situ_file)
    compared_variables = 0
    sufficient_variables = 0

    for variable, spec in IN_SITU_COMPARISON_SPECS.items():
        observation = observations[variable]
        if observation["status"] != "available":
            report["variables"][variable] = {
                "status": observation["status"],
                "units": spec["units"],
            }
            continue

        paired = forcing[["timestamp_start", variable]].merge(
            observation["data"],
            on="timestamp_start",
            how="inner",
        )
        paired = paired.dropna(subset=[variable, "observed"])
        paired = paired.sort_values("timestamp_start")
        paired_hours = int(len(paired))
        compared_variables += int(paired_hours > 0)

        variable_report = {
            "status": "insufficient_data" if paired_hours < min_pairs else "compared",
            "units": spec["units"],
            "observed_column": observation["observed_column"],
            "qc_column": observation["qc_column"],
            "measured_qc_value": 0 if observation["qc_column"] else None,
            "paired_hours": paired_hours,
        }
        if paired_hours == 0:
            report["variables"][variable] = variable_report
            continue

        forcing_values = paired[variable].to_numpy(dtype=float)
        observed_values = paired["observed"].to_numpy(dtype=float)
        differences = forcing_values - observed_values
        forcing_mean = float(np.mean(forcing_values))
        observed_mean = float(np.mean(observed_values))
        mean_bias = float(np.mean(differences))
        metrics = {
            "forcing_mean": forcing_mean,
            "observed_mean": observed_mean,
            "mean_bias": mean_bias,
            "mean_absolute_error": float(np.mean(np.abs(differences))),
            "root_mean_square_error": float(np.sqrt(np.mean(differences ** 2))),
            "relative_mean_bias_fraction": (
                mean_bias / abs(observed_mean) if abs(observed_mean) > 1.0e-12 else None
            ),
            "forcing_total": float(np.sum(forcing_values)),
            "observed_total": float(np.sum(observed_values)),
            "first_paired_timestamp": paired["timestamp_start"].iloc[0].isoformat(),
            "last_paired_timestamp": paired["timestamp_start"].iloc[-1].isoformat(),
        }
        metrics["relative_total_bias_fraction"] = (
            (metrics["forcing_total"] - metrics["observed_total"])
            / abs(metrics["observed_total"])
            if abs(metrics["observed_total"]) > 1.0e-12
            else None
        )
        if np.std(forcing_values) > 0 and np.std(observed_values) > 0:
            metrics["pearson_correlation"] = _finite_float(
                np.corrcoef(forcing_values, observed_values)[0, 1]
            )
        else:
            metrics["pearson_correlation"] = None
        if variable == "SRADH":
            # A whole-hour clock shift leaves mean bias near zero, so compare the
            # diurnal radiation centroids over the same paired hours.
            hours = paired["timestamp_start"].dt.hour
            forcing_centroid = radiation_centroid_hour(
                pd.DataFrame({"hour": hours, "SRADH": forcing_values})
            )
            observed_centroid = radiation_centroid_hour(
                pd.DataFrame({"hour": hours, "SRADH": observed_values})
            )
            if forcing_centroid is not None and observed_centroid is not None:
                metrics["diurnal_centroid_lag_hours"] = wrap_hours(
                    forcing_centroid - observed_centroid
                )
        variable_report.update(metrics)

        if paired_hours >= min_pairs:
            sufficient_variables += 1
            reasons = _large_difference_reasons(metrics, spec)
            lag = metrics.get("diurnal_centroid_lag_hours")
            if lag is not None and abs(lag) > CLOCK_LAG_WARNING_HOURS:
                reasons.append(
                    f"diurnal radiation centroid lags the tower by {lag:+.2f} h "
                    "(forcing clock likely shifted)"
                )
            if reasons:
                warning = {
                    "variable": variable,
                    "message": (
                        f"{variable} forcing differs substantially from measured "
                        f"{observation['observed_column']}: " + "; ".join(reasons)
                    ),
                }
                variable_report["warning"] = warning["message"]
                report["warnings"].append(warning)

        report["variables"][variable] = variable_report

    if sufficient_variables:
        report["status"] = "compared"
    elif compared_variables:
        report["status"] = "insufficient_data"
        report["reason"] = "Overlapping measured records did not meet the paired-hour minimum."
    else:
        report["reason"] = "No overlapping measured records were available."
    return report


def convert_era5_to_ecosim(
    era5_file,
    output_file,
    longitude,
    elevation=None,
    quality_report_file=None,
    site_id=None,
    in_situ_file=None,
    compare_in_situ=True,
    comparison_min_pairs=DEFAULT_COMPARISON_MIN_PAIRS,
    utc_offset_hours=None,
    time_registration="auto",
    time_shift_hours=None,
):
    """
    Convert ERA5 half-hourly data to ECOSIM hourly format.

    Parameters:
    era5_file (str): Path to the Ameriflux ERA5 CSV file
    output_file (str): Path to output netCDF file
    longitude (float): Longitude for solar noon calculation
    in_situ_file (str): Optional AmeriFlux FULLSET HH/HR file for validation
    utc_offset_hours (float): UTC offset of the CSV timestamps; inferred when None
    time_registration (str): "auto" shifts ERA5 onto the tower clock when a lag
        is detected, "check" only reports it, "off" skips the check
    time_shift_hours (float): Known ERA5 lag relative to the tower (hours);
        applied directly, overriding detection
    """

    # Read the CSV data with dtype specification to avoid automatic conversion
    df = pd.read_csv(era5_file, dtype={'TIMESTAMP_START': str, 'TIMESTAMP_END': str})

    # Parse timestamps
    df['timestamp_start'] = df['TIMESTAMP_START'].apply(parse_timestamps)
    df['timestamp_end'] = df['TIMESTAMP_END'].apply(parse_timestamps)
    source_frequency = infer_source_frequency(df['timestamp_start'])
    record_start = df['timestamp_start'].min()
    record_end = df['timestamp_start'].max()

    # Coregister ERA5 with the tower clock before QC and hourly aggregation.
    registration_in_situ = in_situ_file
    if registration_in_situ is None and time_registration != "off":
        registration_in_situ = discover_in_situ_file(era5_file, site_id)
    df, registration = register_era5_to_tower(
        df,
        source_frequency,
        in_situ_file=registration_in_situ,
        mode=time_registration,
        shift_hours=time_shift_hours,
    )
    if registration["applied_shift_hours"]:
        print(f"Time registration: {registration['note']}")
    if registration["warning"]:
        print(f"WARNING: {registration['warning']}", file=sys.stderr)

    df, quality_report = sanitize_era5_dataframe(
        df,
        elevation_m=elevation,
        frequency=source_frequency,
        index_start=record_start,
        index_end=record_end,
    )
    quality_report["time_registration"] = registration

    # Convert to hourly data by averaging consecutive half-hourly values.
    df['hour'] = df['timestamp_start'].dt.hour
    df['day_of_year'] = df['timestamp_start'].dt.dayofyear
    df['year'] = df['timestamp_start'].dt.year
    # Convert VPD to vapor pressure before averaging so each half-hour uses its
    # own saturation vapor pressure.
    df['EA_KPA'] = vapor_pressure_from_vpd_kpa(df['TA_ERA'], df['VPD_ERA'])

    hourly_df = (
        df.groupby(['year', 'day_of_year', 'hour'], as_index=False)
        .agg(
            TMPH=('TA_ERA', 'mean'),
            WINDH=('WS_ERA', 'mean'),
            RAINH=('P_ERA', 'sum'),
            DWPTH=('EA_KPA', 'mean'),
            SRADH=('SW_IN_ERA', 'mean'),
            PATM=('PA_ERA', 'mean'),
        )
        .rename(columns={'day_of_year': 'day'})
    )
    # Create the netCDF file
    solar_noon = create_ecosim_climate_file(
        hourly_df, output_file, longitude, utc_offset_hours=utc_offset_hours
    )
    quality_report["solar_noon"] = solar_noon
    if solar_noon["warning"]:
        print(f"WARNING: {solar_noon['warning']}", file=sys.stderr)

    if compare_in_situ:
        selected_in_situ_file = in_situ_file or discover_in_situ_file(era5_file, site_id)
        comparison = compare_forcing_with_in_situ(
            output_file,
            selected_in_situ_file,
            min_pairs=comparison_min_pairs,
        )
        quality_report["in_situ_comparison"] = comparison
        if comparison["status"] == "not_available":
            print(f"In situ comparison skipped: {comparison.get('reason', 'observations unavailable')}")
        elif comparison["status"] == "insufficient_data":
            print(f"In situ comparison incomplete: {comparison.get('reason', 'insufficient overlap')}")
        else:
            print(
                f"Compared forcing with {comparison['in_situ_file']}; "
                f"{len(comparison['warnings'])} large-difference warning(s)."
            )
        for warning in comparison["warnings"]:
            print(f"WARNING: {warning['message']}", file=sys.stderr)
    else:
        quality_report["in_situ_comparison"] = {
            "status": "skipped",
            "reason": "Disabled by caller.",
            "variables": {},
            "warnings": [],
        }

    # Name the file by the forcing years it contains, e.g.
    # US-UMB_ecosim_climate_1981-2021.nc. Intermediate files that do not end in
    # .nc (such as the climate tool's *.nc.temp) are left for the caller to name.
    if str(output_file).endswith(".nc"):
        output_file = str(finalize_climate_file_name(output_file))
        print(f"Climate forcing written to {output_file}")
    quality_report["output_file"] = os.path.abspath(output_file)

    # Record the output in a YAML file
    try:
        import yaml
        if site_id:
            result_dir = f"result/{site_id}"
            os.makedirs(result_dir, exist_ok=True)
            yaml_path = os.path.join(result_dir, f"{site_id}_forcing.yaml")

            forcing_data = {"clm_hour_file_in": os.path.abspath(output_file)}

            # If YAML already exists, preserve other keys (like grid_file_in)
            if os.path.exists(yaml_path):
                with open(yaml_path, 'r') as f:
                    existing_data = yaml.safe_load(f) or {}
                    forcing_data.update(existing_data)
                    forcing_data["clm_hour_file_in"] = os.path.abspath(output_file)

            with open(yaml_path, 'w') as f:
                yaml.dump(forcing_data, f, default_flow_style=False)
            print(f"Climate forcing path recorded in {yaml_path}")
    except ImportError:
        print("PyYAML not installed; could not record forcing path to YAML.", file=sys.stderr)
    except Exception as e:
        print(f"Error recording forcing path to YAML: {e}", file=sys.stderr)

    if quality_report_file:
        quality_report_dir = os.path.dirname(quality_report_file)
        if quality_report_dir:
            os.makedirs(quality_report_dir, exist_ok=True)
        with open(quality_report_file, "w") as f:
            json.dump(quality_report, f, indent=2)
    return quality_report

def radiation_centroid_hour(df):
    """Radiation-weighted mean hour of the diurnal SRADH cycle on the forcing clock.

    Hour index h holds the average over [h, h+1), so its midpoint is h + 0.5.
    A circular mean keeps the result valid when the daylight period wraps midnight.
    """
    profile = df.groupby('hour')['SRADH'].mean().clip(lower=0.0)
    if profile.sum() <= 0.0:
        return None
    angles = 2.0 * np.pi * (profile.index.to_numpy(dtype=float) + 0.5) / 24.0
    weights = profile.to_numpy(dtype=float)
    angle = math.atan2((weights * np.sin(angles)).sum(), (weights * np.cos(angles)).sum())
    return (angle * 24.0 / (2.0 * np.pi)) % 24.0


def wrap_hours(value):
    """Wrap an hour difference into [-12, 12)."""
    return (value + 12.0) % 24.0 - 12.0


def infer_utc_offset_hours(df, longitude):
    """Infer the forcing clock's whole-hour UTC offset from the diurnal SRADH cycle.

    AmeriFlux timestamps are local standard time, but the offset is not stored in
    the CSV, and rounding longitude/15 fails for sites west of their zone meridian
    (e.g. US-UMB at -84.7 deg uses UTC-5, not UTC-6).
    """
    centroid = radiation_centroid_hour(df)
    if centroid is None:
        raise ValueError("Cannot infer UTC offset: SRADH has no positive values.")
    return int(round(wrap_hours(centroid - (12.0 - longitude / 15.0))))


def mean_solar_noon_hour(longitude, utc_offset_hours):
    """Mean solar noon (hours) on the forcing clock.

    EcoSIM DayMod adds the daily equation of time to ZNOONG, so it is not
    included here.
    """
    return (12.0 - longitude / 15.0 + utc_offset_hours) % 24.0


def create_ecosim_climate_file(df, output_file, longitude, utc_offset_hours=None):
    """
    Create ECOSIM climate forcing netCDF file from hourly data.

    Parameters:
    df (DataFrame): Hourly climate data on the AmeriFlux (local standard time) clock
    output_file (str): Path to output netCDF file
    longitude (float): Longitude of the site for solar noon calculation
    utc_offset_hours (float): UTC offset of the forcing clock; inferred from the
        diurnal SRADH cycle when None

    Returns:
    dict: Solar-noon derivation and consistency check for the quality report
    """

    centroid = radiation_centroid_hour(df)
    if utc_offset_hours is None:
        utc_offset_hours = infer_utc_offset_hours(df, longitude)
        offset_source = "inferred_from_SRADH_diurnal_cycle"
    else:
        offset_source = "user_supplied"
    znoong = mean_solar_noon_hour(longitude, utc_offset_hours)
    mismatch = None if centroid is None else wrap_hours(centroid - znoong)
    solar_noon = {
        "ZNOONG_hours": round(znoong, 4),
        "utc_offset_hours": utc_offset_hours,
        "utc_offset_source": offset_source,
        "SRADH_centroid_hour": None if centroid is None else round(centroid, 4),
        "centroid_minus_ZNOONG_hours": None if mismatch is None else round(mismatch, 4),
        "warning": None,
    }
    if mismatch is not None and abs(mismatch) > SOLAR_NOON_MISMATCH_WARNING_HOURS:
        solar_noon["warning"] = (
            f"ZNOONG={znoong:.2f} h differs from the SRADH diurnal centroid "
            f"{centroid:.2f} h by {mismatch:+.2f} h; EcoSIM will clip radiation "
            "with a misplaced sun angle. Check the UTC offset."
        )

    # Create a new netCDF file
    nc_file = Dataset(output_file, 'w', format='NETCDF4')

    # Define dimensions
    nyears = len(df['year'].unique())
    ndays = 366
    nhours = 24
    ngrid = 1

    nc_file.createDimension('year', nyears)
    nc_file.createDimension('day', ndays)
    nc_file.createDimension('hour', nhours)
    nc_file.createDimension('ngrid', ngrid)

    # Create variables
    # Temperature (oC)
    tmp_var = nc_file.createVariable('TMPH', 'f4', ('year', 'day', 'hour', 'ngrid'), fill_value=1e30)
    tmp_var.long_name = "hourly air temperature"
    tmp_var.units = "oC"

    # Wind speed (m/s)
    wind_var = nc_file.createVariable('WINDH', 'f4', ('year', 'day', 'hour', 'ngrid'), fill_value=1e30)
    wind_var.long_name = "horizontal wind speed"
    wind_var.units = "m s^-1"

    # Precipitation (mm m^-2 hr^-1) - Need to convert from mm/h to mm m^-2 hr^-1
    rain_var = nc_file.createVariable('RAINH', 'f4', ('year', 'day', 'hour', 'ngrid'), fill_value=1e30)
    rain_var.long_name = "Total precipitation"
    rain_var.units = "mm m^-2 hr^-1"

    # Vapor pressure (kPa)
    dwpt_var = nc_file.createVariable('DWPTH', 'f4', ('year', 'day', 'hour', 'ngrid'), fill_value=1e30)
    dwpt_var.long_name = "atmospheric vapor pressure"
    dwpt_var.units = "kPa"

    # Solar radiation (W m^-2)
    srad_var = nc_file.createVariable('SRADH', 'f4', ('year', 'day', 'hour', 'ngrid'), fill_value=1e30)
    srad_var.long_name = "Incident solar radiation"
    srad_var.units = "W m^-2"

    # Atmospheric pressure (kPa)
    patm_var = nc_file.createVariable('PATM', 'f4', ('year', 'day', 'hour', 'ngrid'), fill_value=1e30)
    patm_var.long_name = "Surface atmospheric pressure"
    patm_var.units = "kPa"

    # Year variable
    year_var = nc_file.createVariable('year', 'i4', ('year',))
    year_var.long_name = "year AD"

    # Other variables with fixed values for this site
    z0g_var = nc_file.createVariable('Z0G', 'f4', ('year', 'ngrid'), fill_value=1e30)
    z0g_var.long_name = "windspeed measurement height"
    z0g_var.units = "m"

    iflgw_var = nc_file.createVariable('IFLGW', 'i4', ('year', 'ngrid'))  # No fill_value for integer variables
    iflgw_var.long_name = "flag for raising Z0G with vegeation"

    znoong_var = nc_file.createVariable('ZNOONG', 'f4', ('year', 'ngrid'), fill_value=1e30)
    znoong_var.long_name = "time of solar noon"
    znoong_var.units = "hour"
    znoong_var.note = (
        f"Mean solar noon on the forcing clock (UTC{utc_offset_hours:+g}, {offset_source}); "
        "EcoSIM adds the daily equation of time."
    )

    # Create a simple mapping of years to indices
    unique_years = sorted(df['year'].unique())

    year_var[:] = unique_years

    year_lookup = {year: idx for idx, year in enumerate(unique_years)}
    year_idx = df['year'].map(year_lookup).to_numpy(dtype=np.int64)
    day_idx = df['day'].to_numpy(dtype=np.int64) - 1
    hour_idx = df['hour'].to_numpy(dtype=np.int64)

    for variable, column in (
        (tmp_var, 'TMPH'),
        (wind_var, 'WINDH'),
        (rain_var, 'RAINH'),
        (dwpt_var, 'DWPTH'),
        (srad_var, 'SRADH'),
        (patm_var, 'PATM'),
    ):
        data = np.full((nyears, ndays, nhours, ngrid), 1e30, dtype='f4')
        data[year_idx, day_idx, hour_idx, 0] = df[column].to_numpy(dtype='f4')
        variable[:] = data

    # Set other fixed variables
    for i, year in enumerate(unique_years):
        z0g_var[i, 0] = 1.0  # Fixed value
        iflgw_var[i, 0] = 0  # Fixed value
        znoong_var[i, 0] = znoong

    # Close the file
    nc_file.close()

    print(f"ECOSIM climate file created successfully: {output_file}")
    return solar_noon

def main():
    parser = argparse.ArgumentParser(description='Convert Ameriflux ERA5 half-hourly climate data to ECOSIM hourly format')
    parser.add_argument('--input', '-i', required=True, help='Input CSV file path')
    parser.add_argument('--output', '-o', required=True, help='Output netCDF file path')
    parser.add_argument('--site-id', '-s', required=True, help='AmeriFlux site ID (e.g., US-Ha1) to get longitude from')
    parser.add_argument(
        '--quality-report',
        help='Optional JSON report for physical checks, interpolation repairs, and in situ comparison',
    )
    parser.add_argument(
        '--in-situ',
        help='Optional AmeriFlux FULLSET HH/HR CSV; otherwise auto-discover beside the ERA5 input',
    )
    parser.add_argument(
        '--comparison-min-pairs',
        type=int,
        default=DEFAULT_COMPARISON_MIN_PAIRS,
        help='Minimum paired measured hours required before large-difference warnings (default: 720)',
    )
    parser.add_argument(
        '--skip-in-situ-comparison',
        action='store_true',
        help='Disable comparison against an available AmeriFlux FULLSET file',
    )
    parser.add_argument(
        '--utc-offset',
        type=float,
        default=None,
        help='UTC offset (hours) of the CSV timestamps, e.g. -5 for EST; '
             'inferred from the diurnal SW_IN cycle when omitted',
    )
    parser.add_argument(
        '--time-registration',
        choices=TIME_REGISTRATION_MODES,
        default='auto',
        help='Coregister ERA5 with the tower clock using SW_IN cross-correlation: '
             'auto (detect and shift, default), check (report only), off',
    )
    parser.add_argument(
        '--time-shift-hours',
        type=float,
        default=None,
        help='Known ERA5 lag relative to the tower in hours (positive = ERA5 late); '
             'applied directly instead of detection',
    )

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input file {args.input} does not exist", file=sys.stderr)
        sys.exit(1)

    # Get longitude and elevation from site info
    site_data = get_site_metadata(args.site_id)
    longitude = site_data.get('ALONG') if site_data else None
    if longitude is None:
        print(f"Error: Could not get longitude for site {args.site_id}", file=sys.stderr)
        sys.exit(1)
    elevation = site_data.get('ALTIG') if site_data else None

    print(f"Using longitude {longitude} for site {args.site_id}")
    convert_era5_to_ecosim(
        args.input,
        args.output,
        longitude,
        elevation=elevation,
        quality_report_file=args.quality_report,
        site_id=args.site_id,
        in_situ_file=args.in_situ,
        compare_in_situ=not args.skip_in_situ_comparison,
        comparison_min_pairs=args.comparison_min_pairs,
        utc_offset_hours=args.utc_offset,
        time_registration=args.time_registration,
        time_shift_hours=args.time_shift_hours,
    )
    print("Conversion completed successfully!")

if __name__ == "__main__":
    main()
