"""Indeed job-board crawler (first pluggable source)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from crawlers.base_crawler import BaseCrawler
from crawlers.fetch_backends import (
    BaseFetcher,
    IndeedAccessBlocked,
    OfflineHtmlFetcher,
    build_fetcher,
    detect_access_block,
)
from models.job_record import RawJobPosting, today_iso
from utils.logger import get_logger

logger = get_logger(__name__)


class IndeedCrawler(BaseCrawler):
    """Crawl Indeed search results and job detail pages.

    Notes for researchers:
    - Indeed frequently challenges automated clients (403 / Permission Denied /
      CAPTCHA). Prefer ``fetch_mode="playwright"`` or ``fetch_mode="offline"``.
    - When a live request is blocked or returns no parseable jobs, the crawler
      can fall back to a transparent demo dataset (``use_demo_fallback=True``).
    - Every raw HTML/JSON payload is written under ``data/raw/``.
    """

    source_platform = "Indeed"
    base_url = "https://www.indeed.com"

    def __init__(
        self,
        raw_output_dir: Path | str,
        *,
        request_delay_seconds: float = 3.0,
        max_pages: int = 1,
        timeout_seconds: int = 45,
        use_demo_fallback: bool = True,
        fetch_mode: str = "requests",
        html_dir: Path | str | None = None,
        browser_headed: bool = False,
        fetcher: BaseFetcher | None = None,
    ) -> None:
        super().__init__(raw_output_dir)
        self.request_delay_seconds = request_delay_seconds
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.use_demo_fallback = use_demo_fallback
        self.fetch_mode = fetch_mode
        self.html_dir = Path(html_dir) if html_dir else None
        self.browser_headed = browser_headed
        self.fetcher = fetcher or build_fetcher(
            fetch_mode,
            html_dir=html_dir,
            headless=not browser_headed,
            timeout_seconds=timeout_seconds,
        )
        self._blocked_count = 0

    def build_search_url(self, keyword: str, location: str, start: int = 0) -> str:
        """Build an Indeed search URL for keyword/location pagination."""
        query = quote_plus(keyword)
        loc = quote_plus(location)
        return f"{self.base_url}/jobs?q={query}&l={loc}&start={start}"

    def fetch_page(self, url: str, **kwargs: Any) -> str:
        """Fetch a page through the configured backend."""
        html = self.fetcher.fetch(url)
        reason = detect_access_block(html)
        if reason:
            self._blocked_count += 1
            raise IndeedAccessBlocked(reason)
        return html

    def parse_jobs(self, page_content: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Parse Indeed search HTML into job summary dictionaries."""
        reason = detect_access_block(page_content)
        if reason:
            logger.warning("Refusing to parse blocked page: %s", reason)
            return []

        soup = BeautifulSoup(page_content, "lxml")
        jobs: list[dict[str, Any]] = []

        cards = soup.select("div.job_seen_beacon, div.resultContent, li.css-5lfssg")
        if not cards:
            cards = soup.select("a[data-jk], div[data-jk]")

        seen_keys: set[str] = set()
        for card in cards:
            job = self._parse_card(card)
            if not job:
                continue
            key = job.get("job_key") or job.get("url") or job.get("job_title")
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            jobs.append(job)

        logger.info("Parsed %d job cards from search page", len(jobs))
        return jobs

    def _parse_card(self, card: Any) -> dict[str, Any] | None:
        title_el = card.select_one(
            "h2.jobTitle span[title], h2.jobTitle a, a.jcs-JobTitle, "
            "h2 span[title], a[data-jk]"
        )
        if title_el is None:
            return None

        title = title_el.get("title") or title_el.get_text(" ", strip=True)
        if not title:
            return None

        href = ""
        link_el = card.select_one("a[href]")
        if link_el and link_el.get("href"):
            href = urljoin(self.base_url, link_el["href"])

        job_key = (
            card.get("data-jk")
            or (link_el.get("data-jk") if link_el else None)
            or self._extract_jk_from_url(href)
        )

        company_el = card.select_one(
            "[data-testid='company-name'], span.companyName, span.css-1h7lukg"
        )
        location_el = card.select_one(
            "[data-testid='text-location'], div.companyLocation, "
            "div[class*='companyLocation']"
        )
        salary_el = card.select_one(
            "div.salary-snippet-container, div.metadata.salary-snippet-container, "
            "[data-testid='attribute_snippet_testid']"
        )
        date_el = card.select_one("span.date, span[data-testid='myJobsStateDate']")
        snippet_el = card.select_one("div.job-snippet, ul li")

        return {
            "job_title": title.strip(),
            "company": company_el.get_text(" ", strip=True) if company_el else "",
            "location": location_el.get_text(" ", strip=True) if location_el else "",
            "salary": salary_el.get_text(" ", strip=True) if salary_el else "",
            "description": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            "url": href or (f"{self.base_url}/viewjob?jk={job_key}" if job_key else ""),
            "posting_date": date_el.get_text(" ", strip=True) if date_el else "",
            "job_key": job_key or "",
        }

    @staticmethod
    def _extract_jk_from_url(url: str) -> str:
        match = re.search(r"[?&]jk=([a-zA-Z0-9]+)", url)
        return match.group(1) if match else ""

    def extract_job_details(self, job_ref: dict[str, Any], **kwargs: Any) -> RawJobPosting:
        """Enrich a job summary with detail-page description when available."""
        description = job_ref.get("description", "")
        url = job_ref.get("url", "")

        if url and self.fetch_mode != "offline":
            try:
                time.sleep(self.request_delay_seconds)
                detail_html = self.fetch_page(url)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                jk = job_ref.get("job_key") or "unknown"
                self.save_raw_payload(f"indeed_detail_{jk}_{stamp}.html", detail_html)
                detail_text = self._parse_detail_description(detail_html)
                if detail_text:
                    description = detail_text
            except Exception as exc:  # noqa: BLE001 - keep crawl resilient
                logger.warning("Detail fetch failed for %s: %s", url, exc)

        return RawJobPosting(
            job_title=job_ref.get("job_title", ""),
            company=job_ref.get("company", ""),
            location=job_ref.get("location", "") or kwargs.get("search_location", ""),
            salary=job_ref.get("salary", ""),
            description=description,
            url=url,
            posting_date=job_ref.get("posting_date", ""),
            source_platform=self.source_platform,
            search_keyword=kwargs.get("search_keyword", ""),
            search_location=kwargs.get("search_location", ""),
            collected_at=today_iso(),
        )

    @staticmethod
    def _parse_detail_description(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        node = soup.select_one(
            "#jobDescriptionText, div.jobsearch-jobDescriptionText, "
            "div[id*='jobDescription']"
        )
        return node.get_text("\n", strip=True) if node else ""

    def crawl_from_html_dir(
        self,
        html_dir: Path | str,
        *,
        search_keyword: str = "",
        search_location: str = "",
    ) -> list[RawJobPosting]:
        """Parse locally saved Indeed HTML pages (anti-bot-safe research path)."""
        offline = OfflineHtmlFetcher(html_dir)
        results: list[RawJobPosting] = []
        files = offline.list_html_files()
        if not files:
            logger.warning("No .html/.htm files found in %s", html_dir)
            return results

        for path in files:
            logger.info("Parsing offline HTML: %s", path.name)
            html = path.read_text(encoding="utf-8", errors="ignore")
            # Archive a copy into data/raw for reproducibility.
            self.save_raw_payload(f"offline_ingest_{path.name}", html)
            meta_keyword, meta_location = self._infer_search_meta_from_filename(
                path.name,
                default_keyword=search_keyword,
                default_location=search_location,
            )
            for card in self.parse_jobs(html):
                results.append(
                    RawJobPosting(
                        job_title=card.get("job_title", ""),
                        company=card.get("company", ""),
                        location=card.get("location", "") or meta_location,
                        salary=card.get("salary", ""),
                        description=card.get("description", ""),
                        url=card.get("url", ""),
                        posting_date=card.get("posting_date", ""),
                        source_platform=self.source_platform,
                        search_keyword=meta_keyword,
                        search_location=meta_location,
                        collected_at=today_iso(),
                    )
                )
        logger.info("Offline ingest complete: %d raw jobs from %d files", len(results), len(files))
        return results

    @staticmethod
    def _infer_search_meta_from_filename(
        filename: str,
        *,
        default_keyword: str,
        default_location: str,
    ) -> tuple[str, str]:
        """Best-effort keyword/location inference from offline filenames."""
        stem = Path(filename).stem
        # Accept patterns like: louisiana__health_information_management.html
        if "__" in stem:
            left, right = stem.split("__", 1)
            location = left.replace("_", " ").strip() or default_location
            keyword = right.replace("_", " ").strip() or default_keyword
            return keyword, location
        return default_keyword, default_location

    def crawl(
        self,
        keywords: list[str],
        locations: list[str],
        **kwargs: Any,
    ) -> list[RawJobPosting]:
        """Crawl Indeed for each keyword × location combination."""
        max_pages = int(kwargs.get("max_pages", self.max_pages))
        fetch_details = bool(kwargs.get("fetch_details", False))
        force_demo = bool(kwargs.get("force_demo", False))
        html_dir = kwargs.get("html_dir", self.html_dir)

        results: list[RawJobPosting] = []
        live_success = False
        self._blocked_count = 0

        try:
            if force_demo:
                logger.warning("force_demo=True: using transparent demo Indeed dataset")
                return self._demo_jobs(keywords, locations)

            if self.fetch_mode == "offline":
                if html_dir is None:
                    raise ValueError("html_dir is required for offline fetch mode")
                results = self.crawl_from_html_dir(
                    html_dir,
                    search_keyword=keywords[0] if keywords else "",
                    search_location=locations[0] if locations else "",
                )
                live_success = bool(results)
            else:
                results, live_success = self._crawl_live(
                    keywords=keywords,
                    locations=locations,
                    max_pages=max_pages,
                    fetch_details=fetch_details,
                )

            if not results and self.use_demo_fallback and not live_success:
                if self._blocked_count:
                    logger.warning(
                        "Indeed blocked automated access (%d challenge/block events). "
                        "Recommended next steps:\n"
                        "  1) python main.py --fetch-mode playwright --browser-headed\n"
                        "  2) python main.py --fetch-mode offline --html-dir data/raw/manual_indeed\n"
                        "Falling back to transparent demo dataset for pipeline continuity.",
                        self._blocked_count,
                    )
                else:
                    logger.warning(
                        "Live Indeed crawl returned no jobs. "
                        "Falling back to transparent demo dataset for pipeline continuity."
                    )
                results = self._demo_jobs(keywords, locations)

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            payload = [job.model_dump() for job in results]
            self.save_raw_payload(
                f"indeed_raw_jobs_{stamp}.json",
                json.dumps(payload, indent=2, ensure_ascii=False),
            )
            logger.info("Indeed crawl complete: %d raw jobs", len(results))
            return results
        finally:
            self.fetcher.close()

    def _crawl_live(
        self,
        *,
        keywords: list[str],
        locations: list[str],
        max_pages: int,
        fetch_details: bool,
    ) -> tuple[list[RawJobPosting], bool]:
        results: list[RawJobPosting] = []
        live_success = False

        for keyword in keywords:
            for location in locations:
                for page_index in range(max_pages):
                    start = page_index * 10
                    url = self.build_search_url(keyword, location, start=start)
                    try:
                        time.sleep(self.request_delay_seconds)
                        html = self.fetch_page(url)
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        safe_kw = re.sub(r"[^a-zA-Z0-9]+", "_", keyword)[:40]
                        safe_loc = re.sub(r"[^a-zA-Z0-9]+", "_", location)[:40]
                        self.save_raw_payload(
                            f"indeed_search_{safe_kw}_{safe_loc}_{start}_{stamp}.html",
                            html,
                        )
                        cards = self.parse_jobs(html)
                        if not cards:
                            logger.warning(
                                "No parseable jobs for keyword=%s location=%s page=%s",
                                keyword,
                                location,
                                page_index,
                            )
                            continue

                        live_success = True
                        for card in cards:
                            if fetch_details:
                                posting = self.extract_job_details(
                                    card,
                                    search_keyword=keyword,
                                    search_location=location,
                                )
                            else:
                                posting = RawJobPosting(
                                    job_title=card.get("job_title", ""),
                                    company=card.get("company", ""),
                                    location=card.get("location", "") or location,
                                    salary=card.get("salary", ""),
                                    description=card.get("description", ""),
                                    url=card.get("url", ""),
                                    posting_date=card.get("posting_date", ""),
                                    source_platform=self.source_platform,
                                    search_keyword=keyword,
                                    search_location=location,
                                    collected_at=today_iso(),
                                )
                            results.append(posting)
                    except IndeedAccessBlocked as exc:
                        self._blocked_count += 1
                        logger.error(
                            "Indeed access blocked keyword=%s location=%s: %s",
                            keyword,
                            location,
                            exc,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "Crawl error keyword=%s location=%s: %s",
                            keyword,
                            location,
                            exc,
                        )

        return results, live_success

    def _demo_jobs(self, keywords: list[str], locations: list[str]) -> list[RawJobPosting]:
        """Return labeled synthetic/demo jobs for offline reproducibility."""
        keyword = keywords[0] if keywords else "Health Information Management"
        samples = [
            {
                "job_title": "Health Information Manager",
                "company": "Ochsner Health",
                "location": "New Orleans, LA",
                "salary": "$70,000 - $85,000 a year",
                "description": (
                    "Manage HIM operations, ensure HIPAA compliance, oversee coding "
                    "quality, and support EHR documentation integrity. RHIA preferred. "
                    "Bachelor's degree in Health Information Management required. "
                    "2-4 years HIM experience."
                ),
            },
            {
                "job_title": "Clinical Documentation Specialist",
                "company": "Houston Methodist",
                "location": "Houston, TX",
                "salary": "$65,000 - $78,000 a year",
                "description": (
                    "Review clinical documentation for completeness and coding accuracy. "
                    "Collaborate with providers on CDI queries. CCS or CDIP preferred. "
                    "Knowledge of ICD-10-CM/PCS and EHR workflows."
                ),
            },
            {
                "job_title": "Medical Records Director",
                "company": "Regional Medical Center",
                "location": "Little Rock, AR",
                "salary": "$90,000 - $110,000 a year",
                "description": (
                    "Lead medical records and health information services. "
                    "Supervise HIM staff, privacy program support, and release of "
                    "information. RHIA required. 5 years experience preferred."
                ),
            },
            {
                "job_title": "Health Informatics Analyst",
                "company": "University Medical Center",
                "location": "Jackson, MS",
                "salary": "$60,000 - $75,000 a year",
                "description": (
                    "Analyze clinical and administrative data, build reports in SQL and "
                    "Power BI, and support interoperability initiatives including FHIR. "
                    "Bachelor's in Health Informatics or related field."
                ),
            },
            {
                "job_title": "Remote Coding Specialist",
                "company": "Cotiviti",
                "location": "Remote",
                "salary": "$28 - $35 an hour",
                "description": (
                    "Remote inpatient/outpatient coding using ICD-10-CM/PCS and CPT. "
                    "CCS or CPC required. Experience with EHR coding modules."
                ),
            },
            {
                "job_title": "Registered Nurse Case Manager",
                "company": "Community Hospital",
                "location": "Baton Rouge, LA",
                "salary": "$80,000 - $95,000 a year",
                "description": (
                    "RN case management for acute care patients. Nursing license required. "
                    "Coordinate discharge planning and clinical pathways."
                ),
            },
            {
                "job_title": "Senior VP of Clinical Operations",
                "company": "Health System Corporate",
                "location": "Dallas, TX",
                "salary": "$180,000 - $220,000 a year",
                "description": (
                    "Executive leadership for clinical operations across the enterprise. "
                    "Over 10 years experience required. Senior leadership background."
                ),
            },
            {
                "job_title": "HIM Coding Auditor",
                "company": "Baptist Health",
                "location": "Remote",
                "salary": "$55,000 - $68,000 a year",
                "description": (
                    "Audit coded encounters for accuracy and compliance. Provide coder "
                    "education. RHIT/CCS preferred. 3+ years coding experience."
                ),
            },
        ]

        demo_jobs: list[RawJobPosting] = []
        for index, sample in enumerate(samples, start=1):
            loc = sample["location"]
            search_location = locations[0] if locations else loc
            demo_jobs.append(
                RawJobPosting(
                    job_title=sample["job_title"],
                    company=sample["company"],
                    location=loc,
                    salary=sample["salary"],
                    description=sample["description"],
                    url=f"https://www.indeed.com/viewjob?jk=demo{index:04d}",
                    posting_date="Demo dataset",
                    source_platform=self.source_platform,
                    search_keyword=keyword,
                    search_location=search_location,
                    collected_at=today_iso(),
                )
            )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.save_raw_payload(
            f"indeed_demo_jobs_{stamp}.json",
            json.dumps([job.model_dump() for job in demo_jobs], indent=2),
        )
        return demo_jobs
