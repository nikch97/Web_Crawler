"""Unit tests for the rule-based filtering engine."""

from __future__ import annotations

from models.job_record import RawJobPosting
from processing.filtering import JobFilterEngine


INCLUSION = {
    "locations": ["Louisiana", "Arkansas", "Mississippi", "Texas", "Remote"],
}

EXCLUSION = {
    "title_keywords": ["RN", "Nurse", "Nursing"],
    "description_keywords": [],
    "career_level_keywords": ["Senior", "VP"],
    "experience_rules": {"maximum_years": 7},
    "salary_rules": {"maximum_annual": 150000},
}


def _job(**kwargs) -> RawJobPosting:
    defaults = {
        "job_title": "Health Information Manager",
        "company": "Example Health",
        "location": "New Orleans, LA",
        "salary": "$70,000 a year",
        "description": "HIM operations. RHIA preferred. 3 years experience.",
        "url": "https://www.indeed.com/viewjob?jk=abc",
        "source_platform": "Indeed",
    }
    defaults.update(kwargs)
    return RawJobPosting(**defaults)


def test_includes_louisiana_him_role():
    engine = JobFilterEngine(INCLUSION, EXCLUSION)
    decision = engine.evaluate(_job())
    assert decision.included is True


def test_excludes_nurse_title():
    engine = JobFilterEngine(INCLUSION, EXCLUSION)
    decision = engine.evaluate(
        _job(job_title="Registered Nurse Case Manager", description="RN license required")
    )
    assert decision.included is False
    assert decision.stage.startswith("exclusion:")


def test_excludes_high_experience():
    engine = JobFilterEngine(INCLUSION, EXCLUSION)
    decision = engine.evaluate(
        _job(description="Over 10 years experience required in HIM leadership")
    )
    assert decision.included is False
    assert "experience" in decision.stage


def test_excludes_out_of_area_location():
    engine = JobFilterEngine(INCLUSION, EXCLUSION)
    decision = engine.evaluate(_job(location="Seattle, WA"))
    assert decision.included is False
    assert "location" in decision.stage


def test_includes_remote():
    engine = JobFilterEngine(INCLUSION, EXCLUSION)
    decision = engine.evaluate(_job(location="Remote"))
    assert decision.included is True
