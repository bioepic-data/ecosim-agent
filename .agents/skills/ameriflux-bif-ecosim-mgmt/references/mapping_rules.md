# AmeriFlux BIF To EcoSIM Management Mapping Rules

## Inputs

The extractor reads AmeriFlux BIF long-table rows with:

```text
SITE_ID
GROUP_ID
VARIABLE_GROUP
VARIABLE
DATAVALUE
```

Rows are reconstructed into records by `(SITE_ID, VARIABLE_GROUP, GROUP_ID)`.
Filter to the requested site first. `--site-id` selects rows, not merely output
names; without it the input must contain exactly one identifiable site.

## Plant Management

Converted groups:

```text
GRP_DM_PLANTING     planting rows in plant workbook
GRP_DM_AGRICULTURE  harvest rows in plant workbook when text contains Harvest
```

Planting rules:

```text
date                  first available DM_DATE_START, DM_DATE, or DM_DATE_END
crop                  inferred from DM_COMMENT text
seed rate             parsed from seeds/acre, seed/acre, plants/acre, or 140k seeds/acre patterns
Planting_population   seed_rate_per_acre / 4046.8564224, plants m-2
Planting_depth        default --planting-depth-m
pft_type              composed from crop short code and --koppen-code, then validated against --pftpar-nc pfts
```

Crop short-code defaults:

```text
corn/maize      maiz
soy/soybean     soyb
wheat           swhe
rice            rice
barley          barl
oats            oats
alfalfa         alfa
clover          clva
```

When `--pftpar-nc` is supplied, the extractor reads `pfts`, `pfts_short`, and `pfts_long`. It writes only validated six-character PFT codes when a matching code is available. If `<short><koppen>` is missing, it uses the first available code for that short code and records the substitution in the review workbook.

If multiple planting records occur in one year, the extractor keeps one PFT slot for crop rotation workflows. It uses the earliest planting date and a surface-fraction-weighted planting population when `DM_SURF` exists, otherwise an arithmetic mean.

Harvest rules:

```text
iHarvType    default 1, grain harvest
jHarvType    default 1, terminate plant
CutHeight    default 0.1 m
FractionCut  DM_SURF/100 when available; otherwise split equally across same-year harvest records
```

The component harvest fractions default to zero and should be reviewed against the specific crop-harvest interpretation in the EcoSIM branch. The generated rows are intended to be conversion-ready placeholders, not final biophysical truth.
For an intended exported grain harvest, inspect both PFT fine-nonleaf removal
and ecosystem fine-nonleaf export. The grain flag and termination flag alone
do not establish exported biomass. Do not turn a benchmark's 0.95 export
fraction into an unsourced default for other cases.

## Soil Management

Converted groups:

```text
GRP_DM_FERT_M  fertilizer rows in soil workbook
GRP_DM_TILL    tillage rows in soil workbook
```

Fertilizer rules:

```text
date          first available DM_DATE_START, DM_DATE, or DM_DATE_END
N rate        parsed from lb N/acre, lbs N/acre, or lb/acre when DM_FERT_M=N
unit          1 lb acre-1 = 0.1120851156 g m-2
AppDepth      default --fert-app-depth-m
BandWidth     default --fert-band-width-m
```

N product allocation:

```text
UAN / urea ammonium nitrate   25% NH4Soil, 25% NO3Soil, 50% UreaSoil
NH3 / anhydrous ammonia       100% NH3Soil
urea                          100% UreaSoil
unresolved product            100% NH4Soil placeholder
```

Tillage rules:

```text
iSoilDisturbType  default --tillage-type, 1
DepzCorp          default --tillage-depth-m, 0.15 m
```

Pesticide, external weather, and unresolved `GRP_DM_*` records are copied to the review workbook but are not written to EcoSIM plant or soil management tables.
In particular, derive and review `GRP_DM_WATER` separately for irrigated sites;
an empty generated irrigation table is not evidence of rainfed management.

Check whether separate N and P rows describe one fertilizer product, whether
resumed passes repeat the same dose, and whether `DM_SURF` or acreages imply
partial-field coverage. Keep source group IDs and record consolidation choices.
Use actual nutrient masses: a product's N-P2O5-K2O grade needs oxide-to-element
conversion before elemental P input. Distinguish seed targets from measured
stand counts, and mark unparsed rates or unknown formulations as unresolved.

## Review Requirements

Before converting to NetCDF, review:

```text
crop PFT codes and Koppen suffixes
planting density and depth
harvest type, termination flag, FractionCut, and component fractions
fertilizer rate units and product chemistry split
tillage type/depth
unconverted management records
```
