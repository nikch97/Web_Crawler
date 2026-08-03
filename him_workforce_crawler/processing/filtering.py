"""Rule-based inclusion/exclusion filtering engine.

Business rules are supplied at runtime from Inclusion/Exclusion spreadsheets.
The engine evaluates registered rule handlers rather than hardcoded if-chains
tied to specific keywords or locations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from models.job_record import FilterDecision, JobRecord, RawJobPosting
from utils.logger import get_logger

logger = get_logger(__name__)

STATE_ABBREVIATIONS = {
    "louisiana": "la",
    "arkansas": "ar",
    "mississippi": "ms",
    "texas": "tx",
}


@dataclass
class FilterResult:
    """Outcome of applying the full filter pipeline to one job."""

    job: JobRecord | None
    raw: RawJobPosting
    decision: FilterDecision
    audit: dict[str, Any] = field(default_factory=dict)


RuleHandler = Callable[[RawJobPosting, dict[str, Any]], tuple[bool, list[str]]]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _contains_keyword(text: str, keyword: str) -> bool:
    """Match keywords with word boundaries for short tokens, substring for phrases."""
    needle = keyword.strip()
    if not needle:
        return False
    haystack = text or ""
    if len(needle) <= 3:
        pattern = rf"(?<![A-Za-z]){re.escape(needle)}(?![A-Za-z])"
        return re.search(pattern, haystack, flags=re.IGNORECASE) is not None
    return needle.lower() in haystack.lower()


def _location_matches(job_location: str, allowed_locations: Iterable[str]) -> tuple[bool, str]:
    loc = _normalize_text(job_location)
    if not loc:
        return False, "missing location"

    for allowed in allowed_locations:
        allowed_norm = _normalize_text(allowed)
        if not allowed_norm:
            continue
        if allowed_norm == "remote" and "remote" in loc:
            return True, f"matched location '{allowed}'"
        if allowed_norm in loc:
            return True, f"matched location '{allowed}'"
        abbr = STATE_ABBREVIATIONS.get(allowed_norm)
        if abbr and re.search(rf"(?<![A-Za-z]){abbr}(?![A-Za-z])", loc):
            return True, f"matched location abbreviation for '{allowed}'"
    return False, "location not in inclusion list"


def _extract_years_required(text: str) -> list[int]:
    patterns = [
        r"(\d+)\s*\+?\s*(?:years?|yrs?)",
        r"minimum\s+of\s+(\d+)\s+years?",
        r"at\s+least\s+(\d+)\s+years?",
    ]
    years: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            years.append(int(match.group(1)))
    return years


def _extract_salary_annual(text: str) -> list[int]:
    """Best-effort annual salary extraction from free text."""
    values: list[int] = []
    money = re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", text or "")
    for raw in money:
        amount = float(raw.replace(",", ""))
        lowered = (text or "").lower()
        if "hour" in lowered or "/hr" in lowered or "an hour" in lowered:
            # Convert hourly-looking amounts to annual approximation.
            if amount < 500:
                amount = amount * 2080
        values.append(int(amount))
    return values


def rule_location_inclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    locations = rules.get("locations") or []
    if not locations:
        return True, ["no location inclusion rules configured"]
    ok, reason = _location_matches(job.location, locations)
    return ok, [reason]


def rule_keyword_inclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    keywords = list(rules.get("keywords") or [])
    title_keywords = list(rules.get("title_keywords") or [])
    description_keywords = list(rules.get("description_keywords") or [])

    if not keywords and not title_keywords and not description_keywords:
        return True, ["no keyword inclusion rules configured"]

    reasons: list[str] = []
    title = job.job_title or ""
    description = job.description or ""
    blob = f"{title} {description}"

    for keyword in title_keywords:
        if _contains_keyword(title, keyword):
            reasons.append(f"title inclusion keyword '{keyword}'")
    for keyword in description_keywords:
        if _contains_keyword(description, keyword):
            reasons.append(f"description inclusion keyword '{keyword}'")
    for keyword in keywords:
        if _contains_keyword(blob, keyword):
            reasons.append(f"inclusion keyword '{keyword}'")

    if reasons:
        return True, reasons
    return False, ["no inclusion keywords matched title/description"]


def rule_title_exclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    keywords = rules.get("title_keywords") or []
    hits = [kw for kw in keywords if _contains_keyword(job.job_title, kw)]
    if hits:
        return False, [f"excluded title keyword '{hit}'" for hit in hits]
    return True, []


def rule_description_exclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    keywords = rules.get("description_keywords") or []
    hits = [kw for kw in keywords if _contains_keyword(job.description, kw)]
    if hits:
        return False, [f"excluded description keyword '{hit}'" for hit in hits]
    return True, []


def rule_career_level_exclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    keywords = rules.get("career_level_keywords") or []
    blob = f"{job.job_title} {job.description}"
    hits = [kw for kw in keywords if _contains_keyword(blob, kw)]
    if hits:
        return False, [f"excluded career-level keyword '{hit}'" for hit in hits]
    return True, []


def rule_experience_exclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    experience_rules = rules.get("experience_rules") or {}
    maximum_years = experience_rules.get("maximum_years")
    if maximum_years is None:
        return True, []

    years = _extract_years_required(f"{job.job_title} {job.description}")
    if not years:
        return True, []
    required = max(years)
    if required > int(maximum_years):
        return False, [
            f"experience requirement {required} years exceeds maximum {maximum_years}"
        ]
    return True, []


def rule_salary_exclusion(job: RawJobPosting, rules: dict[str, Any]) -> tuple[bool, list[str]]:
    salary_rules = rules.get("salary_rules") or {}
    maximum_annual = salary_rules.get("maximum_annual")
    if maximum_annual is None:
        return True, []

    salaries = _extract_salary_annual(f"{job.salary} {job.description}")
    if not salaries:
        return True, []
    # Use the lower bound of listed salaries for exclusion decisions.
    low = min(salaries)
    if low > int(maximum_annual):
        return False, [
            f"salary/pay {low} exceeds maximum annual {maximum_annual}"
        ]
    return True, []


INCLUSION_HANDLERS: list[tuple[str, RuleHandler]] = [
    ("location", rule_location_inclusion),
    ("relevance", rule_keyword_inclusion),
]

EXCLUSION_HANDLERS: list[tuple[str, RuleHandler]] = [
    ("title_keywords", rule_title_exclusion),
    ("description_keywords", rule_description_exclusion),
    ("career_level", rule_career_level_exclusion),
    ("experience", rule_experience_exclusion),
    ("salary", rule_salary_exclusion),
]


class JobFilterEngine:
    """Configurable rule engine for HIM inclusion/exclusion criteria."""

    def __init__(
        self,
        inclusion_rules: dict[str, Any],
        exclusion_rules: dict[str, Any],
        *,
        inclusion_handlers: list[tuple[str, RuleHandler]] | None = None,
        exclusion_handlers: list[tuple[str, RuleHandler]] | None = None,
    ) -> None:
        self.inclusion_rules = inclusion_rules
        self.exclusion_rules = exclusion_rules
        self.inclusion_handlers = inclusion_handlers or INCLUSION_HANDLERS
        self.exclusion_handlers = exclusion_handlers or EXCLUSION_HANDLERS

    def evaluate(self, raw: RawJobPosting) -> FilterDecision:
        """Evaluate all inclusion then exclusion rules for one raw job."""
        reasons: list[str] = []

        for stage, handler in self.inclusion_handlers:
            ok, stage_reasons = handler(raw, self.inclusion_rules)
            reasons.extend(stage_reasons)
            if not ok:
                return FilterDecision(included=False, stage=f"inclusion:{stage}", reasons=reasons)

        for stage, handler in self.exclusion_handlers:
            ok, stage_reasons = handler(raw, self.exclusion_rules)
            reasons.extend(stage_reasons)
            if not ok:
                return FilterDecision(included=False, stage=f"exclusion:{stage}", reasons=reasons)

        if not reasons:
            reasons.append("passed all inclusion and exclusion rules")
        else:
            reasons.append("passed all exclusion rules")
        return FilterDecision(included=True, stage="accepted", reasons=reasons)

    def filter_jobs(self, raw_jobs: list[RawJobPosting]) -> list[FilterResult]:
        """Apply the rule engine to a list of raw jobs."""
        results: list[FilterResult] = []
        for raw in raw_jobs:
            decision = self.evaluate(raw)
            results.append(
                FilterResult(
                    job=None,
                    raw=raw,
                    decision=decision,
                    audit={
                        "source_url": raw.url,
                        "source_platform": raw.source_platform,
                        "job_title": raw.job_title,
                        "employer": raw.company,
                        "location": raw.location,
                        "included": decision.included,
                        "stage": decision.stage,
                        "reasons": "; ".join(decision.reasons),
                    },
                )
            )
        included = sum(1 for item in results if item.decision.included)
        logger.info(
            "Filtering complete: %d/%d jobs included",
            included,
            len(results),
        )
        return results
