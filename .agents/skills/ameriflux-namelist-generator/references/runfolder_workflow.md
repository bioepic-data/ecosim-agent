# AmeriFlux EcoSIM Run Folder Workflow

Use this workflow whenever a user asks to create a workable EcoSIM run folder, runfolder, run directory, or runnable case for a given AmeriFlux site. Assume the EcoSIM executable is already built unless the user says otherwise.

## Goal

Produce a run directory that contains or can resolve:

- an EcoSIM executable or symlink if the user wants it placed in the folder
- a site-specific `.namelist`
- reachable grid, climate, plant-management, PFT parameter, and greenhouse-gas NetCDF inputs
- optional output/log subdirectories

## Canonical Skill Order

1. Use `ameriflux-site-info` to resolve site metadata under `result/<SITE_ID>/`.
2. Use `ameriflux-atmchem-info` or `unified-ameriflux-extractor` to derive NADP/tDEP chemistry when climate chemistry variables are needed.
3. Use `ameriflux-surgo-grid-extract` or `unified-ameriflux-extractor` to derive soil/grid profile values.
4. Use `ameriflux-era5-to-ecosim` or `unified-ameriflux-extractor` to create hourly climate forcing.
5. Use `ecosim-vegetation-code` to validate the EcoSIM PFT code from site vegetation and Koppen code.
6. Use `ecosim-natural-plant-mgmt` to create editable PFT-management Excel, JSON, and NetCDF files.
7. Use `ecosim-soil-mgmt` to create editable soil-management Excel, JSON, and NetCDF files when fertilizer, tillage, irrigation, or other soil management is active.
8. Use `ameriflux-namelist-generator` (`scripts/create_site_run_folder.py`) to assemble `result/<SITE_ID>/input/` and `result/<SITE_ID>/run_dir/` and write the namelist.

## Expected Artifacts

Prefer these paths:

```text
result/<SITE_ID>/<SITE_ID>_ecosim_site.json
result/<SITE_ID>/<SITE_ID>_ecosim_grid.nc
result/<SITE_ID>/<SITE_ID>_ecosim_grid.xlsx
result/<SITE_ID>/<SITE_ID>_ecosim_climate.nc
result/<SITE_ID>/<SITE_ID>_pft_mgmt.xlsx
result/<SITE_ID>/<SITE_ID>_pft_mgmt.json
result/<SITE_ID>/<SITE_ID>_pft_mgmt.nc
result/<SITE_ID>/<SITE_ID>_soil_mgmt.xlsx
result/<SITE_ID>/<SITE_ID>_soil_mgmt.json
result/<SITE_ID>/<SITE_ID>_soil_mgmt.nc
result/<SITE_ID>/<SITE_ID>_climate_derivation_report.json
result/<SITE_ID>/<SITE_ID>_grid_derivation_report.json
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
python-tools `result/` directory, one folder per site, with exactly two
subfolders. For US-UMB:

```text
result/US-UMB/
  input/      all site inputs and sidecars: *_ecosim_grid.nc, *_ecosim_climate.nc,
              *_pft_mgmt.nc (and *_soil_mgmt.nc when active), Excel/JSON sidecars,
              *_ecosim_site.json, derivation and quality reports, logs, plus copies
              of the PFT parameter, atmospheric GHG, and microbial parameter NetCDF
  run_dir/    US-UMB.namelist only; every input path is ../input/<file>
```

The extraction skills write their artifacts directly into `result/<SITE_ID>/`.
Assembling the run folder moves those files into `input/`, so nothing is left
loose in `result/<SITE_ID>/`. Copying the shared parameter files makes the
folder self-contained and pins the parameter versions used by the case.

Do not create `output/` or other files in `run_dir/`; EcoSIM writes its output
there when run. Link or copy an executable into `run_dir/` only when the user
asks or gives its path; otherwise report the executable assumption.

## Run Folder Creation

After the site inputs exist, assemble the folder and write the namelist with
the bundled script from the python-tools repository root:

```bash
python .agents/skills/ameriflux-namelist-generator/scripts/create_site_run_folder.py \
  --site-id <SITE_ID>
```

The script:

1. moves every file in `result/<SITE_ID>/` into `result/<SITE_ID>/input/`;
2. copies the PFT parameter, GHG, and microbial parameter files into `input/`
   (defaults: newest `ecosim_pftpar_*.nc`, `fatm_hist_GHGs_*.nc`, and
   `MicrobePars.*.nc` in the parent EcoSIM `input_data/`);
3. fails if `input/<SITE_ID>_ecosim_grid.nc`, `input/<SITE_ID>_ecosim_climate.nc`,
   or `input/<SITE_ID>_pft_mgmt.nc` is missing, and wires
   `input/<SITE_ID>_soil_mgmt.nc` when present;
4. writes `run_dir/<SITE_ID>.namelist` through `create_ameriflux_namelist.py`
   with paths relative to `run_dir/`;
5. fails if any namelist input path does not resolve to `../input/`, or if
   `run_dir/` holds anything besides the namelist.

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

## Validation Checklist

Before declaring the folder workable:

1. Confirm `result/<SITE_ID>/` holds only `input/` and `run_dir/`, and `run_dir/` holds only the namelist.
2. From `run_dir/`, resolve every namelist file path that is not `'NO'`; each must be `../input/<file>`.
3. Confirm `grid_file_in`, `pft_mgmt_in`, and `clm_hour_file_in` point to the intended site artifacts.
4. Confirm editable Excel sidecars exist for `grid_file_in`, `pft_mgmt_in`, and soil management when active, and that they were generated from the current NetCDF or reviewed before NetCDF conversion.
5. Confirm `forc_periods` uses climate forcing years that exist in the source ERA5 or climate NetCDF.
6. Confirm `delta_time=3600.` for hourly AmeriFlux/EcoSIM forcing.
7. Confirm `atm_ghg_in` spans the requested simulation period; historical GHGs through 2023 are not sufficient for future SSP runs.
8. Confirm `pft_mgmt_in` has active PFTs and reasonable planting density/depth units.
9. Confirm soil-management selectors and event tables are plausible for fertilizer, tillage, and irrigation schedules.
10. Confirm output frequency and `hist_fincl1` are appropriate for expected output volume.

## Scientific Cautions

- Do not set missing climate, grid, or plant-management inputs to `'NO'`; that can make the run physically meaningless.
- Treat gSSURGO/FAO fallback soil properties and NADP chemistry gap fills as assumptions; keep derivation reports with the run package.
- For natural vegetation, avoid mass-balance errors in management files: thinning without ecosystem export should use zero ecosystem-level harvested fractions.
- If forcing is recycled for spinup, document the repeated forcing window and cycle count.

## Completion Report

When finished, report:

- run folder path (`result/<SITE_ID>/`)
- namelist path (`result/<SITE_ID>/run_dir/<SITE_ID>.namelist`)
- shared parameter files copied into `input/` and their source paths
- input files referenced by the namelist
- grid, PFT-management, and soil-management Excel workbook paths
- forcing year range and `stop_n`
- any missing assumptions, fallback data, or validation steps that could not be completed
