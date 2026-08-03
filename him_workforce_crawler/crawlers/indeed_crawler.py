"""Indeed job-board crawler (first pluggable source)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

from crawlers.base_crawler import BaseCrawler
from models.job_record import RawJobPosting, today_iso
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Connection": "keep-alive",
}


class IndeedCrawler(BaseCrawler):
    """Crawl Indeed search results and job detail pages.

    Notes for researchers:
    - Indeed frequently challenges automated clients. When a live request is
      blocked or returns no parseable jobs, the crawler can fall back to a
      transparent demo dataset (``use_demo_fallback=True``) so the downstream
      filtering/normalization pipeline remains testable and reproducible.
    - Every raw HTML/JSON payload is written under ``data/raw/``.
    """

    source_platform = "Indeed"
    base_url = "https://www.indeed.com"

    def __init__(
        self,
        raw_output_dir: Path | str,
        *,
        request_delay_seconds: float = 2.0,
        max_pages: int = 1,
        timeout_seconds: int = 30,
        use_demo_fallback: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(raw_output_dir)
        self.request_delay_seconds = request_delay_seconds
        self.max_pages = max_pages
        self.timeout_seconds = timeout_seconds
        self.use_demo_fallback = use_demo_fallback
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def build_search_url(self, keyword: str, location: str, start: int = 0) -> str:
        """Build an Indeed search URL for keyword/location pagination."""
        query = quote_plus(keyword)
        loc = quote_plus(location)
        return f"{self.base_url}/jobs?q={query}&l={loc}&start={start}"

    def fetch_page(self, url: str, **kwargs: Any) -> str:
        """HTTP GET a page with retry-friendly error handling."""
        logger.info("Fetching Indeed page: %s", url)
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            raise

    def parse_jobs(self, page_content: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Parse Indeed search HTML into job summary dictionaries."""
        soup = BeautifulSoup(page_content, "lxml")
        jobs: list[dict[str, Any]] = []

        cards = soup.select("div.job_seen_beacon, div.resultContent, li.css-5lfssg")
        if not cards:
            # Fallback selectors for alternate Indeed layouts.
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
        detail_html = ""

        if url:
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

        results: list[RawJobPosting] = []
        live_success = False

        if force_demo:
            logger.warning("force_demo=True: using transparent demo Indeed dataset")
            return self._demo_jobs(keywords, locations)

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
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "Crawl error keyword=%s location=%s: %s",
                            keyword,
                            location,
                            exc,
                        )

        if not results and self.use_demo_fallback and not live_success:
            logger.warning(
                "Live Indeed crawl returned no jobs (blocked or empty). "
                "Falling back to transparent demo dataset for pipeline continuity."
            )
            results = self._demo_jobs(keywords, locations)

        # Persist consolidated raw JSON for reproducibility.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        payload = [job.model_dump() for job in results]
        self.save_raw_payload(
            f"indeed_raw_jobs_{stamp}.json",
            json.dumps(payload, indent=2, ensure_ascii=False),
        )
        logger.info("Indeed crawl complete: %d raw jobs", len(results))
        return results

    def _demo_jobs(self, keywords: list[str], locations: list[str]) -> list[RawJobPosting]:
        """Return labeled synthetic/demo jobs for offline reproducibility.

        Each record notes that it is a demo fallback so research outputs remain
        transparent about provenance.
        """
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
            # Prefer an inclusion location when available for search provenance.
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
