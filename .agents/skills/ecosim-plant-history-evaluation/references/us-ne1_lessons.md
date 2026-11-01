# US-Ne1 worked example: lessons, not universal targets

These observations came from the US-Ne1 analysis on 2026-10-31. Recompute them
for new inputs, periods or model revisions. The source inspected at that time
was EcoSIM `33cd9e40`; the existing executable was reused, so source/binary
equivalence was not independently established.

## Window and model versus forcing years

The fixed original five-year history window was model 1987–1991, corresponding
to forcing 2005, 2006, 2001, 2002 and 2003. Follow-up trait interpretation retained
that snapshot. A separate 1992 restart experiment used model 1992–1993 / forcing
2004–2005, not observations dated 1992–1993. Always establish actual completed
history coverage; a long planned namelist duration is not completed output.

## Capacity is not turnover or activation

All five original trait descriptions were identical and printed VCMX=45,
VOMX=12. The inspected reader printed traits before multiplying both values
by 1.92. The history mapping was verified as:

```text
Vcmax25 = 1.92 * VCMX * 3.3 * LeafRubiscoN_pft
Vomax25 = 1.92 * VOMX * 3.3 * LeafRubiscoN_pft
```

The 3.3 converts Rubisco N to C in that implementation. This is not a universal
conversion for other versions/protein conventions. The printed enzyme-carbon
label and reader conversion needed reconciliation before kinetic tuning.
Positive maximum capacity persisted after activation/assimilation ceased;
larger VCMX was therefore not the first supported remedy.

Realized growing-season SLA was already comparable to inverses of site LMA
under the historical carbon-area convention. Raising SLA1 to repair low LAI
would have risked compensating for low leaf biomass. Exact BIF unit/version
uncertainty remained visible.

## Root geometry and the matched restart sensitivity

The initial fine-root radius was RRAD2M=50 µm; RVSR=1.389 µm implied about 259
equal vessels under the fixed 20% lumen-area assumption. At the user's requested
RVSR=25 µm, the count became 0.8, violating the check N>=1. The corresponding
radius limit was 22.36 µm. Neither case was validated anatomically merely by
its execution or geometry status.

Matched two-year branches from the same 1992 checkpoint changed only RVSR:

| Model / forcing | GPP control → test (g C/m²/year) | Peak LAI control → test | Stress-counter peak control → test (h) |
|---|---:|---:|---:|
| 1992 / 2004 | 1,379 → 2,068 | 3.40 → 5.70 | 748 → 0 |
| 1993 / 2005 | 708 → 1,803 | 2.17 → 5.23 | 888 → 0 |

This supported a material root axial-transport limitation in the control, while
the requested test retained the conduit geometry error. It did not establish
25 µm as an accepted correction. Radial/stalk parameters stayed fixed; the
approximate resistance partition from daily means was not an exact hourly
partition or a pure vessel-resistance fraction.

Root-class context matters: [Hazman and Kabil (2023)](https://doi.org/10.1007/s40415-023-00925-5)
reported one central vessel in small sweet-corn laterals and one to three in
larger laterals, at mean root diameters 161 and 288 µm. These roots were wider
than the model's 100 µm fine-root diameter, and central counts do not enumerate
every possible small peripheral conduit. Do not impose those counts on all PFTs.

## Nutrient timing and grain filling remained separate issues

Annual mineral N uptake rose from 245/133 to 327/261 kg N/ha in the restart test
without changing fertilizer or uptake Vmax. Uptake was coupled to the improved
water supply and growth. Nevertheless, only about 5.2%/3.6% occurred after modeled
anthesis, and anthesis-to-end-fill remained 42/36 days. Resolving water stress
did not automatically resolve nutrient or reproductive timing.

The original management budget required irrigation-borne N, post-harvest N and
P/P₂O₅ interpretation to be explicit. No whole-plant N measurement, fertilizer
recovery estimate, or verified field application rate was inferred solely from
the history mineral-uptake counter.

## Observation evaluation extension (2026-11-01)

The subsequent transient evaluation used model 2001–2018 and FLUXNET v1.3_r1
observations 2001–2024. Split h0 offsets retained the global 1977 origin;
filenames did not reset time. Observation-only 2019–2024 was displayed but
excluded from model scores. These results are distinct from the earlier
five-year spin-up and restart comparisons above.

- Daily GPP correlation was about 0.96, but annual GPP correlation was about
  0.12 over 17 complete observation years. Seasonal agreement did not establish
  interannual skill. Annual NEE uptake magnitude was about 43% too large.
- General BIF records alone missed soil sensor depths. Archived BIFVARINFO_HR
  resolved eight temperature depths (2–50 cm) and four moisture depths
  (10/25/50/100 cm). Temperature warm bias was about 0.7–0.9°C. Depth changes
  around 2015 were kept as separate channels.
- The supplied US-Ne3 MATLAB script confirmed centre-depth interpolation and
  conversion before interpolation. Its first-record matrix porosity and ten
  layers were not imposed on US-Ne1's twelve active layers. The inspected
  current saturation equation included macropores; the bulk-aware reconstruction
  was retained alongside matrix-only sensitivity, with source/binary and daily
  covariance caveats.
- At 1 m, reconstructed moisture was about 0.083 m³/m³ too dry; observations
  exceeded model pore capacity on about 71% of paired days. The observed level
  shifted around 2015. Neither feature alone proved which source was wrong,
  and neither justified increasing RVSR to fit moisture.
- Yield was initially overlooked because explicit values occurred in management
  comments: 2016 202.1, 2017 225 and 2018 135 bu/acre, all at 15.5% moisture;
  two 2017 harvest portions reported the same rate. A 2021 tower/IMZ comment
  reported 226.7 bu/acre with no moisture basis and no model overlap.
- At an **assumed** grain carbon fraction of 0.45, previous-day standing grain C
  gave conditional yield biases of approximately −3%, −17% and +9% in
  2016–2018. The 0.42–0.48 plotted range was an illustrative sensitivity,
  not site evidence. In 2016 the reported date was harvest completion while
  model grain continued growing. Exact harvested flux remained unavailable.

The reproducible assessment is under the case directory
`work-space/US-Ne1/evaluation/fluxnet_evaluation_20261101_203520/` in the
workspace used for this analysis. Values above are worked examples, not
portable targets or default conversion parameters.

## Hourly branch and 21-day summer plots (2026-11-01)

An isolated branch from the 2017-01-01 checkpoint completed 2017–2018 with
17,520 hourly steps and two 8,760-record history files. Bounds were contiguous
from day 0 to 730 despite January-00 filenames. Climate-preparation provenance
showed that a −1-hour radiation alignment had already been applied; additional
lag checks peaked at zero for temperature and incoming shortwave radiation.

Measured-support hourly NEE r was about 0.91, partition-derived GPP r about
0.93–0.94, and LE r about 0.79. H was weaker (r about 0.58, NSE about −0.02).
Summer H cycles and time sequences revealed excessive daytime amplitude and
abrupt hourly features. Net radiation, newly enabled, was low by about 22 W/m²
on available hours. These results apply to this branch and support, not the
full transient parent period.

The branch's first seven daily comparisons closely matched the parent, but
later heat differences were material: pooled daily branch-minus-parent LE/H
biases were +1.12/+3.49 W/m², with maxima near 100/179 W/m². The report retained
these differences without assigning a cause or claiming an exact replay.

At the user's request, July 1–21 was added for each year: 504 hourly intervals
per window, plotting GPP, NEE, LE and H on common QC support with gaps preserved.
NEE/GPP paired hours were only 322 in 2017 and 289 in 2018; a 504-hour window is
not 504 observations. The saved window CSV and plot script supplement the
seasonal diurnal cycles and original week-long plots. Dates and duration are
examples, not compulsory windows for other sites.
