# Tower-to-hourly EcoSIM adaptation

The paper evaluates daily data. Neither month-by-hour fitting nor daily-to-hourly
reconstruction is automatically validated by that paper. Select and document
one candidate strategy, compare alternatives on held-out years, and retain the
published daily engine as the reference method.

## Temporal support and hourly candidates

Verify source and tower interval bounds before aggregation. For NLDAS point
samples versus tower hourly means, use an explicit interval-mean approximation;
assign rain using its native accumulation bounds. Do not apply an inherited
ERA time shift. Fix clock conventions from provenance, with temperature and
shortwave lag diagnostics as checks rather than flux-fit tuning.

Two useful candidates are:

- **Daily BASD plus hourly reconstruction:** derive daily source/reference
  series on identical eligible days, correct them with the pinned daily engine,
  then restore subdaily variability explicitly. Temperature can start with a
  constant within-day offset `T'_h = T_h + (T'_d - mean(T_h))`. This conserves
  the corrected daily mean but leaves diurnal-range error unresolved; evaluate
  daily range/skewness correction or a fitted subdaily-shape adaptation when
  needed. Radiation can start with nonnegative scaling of the source daylight
  profile to the corrected daily energy. A zero source daily integral cannot
  be scaled to a positive target; flag it instead of dividing by epsilon.
- **Month/local-hour BASD adaptation:** fit each eligible local-hour stratum
  across multiple years, preserving chronological application values. Assess
  monthly sample sizes, autocorrelation, tail support and missing-hour bias.
  Define radiation bounds and seasonality at the correct temporal scale. Check
  discontinuities between hours/months and daily energy/trend changes; this is
  an hourly adaptation, not literal reproduction of the daily method.

Daily calibration should normally require 24 finite measured one-hour samples
in a local-standard-time day. A different support rule is possible with a
stated justification and sensitivity; do not compare a partial tower day to a
full source day. For distributions, source and reference samples must describe
comparable seasonal/hourly support. Keep application source values complete.
Never use filled meteorological reference data without identifying that training
sensitivity and its possible dependence on the source being corrected.

## Coupled variables and bounded outputs

Choose a humidity basis explicitly. One paper-aligned candidate corrects RH as
a bounded variable and temperature separately, then reconstructs vapor pressure
and VPD. A direct vapor-pressure correction is a different adaptation. In both
cases assess joint errors after reconstruction: `e = RH/100 * es(T)` and
`VPD = es(T) - e` must use a consistent documented saturation-vapor-pressure
formula and units. Report supersaturation, freezing conventions and any clipping;
independent univariate correction does not preserve dependencies automatically.

Maintain nighttime shortwave zero, nonnegative rain/wind, and reasonable
pressure/longwave bounds. Avoid smoothing away weather events. If precipitation
is corrected, preserve corrected daily totals during hourly disaggregation;
creating new wet hours on originally dry days needs a separately validated
occurrence/timing model. Do not imply distribution mapping predicts storm timing.

A tower-height wind correction changes the target forcing domain. Either keep
10 m wind and use justified profile harmonization, or consistently supply the
corrected tower-height series with its actual wind reference height. Crop canopy
height/displacement and stability make a universal scaling factor inappropriate.

## Validation and acceptance

Use contiguous held-out years, not random hourly splits. The fold helper's
training and validation sets contain disjoint years; it does not tune models.
Select extra options with training-only/nested validation, not by repeatedly
optimizing against the held-out final scores. Application source distributions
may include held-out source data under a declared offline method; this does
not authorize using held-out tower values.

For every variable report sample counts and coverage, raw and corrected bias,
RMSE, correlation, quantile errors (including tails where supported), seasonal
and hourly errors, daily range/energy, and interannual anomalies. Check quantile
change signals on synthetic controlled-change fixtures and on sufficiently long
observed/source windows, using each variable's specified trend definition.
Include correction-factor distributions, discontinuities and cross-variable
consistency. A perfect training distribution is not evidence of held-out skill.

Compare against a simple month/hour offset or scaling baseline. Promote the
complex correction only when held-out improvements justify it without material
physical, tail or interannual degradation. Criteria should be declared for the
actual intended model use; no universal RMSE improvement threshold is implied.

Inspect coupled-variable tradeoffs explicitly. A correction selected on RH and
VPD may improve vapor pressure relative to raw forcing while still performing
worse than a simpler vapor-pressure baseline. Report all three comparisons;
do not claim the selected humidity representation is best for every target.
Separate acceptance based on the original criteria from a later scientific
review veto, preserving rejected candidate scores and the reason. Choose
hyperparameters within training data or nested folds; using outer-fold scores
to select fields means those scores are selection evidence, not an untouched
final validation set. An independent period is needed for that stronger claim.

If observations will also evaluate the ecosystem model, distinguish meteorological
calibration from ecosystem validation. Meteorology may be observation-driven,
but forcing errors scored against those same calibration values are in-sample.
Keep out-of-fold forcing scores separate from final refitted-product scores.

For an observation-first product, preserve measured tower values where eligible
and fill only gaps with the accepted corrected source. Mark per-value provenance
and transitions; it is a hybrid forcing product, not pure bias-adjusted NLDAS.
An entirely unobserved year is a source-driven extension with unverified target
year performance.

## US-Ne1 starting evidence (not universal defaults)

The staged FLUXNET v1.3_r1 hourly observations span 2001–2024; new NLDAS forcing
spans 2001–2025. The full-period local evaluation found summer NLDAS temperature
bias +2.09°C and VPD bias +0.586 kPa. Do not apply these pooled numbers as fixed
corrections: they are diagnostics. Wind QC=0 coverage is only about 30%, no year
has 75% measured wind coverage, and measured precipitation is entirely missing.
Leave rainfall uncalibrated to that release; keep its budget warning explicit.

Existing reusable evidence is under the case's
`evaluation/fluxnet_evaluation_20261101_203520/`, including
`nldas_fullperiod_audit.json`, coverage/yearly/clock CSVs and the analysis script.
Verify current hashes before reuse. The staged tower and source wind heights
are 6.2 and 10 m, respectively; temperature/humidity heights are 5.5 and 2 m.
Keep site-footprint/irrigation effects visible when adjusting gridded reanalysis
to the irrigated tower microclimate.
