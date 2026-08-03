"""JSON persistence helpers for HIM workforce outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from models.job_record import JobRecord
from utils.logger import get_logger

logger = get_logger(__name__)


def write_json(data: Any, path: Path | str, *, indent: int = 2) -> Path:
    """Serialize an arbitrary JSON-compatible object to disk."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=indent, ensure_ascii=False)
    logger.info("Wrote JSON: %s", output_path)
    return output_path


def write_job_records_json(
    records: Iterable[JobRecord],
    path: Path | str,
    field_names: list[str],
) -> Path:
    """Write JobRecords as a JSON array of schema-ordered dictionaries."""
    payload = [record.to_schema_dict(field_names) for record in records]
    return write_json(payload, path)
