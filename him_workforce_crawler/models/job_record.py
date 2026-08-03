"""Pydantic models for raw crawl results and standardized HIM workforce records."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RawJobPosting(BaseModel):
    """Unnormalized job posting captured by a crawler."""

    model_config = ConfigDict(extra="allow")

    job_title: str = ""
    company: str = ""
    location: str = ""
    salary: str = ""
    description: str = ""
    url: str = ""
    posting_date: str = ""
    source_platform: str = ""
    search_keyword: str = ""
    search_location: str = ""
    collected_at: str = ""


class FilterDecision(BaseModel):
    """Explainable include/exclude decision for research transparency."""

    included: bool
    stage: str
    reasons: list[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    """Standardized HIM workforce record aligned to HIM_Data_Dictionary.csv.

    Only dictionary fields are first-class model attributes. Audit metadata for
    filtering decisions is kept separately and may be folded into ``notes``.
    """

    model_config = ConfigDict(extra="ignore")

    record_id: str = ""
    dataset_source: str = ""
    collection_date: str = ""
    source_url: str = ""
    source_platform: str = ""
    job_title: str = ""
    alternate_titles: str = ""
    employer: str = ""
    location: str = ""
    work_setting: str = "Not Specified"
    career_level: str = "Not Specified"
    himss_category: str = "Not Specified"
    beebe_domain: str = ""
    ahima_career_map_domain: str = ""
    ahima_rhia_domain_alignment: str = ""
    himss_domain_alignment: str = ""
    education_requirements: str = ""
    required_qualifications: str = ""
    preferred_qualifications: str = ""
    credential_required: str = "Not Specified"
    credential_preferred: str = "Not Specified"
    experience_requirements: str = ""
    work_requirements: str = ""
    long_description: str = ""
    primary_responsibilities: str = ""
    qualifications: str = ""
    technical_skills: str = ""
    soft_skills: str = ""
    ai_or_emerging_technology_skills: str = ""
    salary_or_pay: str = "Not Listed"
    employment_type: str = "Not Specified"
    notes: str = ""

    # Research audit fields (not written to final CSV unless requested)
    filter_decision: Optional[FilterDecision] = None

    def to_schema_dict(self, field_names: list[str]) -> dict[str, Any]:
        """Serialize only the dictionary-defined fields in schema order."""
        data = self.model_dump(exclude={"filter_decision"})
        return {name: data.get(name, "") for name in field_names}


def today_iso() -> str:
    """Return today's date in YYYY-MM-DD format."""
    return date.today().isoformat()
