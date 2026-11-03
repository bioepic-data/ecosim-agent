# Required review of nitrogen-fixing vegetation

Apply this review before choosing the PFT composition of a new site vegetation
package or revising its ecological representation. A mechanical Excel/NetCDF
conversion that preserves composition can retain the existing review. If none
exists, report that gap; do not invent a review or silently change composition.

## Search deliberately

Read local site metadata, BADM/BIF, species surveys and previous case provenance,
then search the web for the site ID, full site name and aliases with combinations
of `legume`, `clover`, `Trifolium`, `nitrogen fixation`, `N2 fixation`,
`biological nitrogen fixation`, `species composition` and `model`. Adapt taxon
terms to the ecosystem, including fixing shrubs/trees and actinorhizal plants
where relevant. A broad land-cover class is not a complete species inventory.

Check primary site papers and **modeling studies**, including their methods,
supplements, parameter tables and available input files. Identify whether they
used a separate fixing PFT, prescribed fixation, a productivity-based fixation
function, or omitted fixation. Do not infer that a model activated fixation at
this site merely because its general equations support it. Search accessible
theses or author repositories when primary methods/supplements are missing;
retain unverified leads separately from verified evidence.

A useful review covers a field/site description and a targeted modeling search,
or records why either source class could not be found/accessed. Record the
search date, queries, sources reviewed and unresolved gaps. One failed query,
a brief species list, or a missing BIF field does not establish absence.

## Assess significance without inventing a threshold

Distinguish the following evidence:

- **Presence:** a fixing taxon is reported at the site. Confirm fixation
  capability from a reliable taxon/process source; not every forb fixes N.
- **Community importance:** measured cover, biomass, density or frequency;
  descriptions of a major community component; interannual changes such as
  clover-rich years; or evidence of an important contribution to ecosystem N.
  Record the observation period, season, footprint and spatial support. Do not
  interpret a list of several dominant species as equal abundance fractions.
- **Fixation magnitude:** measured flux, method and units, distinguishing
  per-fixer-area from whole-site rates and seasonal from annual totals.
  Foliar N, clover presence or a modeled N input is not a measured fixation flux.
- **Model representation:** explicitly separated fixing/non-fixing populations
  or an assumed fixation input. This can justify a modeling choice but does
  not establish observed abundance or validate the simulated fixation rate.

Report presence and significance separately: for example, presence confirmed
with importance quantified, importance supported qualitatively, or importance
unresolved. If the search finds no positive evidence, say **not found in sources
reviewed**, not absent. Do not impose a universal cover threshold: a minority
fixer population can affect the N budget, and its contribution can vary by year.

## Carry the evidence into vegetation inputs

When evidence supports an important fixer component, represent it with a
separate, validated fixing PFT where the model supports that vegetation type.
Do not collapse it into a non-fixing grass/tree merely because the site is
classified as grassland/forest. If the chosen configuration cannot represent it,
record the limitation and its implications for N limitation and productivity.
Where presence is confirmed but significance remains unresolved, explicitly
justify the baseline choice and, when useful, prepare a sensitivity case with
clearly labeled assumptions. Do not assign a universal abundance or fixation
rate from species presence alone.

Validate annual/perennial life cycle, photosynthetic pathway, climate suffix
and fixation controls against the **selected PFT table and active reader/enums**.
Verify the resulting fixation identity in the generated plant-trait output;
a name containing “clover” is not a substitute for active parameter checks.
Follow the existing smoke/trait workflow for newly activated PFTs. Correct
severe parameter findings only with verified numerical evidence; otherwise
retain the finding and the appropriate full-run readiness block.

Retain measured or user-specified initial densities, with units and provenance.
Do not equate density ratios with cover or biomass fractions, prescribe observed
community shares from a species list, or apply a pure-stand fixation rate to the
whole mixed site. Label borrowed establishment dates/depths as model priors.
Constant management (`pft_dflag=0`) does not impose constant vegetation shares
or constant N fixation; annual regeneration and competition remain model
processes. Respect explicit user choices and do not invent fertilizer, grazing
or reseeding schedules to compensate for an omitted N-fixer population.

## Required record and site guide

Save the review in the site's vegetation/management provenance sidecar, alongside
its JSON, NetCDF and workbook. Include taxa, presence/importance verdicts,
quantitative values with units/support when available, exact citations/sections,
model assumptions, chosen PFTs and fixation flags, initial densities, omitted
components and unresolved evidence. If numerical fractions/rates are unavailable,
say so explicitly. Summarize the decision in the site HTML. Put unresolved
importance, uncertain abundance or omitted fixation under a **red warning/conflict
title** using the guide's existing warning styling.

For example, at US-Var, field literature lists *Trifolium hirtum* and *T. dubium*
([Ryu et al., 2008](https://doi.org/10.1029/2007JD009263)), and a site-specific
ecosys study explicitly uses competing annual grass and symbiotically fixing
annual legume PFTs ([Grant et al., 2012, section 2.2.2](https://doi.org/10.1016/j.agrformet.2011.09.012)).
That modeling precedent adds information beyond the site's grassland label.
It does not supply an observed clover fraction or measured annual fixation flux.
The US-Var user-prescribed one-third clover:grass seeding-density ratio is a
case-specific initialization, **not a default for other sites**.
