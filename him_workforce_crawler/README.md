# HIM Workforce Crawler (Phase 1: Indeed)

Research-grade Python pipeline for collecting, filtering, normalizing, and exporting Health Informatics Management (HIM) workforce job postings.

Phase 1 implements the foundation plus an Indeed crawler. Additional sources (LinkedIn, hospital career pages, etc.) can be added by subclassing `BaseCrawler` without rewriting the core pipeline.

---

## Input file inspection (foundation)

The pipeline is driven exclusively by three input files in `data/input/`.

### 1. `Inclusion.csv`

**Structure observed**

| Region | Content |
|---|---|
| Row 1 | Section header: `Location` |
| Rows 2–6 | Location values: Louisiana, Arkansas, Mississippi, Texas, Remote |
| Row 7+ | Empty rows, then an embedded copy of the HIM Data Dictionary |

**Loader behavior**

- `load_inclusion_rules()` reads the file dynamically as a sectioned rule sheet.
- Values under `Location` become `INCLUSION_RULES["locations"]`.
- Optional future sections (`Keywords`, `Title Keywords`, `Description Keywords`) are supported.
- Embedded dictionary rows are detected and ignored (they are not inclusion criteria).

Example runtime object:

```json
{
  "locations": ["Louisiana", "Arkansas", "Mississippi", "Texas", "Remote"]
}
```

### 2. `Exclusion.csv`

**Structure observed**

A single-column free-text rule list:

- `Nursing`
- `VP`
- `Over 7 years experience required`
- `Salary over $150,000 annually`
- `Senior`
- `RN`
- `Nurse`

**Loader behavior**

- `load_exclusion_rules()` classifies each row by pattern into typed rule buckets.
- No hardcoded exclusion if-statements in the filter engine.
- Produces `EXCLUSION_RULES` such as:

```json
{
  "title_keywords": ["Nursing", "RN", "Nurse"],
  "career_level_keywords": ["VP", "Senior"],
  "experience_rules": {"maximum_years": 7},
  "salary_rules": {"maximum_annual": 150000}
}
```

### 3. `HIM_Data_Dictionary.csv`

**Structure observed**

- Title / provenance rows at the top
- Header row containing: Variable Name, Label, Definition, Data Type, Allowed Values / Examples, Applies To, Source Sheet / Column, Notes for Coding
- One row per standardized output field (`record_id`, `job_title`, `employer`, …)

**Loader behavior**

- `load_data_dictionary()` builds:
  - `OUTPUT_SCHEMA`: field → metadata
  - `ALLOWED_VALUES`: field → parsed allowed/example values
- Final CSV columns are emitted strictly in dictionary field order.

---

## Implementation plan

1. **Configuration layer** — dynamically load inclusion, exclusion, and schema artifacts (CSV/Excel).
2. **Data model** — Pydantic `JobRecord` aligned to dictionary fields only.
3. **Crawler layer** — `BaseCrawler` contract + `IndeedCrawler` implementation.
4. **Processing** — rule-based filter engine, normalization, validation, deduplication.
5. **Storage** — raw JSON/HTML, audit CSV, final dictionary-aligned CSV.
6. **Tests + reproducibility** — unit tests for loaders/filters/normalization; audit trail for every include/exclude decision.

---

## Architecture

```text
him_workforce_crawler/
├── main.py
├── requirements.txt
├── README.md
├── config/                 # dynamic rule + schema loaders
├── crawlers/               # BaseCrawler + IndeedCrawler
├── models/                 # JobRecord / RawJobPosting
├── processing/             # filter, normalize, validate, dedupe
├── storage/                # CSV / JSON writers
├── utils/                  # logging
├── tests/
└── data/
    ├── input/              # Inclusion, Exclusion, Data Dictionary
    ├── raw/                # raw HTML/JSON crawl payloads
    ├── processed/          # rules snapshots + audit trail
    └── final/              # him_workforce_jobs.csv
```

---

## Setup

```bash
cd him_workforce_crawler

python -m venv .venv

.venv\Scripts\activate

pip install -r requirements.txt

copy .env.example .env   # optional keyword overrides
```

Python 3.11+ required.

---

## Run the pipeline

### Recommended first run (transparent demo mode)

Indeed frequently blocks automated clients. For a reproducible first milestone run:

```bash
python main.py --demo
```

This uses a clearly labeled demo/fallback Indeed dataset, then applies the real inclusion/exclusion/normalization pipeline and writes:

- `data/final/him_workforce_jobs.csv`
- `data/processed/filter_audit_trail.csv`
- `data/processed/INCLUSION_RULES.json`
- `data/processed/EXCLUSION_RULES.json`
- `data/processed/OUTPUT_SCHEMA.json`

### Live Indeed crawl (often blocked)

```bash
python main.py --max-pages 1
```

Indeed commonly returns **Permission Denied / 403 / CAPTCHA** to plain HTTP clients (`requests`). That is expected anti-bot behavior, not a bug in this pipeline.

### Handling Indeed anti-bot protections

Use one of these research-safe modes (in recommended order):

#### 1) Offline HTML ingest (most reliable for academic work)

1. Open Indeed in your normal browser and run your search.
2. Save the results page as HTML into `data/raw/manual_indeed/`.
3. Run:

```bash
python main.py --fetch-mode offline --html-dir data/raw/manual_indeed
```

See `data/raw/manual_indeed/README.md`.

#### 2) Playwright browser fetch

```bash
pip install playwright
playwright install chromium

# Headless attempt
python main.py --fetch-mode playwright --max-pages 1 --request-delay 5

# If challenged, use a visible browser window
python main.py --fetch-mode playwright --browser-headed --max-pages 1
```

#### 3) Demo fallback (pipeline testing only)

```bash
python main.py --demo
```

If a live crawl is blocked/empty, the crawler falls back to the labeled demo dataset unless `--no-demo-fallback` is set.

**Out of scope on purpose:** CAPTCHA-solving services, residential-proxy rotation, and fingerprint spoofing. Those are fragile, often violate site terms, and reduce research transparency. Prefer offline ingest or official/partner data sources when automation is blocked.

### Useful options

```bash
python main.py --keywords "Health Information Management" "RHIA" --locations Louisiana Remote
python main.py --fetch-details
python main.py --demo
python main.py --fetch-mode offline --html-dir data/raw/manual_indeed
python main.py --fetch-mode playwright --browser-headed
```

Search keywords are **query parameters for Indeed**, not hardcoded inclusion criteria. Inclusion/exclusion business rules always come from the input spreadsheets.

---

## Filtering transparency

For every collected job, the audit trail records:

- source URL
- source platform
- include/exclude decision
- stage (`inclusion:location`, `exclusion:title_keywords`, etc.)
- human-readable reasons

Accepted records also retain filter provenance in the dictionary `notes` field.

---

## Tests

```bash
cd him_workforce_crawler
pytest -q
```

---

## Extending to new sources

1. Create `crawlers/<source>_crawler.py` subclassing `BaseCrawler`.
2. Implement `fetch_page`, `parse_jobs`, `extract_job_details`, `crawl`.
3. Register/call it from `main.py`.

No changes are required to the rule engine, schema loader, or final CSV writer.
