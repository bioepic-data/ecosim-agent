---
name: ecosim-energy-flux-evaluation
description: Evaluate EcoSIM surface energy fluxes against eddy-covariance tower data. Covers net radiation (shortwave, longwave, albedo, radiating temperature), latent and sensible heat with the tower's energy-closure gap, and the split of evapotranspiration into transpiration, ground evaporation and canopy evaporation. Use when asked to assess LE, H, Rn, ET partitioning or energy-balance biases in EcoSIM history output.
---

# EcoSIM energy flux evaluation

Evaluate four linked questions. Do not stop at seasonal bias tables.

1. Is net radiation right, and if not, is the error shortwave (albedo) or longwave (surface emission)?
2. How large is the tower's closure gap, and how does it change the LE and H verdicts?
3. Is the model wrong in total turbulent flux (H + LE) or only in how it splits it between H and LE?
4. Where does the model's ET come from (transpiration, ground evaporation, canopy evaporation), and is that split plausible?

## Inputs

- **EcoSIM history files:** daily `h0` and, for diurnal and night checks, hourly
  `h1`, together with the history time origin (the run's start date; `time_bounds`
  are days from it).
- **Tower data** at daily or hourly resolution, with FLUXNET/AmeriFlux names:
  `NETRAD`, `LE_F_MDS`, `H_F_MDS`, `G_F_MDS`, `SW_IN_F` (or `SW_IN_F_MDS`),
  `SW_OUT`, `LW_IN_F`, `LW_OUT`, and soil water and temperature if you check
  coupling.
- **Optional** precipitation and irrigation, for an ET budget check.

Read [variables and conventions](references/variables.md) before computing
anything. EcoSIM sign conventions differ between fields, and two radiation
outputs depend on the build.

## Run

```sh
python3 scripts/energy_eval.py \
  --history 'run_dir/CASE.ecosim.h0.*.nc' --origin 2013-01-01 \
  --tower tower_daily.csv --start 2013-01-01 --end 2023-12-31 \
  --out result/<SITE>/energy_eval
```

Add `--history` more than once, for example once for `h0` and once for `h1`. The
script uses the finest model resolution that the tower data support. It writes:

- `scores.csv`: bias, RMSE, r and n by season;
- `monthly.csv`;
- `partition.csv`: ET components and transpiration/ET ratio by season;
- `radiation.csv`: albedo, outgoing longwave, radiating temperature and the
  Rn-bias decomposition;
- `closure.csv`;
- `summary.json`: key numbers, which variables were found, and warnings.

The script reports diagnostics. It does not draw conclusions; use the
interpretation rules below.

## Required checks

1. **Forcing consistency.** Model incoming shortwave and longwave should match the
   tower to within a few percent. If they don't, report it before judging any flux.
2. **Radiation.**
   - Albedo as total reflected over total incoming shortwave for the period
     (daytime only when hourly), not a mean of ratios.
   - Outgoing longwave and the radiating temperature it implies.
   - Split the Rn bias into incoming shortwave, reflected shortwave, incoming
     longwave and outgoing longwave parts.
3. **Closure.**
   - Tower closure ratio (H + LE)/(Rn − G), and its residual by season.
   - Recompute LE and H scores against closure-corrected tower values. Add the
     gap in proportion to the tower's H and LE (Bowen ratio preserved).
   - State this as an assumption; report raw and corrected verdicts side by side.
4. **H + LE versus partition.**
   - Compare bias(H) + bias(LE) with bias(Rn) and the tower residual.
   - If several runs differ in H and LE but not in H + LE, the changes only move
     energy between H and LE.
5. **ET partition.**
   - Transpiration, ground evaporation and canopy evaporation by season, plus the
     transpiration/ET ratio.
   - Night-time LE when hourly data exist (model versus tower).
   - Total ET against tower ET, both raw and closure-corrected.
6. **Diurnal cycle.** With hourly data, compare mean diurnal H, LE, Rn and
   outgoing longwave for the peak season. A daily bias can be confined to dawn,
   dusk or night.

## Interpretation rules

See [interpretation guide](references/interpretation.md) for reasoning and the
US-Ne1 worked example. In short:

- **Bias in total H + LE:** this is set by Rn and the closure gap; soil and
  stomatal changes won't fix it. A Rn bias that comes from outgoing longwave
  points to the radiating (surface) temperature, not to albedo.
- **H too high, LE about right after closure correction:** the split is wrong.
  The fix is more transpiration or more ground evaporation, and the two routes
  have opposite side effects on soil temperature and soil water.
- **Transpiration ratio:** a low transpiration/ET ratio under full canopy, or high
  night LE, points to too much ground evaporation. Check soil surface water before
  changing stomata.
- **Unverified claims:** do not call a flux right or wrong on the raw tower alone
  when the closure gap is larger than the bias.

## Deliverables

Report:
- the forcing check;
- the radiation decomposition;
- closure statistics;
- raw and closure-corrected LE/H scores;
- H + LE against partition;
- the ET partition table;
- for hourly data, diurnal panels.

State each verdict as "with raw tower" and "with closure-corrected tower" when
they differ. Keep scripts and outputs with the report. Distinguish confirmed
diagnostics from candidate explanations.
