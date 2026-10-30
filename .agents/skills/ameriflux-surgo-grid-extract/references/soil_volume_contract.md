# Density, macropores and water-volume consistency

Apply this cross-check when preparing or editing soil/grid driving data. Do not
infer a measured porosity merely because porosity can be calculated from density.

## Establish the source basis

Conventional bulk density is dry soil mass divided by a sample volume including
solids and the pores captured by that measurement. It does not necessarily
include all field structural voids or EcoSIM’s explicit macropore domain. Fine-earth bulk
density excludes coarse fragments, not macropores. Core/clod methods, structure
lost during sampling and moisture-state shrinkage limit representativeness.
`dbovendry_r` and `dbthirdbar_r` are different volume-state definitions, not
independent confirmations of a site's density. USDA defines coated-clod
`dbovendry` as excluding desiccation cracks. A fine-earth label establishes
coarse-fragment exclusion, not whether the measurement includes the pores
represented by FHOL. Do not assume that all macropores are excluded either:
pores inside the clod may be captured while external structural voids are not.
Match the source sampling method and volume state to the chosen model domains.
See the [USDA field definitions](https://www.nrcs.usda.gov/sites/default/files/2025-07/NASIS%207.4.3%20Table%20Column%20Descriptions.pdf). Preserve units, depth support,
measurement method, source and uncertainty. An oven-dry mass alone does not
establish the moisture state at which the sample volume was measured.

Trace the active reader and initialization, including
`f90src/IOutils/readimod.F90`, `Ecosim_mods/StartsMod.F90`,
`Modelforc/Hour1Mod.F90` and `Utils/EcoSimConst.F90`. The current code takes
BKDSI as a matrix-domain density and excludes FHOL from its soil-mass volume;
the reader's bulk-to-matrix density correction is commented out. Do not
assume the reader converts a mapped bulk density automatically.

## Cross-check selected supplied layers

Use NUI through NJ+NL1, matching source and grid depths geometrically. NL2
layers are derived, not additional measured horizons. For ROCK=0, calculate:

```
organic_mass_fraction = clip((CORGC / 1000) / OMCMassFrac, 0, 1)
particle_density = DensitySolidOM * organic_mass_fraction
                 + DensitySolidMineral * (1 - organic_mass_fraction)
matrix_porosity = 1 - BKDSI / particle_density
total_porosity = FHOL + (1 - FHOL) * matrix_porosity
soil_mass_per_total_volume = BKDSI * (1 - FHOL)
```

CORGC is kg C/Mg soil. Read constants and the mixture rule from the selected
checkout; do not hardcode a universal particle density of 2.65. The Python audit
uses the current arithmetic mixture in StartsMod, not a new proposed physical
mixing rule. Flag negative porosity before the model clips it.

Compare the calculated mass with the independent source bulk density. Agreement
between two quantities derived from the same BKDSI is only an internal algebra
check. Only if the source density includes the same explicit macropore volume,
inserting it unchanged while FHOL>0 reduces mass relative to that source
total-volume reference by FHOL. Otherwise that percentage is a scenario, not
a confirmed mass deficit. Conversely, preserving mass would require
`BKDSI = source_bulk_density / (1-FHOL)` under this ROCK=0 convention. This
identity is diagnostic, not authorization to rescale density alone.

Check `0 <= WP < FC <= matrix_porosity` for explicitly supplied matrix water
contents. Negative estimation codes require model estimates; do not treat them
as measured negative water content or certify their capacity before estimation.
Source FC/WP may use a different volume denominator. Record how much water is
assigned to macropores at each retention pressure and during cold start.
Only if macropores are assumed drained and the source is bulk-volume water:

```
matrix_FC = source_FC / (1-FHOL)
matrix_WP = source_WP / (1-FHOL)
source_FC + FHOL <= 1 - source_bulk_density / particle_density
```

An undeclared occupancy makes the last inequality a conditional finding, not
proof that field measurements contradict one another. Examine THW/THI and
`SetColdRunSoilStates`; the cold-start allocation can place water in macropores,
so a drained retention assumption does not automatically describe initialization.
FC/WP are pressure-specific water contents, not independent total porosity.
If a measured porosity or saturated water content exists, compare it only after
checking its volume basis and whether trapped air affects the saturation value.

For nonzero ROCK, trace whole-soil, non-rock and matrix domains separately.
Current automated source reconciliation is limited to ROCK=0; the audit reports
the other cases as needing a rock/macropore geometry review. Do not apply the
rock-free correction blindly or double-correct a fine-earth source.

## Run and document the audit

`Tools/driving_data_conflicts.py` now includes `soil_volume_audit`, with
per-layer particle density, matrix/total porosity, mass and source comparisons.
Source values are overlap-weighted from `interpolated.CDPTH/BKDSI/FC/WP` in
the selected source-profile JSON. Missing source support is unverified. The
tool infers conventional fine-earth bulk density for gSSURGO input; for other
sources, explicitly record the applicable basis in the source-profile evidence:

```json
{
  "volume_basis": {
    "BKDSI": "fine_earth_bulk",
    "FC": "fine_earth_bulk",
    "WP": "fine_earth_bulk",
    "macropores_at_retention": "unknown",
    "density_includes_model_macropores": null,
    "density_pore_basis_evidence": ""
  }
}
```

Use `drained` only when it is a documented modeling assumption supported by the
chosen retention convention. A source already expressed on the matrix basis
can declare `matrix`; it does not then independently establish bulk-volume mass.
Keep original source values and document transformations separately; changing a
basis label must not conceal an unperformed conversion.

`density_includes_model_macropores` is true only with documented matching
source/model pore support; false means documented exclusion of that domain;
null or absent means unknown. A nonempty `density_pore_basis_evidence` must
identify the method/source and its applicability to FHOL. Do not infer true
from gSSURGO provenance or `fine_earth_bulk`, and do not set false merely to
remove a finding. The validator retains the stable density-comparison code
but uses warning severity and a conditional explanation when support is unknown;
source pore-capacity scenarios cannot become errors without matching density
support and the required retention assumptions. Actual matrix-capacity and
particle-density violations remain errors regardless of the source definition.

The checks do not rewrite inputs. Stop on confirmed comparable-domain mass
errors or actual matrix-capacity errors;
retain conditional findings and missing evidence as warnings. Do not adopt
FHOL=0.01 universally because one numerical example fits, or choose FHOL to
mask a density error. Reconcile density, pore geometry, FC/WP and cold-start
occupancy together, then recheck initial C/N/P mass and root/hydraulic behavior.
Update the site's NetCDF/workbook, provenance, conflict report and HTML grid
section together when inputs change. Document calculated versus measured
porosity, selected volume bases, assumptions and unresolved conflicts.

## Reproduce a density/macropore finding

For each affected layer, explain that the matrix domain includes soil solids
and matrix pores; it is not the solid volume or the water-filled pore volume.
For ground area A, thickness dz and geometric volume V=A*dz, trace these current
model relationships through the selected checkout:

```
matrix_fraction = (1-ROCK)*(1-FHOL)
V_matrix = V*matrix_fraction
M_model = BKDSI*V_matrix
# Source fine-earth bulk volume equals V only for ROCK=0:
M_source = source_bulk_density*V          # ROCK=0
mass_deficit = M_source-M_model
percent_deficit = 100*(1-M_model/M_source)
# Densities Mg/m3 and A=1 m2 give mass in Mg/m2; multiply by 1000 for kg/m2.
```

Use the site's actual source density, BKDSI, ROCK, FHOL and thickness; match
source support by overlap weighting before comparing. Report a worked layer,
a table for every affected supplied layer, and cumulative mass over their
combined depth. If BKDSI equals source density and ROCK=0, the relative
difference against a hypothetical source total-volume reference equals FHOL; calculate the actual masses rather than stating only
that identity. Include a read-only reproducer when remapping is involved, per
`../../ecosim-data-readiness/references/site_conflicts.md`.

Illustration only, not site defaults: for A=1 m2, dz=0.01 m, ROCK=0,
FHOL=0.15 and source density=BKDSI=1.65 Mg/m3, V_matrix=0.0085 m3;
M_source=16.500 kg and M_model=14.025 kg. The difference is 2.475 kg or
100*2.475/16.500=15%. Replace all illustrative values with selected-site data.

For a mass-preserving candidate, show BKDSI_candidate, the organic-adjusted
particle-density calculation (including CORGC and the selected model constants),
and candidate matrix porosity. Compare it numerically with supplied FC/WP and,
where applicable, the conditional bulk-to-matrix retention conversion. Explain
which stock initializations multiply concentrations by the changed soil mass;
do not claim every C/N/P pool changes identically without tracing its units and
initialization. A mass mismatch against a mapped source does not establish that
the map is the true field density, nor that EcoSIM loses mass during a run.

## Texture and organic-matter plausibility

When the concern is whether BKDSI is reasonable, compare it with applicable
published pedotransfer functions (PTFs), rather than using a volume-partition
identity as proof of a conflict. Prefer functions appropriate to soil region,
land use, horizon and input range. Verify equations and units from primary
sources; distinguish organic carbon from organic matter and percentages from
fractions. Record the source and conversion used for each predictor.

Report the selected-site sand/silt/clay, OC/OM, horizon and depth; equations,
substitutions, predictions and absolute/relative differences; and the PTF's
calibration domain, measurement basis and reported prediction uncertainty.
Use source horizon values for native-horizon predictions or document remapping.
A deviation from a mean PTF is a plausibility flag, not statistical significance
or a physical contradiction. Do not interpret a reported mean prediction error
as a site-specific confidence interval or apply one topsoil function to every
subsoil horizon without qualification.

Useful starting references are [Saxton–Rawls (2006)](https://doi.org/10.2136/sssaj2005.0117)
(texture/OM normal density and structural adjustment) and
[Hollis et al. (2012)](https://doi.org/10.1111/j.1365-2389.2011.01412.x)
(land-use/horizon-specific density PTFs). These functions do not independently
identify EcoSIM's explicit macropore domain. Compare matrix BKDSI and, where
justified, total-layer effective density BKDSI*(1-FHOL) for ROCK=0, labeling the
two domains separately. Never select whichever comparison happens to agree.
Retain the separate check that FC/WP fit actual model matrix porosity.
Do not replace BKDSI or tune FHOL solely to match a PTF; reconcile method,
volume state, structural pores and site evidence first.
