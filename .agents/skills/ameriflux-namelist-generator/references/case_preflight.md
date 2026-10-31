# Mandatory case preflight

Case assembly and model launch must use `Tools/case_preflight.py`. This is an
executable gate, not an optional inspection or a previous report to trust.
Run it from the ecosim-agent root to inspect an existing case without running it:

```bash
python Tools/case_preflight.py result/SITE/run_dir/SITE.namelist
```

`create_site_run_folder.py` runs it before returning success.
`create_site_smoke_run.py` checks both the full production request (as a fresh
run) and the proposed short namelist before deleting any previous smoke output;
it repeats these checks after a PFT correction. The one-year evaluation checker
checks both the source production request and the prepared scratch case before
launch. `run_site_full.py` checks the proposed fresh configuration before
changing `continue_run` and checks again before each launch, including the one
permitted continuation. Any new case/run entry point must call the same gate.
Do not bypass it with a direct executable launch when preparing a site case.

Reports contain the assessment time, exact configuration hash, selected input
paths and SHA-256 hashes, requested model/forcing years, climate findings and
site-conflict evidence. The default is `<namelist-stem>.preflight.json` beside
the namelist; smoke checks use the site root and evaluation checks their scratch
root. Reports describe those exact inputs at that time; input, output-selection,
namelist or scope changes require a fresh check. Reports are expected run-folder
artifacts, not model output.

The gate blocks on:

- Unsupported/invalid namelist syntax; unknown groups or keys against the local
  standalone Fortran declarations; invalid workflow timing, solver counts,
  logical controls or explicit history names.
- Missing selected files or required reader variables; invalid numeric active
  entries and incompatible checked dimensions, topographic bounds or capacity.
- Missing/invalid weather records over the actual requested period, using the
  climate-readiness validator, including its restart scope and calendar rules.
  A stop duration longer than the forcing schedule is an error in this workflow.
- Invalid grid layers and physical/site contradictions from the site-conflict
  validator. Only supplied layers are checked for missing data; derived bottom
  layers and inactive PFT/event slots can contain padding.
- Incompatible PFT table/management identifiers (including the grid Koppen
  override), active/event counts, malformed event strings, invalid dates,
  harvest enums and fractions, or missing annual management records.
- Missing soil-management selector targets or malformed records; monthly GHG
  gaps/units or coverage missing **model years**, including spin-up, rather
  than merely the cycled weather years.

Unsupported active climate-factor files, fixed-climate/warming modes,
non-annual plant-management schemas, and explicit `restartFileFullPath`
configurations block until a reader-specific validator is implemented. Extend
and test the validator for an additional format/mode; do not add a skip flag or
record an unsupported check as passed. Strict syntax is intentionally narrower
than all possible Fortran namelist syntax; resolve unsupported syntax explicitly.

Scientific provenance and representativeness are separate from these mechanical
checks. Unverified aspect, soil priors, conditional pore-domain comparisons,
weather-clock metadata and absent field evidence remain warnings. A successful
preflight does not establish complete parameter plausibility, management budgets,
all event-code semantics, full CF compliance, restart state equivalence, or
agreement with observations/literature. Continue the sourced field-data review,
two-year trait smoke test and one-year evaluation-output check. The contract is
against the local model source; it does not prove that an arbitrary binary was
built from that source. Preserve the executable/build checks.
