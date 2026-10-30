---
name: ameriflux-namelist-generator
description: Create EcoSIM Fortran namelists, run folders, and sourced HTML site guides from site-specific grid, plant management, climate forcing, and greenhouse-gas inputs. Use when preparing or updating an EcoSIM namelist, run folder, run directory, or runnable site case for AmeriFlux or FLUXNET sites.
---

# AmeriFlux Namelist Generator

## Overview

Use this skill to create EcoSIM run namelists and executable-ready run folders for AmeriFlux sites. The namelist follows the Blodgett `Blodget.ndlf.nolig.namelist` pattern: one `regression_test` block, one `ecosim` block with site-specific forcing paths and output controls, an empty `bbgcforc` block, and one `ecosim_time` block.

Read [references/namelist_contract.md](references/namelist_contract.md) when you need the field contract, forcing-period rules, path conventions, or validation checklist.

For any request to create a run folder, runfolder, run directory, or runnable site case, read and follow [references/runfolder_workflow.md](references/runfolder_workflow.md) before writing files.

Include a self-contained `<SITE_ID>.html` site guide at the site-folder root
(alongside `input/` and `run_dir/`), using the user's requested destination.
Explain the site setting, climate, soils, vegetation, management history, and
local case using cited field records and metadata. Distinguish observations
from model assumptions and report the actual validation and simulation status.
Follow the site-guide step in the run-folder workflow; the assembly script
preserves an existing guide but does not author its content.

Document the actual grid selected by the namelist in the guide, including
layer geometry, soil properties with units, initialization and hydrologic
boundaries, parameter sources/defaults, and the latest conflict-check result.
Use the grid-documentation checklist in the site-guide workflow; a general
soil description or benchmark comparison alone does not document the active grid.

When asked to create a run folder for a site, build it under `result/<SITE_ID>/` with two subfolders: `input/` for every input file (site NetCDF, sidecars, reports, and copies of the PFT, GHG, and microbial parameter files) and `run_dir/` for the namelist alone. Use `scripts/create_site_run_folder.py --site-id <SITE_ID>` after the site inputs exist, then always run `scripts/create_site_smoke_run.py --site-id <SITE_ID>` for a 2-year smoke run in `result/<SITE_ID>/smoke_run/` followed by the plant-trait evaluation of the `.desc` file it writes; severe trait findings are corrected from literature-backed ranges in a site PFT parameter copy and the smoke run is repeated until they are fixed (see the run-folder workflow). Track the request with the `ecosim-site-run-tracker` skill.

## Workflow

1. Identify the AmeriFlux `site_id` such as `US-Ha1`, `US-MMS`, or `US-Var`.
2. Confirm that site artifacts exist. Prefer:
   - `result/<SITE_ID>/<SITE_ID>_ecosim_grid.nc`
   - `result/<SITE_ID>/<SITE_ID>_ecosim_climate_<first>-<last>.nc` (climate files carry their forcing years)
   - `result/<SITE_ID>/<SITE_ID>_pft_mgmt.nc`
   - `result/<SITE_ID>/<SITE_ID>_ecosim_site.json`
   Then search `result/`, `inputs/`, `data/`, and the repository root.
3. If climate, grid, soil, or plant-management files are missing, use the relevant AmeriFlux skills first: `ameriflux-era5-to-ecosim`, `ameriflux-surgo-grid-extract`, `ecosim-natural-plant-mgmt`, or `unified-ameriflux-extractor`.
4. Generate the namelist with the bundled script:

```bash
python .agents/skills/ameriflux-namelist-generator/scripts/create_ameriflux_namelist.py \
  --site-id US-Var \
  --output result/US-Var/US-Var.namelist \
  --pft-file /Users/jinyuntang/work/github/ecosim_workspace/croots/input_data/ecosim_pftpar_20260520.nc \
  --atm-ghg-file /Users/jinyuntang/work/github/ecosim_workspace/croots/input_data/fatm_hist_GHGs_1750-2023.nc
```

5. If writing the namelist into an EcoSIM run directory, pass `--run-dir /path/to/run_dir` so input paths are written relative to the run directory, matching the Blodgett example.
6. Review `start_date`, `forc_periods`, and `stop_n`. The script can infer forcing years from `year` in the climate NetCDF, but simulation chronology is a modeling decision.
7. Validate that referenced files exist from the namelist execution directory.
8. For a run-folder request, create or refresh the site-root HTML guide after
   assembly and update its status after the smoke run, including when blocked.

When merging management sources or changing chronology, follow the run-folder
workflow's Management Coverage and Code Evaluation step. Cover every selected
year, retain provenance, and check the actual reader/process semantics before
calling the inputs ready. Carry-forward is an explicitly authorized gap policy,
not an automatic substitute for missing observations.

## Common Overrides

- Use `--case-name` for a model case label different from the site ID.
- Use `--grid-file`, `--pft-mgmt-file`, and `--climate-file` when auto-discovery chooses the wrong artifact.
- Use `--force-start-year`, `--spinup-end-year`, `--force-end-year`, `--spinup-cycles`, `--start-year`, and `--stop-n` to control chronology.
- Use `--lignification true` for lignified root experiments; default is `.false.`.
- Use `--micpar-file` to include a `micpar_file_in` line.

## Cautions

- Do not set missing climate, grid, or plant-management files to `NO`; that can make EcoSIM run with physically meaningless forcing or no active plant management.
- Keep `delta_time=3600.` when using hourly AmeriFlux/EcoSIM climate forcing.
- Ensure the greenhouse-gas forcing spans the requested simulation period. Historical-only `fatm_hist_GHGs_1750-2023.nc` is not sufficient for future SSP runs.
- If `forc_periods` repeats a short forcing window for spinup, make that intentional and document it in the run notes.

## Validation

Run:

```bash
python .agents/skills/ameriflux-namelist-generator/scripts/create_ameriflux_namelist.py --site-id US-Var --dry-run
```

Then inspect the output and verify:

- `case_name` matches the site/case.
- `grid_file_in`, `pft_mgmt_in`, and `clm_hour_file_in` point to the intended NetCDF files.
- `forc_periods` uses valid years available in the climate forcing.
- `hist_fincl1`, `hist_mfilt`, and `hist_nhtfrq` match the intended output diagnostics.
