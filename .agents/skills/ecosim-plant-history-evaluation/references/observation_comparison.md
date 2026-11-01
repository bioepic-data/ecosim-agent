# Observation matching and conversions

Use these rules when evaluating existing history against tower and field data.
They do not authorize simulations or input changes. Site-specific examples are
in [US-Ne1 lessons](us-ne1_lessons.md).

## Inventory before declaring observations missing

Search both structured measurements and management free-text comments. Harvest
yield may occur only in agriculture comments, despite no structured grain-yield
target. Retain original text, group IDs, dates, area/footprint and units. A
comment is evidence with its stated scope, not automatically a whole-site
annual measurement. Distinguish observations unavailable from comparisons
blocked by missing model diagnostics or uncertain conversion.

For FLUXNET soil channels, inspect the actual data archive's BIFVARINFO metadata
as well as general BIF records. Pivot metadata by GROUP_ID and VARIABLE to resolve
VAR_INFO_VARNAME, HEIGHT, UNIT, DATE and MODEL where present. Verify depth sign,
units and channel coverage; channel indices are not model layer numbers.
A replacement probe at a different depth is a separate series, not a continuous
same-depth record. Missing/all-fill corrected fluxes are unavailable: retain
uncorrected comparisons with their energy-closure uncertainty.

## Time support and scoring

Show the full available observation record alongside the actual model overlap
when a whole-record comparison is requested. Shade/label observation-only years;
do not extrapolate model curves. Compute pooled scores on matched overlap,
separately from per-year scores. Good daily correlations can be driven by the
seasonal cycle; assess annual totals/anomalies or year-to-year variability
separately before claiming interannual skill.

Define daily completeness and QC selection explicitly. Full-day means and
integrals must retain full-day temporal support: dropping bad hours and averaging
the remainder produces a partial-day statistic. One defensible hourly workflow
requires 24 finite values and a declared fraction of measured/good-gapfilled
hours, retaining all 24 values on qualifying days. Adapt to actual sampling
intervals, timezone and DST; do not universalize 24-hour or 75% thresholds.

Annual paired-day soil statistics are not full-year means unless coverage
supports that claim. Label incomplete years and do not scale partial-year flux
totals without a justified method. Preserve original observation products and
partitioning-method sensitivity; tower GPP/respiration products are estimates.

## Hourly restart comparisons and summer plots

When an hourly run is authorized, use the model repository's restart-run skill
for an isolated branch with a new namelist, mandatory preflight and preserved
parent data. Select a checkpoint/period with useful observations and complete
forcing/management coverage. Freeze selected inputs and record executable,
checkpoint and namelist identities. Keep registered inactive targets explicit
in the namelist and verify actual emission, including net radiation when useful.

At a 3600-second timestep, `hist_nhtfrq=1` requests hourly output. Confirm the
actual bounds, contiguous one-hour intervals and full requested coverage after
execution. Preserve per-field averaging semantics: hourly cumulative GPP/RA/RH
endpoints need differencing with annual-reset and initial-state handling, not
interpretation as rates. Cross-check an emitted canopy GPP rate where equivalent
PFT/area support permits. Sampling every hour does not turn an end-of-step state
into a true subhour mean.

Establish local standard time versus UTC, DST and interval-start/end conventions
from observation documentation and forcing provenance. Check for shifts already
applied during preprocessing before applying another. Use air temperature and
incoming radiation lag checks to diagnose alignment; do not optimize a time
shift against ecosystem-flux fit. Decode bounds against the verified origin even
when an hourly filename contains an invalid calendar day such as January 00.

For direct NEE/LE/H comparisons, distinguish measured-only support from a
measured-plus-good-gapfill sensitivity using the actual product QC definitions.
Partition-derived GPP/RECO selected using measured NEE support remain derived
products, not direct measurements. Retain supplied ustar-screening semantics.
Report year, season and day/night support instead of merging unlike samples.

Include both mean diurnal cycles and a **continuous multiweek hourly sequence**
when diagnosing summer timing and amplitude; a mean cycle can conceal event
errors and hourly spikes. Honor the requested window. A fixed 21-day window is
a useful example: 504 one-hour intervals in each year, with explicit local start
and end times. Choose dates independently of fit quality, and use the same
calendar window across compared years when scientifically appropriate.

Preserve the complete hourly index with NaN gaps; dropping missing rows before
plotting can draw lines across unobserved periods. Show units, timezone, QC
support and actual paired counts. Paired plots may mask both curves to common
support, but state that choice; a separately shown complete model curve should
be distinguished. Do not smooth or aggregate away hourly features. Save the
window extract and reproducible plotting script. Visually inspect exported
figures and verify their embedding and links when updating HTML.

Aggregate the branch back to daily support and compare with the parent. Report
initial-state/first-days agreement and later drift separately. Unchanged current
parameters do not establish an identical historical replay. Retain material
branch-parent differences alongside observation scores without asserting their
cause. Hourly moisture products still need the pore-volume and subhour-covariance
caveats below. Reconcile earlier report claims such as "no simulation launched"
or "net radiation unavailable" with the new run's precise scope.

## Soil depths and water definitions

Construct layer centres from actual top/bottom boundaries and active masks,
excluding surface litter and unused padding as appropriate. Interpolate profile
values to documented sensor depths; reject or explicitly justify extrapolation.
For changing geometry, use the corresponding time's centres. Record that point
interpolation approximates layer/probe spatial support.

Relative saturation is not volumetric water content. Trace the history update
and saturation denominator in the relevant implementation. Determine whether
porosity applies to the matrix or bulk soil and whether rock, macropores or ice
are included. Convert saturation to the appropriate layer water content **before**
depth interpolation. Multiplying two independently interpolated profiles is
generally different. Percent volumetric water divided by 100 is m³/m³.

For the inspected zero-rock EcoSIM formulation with matrix porosity phi,
macropore volume fraction f, and saturation s based on matrix + macro pore space:

```text
theta_liquid = s * [(1-f)*phi + f]
```

This formula is conditional on that denominator and volume definition; it is
not a universal EcoSIM conversion. Check restart geometry/volume fractions when
using input fractions to reconstruct historical bulk pore space. Current source
alone does not prove the executable revision that produced old output.

Multiplying daily mean saturation by mean porosity omits subdaily covariance;
label this a proxy unless exact averaging equivalence is established. A legacy
script's first-record porosity, fixed layer count or column indices are reference
choices to verify, not transferable defaults. Compare alternative conversions on
the same dates when semantics or provenance remain uncertain. Do not select the
conversion simply because it fits observations better.

Check observations against reconstructed pore capacity. Repeated values above
model capacity expose a conditional volume/support, porosity or calibration
conflict that adjusting saturation alone cannot resolve. Examine sensor level
shifts and before/after scores; do not infer a calibration change from a step
alone. Retain frozen-soil liquid/ice and sensor-response uncertainty.

## Harvest yield

Separate yield rates from harvested totals and avoid summing repeated bu/acre
rates across harvest passes. Distinct area-specific yields need area weighting
with adequate coverage; duplicate text does not establish independent years.
Verify crop, wet/dry mass, standard moisture, bushel mass and grain carbon
fraction. For maize, the documented 56-lb bushel at 15.5% moisture gives:

```text
dry grain [g/m²] = yield [bu/acre] * 56 * 453.59237 / 4046.8564224 * 0.845
```

Source: [Purdue corn grain-quality guidance](https://www.extension.purdue.edu/extmedia/ay/ay-225.pdf).
Do not apply that crop/moisture convention to unspecified records. Do not
subtract actual harvest moisture again when yield is already standardized.

Prefer verified harvested-grain output to standing GRAIN_C. If only standing
carbon exists, label equivalent yield conditional on carbon fraction and grain
recovery. Match near harvest and check whether daily harvest-day means mix
pre/post harvest pools; a previous-day mean can be an explicit proxy. Show
annual peak separately when useful; it is not automatically harvested yield.
Harvest completion and harvest start are different dates. Report phenology
mismatch rather than silently aligning peaks to field dates.

Use measured/documented grain carbon fraction when available. Any assumed
fraction or sensitivity interval must be labeled as such; an illustrative
interval is not measured uncertainty or a literature confidence interval.
Do not derive grain yield from ambiguous Total/Foliage production labels.
