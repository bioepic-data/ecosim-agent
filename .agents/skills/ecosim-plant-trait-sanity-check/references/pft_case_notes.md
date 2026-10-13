# Retained PFT And Site Case Notes

Use these notes as evidence-aware starting points, not universal hard bounds. A six-character EcoSIM PFT code identifies a functional type and climate class; it does not uniquely identify a species. Reconfirm the represented species, stand age, initialization year, and disturbance history before transferring a value.

## `ndlf35` Ponderosa-Pine Cases

- Root-resistance semantics and formulas are code-version dependent. Record the EcoSIM source commit or version used for a run and verify the active resistance equations before reusing the hydraulic interpretations below.
- For ponderosa pine, `RVSR = 15.2e-6 m` is a defensible species-level mean root-tracheid lumen radius. It comes from a measured mean diameter of `30.4 um`; EcoSIM requires the literal radius. Source: https://doi.org/10.1093/treephys/18.5.333.
- `RSRR = 5000 MPa h m-1` is a defensible sensitivity starting value for conifer parameterization, not a measured ponderosa-pine constant. It implies fully wetted `k_radial = 5.56e-8 m s-1 MPa-1`. Always report the conversion and inspect modeled radial, axial, and soil resistance contributions.
- In the inspected parallel-conduit implementation, conduit number is proportional to lumen area divided by individual conduit area. Holding `RRAD2M` and `ARSRA` fixed therefore makes root-bundle axial resistance scale as `RVSR^-2`, even though single-conduit Hagen-Poiseuille resistance scales as `RVSR^-4`. Reconfirm this in the source revision used by the simulation.
- Use `ARSRA`, not an artificially small `RVSR`, for pit, end-wall, and nonideal-flow resistance.
- `ANGSH = 0` is appropriate because conifer needles do not use the modeled petiole/sheath organ.
- `KLGMAX` is retired. `CNRTLIG` and `CPRTLIG` are also no longer required; tree root protein relationships apply only to active structural roots and exclude lignified heartwood.
- The US-Me2 project selected `ZTYPI = 2.0` for its cool-temperate ponderosa-pine configuration. Treat this as a site configuration, not a universal `ndlf35` constant.

## Age-Conditioned Output Diagnosis

- When all plants emerge from seed in 1930, evaluate first-year GPP, LAI, height, biomass, and root depth against seedlings or very young cohorts. Mature AmeriFlux stand totals are not direct first-year targets.
- Diagnose photosynthetic limitation with both `dCAN_GPP_CLIM_pft` and `dCAN_GPP_eLIM_pft`. Their separation helps distinguish climate limitation from nutrient/energy limitation, but high first-year GPP can also result from excessive leaf deployment or carbon allocation rather than `VCMX` alone.
- Treat `WTSTDI` as a dated initial-condition pool. Modern snag observations do not justify importing that biomass into a 1930 seed-origin initialization unless legacy dead wood is an explicit scenario assumption.

## US-xSP/SOAP `ndlf35` And `woak35`

- SOAP's modern standing-dead pool is substantial but is dominated by ponderosa pine and other conifers following drought and bark-beetle mortality. Broadleaf oak mortality was generally much lower. Allocate a contemporary snag pool primarily to the represented conifer PFT unless species-resolved inventory supports an oak pool. Sources: https://www.neonscience.org/field-sites/soap and https://pmc.ncbi.nlm.nih.gov/articles/PMC12079731/.
- `woak35` at SOAP may aggregate deciduous California black oak (`Quercus kelloggii`) and evergreen canyon live oak (`Quercus chrysolepis`), which have materially different SLA. Do not use one species as the site-average target without composition weighting.
- Canyon live oak LMA of `187.6 g dry mass m-2` implies SLA near `0.011-0.012 m2 gC-1` for leaf carbon fractions of `0.45-0.48`. Source: https://doi.org/10.1111/nph.14406.
- A synthesis reports California black oak SLA of `9.8 +/- 1.9 m2 kg-1 dry mass`, corresponding to roughly `0.020-0.022 m2 gC-1` at the same carbon fractions. Source: https://doi.org/10.1029/2010GB003942.
- If species composition is unresolved, `SLA1 = 0.018 m2 gC-1` is a practical mixed-oak starting value, with `0.016-0.020 m2 gC-1` as a sensitivity range. This is an inference from the two species, not a direct SOAP measurement. Retune when species-resolved canopy or leaf-mass fractions are available.

## US-UMB `bdlf42` (University of Michigan Biological Station)

- Canopy (AmeriFlux BADM 2010 overstory): Populus grandidentata 39%, Quercus rubra 17%, Acer rubrum 15%, Populus tremuloides 13%, Betula papyrifera 8%, minor Fagus grandifolia, Acer saccharum, Pinus strobus. Site LMA (1998-99) gives canopy-weighted SLA 0.056-0.068 m2 gC-1; site foliage N:C 0.040-0.043.
- The shared `bdlf42` template uses `CHL = 0.025` and `ETMX = 405` (as do most `bdlf*` records; 62 of 83 PFTs in `ecosim_pftpar_20260915.nc` have `CHL < 0.08`), giving a C3 protein-normalized Jmax:Vcmax of 0.51 with `VCMX = 45`, `RUBP = 0.125`. With those `ETMX`, `VCMX`, and `RUBP`, the diagnostic is about `20.6*CHL`, so `CHL` in 0.058-0.146 satisfies Jmax:Vcmax 1.2-3, and only 0.08-0.146 also satisfies the 0.08-0.30 `CHL` screen.
- No sourced light-harvesting fraction or Jmax:Vcmax at 25 oC was verified by web search (2026-10-12; candidate full texts: Evans 1989, Evans & Poorter 2001, Niinemets & Tenhunen 1997 for Acer saccharum, Kattge & Knorr 2007, Wullschleger 1993), so the correction loop left `CHL` unresolved.
- 2026-10-12: by user instruction, `CHL` was set to 0.12 for `bdlf42` in the US-UMB site copy `result/US-UMB/input/US-UMB_ecosim_pftpar.nc` only (ETMX unchanged). This raises Jmax 4.8x (Jmax:Vcmax 0.51 -> 2.47). It is a user-specified value, not literature-backed; revisit as a CHL/ETMX pair once the sources above are verified, and check modeled GPP and light response against US-UMB tower GPP (2007-2021).

