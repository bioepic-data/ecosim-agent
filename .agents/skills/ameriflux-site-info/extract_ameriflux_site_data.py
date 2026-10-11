#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.request import Request, urlopen

try:
    import requests
except ImportError:  # pragma: no cover - cached metadata should still work.
    requests = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - requests-only extraction should still work.
    sync_playwright = None


AMERIFLUX_SITEINFO_URL = "https://ameriflux.lbl.gov/sites/siteinfo/{site_id}"

KOPPEN_MAP = {
    "Af": 11, "Am": 12, "As": 13, "Aw": 14, "BWk": 21, "BWh": 22, "BSk": 26, "BSh": 27,
    "Cfa": 31, "Cfb": 32, "Cfc": 33, "Csa": 34, "Csb": 35, "Csc": 36, "Cwa": 37, "Cwb": 38, "Cwc": 39,
    "Dfa": 41, "Dfb": 42, "Dfc": 43, "Dfd": 44, "Dsa": 45, "Dsb": 46, "Dsc": 47, "Dsd": 48,
    "Dwa": 49, "Dwb": 50, "Dwc": 51, "Dwd": 52, "ET": 61, "EF": 62,
}
KOPPEN_BY_UPPER = {code.upper(): code for code in KOPPEN_MAP}

IGBP_CODES = {
    "ENF", "EBF", "DNF", "DBF", "MF", "CSH", "OSH", "WSA", "SAV", "GRA",
    "WET", "CRO", "URB", "CNV", "SNO", "BSV", "WAT",
}
IGBP_NAME_TO_CODE = {
    "evergreen needleleaf": "ENF",
    "evergreen broadleaf": "EBF",
    "deciduous needleleaf": "DNF",
    "deciduous broadleaf": "DBF",
    "mixed forest": "MF",
    "closed shrub": "CSH",
    "open shrub": "OSH",
    "woody savanna": "WSA",
    "savanna": "SAV",
    "grassland": "GRA",
    "grasslands": "GRA",
    "wetland": "WET",
    "wetlands": "WET",
    "cropland": "CRO",
    "croplands": "CRO",
    "urban": "URB",
    "snow": "SNO",
    "barren": "BSV",
    "water": "WAT",
}

REQUIRED_RAW_FIELDS = ("latitude", "longitude", "elevation", "MAT", "climate_code", "igbp_type")

FIELD_PATTERNS = {
    "latitude": (r"\bsite\s+latitude\b", r"\blatitude\b", r"\blat\b"),
    "longitude": (r"\bsite\s+longitude\b", r"\blongitude\b", r"\blong\b", r"\blon\b"),
    "elevation": (r"\belevation\b", r"\baltitude\b", r"\belev\b"),
    "MAT": (
        r"\bmean\s+annual\s+(?:air\s+)?temperature\b",
        r"\bmean\s+annual\s+temp\b",
        r"\bmean\s+air\s+temperature\b",
        r"\bMAT\b",
    ),
    "climate_code": (
        r"\bkoppen[-\s]*(?:geiger)?(?:\s+climate)?(?:\s+class(?:ification)?)?\b",
        r"\bkoeppen[-\s]*(?:geiger)?(?:\s+climate)?(?:\s+class(?:ification)?)?\b",
        r"\bclimate\s+(?:code|class|classification|zone)\b",
    ),
    "igbp_type": (
        r"\bIGBP\b",
        r"\bvegetation\s+(?:type|class|classification)\b",
        r"\bland\s+cover\s+(?:type|class|classification)\b",
    ),
}


# Approximate (lat_min, lat_max, lon_min, lon_max) envelopes of countries that host
# AmeriFlux sites; the site ID prefix is the ISO 3166 country code.
COUNTRY_BOUNDS = {
    "US": (18.5, 71.6, -179.9, -66.5),
    "CA": (41.6, 83.2, -141.1, -52.5),
    "MX": (14.5, 32.8, -118.5, -86.7),
    "PR": (17.8, 18.6, -67.3, -65.2),
    "CR": (8.0, 11.3, -86.0, -82.5),
    "PA": (7.1, 9.7, -83.1, -77.1),
    "BR": (-33.8, 5.3, -74.0, -28.8),
    "AR": (-55.1, -21.7, -73.6, -53.6),
    "CL": (-56.0, -17.5, -75.8, -66.4),
    "PE": (-18.4, -0.0, -81.4, -68.6),
    "BO": (-22.9, -9.6, -69.7, -57.4),
    "CO": (-4.3, 13.4, -79.1, -66.8),
    "EC": (-5.1, 1.5, -92.1, -75.1),
    "GF": (2.1, 5.8, -54.6, -51.6),
    "UY": (-35.0, -30.0, -58.5, -53.0),
    "PY": (-27.6, -19.2, -62.7, -54.2),
}
COUNTRY_BOUNDS_MARGIN_DEG = 0.5

# Region words in the site Description and the latitude range they imply.
DESCRIPTION_LATITUDE_HINTS = (
    (r"\bsouthern hemisphere\b", -90.0, 0.0),
    (r"\bnorthern hemisphere\b", 0.0, 90.0),
    (r"\bsouth america\b", -56.0, 13.0),
    (r"\bcentral america\b", 7.0, 19.0),
    (r"\bcaribbean\b", 10.0, 27.0),
    (r"\bamazon(?:ia|ian)?\b", -20.0, 6.0),
    (r"\bpantanal\b", -22.0, -15.0),
    (r"\bpatagonia\b", -56.0, -37.0),
    (r"\balaska\b", 51.0, 72.0),
    (r"\barctic\b", 60.0, 90.0),
)

# Plausible mean annual temperature (degC) for each Koppen main group.
KOPPEN_MAT_RANGES = {"A": (15.0, 35.0), "C": (-3.0, 30.0), "D": (-25.0, 16.0), "E": (-60.0, 8.0)}
TROPICAL_MAX_ABS_LATITUDE = 35.0


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag in {"br", "p", "div", "li", "tr", "td", "th", "section", "article", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "li", "tr", "td", "th", "section", "article", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(value: str) -> str:
    parser = HtmlTextExtractor()
    parser.feed(value)
    return parser.text


def normalize_text(value: str) -> str:
    value = unescape(value)
    value = value.replace("\xa0", " ")
    value = value.replace("K\u00f6ppen", "Koppen")
    value = value.replace("k\u00f6ppen", "koppen")
    value = value.replace("Koeppen", "Koppen")
    value = value.replace("koeppen", "koppen")
    return value


def split_lines(text: str) -> List[str]:
    text = normalize_text(text)
    rough_lines = re.split(r"[\r\n]+", text)
    lines = []
    for line in rough_lines:
        cleaned = clean_line(line)
        if cleaned:
            lines.append(cleaned)
    return lines


def clean_line(line: str) -> str:
    """Collapse whitespace and drop label punctuation without touching numeric signs.

    Values such as "-16.4980, -56.4120" or "-12.6" sit on their own line on
    AmeriFlux pages, so a leading "-" is only removed when it is a bullet
    (dash followed by whitespace), never when it is the sign of a number.
    """
    cleaned = re.sub(r"\s+", " ", line).strip(" \t:|")
    cleaned = re.sub(r"^-+\s+", "", cleaned)
    return cleaned.rstrip(" -:|")


def candidate_segments(lines: Sequence[str], patterns: Sequence[str], lookahead: int = 2) -> Iterable[str]:
    for index, line in enumerate(lines):
        for pattern in patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if not match:
                continue
            yield line[match.end():]
            yield line
            for offset in range(1, lookahead + 1):
                if index + offset < len(lines):
                    yield lines[index + offset]


def bounded_float(text: str, lower: float, upper: float) -> Optional[float]:
    for match in re.finditer(r"[-+]?\d+(?:\.\d+)?", text):
        value = float(match.group(0))
        if lower <= value <= upper:
            return value
    return None


def bounded_floats(text: str, lower: float, upper: float) -> List[float]:
    values = []
    for match in re.finditer(r"[-+]?\d+(?:\.\d+)?", text):
        value = float(match.group(0))
        if lower <= value <= upper:
            values.append(value)
    return values


def extract_float(lines: Sequence[str], field: str, lower: float, upper: float) -> Optional[float]:
    for segment in candidate_segments(lines, FIELD_PATTERNS[field]):
        value = bounded_float(segment, lower, upper)
        if value is not None:
            return value
    return None


def extract_lat_long_pair(lines: Sequence[str]) -> Optional[Tuple[float, float]]:
    pair_pattern = r"\blat(?:itude)?\s*,\s*lon(?:g|gitude)?\b"
    for index, line in enumerate(lines):
        if not re.search(pair_pattern, line, flags=re.IGNORECASE):
            continue
        for offset in range(0, 4):
            if index + offset >= len(lines):
                break
            values = bounded_floats(lines[index + offset], -180.0, 180.0)
            if len(values) >= 2 and -90.0 <= values[0] <= 90.0:
                return values[0], values[1]
    return None


def normalize_koppen_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    code = str(value).strip()
    return KOPPEN_BY_UPPER.get(code.upper())


def extract_koppen_code(lines: Sequence[str]) -> Optional[str]:
    code_pattern = "|".join(re.escape(code) for code in sorted(KOPPEN_MAP, key=len, reverse=True))
    for segment in candidate_segments(lines, FIELD_PATTERNS["climate_code"], lookahead=3):
        match = re.search(rf"\b({code_pattern})\b", segment, flags=re.IGNORECASE)
        if match:
            return normalize_koppen_code(match.group(1))

    full_text = " ".join(lines)
    matches = {
        normalize_koppen_code(match.group(1))
        for match in re.finditer(rf"\b({code_pattern})\b", full_text, flags=re.IGNORECASE)
    }
    matches.discard(None)
    if len(matches) == 1:
        return next(iter(matches))
    return None


def normalize_igbp_code(value: Any) -> Optional[str]:
    if value is None:
        return None
    candidate = str(value).strip().upper()
    if candidate in IGBP_CODES:
        return candidate
    lowered = str(value).strip().lower()
    for name, code in IGBP_NAME_TO_CODE.items():
        if name in lowered:
            return code
    return None


def extract_igbp_type(lines: Sequence[str]) -> Optional[str]:
    code_pattern = "|".join(sorted(IGBP_CODES, key=len, reverse=True))
    for segment in candidate_segments(lines, FIELD_PATTERNS["igbp_type"], lookahead=3):
        match = re.search(rf"\b({code_pattern})\b", segment, flags=re.IGNORECASE)
        if match:
            return normalize_igbp_code(match.group(1))
        code = normalize_igbp_code(segment)
        if code:
            return code
    return None


def extract_site_name(lines: Sequence[str], site_id: str) -> str:
    title_pattern = rf"^{re.escape(site_id)}\s*:\s*(.+)$"
    for line in lines:
        match = re.search(title_pattern, line)
        if match:
            return match.group(1).strip()

    for segment in candidate_segments(lines, (r"\bsite\s+name\b",), lookahead=2):
        value = segment.strip(" :|-")
        if value and site_id not in value:
            return value
    return site_id


def parse_site_metadata_from_text(text: str, site_id: str, source: str) -> Optional[Dict[str, Any]]:
    lines = split_lines(text)
    if not lines:
        return None

    lat_long = extract_lat_long_pair(lines)
    description = extract_description(lines)
    raw = {
        "site_name": extract_site_name(lines, site_id),
        "latitude": lat_long[0] if lat_long else extract_float(lines, "latitude", -90.0, 90.0),
        "longitude": lat_long[1] if lat_long else extract_float(lines, "longitude", -180.0, 180.0),
        "elevation": extract_float(lines, "elevation", -500.0, 9000.0),
        "MAT": extract_float(lines, "MAT", -90.0, 70.0),
        "climate_code": extract_koppen_code(lines),
        "igbp_type": extract_igbp_type(lines),
        "description": description,
        "source": source,
    }
    if all(raw.get(field) is not None for field in REQUIRED_RAW_FIELDS):
        raw["sign_checks"] = check_signs_against_page(text, raw)
        return raw
    return None


def extract_description(lines: Sequence[str]) -> Optional[str]:
    for index, line in enumerate(lines):
        if re.fullmatch(r"(?:site\s+)?description", line, flags=re.IGNORECASE):
            for candidate in lines[index + 1:index + 3]:
                if len(candidate) > 20:
                    return candidate[:600]
    return None


SIGNED_NUMBER = r"[-+\u2212]?\d+(?:\.\d+)?"


def signed_numbers_after_label(text: str, label_pattern: str, lookahead: int = 2) -> List[float]:
    """Numbers, with their signs, on the label line and the next lines of the raw page text."""
    raw_lines = [line.strip() for line in re.split(r"[\r\n]+", normalize_text(text)) if line.strip()]
    for index, line in enumerate(raw_lines):
        if re.search(label_pattern, line, flags=re.IGNORECASE):
            window = " ".join(raw_lines[index:index + 1 + lookahead])
            window = re.sub(label_pattern, " ", window, count=1, flags=re.IGNORECASE)
            return [float(value.replace("\u2212", "-")) for value in re.findall(SIGNED_NUMBER, window)]
    return []


def check_signs_against_page(text: str, raw: Dict[str, Any]) -> Dict[str, str]:
    """Confirm each parsed number appears with the same sign in the raw page text."""
    labels = {
        "latitude": r"\blat(?:itude)?\b",
        "longitude": r"\blon(?:g|gitude)?\b",
        "elevation": r"\belevation\b",
        "MAT": r"\bmean\s+annual\s+temp(?:erature)?\b",
    }
    pair = signed_numbers_after_label(text, r"\blat(?:itude)?\s*,\s*lon(?:g|gitude)?\b")
    results = {}
    for field, label in labels.items():
        value = float(raw[field])
        numbers = pair if field in ("latitude", "longitude") and pair else signed_numbers_after_label(text, label)
        if any(abs(number - value) < 1.0e-9 for number in numbers):
            results[field] = "matched"
        elif value != 0.0 and any(abs(number + value) < 1.0e-9 for number in numbers):
            results[field] = "sign_mismatch"
        else:
            results[field] = "not_found"
    return results


def map_vegetation(igbp: str) -> int:
    igbp = str(igbp).upper()
    if "ENF" in igbp:
        return 11
    if "DBF" in igbp:
        return 10
    return 10


def raw_to_ecosim(site_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    climate_code = normalize_koppen_code(raw.get("climate_code"))
    igbp_type = normalize_igbp_code(raw.get("igbp_type"))
    if climate_code is None:
        raise ValueError(f"Unknown Koppen climate code: {raw.get('climate_code')}")
    if igbp_type is None:
        raise ValueError(f"Unknown IGBP vegetation type: {raw.get('igbp_type')}")

    normalized_raw = dict(raw)
    normalized_raw["climate_code"] = climate_code
    normalized_raw["igbp_type"] = igbp_type
    return {
        "site_name": site_id,
        "ALATG": float(raw["latitude"]),
        "ALONG": float(raw["longitude"]),
        "ALTIG": float(raw["elevation"]),
        "ATCAG": float(raw["MAT"]),
        "IETYPG": KOPPEN_MAP[climate_code],
        "IXTYP1": map_vegetation(igbp_type),
        "_raw": normalized_raw,
    }


def validate_site_location(site_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-check coordinates and MAT against the site ID, page text, and Koppen class.

    Errors (sign lost while parsing, coordinates outside the site-ID country)
    mean the metadata must not be used; warnings flag implausible combinations
    for review.
    """
    raw = data.get("_raw", {})
    lat, lon, mat = float(data["ALATG"]), float(data["ALONG"]), float(data["ATCAG"])
    errors: List[str] = []
    warnings: List[str] = []
    checks: Dict[str, str] = {}

    sign_checks = raw.get("sign_checks") or {}
    mismatched = [field for field, status in sign_checks.items() if status == "sign_mismatch"]
    checks["page_signs"] = "failed" if mismatched else ("passed" if sign_checks else "not_available")
    if mismatched:
        errors.append(f"Parsed {', '.join(mismatched)} lost the sign shown on the AmeriFlux page.")

    country = site_id[:2].upper()
    bounds = COUNTRY_BOUNDS.get(country)
    if bounds is None:
        checks["country_bounds"] = f"skipped (no envelope for {country})"
    else:
        def inside(la: float, lo: float) -> bool:
            m = COUNTRY_BOUNDS_MARGIN_DEG
            return bounds[0] - m <= la <= bounds[1] + m and bounds[2] - m <= lo <= bounds[3] + m

        if inside(lat, lon):
            checks["country_bounds"] = "passed"
        else:
            checks["country_bounds"] = "failed"
            hint = ""
            if inside(-lat, lon):
                hint = f" Latitude {-lat} (opposite sign) would fit; suspect a lost minus sign."
            elif inside(lat, -lon):
                hint = f" Longitude {-lon} (opposite sign) would fit; suspect a lost minus sign."
            errors.append(f"({lat}, {lon}) is outside the {country} envelope implied by {site_id}.{hint}")

    description = (raw.get("description") or "") + " " + (raw.get("site_name") or "")
    hint_results = []
    for pattern, lower, upper in DESCRIPTION_LATITUDE_HINTS:
        match = re.search(pattern, description, flags=re.IGNORECASE)
        if match:
            ok = lower <= lat <= upper
            hint_results.append(ok)
            if not ok:
                warnings.append(
                    f"Description mentions '{match.group(0)}' (latitude {lower} to {upper}) but latitude is {lat}."
                )
    checks["description_region"] = (
        "not_available" if not hint_results else ("passed" if all(hint_results) else "failed")
    )

    koppen = normalize_koppen_code(raw.get("climate_code")) or next(
        (code for code, number in KOPPEN_MAP.items() if number == data.get("IETYPG")), None
    )
    group = koppen[0] if koppen else None
    if group in KOPPEN_MAT_RANGES:
        lower, upper = KOPPEN_MAT_RANGES[group]
        koppen_ok = lower <= mat <= upper
        if not koppen_ok:
            warnings.append(f"MAT {mat} degC is implausible for Koppen {koppen} ({lower} to {upper} degC).")
        if group == "A" and abs(lat) > TROPICAL_MAX_ABS_LATITUDE:
            koppen_ok = False
            warnings.append(f"Tropical Koppen {koppen} at latitude {lat}.")
        checks["koppen_consistency"] = "passed" if koppen_ok else "failed"
    else:
        checks["koppen_consistency"] = "not_applicable"

    status = "failed" if errors else ("warnings" if warnings else "passed")
    return {"status": status, "checks": checks, "errors": errors, "warnings": warnings}


def output_directory(site_id: str, output_dir: Optional[str]) -> str:
    if output_dir:
        return output_dir
    return os.path.join("result", site_id)


def candidate_cache_files(site_id: str, output_dir: Optional[str]) -> List[str]:
    outdir = output_directory(site_id, output_dir)
    return [
        os.path.join(outdir, f"{site_id}_ecosim_site.json"),
        os.path.join("result", site_id, f"{site_id}_ecosim_site.json"),
        os.path.join("result", f"{site_id}_ecosim_site.json"),
    ]


def load_cached_metadata(site_id: str, output_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    for path in dict.fromkeys(candidate_cache_files(site_id, output_dir)):
        if not os.path.exists(path):
            continue
        with open(path, "r") as handle:
            data = json.load(handle)
        if all(key in data for key in ("ALATG", "ALONG", "ALTIG", "ATCAG", "IETYPG", "IXTYP1")):
            data.setdefault("site_name", site_id)
            return data
    return None


def urllib_page_text(site_id: str) -> Iterable[Tuple[str, str]]:
    url = AMERIFLUX_SITEINFO_URL.format(site_id=site_id)
    request = Request(
        url,
        headers={"User-Agent": "EcoSIM-python-tools/1.0 (+https://github.com/bioepic-data/ecosim-agent)"},
    )
    with urlopen(request, timeout=60) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        text = response.read().decode(charset, errors="replace")
    yield f"AmeriFlux siteinfo/{site_id} text", html_to_text(text)
    yield f"AmeriFlux siteinfo/{site_id} html", text


def requests_page_text(site_id: str) -> Iterable[Tuple[str, str]]:
    if requests is None:
        return
    url = AMERIFLUX_SITEINFO_URL.format(site_id=site_id)
    response = requests.get(
        url,
        headers={"User-Agent": "EcoSIM-python-tools/1.0 (+https://github.com/bioepic-data/ecosim-agent)"},
        timeout=60,
    )
    response.raise_for_status()
    yield f"AmeriFlux siteinfo/{site_id} text", html_to_text(response.text)
    yield f"AmeriFlux siteinfo/{site_id} html", response.text


def playwright_page_text(site_id: str) -> Iterable[Tuple[str, str]]:
    if sync_playwright is None:
        return
    url = AMERIFLUX_SITEINFO_URL.format(site_id=site_id)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 1600})
            page.goto(url, wait_until="networkidle", timeout=90000)
            yield f"AmeriFlux siteinfo/{site_id} rendered_text", page.locator("body").inner_text(timeout=30000)
            yield f"AmeriFlux siteinfo/{site_id} rendered_html", page.content()
        finally:
            browser.close()


def fetch_and_parse(site_id: str) -> Dict[str, Any]:
    errors: List[str] = []
    sources_seen = 0
    for provider in (urllib_page_text, requests_page_text, playwright_page_text):
        try:
            for source, text in provider(site_id) or ():
                sources_seen += 1
                raw = parse_site_metadata_from_text(text, site_id, source)
                if raw:
                    return raw
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")

    if sources_seen:
        detail = f"fetched {sources_seen} page text sources but required fields were incomplete"
        if errors:
            detail = f"{detail}; " + "; ".join(errors)
    else:
        detail = "; ".join(errors) if errors else "no page text providers were available"
    raise RuntimeError(f"Could not derive complete AmeriFlux metadata for {site_id}: {detail}")


def extract_site_info(
    site_id: str,
    output_dir: Optional[str] = None,
    force_refresh: bool = False,
    check_location: bool = True,
) -> Dict[str, Any]:
    if not force_refresh:
        cached = load_cached_metadata(site_id, output_dir)
        if cached:
            validation = validate_site_location(site_id, cached)
            if not check_location or validation["status"] != "failed":
                cached["_location_validation"] = validation
                for message in validation["warnings"]:
                    print(f"WARNING: {site_id}: {message}", file=sys.stderr)
                return cached
            print(
                f"Cached metadata for {site_id} failed location checks "
                f"({'; '.join(validation['errors'])}); refetching.",
                file=sys.stderr,
            )

    raw = fetch_and_parse(site_id)
    final_json = raw_to_ecosim(site_id, raw)
    validation = validate_site_location(site_id, final_json)
    final_json["_location_validation"] = validation
    for message in validation["warnings"]:
        print(f"WARNING: {site_id}: {message}", file=sys.stderr)
    if check_location and validation["status"] == "failed":
        raise ValueError(
            f"Location checks failed for {site_id}: {'; '.join(validation['errors'])} "
            "Use --skip-location-check only after confirming the coordinates."
        )

    outdir = output_directory(site_id, output_dir)
    os.makedirs(outdir, exist_ok=True)
    output_file = os.path.join(outdir, f"{site_id}_ecosim_site.json")
    with open(output_file, "w") as handle:
        json.dump(final_json, handle, indent=4)
    return final_json


def run_site_metadata_flow(
    site_id: str,
    output_dir: Optional[str] = None,
    force_refresh: bool = False,
    check_location: bool = True,
) -> Dict[str, Any]:
    final_json = extract_site_info(
        site_id, output_dir=output_dir, force_refresh=force_refresh, check_location=check_location
    )
    outdir = output_directory(site_id, output_dir)
    output_file = os.path.join(outdir, f"{site_id}_ecosim_site.json")
    print(f"Successfully resolved EcoSIM site metadata for {site_id}")
    print(f"Output file: {output_file}")
    print(json.dumps(final_json, indent=4))
    return final_json


def run_vision_rag_flow(site_id: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
    """Backward-compatible wrapper for callers that used the old function name."""
    return run_site_metadata_flow(site_id, output_dir=output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract AmeriFlux site metadata for EcoSIM inputs.")
    parser.add_argument("site_id", help="AmeriFlux site identifier, for example US-Ha1.")
    parser.add_argument("output_dir", nargs="?", help="Optional output directory. Defaults to result/<SITE_ID>.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cached metadata and refetch AmeriFlux.")
    parser.add_argument(
        "--skip-location-check",
        action="store_true",
        help="Write metadata even if coordinates fail the page-sign or country-envelope checks.",
    )
    args = parser.parse_args()

    try:
        run_site_metadata_flow(
            args.site_id,
            output_dir=args.output_dir,
            force_refresh=args.force_refresh,
            check_location=not args.skip_location_check,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
