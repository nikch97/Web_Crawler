#!/usr/bin/env python3
"""HIM Workforce Crawler — Phase 1 entrypoint (Indeed source).

Pipeline:
    1. Load Inclusion / Exclusion / Data Dictionary rules dynamically
    2. Crawl Indeed for configured search keywords × inclusion locations
    3. Deduplicate raw postings
    4. Apply location / relevance / exclusion filters (rule engine)
    5. Normalize to HIM Data Dictionary schema
    6. Validate and write final CSV (+ audit artifacts)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Ensure package-relative imports work when launched as `python main.py`.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.rules_loader import load_exclusion_rules, load_inclusion_rules
from config.schema_loader import load_data_dictionary, schema_field_names
from crawlers.indeed_crawler import IndeedCrawler
from processing.deduplication import deduplicate_job_records, deduplicate_raw_jobs
from processing.filtering import JobFilterEngine
from processing.normalization import normalize_job
from processing.validation import validate_records
from storage.csv_writer import write_dicts_csv, write_job_records_csv
from storage.json_writer import write_json, write_job_records_json
from utils.logger import get_logger, setup_logger

DEFAULT_SEARCH_KEYWORDS = [
    "Health Information Management",
    "Health Informatics",
    "RHIA",
    "Medical Records",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HIM workforce job-market collector (Indeed, Phase 1)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "data" / "input",
        help="Directory containing Inclusion, Exclusion, and HIM_Data_Dictionary files",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "data" / "raw",
        help="Directory for raw crawl payloads",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=ROOT / "data" / "processed",
        help="Directory for intermediate/audit outputs",
    )
    parser.add_argument(
        "--final-dir",
        type=Path,
        default=ROOT / "data" / "final",
        help="Directory for final standardized dataset",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="Indeed search keywords (defaults to HIM-oriented research terms or .env)",
    )
    parser.add_argument(
        "--locations",
        nargs="+",
        default=None,
        help="Override inclusion locations (defaults to Inclusion.csv locations)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="Max Indeed result pages per keyword/location",
    )
    parser.add_argument(
        "--fetch-details",
        action="store_true",
        help="Fetch each job detail page (slower; more complete descriptions)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Force transparent demo dataset (skip live Indeed requests)",
    )
    parser.add_argument(
        "--no-demo-fallback",
        action="store_true",
        help="Do not fall back to demo data if live crawl is empty/blocked",
    )
    parser.add_argument(
        "--fetch-mode",
        choices=["requests", "playwright", "offline"],
        default="requests",
        help=(
            "How to obtain Indeed HTML: requests (often blocked), "
            "playwright (real browser), offline (parse saved HTML)"
        ),
    )
    parser.add_argument(
        "--html-dir",
        type=Path,
        default=ROOT / "data" / "raw" / "manual_indeed",
        help="Directory of manually saved Indeed HTML pages (offline mode)",
    )
    parser.add_argument(
        "--browser-headed",
        action="store_true",
        help="Run Playwright with a visible browser window (useful for challenges)",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=3.0,
        help="Seconds to wait between Indeed requests (be polite; default 3)",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=ROOT / "data" / "processed" / "pipeline.log",
        help="Optional log file path",
    )
    return parser.parse_args()


def resolve_search_keywords(cli_keywords: list[str] | None) -> list[str]:
    if cli_keywords:
        return cli_keywords
    env_value = os.getenv("HIM_SEARCH_KEYWORDS", "").strip()
    if env_value:
        return [part.strip() for part in env_value.split("|") if part.strip()]
    return list(DEFAULT_SEARCH_KEYWORDS)


def run_pipeline(args: argparse.Namespace) -> int:
    setup_logger("him_workforce", log_file=args.log_file)
    logger = get_logger("him_workforce")
    load_dotenv(ROOT / ".env")

    inclusion_path = args.input_dir / "Inclusion.csv"
    exclusion_path = args.input_dir / "Exclusion.csv"
    dictionary_path = args.input_dir / "HIM_Data_Dictionary.csv"

    logger.info("Loading configuration from %s", args.input_dir)
    inclusion_rules = load_inclusion_rules(inclusion_path)
    exclusion_rules = load_exclusion_rules(exclusion_path)
    output_schema, allowed_values = load_data_dictionary(dictionary_path)
    field_names = schema_field_names(output_schema)

    # Persist loaded rule objects for reproducibility / audit.
    args.processed_dir.mkdir(parents=True, exist_ok=True)
    write_json(inclusion_rules, args.processed_dir / "INCLUSION_RULES.json")
    write_json(exclusion_rules, args.processed_dir / "EXCLUSION_RULES.json")
    write_json(output_schema, args.processed_dir / "OUTPUT_SCHEMA.json")
    write_json(allowed_values, args.processed_dir / "ALLOWED_VALUES.json")

    search_keywords = resolve_search_keywords(args.keywords)
    search_locations = args.locations or list(inclusion_rules.get("locations") or [])
    if not search_locations:
        logger.error("No search locations available from Inclusion rules or CLI.")
        return 1

    logger.info("Search keywords: %s", search_keywords)
    logger.info("Search locations: %s", search_locations)

    logger.info("Fetch mode: %s", args.fetch_mode)
    crawler = IndeedCrawler(
        raw_output_dir=args.raw_dir,
        max_pages=args.max_pages,
        use_demo_fallback=not args.no_demo_fallback,
        fetch_mode=args.fetch_mode,
        html_dir=args.html_dir,
        browser_headed=args.browser_headed,
        request_delay_seconds=args.request_delay,
    )
    raw_jobs = crawler.crawl(
        keywords=search_keywords,
        locations=search_locations,
        max_pages=args.max_pages,
        fetch_details=args.fetch_details,
        force_demo=args.demo,
        html_dir=args.html_dir,
    )
    raw_jobs = deduplicate_raw_jobs(raw_jobs)
    write_json(
        [job.model_dump() for job in raw_jobs],
        args.processed_dir / "raw_jobs_deduped.json",
    )

    filter_engine = JobFilterEngine(inclusion_rules, exclusion_rules)
    filter_results = filter_engine.filter_jobs(raw_jobs)
    write_dicts_csv(
        [item.audit for item in filter_results],
        args.processed_dir / "filter_audit_trail.csv",
    )

    included_records = []
    for index, item in enumerate(filter_results, start=1):
        if not item.decision.included:
            continue
        record = normalize_job(
            item.raw,
            record_id=f"IND{index:04d}",
            decision=item.decision,
            dataset_source="Indeed",
        )
        included_records.append(record)

    included_records = deduplicate_job_records(included_records)
    valid_records, issues = validate_records(
        included_records,
        output_schema,
        allowed_values,
    )
    if issues:
        write_dicts_csv(issues, args.processed_dir / "validation_issues.csv")

    # Re-number surviving records for a clean final ID sequence.
    for index, record in enumerate(valid_records, start=1):
        record.record_id = f"IND{index:04d}"

    final_csv = args.final_dir / "him_workforce_jobs.csv"
    write_job_records_csv(valid_records, final_csv, field_names)
    write_job_records_json(
        valid_records,
        args.final_dir / "him_workforce_jobs.json",
        field_names,
    )

    logger.info("Pipeline complete.")
    logger.info("Final dataset: %s (%d records)", final_csv, len(valid_records))
    logger.info(
        "Audit trail: %s",
        args.processed_dir / "filter_audit_trail.csv",
    )
    return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(run_pipeline(args))


if __name__ == "__main__":
    main()
