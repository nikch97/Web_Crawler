"""Unit tests for deduplication helpers."""

from __future__ import annotations

from models.job_record import RawJobPosting
from processing.deduplication import deduplicate_raw_jobs


def test_deduplicate_by_url_and_tcl():
    jobs = [
        RawJobPosting(
            job_title="HIM Manager",
            company="A",
            location="LA",
            url="https://indeed.com/viewjob?jk=1",
        ),
        RawJobPosting(
            job_title="HIM Manager",
            company="A",
            location="LA",
            url="https://indeed.com/viewjob?jk=1&from=search",
        ),
        RawJobPosting(
            job_title="Coder",
            company="B",
            location="TX",
            url="",
        ),
        RawJobPosting(
            job_title="Coder",
            company="B",
            location="TX",
            url="",
        ),
    ]
    unique = deduplicate_raw_jobs(jobs)
    assert len(unique) == 2
