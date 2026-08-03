"""Unit tests for normalization into JobRecord schema."""

from __future__ import annotations

from models.job_record import FilterDecision, RawJobPosting
from processing.normalization import normalize_job


def test_normalize_maps_core_fields_and_audit_notes():
    raw = RawJobPosting(
        job_title="Health Informatics Analyst",
        company="UMC",
        location="Jackson, MS",
        salary="$60,000 - $75,000 a year",
        description=(
            "Analyze clinical data with SQL and Power BI. FHIR experience preferred. "
            "Bachelor's degree required. RHIA preferred. 2 years experience."
        ),
        url="https://www.indeed.com/viewjob?jk=demo0001",
        source_platform="Indeed",
        collected_at="2026-08-03",
        search_keyword="Health Informatics",
        search_location="Mississippi",
        posting_date="Demo dataset",
    )
    decision = FilterDecision(
        included=True,
        stage="accepted",
        reasons=["matched location 'Mississippi'", "passed all exclusion rules"],
    )
    record = normalize_job(raw, record_id="IND0001", decision=decision)
    assert record.record_id == "IND0001"
    assert record.source_platform == "Indeed"
    assert record.employer == "UMC"
    assert record.work_setting in {"Remote", "Hybrid", "On Site", "Not Specified"}
    assert "SQL" in record.technical_skills
    assert "Filter stage: accepted" in record.notes
    assert "demo/fallback" in record.notes.lower()
