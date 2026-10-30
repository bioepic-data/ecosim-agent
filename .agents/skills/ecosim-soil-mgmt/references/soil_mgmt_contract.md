# EcoSIM Soil Management Contract

Use this reference for exact soil-management file layout, Excel editing schema, and EcoSIM reader behavior. The reader is `f90src/IOutils/ReadManagementMod.F90`; the bundled bridge is `scripts/soil_mgmt_excel_bridge.py`.

## NetCDF Layout

Core dimensions:

```text
ntopou      number of topo units
year        number of year records
string10    10 chars for selector names
nfert       fertilizer records per fertilizer table, max 12 in the reader
string128   128 chars for fertilizer/irrigation event rows
ntill       tillage records per tillage table
string24    24 chars for tillage event rows
nirri       irrigation records per irrigation table, max 24 in the reader
```

Required variables:

```text
year(year)
NH1(ntopou)
NV1(ntopou)
NH2(ntopou)
NV2(ntopou)
fertf(year, ntopou, string10)
tillf(year, ntopou, string10)
irrigf(year, ntopou, string10)
```

Selector behavior:

```text
NO       no management of that type
<name>   read NetCDF variable named <name>
auto*    irrigation selector beginning with auto triggers automated irrigation parsing
```

Check the actual branch's year-selection code: `ReadManagementFiles` normally
selects forcing year `yeari`; `soil_mgmt_use_model_year` can select simulation
year instead. Cover every selected year. The current reader stops when a year
is missing; legacy branches may lack that bound check.

## Fertilizer Rows

Fertilizer table variables are char arrays such as `fertf_2024(nfert, string128)`. The Fortran reader expects exactly 27 fields:

```text
DDMMYYYY
NH4Soil
NH3Soil
UreaSoil
NO3Soil
NH4Band
NH3Band
UreaBand
NO3Band
MonocalciumPhosphateSoil
MonocalciumPhosphateBand
hydroxyapatite
LimeStone
Gypsum
PlantResC
PlantResN
PlantResP
ManureC
ManureN
ManureP
AppDepth
BandWidth
PO4Soil
PO4Band
IsAmendtypFert
IsAmendtypResidual
IsAmendtypManure
```

Units and interpretation:

```text
N, P, C, Ca fields  g m-2
AppDepth            m
BandWidth           m
type flags          integer category flags
```

Useful conversion:

```text
1 g m-2 = 10 kg ha-1
```

The reader assigns these fields directly to `FERT(...)`, `FDPTH`, `ROWI`, and `IYTYP`.
These are daily slots: multiple fertilizer rows on the same date can overwrite
rather than add doses. Consolidate only after reconciling products, placement,
and source duplication; preserve the intended nutrient totals. A nonzero width
parameter alone does not populate band nutrient channels.

## Tillage Rows

Tillage variables are char arrays such as `tillf_2024(ntill, string24)`. Each row has:

```text
DDMMYYYY
iSoilDisturbType
DepzCorp
```

Reader meaning:

```text
iSoilDisturbType  1-20 tillage, 21 litter removal, 22 fire, 23-24 drainage
DepzCorp          intensity for fire or depth for tillage/drainage
```

In the current `DayMod.F90`, codes 1-10 give mixing fraction `code/10`, and
codes 11-20 give `(code-10)/10`, with the result bounded to [0, 1]. Thus codes
1 and 3 imply fractions 0.1 and 0.3; they are not fixed plow/disk identifiers.
Read the branch's process code before assigning implement proxies and keep
depth separate from mixing fraction. Document benchmark-derived proxies and
check same-day tillage rows for daily-slot overwrites.

## Irrigation Rows

Scheduled irrigation variables are char arrays such as `irrigf_2024(nirri, string128)`. Each scheduled row has:

```text
DDMMYYYY
RR
JST
JEN
WDPTH
PHQ
NH4
NO3
H2PO4
Al
Fe
Ca
Mg
Na
K
SO4
Cl
```

Reader meaning:

```text
RR       irrigation amount, mm over JST-JEN; reader converts to m h-1
JST/JEN  start/end hours
WDPTH    depth of irrigation application, m
PHQ      pH
ions     g m-3 in irrigation water; reader converts to molar concentration
```

`JST` and `JEN` are inclusive: require `1 <= JST <= JEN <= 24`, `RR >= 0`,
and hourly water rate `RR / (JEN-JST+1) / 1000` in m/h. The N and P ion fields
are elemental N/P concentrations: the reader divides NH4/NO3 by 14 and H2PO4
by 31. Include waterborne N in the budget:

```text
fertilizer N [kg/ha] = sum(eight soil/band N fields [g/m2]) * 10
irrigation N [kg/ha] = sum(RR [mm] * (NH4-N + NO3-N) [g/m3]) * 0.01
combined N = fertilizer-table N + irrigation-water N
```

Reconcile source events before adding channels so fertigation is not counted
twice. Distinguish calendar-year totals, autumn applications for the next crop,
and crop-season budgets.

The current reader adds hourly water pulses but stores chemistry and application
depth as daily properties. Nonzero same-day pulses must have identical pH,
ion concentrations, and application depth; conflicting properties stop the run.
If a volume-weighted daily concentration is chosen as a documented approximation,
use `sum(RR_i*C_i)/sum(RR_i)`, preserve water amounts/windows, and verify each
solute's daily mass. This changes pulse-level nutrient timing. Do not average
pH arithmetically or blend differing application depths without a justified
policy. Check the actual branch before relying on this behavior.

Automated irrigation is used when the selector begins with `auto`. The first row of the referenced variable has:

```text
DST
DEN
iIrrigOpt
FIRRA
CIRRA
DIRRA
WDPTH
PHQ
NH4
NO3
H2PO4
Al
Fe
Ca
Mg
Na
K
SO4
Cl
```

## Excel Editing Schema

Use `scripts/soil_mgmt_excel_bridge.py` to round-trip NetCDF, Excel, and JSON. The workbook has seven sheets:

```text
control
topo_units
year_selectors
event_files
fertilizer
tillage
irrigation
```

`control` columns:

```text
key
value
```

`topo_units` columns:

```text
topou
NH1
NV1
NH2
NV2
```

`year_selectors` columns:

```text
year
topou
fertf
tillf
irrigf
```

`event_files` columns:

```text
category
file
```

Use `category` values `fertilizer`, `tillage`, or `irrigation`. This sheet preserves event-table variables even when they currently have no rows, such as dormant `fertf_test` or `tillf_test` variables.

`fertilizer` columns:

```text
file
event_index
DDMMYYYY
NH4Soil
NH3Soil
UreaSoil
NO3Soil
NH4Band
NH3Band
UreaBand
NO3Band
MonocalciumPhosphateSoil
MonocalciumPhosphateBand
hydroxyapatite
LimeStone
Gypsum
PlantResC
PlantResN
PlantResP
ManureC
ManureN
ManureP
AppDepth
BandWidth
PO4Soil
PO4Band
IsAmendtypFert
IsAmendtypResidual
IsAmendtypManure
```

`tillage` columns:

```text
file
event_index
DDMMYYYY
iSoilDisturbType
DepzCorp
```

`irrigation` columns:

```text
file
event_index
mode
DDMMYYYY
DST
DEN
RR
JST
JEN
iIrrigOpt
FIRRA
CIRRA
DIRRA
WDPTH
PHQ
NH4
NO3
H2PO4
Al
Fe
Ca
Mg
Na
K
SO4
Cl
```

Use `mode=scheduled` for `DDMMYYYY/RR/JST/JEN` rows and `mode=auto` for `DST/DEN/iIrrigOpt/FIRRA/CIRRA/DIRRA` rows.

Common commands:

```bash
.venv-cmip6/bin/python .agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py inspect input.nc
.venv-cmip6/bin/python .agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py nc-to-xlsx input.nc editable.xlsx
.venv-cmip6/bin/python .agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py xlsx-to-json editable.xlsx edited.json
.venv-cmip6/bin/python .agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py xlsx-to-nc editable.xlsx edited.nc --json-output edited.json
.venv-cmip6/bin/python .agents/skills/ecosim-soil-mgmt/scripts/soil_mgmt_excel_bridge.py json-to-nc input.json output.nc
```

## Validation Notes

Check these before using the file in a run:

```text
selectors != NO point to existing variables
dates are valid DDMMYYYY
event date years match selector years unless deliberately yearless
annual N/P totals are plausible
no negative nutrient, depth, width, or irrigation values
record counts fit reader limits
```

The Fortran reader uses the event-string year for leap-year day-of-year conversion. A typo such as `07120090` will be treated as year 0090; it may land on the same day-of-year as the intended non-leap year, but it is still a data-quality issue.

## Fixed-width string compatibility

Write populated fixed-width text with ASCII space padding, never NUL padding.
Fortran `TRIM` removes trailing spaces, not NUL bytes. For a 10-character
inactive selector the correct bytes are `b'NO        '`, not
`b'NO\x00\x00\x00\x00\x00\x00\x00\x00'`.
The latter is not equal to `NO` in `ReadManagementFiles`, so EcoSIM attempts to
open a nonexistent tillage/fertilizer/irrigation table. A short active name may
also be misinterpreted depending on the called interface. NumPy fixed-byte
strings pad with NULs unless explicitly padded before conversion; the bridge's
`write_fixed_strlen` now pads all strings and rejects overlong values.

Inspect every `fertf`, `tillf` and `irrigf` record across all configured years
and topographic units, both in the final input and scratch Excel roundtrip.
Disable automatic masking so zero bytes cannot disappear as masked values.
Check that the space-trimmed selector is exactly `NO`, an applicable `auto*`
selector, or an existing NetCDF variable. Check event-row termination against
the consuming reader too; blank rows must be spaces where `len_trim` controls
the event loop. Verify width before casting, rather than silently truncating.

Example check (run with numpy and netCDF4; pass the selected soil file):

```python
import sys
import numpy as np
from netCDF4 import Dataset

with Dataset(sys.argv[1]) as ds:
    for name in ('fertf', 'tillf', 'irrigf'):
        var = ds[name]
        var.set_auto_mask(False)
        width = var.shape[-1]
        for index, row in enumerate(np.asarray(var[:]).reshape(-1, width)):
            raw = row.tobytes()
            assert b'\x00' not in raw, (name, index, raw)
            selector = raw.rstrip(b' ').decode('ascii')
            assert (selector == 'NO' or selector.startswith('auto')
                    or selector in ds.variables), (name, index, selector)
```

For a repair, archive the source file and failed run, normalize padding only,
and compare decoded configurations and numerical variables before/after.
Regenerate the editable workbook, repeat the byte check after its roundtrip,
and confirm event dates, rates and water/N/P totals are unchanged. Keep hashes
and affected variable/byte counts in the site provenance. Use
`scripts/test_string_padding.py` for the writer regression test.

Exercise a year transition when verifying the repair in EcoSIM. The US-Ne1
failure occurred only when the next year's inactive tillage selector was read;
one successful initialization or first year did not expose it. This example
is a reader-compatibility lesson, not permission to change management schedules.
