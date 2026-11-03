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
