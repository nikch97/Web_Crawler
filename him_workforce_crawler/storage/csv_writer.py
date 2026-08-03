"""CSV persistence helpers for HIM workforce outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from models.job_record import JobRecord
from utils.logger import get_logger

logger = get_logger(__name__)


def write_job_records_csv(
    records: Iterable[JobRecord],
    path: Path | str,
    field_names: list[str],
) -> Path:
    """Write JobRecords to CSV using the HIM Data Dictionary column order."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [record.to_schema_dict(field_names) for record in records]
    dataframe = pd.DataFrame(rows, columns=field_names)
    dataframe.to_csv(output_path, index=False, encoding="utf-8")
    logger.info("Wrote %d records to %s", len(rows), output_path)
    return output_path


def write_dicts_csv(rows: list[dict[str, Any]], path: Path | str) -> Path:
    """Write a list of dictionaries to CSV."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe = pd.DataFrame(rows)
    dataframe.to_csv(output_path, index=False, encoding="utf-8")
    logger.info("Wrote %d rows to %s", len(rows), output_path)
    return output_path
