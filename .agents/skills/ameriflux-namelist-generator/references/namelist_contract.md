# AmeriFlux EcoSIM Namelist Contract

This reference captures the namelist pattern derived from:

`/Users/jinyuntang/work/github/ecosim_workspace/croots/examples/run_dir/blodgett/Blodget.ndlf.nolig.namelist`

## Required Blocks

- `&regression_test`: keep `cells = 3` unless a regression workflow asks otherwise.
- `&ecosim`: core model controls, forcing input files, solver cycles, and history output variables.
- `&bbgcforc`: keep present even when empty.
- `&ecosim_time`: restart, timestep, stop, and diagnostic controls.

## Site-Specific Fields

- `case_name`: concise site or experiment label.
- `pft_file_in`: EcoSIM PFT parameter NetCDF. Use the croots `input_data/ecosim_pftpar_20260520.nc` default only when it matches the experiment.
- `grid_file_in`: site grid NetCDF, usually `<SITE_ID>_ecosim_grid.nc`.
- `pft_mgmt_in`: plant management NetCDF, usually `<SITE_ID>_pft_mgmt.nc`.
- `clm_hour_file_in`: hourly climate forcing NetCDF named by its forcing years, `<SITE_ID>_ecosim_climate_<first>-<last>.nc`.
- `atm_ghg_in`: greenhouse-gas concentration file.
- `soil_mgmt_in` and `clm_factor_in`: normally `'NO'` for AmeriFlux natural-site runs unless a site-specific file exists.

## Chronology

The Blodgett example uses:

```text
start_date = '19410101000000'
forc_periods = 2012,2015,18,2012,2022,1,2012,2022,0
stop_n = 82
stop_option = 'nyears'
```

Interpretation:

- `forc_periods` is nine integers: spinup forcing start/end/cycles, regular forcing start/end/cycles, and final forcing start/end/cycles.
- A zero cycle in the final triple can encode no additional final period.
- `stop_n` overrides the run length implied by `forc_periods`.
- EcoSIM's `get_sim_len` (`EcoSIMCtrlMod.F90`) computes that length as the sum of (end - start + 1) x cycles over the triples; the generator uses it as the default `stop_n` and starts the run that many spinup years before the forcing start. The Blodgett `stop_n = 82` is a manual choice one year short of its 83-year forcing plan.
- `start_date` is the model simulation date, not necessarily the first climate forcing year.

For ecosystems other than grasslands/croplands, use the climate NetCDF year range as the default forcing window. If `netCDF4` or a `year` variable is unavailable, infer the forcing range from AmeriFlux ERA5 filenames under `data/` or `result/`. Still choose `start_date` and `stop_n` deliberately for the scientific question.

### Grassland and cropland default

For grassland (`GRA`) and cropland (`CRO`) case creation, let `A` be the first
selected complete forcing year and `O` the last year with available evaluation
observations. Repeat forcing `A..A+4` four times, then use `A..O` once:

```text
start_year = A - 20
forc_periods = A,A+4,4,A,O,1,A,O,0
stop_n = 20 + (O-A+1)
last_simulated_year = start_year + stop_n - 1 = O
```

The transient starts at `A`, not `A+5` or the first observation year. The
20 spinup years are additional model years before `A`; the first five forcing
years are used four times during spinup and once again in the transient.
Set `start_date` to January 1 of `start_year` and `stop_option='nyears'`.
This defines a duration, not evidence of equilibrium.

Determine `O` from actual usable target-variable records and retain the source,
last valid timestamp, QC/missing-value criteria, and partial-year coverage in
the HTML guide. Use the latest year with usable observations among the selected
evaluation targets; report each target's shorter coverage separately. A padded
or gap-filled file ending is not evidence of measured coverage. Include a
partial final observation year in the simulation but compare only valid pairs.
Pass the verified year with `--observation-end-year`; never silently infer it
from the forcing filename or shorten it because inputs are missing.

Require five complete spinup forcing years and climate, management/deposition,
GHG and other active inputs for their applicable model/forcing calendars.
If coverage ends before `O`, extend the inputs or report the gap. Do not repeat
a shorter window and call it a 20-year spinup, invent management records, or
extend the transient to a newer forcing-only year. Run mandatory preflight
on the resulting namelist; check crop rotation and event replay in the repeated
window against the actual management reader. Keep recycled forcing years and
model calendar years distinct when indexing management and GHG data.

Record the first five years, four cycles, model start/end, transient range,
20-year spinup and total years in the HTML guide and progress notes. Include
any representativeness warning (for example an unusually wet first-five-year
window); this schedule does not remove forcing-bias checks. Short smoke and
output-check namelists remain isolated tests and need not run the full schedule.

## Path Conventions

- If the namelist lives in an EcoSIM run directory, write input paths relative to that directory.
- If the namelist lives under `result/<SITE_ID>/`, absolute paths are safer unless the user requests relative paths.
- Verify paths from the directory where EcoSIM will be launched, not from the Python-tools repository.

## Default Output Variables

The generator defaults to the Blodgett root-carbon diagnostics, including canopy C fluxes, root biomass and sink weights, soil temperature/water variables, plant physiological variables, microbial pore-flow variables, and root hydraulic state variables.

Trim `hist_fincl1` for long production runs if output volume is too high.

## Validation Checklist

- Every quoted file path either exists or is intentionally `'NO'`.
- `delta_time` is `3600.` for hourly forcing.
- `hist_nhtfrq=-24` for daily history output unless a different output cadence is requested.
- `hist_mfilt` is large enough for the number of output records per file.
- `NPXS`, `NPYS`, `NCYC_LITR`, and `NCYC_SNOW` match the numerical stability needs of the site hydrology and redox chemistry.
