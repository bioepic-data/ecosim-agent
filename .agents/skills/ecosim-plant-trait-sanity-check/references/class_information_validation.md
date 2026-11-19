# Plant class validation contract

Every `PLANT CLASS INFORMATION` item must be **validated against the model
contract and applicable plant/site evidence**, not merely parsed or displayed.
Read this reference for every full class review. The checker emits a record for
each of the 14 fields in every selected plant block. Missing/duplicate rows,
invalid codes and code/label conflicts are errors. Unknown additional class
items remain unresolved until their validation contract is implemented.

## Model encoding and ecological support are separate

The schema in `scripts/plant_class_validation.py` follows `ElmIDMod.F90`,
`PlantInfoMod.F90::pft_display`, and the woody-form predicate in
`PlantMathFuncMod.F90`, inspected 2026-11-16. Recheck those definitions when the
model changes. Numeric code validity does not establish ecological suitability.

| Field | Model domain | Evidence or mapping that must be reviewed |
| --- | --- | --- |
| ICTYP | 3 C3; 4 C4 | Photosynthetic pathway of the represented taxon |
| IEBTYP | 0 bryophyte; 1 pteridophyte; 2 gymnosperm; 3 monocot; 4 eudicot | Taxonomic identity |
| IGTYP | 0 shallow; 1 intermediate/fibrous; 2 deep penetrative; 3 very deep/tap | Root architecture/depth and mapping to model pathways; code 2 alone does not mean tree |
| ISTYP | 0 annual; 1 perennial | Life cycle of the represented cohort |
| IDTYP | 0 determinate; 1 indeterminate | Species/cultivar growth habit; do not infer all soybean cultivars share one habit |
| INTYP | 0 none; 1–3 root fixation fast/medium/slow; 4–6 canopy fixation fast/medium/slow | Symbiosis identity, fixation location and evidence for the rate class; a legume name alone does not validate a rate class |
| IWTYP | 0 evergreen; 1 cold deciduous; 2 drought deciduous; 3 combined | Model phenology pathway and verified management context; human-seeded annual crops conventionally use0, not all annual vegetation |
| IPTYP | 0 neutral; 1 short day; 2 long day | Species/cultivar flowering response, plus XDL/XPPD and model trigger logic |
| IBTYP | 0–5 for the woody pathway; 0,1,3 for non-woody | Turnover representation and IGTYP/I2EXP combination; printer merges0/1 labels, so numeric readback may be needed |
| I2EXP | 0 no; 1 yes | Secondary growth of the represented tissues; soybean can legitimately express secondary root growth |
| ISNTYP | 0 bryophyte; 1 grass; 2 shrub; 3 deciduous tree; 4 conifer | Canopy form and snow-interception representation; distinguish model approximation from taxonomy |
| IRTYP | 0 aboveground; 1 belowground | Storage/reproductive organ represented; not rooting depth |
| MY | 1 no; 2 yes | Mycorrhizal association and the model's supported representation; yes does not prove observed colonization |
| ZTYPI | Continuous 0–5 | Growing-season thermal adaptation and site/taxon evidence; valid fractional values must not be rejected as noninteger enums |

Check all items against species/cultivar and site evidence, using the source
priorities in `web_evidence.md`. Explicitly justify model-specific mappings.
Site observations, primary literature, model conventions, proxies and
user-prescribed sensitivities must remain distinguishable. A model enum citation
alone cannot validate the biological choice of that enum. A documented prior
or failed search remains unresolved, not supported.

For soybean, primary research supports a generally short-day response with
genetic variation in sensitivity (Zhu et al., 2019,
https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2018.01867/full).
Do not silently assign every cultivar a categorical correction from that broad
fact. Evaluate the intended cultivar/representation and honor prescribed tests.
Older process versions also allowed a declining-day annual-crop bypass. The
current annual-evergreen pathway accumulates floral development using
`FL_POPT` and `FL_PSENS`; it does not use that bypass. `FL_PSENS=0` imposes no
photoperiod delay, and negative `FL_POPT` uses site-resolved XDL. Negative XDL
means site maximum daylength. Check the actual executable/source identity;
valid photoperiod encoding alone does not validate flowering dates.

## Run validation

For comprehensive classification validation or scientific run readiness:

```bash
python3 scripts/check_plant_trait_desc.py /path/to/read-only.plant_trait.desc \
  --strict-physiology --require-class-evidence \
  --pft-file /path/to/the/selected/ecosim_pftpar.nc \
  --web-evidence /path/to/site_evidence.json --json
```

`--pft-file` is read-only. It checks numeric classes against printed labels and
disambiguates lossy output. Record the selected file's identity and whether it
actually produced the description: a current table is not historical run
provenance. A corrected table compared with old output should expose a stale
readback, not relabel that old run as corrected.

INTYP now prints `No N-fixation` for 0 in the model. The inspected printer uses
the same default branch for unsupported codes; older output can say `Not
defined`. Such a label without an explicit number requires numeric PFT readback
before it can establish INTYP=0. Both formats are supported without guessing.

Evidence uses existing `categorical_expectations` rules; `expected_codes`
allows comparisons by decoded model code instead of spelling. For example,
this is **partial coverage only**, leaving the other 13 fields unresolved:

```json
{
  "plants": [{
    "pft_code": "soyb41",
    "categorical_expectations": [{
      "variable": "IPTYP",
      "expected_codes": [1],
      "source": "Applicable soybean photoperiod evidence reviewed for this representation",
      "url": "https://www.frontiersin.org/journals/plant-science/articles/10.3389/fpls.2018.01867/full",
      "evidence_level": "species",
      "mapping_note": "Short-day flowering response mapped to active ElmIDMod IPTYP=1; cultivar sensitivity still needs separate review."
    }]
  }]
}
```

Rules require a nonempty test (`expected_codes`, `contains`, `allowed_values`,
or numeric bounds for ZTYPI), a source, a URL or existing `source_path`, and an
`evidence_level`. Record applicability, conversions/model mapping and the actual
search in the evidence audit. Missing citations, no-op rules, contradictory
rules, `not_searched`, `searched_no_applicable_evidence`, provisional conclusions
and `model-prior`/`user-default`/`user-prescribed` levels cannot silently produce
a supported verdict. Include the concrete reason when a choice remains an
assumption. All14 classes apply to model records; "not applicable" must not be
used to bypass validation of their selected code and mapping.

Per-item reports separate `model_status`, `evidence_status` and overall
`status`. Only valid encoding plus supported evidence yields `validated`.
`--require-class-evidence` returns a nonzero status for incomplete or
contradicted coverage. Exploratory/offline runs without the flag still list all
items and warn about unresolved coverage; they do not establish validation.
`--strict-physiology` is a separate requirement and cannot substitute for class
coverage. Corrections still require authorized PFT NetCDF edits through ParEditor;
the evaluator never changes the description or parameter file.

`THERMAL PROPERTIES` is a separate optional section for older-output compatibility.
When present, all six resolved RG/CG/LG inactivation energies must be finite,
positive scalars. They must not be swallowed into class information. Unknown
future sections receive an explicit unresolved warning.
