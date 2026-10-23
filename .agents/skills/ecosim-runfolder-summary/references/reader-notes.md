# Reader notes for configuration summaries

These mappings were checked against the root EcoSIM tree while developing the
SOAP example. Locate the active implementation in the selected driver's CMake
source lists before interpreting another branch or executable. In this tree,
`f90src/IOutils/CMakeLists.txt` includes `readimod.F90` and `PlantInfoMod.F90`.
The helper inventories configuration; it does not emulate all initialization.
For hydrology controls, boundary coefficients, surface routing and initial
water-table derivation, read [hydrology.md](hydrology.md).

## Grid and soil

- `f90src/IOutils/readimod.F90`: `DHI` is W–E column width and `DVI` is N–S
  row width in meters. `NH1:NH2` bounds columns; `NV1:NV2` bounds rows. Some
  management-file long names swap these meanings. Use equations and loops.
- `topo_grid` is a one-based grid ID, used for the site's `IETYPG` climate code.
  `ASPX` is clockwise degrees from north (180° is south); `SL0` is degrees,
  not percent slope. `NHW/NHE/NVN/NVS` identify the landscape bounds.
- `NUI` is the surface-layer index; `NJ` the maximum rooting-layer index.
  `NM=NJ+NL1` is the last supplied layer; `NJ+NL1+NL2` is the configured
  active bottom, subject to the compiled layer limit. `SetDeepSoil` in
  `f90src/HydroTherm/PhysData/SoilPhysParaMod.F90` initializes extra layers.
  The helper reports supplied profiles and preserves all stored arrays in JSON.
  Unused stored depths can be zero, sentinel-valued, or nonmonotonic; do not
  report those as active-profile defects. Do not treat the stored NL2 depth as
  the final initialized bottom.
- `FC`, `WP`, conductivity, organic N/P and CEC can use negative values to
  request estimation. `CORGC` is kg C/Mg soil, while `CORGN/CORGP` are g N/P
  per Mg soil in SOAP. `CSAND/CSILT` are kg/Mg, not fractions or percent.
- `f90src/HydroTherm/SoilPhys/SoilHydroParaMod.F90` interprets `THW>1` as
  saturated, `THW=1` as field capacity, `THW=0` as wilting point, negative as
  dry, and interior values as volumetric water. `THI` has analogous special
  values capped by pore space remaining after water; zero need not mean zero
  initialized ice. Unspecified hydraulic limits below the water table can
  force saturation. Report raw settings and this dependency, not invented final
  volumetric contents.

## Vegetation and management

`f90src/IOutils/PlantInfoMod.F90` controls selection:

| pft_dflag | Selection |
| --- | --- |
| -1 | Plants disabled; other plant arrays need not exist. |
| 0 | Record 1 only; `year` variable is not required. |
| 1 | Match forcing year for PFTs and planting/management. |
| Other nonzero | In this version, species selection uses forcing year, while planting/management uses model year. Inspect both readers. |

Only slots `1:NZ` matter. Nonpositive rectangle bounds stop the unit loop.
`first_topou` limits reading to the first topographic unit; it does not prove
that the landscape consists of only one cell. Runtime selectors and process
switches can further restrict execution; the helper reports records enabled
by file selection, not realized plant populations.

If `IETYPG>0`, the effective parameter key is the first four characters of
`pft_type` plus the grid's two-digit Koppen code. Check each covered grid unit
and verify existence in the **selected** parameter NetCDF `pfts` catalog;
`pfts_short/pfts_long` supply names. Negative Koppen cases need reader-specific
inspection; do not treat them as verified keys.

Planting accepts `DDMMYYYY population depth [maximum_population]`. Read the
optional fourth field rather than dropping it. Population is per m² and depth
is m. SOAP's `9999` date suffix is not a request to wait until calendar year
9999: the current planting routine applies the day/month in the current year
for eligible initialization/establishment. Preserve the raw date and trace
selection when interpreting other cases. Moss/lichen use nonvascular pathways;
planting pools do not specify mature biomass.

Management rows have 13 fields:

```text
DDMMYYYY ICUT JCUT HCUT PCUT ECUT11 ECUT12 ECUT13 ECUT14 ECUT21 ECUT22 ECUT23 ECUT24
```

`PCUT` is the thinning fraction. `ECUT11–14` apply to fine leaf, fine nonleaf,
stalk, and standing dead components at PFT level; `ECUT21–24` are ecosystem
export fractions conditional on removal. Litter retention is proportional to
`pool × PFT_removal × (1 − ecosystem_export)` with disturbance-specific
modifiers. See `PlantDisturbsMod.F90` and fire routines for actual transfers.
Do not sum event fractions and label the result exact annual mortality;
12 sequential equal events compound as `1 − (1 − fraction)^12` if applicable.

Read harvest and termination enums from `f90src/Modelconfig/ElmIDMod.F90`:
`ICUT=0..6` currently maps to none, grain, aboveground, pruning, grazing,
fire, herbivory; `JCUT=0..2` maps to no action, terminate, terminate/reseed.
`ICUT=0` can accompany nonzero thinning. Grazing/herbivory can pair events to
define intervals; do not interpret all entries as isolated daily cuts.

The current `ParsePlantEventDate` computes day of year using the **model year**,
including raw event dates with suffix `0000`. Older skill references describing
year-zero leap conversion are stale for this implementation. Date behavior
must be checked in the selected source version.

## Forcing and time

`drivers/ecosim/ecosim.F90` iterates `forc_periods` as inclusive triplets
`first,last,repeats`, including reversed year ranges. A zero-repeat triplet
adds no simulation years. `start_date` belongs to the model calendar and need
not match the forcing year. `EcoSIMCtrlMod.F90:get_sim_len` computes the total
scheduled duration. In this tree, `ecosim_time_mod.F90:update_sim_len` sets a
`nyears` stop count to `min(schedule_length, stop_n)`; a namelist comment
saying stop_n overrides the schedule is insufficient evidence of a longer run.

Report explicit time/history controls and inspect defaults only when needed.
Header coordinate coverage is not proof of gap-free hourly forcing or correct
time registration. GHG readers may use different coordinate names/time bases;
the helper preserves headers and labels coverage unverified without a `year`
coordinate. Check model-calendar coverage separately from climate cycling.

`soil_mgmt_in=NO` disables the soil-management file, not vegetation events.
For an enabled file use the sibling `ecosim-soil-mgmt` skill and its contract
to decode fertilizer/tillage/irrigation selectors and units. Check
`soil_mgmt_use_model_year` in the current `ReadManagementMod.F90`: the selection
may use forcing or model years. The helper checks named selector targets but
does not calculate fertilizer totals or validate full management coverage.
