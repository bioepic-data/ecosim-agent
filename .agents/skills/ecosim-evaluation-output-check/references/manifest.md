# Target manifest

Write one JSON manifest per observation inventory. Paths are absolute or relative
to the manifest file. Every relevant target needs a status; never drop targets
just to achieve a pass. The helper verifies output structure and values; the
agent must review the scientific mapping and observation availability.

```json
{
  "site_id": "SITE",
  "year": 2001,
  "year_reason": "Climate/management cover this year and LAI/flux observations overlap.",
  "targets": [
    {
      "id": "lai",
      "status": "mapped",
      "observations": [{"path": "/path/to/site_BIF.csv", "variable": "LAI_TOT", "coverage": "2001 growing season", "valid_count": 10}],
      "comparison": "Sum active-PFT leaf area as ECO_LAI_col; compare matching observation dates and LAI definition.",
      "variables": [{"name": "ECO_LAI_col", "units": ["m2/m2"], "dimensions": ["time", "column"], "temporal": "mean", "nonzero": true}]
    },
    {
      "id": "soil_moisture",
      "status": "blocked",
      "observations": [{"path": "/path/to/fullset.csv", "variable": "SWC_F_MDS_1", "valid_count": 100}],
      "reason": "Sensor depth and direct volumetric-water history mapping still need verification.",
      "variables": [{"name": "rWatFLP_vr", "units": ["m3 H2O/m3 soil pore"], "dimensions": ["time", "levsoi", "column"], "temporal": "mean"}]
    }
  ]
}
```

- `status` is `mapped` or `blocked`. Blocked targets need a reason and can still
  request useful registered support fields; an empty variable list is allowed
  only for blocked targets. They prevent an overall pass, but do not prevent
  checking the mapped targets in the one-year run.
- Every observation needs an existing file, variable identity and positive
  `valid_count`. Counts and their QC/depth/time meaning must come from a real
  inventory, not be fabricated to satisfy the schema. The script checks recorded
  evidence/file existence, not every observation file's schema or values.
- `comparison` is required for mapped targets. Include conversions, signs,
  observation/model time alignment and any aggregation/pool conventions.
- Model `units` lists accepted **source** units, not desired observation units.
  Comparisons ignore whitespace/case only. `dimensions` is the exact ordered
  dimension list. `temporal` is `mean`, `point`, `sum` or `static`, checked against
  the emitted `cell_method` or `cell_methods`. Accumulated GPP/ET are point
  samples of a cumulative counter, not interval sums.
- `nonzero` and `varying` are optional checks for targets where zero/constant
  output would be suspicious; do not impose them on every pool or component.
- Declare all required support fields (depth, active-PFT/pool selection, pore
  volume, coordinate conversion) in `variables`. Include static fields with
  `temporal: static` if the selected registry/history implementation emits them.
- List inactive-by-default fields exactly like active fields. `prepare` adds
  every required field to `hist_fincl1` without requiring a Fortran default
  change; the plan records additions and conflicting exclusions removed. For
  example, organ-specific biomass may need `LEAF_C_pft`/`STALK_C_pft`, and a
  profile comparison may need `BOTDEPZ_vr` or `VSPore_vr` even when they were
  absent from the original output. Their presence still does not resolve the
  pool, depth or averaging questions in the comparison mapping.
- The helper checks against registered literal `fname` values under the chosen
  EcoSIM tree. Dynamically named fields require manual resolution and a reviewed
  helper extension; do not silently bypass the registry check.
