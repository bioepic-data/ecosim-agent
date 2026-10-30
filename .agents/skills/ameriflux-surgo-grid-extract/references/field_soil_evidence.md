# Field evidence for texture and organic carbon

Use when selecting, checking or correcting a site's soil texture or organic
carbon. Keep source extraction separate from the decision about which evidence
represents the modeled field. Do not generalize one site's selected carbon stock,
texture, correction factor or assumed application depth to other sites.

## Review site evidence before accepting mapped inputs

- Search staged BIF/BADM records for `GRP_SOIL_TEX` and `GRP_SOIL_CHEM`, alongside
  source horizons and relevant site publications. Filter by `SITE_ID` before
  grouping; associate measurements using site, variable group and `GROUP_ID`.
  A shared group number across sites does not establish a shared measurement.
- Preserve raw fields and source version/path. For each candidate, record the
  value, units and their evidence, upper/lower depth, date, measurement method,
  footprint, replication/uncertainty and missing metadata. Common fields include
  `SOIL_TEX_SAND/SILT/CLAY`, `SOIL_TEX_PROFILE_MIN/MAX`,
  `SOIL_CHEM_C_ORG`, `SOIL_CHEM_BD`, `SOIL_CHEM_PROFILE_MIN/MAX` and
  `SOIL_CHEM_DATE`; not every record supplies them all.
- Prefer relevant field evidence with compatible depth, method, units and time
  support over generic mapped representatives. A BIF label alone does not prove
  a laboratory measurement or make conflicting values authoritative. Verify
  units from the applicable dictionary/version; if unavailable, state the
  assumed interpretation and keep the comparison conditional.
- Match publications to the actual field/site and distinguish measured values,
  mapped descriptions, fitted hydraulic parameters and model outputs. A soil
  series description or matching texture class supports plausibility, not an
  exact site-specific profile. Retain differing classifications or methods as
  unresolved evidence rather than silently choosing the agreeable description.

## Texture comparisons

For EcoSIM inputs, `sand_percent=CSAND/10` and `silt_percent=CSILT/10` when the
file uses kg/Mg mineral soil. Inferred clay is `100-sand_percent-silt_percent`.
Check nonnegative fractions and a 100% total. Compare these mineral fractions
with compatible field fractions, before any runtime organic-matter correction.
Report numerical differences in percentage points as well as texture class;
matching classes do not imply identical fractions or statistical agreement.

A record without sampling depth is a site summary with unspecified support.
Do not silently interpret it as a surface sample, a 0–30 cm composite or a
uniform full-depth profile. If the user authorizes using it without specifying
depth, state a reasonable application-depth assumption and record it explicitly;
retain deeper mapped values unless their replacement is also justified.

## Organic carbon, organic matter and stock

`CORGC` is total organic **carbon**, not organic matter. kg C/Mg soil equals
g C/kg soil; carbon percent is `CORGC/10`. Source OM-to-C conversion factors
are conventions, not independent measurements. Keep the extraction convention
separate from the model's OM conversion: read the current `OMCMassFrac` and
particle-density constants rather than assuming they equal the source factor.

Compare concentrations at matching depths first. For a stock comparison,
establish the source bulk/fine-earth/matrix volume basis and integrate the
actual selected layers. Under the current model matrix convention:

```
dz_overlap = max(0, min(layer_bottom, target_bottom)
                   - max(layer_top, target_top))
SOC [Mg C/ha] = sum(CORGC [kg C/Mg soil] * BKDSI [Mg soil/m3]
                   * (1-ROCK) * (1-FHOL) * dz_overlap [m]) * 10
```

Use the [soil-volume contract](soil_volume_contract.md) for pore-domain
qualifications. Distinguish fixed-depth inventories from equivalent-soil-mass
estimates, and method summaries from observations at a particular date. Do not
average conflicting BIF and published stocks, select a midpoint as a measured
value, or rescale the profile before reconciling units, sampling support and
methods. A provisional literature-constrained target may be used within the
user's authorized task, with its rationale and remaining discrepancy recorded.

## Apply and verify an authorized correction

- Archive the current grid/workbook and affected reports; preserve original
  mapped horizons and raw field evidence. Record an applied override rather
  than rewriting source interpolation to make it appear measured.
- Decide how an observation boundary intersects model layers before editing.
  A partially intersecting layer cannot represent two different concentrations
  without remapping or changing geometry. Prefer mass-conserving remapping
  where appropriate. If retaining geometry and assigning a uniform value to the
  full layer, report both the target depth and full changed support. Quantify
  any stock added outside the target interval; do not claim that region stayed
  unchanged. A depth-averaged blend also requires checking the achieved target.
- For SOC changes, identify `CORGR` as part of `CORGC`, not additional SOC.
  Review the POC share and organic N/P handling. Preserve unknown-value codes
  unless evidence supports replacement; trace their current initialization
  equations and report nonlinear changes in estimated N/P. Do not assume
  unchanged selectors mean unchanged initialized nutrient stocks.
- Recheck organic-adjusted particle density, matrix/total porosity and FC/WP
  capacity. Texture changes warrant review of texture-dependent calculations;
  they do not by themselves authorize replacing measured hydraulic values with
  a PTF or recalibrating density/conductivity.
- Read the edited file back, integrate the achieved stock when relevant, and
  verify masks, geometry, deeper/unrelated variables and intended pool ratios.
  Regenerate the workbook and compare a scratch roundtrip's data, attributes,
  dimensions and masks. Run the applicable layer and conflict checks; retain
  unresolved evidence warnings even when format and capacity checks pass.
- Refresh the active grid snapshot, provenance, HTML and linked calculations
  together. Identify report hashes and the baseline for each check: the original
  grid establishes the cumulative change, while the most recent archive checks
  preservation during a subsequent edit. Keep older comparisons explicitly
  historical; update reproducible checks for authorized later changes instead
  of calling every difference from the first archive an error.
- Mark earlier runs/restarts as predating the changed input in the tracker,
  preserving their historical results. Do not reuse a restart as evidence that
  a new cold-start pool was applied. A corrected initialization stock is not a
  verified observation-year stock after spin-up; validate that year separately
  during a requested fresh run, without silently launching a long simulation.
