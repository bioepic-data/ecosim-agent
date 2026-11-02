# Method identity and engine use

Primary sources (checked 2026-11-01):

- [Lange (2019), final paper](https://doi.org/10.5194/gmd-12-3055-2019),
  especially Sect. 3.2.1, Tables 1–2 and Eqs. 1–15.
- [Author's archived ISIMIP3BASD v1.0 code](https://zenodo.org/records/2549632),
  released 2019-03-05, linked to the discussion paper. Later software versions
  exist; a version number alone does not prove equivalence to the final paper.

Do not confuse parametric mapping with its empirical change-signal transfer.
The published method combines quantile-specific change transfer, parametric
mapping and event-likelihood adjustment; bounded variables additionally require
bound-event handling. Consult the source for detrending, radiation scaling and
variable settings. A shortcut that omits one of these is a named adaptation.

## Pin the backend

Prefer the author's code with a fixed archive/commit. For literal paper
reproduction compare its implementation and settings against the final paper;
record discrepancies rather than silently substituting a newer release.

The inspected v1.0 archive is:

```
URL: https://zenodo.org/records/2549632/files/isimip3basd-master.tar.gz
MD5 (publisher): 57e02d1a2ea88d5c7ff6350d0d1e54c7
SHA256 (independently computed): 1803eb14a9c7df0453c8cb91767bd780fbf25dee1877facbb0b459bd2b2b2e32
```

Download to a task-specific dependency directory when needed, verify its hash,
inspect archive paths before extraction, and retain its AGPL license. Do not
vendor it into this skill by default. `README.txt` lists its historical Python,
NumPy, SciPy, Iris and Dask dependencies. Use an isolated compatible environment;
record any compatibility patches and verify unchanged numerical behavior on
complete-input fixtures. Do not alter the user's normal environment implicitly.

The inspected `bias_adjustment.py` module contains variable-specific options.
Its `-v` expects a **shared standard_name**, not necessarily the NetCDF variable
name. It expects Iris-readable CF time/spatial coordinates, not EcoSIM's padded
four-axis arrays. Prepare separate CF daily intermediates and preserve calendars.
Run `--help` in the chosen environment before execution; do not assume flags
from another BASD version.

Example for complete, harmonized daily temperature inputs using inspected v1.0
flags (paths are task-specific; output must be new):

```bash
python /path/to/pinned/isimip3basd-master/bias_adjustment.py \
  --obs-hist /path/to/train_observed_tas.nc \
  --sim-hist /path/to/train_source_tas.nc \
  --sim-fut /path/to/application_source_tas.nc \
  --sim-fut-ba /path/to/new_corrected_tas.nc \
  --variable air_temperature --months 1,2,3,4,5,6,7,8,9,10,11,12 \
  --distribution normal --trend-preservation additive --detrend \
  --halfwin-upper-bound-climatology 0 --randomization-seed 1729 \
  --invalid-value-warnings --restore-invalid-values
```

This example is not proof of runtime compatibility. Confirm requirements and
inputs, run a small complete-input fixture, and capture actual warnings first.

For point-series workflows, the author's array kernels can also be called
directly without the Iris/Dask file-IO layer. Preserve their numerical function
bodies and utility dependencies; record source hashes, the loader/adapter and
exact omitted IO components. This avoids silently invoking the file wrapper's
reference-gap sampling. Filter finite measured observation/source training
pairs together, pass their actual year arrays, and keep application timestamps
complete. This is an explicit missing-aware/hourly adaptation, not evidence that
the unmodified file wrapper accepts gaps safely.

Use an isolated environment with compatible NumPy/SciPy versions. Test normal
identity mapping, a known additive quantile change, input-array preservation,
and bounded-variable outputs before applying the adapter. Bounded identity
need not be exact because source/target fit supports can differ at event
thresholds; distinguish that behavior from a numerical defect. Record fit
fallbacks and failures, and retain raw values for unsupported strata with an
explicit status rather than silently counting them as corrected.

## Variable configuration decisions

Use final Table 2 plus the pinned backend's documentation for numeric thresholds,
units and all flags. Record choices rather than relying on implicit defaults.
The source families to check are normal for temperature/longwave/sea-level
pressure, Weibull for wind, gamma for precipitation, and beta for RH/scaled
shortwave. Do not replace mixed or bounded trend behavior with a uniform
additive/multiplicative rule. Preserve the distinction between a physical bound
and its event-frequency threshold; daily precipitation thresholds are not
hourly thresholds. The original shortwave upper-bound climatology uses a
31-day window; reusing it at hourly cadence changes its meaning.

EcoSIM `PATM` is **surface** pressure. Applying the sea-level-pressure recipe
is an explicitly documented adaptation after elevation/measurement checks.
EcoSIM `DWPTH` is actual vapor pressure: use a documented humidity representation
and thermodynamic conversion, not an invented Lange vapor-pressure preset.

## Missing data, sparse periods and failure behavior

The inspected v1.0 engine samples replacements for invalid values internally,
even with `--restore-invalid-values`; that flag does not prevent imputed
reference values entering fits. Do not silently use this to turn tower gaps
into measured training data. Choose complete eligible windows or an explicitly
verified missing-aware adaptation. Mask matching source dates consistently,
preserve calendar positions, and record any reference imputation separately.
Never zero-fill an all-missing precipitation reference.

Log distribution-fit failures, fallback mapping, insufficient tail/event
samples, seeds and altered outputs. Do not broaden thresholds until a run passes.
Validate an adaptation against upstream complete-input results and relevant
physical/change-signal invariants before production use. Fail only the affected
variable/stratum when independent correction can proceed.

A single unobserved year such as 2025 supplies few daily samples per calendar
month and cannot support a robust new application-distribution fit by itself.
Evaluate a declared multi-year source application window or a frozen historical
transfer as a separately labeled extension. Do not pull held-out observations
into that window's calibration or claim a one-year trend estimate.
