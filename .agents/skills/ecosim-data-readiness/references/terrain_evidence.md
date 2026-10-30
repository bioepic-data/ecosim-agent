# Terrain evidence and inherited grid values

Use before accepting or changing slope, aspect, grid geometry or directional
runoff boundaries. Numerical validity and successful execution do not establish
that reference/template values belong to the site.

## Evidence and spatial support

- Inspect site-filtered BIF terrain records, site/operator descriptions and
  primary field papers before retaining a reference value. Keep source versions,
  locations, units, measurement/derivation methods and footprint support. If the
  task needs terrain beyond published evidence, use a suitable site-scale DEM
  or field survey; record its resolution, vertical datum, method and uncertainty.
- Separate slope magnitude from downslope direction. Text such as "gentle
  slope (<2%)", "flat" or "very little slope" does not establish an aspect.
  Convert grade to angle using `degrees(atan(percent/100))`; an upper bound is
  not an exact measured slope. Distinguish a two-field mean from a tower-point
  or individual-field value before declaring agreement or a contradiction.
- Distinguish field location ("west field"), crop-row orientation, wind direction
  and a generic soil-series type-pedon aspect from the site's downslope aspect.
  Likewise, boundaries configured to allow eastward runoff are not independent
  evidence of an east-facing field.
- Aspect is circular: compare directions modulo 360, not by ordinary arithmetic
  means. For DEM aggregation, state the footprint and method; near-flat/noisy or
  directionally variable terrain may not support one reliable representative
  aspect. Do not turn a DEM flat/no-data code into a physical angle.

## Trace the actual input convention

Read the selected namelist/grid and follow the current reader into terrain,
radiation and runoff consumers. `ASPX` uses clockwise compass degrees from
north; the current `readimod.F90` converts it to geometric east-zero,
counterclockwise degrees before `StartsMod.F90` and `SurfaceRadiationMod.F90`
use it. Check that conversion before diagnosing a 90-degree rotation from
downstream formulas or runtime logs alone. Correct interpretation proves the
code convention, not site correctness. Check the active implementation when
using a different driver or coupling interface.

## Retain uncertainty instead of laundering a reference value

- Identify each supplied parameter as observed, mapped/DEM-derived, fitted,
  explicitly assumed or inherited from a template. A generic `source=site_metadata`
  label is insufficient without the underlying evidence. Document substantive
  sources in variable metadata and the provenance report; keep uncertainty and
  assumptions separate from source identity.
- If no source establishes aspect, flag the existing value as provisional and
  explain what radiation/runoff assumptions it imposes. Do not silently replace
  it with another angle or set slope to zero as a substitute for missing evidence.
  Continue independent preparation with the limitation visible, within the user's
  authorized scope; missing evidence does not create a blanket approval gate.
- The generator's aspect placeholder is tagged `template_default_unverified`.
  The shared conflict tool reports `aspect_unverified` for missing or provisional
  provenance and rejects out-of-range angles. A substantive source string only
  clears this provenance-presence check; it is not scientific verification. Do
  not relabel a default as a field source merely to clear a warning.
- Review slope, aspect, grid dimensions and directional surface/subsurface
  boundaries together before applying a justified correction. Preserve originals,
  regenerate the workbook, verify the reader convention and affected geometry,
  refresh the conflict report/HTML and mark previous runs as historical. Do not
  change site inputs during a literature-only review.

Apply the same source question to other borrowed grid values: identify the
reference ecosystem, parameter meaning, applicable units/domain and site
evidence before accepting them. A reference is a candidate prior, not an
observation of the new site.
