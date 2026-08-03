"""Deduplicate job records using title, company, location, and URL."""

from __future__ import annotations

import re
from typing import Iterable

from models.job_record import JobRecord, RawJobPosting
from utils.logger import get_logger

logger = get_logger(__name__)


def _norm(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def make_dedupe_key(
    *,
    title: str,
    company: str,
    location: str,
    url: str,
) -> str:
    """Build a stable deduplication key.

    URL identity wins when present; otherwise title+company+location is used.
    """
    normalized_url = _norm(url)
    if normalized_url:
        # Prefer stable job keys (e.g., Indeed jk=) when available.
        jk_match = re.search(r"[?&]jk=([a-z0-9]+)", normalized_url)
        if jk_match:
            return f"url::jk::{jk_match.group(1)}"
        # Drop only fragment identifiers; keep query identity otherwise.
        normalized_url = re.sub(r"#.*$", "", normalized_url)
        return f"url::{normalized_url}"
    return f"tcl::{_norm(title)}|{_norm(company)}|{_norm(location)}"


def deduplicate_raw_jobs(jobs: Iterable[RawJobPosting]) -> list[RawJobPosting]:
    """Deduplicate raw crawler jobs."""
    unique: list[RawJobPosting] = []
    seen: set[str] = set()
    for job in jobs:
        key = make_dedupe_key(
            title=job.job_title,
            company=job.company,
            location=job.location,
            url=job.url,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(job)
    logger.info("Raw deduplication: %d unique jobs", len(unique))
    return unique


def deduplicate_job_records(records: Iterable[JobRecord]) -> list[JobRecord]:
    """Deduplicate normalized JobRecords."""
    unique: list[JobRecord] = []
    seen: set[str] = set()
    for record in records:
        key = make_dedupe_key(
            title=record.job_title,
            company=record.employer,
            location=record.location,
            url=record.source_url,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    logger.info("Record deduplication: %d unique jobs", len(unique))
    return unique
