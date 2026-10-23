# SOAP acceptance example

Input example is the root repository's `SOAP/SOAP_share/`, discovered by giving
the helper its parent `SOAP/`. These values describe the files inspected during
development; rerun if the case changes. Do not copy this fixture into unrelated
case reports.

```bash
python3 ecosim-agent/.agents/skills/ecosim-runfolder-summary/scripts/summarize_runfolder.py SOAP --source-root .
```

Expected evidence:

- Unique `name_SOAP_S_6grid.namelist`; case `SOAP_S_6grid_08072026`.
  Trailing spaces inside grid/climate path strings must not break resolution.
- Six columns, one row; six topographic units; each cell 40 × 40 m (1600 m²
  footprint). Aspect 180° throughout, slopes 10, 25, 35, 25, 20, 10°.
- Latitude approximately 37.033° N, elevation 1210 m, grid Koppen code 35.
  No longitude is supplied by this grid; do not manufacture it from a site name.
- `NUI=1`, `NJ=4..9`, `NL1=0`, `NL2=1`. Root-bottom depths are approximately
  0.30, 0.45, 0.60, 0.80, 1.20, 1.80 m. Twenty stored depth slots are not
  twenty active soil layers. Negative hydraulic/organic-P fields are estimation
  requests, and THW/THI values are initialization controls, not final contents.
- `pft_dflag=0`, no `year` variable, two stored records: only record 1 applies.
  `NZ=4` per unit: `ndlf35`, `gr3s35`, `woak35`, `shru35`, all present in
  `SOAP_pftpar_20260804.nc`. Ignore empty fifth slots and the unused second
  record. There should be 24 decoded PFT records, not 60.

| PFT | Raw planting date | Population m⁻² | Depth m | Events/unit |
| --- | --- | --- | --- | --- |
| ndlf35 | 15059999 | 0.1 | 0.011 | 12 |
| gr3s35 | 01019999 | 50 | 0.001 | 0 |
| woak35 | 15059999 | 0.8 | 0.040 | 12 |
| shru35 | 15059999 | 3.0 | 0.004 | 0 |

The two tree PFTs use monthly dates ending in `0000`, `ICUT=JCUT=0`,
`HCUT=1000`, `PCUT=0.0008333333`, `ECUT11..14=1,1,1,0`,
`ECUT21..24=0,0,0,0`. Explain recurring thinning with no ecosystem export
configured through these fractions. Do not label it commercial harvest or fire.
No fertilizer, soil tillage, or irrigation file is enabled (`soil_mgmt_in=NO`).

Run controls: start 1930-01-01; fresh run; hourly timestep. Climate file has
1979–2025 year records, covering scheduled 1980–1989 repeated six times plus
1990–2025 once. The third triplet has zero repeats. That totals 96 scheduled
years, although `stop_n=131`; the checked standalone driver caps the yearly
stop to 96. Output names do not prove those years completed. `hist_nhtfrq=-24`
and `rest_opt='nyears',rest_frq=1` are the explicit history/restart controls.
The GHG input resolves outside the case to root `input_data/`.

Hydrology acceptance checks, including ERT's contrasting geometry, are in
[hydrology.md](hydrology.md). Both Markdown and JSON should now include flow
directions, boundary coefficients, soil hydraulic settings, and derived initial
terrain/water-table geometry, with unresolved runtime controls labeled.

Validation should also confirm no case file content changed. Reports belong
under `ecosim-agent/result/runfolder_summary/`, not inside SOAP. This example
is configuration evidence, not validation of scientific plausibility or the
provenance/version of the executable that produced existing outputs.
