"""Quality controls for EcoSIM climate forcing derivation."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

FILL_VALUE = 1.0e30

ERA5_COLUMNS = (
    "TA_ERA",
    "WS_ERA",
    "P_ERA",
    "VPD_ERA",
    "SW_IN_ERA",
    "PA_ERA",
)

BASE_ERA5_LIMITS = {
    "TA_ERA": (-90.0, 60.0),      # degC
    "WS_ERA": (0.0, 75.0),        # m s-1
    "P_ERA": (0.0, 500.0),        # mm per source interval
    "VPD_ERA": (0.0, 100.0),      # hPa in AmeriFlux ERA5 products
    "SW_IN_ERA": (0.0, 1400.0),   # W m-2
    "PA_ERA": (45.0, 110.0),      # kPa fallback bounds
}


def _as_float_or_none(value: Any) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def pressure_limits_for_elevation(elevation_m: Optional[Any]) -> Tuple[float, float]:
    """Return broad kPa bounds centered on standard-atmosphere pressure."""

    elevation = _as_float_or_none(elevation_m)
    if elevation is None:
        return BASE_ERA5_LIMITS["PA_ERA"]

    # The standard atmosphere expression is valid through the lower troposphere.
    elevation = min(max(elevation, -500.0), 9000.0)
    expected = 101.325 * (1.0 - 2.25577e-5 * elevation) ** 5.25588
    return max(45.0, expected * 0.80), min(110.0, expected * 1.20)


def era5_physical_limits(elevation_m: Optional[Any] = None) -> Dict[str, Tuple[float, float]]:
    limits = dict(BASE_ERA5_LIMITS)
    limits["PA_ERA"] = pressure_limits_for_elevation(elevation_m)
    return limits


def sanitize_era5_dataframe(
    df: pd.DataFrame,
    *,
    timestamp_col: str = "timestamp_start",
    elevation_m: Optional[Any] = None,
    frequency: str = "30min",
    index_start: Optional[Any] = None,
    index_end: Optional[Any] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Mask physically invalid ERA5 values and gap-fill them by time interpolation.

    The function also reindexes to the expected half-hourly source cadence so
    true gaps in the input stream are treated the same way as invalid values.
    ``index_start``/``index_end`` fix the reindexed span (e.g. the original record
    span after a time-registration shift); rows outside it are dropped and steps
    missing at the edges are filled from the nearest valid value.
    """

    if timestamp_col not in df.columns:
        raise ValueError(f"Missing timestamp column: {timestamp_col}")

    working = df.copy()
    working[timestamp_col] = pd.to_datetime(working[timestamp_col])
    working = working.sort_values(timestamp_col)

    duplicate_timestamps = int(working.duplicated(timestamp_col).sum())
    if duplicate_timestamps:
        working = working.drop_duplicates(timestamp_col, keep="first")

    source_rows = int(len(working))
    limits = era5_physical_limits(elevation_m)
    report: Dict[str, Any] = {
        "source_rows": source_rows,
        "duplicate_timestamps_removed": duplicate_timestamps,
        "elevation_m": _as_float_or_none(elevation_m),
        "source_frequency": frequency,
        "missing_half_hour_steps_filled": 0,
        "variables": {},
    }

    if working.empty:
        return working, report

    working = working.set_index(timestamp_col)
    span_start = pd.Timestamp(index_start) if index_start is not None else working.index.min()
    span_end = pd.Timestamp(index_end) if index_end is not None else working.index.max()
    working = working.loc[(working.index >= span_start) & (working.index <= span_end)]
    expected_index = pd.date_range(span_start, span_end, freq=frequency)
    missing_steps = expected_index.difference(working.index)
    report["missing_half_hour_steps_filled"] = int(len(missing_steps))
    working = working.reindex(expected_index)
    working.index.name = timestamp_col

    for column in ERA5_COLUMNS:
        if column not in working.columns:
            continue

        series = pd.to_numeric(working[column], errors="coerce")
        lower, upper = limits[column]
        non_finite = ~np.isfinite(series)
        sentinel = series.abs().ge(FILL_VALUE / 10.0).fillna(False)
        out_of_range = series.lt(lower).fillna(False) | series.gt(upper).fillna(False)
        invalid = non_finite | sentinel | out_of_range

        # Count only source-row invalid values separately from introduced gaps.
        original_series = pd.to_numeric(df[column], errors="coerce")
        original_non_finite = ~np.isfinite(original_series)
        original_sentinel = original_series.abs().ge(FILL_VALUE / 10.0).fillna(False)
        original_out_of_range = (
            original_series.lt(lower).fillna(False)
            | original_series.gt(upper).fillna(False)
        )
        original_invalid = original_non_finite | original_sentinel | original_out_of_range

        series = series.mask(invalid)
        gaps_before = int(series.isna().sum())
        filled = series.interpolate(method="time", limit_direction="both")
        gaps_after = int(filled.isna().sum())
        working[column] = filled

        report["variables"][column] = {
            "valid_min": lower,
            "valid_max": upper,
            "invalid_source_values": int(original_invalid.sum()),
            "gap_values_filled_by_interpolation": gaps_before - gaps_after,
            "remaining_missing_values": gaps_after,
        }

    cleaned = working.reset_index()
    cleaned["TIMESTAMP_START"] = cleaned[timestamp_col].dt.strftime("%Y%m%d%H%M")
    cleaned["timestamp_end"] = cleaned[timestamp_col] + pd.Timedelta(frequency)
    cleaned["TIMESTAMP_END"] = cleaned["timestamp_end"].dt.strftime("%Y%m%d%H%M")
    report["rows_after_reindex"] = int(len(cleaned))
    return cleaned, report
