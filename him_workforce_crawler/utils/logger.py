"""Centralized logging configuration for the HIM workforce pipeline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logger(
    name: str = "him_workforce",
    level: int = logging.INFO,
    log_file: Path | str | None = None,
) -> logging.Logger:
    """Create and configure a project logger.

    Args:
        name: Logger name.
        level: Logging level.
        log_file: Optional path for a file handler.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_logger(name: str = "him_workforce") -> logging.Logger:
    """Return an existing logger or create a default one."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        return setup_logger(name)
    return logger
