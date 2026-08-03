"""Validate JobRecords against the HIM Data Dictionary schema."""

from __future__ import annotations

import re
from typing import Any

from models.job_record import JobRecord
from utils.logger import get_logger

logger = get_logger(__name__)


class ValidationIssue:
    """A single validation finding for a job record."""

    def __init__(self, record_id: str, field: str, message: str, severity: str = "error") -> None:
        self.record_id = record_id
        self.field = field
        self.message = message
        self.severity = severity

    def as_dict(self) -> dict[str, str]:
        return {
            "record_id": self.record_id,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }


REQUIRED_FOR_JOB_POSTINGS = {
    "record_id",
    "dataset_source",
    "collection_date",
    "source_url",
    "source_platform",
    "job_title",
    "employer",
    "location",
}


def validate_record(
    record: JobRecord,
    output_schema: dict[str, dict[str, Any]],
    allowed_values: dict[str, list[str]],
    *,
    enforce_allowed_values: bool = False,
) -> list[ValidationIssue]:
    """Validate one JobRecord against OUTPUT_SCHEMA / ALLOWED_VALUES.

    Args:
        record: Normalized job record.
        output_schema: Schema loaded from the data dictionary.
        allowed_values: Allowed/example values from the data dictionary.
        enforce_allowed_values: When True, categorical fields must match an
            allowed example. Defaults to False because the dictionary mixes
            examples with open-ended guidance.
    """
    issues: list[ValidationIssue] = []
    data = record.model_dump(exclude={"filter_decision"})

    # Unknown fields are ignored by the model; ensure required schema coverage.
    for field_name in REQUIRED_FOR_JOB_POSTINGS:
        if field_name not in output_schema:
            continue
        value = data.get(field_name, "")
        if value is None or str(value).strip() == "":
            issues.append(
                ValidationIssue(
                    record.record_id,
                    field_name,
                    "required field is empty",
                    severity="error",
                )
            )

    for field_name, meta in output_schema.items():
        if field_name not in data:
            issues.append(
                ValidationIssue(
                    record.record_id,
                    field_name,
                    "schema field missing from JobRecord model",
                    severity="warning",
                )
            )
            continue

        value = data.get(field_name, "")
        data_type = (meta.get("data_type") or "").lower()

        if "date" in data_type and value:
            # Accept YYYY-MM-DD or month/year text per dictionary guidance.
            if not (
                re_match_date(str(value))
                or re_match_month_year(str(value))
            ):
                issues.append(
                    ValidationIssue(
                        record.record_id,
                        field_name,
                        f"date-like value may be nonstandard: {value}",
                        severity="warning",
                    )
                )

        if enforce_allowed_values and field_name in allowed_values:
            allowed = allowed_values[field_name]
            # Only enforce when dictionary values look categorical/short.
            categoricalish = all(len(item) <= 60 for item in allowed)
            if categoricalish and str(value) and str(value) not in allowed:
                # Allow "Not Specified" / "Not Listed" / "Not Applicable" soft defaults.
                soft_defaults = {
                    "not specified",
                    "not listed",
                    "not applicable",
                    "",
                }
                if str(value).strip().lower() not in soft_defaults:
                    issues.append(
                        ValidationIssue(
                            record.record_id,
                            field_name,
                            f"value '{value}' not in allowed examples",
                            severity="warning",
                        )
                    )

    return issues


def re_match_date(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()))


def re_match_month_year(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}", value.strip()))


def validate_records(
    records: list[JobRecord],
    output_schema: dict[str, dict[str, Any]],
    allowed_values: dict[str, list[str]],
) -> tuple[list[JobRecord], list[dict[str, str]]]:
    """Validate many records; return records plus flattened issue dictionaries."""
    all_issues: list[dict[str, str]] = []
    valid_records: list[JobRecord] = []

    for record in records:
        issues = validate_record(record, output_schema, allowed_values)
        all_issues.extend(issue.as_dict() for issue in issues)
        # Keep records with warnings; drop only if required-field errors exist.
        has_error = any(issue.severity == "error" for issue in issues)
        if has_error:
            logger.warning(
                "Record %s failed validation: %s",
                record.record_id,
                "; ".join(i.message for i in issues if i.severity == "error"),
            )
        else:
            valid_records.append(record)

    logger.info(
        "Validation complete: %d valid / %d total (%d issues)",
        len(valid_records),
        len(records),
        len(all_issues),
    )
    return valid_records, all_issues
