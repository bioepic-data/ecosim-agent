# EcoSIM Plant Trait Sanity Rules

These checks are intentionally conservative. They catch values that are likely to break EcoSIM preprocessing, violate obvious unit/range expectations, or reveal a malformed block. They do not prove that a parameter is ecologically optimal for a given species.

Use web-informed evidence after these deterministic checks when judging whether a plausible numeric value is ecologically reasonable for the plant represented by the block.

## Block Scope

- One `PLANT traits for FUNCTIONAL TYPE` block is one plant record.
- The default scope is the first grid: `NY=1, NX=1`.
- Check all plant records at that grid, usually distinguished by `NZ` and PFT code.
- Do not infer that repeated blocks for other `NX` or `NY` values have been validated unless explicitly checked.

## Structural Checks

Required sections:

- `PLANT CLASS INFORMATION`
- `PHOTOSYNTHETIC PROPERTIES`
- `OPTICAL PROPERTIES`
- `PHENOLOGICAL PROPERTIES`
- `MORPHOLOGICAL PROPERTIES`
- `ROOT CHARACTERISTICS`
- `ROOT UPTAKE PARAMETERS`
- `WATER RELATIONS`
- `ORGAN GROWTH YIELDS`
- `ORGAN N AND P CONCENTRATIONS`

The parser should keep duplicate variable names in file order.

## High-Value Range Rules

- In `PLANT CLASS INFORMATION`, any block with `ISTYP` set to annual should have `IWTYP` set to evergreen. This is an EcoSIM convention: all annual plants are considered evergreen for the phenology-type field.
- `IEBTYP` should use EcoSIM embryophyte type codes `0=bryophyte`, `1=pteridophyte`, `2=gymnosperm`, `3=monocot`, and `4=eudicot`. The checker accepts either a valid integer code or a recognized label in `.desc` output, errors on missing, unrecognized, non-integer, or out-of-range values, and warns when the value conflicts with the inferred PFT form from the block code/name.
- `ISNTYP` should use EcoSIM snow interception pattern codes `0=bryophyte`, `1=grass`, `2=shrub`, `3=deciduous tree`, and `4=conifer`. The checker errors on missing, non-integer, or out-of-range numeric codes, and warns when the code conflicts with the label text or the inferred PFT form from the block code/name.
- Fraction traits such as `RUBP`, `PEPC`, `CHL`, `FCO2`, `ALBR`, `ALBP`, `TAUR`, `TAUP`, `CFI`, `PORT`, `PhiMIN`, `PhiMAX`, and `PhiMean` should be within `[0, 1]`. In photosynthetic properties, `CHL` is the fraction of total leaf protein in chlorophyll-bound/light-harvesting proteins, including chlorophyll-protein complexes associated with PSI, PSII, and LHC.
- `CHL` values in `PHOTOSYNTHETIC PROPERTIES` should normally fall in a broad screening range of `0.08-0.30` on the total-leaf-protein basis. Values below this range likely under-allocate protein to light harvesting, especially for evergreen needleleaf PFTs.
- Do not impose a universal deterministic range on `SLA1`. Allow a web-informed warning when a species- or site-specific range has been converted to `m2 gC-1` with a documented dry-mass carbon fraction. Do not use `SLA1` to derive leaf-area photosynthetic capacities unless the model mapping and conversions are explicitly supported.
- `CLASS` must contain four numeric inclination fractions, each in `[0, 1]`, summing to one.
- Optical checks should flag `ALBR + TAUR > 1` and `ALBP + TAUP > 1`.
- `ANGSH` must be `0 degrees` for plant forms that lack petiole or sheath tissue. This includes conifer, lichen, and moss PFTs where a nonzero petiole/sheath angle would imply an organ that is not represented by the plant morphology.
- Growth yields `DMLF`, `DMSHE`, `DMSTK`, `DMRSV`, `DMHSK`, `DMEAR`, `DMGR`, and `DMRT` should be positive and normally not exceed about `1.2`.
- Phenological accumulation thresholds `VRNLI` and `VRNXI` should be nonnegative; zero can encode no accumulated leafout or leafoff requirement.
- Organ N and P mass ratios (`CNLF`, `CNSHE`, `CNSTK`, `CNRSV`, `CNHSK`, `CNEAR`, `CNGR`, `CNRT`, `CPLF`, `CPSHE`, `CPSTK`, `CPRSV`, `CPHSK`, `CPEAR`, `CPGR`, `CPRT`) must be positive; values above `0.2 g element gC-1` are suspicious enough to warn.
- `OSMO` should be negative in MPa. Very negative values below about `-5 MPa` deserve a warning.
- `WTSTDI` should be nonnegative. It is standing dead biomass at initialization, so judge it against the simulation start date, cohort origin, and disturbance history rather than as a timeless species trait. A seed-origin run should normally start with zero PFT-specific standing dead biomass unless legacy material is intentionally represented.
- `KLGMAX` is retired and is not required for woody or non-woody PFT blocks. Do not flag its absence or use it to judge coarse-root lignification.

## Root Conduit Hydraulics

- Root-resistance behavior is code-version dependent. Verify these equations in the EcoSIM source revision that produced the run and record the commit or version when possible. If it cannot be verified, label resistance decomposition and scaling conclusions as version-unverified. Do not assume that parameter metadata or equations from another branch describe the active executable.
- Interpret `RSRR` as radial root resistivity per unit absorbing-root surface area in `MPa h m-1`. In the regular EcoSIM uptake calculation, `RootRadialResist = RSRR*(VLMicP/VLWatMicPM)/RootSurfaceArea`; therefore high `RSRR` means low water uptake capacity, and drying micropores increase the effective radial resistance further.
- Convert `RSRR` to the fully wetted intrinsic radial conductivity with `k_radial = 1/RSRR` in `m h-1 MPa-1`, or `k_radial = 1/(3600*RSRR)` in `m s-1 MPa-1`. Do not interpret conductivity-like NetCDF metadata as overriding the resistance semantics established by the model equation.
- For conifers, use `1000-10000 MPa h m-1` as a broad deterministic warning screen. This corresponds to `2.78e-8-2.78e-7 m s-1 MPa-1`. Field means for intact fine roots of four mature conifers were `0.51e-7-2.06e-7 m s-1 MPa-1` (https://doi.org/10.1016/j.rhisph.2022.100489). The screen is deliberately wider because intact-root measurements can include axial and interface resistance and are not identical to EcoSIM's intrinsic radial term.
- For non-conifers without species-level evidence, warn only when implied `k_radial` is outside the broad cross-study root hydraulic-conductivity envelope of `4.7e-9-1.2e-5 m s-1 MPa-1` (https://pmc.ncbi.nlm.nih.gov/articles/PMC10999368/). Prefer species-, organ-, age-, temperature-, and measurement-specific evidence whenever available.
- Never tune `RSRR` to compensate for implausible `RVSR` or `ARSRA`. Evaluate radial, axial, and soil resistance separately. If `RSRR` is high, inspect their modeled contributions during moist periods and use a sensitivity test before changing the trait.
- Interpret `RVSR` as the literal arithmetic mean root-conduit lumen radius in meters. Use tracheid lumen radius for conifers and vessel-element lumen radius for angiosperms. Never substitute an effective hydraulic radius.
- Convert measured diameter to `RVSR` with `RVSR = mean_lumen_diameter_m / 2`. Prefer species-level root measurements; do not silently substitute stem or hydraulically weighted diameters.
- Screen conifer `RVSR` at `2.5e-6-40e-6 m` radius, corresponding to the broad published `5-80 um` tracheid-diameter envelope (https://www.srs.fs.usda.gov/pubs/chap/chap_2015_domec_001.pdf). Treat this as a warning range and use species-specific web evidence to narrow it. Mature healthy ponderosa-pine roots measured near Burns, Oregon averaged `30.4 um` tracheid diameter, or about `15.2e-6 m` radius (https://doi.org/10.1093/treephys/18.5.333).
- In the inspected EcoSIM implementation, single-conduit resistance is `ARSRA * Rax_ref * (1e-6/RVSR)^4`, single-conduit area is `pi*RVSR^2`, and conduit count is `0.2*(RRAD2M/RVSR)^2`. Require the implied count to be at least one only after confirming that geometry in the active code version. With `RRAD2M` and `ARSRA` fixed, this implementation makes bundle axial resistance scale as `RVSR^-2`; do not generalize that scaling to another code revision without verification.
- Require `ARSRA > 0` and warn when `ARSRA < 1`, because pit, end-wall, and nonideal-flow effects cannot make an actual conduit less resistant than its ideal lumen. Use `ARSRA`, not an artificially small `RVSR`, to represent those additional axial resistances.

## Active Structural Protein Pools

- Relate total protein C to both total N and total P in active structural biomass. For an organ with structural N and P concentrations `N/C` and `P/C`, calculate the N-supported protein fraction as `(protein C:N) * (N/C)`, the P-supported fraction as `(protein C:P) * (P/C)`, and the realized protein C fraction as the smaller of the two.
- For leaves, all structural biomass is active. Use `leaf protein C / leaf structural C = min(CNWL*CNLF, CPWL*CPLF)`.
- For non-tree roots, all structural biomass is active. Use `root protein C / root structural C = min(CNWR*CNRT, CPWR*CPRT)` across the entire structural root pool.
- For tree and other woody-PFT roots, apply the same root relationship only to active structural biomass and exclude lignified heartwood. Separate `CNRTLIG` and `CPRTLIG` inputs are no longer required. A static trait file does not contain the dynamic active-to-heartwood C partition, so do not extrapolate the active-root protein fraction to the whole woody-root pool.
- Require the N/P-limited protein C fraction to be no greater than `1 gC protein gC structural biomass-1`. Warn when either single-nutrient-supported amount exceeds one even if the other nutrient keeps the realized pool below one.
- Interpret `CNWL`, `CPWL`, `CNWR`, and `CPWR` as protein-pool stoichiometry, not whole-organ elemental C:N or C:P ratios.

## Clean Photosynthetic Parameterization

Apply these as pathway-aware physiological warnings. Promote them to errors when `--strict-physiology` is requested, except for the model-formulation-sensitive `Jmax:Vcmax` diagnostic. The ranges are deliberately broad screening bounds, not cultivar-specific calibration targets.

- Require a recognizable `ICTYP` pathway and all inputs needed for the corresponding kinetic, allocation, capacity-ratio, and optical checks. Missing values must not silently disable strict physiology.
- Treat `VCMX`, `VOMX`, `XKCO2`, and `XKO2` as a Rubisco kinetic quartet. Check `VCMX/VOMX` against `6-14` for C4 and `2-8` for C3, and check implied specificity `VCMX*XKO2/(VOMX*XKCO2)` against `70-140`. This prevents an unrealistic turnover ratio from being hidden by compensating Km values.
- Screen C4 `XKCO2` at `10-35 uM`, `XKO2` at `120-400 uM`, and effective `XKCO24` at `0.5-20 uM`. Screen C3 `XKCO2` at `8-30 uM` and `XKO2` at `180-650 uM`. EcoSIM uses these as aqueous 25 C reference constants.
- Define photosynthesis-related allocations (`RUBP`, `PEPC`, and `CHL`) relative to total leaf protein C, not leaf structural C or total leaf N. First derive `leaf_protein_C_per_leaf_C = min(CNWL*CNLF, CPWL*CPLF)` and `effective_CNWL = leaf_protein_C_per_leaf_C/CNLF`. Then estimate enzyme nitrogen allocation with `fN_enzyme = allocation * effective_CNWL / 3.3`, using `3.3 gC protein gN-1` when enzyme-specific composition is unavailable. Screen Rubisco at `5-16%` of total leaf N for C4 and `8-30%` for C3. Screen C4 PEPC at `1-6%`; field maize commonly allocates about `2.5-3.5%`.
- Require `RUBP + CHL + PEPC <= 0.65` for C4 and `RUBP + CHL <= 0.65` for C3 because these fractions draw from one total-leaf-protein pool.
- For C4, calculate protein-normalized `Vpmax:Vcmax = VCMX4*PEPC/(VCMX*RUBP)` and screen at `0.8-2.5`.
- `CHL` and `ETMX` act only as a product in the C3 path. In `f90src/Plant_bgc/StomatesMod.F90` (verified 2026-10-12 at EcoSIM `f90src` commit `9d2af01a`), `MesophyllChlDensity = CHL * ProteinCLeafAreaDensity / 3.5` and `ElectronTransptJmaxRef = ETMX * MesophyllChlDensity`; `CHL` enters C3 photosynthesis nowhere else, and EcoSIM keeps no separate protein budget that a larger `CHL` would draw from. Jmax therefore scales with `ETMX*CHL`, and the two are not separately identifiable from model output. Changing `CHL` alone rescales Jmax by the same factor (e.g. 0.025 -> 0.19 is about 7.6x). Do not correct `CHL` in isolation: correct `CHL` and `ETMX` as a pair, taking `CHL` from a sourced light-harvesting protein fraction and setting `ETMX = (Jmax:Vcmax)*3.5*VCMX*RUBP/CHL` from a sourced Jmax:Vcmax at 25 oC, and record a user-chosen value as such. Literature light-harvesting fractions are usually shares of leaf N; convert to EcoSIM's total-leaf-protein basis before use. For C4, the mesophyll term adds `fCHLMESO` and the 3.7 conversion, and the bundle-sheath term uses `(1-fCHLMESO)/5.5`.
- **C3 tree `CHL` default (user rule, 2026-10-12):** for C3 trees (`ICTYP` C3 with a tree `pfts_short`: `ndlf`, `ndld`, `bdlf`, `bdln`, `bdlw`, `bspr`, `dfir`, `jpin`, `lpin`, `tasp`, `woak`), when the literature search finds no usable light-harvesting fraction, set `CHL = 0.12`. This is the one sanctioned exception to literature-backed values: record it as `user default`, not as literature, in the correction log and report. Because Jmax scales with `ETMX*CHL`, check the protein-normalized Jmax:Vcmax diagnostic after applying it and report the change in Jmax (for the `bdlf42` template, 0.025 -> 0.12 raises Jmax 4.8x, Jmax:Vcmax 0.51 -> 2.47). A sourced `CHL`/`ETMX` pair replaces the default when one is found. `scripts/trait_corrections.py` applies this default automatically (tree `pfts_short` and `ICTYP` = 3, no `CHL` range in the evidence) with evidence level `user-default`.
- Because the C3 `Jmax:Vcmax` diagnostic below uses exactly the code's `ETMX*CHL/3.5` against `VCMX*RUBP` per unit leaf protein, a value far below 1 (e.g. 0.51 for the shared `bdlf42` template) indicates electron transport capping light-limited photosynthesis, which matters more for GPP than the `CHL` screening error alone.
- Calculate protein-normalized `Jmax:Vcmax` using EcoSIM's chlorophyll-C conversion: `ETMX*CHL*fCHLMESO/(3.7*VCMX*RUBP)` for C4, screened at `4-8`; and `ETMX*CHL/(3.5*VCMX*RUBP)` for C3, screened at `1.2-3`. Treat this as a lower-priority, model-formulation-sensitive diagnostic that remains `WARN` in strict mode. Do not use it to override direct protein-allocation evidence for `CHL` or `RUBP`, and do not force agreement with ratios derived under a different photosynthesis formulation.
- Screen `FCO2` at `0.25-0.50` for C4 and `0.55-0.85` for C3. Screen C4 `fCHLMESO` at `0.40-0.75`.
- Screen leaf PAR absorptance `1-ALBP-TAUP` at `0.80-0.95` and broad shortwave absorptance `1-ALBR-TAUR` at `0.30-0.85`.

Use web evidence to narrow these ranges for a species, cultivar, nitrogen treatment, growth stage, and measurement protocol. Useful foundations include the corrected Rubisco kinetic compilation (https://doi.org/10.1093/jxb/erab383), maize nitrogen allocation (https://doi.org/10.3389/fpls.2016.00699), maize seasonal capacity ratios (https://doi.org/10.1111/pce.13511), and maize mesophyll/bundle-sheath chlorophyll partitioning (https://doi.org/10.1104/pp.51.6.1133).

## Organ N:C And P:C As Growth Stoichiometry

Verified 2026-10-28 at EcoSIM commit `5dc6fe57` (`PlantBranchMod.F90`, `GrosubsMod.F90`, `PlantBGCPars.F90`); recheck in the active revision.

- `CNLF` and `CPLF` are the maximum N:C and P:C of new leaf tissue, not fixed leaf concentrations. New leaf N:C is `CNLF*(ZPLFM + ZPLFD*CNPG)` with `ZPLFM = 0.33`, `ZPLFD = 0.67`, so realized leaf N:C ranges from `0.33*CNLF` (fully stressed) to `CNLF`. The same form applies to P.
- `CNPG = min(CNG, CPG)`, where `CNG = N/(N + C*CNKI)` and `CPG` likewise are computed from the leaf non-structural N, P and C pools against fixed constants. They are reported as `CanopyNLim_pft` and `CanopyPLim_pft`; 1 means no limitation. The source comment in `PlantBranchMod.F90` that calls them "[0->1] stronger limitation" is inverted.
- Growth is capped by `non-structural N * growth yield / N needed per unit new C`, and leaf terms of that requirement scale with `CNLF` (P: `CPLF`). A higher `CNLF` lets the same N pool support less growth and draws the non-structural pool down harder, which lowers `CNG` indirectly; `CNG` is not computed relative to `CNLF`.
- Leaf N remobilization keeps at least `0.33*CNLF*leaf C` in the leaf.
- Leaf protein C is `min(N*CNWL, P*CPWL)` of the leaf, and protein sets Rubisco, chlorophyll, Vcmax and Jmax. Lowering `CNLF` therefore lowers the maximum leaf protein and photosynthetic capacity per leaf area while easing N demand for leaf growth. Do not predict the net GPP effect of a `CNLF` change from either side alone; test it.
- Compare `CNLF` and `CPLF` with literature leaf N and P of well-supplied foliage (dry-mass values divided by the leaf C fraction), and compare realized output (`fLEAF_CN_pft`, `fLEAF_CP_pft`) with field leaf N and P. Before attributing low growth to a nutrient trait, check `CanopyNLim_pft` and `CanopyPLim_pft` and the soil nutrient inputs (initial soil P pools, fertilization).

## Shoot-Root Exchange (`PTSHT`)

- `PTSHT` (h-1) is the rate constant of a gradient-driven exchange of non-structural C, N and P between each branch and each root layer (`PlantNonstElmDynMod.F90`, commit `5dc6fe57`). C moves toward the organ with the lower non-structural C concentration; N and P move by their own gradients, with a transpiration-driven upward term. For annuals it is scaled by leaf allocation; for perennials it is used directly.
- `PTSHT` is not an allocation fraction. Raising it speeds up equilibration in both directions and need not increase root C. In one perennial C3 grass test, doubling it changed root C by +3% and fluxes negligibly.

## Thermal Adaptation Zone (`ZTYPI`)

- `ZTYPI` sets `TempOffset = 2.667*(2.5 - ZTYPI)` degrees C, added to canopy and soil temperature in the Arrhenius functions for carboxylation, oxygenation and electron transport (`StomatesMod.F90`), growth and respiration (`GrosubsMod.F90`), and node/leaf rates. It also sets the leaf-out threshold `5 - TempOffset` C (`InitPlantMod.F90`), the seed heat-stress limit, and branch leaf-out/off hour requirements. Zone 2 (cool temperate) is +1.33 C; zone 3 (warm temperate) is -1.33 C. Changing it shifts all temperature responses together; it does not add or remove a vernalization requirement.

## Floral Initiation In Evergreen Perennial Grasses

- For an evergreen perennial (`IWTYP` evergreen, `ISTYP` perennial), floral initiation (`InitiateBranchFloral`, `PlantPhenolMod.F90`, commit `5dc6fe57`) requires `ShootNodeNum > MatureGroup + ShootNodeNumAtInitFloral`, with `MatureGroup = GROUPX - XTLI` (both divided by 25 when `IBTYP != 0`), plus leaf-out completion and, for long-day plants, `max(XDL - daylength, 0) < XPPD`. `XDL < 0` is replaced by the site maximum daylength.
- `ShootNodeNumAtInitFloral` is reset to the current node number at every spring leaf-out (`ResetNonAnnualBranch`, `PlantBranchMod.F90`) and at every cut above the cutting height (`ResetCutBranch`, `PlantDisturbsMod.F90`). There is no vernalization anywhere in `f90src`. Cool-season grasses that are induced over winter and head in spring therefore cannot be reproduced by `GROUPX`, `XRNI` or `XDL` alone: high `GROUPX` gives no flowering, intermediate values give initiation only in long gaps between summer cuts, and low values allow repeated summer re-initiation. Treat this as a model-design limit, not a trait error; report it rather than tuning traits to force a flowering date.
- The cold-deciduous perennial path has a separate seasonal trigger (shortening days). Do not switch an evergreen grass to deciduous to obtain it: that also imposes autumn leaf-off and removes winter photosynthesis.

## Woody Versus Herbaceous Root Blocks

Woody short codes include common EcoSIM trees and shrubs such as `ndlf`, `ndld`, `bdlf`, `bdln`, `bdlw`, `bspr`, `dfir`, `jpin`, `lpin`, `tasp`, `woak`, `shru`, `bush`, and `busn`.

Woody blocks should include:

- `ROOTMAGE`
- `PhiMIN`
- `PhiMAX`
- `R95MAT`

Herbaceous blocks should include `PhiMean` unless both `PhiMIN` and `PhiMAX`
are defined. When the root maturation pair `PhiMIN`/`PhiMAX` is present,
`PhiMean` is not required.
Exception: soybean (`soyb*`) is a eudicot crop that can express secondary root
growth, so `ROOTMAGE`, `PhiMIN`, `PhiMAX`, and `R95MAT` are allowed for soybean
without issuing the generic non-woody root-trait warning. Still check their
numeric bounds and `PhiMIN <= PhiMAX`.

## Interpretation

Treat `ERROR` findings as likely malformed or numerically invalid input. Treat `WARN` findings as requiring domain review. A warning can be ecologically defensible, but it should be traceable to a species-specific source, deliberate parameterization, or documented EcoSIM convention.
