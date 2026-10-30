# Site evidence and driving-data conflicts

Read this when preparing driving data. A valid NetCDF or plausible number does
not establish that it belongs to the site.

Before extracting or converting inputs:

- Confirm site ID, signed coordinates, elevation, vegetation, climate class and
  source version. Recheck cached metadata against original extraction/sign
  evidence and site-filtered BIF records. Nearby sites and meteorological
  source-grid cells are not interchangeable with the target site.
- Read descriptions and field records for terrain, soil horizons, drainage,
  irrigation and dated management changes. Separate exact values, bounds,
  uncertain narrative and missing evidence. Convert percent slope to degrees
  with `atan(percent/100)`; do not write percentages into SL0.
- Identify parameters as observed, mapped/derived, model estimates, ecosystem
  priors or template defaults. Keep source path/URL, field/group, units,
  date/depth support and conversion. Unknown is not agreement.

Run the shared offline assessment after grid generation and after edits:

```bash
python Tools/driving_data_conflicts.py SITE --input-dir result/SITE/input
python Tools/driving_data_conflicts.py SITE --input-dir result/SITE/input --namelist result/SITE/run_dir/SITE.namelist
# Before layout assembly use --input-dir result/SITE instead.
# Before climate extraction, --site-only checks the selected site evidence.
```

The tool reads `SITE_ecosim_site.json`, site-filtered `SITE_BIF_records.json`,
the selected grid (`--grid PATH` overrides the default), and soil-profile
provenance. It rechecks location/sign plausibility, compares BIF locations,
converts explicit terrain bounds, compares grid/site values, validates supplied
soil layers, checks gSSURGO FC pressure and flags initial ice semantics.
Reports include severity, evidence and hashes of inspected files.
With `--namelist`, the assessment also compares annual `stop_n` with the forcing
plan and verifies selected forcing years against climate and management year
axes. This does not replace full calendar/cadence or management-event validation.
Climate preparation performs site-only preflight; grid preparation and run-folder
assembly perform the grid assessment. Errors stop those commands. Warnings
permit assembly but remain unresolved evidence/priors. The tracker must not
label a missing, stale or unavailable assessment as done.

For reviewed numerical field evidence not automatically parsed from BIF, save
`SITE_site_constraints.json` using **EcoSIM variable units**:

```json
{
  "site_id": "SITE",
  "constraints": [
    {"variable": "SL0", "minimum": 0, "maximum": 1.1458,
     "source": "site field log, terrain record, slope less than 2 percent"}
  ]
}
```

Use actual site evidence, not the example bound. `exclusive_maximum: true`
represents a strict upper bound. Constraints currently compare scalar or
uppermost values; depth-dependent evidence needs a depth-matched manual review.
Do not turn an upper bound into an exact measured slope.

Review remaining relationships through the consuming code:

For soil density/porosity, follow
`../../ameriflux-surgo-grid-extract/references/soil_volume_contract.md`.
The shared assessment now reports a per-layer `soil_volume_audit`. It checks
the current model particle-density mixture and matrix capacity, compares
source density against initialized mass only on established comparable pore
domains, and separates conditional
drained-macropore findings from actual grid contradictions. Missing source
volume basis or occupancy is unverified, not agreement.

- Check FC/WP pressure and fine-earth volume basis, porosity versus density,
  texture totals, layer ordering and source horizons versus root-zone depth.
  Supplied data extend through `NJ+NL1`; `NL2` layers are derived. Their unused
  file slots are not missing measurements; extended horizons are not observed
  deep soil. Multiple topographic units require a dedicated review; the current
  single-unit validator reports them as unsupported rather than passing them.
- Water-table modes/depths, runoff orientation, nutrients/minerals, macropores
  and Gapon coefficients need site rationale beyond range checks. `THW=1` means
  field capacity; `THI=0` requests wilting-point ice in the checked cold-start
  code. Negative unknown-chemistry codes are not negative physical pools.
- Use the meteorology skill's period/calendar, units, coverage, timezone,
  solar-noon and tower checks. Record reanalysis cell distance rather than
  requiring its coordinates to equal the tower. MAT values from different
  averaging periods need interpretation. Check hemisphere and seasonality.
- Use dated management regimes, source fields and model enums, preserving
  explicit missing-year assumptions. Compare benchmarks at matching depths,
  dates and units; a difference does not make either source authoritative.

Resolve contradictions using applicable evidence and record the decision.
Do not silently retain conflicting defaults, replace values merely to match a
benchmark, or label unperformed checks as passed. Refresh NetCDF, editable
workbooks, reports and the site guide together when changing values.

## Explain the findings

Report what conflict the given inputs produce, not just that a validator failed.
Group layer/date instances sharing a root cause; distinguish root-issue counts
from the number of error instances. Preserve per-layer details in the JSON.

For each root issue, provide:

- **Type and certainty:** field-evidence contradiction, unit/volume-basis
  mismatch, physical-capacity violation, chronology/coverage mismatch, or
  unverified assumption. Label conditional tests and their assumptions explicitly.
- **Scope and evidence:** selected file/variable, layer and depth or dates,
  source record/group, and consuming code where relevant. Do not compare unequal
  depth supports or mix bulk, fine-earth and matrix denominators.
- **Actual versus expected:** show numerical values, units, allowed bound or
  equation, and the size of the discrepancy. If a value is unavailable, say so
  rather than inventing a comparison. Computed porosity is not a measured value.
- **Mechanism and consequence:** explain why those values conflict and what
  changes physically or operationally: soil/water/C/N/P mass, pore capacity,
  retention pressure, runoff/lateral flow, or missing forcing years. Distinguish
  a code-derived consequence from an observed simulation outcome.
- **Resolution and remaining uncertainty:** state what definitions or evidence
  must be reconciled and what checks to rerun. Do not silently correct inputs
  during a reporting task or recommend changing one coupled parameter alone.

For example, do not stop at "BKDSI/FHOL conflict." Show how
`BKDSI*(1-FHOL)` compares with the source bulk density, quantify the soil-mass
difference under explicitly established pore-volume assumptions. A gSSURGO
fine-earth label alone does not establish that its measurement includes the
explicit FHOL domain; unknown support is a warning, not a confirmed mass error.
Explain conditional effects on initial concentration-to-stock quantities, and check
whether a mass-preserving conversion would make FC/WP exceed matrix pore space.
For retention, show both `source_FC+FHOL` and the available source porosity;
state that the drained-macropore test is conditional when occupancy is unknown.

Machine findings retain stable `code/message/severity` fields and now also
provide `conflict_type`, `explanation`, `consequence`, `resolution`, and numerical
`evidence`. Use those details in the site HTML, with a concise grouped summary
and expandable per-layer evidence. Refresh resolved findings too: show the
previous value, correction, source bound and remaining assumptions. An adopted
value within a field bound is a modeling choice, not an exact field measurement.

## Reproducible numerical explanations

When a finding claims a numerical discrepancy, supply enough information for a
reader to calculate it from the selected inputs. Keep the grouped summary short,
but put the following evidence in the linked report and HTML details:

- Define every symbol and its units, denominator and spatial/time support.
  Identify inputs versus derived values and the selected source and consuming
  code procedure/line or equation. For densities, distinguish solid volume,
  pore volume, matrix-domain bulk volume and total layer volume.
- State the governing equations, then substitute the actual numbers in at least
  one worked example for each distinct mechanism. Show intermediate results,
  unit conversions, absolute difference and the denominator used for a percent.
  An equation without numerical substitution, or a percent without its baseline,
  does not meet this requirement.
- Tabulate all affected layers/periods with the values needed to repeat their
  calculation. Include layer top/bottom and thickness or time interval, source
  and model quantities, and the difference. Aggregate stocks or flux integrals
  only over matching domains; report a useful total as well as per-instance
  values. Do not sum densities, concentrations or percentages as if they were
  stocks. Explain overlap weighting or other remapping and rounding.
- If the proposed reconciliation changes another constraint, calculate that
  consequence too (for example, density correction versus matrix FC capacity).
  Distinguish confirmed effects from conditional occupancy or boundary scenarios.
- Preserve structured numerical inputs, equations, units, outputs and input
  identities/hashes in JSON alongside the narrative. Reuse the existing audit
  values; do not replace them with rounded prose or hardcoded example results.
  When reproducing the result requires remapping or multiple coupled steps,
  include a read-only calculation script or exact runnable command with its
  dependencies and working directory. Run it against the selected inputs and
  compare its outputs with the report before delivery.

Use this standard when writing or renewing reports, even if the automatic
validator emits only a short finding. Supplement its evidence without treating
that short message as a complete explanation. Keep JSON and HTML calculations
consistent; identify the unchanged assessment versus a documentation-only
revision. An input-mapping difference is not a measured field loss, a runtime
conservation residual, or an observed simulation outcome. Refresh calculations
when the selected inputs change; do not silently reuse a prior site's example.

For density plausibility questions, use the soil-volume contract's "Texture and
organic-matter plausibility" review. Preserve texture/OC/OM inputs and applicable
PTF predictions, uncertainty and domains. Distinguish an unusually dense matrix,
a conditional bulk-volume comparison and a physically impossible pore/water
configuration. A coated-clod oven-dry density excludes desiccation cracks;
neither universal macropore inclusion nor universal exclusion follows from the
bulk-density name. Keep those measurement limitations visible in findings and
avoid declaring a fixed percentage soil-mass deficit without comparable support.

## Fixed-width management strings before a run

A valid NetCDF and an identical decoded JSON/Excel roundtrip do not establish
Fortran string compatibility. Read the soil-management contract's "Fixed-width
string compatibility" section and inspect raw selector bytes for every forcing
year/topographic unit, with NetCDF automatic masking disabled. Check inactive
NO sentinels as well as active table names. Distinguish active plant/event
strings from unused slots. A NUL-padded sentinel that the selected reader
compares with space-only TRIM is a confirmed reader-compatibility error.
Record the raw bytes, width, year/unit, expected sentinel/target and consuming
comparison so the finding is reproducible. Padding-only repairs must preserve
decoded schedules and water/nutrient amounts. Verify again after regeneration.
