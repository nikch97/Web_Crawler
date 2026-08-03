"""Unit tests for inclusion/exclusion/schema loaders."""

from __future__ import annotations

from pathlib import Path

from config.rules_loader import load_exclusion_rules, load_inclusion_rules
from config.schema_loader import load_data_dictionary, schema_field_names

INPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "input"


def test_load_inclusion_rules_locations():
    rules = load_inclusion_rules(INPUT_DIR / "Inclusion.csv")
    assert "locations" in rules
    assert "Louisiana" in rules["locations"]
    assert "Remote" in rules["locations"]
    # Dictionary artifact must not be ingested as locations.
    assert "record_id" not in rules["locations"]
    assert "Variable Name" not in rules["locations"]


def test_load_exclusion_rules_classification():
    rules = load_exclusion_rules(INPUT_DIR / "Exclusion.csv")
    assert "RN" in rules["title_keywords"] or "Nurse" in rules["title_keywords"]
    assert rules["experience_rules"].get("maximum_years") == 7
    assert rules["salary_rules"].get("maximum_annual") == 150000
    assert any(item.lower() == "senior" for item in rules["career_level_keywords"])


def test_load_data_dictionary_schema():
    output_schema, allowed_values = load_data_dictionary(
        INPUT_DIR / "HIM_Data_Dictionary.csv"
    )
    fields = schema_field_names(output_schema)
    assert "record_id" in fields
    assert "job_title" in fields
    assert "ai_or_emerging_technology_skills" in fields
    assert "source_platform" in allowed_values
    assert "Indeed" in allowed_values["source_platform"]
