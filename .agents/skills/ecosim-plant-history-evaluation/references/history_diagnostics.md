# History semantics and diagnostic rules

These mappings describe inspected standalone EcoSIM implementations. Confirm
them in the actual `HistRegister*`, `HistUpdate*`, plant-process, input-reader
and restart code; a source revision does not prove binary equivalence. Missing
diagnostics limit a conclusion rather than justify a guessed variable alias.

## Time, dimensions and aggregation

- Inspect every history file needed for the selected window. Decode CF time
  where available. Some h0 files contain only day-offset `time_bounds`; verify
  their origin from run/restart metadata and recorded intervals. A split-file
  filename does not establish that offsets restarted there. Check interval
  widths, gaps, overlaps, duplicates and complete-year coverage, not endpoints
  alone. Incomplete years may be reported separately with their actual support.
- Keep the distinction between model year, cycled weather year and input
  selectors using model versus forcing year. Establish calendar alignment
  before comparing date-specific observations. Repeated forcing cycles can
  diagnose state drift, but do not constitute independent weather samples.
- Inspect variable dimensions rather than assuming PFT, root-axis and soil
  layer order. Identify active grid/PFT/layer slots; preserve legitimate zeros
  and exclude fill/masked padding. Keep PFT metrics separate before aggregation.
- Use actual PFT leaf area for leaf-area weighting where available. Stalk area
  and whole-shoot-C × SLA are not exact leaf-area weights. If only a proxy or
  active-PFT mean is possible, report that approximation.
- Time-weight interval means by interval duration. Distinguish instantaneous
  samples, interval-integrated fluxes, interval means and cumulative counters.
  Derive reset handling from source and management, not a generic monotonicity
  assumption. For differencing an annual counter, verify its initial/reset
  state, especially for midyear restarts and changes in crop population.

## Plant metrics and interpretation

| Target | Candidate history | Calculation and interpretation |
|---|---|---|
| GPP, NPP, autotrophic respiration | `ECO_GPP_col`, `ECO_NPP_col`, `ECO_RA_col` | When annual cumulative, use the last counter after establishing its reset. Do not sum daily counters. In the inspected sign convention RA is negative, so NPP = GPP + RA. Check the identity at common times. |
| ET | `ECO_ET_col` | Verify cumulative semantics, water-depth units and resets before forming an annual total. |
| Canopy size | `ECO_LAI_col`, `CAN_HT_pft`, `LEAF_C_pft`, `SHOOT_C_pft`, `Root_C_pft` | Report annual peaks and relevant growing/mature-season distributions. Whole shoot includes its components; do not add leaf/stalk/grain pools to it again. |
| Height | `CAN_HT_pft`, `Stalk_HT_pft` | Report canopy and stalk height separately in m, with peak dates and paired field-date comparisons. Daily means and observed sampling statistics may differ. |
| Leaf N | `LEAF_N_pft`, `LeafN_pft`, `LeafProteinN_pft`, matched PFT leaf area | Distinguish structural leaf N stock (g N/m² ground), structural N density (g N/m² leaf) and protein N density (g N/m² leaf). Trace storage inclusion; these fields are not interchangeable. A stock/LAI ratio of interval means is a proxy for mean leaf N density. |
| Phenological development | `Growth_Stage_pft` and applicable phenology/node/leaf diagnostics | Trace stage codes and branch aggregation before deriving milestones. Report full progression, transition dates and control/test shifts; always include the crop stage-entry/DAP table below. Model stages are not automatically field V/R stages. |
| Shoot-to-root ratio | `SHOOT_C_pft`, `Root_C_pft` | Dimensionless shoot C / root C on the same PFT/area/time basis; show both stocks and follow the ratio rules below. |
| Reproduction | `GRAIN_C_pft`, `GRAIN_N_pft`, `GRAIN_Number_pft`, `Growth_Stage_pft` | Match grain C and number at the same time for carbon per kernel. Peak standing grain C is not harvested dry-matter yield. Trace stage codes/reset rules before deriving dates. |
| SLA | `SLA_pft` | Common history units are cm²/g C; multiply by 1e-4 for m²/g C. Check mature-leaf periods separately from low-mass senescence. SLA1 is a growth-allometry coefficient, not necessarily realized SLA. |
| Photosynthetic capacity | `VcMax25C_RUBISCO_pft`, `VoMax25C_RUBISCO_pft`, `JMax25C_photo_pft`, `RUB_ACTVN_pft`, `LeafRubiscoN_pft` | Verify CO₂/O₂/electron and leaf-area bases, protein/enzyme-C conversions and activation. Compare leaf-area quantities with leaf-area observations, not protein-normalized input turnover rates. |
| Canopy water status | `PSI_CAN_pft`, `WTR_STRESS_pft` | Daily mean potential is not daily minimum. In the inspected vascular pathway the stress counter accumulates hours below a threshold; report its appropriate increment or peak with reset/averaging caveats. Never sum cumulative daily rows. |
| Root distribution | `Root1stDepz_pft`, `RootC_pvr`, `RootC_2nd_pvr`, `RootH2OUptk_pvr` | Compare raw root-tip depth, configured rooting-layer boundary and actual biomass/uptake profile. Raw depth overshoot alone does not prove active roots below the allowed layer. Verify density versus area-integrated units before summing layers. |
| Root environment | `rWatFLP_vr`, `PSI_vr`, `Root_OXYSTRESS_pvr` | Use rooted/uptaking layers rather than whole-column averages. Relative saturation is not volumetric water content. Verify oxygen-factor direction. |
| Nutrient supply/demand | `Uptk_NMin_CumYr_FLX_pft`, `Uptk_N_CumYr_FLX_pft`, `CanopyNLim_pft`, `CanopyPLim_pft`, `RootNutUptk_fNlim_pvr`, `RootNutUptk_fPlim_pvr` | Distinguish mineral/total uptake, gross uptake and plant accumulation. g N/m² ×10 = kg N/ha. Derive limitation-factor direction from equations; some factors allow more uptake when larger despite misleading labels. |
| Conservation | `Plant_BALANCE_C_pft`, `Plant_BALANCE_N_pft`, `Plant_BALANCE_P_pft` | Verify reset, accumulation, sign and elemental units. Some N/P metadata historically say gC/m²; inspect the update before interpreting units. Small residuals establish numerical accounting only. |

Use an explicit season mask suited to the plant and question. `LAI>0.1` is one
possible crop mask, not a universal threshold. Report that mask, observation
dates and sample counts. Do not treat off-season fill, zero activity, or late
high SLA from a depleted leaf-C denominator as typical mature traits.

For leaf mass/area, invert matched-date LMA only after establishing its mass
basis. Historical BADM protocols may use g C/m²; other sources use g dry
matter/m². A missing unit is uncertainty. Use a documented carbon fraction
only when converting a dry-mass measurement; do not apply it twice. Likewise,
BIF organ `Total` chemistry does not establish leaf N/P concentrations.

## Crop phenology table

Every crop-output evaluation must show a table with **phenological stage,
entry date and elapsed days after planting (DAP)** in its readable report and
user-facing evaluation response. Identify the case, grid/PFT, cohort or crop
cycle and season above the table or in columns. Save a CSV alongside generated
evaluation artifacts. For a sensitivity comparison, provide dates and DAP for
each case and the DAP shift relative to its matched control.

- Establish the actual planting event from the selected management input and
  model calendar/log when available. Honor model/forcing-year mapping and
  events skipped because a crop is already alive. Record the planting source;
  do not substitute emergence, January 1, run start or checkpoint date.
- Calculate `DAP = stage_entry_date - planting_date` as elapsed days in the
  model calendar, with planting at **DAP 0**, not 1. Use full dates across year
  boundaries. Keep rotations, repeated plantings and cohorts separate; a
  continued crop retains its original planting origin across a restart. Each
  comparison case uses its own planting date, so date shifts and DAP shifts
  need not be equal.
- Include planting and all applicable stages in the selected model's sequence:
  emergence, floral initiation, jointing, elongation, heading, anthesis,
  beginning seed/grain fill, seed-number setting, seed-mass setting and end of
  seed fill, plus diagnosed senescence or maturity where available. Keep
  harvest separately labeled as a management event if included. Do not invent
  absent stage codes or force cereal stage names onto a soybean V/R scale.
- Prefer recorded stage calendars or transition timestamps. Otherwise state
  the history variable, code/threshold, sampling resolution and entry rule,
  accounting for averaging, resets and aggregation across branches. Label the
  first observed attainment at daily resolution accordingly; do not present
  it as an exact within-day or independently verified main-stem event. Distinguish
  floral induction/initiation from first open flower; validate any mapping to
  observed crop stages before interpreting a timing error.
- Retain rows with `not reached`, `before evaluation window`, `unavailable` or
  `not applicable`, with the reason. If planting is unknown, show available
  stage dates but mark DAP unavailable. If a stage was already attained at the
  start and its actual date cannot be recovered, do not assign run start as its
  entry date. Missing observations do not remove the model table; label any
  observed dates/DAP separately from simulated values.

## Shoot-to-root ratio

Use `SHOOT_C_pft / Root_C_pft` as the default **carbon-based** shoot-to-root
ratio (g shoot C / g root C), after checking the selected executable's pool
definitions. In the inspected implementation, both fields are area-normalized
stocks from `ShootElms_pft` and `RootElms_pft` in `HistUpdatePlant.F90`;
`PlantBalMod.F90` constructs the pools. `Root_C_pft` includes structural and
nonstructural root C and excludes root nodules. Do not silently replace it with
`RootST_C_pft`, a fine-root-only pool or an unintegrated `RootC_pvr` density.
Record the shoot's reproductive/storage inclusion and any nodule, mycorrhizal
or dead-pool exclusions. A structural, vegetative-only or nodule-inclusive ratio
is a separately defined diagnostic requiring matching component outputs.

- Show shoot and root stocks with the ratio, using a common growing-season or
  live-cohort mask. Mark ratios undefined for masked/nonfinite data or root C
  at/below a documented small-denominator threshold. Report the threshold and
  excluded support; never turn 0/0 into zero or hide invalid roots by clipping.
- Ratios of daily mean stocks are **ratios of daily means**, not means of
  hourly ratios. State whether a seasonal summary averages valid daily ratios
  or divides matched-period mean stocks; do not confuse the two.
- Compute per-PFT ratios first. For a stand ratio, divide the sums of shoot and
  root C on a verified common area basis rather than averaging PFT ratios.
  Use matched calendar periods and relevant phenological milestones for
  control/test comparisons; separate growth allocation from harvest, tissue
  turnover and root loss. A stock ratio is not a shoot/root allocation flux.
- Match observed organ, root-depth and living/structural definitions. A carbon
  ratio is not automatically a dry-mass ratio: with distinct carbon fractions,
  `S:R_dry = (shoot_C / root_C) * (fC_root / fC_shoot)`. Document the fractions
  and pool compatibility before comparison; retain model-only analysis when
  suitable root observations are unavailable.

## Excessive height

When canopy height is excessive, check both `SSL1` and `SNL1` in the selected
PFT file and read-only trait output before recommending unrelated physiology
or leaf-area changes. `SSL1` scales petiole/sheath extension; `SNL1` scales
stem internode extension. These are growth-allometry coefficients, not measured
organ lengths: biomass exponents, growth allocation, etiolation and angular
projection also enter the equations.

Compare `CAN_HT_pft` and `Stalk_HT_pft` on the same dates, including stalk height
at the canopy-height peak. Canopy height includes petiole and leaflet elevation;
the difference is not petiole length alone. Large canopy-minus-stalk excess
prioritizes petiole geometry/`SSL1` and height assembly; excessive stalk height
prioritizes internodes/`SNL1`, node number, branching and biomass allocation.
Inspect node-level lengths where available, and retain potential geometry or
unit errors rather than compensating for them through tuning. Match literature
by organ, stage, cultivar and angle reference. Keep LAI, leaf protein/capacity,
GPP timing and energy fluxes in the comparison: shorter plants do not establish
better emergence or carbon fluxes. Label combined SSL1/SNL1 perturbations as a
joint response, not separate attribution; do not transfer case-specific tested
values as defaults.

## Hydraulics and parameter diagnosis

Read `PlantInfoMod`, `StartqMod` and water-uptake resistance assembly in the
selected source. Distinguish the following:

- `RSRR`: radial resistivity in MPa h/m. Intrinsic fully wetted conductivity is
  `1/(3600*RSRR)` m/s/MPa; soil drying and root surface area modify resistance.
- `RVSR`: literal mean vessel/tracheid lumen radius in m. The inspected count
  is `N=0.2*(RRAD2M/RVSR)^2`; `N>=1` requires
  `RVSR<=sqrt(0.2)*RRAD2M`. Use the static trait checker and retain its findings.
  The fixed whole-root lumen fraction is a model assumption, not field evidence.
  Review fine-root radius, conduit distribution/count and root order together.
- `ARSRA`: correction from ideal lumen to actual axial resistance. Under the
  inspected fixed-area conduit-count rule, bundle resistance scales with
  `RVSR^-2` at fixed RRAD2M/ARSRA, although individual-vessel resistance scales
  with `RVSR^-4`. This does not determine the whole soil–plant resistance.
- `RSSA`: stalk contribution, distinct from root vessel and radial terms.

Do not directly ratio `KH2OAxial_pvr` and `KH2ORadial_pvr` without reconciling
their scaling; the inspected axial field includes a layer-thickness-squared
factor. A source-verified diagnostic in one implementation is:

```text
Cr = KH2ORadial_pvr
Ct = RootKond2H2O_pvr * 3600 / 1e7
approximate radial share of internal resistance = Ct / Cr
```

Use only where both terms are positive and physically active. Weight by
verified water-uptake sign/amount and report excluded records. Confirm the
conversion and whether Ct includes stalk/other terms in the active revision.
This proxy excludes soil resistance. Products/ratios of daily means are not
exact hourly integrations or resistance partitions. Avoid presenting their
complement as pure vessel resistance when stalk is included.

## Fertilizer and nutrient budgets

Use [ecosim-soil-mgmt](../../ecosim-soil-mgmt/SKILL.md) to read back the selected
management NetCDF rather than trusting a stale source table. Trace applied
N/P channels, mineral/organic forms and event timing. Where irrigation is in
mm and dissolved N in g N/m³:

```text
irrigation N [kg N/ha] = irrigation [mm] * dissolved N [g N/m³] * 0.01
```

Include irrigation-borne N once. Distinguish calendar input, crop-season input,
post-harvest application and previous-autumn carryover. Verify elemental P
versus P₂O₅ before comparing rates. Do not interpret gross uptake/applied N as
fertilizer recovery without separating soil supply, residual fertilizer,
storage and losses. Whole-column mineral N is not all root-accessible supply;
inspect depth, adsorption, nitrification and initialization where relevant.

Check uptake before/after observed or modeled anthesis and through grain fill,
with date definitions explicit. Daily peaks, seasonal integral and plant N
accumulation are different metrics. Differentiate demand/carbon/root-activity
feedback from transporter Vmax before recommending a larger uptake parameter.

## Mechanisms, comparisons and recommendations

- Match field LAI, biomass and height on dates, organs, age and planting density.
  Keep tower partition-derived GPP distinguishable from directly observed flux.
  Do not report a full-year bias against a partial-year observation total.
- Interpret fitted gas-exchange Vcmax as formulation-dependent. Maximum enzyme
  capacity and fitted active capacity may differ. Keep VCMX/VOMX/Kc/Ko as a
  coherent kinetic set; review specificity and units before changing one value.
- Evaluate the coincidence of green leaves, activation, carbon supply, grain
  filling and uptake cessation. A nominal GFILL/GRMX maximum-rate calculation
  is not a prediction of actual filling duration. Use branch counters or
  instrumentation to establish a specific shutdown path; history correlation
  alone is not proof. Do not propose source constants as namelist parameters.
- Compare matched control/test years on the same checkpoint, executable,
  forcing and output support. Report changes in coupled water, C, N and canopy
  variables, and residual geometry/phenology problems. A successful short
  sensitivity need not be anatomically valid or equilibrated.
- Recommend only parameters present in the selected configuration. Explain
  the baseline, causal hypothesis, evidence and what a diagnostic perturbation
  would resolve. Preserve provenance warnings; do not tune management away
  from field records to improve fit.
