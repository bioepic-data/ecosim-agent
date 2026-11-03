# US-Ne1 application evidence

This is a worked example from the 2026-11-01 application, not a universal
preset. Find the original artifacts under the workspace's
`work-space/US-Ne1/sources/NLDAS/bias_adjustment_20261101/` and verify hashes
before reusing them. `apply_adjustment.py`, `audit.json`, `fit_strata.csv`,
`heldout_scores.csv`, `interannual_review.json` and `validation.json` preserve
the numerical configuration and checks. The external author engine and its
license are stored in that task's `backend/`, not vendored into this skill.

## Tested adaptation

The task used the archived v1.0 array functions without numerical body changes,
loading their original function ASTs and omitting unused Iris/Dask IO layers.
Finite QC=0 observation/source pairs were filtered together; no tower-gap
sampling was used. NumPy 2.2.6 was installed in a task environment because the
initial system NumPy was outside the installed SciPy's compatibility range.
The isolated environment inherited other packages; its recorded versions are
part of the evidence, not a fully portable lockfile.

Monthly/local-hour strata were evaluated with four six-year holdouts over
2001–2024. Minimum training support was 150 pairs spanning eight years; these
numbers are application choices. Normal, additive, detrended temperature and
bounded derived RH corrections were accepted; RH and selected temperature were
converted back to actual vapor pressure and VPD. Application source statistics
were allowed, without held-out tower observations. The final 2001–2025 product
was refitted using all eligible observations; 2025 remains unvalidated.

## What changed the decisions

| Diagnostic | Raw | Corrected candidate | Decision |
|---|---:|---:|---|
| Temperature hourly RMSE, °C | 2.930 | 2.546 | Accept, disclose annual tradeoff |
| Vapor-pressure hourly RMSE, kPa | 0.216 | 0.185 | Improves raw; simple baseline is better at 0.165 |
| VPD hourly RMSE, kPa | 0.585 | 0.334 | Joint humidity benefit |
| Longwave hourly RMSE, W m−2 | 25.860 | 23.246 | Insufficient alone for promotion |
| Longwave annual-anomaly RMSE, W m−2 | 4.290 | 5.131 | Retain raw longwave |

Temperature annual-anomaly RMSE rose from 0.536 to 0.571°C despite the hourly
improvement; this was disclosed rather than described as improvement at all
scales. The longwave rejection used an additional review veto introduced after
inspecting annual diagnostics. Keep that fact visible: the selected-product
out-of-fold scores also informed field selection and are not a new independent
validation of that choice.

Pressure and shortwave candidates failed the initial acceptance criteria.
Rainfall had no measured reference; wind had sparse, height-mismatched
observations. The final file therefore changes only temperature and humidity.
Rejected corrections and their scores were retained. The attempted shortwave
candidate used a fixed 1400 W m−2 hourly bound, not the published daily
moving upper-bound climatology; do not inherit that experimental shortcut
without new validation.

## Simulation handoff

The experiment is under
`work-space/US-Ne1/experiments/nldas_lange_20261101/US-Ne1/`. It retained the
parent's 24-year spinup and 2001–2018 analysis, rather than extending management
automatically to match 2025 forcing. A fresh initialization avoided an
unacknowledged legacy-forcing state. Whole-case preflight had zero mechanical
errors; a two-year smoke run completed 17,520 hours; the one-year output check
emitted all 74 requested fields. Strict inherited Rubisco/PEPC findings remained
separate from those execution passes.

The requested `ecosim_pftpar_20261021.nc` was identical to the initial site-named
copy. The production path was revised to the dated filename, with fresh
preflight/provenance and no redundant simulation for a rename. The inherited
trait findings persisted because the parameter bytes did not change. Use this
identity check instead of assuming a filename change resolves physiology.
