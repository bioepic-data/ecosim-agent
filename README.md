# EcoSIM Python Tools

This repository contains Python utilities and Agent Skills for preparing EcoSIM inputs from AmeriFlux, ERA5, NADP/tDEP, gSSURGO, and FAO HWSD2 data.

## Main Entry Points

- Agent Skills: [`.agents/skills/*/SKILL.md`](.agents/skills)
- Climate forcing orchestration: [`Tools/create_ecosim_climate_forcing.py`](Tools/create_ecosim_climate_forcing.py)
- ERA5 to EcoSIM converter: [`.agents/skills/ameriflux-era5-to-ecosim/era5_to_ecosim_converter.py`](.agents/skills/ameriflux-era5-to-ecosim/era5_to_ecosim_converter.py)
- Notebook helper scripts: [`applications/notebooks/scripts/`](applications/notebooks/scripts)

The duplicate `.claude/skills` path points to `.agents/skills`, so skill updates made under `.agents/skills` are available through both paths automatically.

## Documentation

- Quick start: [`docs/QUICKSTART.md`](docs/QUICKSTART.md)
- Grid forcing workflow notes: [`docs/GRID_FORCING_README.md`](docs/GRID_FORCING_README.md)
- Grid forcing implementation notes: [`docs/GRID_FORCING_IMPLEMENTATION.md`](docs/GRID_FORCING_IMPLEMENTATION.md)

The grid forcing docs are supplemental workflow notes. The current checked-in orchestration script is `Tools/create_ecosim_climate_forcing.py`; if you are looking for a grid forcing entry point, verify that `Tools/create_ecosim_grid_forcing.py` exists in your branch before following those examples.

## Notebook Runfile Helpers

The old `README.txt` referenced runfile writers without paths. The available helper scripts are under `applications/notebooks/scripts/`:

- `ExampleInputWriter.py` writes example input/run configuration files.
- `PlantMgmtWriter.py`, `pftMgmtWriter.py`, and `MgmntUtil.py` support plant management inputs.
- `SoilMgmtWriter.py` and `soilManagementWriter.py` support soil management inputs.
- `SiteTopoWriter.py` writes site/topography inputs.
- `PlantTraitWriter.py` writes plant functional type parameterizations.

## ERA5 Conversion

Use the maintained skill converter instead of the removed root-level `convert_era5_to_ecosim.py`:

```bash
python .agents/skills/ameriflux-era5-to-ecosim/era5_to_ecosim_converter.py \
  --input data/AMF_US-Ha1_FLUXNET_FULLSET_1991-2020_3-5/AMF_US-Ha1_FLUXNET_ERA5_HR_1981-2021_3-5.csv \
  --output result/US-Ha1/US-Ha1_ecosim_climate.nc \
  --site-id US-Ha1 \
  --quality-report result/US-Ha1/US-Ha1_era5_quality_report.json
```

The written file is named by the forcing years it contains, e.g. `result/US-Ha1/US-Ha1_ecosim_climate_1981-2021.nc`.

## Soil Extraction

The `ameriflux-surgo-grid-extract` skill uses gSSURGO as the primary soil source and FAO HWSD v2.0 from `data/FAO_HWSD2` as fallback when gSSURGO is unavailable or has invalid/fill values. See [the skill instructions](.agents/skills/ameriflux-surgo-grid-extract/SKILL.md) for variable mappings and assumptions.

## Skill Validation

Validate skills against the Agent Skills standard with:

```bash
for skill in .agents/skills/*; do
  uvx --from 'git+https://github.com/agentskills/agentskills.git#subdirectory=skills-ref' skills-ref validate "$skill"
done
```
