# Candidate history mappings

These are candidate standalone EcoSIM names, not guaranteed fields for every
executable or proof of observational equivalence. Inspect the current
`f90src/IOutils/HistData/HistRegister*.F90` and `HistUpdate*.F90`, then put actual
names, units, dimensions and semantics into the manifest.

| Observation target | Candidate fields | Required interpretation/support |
|---|---|---|
| NEE | `ECO_NEE_CO2_col` | Verify sign, units, mean interval and observation QC. |
| GPP | `ECO_GPP_col` | Cumulative counter; annual end value or reset-aware increments. NT/DT tower products are partition-derived. |
| Ecosystem respiration | `ECO_RA_col`, `ECO_RH_col` | Verify signs and cumulative counters before summation. |
| ET from LE | `ECO_ET_col` | Cumulative water depth; integrate LE with explicit latent-heat convention. |
| Sensible heat | `ECO_HeatS_col` | Confirm sign and time averaging against H. |
| Radiation | `ECO_RADSW_col`, `SOL_RADN_col`, `RADN_col` | Absorbed/incoming/net and shortwave/all-wave are different quantities. Missing outgoing components must be flagged. |
| LAI | `ECO_LAI_col` | Leaf area versus leaf+stalk area; active PFT aggregation. |
| Canopy height | `CAN_HT_pft`, `Stalk_HT_pft` | Match height statistic and plant organs. |
| Foliage/aboveground C | `LEAF_C_pft`, `PetolSheth_C_pft`, `STALK_C_pft`, `RESERVE_C_pft`, `HUSK_C_pft`, `GRAIN_C_pft`, `SHOOT_C_pft`, `SHOOT_NONSTC_pft` | Trace pool overlap and storage before choosing/summing. Request leaf/sheath for foliage and all relevant aboveground pools for total biomass; do not double-count aggregate SHOOT_C. |
| Crop production | Organ/pool/harvest fields established from code | Standing biomass is not cumulative harvested/exported production; Total/Foliage BIF is not automatically grain yield. |
| LMA/SLA | `SLA_pft` and matching leaf C/dry-mass basis | Verify observed units and carbon fraction; leaf and shoot pools differ. |
| Plant C/N | Matching organ C and N fields | Confirm concentration/stock and structural/storage basis. Block ambiguous records. |
| Soil temperature | `TEMP_vr`, `BOTDEPZ_vr` | Actual sensor depths, selected layers and depth interpolation. |
| Soil volumetric water | Direct volumetric-water output if available; `rWatFLP_vr`, `VSPore_vr` as diagnostic support | Relative saturation is not volumetric water. Trace micro/macro/rock basis and time averaging. Daily products cannot generally reconstruct a mean of a product. |
| SOC and soil N | `tSOC_vr`, `tSOCL_vr`, `tSON_vr`, `BOTDEPZ_vr` | Verify geometric/matrix volume, layer thickness and laboratory pool definition; tSOC includes organic pools excluded by some soil sampling protocols. |
| Bulk density/pH | Actual registry fields if available | Initialization-only values, water/salt pH, sampling depth and runtime changes require explicit mapping; mark missing outputs blocked. |

A one-year output check should include support for **all relevant observations**,
not every model variable. Do not demand root biomass, Vcmax/Jmax or NPP simply
because an extractor can compute them if the site has no corresponding data.
If useful observations exist in other years, request their fields as well and
record that this year validates output coverage rather than a same-year score.
