# Evidence coverage audit

Parameter-screen success and evidence-review completeness are separate results.
The checker does not browse and cannot tell whether omitted evidence means
“searched and unavailable” or “never searched.” An empty numeric-range list or
zero warnings must never stand in for a completed literature review.

## Required coverage table

Before a comprehensive plant-trait review or a scientific run-readiness claim,
create a sidecar table/JSON for each active PFT at the selected grid. Cover:

| Group | Traits to review when present/relevant |
| --- | --- |
| Identity and phenology | Pathway, annual/perennial, crop versus self-seeding context, leaf-out/off and reproductive controls |
| Photosynthesis | Rubisco quartet, PEPC where applicable, protein allocations and electron transport |
| Leaf morphology and optics | SLA/LMA, leaf dimensions, absorptance and canopy/root structural assumptions |
| Radial root hydraulics | RSRR and conductivity, root class, assay conditions, intrinsic versus effective resistance |
| Axial hydraulics and anatomy | RVSR, root radius, conduit number/lumen fraction and ARSRA; distinguish anatomical radii from hydraulic equivalents |
| Water relations | Osmotic potential, turgor and stomatal/water-stress controls represented in the model |
| Nutrients and growth | N/P stoichiometry, uptake and growth/respiration yields; fixing species identity and fixation controls/rates where relevant |

Split a group when only some parameters have evidence: one Rubisco paper does
not validate all photosynthetic traits; a clover species list does not validate
fixation rates. Explicitly list the parameters searched, those supported and
those still pending.

Record PFT/taxa, parameters, search date, exact queries, source links/sections,
units and conversions, evidence level, root class/developmental stage and relevant
measurement support. Use `search_status` values `searched_found`,
`searched_no_applicable_evidence`, `not_searched`, or `not_applicable` (with reason).
Record the scientific conclusion separately: supported within stated limits,
provisional proxy/prior, unresolved, or contradicted. Do not fabricate a search
log retroactively. Where past work lacks a traceable search, mark that gap.

Search site species first, then explicitly labeled genus/family/functional
proxies. For hydraulics, search root hydraulic conductivity/resistivity,
xylem vessel or tracheid diameter, fine-root diameter/order, and water potential,
not merely generic “root traits.” Check temperature, species, root age/order,
units and whole-root versus surface-area normalization before mapping a value.
A broad cross-study screen or model geometry bound is not species evidence.

A comprehensive search effort is complete only after every relevant group has
been deliberately searched, with sources or a documented negative/inaccessible
result. That can still leave scientific support unresolved; search completion
does not imply all parameter values are supported. Use a bounded search and
report unresolved gaps instead of repeating queries indefinitely or inventing
correction targets. Offline-only reviews must state their restricted scope.

For a narrow user request, audit the requested trait(s), reuse prior current
evidence, and leave other groups explicitly outside scope/pending. Do not expand
a one-parameter correction into an unsolicited full calibration. When a source
or scope changes, update affected rows rather than treating old evidence as a
blanket validation.

## Report and handoff

Report screening counts, literature coverage, and unresolved assumptions
separately. Save the audit beside the evidence JSON and link it in the site HTML.
Use the guide's red warning/conflict title for missing coverage or unsupported
priors. Preserve parameter sources and uncertainty even if every screen passes.
Do not automatically retune a parameter to the nearest screening boundary.
Carry incomplete evidence into the next scientific review; mechanical preflight
and successful execution remain distinct from ecological validation. A run can
remain an explicitly documented baseline/sensitivity experiment without claiming
its unsupported parameters are calibrated.
