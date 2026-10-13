# Web Evidence Workflow

Use web evidence to catch ecologically implausible parameters that cannot be judged from file structure alone. Every web-informed finding should be traceable to a source URL, evidence level, and unit conversion note.

## Source Priority

Use sources in this order:

1. Species-specific peer-reviewed papers, government plant guides, or curated species profiles for identity, life form, photosynthetic pathway, phenology, rooting habit, nitrogen fixation, and plant architecture.
2. Trait databases with standardized units and attribution, especially TRY, BIEN, LEDA, ORNL DAAC photosynthesis datasets, LeafWeb, and Global Spectra-Trait Initiative data.
3. Genus-, family-, or plant-functional-type syntheses when species evidence is missing.
4. Extension, restoration, or botanical garden summaries only as supporting evidence.

Useful starting points:

- TRY Data Portal: https://www.try-db.org/TryWeb/Database.php
- Global Spectrum of Plant Form and Function dataset: https://doi.org/10.17871/TRY.81
- BIEN trait access documentation: https://rdrr.io/cran/BIEN/f/inst/doc/BIEN_tutorial.Rmd
- LEDA Traitbase: https://uol.de/en/landeco/research/leda
- ORNL DAAC global photosynthesis and leaf trait dataset: https://doi.org/10.3334/ORNLDAAC/1224
- LeafWeb: https://www.leafweb.org/information/about/
- Global Spectra-Trait Initiative: https://doi.org/10.15485/2530733
- USDA PLANTS and NRCS Plant Guides for US species profiles: https://plants.usda.gov/

## Site Vegetation First

When the check serves a site run, the evidence must follow the site's actual
vegetation, not a generic PFT description:

1. Extract the site's species composition and trait measurements from the
   AmeriFlux BADM/BIF (`scripts/site_vegetation.py extract --site-id <SITE>`;
   the site BIF, or the network `AMF_AA-Flx_BIF_*.xlsx` filtered to the site,
   staged under `data/` (e.g. `data/AMF_AA-Net_BIF_CCBY4_20250828.xlsx`, the 615-site superset) or passed with `--bif`). This records the dominant
   species with canopy shares (`SPP_O_PERC`, else basal area), site LMA and
   foliage chemistry by species, the UTC offset, and snag mass.
2. Use evidence in this priority order and record the level on every entry:
   `site` (measurements at the site: BADM/BIF variables or studies at that
   site) > `species` (the site's dominant species) > `genus` > `functional-type`.
   Search each dominant species by name before falling back.
3. Convert site measurements automatically with `site_vegetation.py
   site-evidence` (LMA -> `SLA1`, foliage N:C -> `CNLF`, both per species).
4. Store per-taxon literature values in a range's `species_values` list
   (`taxon`, `min`, `max`, `source`, `url`, `evidence_level`, `conversion_note`)
   and combine them by canopy share with `site_vegetation.py weight`
   (weighted over the covered canopy; kept pooled if coverage is under 50%).
5. Label evidence that is not about the site's taxa, such as generic C3
   Rubisco kinetics, as `functional-type`, and record the region of studies
   from elsewhere in `conversion_note`.
6. Run `site_vegetation.py lint` before using the evidence; it requires a
   valid level, source, URL, and conversion note, site taxa in
   `species_values`, and site-level evidence where site measurements exist.

## Search Pattern

For each selected first-grid plant block:

1. Record the EcoSIM code, `NZ`, plant name, and the site's dominant species with canopy shares (`<SITE>_site_vegetation.json`).
2. Resolve the accepted scientific names. If the block is generic, such as `C3 grass perennial`, and no site species list exists, keep the evidence level as `functional-type`.
3. Search targeted terms for each dominant species first:
   - `<taxon> leaf nitrogen phosphorus concentration`
   - `<taxon> Vcmax Jmax`
   - `<taxon> rooting depth root traits`
   - `<taxon> photosynthetic pathway`
   - `<taxon> phenology evergreen deciduous annual perennial`
4. Prefer sources with measurement units, sample size, geography, and citation.
5. Convert only traits that can be defensibly mapped to EcoSIM variables.

For annual plants, do not add a web-evidence expectation that `IWTYP` should be deciduous even when a botanical source describes the foliage that way. EcoSIM treats all annual plants as evergreen in the phenology-type field.

`SLA1` may be added to web evidence when the source is species- or site-specific. EcoSIM uses `m2 gC-1`; when a source reports `m2 kg-1 dry mass`, divide by the documented leaf carbon fraction and by `1000` to obtain `m2 gC-1`. State the carbon fraction and whether the value represents sun leaves, shade leaves, seedlings, or a canopy average. Do not use `SLA1` to derive leaf-area photosynthetic capacities for sanity-check findings. For photosynthetic properties, treat `CHL` as the fraction of total leaf protein in chlorophyll-bound/light-harvesting proteins, including chlorophyll-protein complexes associated with PSI, PSII, and LHC.

## Unit Conversion Notes

EcoSIM units often differ from trait database units. Document every conversion in `conversion_note`.

- `CNLF` is `gN gC-1`. If a source gives leaf N as `mg gDM-1`, convert with `CNLF = (leaf_N_mg_gDM / 1000) / carbon_fraction`.
- `CPLF` is `gP gC-1`. If a source gives leaf P as `mg gDM-1`, convert with `CPLF = (leaf_P_mg_gDM / 1000) / carbon_fraction`.
- `CNWL` and `CPWL` relate total leaf protein C to total leaf N and P, respectively. Because all leaf structural biomass is active, derive `leaf_protein_C_per_leaf_C = min(CNWL*CNLF, CPWL*CPLF)`; the smaller term is the nutrient-supported protein pool available to photosynthetic allocations.
- `CNWR` and `CPWR` analogously relate root protein C to N and P in active structural roots. For non-trees, all root structural biomass is active, so use `min(CNWR*CNRT, CPWR*CPRT)` for the whole structural root pool. For trees and other woody PFTs, apply the relationship only to active roots and exclude lignified heartwood; separate `CNRTLIG` and `CPRTLIG` inputs are not required, and the active-root protein fraction must not be extrapolated to the whole woody-root pool without a measured or modeled active-to-heartwood C partition.
- `GRDM` is seed carbon mass in `gC seed-1`. Convert dry seed mass with `GRDM = seed_mass_gDM * carbon_fraction`.
- `RSRR` is radial root resistivity in `MPa h m-1`. Prefer measurements reported as root-surface-area-normalized radial hydraulic conductivity. Convert conductivity `k_radial` in `m s-1 MPa-1` with `RSRR = 1/(3600*k_radial)`. Record whether the source measured intrinsic radial conductivity, intact fine-root hydraulic conductivity, whole-root conductance, or axial xylem conductivity; these are not interchangeable. Do not use stem or axial conductivity as direct evidence for `RSRR`.
- `RVSR` is the literal arithmetic mean lumen radius of root water conduits in meters. Search for root tracheid lumen diameter in conifers and root vessel-element lumen diameter in angiosperms, then divide diameter by two and convert micrometers to meters. Prefer species-level root anatomy. If only stem anatomy or a hydraulically weighted diameter is available, label it as indirect evidence in `conversion_note`; do not present it as a direct conversion. Keep pit and end-wall resistance in `ARSRA` rather than shrinking `RVSR`.
- `PEPC`, `RUBP`, and `CHL` are fractions of total leaf protein C. If a source reports the fraction of total leaf N allocated to PEPC, map it with `PEPC = fN_PEPC * protein_C_to_N / effective_CNWL`, where `effective_CNWL = min(CNWL*CNLF, CPWL*CPLF)/CNLF`. Use a measured protein C:N ratio when available; otherwise document the screening assumption of `3.3 gC gN-1`. Conversely, the checker estimates `fN_PEPC = PEPC * effective_CNWL / 3.3` for C4 blocks.
- Map Rubisco nitrogen allocation in the same way. Do not judge `RUBP`, `PEPC`, or `CHL` independently of the shared, N/P-limited total leaf-protein pool.
- When a source reports leaf-area `Vpmax:Vcmax` or `Jmax:Vcmax`, compare it to the corresponding protein-normalized EcoSIM ratio only as a screening constraint. Treat `Jmax:Vcmax` as lower priority than direct protein-allocation evidence because the ratio depends on the photosynthesis model formulation. Record the source growth stage and temperature normalization; do not present the ratio as a direct unit conversion.
- Evaluate `VCMX`, `VOMX`, `XKCO2`, and `XKO2` as one kinetic set. Preserve both a plausible carboxylation:oxygenation turnover ratio and a plausible implied specificity; matching specificity alone does not make compensating Km and turnover values physiological.
- Web `Vcmax25` is commonly leaf-area based (`umol m-2 s-1`), while EcoSIM `VCMX` is Rubisco-C based (`umol CO2 (gC rubisco)-1 s-1`). Do not compare these directly unless the conversion is explicitly supported by additional Rubisco or leaf carbon information.

## Evidence JSON

Save web evidence as JSON and pass it to the checker with `--web-evidence`. Numeric ranges must already be in EcoSIM units.

```json
{
  "plants": [
    {
      "pft_code": "ndlf35",
      "nz": 1,
      "ny": 1,
      "nx": 1,
      "taxon": "generic evergreen needleleaf tree",
      "numeric_ranges": [
        {
          "variable": "RUBP",
          "min": 0.05,
          "max": 0.35,
          "unit": "gC rubisco gC protein-1",
          "source": "functional-type protein-allocation synthesis",
          "url": "https://www.try-db.org/TryWeb/Database.php",
          "evidence_level": "functional-type",
          "conversion_note": "Range must match EcoSIM's total-leaf-protein basis."
        }
      ],
      "categorical_expectations": [
        {
          "variable": "ICTYP",
          "contains": "C3",
          "source": "functional-type identity",
          "url": "https://www.try-db.org/TryWeb/Database.php",
          "evidence_level": "functional-type"
        }
      ]
    }
  ]
}
```

## Interpretation

Web evidence is contextual, not absolute. Treat species-level, measured, site-relevant evidence as strongest. Treat global trait-database summaries and plant-functional-type ranges as screening ranges that should generate warnings, not automatic edits.
