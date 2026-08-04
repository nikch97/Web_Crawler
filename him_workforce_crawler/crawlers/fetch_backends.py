"""HTTP / browser / offline fetch backends for job-board crawlers.

Indeed and similar boards actively block datacenter IP + ``requests`` clients.
This module provides research-appropriate alternatives:

1. ``requests`` — simple HTTP (often blocked)
2. ``playwright`` — real browser automation (more resilient)
3. ``offline`` — parse HTML the researcher saved manually from a normal browser

This intentionally does **not** implement CAPTCHA-solving services, residential
proxy rotation, or fingerprint spoofing meant to defeat anti-bot systems.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

BLOCK_MARKERS = (
    "permission denied",
    "access denied",
    "unusual traffic",
    "additional verification",
    "captcha",
    "cf-challenge",
    "akamai",
    "request blocked",
    "are you a robot",
    "verify you are a human",
    "enable javascript and cookies",
    "sorry, we just need to make sure you're not a robot",
)


class IndeedAccessBlocked(RuntimeError):
    """Raised when Indeed returns a bot-challenge / permission-denied page."""


def detect_access_block(html: str, *, status_code: int | None = None) -> str | None:
    """Return a human-readable block reason, or ``None`` if the page looks usable."""
    if status_code in {401, 403, 429, 503}:
        return f"HTTP {status_code} from Indeed (likely anti-bot / rate limit)"

    text = (html or "").lower()
    if not text.strip():
        return "empty response body"

    for marker in BLOCK_MARKERS:
        if marker in text:
            return f"anti-bot challenge detected ({marker})"

    # Challenge pages are usually short and lack job-result markers.
    has_job_markers = bool(
        re.search(r"job_seen_beacon|jobsearch-ResultsList|data-jk=|jobTitle", html or "")
    )
    if status_code == 200 and len(text) < 1500 and not has_job_markers:
        return "suspiciously short non-results page (possible soft block)"

    return None


class BaseFetcher(ABC):
    """Common interface for page retrieval backends."""

    name: str = "base"

    @abstractmethod
    def fetch(self, url: str) -> str:
        """Return HTML for ``url`` or raise on hard failure / block."""

    def close(self) -> None:
        """Release any held resources."""


class RequestsFetcher(BaseFetcher):
    """Plain HTTP fetcher. Convenient, but frequently blocked by Indeed."""

    name = "requests"

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: int = 30,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }
        if headers:
            default_headers.update(headers)
        self.session.headers.update(default_headers)

    def fetch(self, url: str) -> str:
        logger.info("[requests] GET %s", url)
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise IndeedAccessBlocked(f"network error fetching {url}: {exc}") from exc

        reason = detect_access_block(response.text, status_code=response.status_code)
        if reason:
            raise IndeedAccessBlocked(
                f"{reason}. Plain HTTP requests are commonly blocked by Indeed. "
                "Try --fetch-mode playwright or --fetch-mode offline."
            )
        if response.status_code >= 400:
            raise IndeedAccessBlocked(
                f"HTTP {response.status_code} for {url}. "
                "Try --fetch-mode playwright or --fetch-mode offline."
            )
        return response.text


class PlaywrightFetcher(BaseFetcher):
    """Fetch pages with a real Chromium browser via Playwright.

    Requires optional dependency:
        pip install playwright
        playwright install chromium
    """

    name = "playwright"

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_ms: int = 45000,
        slow_mo_ms: int = 0,
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.slow_mo_ms = slow_mo_ms
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Install with:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo_ms,
        )
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1365, "height": 900},
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout_ms)

    def fetch(self, url: str) -> str:
        self._ensure_browser()
        assert self._page is not None
        logger.info("[playwright] GET %s (headless=%s)", url, self.headless)
        response = self._page.goto(url, wait_until="domcontentloaded")
        # Give client-rendered job cards a moment to appear.
        try:
            self._page.wait_for_selector(
                "div.job_seen_beacon, a[data-jk], h2.jobTitle",
                timeout=min(15000, self.timeout_ms),
            )
        except Exception:  # noqa: BLE001
            logger.debug("Job-card selector wait timed out; continuing with page HTML")

        html = self._page.content()
        status = response.status if response is not None else None
        reason = detect_access_block(html, status_code=status)
        if reason:
            raise IndeedAccessBlocked(
                f"{reason}. Browser fetch was still challenged. "
                "Use headed mode (--browser-headed) and complete any challenge once, "
                "or switch to --fetch-mode offline and save HTML from your own browser."
            )
        return html

    def close(self) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except Exception:  # noqa: BLE001
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None


class OfflineHtmlFetcher(BaseFetcher):
    """Read previously saved HTML files instead of contacting Indeed live.

    This is the most reliable research workflow when anti-bot protections block
    automation: open Indeed in a normal browser, save pages as HTML, then parse.
    """

    name = "offline"

    def __init__(self, html_dir: Path | str) -> None:
        self.html_dir = Path(html_dir)
        if not self.html_dir.exists():
            raise FileNotFoundError(
                f"Offline HTML directory not found: {self.html_dir}. "
                "Create it and save Indeed search-result pages there."
            )

    def fetch(self, url: str) -> str:
        raise IndeedAccessBlocked(
            "OfflineHtmlFetcher does not fetch URLs. "
            "Use IndeedCrawler.crawl_from_html_dir() / --fetch-mode offline."
        )

    def list_html_files(self) -> list[Path]:
        files = sorted(self.html_dir.glob("*.html")) + sorted(self.html_dir.glob("*.htm"))
        return files


def build_fetcher(
    mode: str,
    *,
    html_dir: Path | str | None = None,
    headless: bool = True,
    request_delay_seconds: float = 2.0,  # retained for API symmetry / future use
    timeout_seconds: int = 30,
) -> BaseFetcher:
    """Factory for fetch backends."""
    normalized = (mode or "requests").strip().lower()
    if normalized == "requests":
        return RequestsFetcher(timeout_seconds=timeout_seconds)
    if normalized == "playwright":
        return PlaywrightFetcher(headless=headless, timeout_ms=timeout_seconds * 1000)
    if normalized == "offline":
        if html_dir is None:
            raise ValueError("--html-dir is required when --fetch-mode offline")
        return OfflineHtmlFetcher(html_dir)
    raise ValueError(
        f"Unknown fetch mode '{mode}'. Choose: requests, playwright, offline"
    )
