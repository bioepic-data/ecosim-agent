# AmeriFlux EcoSIM Run Folder Workflow

Use this workflow whenever a user asks to create a workable EcoSIM run folder, runfolder, run directory, or runnable case for a given AmeriFlux site. Assume the EcoSIM executable is already built unless the user says otherwise.

## Goal

Before preparing driving data, follow
`../../ecosim-data-readiness/references/site_conflicts.md` to reconcile site
metadata, field records, source units and template assumptions. Assembly runs
`Tools/driving_data_conflicts.py`, writes the site conflict report, and stops on
errors. Warnings remain visible and do not establish full scientific readiness.

The shared [mandatory case preflight](case_preflight.md) is enforced at assembly
completion and before the two-year smoke test, one-year evaluation-output test,
and every full/continued launch. It checks the actual selected namelist and
inputs, with full production coverage checked before short runs. Resolve errors
and unsupported inputs before proceeding; retain scientific warnings.

Produce a run directory that contains or can resolve:

- an EcoSIM executable or symlink if the user wants it placed in the folder
- a site-specific `.namelist`
- reachable grid, climate, plant-management, PFT parameter, and greenhouse-gas NetCDF inputs
- a sourced `<SITE_ID>.html` site guide at the site-folder root
- optional output/log subdirectories

## Progress Tracking

Track every run-folder or site-run request with `ecosim-site-run-tracker`:
`init --site-id <SITE_ID> --request "<request>"` before step 1 below, `scan`
after each stage (inputs prepared, run folder created, simulation finished,
evaluation written), and `set` for results files cannot show (skipped soil
management at unmanaged sites, executable, test run, run completion). Report
stage status from `result/<SITE_ID>/PROGRESS.md` when finishing or when asked.

## Canonical Skill Order

1. Use `ameriflux-site-info` to resolve site metadata under `result/<SITE_ID>/`.
2. Use `ameriflux-atmchem-info` or `unified-ameriflux-extractor` to derive NADP/tDEP chemistry when climate chemistry variables are needed.
3. Use `ameriflux-surgo-grid-extract` or `unified-ameriflux-extractor` to derive soil/grid profile values.
4. Use `ameriflux-era5-to-ecosim` or `unified-ameriflux-extractor` to create hourly climate forcing.
5. Use `ecosim-vegetation-code` to validate the EcoSIM PFT code from site vegetation and Koppen code.
6. Use `ecosim-natural-plant-mgmt` to create editable PFT-management Excel, JSON, and NetCDF files.
7. Use `ecosim-soil-mgmt` to create editable soil-management Excel, JSON, and NetCDF files when fertilizer, tillage, irrigation, or other soil management is active.
8. Use `ameriflux-namelist-generator` (`scripts/create_site_run_folder.py`) to assemble `result/<SITE_ID>/input/` and `result/<SITE_ID>/run_dir/` and write the namelist.
9. Immediately run the 2-year smoke run and plant-trait evaluation (`scripts/create_site_smoke_run.py`; see Smoke Run and Plant-Trait Evaluation) before any full simulation.
10. Create or refresh the site HTML guide using the Site Explanation HTML step
    below. If simulation is blocked, still create the guide and record the
    blocker and checks actually performed.

## Expected Artifacts

Prefer these paths:

```text
result/<SITE_ID>/<SITE_ID>_ecosim_site.json
result/<SITE_ID>/<SITE_ID>_ecosim_grid.nc
result/<SITE_ID>/<SITE_ID>_ecosim_grid.xlsx
result/<SITE_ID>/<SITE_ID>_ecosim_climate_<first>-<last>.nc
result/<SITE_ID>/<SITE_ID>_pft_mgmt.xlsx
result/<SITE_ID>/<SITE_ID>_pft_mgmt.json
result/<SITE_ID>/<SITE_ID>_pft_mgmt.nc
result/<SITE_ID>/<SITE_ID>_soil_mgmt.xlsx
result/<SITE_ID>/<SITE_ID>_soil_mgmt.json
result/<SITE_ID>/<SITE_ID>_soil_mgmt.nc
result/<SITE_ID>/<SITE_ID>_climate_derivation_report.json
result/<SITE_ID>/<SITE_ID>_grid_derivation_report.json
result/<SITE_ID>/<SITE_ID>.html                 # Remains at the site root
```

Use existing artifacts if they are current and plausible. Do not regenerate large inputs unless requested or clearly required.

## Editable Excel Sidecars

Create Excel workbooks for every editable grid and management NetCDF so users can review values before running EcoSIM:

```bash
.venv-cmip6/bin/python .agents/skills/ameriflux-surgo-grid-extract/scripts/grid_netcdf_excel_bridge.py \
  nc-to-xlsx result/<SITE_ID>/<SITE_ID>_ecosim_grid.nc result/<SITE_ID>/<SITE_ID>_ecosim_grid.xlsx

.venv-cmip6/bin/python .agents/skills/ecosim-natural-plant-mgmt/scripts/plant_mgmt_excel_bridge.py \
  nc-to-xlsx result/<SITE_ID>/<SITE_ID>_pft_mgmt.nc result/<SITE_ID>/<SITE_ID>_pft_mgmt.xlsx

.venv-cmip6/bin/python .agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py \
  nc-to-xlsx result/<SITE_ID>/<SITE_ID>_soil_mgmt.nc result/<SITE_ID>/<SITE_ID>_soil_mgmt.xlsx
```

If the workflow starts from edited workbooks, convert the reviewed Excel files back to JSON and NetCDF with the same bridge scripts before wiring the namelist. Keep the workbook, JSON, and NetCDF together under `result/<SITE_ID>/`.

## User-Provided Data Checklist

Before declaring a run folder workable, make sure the user has downloaded or provided all source data that cannot be inferred from the namelist alone:

- Site forcing data: AmeriFlux/FLUXNET `BASE` or `FULLSET` meteorological files, NLDAS point forcing, or CDS ERA5 point files for the selected forcing route.
- Site metadata and management data: AmeriFlux BADM/BIF files, paper supplement tables, or explicit crop/treatment records for planting, harvest, fertilization, tillage, irrigation, grazing, or disturbance.
- Soil and chemistry data: `gSSURGO_CONUS.gdb` for US soils, NADP precipitation-chemistry rasters, EPA tDEP deposition rasters, or selected non-US chemistry products such as CAMS, MERRA-2, EBAS, or EANET.
- EcoSIM static assets: PFT parameter NetCDF, atmospheric GHG NetCDF, plant trait files, microbial parameter files if used, and any site-specific existing management NetCDF files.
- Observational target data needed for post-run checks, such as fluxes, biomass, LAI, yield, soil water, soil temperature, or SOC tables from AmeriFlux, FLUXNET, paper figures, or supplements.

## Run Folder Layout

Unless the user names another location, create the run folder under the
python-tools `result/` directory, one folder per site, with `input/` and
`run_dir/` as the core subfolders. Honor a user-specified destination such as
`work-space/<SITE_ID>/`. For US-UMB:

```text
result/US-UMB/
  US-UMB.html site explanation, sources, assumptions, and case status
  input/      all site inputs and sidecars: *_ecosim_grid.nc, *_ecosim_climate_<first>-<last>.nc,
              *_pft_mgmt.nc (and *_soil_mgmt.nc when active), Excel/JSON sidecars,
              *_ecosim_site.json, derivation and quality reports, logs, plus copies
              of the PFT parameter, atmospheric GHG, and microbial parameter NetCDF
  run_dir/    US-UMB.namelist and preflight report; inputs use ../input/<file>
```

The extraction skills write their artifacts directly into `result/<SITE_ID>/`.
Assembling the run folder moves input artifacts into `input/`. The site HTML
guide and tracker files remain at the site root. Copying the shared parameter files makes the
folder self-contained and pins the parameter versions used by the case.

Keep only configuration/preflight artifacts in `run_dir/` before launch; EcoSIM writes its output
there when run. Link or copy an executable into `run_dir/` only when the user
asks or gives its path; otherwise report the executable assumption.

## Multiple Sites

When the request covers several sites, prepare each site with this workflow in
its own `result/<SITE_ID>/` (with its own tracker, smoke run, and trait
check), then sort them into one project with the `ecosim-multisite-project`
skill: `result/projects/<PROJECT>/input/<SITE_ID>/` and
`result/projects/<PROJECT>/run_dir/<SITE_ID>/`, plus a `PROJECT.md` manifest.

## Run Folder Creation

Before assembly, apply the following management coverage and evaluation checks
when preparing annual schedules or merging cases.

### Management Coverage and Code Evaluation

Determine the simulation and forcing years from `start_date`, `forc_periods`,
and the consuming branch's year-selection settings. Enumerate required years in
both plant and soil management; first/last year ranges can hide gaps. Check that
`stop_n` agrees with the intended forcing-plan length, documenting any deliberate
override. Benchmark files may represent different years or model versions.

When merging sources, inspect the files actually selected by each namelist,
preserve originals, and retain per-year/event provenance and source hashes.
Resolve overlap using the user's source preference or documented evidence;
do not silently concatenate duplicate events. Validate legacy crop identifiers
against the active catalog and grid climate, and normalize legacy delimiters
without changing numerical values.

Carry schedules into missing years only when the user authorizes that policy.
Copy the preceding selected year's schedule, rebase planting/harvest and soil
event dates to each target year, and retain both the immediate donor and ultimate
source year. Mark synthetic years in provenance and the HTML. Handle leap-day
events explicitly; do not silently retain an invalid date or substitute a
different day. Leave intentional no-management selections distinct from missing
data. Do not adopt a site-specific carry-forward policy as a universal default.

Read the consuming EcoSIM code, not only metadata or examples:

- `PlantInfoMod.F90`: selected years, PFT catalog/climate mapping, topology,
  planting and management strings, and active/event counts.
- `ReadManagementMod.F90`: selectors, field counts, dates, units, event capacity,
  daily-slot overwrites, and same-day irrigation property constraints.
- `PlantDisturbsMod.F90`: intended plant removal versus ecosystem export.
  A grain flag with zero removal/export does not represent exported grain;
  document any adopted harvest efficiency instead of imposing 0.95 globally.
- `DayMod.F90`: tillage codes encode mixing intensity; check implement proxies
  rather than inferring machinery names from integer codes.
- `FertilizerMod.F90`: soil/band placement and elemental-mass conversions;
  include irrigation-borne nutrients in combined budgets without duplication.

Use the plant and soil management skills for detailed contracts. Recompute
water/solute and nutrient totals after merging and NetCDF readback; round-trip
editable workbooks when conversion is part of the change. Record approximations
such as daily blending of pulse chemistry and verify the conserved quantities.
Save the evaluated code revision or hashes and separate source-reviewed file
checks from compiled execution and scientific calibration. Report unavailable
executables and blocked smoke/trait checks accurately.

After the site inputs exist, assemble the folder and write the namelist with
the bundled script from the python-tools repository root:

```bash
python .agents/skills/ameriflux-namelist-generator/scripts/create_site_run_folder.py \
  --site-id <SITE_ID>
```

The script:

1. moves input artifacts in `result/<SITE_ID>/` into `result/<SITE_ID>/input/`,
   preserving `<SITE_ID>.html`, `progress.json`, and `PROGRESS.md` at the root;
2. copies the PFT parameter, GHG, and microbial parameter files into `input/`
   (defaults: newest `ecosim_pftpar_*.nc`, `fatm_hist_GHGs_*.nc`, and
   `MicrobePars.*.nc` in the parent EcoSIM `input_data/`);
3. finds the climate file `input/<SITE_ID>_ecosim_climate_<first>-<last>.nc` (renaming a legacy
   `<SITE_ID>_ecosim_climate.nc` by its `year` variable; several year-named files need
   `--climate-file`); fails if it, `input/<SITE_ID>_ecosim_grid.nc`,
   or `input/<SITE_ID>_pft_mgmt.nc` is missing, and wires
   `input/<SITE_ID>_soil_mgmt.nc` when present;
4. writes `run_dir/<SITE_ID>.namelist` through `create_ameriflux_namelist.py`
   with paths relative to `run_dir/`;
5. fails if any namelist input path does not resolve to `../input/`; other
   items in `run_dir/` (simulation output on a rerun) are reported, not fatal.

It is safe to rerun after editing inputs. Use explicit overrides when needed:
`--grid-file`, `--climate-file`, `--pft-mgmt-file`, `--soil-mgmt-file`,
`--pft-file`, `--atm-ghg-file`, `--micpar-file` or `--no-micpar`, `--case-name`,
and the chronology options `--force-start-year`, `--spinup-end-year`,
`--force-end-year`, `--spinup-cycles`, `--regular-cycles`, `--final-cycles`,
`--start-year`, `--stop-n`.

Default chronology: spinup repeats the first four forcing years 18 times,
`start_date` is the forcing start minus the spinup years, and `stop_n` equals
EcoSIM's `get_sim_len`, i.e. the sum of (end - start + 1) x cycles over the
`forc_periods` triples. For US-UMB (ERA5 1981-2021) that is start 1909 and
`stop_n = 72 + 41 = 113`.

## Site Explanation HTML

For each run-folder request, author a self-contained `<SITE_ID>.html` in the
site-folder root, alongside `input/`, `run_dir/`, and `PROGRESS.md`. For example,
`work-space/US-Ne1/US-Ne1.html` when that is the requested destination. The
assembly helper preserves this file on reruns; writing the guide is an agent
step after assembly, not an automatic output of the script.

Use the actual site's metadata, site-filtered BADM/BIF records, source reports,
and run configuration. Cover the following where evidence is available:

- Site identity, location, ownership/research setting, coordinates, elevation,
  climate averages and their available reference periods, vegetation, and soils.
- Management history and site-specific planting, harvest, fertilization,
  irrigation, tillage, or disturbance records relevant to the case.
- Why the site is scientifically useful, clearly distinguishing research
  questions from observed or simulated findings.
- The local simulation period, spinup/cycling choices, input sources, management
  interpretation, assumptions, and unresolved data gaps.
- Checks actually performed and the current smoke-run, trait-evaluation, and
  simulation status. Record blockers without implying that the model ran.
- Source attribution and links to the local namelist, provenance/quality reports,
  editable workbooks, and progress tracker using paths relative to the HTML.

Separate field measurements from map products, literature priors, and modeling
choices. Date historical descriptions and the page's status snapshot; do not
infer current management from an older record. Identify units and distinguish
calendar-year fertilizer totals from crop-season budgets. Describe missing
information as unavailable rather than supplying unsupported values.

Use visibly red titles for warning/conflict sections and their warning/conflict
subheadings, including expandable section summaries. Follow the CSS and severity
label guidance in
`../../ecosim-data-readiness/references/site_conflicts.md` under "HTML warning
and conflict titles" when creating or refreshing the guide.

### Document the selected grid

Read `grid_file_in` from the current namelist and inspect that NetCDF. Include
a dedicated grid section, even when no benchmark comparison was requested:

- Identify the selected file, grid dimensions, layer selectors `NUI/NJ/NL1/NL2`,
  supplied layer-bottom depths and runtime-derived extent. Distinguish allocated
  slots, selected supplied layers, derived layers, source horizons and any deep
  extrapolation. Do not present unused slots as active soil.
- Show a depth-resolved table of bulk density, texture, FC/WP, conductivity,
  pH, CEC and organic C with units. Explain fine-earth/whole-soil water basis.
  Include a compact expandable inventory or linked local snapshot for the other
  selected grid variables, including nutrient/mineral pools and Gapon values.
- Describe surface litter, water/ice codes, FC/WP pressures, albedo, slope/aspect,
  water-table flags/depths and directional runoff/subsurface boundaries. Interpret
  special codes through the current reader; distinguish unknown/estimated values
  from zero and from unused/fill values.
- Label mapped/derived properties, geometry from a template, gap fills, model
  estimates and unverified defaults. Link the source profile, derivation report,
  grid workbook and current NetCDF; retain date/depth support and source limits.
- Summarize the latest site/grid conflict assessment with failed checks, missing
  evidence and unresolved priors. Link the report and tie its file hash to the
  selected grid. Earlier structural passes must not imply current readiness.
  Explain contradictions such as slope versus field terrain or FC pressure
  versus source water-retention data without silently correcting the evidence.
  For each root issue, show actual versus expected values, the conflict type and
  violated relationship, physical consequence, and a concrete resolution. Follow
  `../../ecosim-data-readiness/references/site_conflicts.md` under "Explain the
  findings"; keep conditional assumptions distinct from confirmed contradictions.
  Apply its "Reproducible numerical explanations" contract: include numerical
  substitutions, affected-layer tables and useful stock totals, with structured
  evidence and a read-only reproducer for multi-step calculations. A summary
  such as "15% less soil mass" must link to the complete calculation.

Include the density/macropore/porosity cross-check from
`../../ameriflux-surgo-grid-extract/references/soil_volume_contract.md`:
distinguish measured source bulk density from matrix BKDSI and computed
particle/matrix/total porosities, document the initialized soil mass and FC/WP
volume bases, and show the soil-volume audit result. Do not describe a
calculated porosity as an independent measurement or use a density-only
correction without rechecking water capacity and cold-start allocation.

When inputs or configuration change, refresh affected tables, grid hashes,
conflict status and chronology from files on disk. Keep historical benchmark
comparisons labeled as snapshots. A documentation update does not authorize
changing input values to make a conflict disappear.

For field/BIF texture or organic-carbon overrides, also follow
[`field_soil_evidence.md`](../../ameriflux-surgo-grid-extract/references/field_soil_evidence.md).
Show the source record and missing metadata, the intended observation interval
versus full changed layer support, and achieved stock where applicable. Refresh
dependent porosity/water calculations as well as concentration tables. Preserve
prior run results as historical and record that fresh initialization/spin-up
verification is pending; earlier outputs do not validate the revised grid.

Use inline CSS, semantic headings, readable tables, and a responsive layout so
the page works offline without remote scripts or fonts. Add simple explanatory
graphics where useful, labeling schematics and derived quantities. Verify local
links and section anchors, render and inspect desktop/mobile layouts, and
refresh case status after validation or a requested simulation changes it.
Preserve existing user-authored content when updating a guide.

## Smoke Run and Plant-Trait Evaluation

For a case intended for model-data comparison, also follow
[`ecosim-evaluation-output-check`](../../ecosim-evaluation-output-check/SKILL.md).
Inventory the actual observations, request the matching registered fields and
support variables, and verify them with a separate one-year run before committing
to a full simulation. Carry the validated history selections into the production
namelist without changing its chronology. Report output readiness separately
from the two-year smoke/trait verdict and from scientific evaluation; do not
silently omit observed targets that still lack a compatible model output.

After the run folder is created, always run a 2-year smoke run and evaluate the
plant traits it writes, before starting the full simulation:

```bash
python .agents/skills/ameriflux-namelist-generator/scripts/create_site_smoke_run.py --site-id <SITE_ID>
```

The script:

1. writes `result/<SITE_ID>/smoke_run/<SITE_ID>.namelist` from the run namelist with
   `stop_option='nyears'` and `stop_n=2` (same `start_date`; `smoke_run/` sits beside
   `run_dir/`, so `../input/` paths resolve and `run_dir/` stays clean);
2. runs the newest EcoSIM build (`--exe` or `$ECOSIM_EXE` to choose), warning if
   Fortran sources are newer than the executable, under a CPU-time cap;
3. passes the run only if EcoSIM exits 0, reaches every hourly step of the two
   calendar years (leap days counted), and writes history without NaN/Inf
   (`ecosim-smoke-run` `check_history.py`);
4. evaluates the `<case>.plant_trait.desc.<start_year>` the run wrote with the
   `ecosim-plant-trait-sanity-check` checker in strict physiology mode, saving
   `smoke_run/<SITE_ID>_plant_trait_check.{json,md}`;
5. writes `smoke_run/smoke_summary.json` and refreshes the progress tracker
   (`smoke_test` and `plant_traits` steps).

The deterministic check is only the first layer. Then follow the
`ecosim-plant-trait-sanity-check` skill for the site's species: gather web
evidence, save it as `input/<SITE_ID>_plant_trait_web_evidence.json` (or in
`smoke_run/`), and re-evaluate without rerunning the model:

```bash
python .agents/skills/ameriflux-namelist-generator/scripts/create_site_smoke_run.py --site-id <SITE_ID> --trait-only
```

The `.desc` file is read-only; corrections go to the PFT parameter NetCDF.
A failed smoke run blocks the full run.

### Correcting severe trait findings

Unless `--no-fix-traits` is given, the script then corrects severe findings and
repeats the smoke run and trait check until none remain. The loop stops when
two consecutive iterations change no parameter value (nothing correctable is
left, e.g. only findings without literature evidence remain; an unchanged
iteration re-plans without rerunning the model) or after `--max-rounds`
(default 4). Severe findings are every `ERROR`, every value outside a
web-evidence range, and every wrong categorical code; the
model-formulation-sensitive Jmax:Vcmax diagnostic is reported, not corrected.

Every corrected value must be backed by literature that follows the site's
vegetation. Before the correcting run, extract the site's species composition
and trait measurements from the AmeriFlux BADM/BIF and turn the measurements
into site-level evidence:

```bash
S=.agents/skills/ecosim-plant-trait-sanity-check/scripts/site_vegetation.py
python $S extract --site-id <SITE_ID> [--bif data/AMF_AA-Flx_BIF_*.xlsx]
python $S site-evidence --site-id <SITE_ID> --pft <PFT>
```

Then search the literature for the site's dominant species (priority: site
measurements > species > genus > functional type) following
`ecosim-plant-trait-sanity-check`, combine per-species values by canopy share
(`python $S weight --site-id <SITE_ID>`), check the file
(`python $S lint --site-id <SITE_ID>`), and record sourced ranges (source, URL,
evidence level, unit conversion) in `input/<SITE_ID>_plant_trait_web_evidence.json`:
`numeric_ranges` for trait variables, and `derived_ranges` for multi-parameter
constraints such as `RUBISCO_SPECIFICITY` (C3 Sc/o at 25 oC). Rules
(`ecosim-plant-trait-sanity-check/scripts/trait_corrections.py`):

- `SLA1`: 0.25 x the typical value (midpoint of the literature range); treated
  as resolved once it equals that target.
- Other numeric parameters: the midpoint (medium value) of the literature range.
- Rubisco kinetics: keep `VCMX`, `XKCO2`, `XKO2`; set `VOMX` so the implied
  specificity is the midpoint of the literature `RUBISCO_SPECIFICITY` range.
- Categorical identity (`IEBTYP`, `ISNTYP`, ...): the EcoSIM enum code from
  `ElmIDMod.F90`, as the checker reports it.
- A severe finding with no literature range is left **unresolved** and reported;
  never substitute a checker screening range or an unsourced value.
- Exception for `CHL` in C3 trees: when no usable literature value is found,
  the correction loop sets `CHL = 0.12` as a recorded user default (evidence level
  `user-default`; see `ecosim-plant-trait-sanity-check` `references/sanity_rules.md`)
  in the site PFT copy and logs the resulting Jmax:Vcmax change; report it.

Corrections are applied with `ParEditor` (`ecosim-pftpar-editor`) to a site copy,
`input/<SITE_ID>_ecosim_pftpar.nc`, which both namelists then use; the shared
`input_data/` file and other sites are untouched, and rebuilding the run folder
keeps using the site copy. Each round's corrections (old, new, read-back, rule,
citation) and unresolved findings are appended to
`input/<SITE_ID>_plant_trait_corrections.json`; ParEditor's change log is
`input/<SITE_ID>_ecosim_pftpar.jsonl`. Report every correction with its source
and every unresolved finding. A full simulation made before the corrections
does not reflect them and must be rerun.

Report the remaining trait findings and corrections before starting the full run.

Trait review must include every model-generated description from the smoke
years. Rotating crops can change PFT between years; checking only the first
`.desc` file misses later crops. The smoke helper aggregates all year reports
for correction and readiness; retain individual crop evidence.

### Verify completion and year-transition compatibility

Before the smoke run, check raw management selector padding using
`../../ecosim-soil-mgmt/references/soil_mgmt_contract.md` under "Fixed-width
string compatibility". The two-year run must pass the annual management reload,
not merely initialize or write one year's output. Validate every selected
management year offline as well; two simulated years do not exercise an entire
recycled forcing plan.

Do not treat process exit 0 or finite partial history as completion. EcoSIM can
emit `ENDRUN:fail ...` and still return 0, as the NUL-padded NO failure did.
Require the expected calendar/hourly step count, appropriate end restart/date,
clean history scan and absence of a fatal termination in the log. Keep runtime
completion separate from physiological screening and scientific validation.
If the model stops early, report the last model date, actual versus expected
steps and the fatal message even when the shell exit status is zero.

Archive failed outputs and record the defect and input hashes before retrying.
After a padding-only repair, rerun the smoke test across the failed transition
before advancing to the full simulation. Parameter changes require a fresh
initialization; a restart generated with old traits does not validate new ones.
Retain user-selected sensitivity settings during a compatibility repair rather
than automatically tuning unrelated parameters.

## Full Simulation

Only when the user asks, run the full simulation with:

```bash
nohup python .agents/skills/ameriflux-namelist-generator/scripts/run_site_full.py --site-id <SITE_ID> > /dev/null 2>&1 &
```

The run passes when EcoSIM exits 0, the last restart reports every hourly step
of the planned years, and history has no NaN/Inf. **If it fails partway, try a
continued run first:** the script sets `continue_run=.true.` in
`run_dir/<SITE_ID>.namelist` and runs it again. EcoSIM reads the restart named
in `run_dir/rpointer.esim`, walks `forc_periods` from the original start
skipping years before the restart date (`drivers/ecosim/ecosim.F90`), and
stops at the end of the plan, so `stop_n` stays unchanged. Because the elapsed
step count is not restored, the continued run is accepted when it exits 0,
reports either the total or the post-restart step count, and writes the
restart for the planned end date. **If the continued run also fails, or there
is no yearly restart to continue from, report the error** (both log tails and
the summary) instead of retrying further. Logs are `run_dir/<SITE_ID>.run.log`
and `<SITE_ID>.continue.log`; the outcome is `run_dir/full_run_summary.json`,
which the progress tracker reads. A fresh run resets `continue_run` to `.false.`.

## Validation Checklist

Before declaring the folder workable:

1. Confirm the site root holds `input/`, `run_dir/`, `<SITE_ID>.html`, the
   progress-tracker files (`progress.json`, `PROGRESS.md`), and any `smoke_run/`
   or `evaluation/` products; a new `run_dir/` holds only the namelist.
2. Confirm the 2-year smoke run passed and the plant-trait findings were reported (`smoke_run/smoke_summary.json`).
2. From `run_dir/`, resolve every namelist file path that is not `'NO'`; each must be `../input/<file>`.
3. Confirm `grid_file_in`, `pft_mgmt_in`, and `clm_hour_file_in` point to the intended site artifacts.
4. Confirm editable Excel sidecars exist for `grid_file_in`, `pft_mgmt_in`, and soil management when active, and that they were generated from the current NetCDF or reviewed before NetCDF conversion.
5. Confirm `forc_periods` uses climate forcing years that exist in the source ERA5 or climate NetCDF.
6. Confirm `delta_time=3600.` for hourly AmeriFlux/EcoSIM forcing.
7. Confirm `atm_ghg_in` spans the requested simulation period; historical GHGs through 2023 are not sufficient for future SSP runs.
8. Confirm `pft_mgmt_in` has active PFTs and reasonable planting density/depth units.
9. Confirm soil-management selectors and event tables are plausible for fertilizer, tillage, and irrigation schedules.
10. Confirm output frequency and `hist_fincl1` are appropriate for expected output volume.
11. Confirm the HTML site guide exists at the site root, its local links resolve,
    and its evidence, assumptions, and validation status match the case files.

## Scientific Cautions

- Do not set missing climate, grid, or plant-management inputs to `'NO'`; that can make the run physically meaningless.
- Treat gSSURGO/FAO fallback soil properties and NADP chemistry gap fills as assumptions; keep derivation reports with the run package.
- For natural vegetation, avoid mass-balance errors in management files: thinning without ecosystem export should use zero ecosystem-level harvested fractions.
- If forcing is recycled for spinup, document the repeated forcing window and cycle count.

## Completion Report

When finished, report:

- run folder path (`result/<SITE_ID>/`) and the tracker summary (`result/<SITE_ID>/PROGRESS.md`)
- namelist path (`result/<SITE_ID>/run_dir/<SITE_ID>.namelist`)
- HTML site-guide path (`<site-folder>/<SITE_ID>.html`)
- shared parameter files copied into `input/` and their source paths
- smoke-run result (exit, steps, history scan, wall time) and plant-trait findings with the report path
- input files referenced by the namelist
- grid, PFT-management, and soil-management Excel workbook paths
- forcing year range and `stop_n`
- any missing assumptions, fallback data, or validation steps that could not be completed
