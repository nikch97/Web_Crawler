"""Abstract base crawler for pluggable job-board sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from models.job_record import RawJobPosting
from utils.logger import get_logger

logger = get_logger(__name__)


class BaseCrawler(ABC):
    """Contract that all job-source crawlers must implement.

    Future sources (LinkedIn, hospital career pages, etc.) should subclass
    this crawler and plug into the same pipeline without changing core logic.
    """

    source_platform: str = "unknown"

    def __init__(self, raw_output_dir: Path | str) -> None:
        self.raw_output_dir = Path(raw_output_dir)
        self.raw_output_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def fetch_page(self, url: str, **kwargs: Any) -> str:
        """Fetch a page and return its HTML/text content."""

    @abstractmethod
    def parse_jobs(self, page_content: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Parse a search-results page into lightweight job dictionaries."""

    @abstractmethod
    def extract_job_details(self, job_ref: dict[str, Any], **kwargs: Any) -> RawJobPosting:
        """Fetch/parse a single job detail page into a RawJobPosting."""

    @abstractmethod
    def crawl(
        self,
        keywords: list[str],
        locations: list[str],
        **kwargs: Any,
    ) -> list[RawJobPosting]:
        """Execute an end-to-end crawl for the given keywords and locations."""

    def save_raw_payload(self, filename: str, content: str | bytes) -> Path:
        """Persist a raw HTML/JSON payload under data/raw/."""
        path = self.raw_output_dir / filename
        mode = "wb" if isinstance(content, bytes) else "w"
        encoding = None if isinstance(content, bytes) else "utf-8"
        with path.open(mode, encoding=encoding) as handle:
            handle.write(content)
        logger.debug("Saved raw payload: %s", path)
        return path
