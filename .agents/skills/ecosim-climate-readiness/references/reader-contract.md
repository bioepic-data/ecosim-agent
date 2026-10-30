# Supported standalone EcoSIM climate reader contract

This checker implements the contract traced in `2model_code` at model commit
`4b1240b2` (root bounds update). Paths below are relative to the model root,
which is normally the parent of `ecosim-agent`. When another model version or
driver is selected, inspect its source before extending these assumptions.

## Sources and selection

- `drivers/ecosim/ecosim.F90`: expand `forc_periods` triplets in order; each
  cycle traverses its inclusive forcing-year range, ascending or descending.
  Each forcing year advances the model year, starting with the year in
  `start_date`. Zero cycles select no years. A partial Fortran array assignment
  retains the driver defaults in omitted entries: `(1980,1980,1)`,
  `(1981,1988,2)`, `(1989,2008,1)`, then two zero-cycle triplets. The report
  records the effective array and warns when active defaults were inherited.
- `f90src/Utils/ecosim_time_mod.F90`: year-based stop length is the smaller of
  `stop_n` and scheduled years. Other stop counters advance from the initial
  model-year January 1, including skipped time before a partial-year start or
  restart. Therefore a midyear branch's `stop_n=2,stop_option='ndays'` does
  not necessarily mean two days after that checkpoint. The checker rejects a
  stop boundary before the effective start rather than claiming coverage.
- `f90src/IOutils/RestartMod.F90::get_restart_date`: continuation uses the
  first line of `rpointer.esim`; `.r.YYYY-MM-DD-SSSSSS.nc` encodes seconds
  since midnight. `--run-cwd` controls relative pointer and checkpoint paths.
  The checker requires an existing checkpoint, but does not validate its state
  content or history-restart companions. Custom instance suffixes are outside
  this standalone contract.
- `f90src/IOutils/ClimReadMod.F90::get_clm_years/get_forc_step_type`: if both
  files exist, use daily records for forcing years before the **first hourly
  year**; otherwise use hourly. This is not an arbitrary per-year fallback.
  The current routine does not support a daily-only configuration: its default
  hourly boundary is `-999`, so it selects the empty hourly path. Missing years
  in the selected file fail rather than borrowing records from the other file.

## File shape and calendar

`clm_hour_file_in` is a NetCDF with `year(year)` and meteorological arrays in
Python/C dimension order `(year,day,hour,ngrid)`. Require `day=366`, `hour=24`,
`ngrid=1`, with unique, increasing integer year coordinates. Daily arrays use
`(year,day,ngrid)`. Annual auxiliaries use `(year,ngrid)`.

Records are addressed by model day of year. The climate reader copies forcing
day 365 into slot 366 for a non-leap forcing year. A leap model year paired
with non-leap forcing therefore reuses the last forcing day for model day 366.
A non-leap model year uses only days 1–365, even with leap-year forcing. The
checker follows these array accesses, rather than assuming calendar dates
match between cycled forcing and model years. Printed dates describe the
forcing array's Gregorian date, not necessarily the model's month/day.

`day`/`hour` dimension slots describe cadence, but cannot prove the physical
clock registration of the underlying observations. The checker must not infer
that a slot means UTC or local time, or an interval start/end.

The `ncdio_pio` wrapper reads raw NetCDF values. It does not implement CF
`scale_factor`/`add_offset` unpacking. Nontrivial packing is rejected, not
silently decoded by Python while the Fortran driver consumes different values.

## Values and units

Required hourly input fields and broad inclusive bounds:

| Variable | Input unit | Range |
|---|---|---|
| TMPH | °C | −100 to 65 |
| WINDH | m/s | 0 to 100 |
| RAINH | mm/hour | 0 to 500 |
| DWPTH | kPa vapor pressure | 0 to 20 |
| SRADH | W/m² | 0 to 1500 |

Hourly `PATM` when present: 30–115 kPa; `XRADH`: 0–1000 W/m²;
`DRYDEPOC`: nonnegative, with no universal upper bound imposed.

Daily fields are `TMPX`, `TMPN` (same temperature bounds), `WIND`
(0–360000 m/hour), `RAIN` (0–12 m/day), `DWPT` (0–20 kPa), and `SRAD`
(0–130 MJ/m²/day). Unlike the hourly branch, the daily NetCDF branch does not
perform the m/s, mm/hour, or W/m² conversions. Do not assign hourly units to
these daily arrays merely because a source dataset uses them.

Required annual auxiliaries are `Z0G`, `ZNOONG`, `IFLGW`, `PHRG`, `CN4RIG`,
`CNORIG`, `CPORG`, `CALRG`, `CFERG`, `CCARG`, `CMGRG`, `CNARG`, `CKARG`,
`CSORG`, and `CCLRG`. Check finite/nonmissing values, nonnegative roughness and
concentrations, `ZNOONG` in 0–24 hours, `IFLGW` in 0–1, and `PHRG` in 0–14.
These broad screens do not assess regional chemistry plausibility.

For daily forcing, `DayMod.F90` interpolates using the previous day's maximum
temperature, the next day's minimum temperature, and both neighboring
vapor-pressure records. The checker includes those records even for a partial
daily run. January 1 and the last model day use the same boundary clamping
as the reader; non-leap forcing padding is mapped back to day 365.

Read `_FillValue` and `missing_value` explicitly, reject NaN/Inf, and also
recognize the EcoSIM large sentinel convention (`abs(value)>=1e20`). Failures
are counted on the selected distinct forcing records, not multiplied by the
number of spinup repetitions. Reports cap coordinate examples at eight per
variable/year/failure category; counts retain all failures.

No climate adjustment file, GHG timeseries, management file, grid, plant trait,
restart state, or downstream numerical stability is certified by this check.
