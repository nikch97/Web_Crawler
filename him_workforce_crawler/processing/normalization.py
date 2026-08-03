"""Normalize raw crawler output into HIM Data Dictionary JobRecords."""

from __future__ import annotations

import re
from typing import Any

from models.job_record import FilterDecision, JobRecord, RawJobPosting, today_iso
from utils.logger import get_logger

logger = get_logger(__name__)

WORK_SETTING_PATTERNS = (
    (re.compile(r"\bhybrid\b", re.I), "Hybrid"),
    (re.compile(r"\bremote\b", re.I), "Remote"),
    (re.compile(r"\b(on[\s-]?site|in[\s-]?person)\b", re.I), "On Site"),
)

EMPLOYMENT_TYPE_PATTERNS = (
    (re.compile(r"\bfull[\s-]?time\b", re.I), "Full Time"),
    (re.compile(r"\bpart[\s-]?time\b", re.I), "Part Time"),
    (re.compile(r"\bcontract\b", re.I), "Contract"),
    (re.compile(r"\btemporary\b", re.I), "Temporary"),
    (re.compile(r"\bintern(ship)?\b", re.I), "Internship"),
)

CREDENTIAL_PATTERN = re.compile(
    r"\b(RHIA|RHIT|CCS|CPC|CCA|CHDA|CDIP|CISSP|CPHIMS|CAHIMS|BLS|CPR)\b",
    re.IGNORECASE,
)

EDUCATION_PATTERN = re.compile(
    r"(high school diploma/?GED|associate(?:'s)? degree|bachelor(?:'s)? degree|"
    r"master(?:'s)?(?: degree)?(?: preferred)?|doctoral degree|Ph\.?D\.?)",
    re.IGNORECASE,
)

TECHNICAL_SKILLS = [
    "SQL",
    "Tableau",
    "Power BI",
    "Excel",
    "EHR",
    "Epic",
    "Cerner",
    "ICD-10-CM",
    "ICD-10-PCS",
    "ICD-10",
    "CPT",
    "FHIR",
    "HL7",
    "Python",
    "R ",
    "HIPAA",
]

SOFT_SKILLS = [
    "communication",
    "collaboration",
    "teamwork",
    "leadership",
    "time management",
    "stakeholder management",
    "problem solving",
    "organization",
]

AI_SKILLS = [
    "artificial intelligence",
    "machine learning",
    " NLP",
    "NLP",
    "predictive modeling",
    "IoMT",
    "cloud",
    "FHIR",
    "interoperability",
    "digital health",
    "AI",
    "ML",
]


def infer_work_setting(location: str, description: str) -> str:
    blob = f"{location} {description}"
    for pattern, label in WORK_SETTING_PATTERNS:
        if pattern.search(blob):
            return label
    return "Not Specified"


def infer_employment_type(description: str, salary: str) -> str:
    blob = f"{description} {salary}"
    for pattern, label in EMPLOYMENT_TYPE_PATTERNS:
        if pattern.search(blob):
            return label
    return "Not Specified"


def extract_matches(text: str, pattern: re.Pattern[str]) -> str:
    matches = pattern.findall(text or "")
    if not matches:
        return ""
    # findall may return tuples for grouped patterns
    cleaned = []
    for match in matches:
        value = match if isinstance(match, str) else match[0]
        if value and value not in cleaned:
            cleaned.append(value)
    return "; ".join(cleaned)


def extract_skill_list(text: str, catalog: list[str]) -> str:
    found: list[str] = []
    lowered = text or ""
    for skill in catalog:
        if skill.strip().lower() in lowered.lower():
            label = skill.strip()
            if label not in found:
                found.append(label)
    return "; ".join(found)


def split_required_preferred(description: str) -> tuple[str, str]:
    """Heuristically split required vs preferred qualification blocks."""
    text = description or ""
    preferred_match = re.search(
        r"(preferred qualifications?|preferred|nice to have|plus)[:\-]?\s*(.*)$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    required_match = re.search(
        r"(required qualifications?|requirements?|minimum qualifications?)[:\-]?\s*(.*)$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    preferred = preferred_match.group(2).strip() if preferred_match else ""
    required = required_match.group(2).strip() if required_match else ""

    if preferred_match and required_match:
        # Truncate required block if preferred appears after it in the same span.
        if preferred_match.start() > required_match.start():
            required = text[required_match.end() : preferred_match.start()].strip()
            preferred = preferred_match.group(2).strip()

    if not required and not preferred:
        return text, ""
    return required or text, preferred


def normalize_job(
    raw: RawJobPosting,
    *,
    record_id: str,
    decision: FilterDecision | None = None,
    dataset_source: str = "Indeed",
) -> JobRecord:
    """Convert one RawJobPosting into a dictionary-aligned JobRecord."""
    description = (raw.description or "").strip()
    required, preferred = split_required_preferred(description)
    credentials = extract_matches(description, CREDENTIAL_PATTERN)
    education = extract_matches(description, EDUCATION_PATTERN)

    credential_required = credentials if credentials else "Not Specified"
    # Preferred credentials heuristic: credentials mentioned near 'preferred'
    preferred_creds = ""
    if re.search(r"preferred", description, re.I):
        preferred_section = preferred or description
        preferred_creds = extract_matches(preferred_section, CREDENTIAL_PATTERN)

    notes_parts = []
    if raw.posting_date:
        notes_parts.append(f"Posting date: {raw.posting_date}")
    if raw.search_keyword:
        notes_parts.append(f"Search keyword: {raw.search_keyword}")
    if raw.search_location:
        notes_parts.append(f"Search location: {raw.search_location}")
    if decision is not None:
        notes_parts.append(f"Filter stage: {decision.stage}")
        notes_parts.append(f"Filter reasons: {'; '.join(decision.reasons)}")
    if "demo" in (raw.url or "").lower() or (raw.posting_date or "").lower().startswith("demo"):
        notes_parts.append(
            "PROVENANCE: demo/fallback Indeed record used because live crawl "
            "was empty or blocked."
        )

    experience = ""
    years = re.findall(r"(\d+\s*\+?\s*years?[^\.]*)", description, flags=re.I)
    if years:
        experience = "; ".join(dict.fromkeys(item.strip() for item in years))

    record = JobRecord(
        record_id=record_id,
        dataset_source=dataset_source,
        collection_date=raw.collected_at or today_iso(),
        source_url=raw.url,
        source_platform=raw.source_platform or "Indeed",
        job_title=raw.job_title.strip(),
        employer=raw.company.strip(),
        location=raw.location.strip(),
        work_setting=infer_work_setting(raw.location, description),
        education_requirements=education,
        required_qualifications=required[:4000],
        preferred_qualifications=preferred[:4000],
        credential_required=credential_required,
        credential_preferred=preferred_creds or "Not Specified",
        experience_requirements=experience,
        long_description=description[:8000],
        primary_responsibilities=description[:4000],
        qualifications=required[:4000] if required else description[:4000],
        technical_skills=extract_skill_list(description, TECHNICAL_SKILLS),
        soft_skills=extract_skill_list(description, SOFT_SKILLS),
        ai_or_emerging_technology_skills=extract_skill_list(description, AI_SKILLS),
        salary_or_pay=raw.salary.strip() if raw.salary.strip() else "Not Listed",
        employment_type=infer_employment_type(description, raw.salary),
        notes="; ".join(notes_parts),
        filter_decision=decision,
    )
    return record


def normalize_jobs(
    raw_jobs: list[RawJobPosting],
    decisions: dict[int, FilterDecision] | None = None,
    *,
    id_prefix: str = "IND",
) -> list[JobRecord]:
    """Normalize many raw jobs into JobRecords with sequential IDs."""
    records: list[JobRecord] = []
    for index, raw in enumerate(raw_jobs, start=1):
        decision = None if decisions is None else decisions.get(index - 1)
        record_id = f"{id_prefix}{index:04d}"
        records.append(normalize_job(raw, record_id=record_id, decision=decision))
    logger.info("Normalized %d job records", len(records))
    return records
