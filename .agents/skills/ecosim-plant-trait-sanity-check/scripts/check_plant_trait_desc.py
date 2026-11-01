#!/usr/bin/env python3
"""Sanity-check EcoSIM plant_trait.*.desc files for one grid."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SECTION_NAMES = {
    "PLANT CLASS INFORMATION",
    "PHOTOSYNTHETIC PROPERTIES",
    "OPTICAL PROPERTIES",
    "PHENOLOGICAL PROPERTIES",
    "MORPHOLOGICAL PROPERTIES",
    "ROOT CHARACTERISTICS",
    "ROOT UPTAKE PARAMETERS",
    "WATER RELATIONS",
    "ORGAN GROWTH YIELDS",
    "ORGAN N AND P CONCENTRATIONS",
}

REQUIRED_SECTIONS = tuple(SECTION_NAMES)

BLOCK_RE = re.compile(
    r"^\s*PLANT traits for FUNCTIONAL TYPE \(NZ,NY,NX\)=\s+"
    r"(?P<nz>\d+)\s+(?P<ny>\d+)\s+(?P<nx>\d+)\s+(?P<code>\S+)"
)

CLASS_LINE_RE = re.compile(r"^\s*(?P<idx>\d+)\|\s*(?P<var>[^:]+):\s*(?P<rest>.*)$")
NUMERIC_LINE_RE = re.compile(r"^\s*(?P<idx>\d+):\s*(?P<var>[^|:]+)(?:\|\s*(?P<rest>.*))?$")
NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")

FRACTION_VARS = {
    "RUBP",
    "PEPC",
    "CHL",
    "fCHLMESO",
    "FCO2",
    "ALBR",
    "ALBP",
    "TAUR",
    "TAUP",
    "CFI",
    "PORT",
    "PhiMIN",
    "PhiMAX",
    "PhiMean",
}

POSITIVE_VARS = {
    "VCMX",
    "VOMX",
    "XKCO2",
    "XKO2",
    "VCMX4",
    "XKCO24",
    "ETMX",
    "XRNI",
    "XRLA",
    "GROUPX",
    "XTLI",
    "SSL1",
    "SNL1",
    "WDLF",
    "GRMX",
    "GRW2L",
    "GRDM",
    "GFILL",
    "RRAD1M",
    "RRAD2M",
    "ROOTMAGE",
    "R95MAT",
    "PR",
    "RSRR",
    "RSRA",
    "ARSRA",
    "RVSR",
    "PTSHT",
    "RTFQ",
    "UPMXZH",
    "UPKMZH",
    "UPMNZH",
    "UPMXZO",
    "UPKMZO",
    "UPMNZO",
    "UPMXPO",
    "UPKMPO",
    "UPMNPO",
    "RCS",
    "RSMX",
    "CNWR",
    "CNWL",
    "CPWR",
    "CPWL",
}

NONNEGATIVE_VARS = {"VRNLI", "VRNXI"}

PHOTOSYNTHETIC_WARN_RANGES = {
    "CHL": (
        0.08,
        0.30,
        "chlorophyll-bound/light-harvesting protein fraction of total leaf protein",
    ),
}

# Protein C:N varies modestly among enzymes. This representative mass ratio
# converts enzyme protein C per leaf N into an implied enzyme-N allocation.
PROTEIN_C_TO_N_MASS_RATIO = 3.3
C4_PEPC_LEAF_N_FRACTION_WARN_RANGE = (0.01, 0.06)
C4_RUBISCO_LEAF_N_FRACTION_WARN_RANGE = (0.05, 0.16)
C3_RUBISCO_LEAF_N_FRACTION_WARN_RANGE = (0.08, 0.30)

PHYSIOLOGY_PATHWAY_RANGES = {
    "C4": {
        "XKCO2": (10.0, 35.0, "Rubisco aqueous CO2 Km at 25 C"),
        "XKO2": (120.0, 400.0, "Rubisco aqueous O2 Km at 25 C"),
        "XKCO24": (0.5, 20.0, "effective aqueous CO2 Km for PEPC at 25 C"),
        "FCO2": (0.25, 0.50, "intercellular-to-atmospheric CO2 ratio"),
        "fCHLMESO": (0.40, 0.75, "fraction of chlorophyll-bound protein in mesophyll cells"),
    },
    "C3": {
        "XKCO2": (8.0, 30.0, "Rubisco aqueous CO2 Km at 25 C"),
        "XKO2": (180.0, 650.0, "Rubisco aqueous O2 Km at 25 C"),
        "FCO2": (0.55, 0.85, "intercellular-to-atmospheric CO2 ratio"),
    },
}

RUBISCO_SPECIFICITY_WARN_RANGE = (70.0, 140.0)
C4_CARBOXYLATION_TO_OXYGENATION_WARN_RANGE = (6.0, 14.0)
C3_CARBOXYLATION_TO_OXYGENATION_WARN_RANGE = (2.0, 8.0)
C4_VPMAX_TO_VCMAX_WARN_RANGE = (0.8, 2.5)
C4_JMAX_TO_VCMAX_WARN_RANGE = (4.0, 8.0)
C3_JMAX_TO_VCMAX_WARN_RANGE = (1.2, 3.0)
LOW_PRIORITY_PHYSIOLOGY_PARAMETERS = {"ETMX*CHL/VCMX*RUBP"}
PAR_ABSORPTANCE_WARN_RANGE = (0.80, 0.95)
SHORTWAVE_ABSORPTANCE_WARN_RANGE = (0.30, 0.85)
PHOTOSYNTHETIC_PROTEIN_ALLOCATION_MAX = 0.65
PHYSIOLOGY_FLOAT_TOLERANCE = 1.0e-6

ACTIVE_STRUCTURAL_PROTEIN_POOLS = {
    "leaf": ("CNWL", "CNLF", "CPWL", "CPLF"),
    "root": ("CNWR", "CNRT", "CPWR", "CPRT"),
}

PHYSIOLOGY_REQUIRED_VARS = {
    "C3": (
        "VCMX",
        "VOMX",
        "XKCO2",
        "XKO2",
        "RUBP",
        "ETMX",
        "CHL",
        "FCO2",
        "CNWL",
        "ALBR",
        "TAUR",
        "ALBP",
        "TAUP",
    ),
    "C4": (
        "VCMX",
        "VOMX",
        "XKCO2",
        "XKO2",
        "RUBP",
        "ETMX",
        "VCMX4",
        "XKCO24",
        "PEPC",
        "CHL",
        "fCHLMESO",
        "FCO2",
        "CNWL",
        "ALBR",
        "TAUR",
        "ALBP",
        "TAUP",
    ),
}

YIELD_VARS = {"DMLF", "DMSHE", "DMSTK", "DMRSV", "DMHSK", "DMEAR", "DMGR", "DMRT"}

CONCENTRATION_VARS = {
    "CNLF",
    "CNSHE",
    "CNSTK",
    "CNRSV",
    "CNHSK",
    "CNEAR",
    "CNGR",
    "CNRT",
    "CPLF",
    "CPSHE",
    "CPSTK",
    "CPRSV",
    "CPHSK",
    "CPEAR",
    "CPGR",
    "CPRT",
}

WOODY_SHORT_CODES = {
    "ndlf",
    "ndld",
    "bdlf",
    "bdln",
    "bdlw",
    "bspr",
    "dfir",
    "jpin",
    "lpin",
    "tasp",
    "woak",
    "shru",
    "bush",
    "busn",
}

SECONDARY_GROWTH_HERBACEOUS_SHORT_CODES = {
    "soyb",
}

SECONDARY_GROWTH_ROOT_TRAITS = ("ROOTMAGE", "PhiMIN", "PhiMAX", "R95MAT")

EMBRYOPHYTE_CODES = {
    0: "bryophyte",
    1: "pteridophyte",
    2: "gymnosperm",
    3: "monocot",
    4: "eudicot",
}

EMBRYOPHYTE_SHORT_CODE_EXPECTATIONS = {
    "lich": 0,
    "moss": 0,
    "fern": 1,
    "pter": 1,
    "bspr": 2,
    "dfir": 2,
    "jpin": 2,
    "lpin": 2,
    "ndld": 2,
    "ndlf": 2,
    "gr3a": 3,
    "gr3s": 3,
    "gr4a": 3,
    "gr4s": 3,
    "maiz": 3,
    "rice": 3,
    "sedg": 3,
    "bdlf": 4,
    "bdln": 4,
    "bdlw": 4,
    "bush": 4,
    "busn": 4,
    "shru": 4,
    "soyb": 4,
    "tasp": 4,
    "woak": 4,
}

EMBRYOPHYTE_LABEL_TOKENS = (
    (0, ("bryophyte", "lichen", "moss", "sphagnum")),
    (1, ("pteridophyte", "fern")),
    (2, ("gymnosperm", "conifer", "needleleaf")),
    (3, ("monocot", "grass", "graminoid", "sedge", "maize", "rice")),
    (4, ("eudicot", "broadleaf", "shrub", "soybean")),
)

SNOW_INTERCEPTION_CODES = {
    0: "bryophyte",
    1: "grass",
    2: "shrub",
    3: "deciduous tree",
    4: "conifer",
}

SNOW_INTERCEPTION_SHORT_CODE_EXPECTATIONS = {
    "lich": 0,
    "moss": 0,
    "gr3a": 1,
    "gr3s": 1,
    "gr4a": 1,
    "gr4s": 1,
    "sedg": 1,
    "shru": 2,
    "bush": 2,
    "busn": 2,
    "bdlf": 3,
    "bdln": 3,
    "bdlw": 3,
    "tasp": 3,
    "woak": 3,
    "bspr": 4,
    "dfir": 4,
    "jpin": 4,
    "lpin": 4,
    "ndld": 4,
    "ndlf": 4,
}

SNOW_INTERCEPTION_LABEL_TOKENS = (
    (0, ("bryophyte", "lichen", "moss", "sphagnum")),
    (1, ("grass", "grasse", "graminoid", "sedge")),
    (2, ("shrub", "bush")),
    (3, ("deciduous",)),
    (4, ("conifer", "confier", "needleleaf")),
)

NO_PETIOLE_SHEATH_SHORT_CODES = {
    "bspr",
    "dfir",
    "fmos",
    "jpin",
    "lich",
    "lpin",
    "mosf",
    "moss",
    "ndld",
    "ndlf",
    "smos",
}

NO_PETIOLE_SHEATH_LABEL_TOKENS = (
    "bryophyte",
    "conifer",
    "coniferous",
    "confierous",
    "douglas fir",
    "feather moss",
    "feathermoss",
    "gymnosperm",
    "jackpine",
    "lichen",
    "moss",
    "needle leaf",
    "needleleaf",
    "pine",
    "sphagnum",
    "spruce",
)

ANGSH_ZERO_TOLERANCE = 1.0e-6
SECONDS_PER_HOUR = 3600.0
CONIFER_RSRR_WARN_RANGE_MPA_H_PER_M = (1000.0, 10000.0)
ROOT_RADIAL_CONDUCTIVITY_BROAD_RANGE_M_PER_S_MPA = (4.7e-9, 1.2e-5)
CONIFER_CONDUIT_RADIUS_WARN_RANGE_M = (2.5e-6, 40.0e-6)
ROOT_LUMEN_AREA_FRACTION = 0.2


@dataclass
class Parameter:
    index: int
    variable: str
    description: str
    raw_value: str
    numeric_values: List[float]
    section: str
    line: int


@dataclass
class PlantBlock:
    nz: int
    ny: int
    nx: int
    code: str
    start_line: int
    end_line: int = 0
    plant_name: str = ""
    koppen: str = ""
    sections: Dict[str, List[Parameter]] = field(default_factory=dict)
    parameters: List[Parameter] = field(default_factory=list)

    @property
    def short_code(self) -> str:
        return self.code[:4].lower()

    @property
    def label(self) -> str:
        return f"{self.code} (NZ={self.nz}, NY={self.ny}, NX={self.nx})"


@dataclass
class Finding:
    severity: str
    pft_code: str
    nz: int
    ny: int
    nx: int
    line: int
    parameter: str
    message: str
    category: str = "general"

    def as_dict(self) -> Dict[str, object]:
        return {
            "severity": self.severity,
            "pft_code": self.pft_code,
            "nz": self.nz,
            "ny": self.ny,
            "nx": self.nx,
            "line": self.line,
            "parameter": self.parameter,
            "message": self.message,
            "category": self.category,
        }


def numeric_values(raw_value: str) -> List[float]:
    values = []
    for match in NUMBER_RE.finditer(raw_value):
        try:
            values.append(float(match.group(0)))
        except ValueError:
            continue
    return values


def parse_parameter_line(line: str, section: str, line_no: int) -> Optional[Parameter]:
    match = CLASS_LINE_RE.match(line)
    if match:
        rest = match.group("rest")
        description, raw_value = split_description_value(rest)
        return Parameter(
            index=int(match.group("idx")),
            variable=match.group("var").strip(),
            description=description,
            raw_value=raw_value,
            numeric_values=numeric_values(raw_value),
            section=section,
            line=line_no,
        )

    match = NUMERIC_LINE_RE.match(line)
    if not match or match.group("rest") is None:
        return None
    description, raw_value = split_description_value(match.group("rest"))
    if raw_value == "":
        return None
    return Parameter(
        index=int(match.group("idx")),
        variable=match.group("var").strip(),
        description=description,
        raw_value=raw_value,
        numeric_values=numeric_values(raw_value),
        section=section,
        line=line_no,
    )


def split_description_value(rest: str) -> tuple[str, str]:
    if ":" not in rest:
        return rest.strip(), ""
    description, raw_value = rest.rsplit(":", 1)
    return description.strip(), raw_value.strip()


def parse_desc(path: Path) -> List[PlantBlock]:
    blocks: List[PlantBlock] = []
    current: Optional[PlantBlock] = None
    current_section: Optional[str] = None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    for line_no, line in enumerate(lines, start=1):
        block_match = BLOCK_RE.match(line)
        if block_match:
            if current is not None:
                current.end_line = line_no - 1
            current = PlantBlock(
                nz=int(block_match.group("nz")),
                ny=int(block_match.group("ny")),
                nx=int(block_match.group("nx")),
                code=block_match.group("code"),
                start_line=line_no,
            )
            blocks.append(current)
            current_section = None
            continue

        if current is None:
            continue

        stripped = line.strip()
        if stripped in SECTION_NAMES:
            current_section = stripped
            current.sections.setdefault(stripped, [])
            continue

        if stripped.startswith("Plant name") and ":" in line:
            current.plant_name = line.rsplit(":", 1)[1].strip()
            continue

        if stripped.startswith("Koppen climate info") and ":" in line:
            current.koppen = line.rsplit(":", 1)[1].strip()
            continue

        if current_section is None:
            continue

        parameter = parse_parameter_line(line, current_section, line_no)
        if parameter is not None:
            current.parameters.append(parameter)
            current.sections.setdefault(current_section, []).append(parameter)

    if current is not None:
        current.end_line = len(lines)
    return blocks


def by_var(block: PlantBlock) -> Dict[str, List[Parameter]]:
    out: Dict[str, List[Parameter]] = {}
    for parameter in block.parameters:
        out.setdefault(parameter.variable, []).append(parameter)
    return out


def first_value(params: Dict[str, List[Parameter]], variable: str) -> Optional[float]:
    for parameter in params.get(variable, []):
        if parameter.numeric_values:
            return parameter.numeric_values[0]
    return None


def check_blocks(blocks: Iterable[PlantBlock]) -> List[Finding]:
    findings: List[Finding] = []

    def add(
        block: PlantBlock,
        severity: str,
        parameter: str,
        line: int,
        message: str,
        category: str = "general",
    ) -> None:
        findings.append(
            Finding(
                severity=severity,
                pft_code=block.code,
                nz=block.nz,
                ny=block.ny,
                nx=block.nx,
                line=line,
                parameter=parameter,
                message=message,
                category=category,
            )
        )

    for block in blocks:
        params = by_var(block)

        for section in REQUIRED_SECTIONS:
            if section not in block.sections:
                add(block, "ERROR", section, block.start_line, f"missing required section {section!r}")

        for parameter in block.parameters:
            if parameter.section == "PLANT CLASS INFORMATION":
                continue
            if not parameter.numeric_values:
                add(block, "ERROR", parameter.variable, parameter.line, "numeric trait row has no parseable numeric value")
                continue
            for value in parameter.numeric_values:
                if not math.isfinite(value):
                    add(block, "ERROR", parameter.variable, parameter.line, f"value is not finite: {value!r}")

        check_class(block, params, add)
        check_class_information_conventions(block, params, add)
        check_embryophyte_type(block, params, add)
        check_snow_interception_pattern(block, params, add)
        check_variable_ranges(block, params, add)
        check_petiole_sheath_angle(block, params, add)
        check_cross_parameters(block, params, add)
        check_root_radial_hydraulics(block, params, add)
        check_root_conduit_hydraulics(block, params, add)
        check_active_structural_protein_pools(block, params, add)
        check_physiological_parameterization(block, params, add)
        check_woody_form(block, params, add)

    return sort_findings(findings)


def sort_findings(findings: List[Finding]) -> List[Finding]:
    severity_rank = {"ERROR": 0, "WARN": 1}
    return sorted(findings, key=lambda f: (severity_rank.get(f.severity, 9), f.pft_code, f.line, f.parameter))


def check_class(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    class_params = params.get("CLASS", [])
    if not class_params:
        add(block, "ERROR", "CLASS", block.start_line, "missing leaf inclination CLASS fractions")
        return

    for parameter in class_params:
        values = parameter.numeric_values
        if len(values) != 4:
            add(block, "ERROR", "CLASS", parameter.line, f"expected four inclination fractions, found {len(values)}")
            continue
        for value in values:
            if value < 0 or value > 1:
                add(block, "ERROR", "CLASS", parameter.line, f"inclination fraction outside [0, 1]: {value:g}")
        total = sum(values)
        if abs(total - 1.0) > 1.0e-3:
            add(block, "ERROR", "CLASS", parameter.line, f"inclination fractions sum to {total:g}, expected 1")


def check_class_information_conventions(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    if not block_is_annual(params):
        return

    iwt_params = params.get("IWTYP", [])
    if not iwt_params:
        add(block, "WARN", "IWTYP", block.start_line, "annual plant is missing phenology type; EcoSIM treats annuals as evergreen")
        return

    for parameter in iwt_params:
        if not raw_has_token(parameter, "evergreen") and 0.0 not in parameter.numeric_values:
            add(
                block,
                "WARN",
                "IWTYP",
                parameter.line,
                f"annual plant has phenology {parameter.raw_value!r}; EcoSIM convention treats all annual plants as evergreen",
            )


def block_is_annual(params: Dict[str, List[Parameter]]) -> bool:
    return any(raw_has_token(parameter, "annual") for parameter in params.get("ISTYP", []))


def raw_has_token(parameter: Parameter, token: str) -> bool:
    return token.lower() in parameter.raw_value.lower()


def check_embryophyte_type(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    iebt_params = params.get("IEBTYP", [])
    if not iebt_params:
        add(
            block,
            "ERROR",
            "IEBTYP",
            block.start_line,
            f"missing embryophyte type; expected {format_embryophyte_mapping()}",
        )
        return

    expected_code = expected_embryophyte_code(block)
    for parameter in iebt_params:
        code = embryophyte_numeric_code(parameter)
        label_code = embryophyte_label_code(parameter.raw_value)

        if parameter.numeric_values and code is None:
            add(
                block,
                "ERROR",
                "IEBTYP",
                parameter.line,
                f"embryophyte type code must be an integer; expected {format_embryophyte_mapping()}",
            )
            continue
        if code is not None and code not in EMBRYOPHYTE_CODES:
            add(
                block,
                "ERROR",
                "IEBTYP",
                parameter.line,
                f"embryophyte type code {code} outside valid range; expected {format_embryophyte_mapping()}",
            )
            continue
        if code is None and label_code is None:
            add(
                block,
                "ERROR",
                "IEBTYP",
                parameter.line,
                f"embryophyte type must include an integer code or recognized label; expected {format_embryophyte_mapping()}",
            )
            continue

        actual_code = code if code is not None else label_code
        if code is not None and label_code is not None and label_code != code:
            add(
                block,
                "WARN",
                "IEBTYP",
                parameter.line,
                "embryophyte label suggests "
                f"{EMBRYOPHYTE_CODES[label_code]} ({label_code}) but numeric code is "
                f"{EMBRYOPHYTE_CODES[code]} ({code})",
            )

        if expected_code is not None and actual_code is not None and expected_code != actual_code:
            add(
                block,
                "WARN",
                "IEBTYP",
                parameter.line,
                f"{block.code} {block.plant_name!r} is expected to use embryophyte type "
                f"{EMBRYOPHYTE_CODES[expected_code]} ({expected_code}); found "
                f"{EMBRYOPHYTE_CODES[actual_code]} ({actual_code})",
            )


def embryophyte_numeric_code(parameter: Parameter) -> Optional[int]:
    if not parameter.numeric_values:
        return None
    value = parameter.numeric_values[-1]
    if not math.isfinite(value):
        return None
    rounded = round(value)
    if abs(value - rounded) > 1.0e-6:
        return None
    return int(rounded)


def embryophyte_label_code(raw_value: str) -> Optional[int]:
    raw_lower = raw_value.lower()
    for code, tokens in EMBRYOPHYTE_LABEL_TOKENS:
        if any(token in raw_lower for token in tokens):
            return code
    return None


def expected_embryophyte_code(block: PlantBlock) -> Optional[int]:
    if block.short_code in EMBRYOPHYTE_SHORT_CODE_EXPECTATIONS:
        return EMBRYOPHYTE_SHORT_CODE_EXPECTATIONS[block.short_code]
    return embryophyte_label_code(f"{block.code} {block.plant_name}")


def format_embryophyte_mapping() -> str:
    return ", ".join(f"{code}={name}" for code, name in EMBRYOPHYTE_CODES.items())


def check_snow_interception_pattern(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    isntyp_params = params.get("ISNTYP", [])
    if not isntyp_params:
        add(
            block,
            "ERROR",
            "ISNTYP",
            block.start_line,
            f"missing snow interception pattern; expected {format_snow_interception_mapping()}",
        )
        return

    expected_code = expected_snow_interception_code(block)
    for parameter in isntyp_params:
        code = snow_interception_numeric_code(parameter)
        if code is None:
            add(
                block,
                "ERROR",
                "ISNTYP",
                parameter.line,
                f"snow interception pattern must include an integer code; expected {format_snow_interception_mapping()}",
            )
            continue
        if code not in SNOW_INTERCEPTION_CODES:
            add(
                block,
                "ERROR",
                "ISNTYP",
                parameter.line,
                f"snow interception code {code} outside valid range; expected {format_snow_interception_mapping()}",
            )
            continue

        label_code = snow_interception_label_code(parameter.raw_value)
        if label_code is not None and label_code != code:
            add(
                block,
                "WARN",
                "ISNTYP",
                parameter.line,
                "snow interception label suggests "
                f"{SNOW_INTERCEPTION_CODES[label_code]} ({label_code}) but numeric code is "
                f"{SNOW_INTERCEPTION_CODES[code]} ({code})",
            )

        if expected_code is not None and expected_code != code:
            add(
                block,
                "WARN",
                "ISNTYP",
                parameter.line,
                f"{block.code} {block.plant_name!r} is expected to use snow interception "
                f"{SNOW_INTERCEPTION_CODES[expected_code]} ({expected_code}); found "
                f"{SNOW_INTERCEPTION_CODES[code]} ({code})",
            )


def snow_interception_numeric_code(parameter: Parameter) -> Optional[int]:
    if not parameter.numeric_values:
        return None
    value = parameter.numeric_values[-1]
    if not math.isfinite(value):
        return None
    rounded = round(value)
    if abs(value - rounded) > 1.0e-6:
        return None
    return int(rounded)


def snow_interception_label_code(raw_value: str) -> Optional[int]:
    raw_lower = raw_value.lower()
    for code, tokens in SNOW_INTERCEPTION_LABEL_TOKENS:
        if any(token in raw_lower for token in tokens):
            return code
    return None


def expected_snow_interception_code(block: PlantBlock) -> Optional[int]:
    if block.short_code in SNOW_INTERCEPTION_SHORT_CODE_EXPECTATIONS:
        return SNOW_INTERCEPTION_SHORT_CODE_EXPECTATIONS[block.short_code]
    return snow_interception_label_code(f"{block.code} {block.plant_name}")


def format_snow_interception_mapping() -> str:
    return ", ".join(f"{code}={name}" for code, name in SNOW_INTERCEPTION_CODES.items())


def check_variable_ranges(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    for variable in FRACTION_VARS:
        for parameter in params.get(variable, []):
            for value in parameter.numeric_values:
                if value < 0 or value > 1:
                    add(block, "ERROR", variable, parameter.line, f"fraction outside [0, 1]: {value:g}")

    for variable in POSITIVE_VARS:
        for parameter in params.get(variable, []):
            for value in parameter.numeric_values:
                if value <= 0:
                    add(block, "ERROR", variable, parameter.line, f"expected positive value, found {value:g}")

    for variable in NONNEGATIVE_VARS:
        for parameter in params.get(variable, []):
            for value in parameter.numeric_values:
                if value < 0:
                    add(block, "ERROR", variable, parameter.line, f"expected nonnegative value, found {value:g}")

    for variable, (lower, upper, meaning) in PHOTOSYNTHETIC_WARN_RANGES.items():
        for parameter in params.get(variable, []):
            if parameter.section != "PHOTOSYNTHETIC PROPERTIES":
                continue
            for value in parameter.numeric_values:
                if not in_range(value, (lower, upper)):
                    add(
                        block,
                        "WARN",
                        variable,
                        parameter.line,
                        f"{meaning} outside broad expected range [{lower:g}, {upper:g}]: {value:g}",
                        "physiology",
                    )

    for variable in YIELD_VARS:
        for parameter in params.get(variable, []):
            for value in parameter.numeric_values:
                if value <= 0:
                    add(block, "ERROR", variable, parameter.line, f"growth yield must be positive, found {value:g}")
                elif value > 1.2:
                    add(block, "WARN", variable, parameter.line, f"growth yield unusually high: {value:g}")

    for variable in CONCENTRATION_VARS:
        for parameter in params.get(variable, []):
            for value in parameter.numeric_values:
                if value <= 0:
                    add(block, "ERROR", variable, parameter.line, f"N/P concentration must be positive, found {value:g}")
                elif value > 0.2:
                    add(block, "WARN", variable, parameter.line, f"N/P concentration unusually high: {value:g} g element gC-1")

    for variable in ("ANGBR", "ANGSH"):
        for parameter in params.get(variable, []):
            for value in parameter.numeric_values:
                if value < 0 or value > 180:
                    add(block, "WARN", variable, parameter.line, f"angle outside expected [0, 180] degrees: {value:g}")

    osmo = params.get("OSMO", [])
    for parameter in osmo:
        for value in parameter.numeric_values:
            if value >= 0:
                add(block, "ERROR", "OSMO", parameter.line, f"osmotic potential should be negative MPa, found {value:g}")
            elif value < -5:
                add(block, "WARN", "OSMO", parameter.line, f"osmotic potential is very negative: {value:g} MPa")

    for parameter in params.get("WTSTDI", []):
        for value in parameter.numeric_values:
            if value < 0:
                add(block, "ERROR", "WTSTDI", parameter.line, f"standing dead biomass at planting is negative: {value:g}")

    for parameter in params.get("XDL", []):
        for value in parameter.numeric_values:
            if value > 24 or value < -24:
                add(block, "WARN", "XDL", parameter.line, f"critical photoperiod magnitude exceeds 24 h: {value:g}")


def check_petiole_sheath_angle(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    if not block_lacks_petiole_or_sheath(block):
        return

    angsh_params = params.get("ANGSH", [])
    if not angsh_params:
        add(
            block,
            "ERROR",
            "ANGSH",
            block.start_line,
            f"{block.code} {block.plant_name!r} lacks petiole/sheath tissue; ANGSH must be explicitly set to 0 degrees",
        )
        return

    for parameter in angsh_params:
        for value in parameter.numeric_values:
            if abs(value) > ANGSH_ZERO_TOLERANCE:
                add(
                    block,
                    "ERROR",
                    "ANGSH",
                    parameter.line,
                    f"{block.code} {block.plant_name!r} lacks petiole/sheath tissue; ANGSH must be 0 degrees, found {value:g}",
                )


def block_lacks_petiole_or_sheath(block: PlantBlock) -> bool:
    if block.short_code in NO_PETIOLE_SHEATH_SHORT_CODES:
        return True

    label = f"{block.code} {block.plant_name}".lower()
    return any(label_has_token(label, token) for token in NO_PETIOLE_SHEATH_LABEL_TOKENS)


def label_has_token(label: str, token: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", label) is not None


def check_cross_parameters(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    albr = first_value(params, "ALBR")
    taur = first_value(params, "TAUR")
    if albr is not None and taur is not None and albr + taur > 1.0 + 1.0e-6:
        line = params["ALBR"][0].line
        add(block, "ERROR", "ALBR/TAUR", line, f"shortwave albedo plus transmission is {albr + taur:g}, expected <= 1")

    albp = first_value(params, "ALBP")
    taup = first_value(params, "TAUP")
    if albp is not None and taup is not None and albp + taup > 1.0 + 1.0e-6:
        line = params["ALBP"][0].line
        add(block, "ERROR", "ALBP/TAUP", line, f"PAR albedo plus transmission is {albp + taup:g}, expected <= 1")

    rrad1m = first_value(params, "RRAD1M")
    rrad2m = first_value(params, "RRAD2M")
    if rrad1m is not None and rrad2m is not None and rrad2m > rrad1m:
        line = params["RRAD2M"][0].line
        add(block, "WARN", "RRAD2M", line, f"fine-root radius {rrad2m:g} exceeds primary-root radius {rrad1m:g}")

    phimin = first_value(params, "PhiMIN")
    phimax = first_value(params, "PhiMAX")
    if phimin is not None and phimax is not None and phimin > phimax:
        line = params["PhiMIN"][0].line
        add(block, "ERROR", "PhiMIN/PhiMAX", line, f"PhiMIN {phimin:g} exceeds PhiMAX {phimax:g}")


def check_root_radial_hydraulics(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    """Check RSRR as radial resistivity and report implausible implied conductivity."""

    rsrr_params = params.get("RSRR", [])
    if not rsrr_params:
        add(block, "ERROR", "RSRR", block.start_line, "missing radial root resistivity RSRR", "hydraulics")
        return

    rsrr = first_value(params, "RSRR")
    if rsrr is None or rsrr <= 0:
        return

    conductivity = 1.0 / (SECONDS_PER_HOUR * rsrr)
    line = rsrr_params[0].line
    if block_is_conifer(block):
        lower, upper = CONIFER_RSRR_WARN_RANGE_MPA_H_PER_M
        if rsrr < lower or rsrr > upper:
            add(
                block,
                "WARN",
                "RSRR",
                line,
                f"RSRR {rsrr:g} MPa h m-1 implies fully wetted radial conductivity "
                f"{conductivity:.3g} m s-1 MPa-1, outside the broad conifer resistance screen "
                f"[{lower:g}, {upper:g}] MPa h m-1; high RSRR means low radial conductivity, "
                "and EcoSIM increases the effective resistance further as micropores dry",
                "hydraulics",
            )
    else:
        lower, upper = ROOT_RADIAL_CONDUCTIVITY_BROAD_RANGE_M_PER_S_MPA
        if conductivity < lower or conductivity > upper:
            add(
                block,
                "WARN",
                "RSRR",
                line,
                f"RSRR {rsrr:g} MPa h m-1 implies fully wetted radial conductivity "
                f"{conductivity:.3g} m s-1 MPa-1, outside the broad cross-study screen "
                f"[{lower:.3g}, {upper:.3g}] m s-1 MPa-1",
                "hydraulics",
            )


def root_conduit_geometry(params: Dict[str, List[Parameter]]) -> Dict[str, Optional[float]]:
    """Equal-conduit geometry used by the inspected StartqMod implementation."""
    rvsr = first_value(params, "RVSR")
    rrad2m = first_value(params, "RRAD2M")
    valid_root = rrad2m is not None and math.isfinite(rrad2m) and rrad2m > 0
    valid_vessel = rvsr is not None and math.isfinite(rvsr) and rvsr > 0
    return {
        "fine_root_radius_m": rrad2m,
        "root_conduit_radius_m": rvsr,
        "root_lumen_area_fraction": ROOT_LUMEN_AREA_FRACTION,
        "implied_root_conduit_count": (
            ROOT_LUMEN_AREA_FRACTION * (rrad2m / rvsr) ** 2
            if valid_root and valid_vessel else None
        ),
        "max_root_conduit_radius_for_one_m": (
            math.sqrt(ROOT_LUMEN_AREA_FRACTION) * rrad2m if valid_root else None
        ),
    }


def check_root_conduit_hydraulics(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    """Check literal root-conduit geometry and its axial-resistance correction."""

    rvsr_params = params.get("RVSR", [])
    if not rvsr_params:
        add(block, "ERROR", "RVSR", block.start_line, "missing literal mean root water-conduit radius RVSR")
    else:
        rvsr = first_value(params, "RVSR")
        if rvsr is not None and rvsr > 0:
            rvsr_line = rvsr_params[0].line
            if block_is_conifer(block):
                lower, upper = CONIFER_CONDUIT_RADIUS_WARN_RANGE_M
                if rvsr < lower or rvsr > upper:
                    add(
                        block,
                        "WARN",
                        "RVSR",
                        rvsr_line,
                        "literal mean conifer tracheid radius "
                        f"{rvsr * 1.0e6:g} um (diameter {2.0 * rvsr * 1.0e6:g} um) is outside "
                        f"the broad anatomical screen [{lower * 1.0e6:g}, {upper * 1.0e6:g}] um radius; "
                        "use ARSRA, not an artificially small RVSR, for pit and end-wall resistance correction",
                        "hydraulics",
                    )

            geometry = root_conduit_geometry(params)
            rrad2m = geometry["fine_root_radius_m"]
            conduit_count = geometry["implied_root_conduit_count"]
            if conduit_count is not None:
                if conduit_count < 1.0 - PHYSIOLOGY_FLOAT_TOLERANCE:
                    add(
                        block,
                        "ERROR",
                        "RVSR/RRAD2M",
                        rvsr_line,
                        f"RVSR {rvsr:g} m and RRAD2M {rrad2m:g} m imply only "
                        f"{conduit_count:g} conduits in EcoSIM's {ROOT_LUMEN_AREA_FRACTION:g} lumen-area fraction; "
                        "expected at least one conduit: N = 0.2*(RRAD2M/RVSR)^2 >= 1, "
                        f"requiring RVSR <= sqrt(0.2)*RRAD2M = {geometry['max_root_conduit_radius_for_one_m']:g} m "
                        f"({geometry['max_root_conduit_radius_for_one_m'] * 1e6:g} um). "
                        "Review root class, conduit anatomy and the fixed lumen-area assumption jointly; "
                        "do not automatically change either radius to satisfy this bound",
                        "hydraulics",
                    )

    arsra_params = params.get("ARSRA", [])
    if not arsra_params:
        add(block, "ERROR", "ARSRA", block.start_line, "missing axial conduit-resistance correction ARSRA")
        return

    arsra = first_value(params, "ARSRA")
    if arsra is not None and 0 < arsra < 1.0 - PHYSIOLOGY_FLOAT_TOLERANCE:
        add(
            block,
            "WARN",
            "ARSRA",
            arsra_params[0].line,
            f"ARSRA {arsra:g} is below 1 and would make the actual conduit less resistant than its ideal lumen; "
            "expected ARSRA >= 1",
            "hydraulics",
        )


def block_is_conifer(block: PlantBlock) -> bool:
    return expected_snow_interception_code(block) == 4 or expected_embryophyte_code(block) == 2


def active_structural_protein_pool(
    params: Dict[str, List[Parameter]],
    organ: str,
) -> Optional[tuple[float, float, float]]:
    """Return N-supported, P-supported, and realized protein C per structural C."""

    protein_c_to_n_var, n_per_c_var, protein_c_to_p_var, p_per_c_var = (
        ACTIVE_STRUCTURAL_PROTEIN_POOLS[organ]
    )
    values = (
        first_value(params, protein_c_to_n_var),
        first_value(params, n_per_c_var),
        first_value(params, protein_c_to_p_var),
        first_value(params, p_per_c_var),
    )
    if any(value is None or value <= 0 for value in values):
        return None

    protein_c_to_n, n_per_c, protein_c_to_p, p_per_c = values
    n_supported = protein_c_to_n * n_per_c
    p_supported = protein_c_to_p * p_per_c
    return n_supported, p_supported, min(n_supported, p_supported)


def effective_leaf_protein_c_to_total_n(
    params: Dict[str, List[Parameter]],
) -> Optional[float]:
    pool = active_structural_protein_pool(params, "leaf")
    leaf_n_per_c = first_value(params, "CNLF")
    if pool is None or leaf_n_per_c is None or leaf_n_per_c <= 0:
        return None
    return pool[2] / leaf_n_per_c


def check_active_structural_protein_pools(
    block: PlantBlock,
    params: Dict[str, List[Parameter]],
    add,
) -> None:
    """Check N/P support for protein C in active leaf and root structure."""

    is_tree = block.short_code in WOODY_SHORT_CODES
    for organ, variables in ACTIVE_STRUCTURAL_PROTEIN_POOLS.items():
        missing = [variable for variable in variables if first_value(params, variable) is None]
        if missing:
            add(
                block,
                "WARN",
                "/".join(missing),
                block.start_line,
                f"cannot derive {organ} protein C from active structural N and P; "
                f"missing numeric values for {', '.join(missing)}",
                "physiology",
            )
            continue

        pool = active_structural_protein_pool(params, organ)
        if pool is None:
            continue
        n_supported, p_supported, realized = pool
        protein_c_to_n_var, n_per_c_var, protein_c_to_p_var, p_per_c_var = variables
        parameter_name = (
            f"{protein_c_to_n_var}*{n_per_c_var}/"
            f"{protein_c_to_p_var}*{p_per_c_var}"
        )
        line = params[protein_c_to_n_var][0].line

        if organ == "leaf":
            scope = "total leaf structural C because all leaf structure is active"
        elif is_tree:
            scope = "active root structural C, excluding lignified heartwood"
        else:
            scope = "total root structural C because all non-tree root structure is active"

        if realized > 1.0 + PHYSIOLOGY_FLOAT_TOLERANCE:
            add(
                block,
                "ERROR",
                parameter_name,
                line,
                f"N/P-limited {organ} protein C is {realized:.3f} gC protein per gC "
                f"of {scope}, which exceeds the available structural C",
                "physiology",
            )
            continue

        for support, nutrient, ratio_var, concentration_var in (
            (n_supported, "N", protein_c_to_n_var, n_per_c_var),
            (p_supported, "P", protein_c_to_p_var, p_per_c_var),
        ):
            if support > 1.0 + PHYSIOLOGY_FLOAT_TOLERANCE:
                add(
                    block,
                    "WARN",
                    f"{ratio_var}*{concentration_var}",
                    params[ratio_var][0].line,
                    f"{nutrient}-supported {organ} protein C is {support:.3f} gC protein "
                    f"per gC of {scope}; the realized pool remains {realized:.3f} because "
                    "EcoSIM uses the smaller N- and P-supported amount",
                    "physiology",
                )


def block_is_c4(params: Dict[str, List[Parameter]]) -> bool:
    return photosynthesis_pathway(params) == "C4"


def photosynthesis_pathway(params: Dict[str, List[Parameter]]) -> Optional[str]:
    for parameter in params.get("ICTYP", []):
        raw = parameter.raw_value.lower()
        if re.search(r"(?<![a-z0-9])c4(?![a-z0-9])", raw):
            return "C4"
        if re.search(r"(?<![a-z0-9])c3(?![a-z0-9])", raw):
            return "C3"
    return None


def check_physiological_parameterization(
    block: PlantBlock,
    params: Dict[str, List[Parameter]],
    add,
) -> None:
    """Flag compensating photosynthetic inputs that are not physiologically clean."""

    pathway = photosynthesis_pathway(params)
    if pathway is None:
        line = params["ICTYP"][0].line if params.get("ICTYP") else block.start_line
        add(
            block,
            "WARN",
            "ICTYP",
            line,
            "cannot determine C3 or C4 pathway; pathway-specific physiological checks were skipped",
            "physiology",
        )
        return

    def warn(parameter: str, line: int, message: str) -> None:
        add(block, "WARN", parameter, line, message, "physiology")

    for variable in PHYSIOLOGY_REQUIRED_VARS[pathway]:
        if first_value(params, variable) is None:
            warn(
                variable,
                block.start_line,
                f"{pathway} clean-physiology check requires {variable}, but no numeric value was found",
            )

    for variable, (lower, upper, meaning) in PHYSIOLOGY_PATHWAY_RANGES[pathway].items():
        value = first_value(params, variable)
        if value is not None and not in_range(value, (lower, upper)):
            warn(
                variable,
                params[variable][0].line,
                f"{pathway} {meaning} {value:g} is outside broad physiological range "
                f"[{lower:g}, {upper:g}]",
            )

    vcmx = first_value(params, "VCMX")
    vomx = first_value(params, "VOMX")
    kc = first_value(params, "XKCO2")
    ko = first_value(params, "XKO2")
    rubp = first_value(params, "RUBP")
    effective_cnwl = effective_leaf_protein_c_to_total_n(params)
    pepc = first_value(params, "PEPC")
    vcmx4 = first_value(params, "VCMX4")
    etmx = first_value(params, "ETMX")
    chl = first_value(params, "CHL")
    fchlmeso = first_value(params, "fCHLMESO")

    if all(value is not None and value > 0 for value in (vcmx, vomx, kc, ko)):
        turnover_ratio = vcmx / vomx
        turnover_range = (
            C4_CARBOXYLATION_TO_OXYGENATION_WARN_RANGE
            if pathway == "C4"
            else C3_CARBOXYLATION_TO_OXYGENATION_WARN_RANGE
        )
        if not in_range(turnover_ratio, turnover_range):
            warn(
                "VCMX/VOMX",
                params["VCMX"][0].line,
                f"{pathway} Rubisco VCMX/VOMX turnover ratio is {turnover_ratio:.2f}, outside "
                f"[{turnover_range[0]:g}, {turnover_range[1]:g}]; do not compensate an "
                "implausible turnover ratio with Km values",
            )

        specificity = vcmx * ko / (vomx * kc)
        lower, upper = RUBISCO_SPECIFICITY_WARN_RANGE
        if not in_range(specificity, (lower, upper)):
            warn(
                "VCMX/VOMX/XKCO2/XKO2",
                params["VCMX"][0].line,
                f"Rubisco CO2/O2 specificity implied by VCMX*XKO2/(VOMX*XKCO2) is "
                f"{specificity:.1f}, outside [{lower:g}, {upper:g}]",
            )

    if rubp is not None and effective_cnwl is not None and rubp >= 0:
        implied_rubisco_n_fraction = rubp * effective_cnwl / PROTEIN_C_TO_N_MASS_RATIO
        rubisco_range = (
            C4_RUBISCO_LEAF_N_FRACTION_WARN_RANGE
            if pathway == "C4"
            else C3_RUBISCO_LEAF_N_FRACTION_WARN_RANGE
        )
        if not in_range(implied_rubisco_n_fraction, rubisco_range):
            warn(
                "RUBP/leaf-protein-pool",
                params["RUBP"][0].line,
                f"RUBP {rubp:g} and the N/P-limited leaf protein pool "
                f"({effective_cnwl:g} gC protein gN-1) imply "
                f"{100.0 * implied_rubisco_n_fraction:.2f}% of total leaf N in Rubisco, "
                f"outside broad {pathway} range [{100.0 * rubisco_range[0]:g}, "
                f"{100.0 * rubisco_range[1]:g}]%",
            )

    if pathway == "C4" and pepc is not None and effective_cnwl is not None and pepc >= 0:
        pepc_c_per_leaf_n = pepc * effective_cnwl
        implied_pepc_n_fraction = pepc_c_per_leaf_n / PROTEIN_C_TO_N_MASS_RATIO
        if not in_range(implied_pepc_n_fraction, C4_PEPC_LEAF_N_FRACTION_WARN_RANGE):
            lower, upper = C4_PEPC_LEAF_N_FRACTION_WARN_RANGE
            warn(
                "PEPC/leaf-protein-pool",
                params["PEPC"][0].line,
                f"PEPC {pepc:g} and the N/P-limited leaf protein pool "
                f"({effective_cnwl:g} gC protein gN-1) imply "
                f"{100.0 * implied_pepc_n_fraction:.2f}% of total leaf N allocated to PEPC "
                f"(PEPC*effective_CNWL={pepc_c_per_leaf_n:g} gC PEPC gN-1; "
                f"protein C:N={PROTEIN_C_TO_N_MASS_RATIO:g}), outside broad C4 range "
                f"[{100.0 * lower:g}, {100.0 * upper:g}]%",
            )

    allocations = [("RUBP", rubp), ("CHL", chl)]
    if pathway == "C4":
        allocations.append(("PEPC", pepc))
    if all(value is not None and value >= 0 for _, value in allocations):
        allocation_sum = sum(value for _, value in allocations)
        if allocation_sum > PHOTOSYNTHETIC_PROTEIN_ALLOCATION_MAX + PHYSIOLOGY_FLOAT_TOLERANCE:
            warn(
                "+".join(variable for variable, _ in allocations),
                params[allocations[0][0]][0].line,
                f"photosynthetic protein fractions sum to {allocation_sum:.3f}, above "
                f"{PHOTOSYNTHETIC_PROTEIN_ALLOCATION_MAX:g}; allocations must share the "
                "same total-leaf-protein pool",
            )

    rubisco_capacity = None
    if vcmx is not None and rubp is not None and vcmx > 0 and rubp > 0:
        rubisco_capacity = vcmx * rubp

    if (
        pathway == "C4"
        and rubisco_capacity is not None
        and vcmx4 is not None
        and pepc is not None
        and vcmx4 > 0
        and pepc > 0
    ):
        vpmax_to_vcmax = vcmx4 * pepc / rubisco_capacity
        if not in_range(vpmax_to_vcmax, C4_VPMAX_TO_VCMAX_WARN_RANGE):
            lower, upper = C4_VPMAX_TO_VCMAX_WARN_RANGE
            warn(
                "VCMX4*PEPC/VCMX*RUBP",
                params["VCMX4"][0].line,
                f"protein-normalized Vpmax:Vcmax is {vpmax_to_vcmax:.2f}, outside broad "
                f"C4 range [{lower:g}, {upper:g}]; VCMX4 and PEPC must be calibrated "
                "together against the Rubisco capacity pair",
            )

    if rubisco_capacity is not None and etmx is not None and chl is not None and etmx > 0 and chl > 0:
        if pathway == "C4" and fchlmeso is not None and fchlmeso > 0:
            jmax_to_vcmax = etmx * chl * fchlmeso / (3.7 * rubisco_capacity)
            jmax_range = C4_JMAX_TO_VCMAX_WARN_RANGE
        elif pathway == "C3":
            jmax_to_vcmax = etmx * chl / (3.5 * rubisco_capacity)
            jmax_range = C3_JMAX_TO_VCMAX_WARN_RANGE
        else:
            jmax_to_vcmax = None
            jmax_range = None

        if jmax_to_vcmax is not None and jmax_range is not None and not in_range(jmax_to_vcmax, jmax_range):
            warn(
                "ETMX*CHL/VCMX*RUBP",
                params["ETMX"][0].line,
                f"model-formulation-sensitive protein-normalized Jmax:Vcmax diagnostic is "
                f"{jmax_to_vcmax:.2f}, outside broad {pathway} range "
                f"[{jmax_range[0]:g}, {jmax_range[1]:g}]; interpret ETMX and CHL within "
                "EcoSIM and prioritize direct protein-allocation evidence",
            )

    check_absorptance_pair(
        block,
        params,
        add,
        "ALBP",
        "TAUP",
        "PAR",
        PAR_ABSORPTANCE_WARN_RANGE,
    )
    check_absorptance_pair(
        block,
        params,
        add,
        "ALBR",
        "TAUR",
        "shortwave",
        SHORTWAVE_ABSORPTANCE_WARN_RANGE,
    )


def in_range(value: float, bounds: tuple[float, float]) -> bool:
    return (
        bounds[0] - PHYSIOLOGY_FLOAT_TOLERANCE
        <= value
        <= bounds[1] + PHYSIOLOGY_FLOAT_TOLERANCE
    )


def check_absorptance_pair(
    block: PlantBlock,
    params: Dict[str, List[Parameter]],
    add,
    albedo_variable: str,
    transmission_variable: str,
    band: str,
    expected_range: tuple[float, float],
) -> None:
    albedo = first_value(params, albedo_variable)
    transmission = first_value(params, transmission_variable)
    if albedo is None or transmission is None or albedo + transmission > 1.0:
        return
    absorptance = 1.0 - albedo - transmission
    if not in_range(absorptance, expected_range):
        add(
            block,
            "WARN",
            f"{albedo_variable}/{transmission_variable}",
            params[albedo_variable][0].line,
            f"leaf {band} absorptance 1-{albedo_variable}-{transmission_variable} is "
            f"{absorptance:.3f}, outside broad range [{expected_range[0]:g}, "
            f"{expected_range[1]:g}]",
            "physiology",
        )


def enforce_strict_physiology(findings: List[Finding]) -> List[Finding]:
    """Promote physiology warnings except model-formulation-sensitive diagnostics."""

    promoted = []
    for finding in findings:
        if (
            finding.category == "physiology"
            and finding.severity == "WARN"
            and finding.parameter not in LOW_PRIORITY_PHYSIOLOGY_PARAMETERS
        ):
            promoted.append(
                Finding(
                    severity="ERROR",
                    pft_code=finding.pft_code,
                    nz=finding.nz,
                    ny=finding.ny,
                    nx=finding.nx,
                    line=finding.line,
                    parameter=finding.parameter,
                    message=f"strict physiology: {finding.message}",
                    category=finding.category,
                )
            )
        else:
            promoted.append(finding)
    return sort_findings(promoted)


def check_woody_form(block: PlantBlock, params: Dict[str, List[Parameter]], add) -> None:
    is_woody = block.short_code in WOODY_SHORT_CODES
    allows_secondary_growth_root_traits = block.short_code in SECONDARY_GROWTH_HERBACEOUS_SHORT_CODES
    woody_required = ("ROOTMAGE", "PhiMIN", "PhiMAX", "R95MAT")

    if is_woody:
        for variable in woody_required:
            if variable not in params:
                add(block, "WARN", variable, block.start_line, f"woody PFT {block.code} is missing {variable}")
        if "PhiMean" in params:
            add(block, "WARN", "PhiMean", params["PhiMean"][0].line, "woody PFT contains herbaceous PhiMean root trait")
    else:
        has_phi_min_max = "PhiMIN" in params and "PhiMAX" in params
        if "PhiMean" not in params and not has_phi_min_max:
            add(block, "WARN", "PhiMean", block.start_line, f"non-woody PFT {block.code} is missing PhiMean")
        for variable in SECONDARY_GROWTH_ROOT_TRAITS:
            if variable in params:
                if allows_secondary_growth_root_traits:
                    continue
                add(
                    block,
                    "WARN",
                    variable,
                    params[variable][0].line,
                    f"non-woody PFT {block.code} contains woody root trait {variable}",
                )


def check_web_evidence(blocks: Iterable[PlantBlock], evidence: Dict[str, Any]) -> List[Finding]:
    """Compare trait values to web-sourced evidence already converted to EcoSIM units."""

    findings: List[Finding] = []

    def add(block: PlantBlock, severity: str, parameter: str, line: int, message: str) -> None:
        findings.append(
            Finding(
                severity=severity,
                pft_code=block.code,
                nz=block.nz,
                ny=block.ny,
                nx=block.nx,
                line=line,
                parameter=parameter,
                message=message,
            )
        )

    plant_rules = evidence.get("plants", [])
    if not isinstance(plant_rules, list):
        return findings

    for plant_rule in plant_rules:
        if not isinstance(plant_rule, dict):
            continue
        matching_blocks = [block for block in blocks if evidence_matches_block(block, plant_rule)]
        for block in matching_blocks:
            params = by_var(block)
            for range_rule in iter_rules(plant_rule, "numeric_ranges", "ranges"):
                check_web_numeric_range(block, params, with_plant_context(plant_rule, range_rule), add)
            for categorical_rule in iter_rules(plant_rule, "categorical_expectations", "categories"):
                check_web_categorical(block, params, with_plant_context(plant_rule, categorical_rule), add)

    return sort_findings(findings)


def iter_rules(plant_rule: Dict[str, Any], *names: str) -> Iterable[Dict[str, Any]]:
    for name in names:
        rules = plant_rule.get(name, [])
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, dict):
                    yield rule


def with_plant_context(plant_rule: Dict[str, Any], rule: Dict[str, Any]) -> Dict[str, Any]:
    context_keys = ("taxon", "evidence_level")
    merged = {key: plant_rule[key] for key in context_keys if key in plant_rule}
    merged.update(rule)
    return merged


def evidence_matches_block(block: PlantBlock, plant_rule: Dict[str, Any]) -> bool:
    pft_code = plant_rule.get("pft_code")
    short_code = plant_rule.get("short_code")
    nz = plant_rule.get("nz")
    ny = plant_rule.get("ny")
    nx = plant_rule.get("nx")

    if pft_code is not None and str(pft_code).lower() != block.code.lower():
        return False
    if short_code is not None and str(short_code).lower() != block.short_code:
        return False
    if nz is not None and int(nz) != block.nz:
        return False
    if ny is not None and int(ny) != block.ny:
        return False
    if nx is not None and int(nx) != block.nx:
        return False
    return any(value is not None for value in (pft_code, short_code, nz, ny, nx))


def check_web_numeric_range(block: PlantBlock, params: Dict[str, List[Parameter]], rule: Dict[str, Any], add) -> None:
    variable = str(rule.get("variable", "")).strip()
    if not variable:
        return
    severity = str(rule.get("severity", "WARN")).upper()
    lower = optional_float(rule.get("min"))
    upper = optional_float(rule.get("max"))
    if lower is None and upper is None:
        return

    matching_params = params.get(variable, [])
    if not matching_params:
        add(block, severity, variable, block.start_line, f"web evidence expects {variable}, but the trait is absent{evidence_suffix(rule)}")
        return

    for parameter in matching_params:
        for value in parameter.numeric_values:
            below = lower is not None and value < lower
            above = upper is not None and value > upper
            if below or above:
                range_text = format_range(lower, upper, rule.get("unit"))
                add(
                    block,
                    severity,
                    variable,
                    parameter.line,
                    f"value {value:g} is outside web-evidence range {range_text}{evidence_suffix(rule)}",
                )


def check_web_categorical(block: PlantBlock, params: Dict[str, List[Parameter]], rule: Dict[str, Any], add) -> None:
    variable = str(rule.get("variable", "")).strip()
    if not variable:
        return

    severity = str(rule.get("severity", "WARN")).upper()
    matching_params = params.get(variable, [])
    if not matching_params:
        add(block, severity, variable, block.start_line, f"web evidence expects {variable}, but the trait is absent{evidence_suffix(rule)}")
        return

    allowed = rule.get("allowed_values")
    contains = rule.get("contains", rule.get("expected_contains"))
    if isinstance(contains, str):
        contains_values = [contains]
    elif isinstance(contains, list):
        contains_values = [str(value) for value in contains]
    else:
        contains_values = []

    allowed_values = [str(value).lower() for value in allowed] if isinstance(allowed, list) else []

    for parameter in matching_params:
        raw = parameter.raw_value.strip()
        raw_lower = raw.lower()
        if variable == "IWTYP" and block_is_annual(params):
            allowed_values = [value for value in allowed_values if value == "0" or "evergreen" in value]
            contains_values = [
                value for value in contains_values if value.strip() == "0" or "evergreen" in value.lower()
            ]
        if allowed_values and raw_lower not in allowed_values:
            add(
                block,
                severity,
                variable,
                parameter.line,
                f"value {raw!r} is not in web-evidence allowed values {allowed_values}{evidence_suffix(rule)}",
            )
        for expected in contains_values:
            if expected.lower() not in raw_lower:
                add(
                    block,
                    severity,
                    variable,
                    parameter.line,
                    f"value {raw!r} does not contain web-evidence expectation {expected!r}{evidence_suffix(rule)}",
                )


def optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_range(lower: Optional[float], upper: Optional[float], unit: Any) -> str:
    unit_text = f" {unit}" if unit else ""
    if lower is None:
        return f"<= {upper:g}{unit_text}"
    if upper is None:
        return f">= {lower:g}{unit_text}"
    return f"[{lower:g}, {upper:g}]{unit_text}"


def evidence_suffix(rule: Dict[str, Any]) -> str:
    parts = []
    taxon = rule.get("taxon")
    source = rule.get("source")
    url = rule.get("url")
    note = rule.get("conversion_note") or rule.get("note")
    evidence_level = rule.get("evidence_level")
    if taxon:
        parts.append(f"taxon={taxon}")
    if evidence_level:
        parts.append(f"level={evidence_level}")
    if source:
        parts.append(f"source={source}")
    if url:
        parts.append(f"url={url}")
    if note:
        parts.append(f"note={note}")
    return "" if not parts else " (" + "; ".join(str(part) for part in parts) + ")"


def block_summary(block: PlantBlock, findings: List[Finding]) -> Dict[str, object]:
    block_findings = [
        f
        for f in findings
        if f.pft_code == block.code and f.nz == block.nz and f.ny == block.ny and f.nx == block.nx
    ]
    params = by_var(block)
    rsrr = first_value(params, "RSRR")
    radial_conductivity = (
        1.0 / (SECONDS_PER_HOUR * rsrr)
        if rsrr is not None and rsrr > 0
        else None
    )
    return {
        "pft_code": block.code,
        "nz": block.nz,
        "ny": block.ny,
        "nx": block.nx,
        "plant_name": block.plant_name,
        "koppen": block.koppen,
        "start_line": block.start_line,
        "end_line": block.end_line,
        "parameter_count": len(block.parameters),
        "section_count": len(block.sections),
        "rsrr_mpa_h_per_m": rsrr,
        "root_radial_conductivity_m_per_s_mpa": radial_conductivity,
        **root_conduit_geometry(params),
        "errors": sum(1 for f in block_findings if f.severity == "ERROR"),
        "warnings": sum(1 for f in block_findings if f.severity == "WARN"),
    }


def render_markdown(
    path: Path,
    blocks: List[PlantBlock],
    selected: List[PlantBlock],
    findings: List[Finding],
    ny: int,
    nx: int,
    web_evidence: Optional[Path],
    strict_physiology: bool,
) -> str:
    errors = sum(1 for f in findings if f.severity == "ERROR")
    warnings = sum(1 for f in findings if f.severity == "WARN")
    lines = [
        "# EcoSIM plant trait sanity check",
        "",
        f"- File: `{path}`",
        f"- Grid scope: `NY={ny}, NX={nx}`",
        f"- Plant blocks checked: {len(selected)} of {len(blocks)} total blocks",
        f"- Web evidence: `{web_evidence}`" if web_evidence else "- Web evidence: not provided",
        f"- Strict physiology: {'enabled' if strict_physiology else 'disabled'}",
        f"- Findings: {errors} ERROR, {warnings} WARN",
        "",
        "## Findings",
        "",
    ]

    if findings:
        for finding in findings:
            location = f"{finding.pft_code} NZ={finding.nz} NY={finding.ny} NX={finding.nx}"
            lines.append(
                f"- {finding.severity} line {finding.line} `{location}` `{finding.parameter}`: {finding.message}"
            )
    else:
        lines.append("- No ERROR or WARN findings for the selected grid.")

    lines.extend(["", "## Plant Summary", ""])
    for block in selected:
        summary = block_summary(block, findings)
        name = f" - {block.plant_name}" if block.plant_name else ""
        rsrr_summary = ""
        if summary["rsrr_mpa_h_per_m"] is not None:
            rsrr_summary = (
                f", RSRR {summary['rsrr_mpa_h_per_m']:g} MPa h m-1 -> "
                f"k_radial {summary['root_radial_conductivity_m_per_s_mpa']:.3g} m s-1 MPa-1"
            )
        conduit_summary = ""
        if summary["implied_root_conduit_count"] is not None:
            conduit_summary = (
                f", RRAD2M {summary['fine_root_radius_m']:g} m, "
                f"RVSR {summary['root_conduit_radius_m']:g} m -> "
                f"N_conduits {summary['implied_root_conduit_count']:.4g} "
                f"(assumed lumen fraction {summary['root_lumen_area_fraction']:g}), "
                f"RVSR limit for N>=1 {summary['max_root_conduit_radius_for_one_m']:.6g} m"
            )
        lines.append(
            f"- `{block.label}`{name}: {summary['parameter_count']} parameters, "
            f"{summary['section_count']} sections, {summary['errors']} ERROR, {summary['warnings']} WARN"
            f"{rsrr_summary}{conduit_summary}"
        )

    return "\n".join(lines) + "\n"


def build_json(
    path: Path,
    blocks: List[PlantBlock],
    selected: List[PlantBlock],
    findings: List[Finding],
    ny: int,
    nx: int,
    web_evidence: Optional[Path],
    strict_physiology: bool,
) -> Dict[str, object]:
    return {
        "source_file": str(path),
        "grid_scope": {"ny": ny, "nx": nx},
        "web_evidence_file": str(web_evidence) if web_evidence else None,
        "strict_physiology": strict_physiology,
        "total_blocks": len(blocks),
        "checked_blocks": len(selected),
        "finding_counts": {
            "ERROR": sum(1 for f in findings if f.severity == "ERROR"),
            "WARN": sum(1 for f in findings if f.severity == "WARN"),
        },
        "findings": [finding.as_dict() for finding in findings],
        "plants": [block_summary(block, findings) for block in selected],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("desc_file", type=Path, help="EcoSIM plant_trait.*.desc file")
    parser.add_argument("--ny", type=int, default=1, help="NY grid index to check; default: 1")
    parser.add_argument("--nx", type=int, default=1, help="NX grid index to check; default: 1")
    parser.add_argument("--web-evidence", type=Path, help="JSON file of web-sourced trait evidence converted to EcoSIM units")
    parser.add_argument(
        "--strict-physiology",
        action="store_true",
        help="promote physiological-consistency warnings to errors",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if not args.desc_file.exists():
        print(f"ERROR: file not found: {args.desc_file}", file=sys.stderr)
        return 2

    blocks = parse_desc(args.desc_file)
    selected = [block for block in blocks if block.ny == args.ny and block.nx == args.nx]
    if not selected:
        available = sorted({(block.ny, block.nx) for block in blocks})
        print(
            f"ERROR: no plant blocks found for NY={args.ny}, NX={args.nx}. "
            f"Available grid tuples: {available}",
            file=sys.stderr,
        )
        return 2

    findings = check_blocks(selected)
    web_evidence = None
    if args.web_evidence is not None:
        if not args.web_evidence.exists():
            print(f"ERROR: web evidence file not found: {args.web_evidence}", file=sys.stderr)
            return 2
        try:
            web_evidence = json.loads(args.web_evidence.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"ERROR: could not parse web evidence JSON: {exc}", file=sys.stderr)
            return 2
        findings = sort_findings(findings + check_web_evidence(selected, web_evidence))

    if args.strict_physiology:
        findings = enforce_strict_physiology(findings)

    if args.json:
        print(
            json.dumps(
                build_json(
                    args.desc_file,
                    blocks,
                    selected,
                    findings,
                    args.ny,
                    args.nx,
                    args.web_evidence,
                    args.strict_physiology,
                ),
                indent=2,
            )
        )
    else:
        print(
            render_markdown(
                args.desc_file,
                blocks,
                selected,
                findings,
                args.ny,
                args.nx,
                args.web_evidence,
                args.strict_physiology,
            ),
            end="",
        )

    return 1 if any(f.severity == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
