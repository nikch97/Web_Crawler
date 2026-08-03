"""Load the HIM Data Dictionary into OUTPUT_SCHEMA and ALLOWED_VALUES."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.excel_loader import dataframe_to_raw_rows, load_tabular_file
from utils.logger import get_logger

logger = get_logger(__name__)

HEADER_ALIASES = {
    "variable name": "variable_name",
    "label": "label",
    "definition": "definition",
    "data type": "data_type",
    "allowed values / examples": "allowed_values",
    "allowed values": "allowed_values",
    "applies to": "applies_to",
    "source sheet / column": "source_columns",
    "source columns": "source_columns",
    "notes for coding": "notes",
}


def _find_header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    """Locate the dictionary header row containing 'Variable Name'."""
    for index, row in enumerate(rows):
        normalized = [cell.strip().lower() for cell in row if cell.strip()]
        if not normalized:
            continue
        if normalized[0] == "variable name":
            return index, row
    raise ValueError(
        "Could not find a 'Variable Name' header row in the HIM Data Dictionary."
    )


def _parse_allowed_values(raw: str) -> list[str]:
    """Split semicolon-delimited allowed values / examples into a list."""
    if not raw or not raw.strip():
        return []
    parts = [part.strip() for part in raw.split(";")]
    return [part for part in parts if part]


def load_data_dictionary(path: Path | str) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Load OUTPUT_SCHEMA and ALLOWED_VALUES from HIM_Data_Dictionary.csv/.xlsx.

    Returns:
        Tuple of ``(OUTPUT_SCHEMA, ALLOWED_VALUES)`` where:

        - OUTPUT_SCHEMA maps field name -> metadata (label, definition, data_type, ...)
        - ALLOWED_VALUES maps field name -> list of example/allowed values
    """
    df = load_tabular_file(path, header=None)
    rows = dataframe_to_raw_rows(df)
    header_index, header_row = _find_header_row(rows)

    column_map: dict[int, str] = {}
    for col_index, cell in enumerate(header_row):
        alias = HEADER_ALIASES.get(cell.strip().lower())
        if alias:
            column_map[col_index] = alias

    if "variable_name" not in column_map.values():
        raise ValueError("Data dictionary header is missing Variable Name.")

    output_schema: dict[str, dict[str, Any]] = {}
    allowed_values: dict[str, list[str]] = {}

    for row in rows[header_index + 1 :]:
        record: dict[str, str] = {}
        for col_index, key in column_map.items():
            value = row[col_index] if col_index < len(row) else ""
            record[key] = value.strip()

        variable_name = record.get("variable_name", "").strip()
        if not variable_name:
            continue

        # Skip accidental repeated header rows.
        if variable_name.lower() == "variable name":
            continue

        field_meta = {
            "variable_name": variable_name,
            "label": record.get("label", ""),
            "definition": record.get("definition", ""),
            "data_type": record.get("data_type", ""),
            "applies_to": record.get("applies_to", ""),
            "source_columns": record.get("source_columns", ""),
            "notes": record.get("notes", ""),
            "allowed_values_raw": record.get("allowed_values", ""),
        }
        output_schema[variable_name] = field_meta

        parsed_allowed = _parse_allowed_values(record.get("allowed_values", ""))
        if parsed_allowed:
            allowed_values[variable_name] = parsed_allowed

    if not output_schema:
        raise ValueError(f"No schema fields found in data dictionary: {path}")

    logger.info(
        "Loaded OUTPUT_SCHEMA (%d fields) and ALLOWED_VALUES (%d fields) from %s",
        len(output_schema),
        len(allowed_values),
        path,
    )
    return output_schema, allowed_values


def schema_field_names(output_schema: dict[str, dict[str, Any]]) -> list[str]:
    """Return ordered schema field names."""
    return list(output_schema.keys())
