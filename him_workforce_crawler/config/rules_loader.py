"""Dynamic inclusion/exclusion rule loaders for the HIM workforce pipeline.

Rules are derived from the input spreadsheet/CSV files. No location lists,
keyword lists, or exclusion thresholds are hardcoded as business rules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config.excel_loader import dataframe_to_raw_rows, load_tabular_file
from utils.logger import get_logger

logger = get_logger(__name__)

# Section headers that mark the start of an inclusion-rule group.
# Values under each header become that group's rule list.
KNOWN_INCLUSION_SECTIONS = {
    "location": "locations",
    "locations": "locations",
    "keyword": "keywords",
    "keywords": "keywords",
    "title keyword": "title_keywords",
    "title keywords": "title_keywords",
    "title": "title_keywords",
    "description keyword": "description_keywords",
    "description keywords": "description_keywords",
}

# Rows that indicate the Inclusion file has transitioned into the data dictionary
# (observed in the provided Inclusion.csv export artifact).
DICTIONARY_MARKERS = {
    "variable name",
    "him compentency-workforce data dictionary",
    "him competency-workforce data dictionary",
}

EXPERIENCE_PATTERN = re.compile(
    r"(?:over|more than|>)\s*(\d+)\s*years?",
    re.IGNORECASE,
)
SALARY_PATTERN = re.compile(
    r"salary\s+over\s+\$?\s*([\d,]+)",
    re.IGNORECASE,
)
CAREER_LEVEL_HINTS = {
    "senior",
    "junior",
    "entry",
    "mid",
    "advanced",
    "expert",
    "vp",
    "director",
    "chief",
    "intern",
    "internship",
}


def _first_cell(row: list[str]) -> str:
    return row[0].strip() if row else ""


def _is_empty_row(row: list[str]) -> bool:
    return all(not cell.strip() for cell in row)


def _normalize_section(label: str) -> str | None:
    key = label.strip().lower()
    return KNOWN_INCLUSION_SECTIONS.get(key)


def load_inclusion_rules(path: Path | str) -> dict[str, Any]:
    """Load inclusion rules from Inclusion.csv / Inclusion.xlsx.

    Expected format is section-based::

        Location
        Louisiana
        Arkansas

        Keywords
        health information management

    The provided Inclusion file currently contains a Location section followed
    by an embedded copy of the data dictionary. Dictionary content is ignored.

    Returns:
        INCLUSION_RULES dictionary, e.g. ``{"locations": [...], "keywords": [...]}``.
    """
    df = load_tabular_file(path, header=None)
    rows = dataframe_to_raw_rows(df)

    inclusion_rules: dict[str, Any] = {
        "locations": [],
        "keywords": [],
        "title_keywords": [],
        "description_keywords": [],
    }
    current_section: str | None = None

    for row in rows:
        first = _first_cell(row)
        if not first:
            current_section = None
            continue

        lowered = first.lower()
        if lowered in DICTIONARY_MARKERS or lowered.startswith("created from uploaded"):
            logger.debug("Stopping inclusion parse at dictionary marker: %s", first)
            break

        section = _normalize_section(first)
        if section is not None:
            current_section = section
            inclusion_rules.setdefault(current_section, [])
            continue

        if current_section is None:
            # Unlabeled leading values are treated as a future free-form rule bucket.
            inclusion_rules.setdefault("other_rules", []).append(first)
            continue

        inclusion_rules[current_section].append(first)

    # Drop empty optional lists for a cleaner runtime object, but keep locations.
    cleaned: dict[str, Any] = {}
    for key, value in inclusion_rules.items():
        if key == "locations" or value:
            cleaned[key] = value

    logger.info(
        "Loaded INCLUSION_RULES from %s | locations=%d keywords=%d title_keywords=%d",
        path,
        len(cleaned.get("locations", [])),
        len(cleaned.get("keywords", [])),
        len(cleaned.get("title_keywords", [])),
    )
    return cleaned


def _classify_exclusion_item(item: str) -> tuple[str, Any]:
    """Classify a free-text exclusion item into a typed rule.

    Classification is pattern-driven so new spreadsheet rows can introduce
    experience, salary, career-level, or keyword exclusions without code changes
    to the filtering engine's control flow.
    """
    text = item.strip()
    if not text:
        return ("skip", None)

    experience_match = EXPERIENCE_PATTERN.search(text)
    if experience_match:
        years = int(experience_match.group(1))
        return ("experience_rules", {"maximum_years": years, "source_text": text})

    salary_match = SALARY_PATTERN.search(text)
    if salary_match:
        amount = int(salary_match.group(1).replace(",", ""))
        return ("salary_rules", {"maximum_annual": amount, "source_text": text})

    token = text.lower()
    if token in CAREER_LEVEL_HINTS or "career level" in token:
        return ("career_level_keywords", text)

    # Default: treat short tokens as title keywords; longer phrases as description keywords.
    word_count = len(text.split())
    if word_count <= 2:
        return ("title_keywords", text)
    return ("description_keywords", text)


def load_exclusion_rules(path: Path | str) -> dict[str, Any]:
    """Load exclusion rules from Exclusion.csv / Exclusion.xlsx.

    Each non-empty first-column value is classified into a typed rule bucket::

        {
            "title_keywords": ["RN", "Nurse"],
            "description_keywords": [...],
            "experience_rules": {"maximum_years": 7},
            "salary_rules": {"maximum_annual": 150000},
            "career_level_keywords": ["Senior", "VP"],
            "raw_rules": [...]
        }

    Returns:
        EXCLUSION_RULES dictionary.
    """
    df = load_tabular_file(path, header=None)
    rows = dataframe_to_raw_rows(df)

    exclusion_rules: dict[str, Any] = {
        "title_keywords": [],
        "description_keywords": [],
        "career_level_keywords": [],
        "experience_rules": {},
        "salary_rules": {},
        "raw_rules": [],
    }

    for row in rows:
        if _is_empty_row(row):
            continue
        item = _first_cell(row)
        if not item:
            continue

        exclusion_rules["raw_rules"].append(item)
        rule_type, payload = _classify_exclusion_item(item)
        if rule_type == "skip" or payload is None:
            continue

        if rule_type in {"experience_rules", "salary_rules"}:
            # Later rows of the same type overwrite earlier ones intentionally
            # so the spreadsheet remains the single source of truth.
            exclusion_rules[rule_type].update(payload)
        else:
            bucket: list[str] = exclusion_rules[rule_type]
            if payload not in bucket:
                bucket.append(payload)

    logger.info(
        "Loaded EXCLUSION_RULES from %s | title=%d description=%d career=%d "
        "experience=%s salary=%s",
        path,
        len(exclusion_rules["title_keywords"]),
        len(exclusion_rules["description_keywords"]),
        len(exclusion_rules["career_level_keywords"]),
        exclusion_rules["experience_rules"],
        exclusion_rules["salary_rules"],
    )
    return exclusion_rules
