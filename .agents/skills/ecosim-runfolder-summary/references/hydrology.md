# Hydrology configuration interpretation

The helper's `hydrology` JSON object and Markdown section describe configured
processes and derived initial geometry. Use the current model source to resolve
differences from these mappings; source hashes record inspected files, not the
provenance of a compiled executable. No flow solve is performed.

## Connectivity and surface routing

`readimod.F90:GridConectionMode` maps `grid_mode=1` to all three directions,
`2` to north–south plus vertical, and `3` to vertical only. The reader assigns
it to `FlowDirIndicator_col`; `WatsubMod.F90` starts its direction loops there.
An omitted mode is unverified in the helper, rather than silently defaulting.
`first_topou` can restrict which units are loaded. Neighbor counts describe
enabled soil cell pairs, not the number or area of individual layer faces.

`readimod.F90` converts geographic aspect to geometric angle using
`(450-ASPX) mod 360`. `StartsMod.F90:InitGridElevation` computes:

```text
s = max(1.745e-4, tan(slope_degrees))
slope_EW = -s*cos(geometric_angle)
slope_NS =  s*sin(geometric_angle)
partition_EW = abs(slope_EW)/(abs(slope_EW)+abs(slope_NS))
partition_NS = 1-partition_EW
```

Negative directional slope means elevation falls east/south. The helper zeros
trigonometric roundoff below 1e-12 for cardinal directions. Actual runoff also
requires water, suitable head differences and direction flags. In
`SurfLitterPhysMod.F90:XGridsSurfRunoffM`, internal surface transfer uses those
partitions; `SurfPhysMod.F90:XBoundSurfaceRunoffM` multiplies by the external
surface coefficient. Surface routines have their own routing rules, so do not
infer that a soil-flow switch alone disables every surface process.

Always relate the aspect to the arrangement of cells. In a six-column,
one-row domain, every cell lies on both north and south boundaries. South-facing
SOAP therefore has surface routing across its southern boundary rather than
through successive columns. East-facing ERT has downhill neighbors along the
six-column sequence. Internal soil flow can run against the terrain slope
because its driving potential also contains matric and osmotic terms.

## External boundaries

For each grid, decode:

| Variable pattern | Meaning |
| --- | --- |
| RCHQ[N/E/S/W]G | External surface runoff/runon multiplier |
| RCHG[N/E/S/W]UG | External subsurface water-exchange multiplier |
| RCHG[N/E/S/W]TG | Lateral distance to external water table, m |
| RCHGDG | Bottom water-exchange multiplier |

Zero disables the corresponding exchange, while missing/masked data are
unverified. Nonzero values scale a process flux, not a fixed rate or a fraction
of total stored water removed every step. They do not multiply internal
cell-to-cell exchange. Do not label a nonzero face an unconditional outlet:
external recharge can also occur, and surface slope partitions may be zero.

`WatsubMod.F90:XBoundaryFlowM` selects external faces and dispatches runoff,
bottom flow, natural-water-table discharge/recharge, and tile drainage. Natural
water-table terms use distance plus 1 m in the denominator; report the supplied
distance separately from this equation convention. Bottom coefficient zero
closes bottom water exchange but does not disable vertical redistribution
inside the soil or imply an insulating heat boundary.

## Groundwater and initial geometry

`IDTBLG`: 0 none; 1 stationary natural; 2 mobile natural; 3 stationary
artificial; 4 mobile artificial. Report `DTBLIG` (natural reference depth),
`DTBLDIG` (artificial reference depth) and `DTBLGG` (water-table slope relative
to terrain). A stationary **external** table does not fix every soil cell's
internal water level; `fixWaterLevel` is a separate control.

`StartsMod.F90` integrates directional slopes across half-widths to derive
cell-center elevations. It uses the landscape minimum as the reference:

```text
initial_natural_depth = DTBLIG +
    (cell_center_elevation - landscape_minimum)*(1-DTBLGG)
```

`DTBLGG=0` gives a horizontal external table; `1` gives equal depth below
the terrain. Consequently, a 0.25 m reference depth can mean a much deeper
table beneath uphill cells. The helper derives initial terrain only for a
single grid, complete nonoverlapping selected rectangles, valid widths and
slopes, and at most 10000 cells. It derives natural table depth only for
natural modes and supplied soil cells. Missing geometry, ponds, artificial
tables or multiple grids remain unverified rather than extrapolated. These
values do not represent a restart, later erosion, or a mobile table's evolution.

## Soil hydraulics and controls

Report the active supplied layers' lateral/vertical saturated conductivity
`SCNH/SCNV` and their actual units, `FHOL` macropore fractions, `ROCK`, `FC/WP`,
and `THW/THI`. Negative hydraulic-limit inputs can request estimation. Input
conductivity is converted and modified in the model; it is not realized flow.
Zero `FHOL` supplies no macropore volume; also inspect `LMacPoreOff`.

`SoilHydroParaMod.F90` uses THW=1 for field capacity, 0 for wilting point,
>1 for saturation, <0 for dry, and interior values for volumetric water.
THI has analogous selectors limited by pore space left after liquid water.
Water-table saturation can override these selections. Preserve raw inputs
alongside the interpretation; THW=1 is not 100% volumetric water and THI=0
does not guarantee zero initialized ice.

Report explicit `fixWaterLevel`, `LMacPoreOff`, `use_ecosys_drainage`,
`lsoilCompaction`, `snowRedist_model`, `NPXS` and `NCYC_SNOW` when supplied.
Resolve omitted controls in the selected API if their actual values are needed.
The helper labels omitted drainage/fixed-level/macropore defaults unverified.
An enabled irrigation input needs the soil-management skill's event/automatic
irrigation interpretation, linked to this hydrology explanation.

## Acceptance examples

- SOAP: mode 1, five EW soil neighbor pairs, no NS neighbors; south surface
  partition 1 and EW partition 0. Relative center elevations equal; natural
  reference/local depths approximately 0.25 m. Boundary multipliers E/S 0.1,
  N/W/bottom 0; external E/S distance 20 m. Active FHOL=0; SCNH varies by layer.
- ERT: mode 1 and five EW neighbor pairs; east surface partition 1. Slopes
  47,47,47,47,16,12°. Relative elevations, west to east, approximately
  165.853,122.958,80.063,37.168,9.986,0 m; initial natural depths approximately
  166.103,123.208,80.313,37.418,10.236,0.25 m. Same boundary multipliers as SOAP,
  but east rather than south receives slope-partitioned runoff. SCNH=SCNV=2 mm/h
  in supplied layers and FHOL=0.

Recheck examples against current input files. These are configuration checks,
not evidence of plausible terrain, realized water flux, or scientific validity.
