# Cropland soil and rooting depth

The user specified on 30 October 2026 that cropland cases use a maximum soil
depth of 3 m and a maximum root-accessible layer supported by literature.
Apply this to newly prepared or recreated crop cases. Keep shallower measured
bedrock or restrictive horizons visible; do not present 3 m as a measured depth.
Do not silently rewrite other existing cases or their restart states.

Find crop/site or region-specific evidence for **total maximum rooting depth**.
Effective irrigation rooting depth describes the zone containing most uptake;
it is not interchangeable with the deepest roots. Separate an observed maximum,
a sampling-depth limit, and a typical agronomic range. Prefer relevant site
measurements; otherwise document the regional/species prior and uncertainty.
When choosing the upper end of a reported range, identify that modeling choice.
Do not copy a numerical rooting depth from another crop or site without review.

NJ is a layer index, not a depth in meters. Place a layer boundary at the chosen
root limit, and set NJ to that layer. For a rotating-crop column, NJ is shared;
document each crop's evidence and whether a common bound limits every crop or
only permits the deepest-rooted crop. Do not claim separate per-crop caps from
one NJ value. Split/remap layers conservatively, preserving areal pools and
soil-mass-weighted concentrations; rerun soil-volume, stock and workbook checks.

Trace the selected reader and initialization before declaring the cap applied:

- `readimod.F90`: supplied layers = NJ+NL1; active layers = NJ+NL1+NL2.
- `StartsMod.F90::startsim`: currently regenerates layers below NJ+1 using
  `SetDeepSoil`, even when more layers were supplied in the input file.
- `SoilPhysParaMod.F90::SetDeepSoil`: extrapolates depth from the last two
  boundaries. Setting CDPTH alone can therefore leave a deeper active layer or
  change a supplied bottom boundary at runtime.

A compatible layout is one supplied non-root layer ending at 3 m (`NL1=1`,
`NL2=0`). Retain the required allocated nlevs/JZ capacity but mark unused input
slots as padding. If using a different layout, verify the actual initialized
active depths rather than assuming the input geometry is preserved. Report NJ,
NL1, NL2, root-boundary depth, active bottom and remaining deep-soil assumptions.

For US-Ne2, Nebraska Extension G2245 Table II reports total mature maize and
soybean rooting depths of 5–6 ft under well-drained deep silt-loam conditions:
https://extensionpubs.unl.edu/publication/g2245/2014/html/view
The recreated case selects 6 ft × 0.3048 = 1.8288 m for both crops. This is a
regional bound, not a site root survey or a universal cropland rooting depth.
