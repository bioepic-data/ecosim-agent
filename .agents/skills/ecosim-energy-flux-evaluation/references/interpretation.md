# Interpreting energy-flux diagnostics

## Total versus split

H + LE = Rn − G − storage. Over a day or longer, G and storage are small, so the
model's total turbulent flux is set mostly by its Rn.

- **Model versus tower totals:** the model closes its energy balance; the tower
  usually does not (closure ratio typically 0.75–0.9). The model's H + LE bias
  against the raw tower is therefore roughly the model's Rn bias plus the tower's
  closure residual.
- **Runs that change only H and LE:** soil, stomatal and evaporation tests
  usually move energy between H and LE and leave H + LE unchanged. Tabulate
  bias(H), bias(LE) and their sum across runs. A constant sum means the tests
  only change the split.
- **After closure correction:** compare the model with closure-corrected tower
  fluxes. A model with too much H and slightly too much LE against the raw tower
  can turn out, after correction, to have too much H and too little LE. That is
  a split problem with a different fix.

## Net radiation

- **Forcing first.** Incoming radiation comes from the forcing, so check it
  matches the tower first.
- **Shortwave or longwave.** With incoming terms matching, a Rn bias is either
  reflected shortwave (albedo) or outgoing longwave (surface temperature or
  emissivity).
- **A longwave-dominated bias means the effective radiating surface is too cold
  or too warm.** Its sign and diurnal shape show which.
  - A surface that radiates too cold at night and through the day goes with a
    cold soil bias and excess Rn.
  - Compare the radiating temperature with the canopy-air temperature contrast
    that drives H. If the model's canopy is warmer than the air, driving large
    H, while its radiating temperature is below the tower's, the aerodynamic and
    radiometric temperatures disagree. Investigate how canopy and ground
    emission combine before calling the surface simply warm or cold.
- **Albedo.** Compare albedo month by month. Snow, residue and canopy cover
  change it seasonally, and an annual mean can hide compensating errors.

## Splitting ET

- **Transpiration fraction.** For a full crop canopy, transpiration/ET is
  commonly 0.6–0.9 in the peak season. Ground evaporation dominates bare, residue
  and early-canopy periods. Treat these ranges as expectations from the
  literature, not site measurements, unless partitioning data (isotopes, sap
  flow, lysimeters) exist.
- **Signs of excess ground evaporation:**
  - a low transpiration fraction under full canopy;
  - night LE well above the tower's;
  - spring ET from bare soil far above the tower's;
  - a wet shallow soil bias.
- **Moving energy from H into LE: the two routes have opposite side effects.**
  - Through transpiration (stomatal conductance): little effect on soil
    temperature; draws water from the root zone.
  - Through ground evaporation: cools and dries the surface soil, so it can
    worsen a cold-soil bias.

  Check soil temperature and moisture responses before choosing.
- **Close the water budget.** For the same window: rain + irrigation = ET +
  drainage + runoff + storage change. Soil moisture and ET can both be too high
  only if the model stores or evaporates water that in reality drains or is
  stored deeper.

## US-Ne1 worked example (irrigated maize/soybean, 2013–2023)

- **Radiation:**
  - Incoming shortwave and longwave matched the tower to within about 1%.
  - Corrected albedo matched (summer 0.169 vs 0.165).
  - The +10 W m⁻² summer Rn bias was entirely outgoing longwave: the model emitted
    11 W m⁻² too little, a radiating surface 0.6–3.2 °C too cold, worst at night.
- **Closure and H + LE:**
  - The tower's summer closure residual was about 30 W m⁻².
  - Across eight runs (stomatal, soil and evaporation changes), the summer H + LE
    bias stayed at +41 W m⁻² while H ranged from +25.6 to +34.8.
  - After closure correction, the model had too much H (about +28 to +30 W m⁻²)
    and too little LE.
- **ET split:**
  - The model's annual transpiration/ET was 0.30, and 0.58 under full summer
    canopy.
  - Spring ET was almost entirely ground evaporation: 1.9 mm d⁻¹ from a soil held
    at field capacity.
  - Night LE was 25 W m⁻² against 8 at the tower.
- **What changed what:**
  - Lowering field capacity cut ground evaporation and raised H.
  - Raising the Ci:Ca ratio (FCO2) raised transpiration and lowered H without
    cooling the soil.
- **Reports:**
  `work-space/share_tang_USNe_3sites/ne1_run/experiments/current_code_2013_2023_20261125T042240Z/US-Ne1/evaluation/current_code_evaluation_20261125/`
  (sections 2, 5–12, 18 and 20).
