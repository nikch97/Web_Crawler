"""Generic tabular file loader supporting CSV and Excel inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm"}


def resolve_input_path(path: Path | str) -> Path:
    """Resolve a path, preferring the given file and common alternate extensions.

    This allows callers to request ``Inclusion.csv`` or ``Inclusion.xlsx``
    interchangeably when either exists.
    """
    candidate = Path(path)
    if candidate.exists():
        return candidate

    for extension in SUPPORTED_EXTENSIONS:
        alt = candidate.with_suffix(extension)
        if alt.exists():
            logger.info("Resolved %s -> %s", candidate, alt)
            return alt

    raise FileNotFoundError(f"Input file not found: {candidate}")


def load_tabular_file(
    path: Path | str,
    *,
    header: int | None = None,
    sheet_name: str | int = 0,
    dtype: Any = str,
) -> pd.DataFrame:
    """Load a CSV or Excel workbook into a DataFrame.

    Args:
        path: Path to a ``.csv``, ``.xlsx``, ``.xls``, or ``.xlsm`` file.
        header: Row index to use as column names, or ``None`` for no header.
        sheet_name: Excel sheet name/index (ignored for CSV).
        dtype: Column dtype coercion passed to pandas.

    Returns:
        DataFrame with all values treated as strings by default.
    """
    resolved = resolve_input_path(path)
    suffix = resolved.suffix.lower()
    logger.debug("Loading tabular file: %s", resolved)

    if suffix == ".csv":
        return pd.read_csv(resolved, header=header, dtype=dtype, keep_default_na=False)

    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(
            resolved,
            header=header,
            sheet_name=sheet_name,
            dtype=dtype,
            engine="openpyxl",
        )

    raise ValueError(f"Unsupported file type: {suffix}")


def dataframe_to_raw_rows(df: pd.DataFrame) -> list[list[str]]:
    """Convert a DataFrame into a list of row cell lists (string-normalized)."""
    rows: list[list[str]] = []
    for _, series in df.iterrows():
        row = ["" if pd.isna(value) else str(value).strip() for value in series.tolist()]
        rows.append(row)
    return rows
