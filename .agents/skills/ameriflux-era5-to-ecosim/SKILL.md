---
name: ameriflux-era5-to-ecosim
description: Convert AmeriFlux ERA5 half-hourly meteorological CSV files into EcoSIM hourly climate forcing NetCDF files, compare generated forcing against overlapping measured tower meteorology when a FULLSET file is available, and report large biases or errors. Use when preparing or validating EcoSIM climate inputs for AmeriFlux sites, checking ERA5 against in situ records, diagnosing forcing discrepancies, or writing climate-quality reports. For non-AmeriFlux paper sites that need a fresh Copernicus CDS ERA5 point download by longitude/latitude, use era5-cds-point-download first.
---

# AmeriFlux ERA5 to EcoSIM Converter

## Use When

- You need to convert AmeriFlux ERA5 CSV forcing data to EcoSIM NetCDF climate forcing.
- You need hourly `TMPH`, `WINDH`, `RAINH`, `DWPTH`, `SRADH`, or `PATM` variables.
- You need range-aware quality control, comparison against in situ meteorology, or a JSON quality report for climate forcing.
- You already have an AmeriFlux-format ERA5 CSV. If the task is to download global CDS ERA5 point data for a non-AmeriFlux literature site, use `era5-cds-point-download` before any EcoSIM conversion.

## Constraints
- NEVER use it extract soil data.

## Overview

This skill converts AmeriFlux ERA5 half-hourly climate forcing data into the EcoSIM hourly climate format. When a matching AmeriFlux FULLSET file is available, it also reads the generated NetCDF back and compares the forcing against measured-only tower records.

For ordinary paper-derived sites outside AmeriFlux/FLUXNET, keep ERA5 acquisition separate: use `era5-cds-point-download` to retrieve Copernicus CDS point time series by longitude/latitude, inspect source units and accumulation conventions, then adapt or convert the data into the AmeriFlux-format columns expected here only after those units are verified.

## Input Data Format

The input is a CSV file with the following columns:
- `TIMESTAMP_START`: Start timestamp (YYYYMMDDHHMM)
- `TIMESTAMP_END`: End timestamp (YYYYMMDDHHMM)
- `TA_ERA`: Air temperature (°C)
- `SW_IN_ERA`: Shortwave incoming radiation (W m⁻²)
- `LW_IN_ERA`: Longwave incoming radiation (W m⁻²)
- `VPD_ERA`: Vapor pressure deficit (hPa in AmeriFlux ERA5 products)
- `PA_ERA`: Atmospheric pressure (kPa)
- `P_ERA`: Precipitation (mm h⁻¹)
- `WS_ERA`: Wind speed (m s⁻¹)

Pay close attention to the source ERA5 time step before conversion. AmeriFlux
archives may provide `ERA5_HH` half-hourly files or `ERA5_HR` hourly files, and
precipitation aggregation must match that cadence to avoid doubling or otherwise
distorting the water input.

## Output Data Format

Climate NetCDF files are named by the forcing years they contain: `<SITE_ID>_ecosim_climate_<first>-<last>.nc` (e.g. `US-UMB_ecosim_climate_1981-2021.nc`). The writer reads the `year` variable after writing and renames the file, so an `--output` given without years (or with a stale range) gets the actual range appended; the final path is printed and recorded in the quality report (`output_file`).

The output is a netCDF file with the following variables:
- `TMPH`: Hourly air temperature (°C)
- `WINDH`: Hourly wind speed (m s⁻¹)
- `RAINH`: Hourly precipitation (mm m⁻² hr⁻¹)
- `DWPTH`: Hourly actual atmospheric vapor pressure (kPa), computed as `es(TA_ERA) - VPD_ERA/10` with EcoSIM's `vapsat0` saturation formula. EcoSIM reads `DWPTH` as vapor pressure (`VPK=min(DWPTH,VPS)`), never as a deficit.
- `SRADH`: Hourly incident solar radiation (W m⁻²)
- `PATM`: Hourly Surface atmospheric pressure (kPa)
- `year`: Year AD
- `Z0G`: Windspeed measurement height (m)
- `IFLGW`: Flag for raising Z0G with vegetation
- `ZNOONG`: Mean solar noon on the forcing clock (hour), `12 - lon/15 + utc_offset`. AmeriFlux timestamps are local standard time, so `ZNOONG` must be local too; EcoSIM adds the daily equation of time and caps hourly `SRADH` by the sun angle from `ZNOONG`.

## Workflow

1. **Data Reading**: Reads half-hourly climate data from the input CSV file
2. **Timestamp Parsing**: Converts timestamp strings to datetime objects
3. **Data Aggregation**: Averages consecutive half-hourly values to create hourly data
4. **Quality Control**: Masks physically invalid values before aggregation and fills those gaps by time interpolation.
5. **Variable Mapping**: Maps ERA5 variables to ECOSIM variable names and units
6. **NetCDF Creation**: Creates a properly formatted netCDF file in ECOSIM format
7. **In Situ Validation**: Reads the generated NetCDF and compares it with complete, measured-only hourly tower records from an overlapping AmeriFlux FULLSET file.
8. **Warning Report**: Adds comparison metrics and large-difference warnings to the JSON quality report and prints warnings to stderr.

## Physical Range Checks

The converter must ensure derived climate values are in legitimate ranges before writing EcoSIM NetCDF:
- `TMPH` from `TA_ERA`: -90 to 60 degC.
- `WINDH` from `WS_ERA`: 0 to 75 m s^-1.
- `RAINH` from `P_ERA`: non-negative source precipitation.
- `DWPTH` from `VPD_ERA` and `TA_ERA`: non-negative source vapor pressure deficit; resulting vapor pressure clipped to `[0, es(TA)]`.
- `SRADH` from `SW_IN_ERA`: 0 to 1400 W m^-2.
- `PATM` from `PA_ERA`: kPa bounds centered on site elevation when `ALTIG` is available, otherwise broad physical fallback bounds.

For sentinel values, non-finite values, and values outside these bounds, mask the value and interpolate along the half-hourly time axis. Use nearest-edge filling only at the beginning or end of the available source period. Emit a JSON quality report when `--quality-report` is supplied.

## Time Registration (ERA5 vs Tower Clock)

Some AmeriFlux releases ship an ERA5 file whose clock is offset from the tower record (e.g. US-Ton 5-7, US-Ne1 4-7, and US-Wkg 4-7 are 1 h late; their tower `SW_IN_F` is on local standard time). Mean bias cannot detect this, so the converter coregisters ERA5 with the tower before QC and hourly aggregation:

1. Find the FULLSET file (`--in-situ`, or auto-discovery beside the ERA5 CSV) and load measured `SW_IN_F` (QC 0).
2. Cross-correlate `SW_IN_ERA` with tower `SW_IN_F` over lags of +/-3 h in source-timestep steps; require at least 2000 paired steps.
3. Treat a non-zero best lag as real only when it raises the correlation by at least 0.01 over zero lag and agrees with the diurnal-centroid lag within 0.5 h; otherwise report `ambiguous` and do not shift.
4. In `--time-registration auto` (default), shift every ERA5 variable onto the tower clock, keep the original record span, and fill the shifted-out edge steps from the nearest valid value. `check` reports without shifting; `off` skips the check. `--time-shift-hours` applies a known lag (positive = ERA5 late) without a tower file.
5. Infer `ZNOONG` after registration, so it follows the corrected (tower) clock.

The quality report's `time_registration` block records `status` (`registered`, `corrected`, `misregistered` in check mode, `ambiguous`, `unverified` without a tower file, `insufficient_overlap`, `manual`, `skipped`), the lag, correlations by lag, centroid lag, and `applied_shift_hours`. After a correction, confirm that the in situ `SRADH` `diurnal_centroid_lag_hours` is near zero. A record without a tower file is `unverified`: its clock is assumed, not checked.

## Solar Noon and Forcing Clock

- The UTC offset of the CSV clock is not stored in AmeriFlux files. By default the converter infers it as the whole hour nearest to `(SW_IN_ERA diurnal centroid) - (12 - lon/15)`; rounding `lon/15` is wrong for sites far from their zone meridian (US-UMB at -84.7 deg is UTC-5). Pass `--utc-offset` to override.
- The quality report's `solar_noon` block records `ZNOONG`, the offset and its source, and the centroid mismatch; a mismatch above 0.75 h is a warning.

## In Situ Comparison

For AmeriFlux sites, compare against observations whenever possible:

1. Use `--in-situ <FULLSET_HH_or_HR.csv>` when the observation file is known. Otherwise, auto-discover a matching `FULLSET_HH` or `FULLSET_HR` CSV beside the ERA5 input.
2. Prefer `TA_F`, `WS_F`, `P_F`, `VPD_F`, `SW_IN_F`, and `PA_F`, but retain only records whose corresponding `*_F_QC` value is `0` (measured). Do not validate ERA5 against records whose QC value indicates gap filling or ERA substitution.
3. If consolidated fields are unavailable, use direct measured columns such as `TA`, `WS`, `P`, `VPD`, `SW_IN`, or `PA` when present.
4. Convert measured `VPD_F` to vapor pressure with measured `TA_F` (both QC 0). Average temperature, wind, vapor pressure, shortwave radiation, and pressure to hourly values. Sum precipitation. Require every source interval within an hour to be measured.
5. Compare the observations against values read back from the generated NetCDF, not merely against the pre-write DataFrame.
6. Record paired hours, forcing and observed means, mean bias, MAE, RMSE, Pearson correlation, and relative bias. For precipitation, emphasize total bias over paired intervals.
7. Require at least 720 paired measured hours before issuing difference warnings by default. Use `--comparison-min-pairs` only when a shorter validation period is scientifically justified.

Use these broad operational warning screens:

| EcoSIM variable | Large-difference screen |
| --- | --- |
| `TMPH` | absolute mean bias > 2 degC or RMSE > 5 degC |
| `WINDH` | absolute mean bias > 1.5 m s-1, RMSE > 3 m s-1, or relative mean bias > 50% when absolute bias is at least 0.5 m s-1 |
| `RAINH` | absolute relative total bias > 30% when measured total is at least 10 mm |
| `DWPTH` | absolute mean bias > 0.30 kPa, RMSE > 0.75 kPa, or relative mean bias > 40% when absolute bias is at least 0.15 kPa |
| `SRADH` | absolute mean bias > 40 W m-2, RMSE > 150 W m-2, or relative mean bias > 30% when absolute bias is at least 20 W m-2 |
| `PATM` | absolute mean bias > 2 kPa or RMSE > 4 kPa |

Treat these as failure-detection screens, not universal ERA5 performance criteria. A warning requires diagnosis of units, timestamp convention, accumulation interval, source-product processing, and representativeness. Never apply an automatic multiplicative correction from these metrics. If no suitable observations are found, record `not_available` in the quality report rather than treating the comparison as passed.

## Usage
To execute the skill, run the following command from the project root. The resulting JSON will be saved to the `./result/` directory:

```bash
python .agents/skills/ameriflux-era5-to-ecosim/era5_to_ecosim_converter.py \
  --input data/AMF_US-Ha1_FLUXNET_FULLSET_1991-2020_3-5/AMF_US-Ha1_FLUXNET_ERA5_HR_1981-2021_3-5.csv \
  --output result/US-Ha1/US-Ha1_ecosim_climate.nc \
  --site-id US-Ha1 \
  --in-situ data/AMF_US-Ha1_FLUXNET_FULLSET_1991-2020_3-5/AMF_US-Ha1_FLUXNET_FULLSET_HH_1991-2020_3-5.csv \
  --quality-report result/US-Ha1/US-Ha1_era5_quality_report.json \
  [--utc-offset -5] [--time-registration auto|check|off] [--time-shift-hours 1]
```

Omit `--in-situ` to use automatic discovery. Use `--skip-in-situ-comparison` only when the task explicitly excludes tower validation.

## Key Features

- Handles physically invalid source data by interpolation before NetCDF writing
- Properly converts precipitation from half-hourly to hourly values (summing)
- Averages temperature, wind speed, and solar radiation over half-hour periods
- Compares generated values with measured-only AmeriFlux meteorology when available
- Emits variable-specific warnings for large bias or RMSE
- Supports multiple years of data
- Creates valid netCDF files with proper metadata

## Limitations

- Calendar padding that is not present in the source data may still use EcoSIM fill values.
- In situ comparison depends on overlapping FULLSET observations and their QC flags.
- Warning thresholds diagnose large discrepancies but do not establish that ERA5 or the tower is universally correct.
- Does not automatically bias-correct climate forcing.

## Requirements

- Python 3.6+
- pandas
- numpy
- netCDF4
