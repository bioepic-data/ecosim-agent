# Variables, signs and conversions

## EcoSIM history fields

Daily `h0` values are daily means of hourly values (`avgflag='A'`). Rates in
mm h⁻¹ therefore become mm d⁻¹ when multiplied by 24.

| Field | Units | Sign in output | Upward or positive-out quantity |
|---|---|---|---|
| `ECO_Radnet_col` | W m⁻² | > 0 into ecosystem | Rn = value |
| `ECO_LE_col` | W m⁻² | > 0 into surface | LE = −value |
| `ECO_HeatS_col` | W m⁻² | > 0 into surface | H = −value |
| `SOL_RADN_col` | W m⁻² | incoming shortwave | SW_in = value |
| `LW_Sky_col` | W m⁻² | incoming longwave | LW_in = value |
| `ECO_RADSW_col` | W m⁻² | absorbed shortwave | SW_abs = value |
| `ECO_SWOUT_col` | W m⁻² | reflected shortwave | SW_out = value |
| `ECO_LWOUT_col` | W m⁻² | outgoing longwave | LW_out = value |
| `QTransp_col` | mm h⁻¹ | < 0 to atmosphere | T = −24 × value (mm d⁻¹) |
| `EVAPGrnd_col` | mm h⁻¹ | > 0 into soil | E_ground = −24 × value |
| `QVegEvap_col` | mm h⁻¹ | < 0 to atmosphere | E_canopy = −24 × value |
| `ECO_ET_col` | mm | cumulative, resets each year | daily ET = within-year difference |
| `SOIL_RN_col`, `SOIL_H_col`, `SOIL_LE_col`, `SOIL_G_col` | W m⁻² | ground terms; check each `long_name` | — |
| `CAN_TEMPC_pft`, `TEMP_SURF_col` | °C | canopy and ground temperature | — |

Always read `long_name` in the file you evaluate. Signs have changed between
versions, so the table is a starting point, not a guarantee.

## Build dependencies

- **`ECO_RADSW_col` before EcoSIM commit `626be305`:** it counted only 1/NPR of
  the shortwave absorbed by surface litter. Residue-covered seasons therefore show
  an implausible albedo (about 0.9 in winter at US-Ne1).
  - Net radiation and the energy balance were not affected.
  - Treat albedo derived from older output as invalid wherever litter is exposed.
- **`ECO_SWOUT_col` and `ECO_LWOUT_col`:** added in `626be305` and derived as
  - SW_out = SW_in − SW_abs;
  - LW_out = LW_in + SW_abs − Rn.

  LW_out therefore includes reflected longwave, consistent with what a tower's
  upward pyrgeometer measures. Check that SW_in − SW_out + LW_in − LW_out
  reproduces Rn to rounding error.

## Tower conventions (FLUXNET / AmeriFlux)

- **Signs:** LE and H are positive upward. NETRAD is positive downward. G is
  positive into the soil.
- **Missing values:** −9999 marks missing. Use gap-filled `_F_MDS` fluxes with
  their quality flags if available. `SW_OUT` and `LW_OUT` are often not
  gap-filled; use their own valid days.
- **Precipitation:** gap-filled `P_F` can come entirely from reanalysis (quality
  flag 2). At irrigated sites, gauges can catch irrigation. Neither is suitable
  for water budgets without checking.

## Formulas

- **ET (mm d⁻¹)** = LE (W m⁻²) × 86400 / λ, with λ = 2.45 × 10⁶ J kg⁻¹ unless a
  temperature-dependent λ is justified.
- **Closure ratio** = (H + LE)/(Rn − G), using daily or longer sums. Residual =
  Rn − G − H − LE.
- **Closure-corrected tower fluxes, Bowen ratio preserved:**
  - LE_c = LE × (Rn − G)/(H + LE);
  - H_c = H × (Rn − G)/(H + LE);
  - apply only when |H + LE| > 20 W m⁻² (daily). Without G, use Rn on daily or
    longer means and say so.
- **Radiating temperature:** T_rad = (LW_out / (ε σ))^0.25 − 273.15, with ε = 0.98
  for both model and tower unless better values are known. Report it as an
  effective temperature.
- **Albedo** = Σ SW_out / Σ SW_in over the period (hourly: daytime hours with
  SW_in > 20 W m⁻²).
- **Rn-bias decomposition:** ΔRn = ΔSW_in − ΔSW_out + ΔLW_in − ΔLW_out, with each
  Δ = model − tower, on common valid times.
