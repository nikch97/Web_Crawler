"""Tests for anti-bot detection and offline HTML ingest."""

from __future__ import annotations

from pathlib import Path

import pytest

from crawlers.fetch_backends import IndeedAccessBlocked, RequestsFetcher, detect_access_block
from crawlers.indeed_crawler import IndeedCrawler

FIXTURE = Path(__file__).parent / "fixtures" / "indeed_search_sample.html"


def test_detect_permission_denied_block():
    html = "<html><body>Permission denied. Please verify you are a human.</body></html>"
    reason = detect_access_block(html, status_code=403)
    assert reason is not None
    assert "403" in reason or "anti-bot" in reason or "permission" in reason.lower()


def test_detect_clean_results_page_not_blocked():
    html = FIXTURE.read_text(encoding="utf-8")
    assert detect_access_block(html, status_code=200) is None


def test_requests_fetcher_raises_on_blocked_response(monkeypatch):
    class DummyResponse:
        status_code = 403
        text = "Permission Denied"

    class DummySession:
        headers: dict = {}

        def get(self, url, timeout=30):  # noqa: ARG002
            return DummyResponse()

    fetcher = RequestsFetcher(session=DummySession())  # type: ignore[arg-type]
    with pytest.raises(IndeedAccessBlocked):
        fetcher.fetch("https://www.indeed.com/jobs?q=test")


def test_offline_html_ingest(tmp_path: Path):
    html_dir = tmp_path / "manual"
    html_dir.mkdir()
    target = html_dir / "louisiana__health_information_management.html"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    crawler = IndeedCrawler(
        raw_output_dir=tmp_path / "raw",
        fetch_mode="offline",
        html_dir=html_dir,
        use_demo_fallback=False,
    )
    jobs = crawler.crawl(
        keywords=["Health Information Management"],
        locations=["Louisiana"],
    )
    assert len(jobs) == 2
    assert jobs[0].job_title == "Health Information Manager"
    assert jobs[0].search_location.lower().startswith("louisiana")
