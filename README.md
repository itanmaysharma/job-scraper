# job-scraper

Dockerized job discovery and aggregation tool for collecting recent openings across multiple job board ecosystems, with CSV export and optional Google Sheets sync.

For quick onboarding and sharing, see [SETUP.md](/Users/sharma/Github/agent-for-job/SETUP.md).

## Overview

`job-scraper` supports two primary collection modes:

- `all_companies`: global discovery using search providers across selected board domains
- `board_urls`: direct fetch from specific company board URLs (Greenhouse/Lever/Ashby)

The pipeline deduplicates records in SQLite, applies export filters, and writes a clean sheet-style CSV output.

## Features

- Multi-board discovery (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS, Dover, Oracle Cloud)
- Role-based query filtering
- Single-window or multi-window collection (`1h`, `4h`, `8h`, `12h`, `24h`)
- Provider chain for deep-fetch (`serper -> serpapi -> html`)
- SQLite persistence and dedupe
- CSV export with ordering and optional time-bucket filtering
- Optional Google Sheets export
- Interactive terminal runner (`make manual-run`)

## Quick Start

1. Create local config:

```bash
cp .env.example .env
```

2. Update `.env` with at least:

- `SERPER_API_KEY=<your-key>`
- `RUN_MODE=all_companies`
- `ROLE_QUERY=Software Engineer` (or your target role)
- `TIME_WINDOWS=1hour,4hours,8hours,12hours,24hours` (or use single `TIME_WINDOW`)

3. Run a one-off collection:

```bash
make first-run
```

4. Preview exported sheet:

```bash
make view-sheet
```

## Makefile Commands

```bash
make init        # create .env from template (non-destructive)
make first-run   # one-off run (build + execute)
make up          # start loop mode in background
make logs        # stream container logs
make down        # stop services
make view-data   # inspect SQLite data summary
make view-sheet  # preview CSV output
make manual-run  # interactive role/time runner
```

## Configuration

### Core Runtime

- `RUN_MODE=all_companies|board_urls`
- `ROLE_QUERY=<keyword or blank>`
- `POLL_MINUTES=15`
- `LOOKBACK_HOURS=24`

### Time Window Controls

- `TIME_WINDOW=24hours` for single-window mode
- `TIME_WINDOWS=1hour,4hours,8hours,12hours,24hours` for multi-window sweep

### Source Selection

- `GLOBAL_BOARD_SOURCES=Greenhouse,Lever,Ashby,...`
- `SOURCE_INCLUDE=...` (allowlist)
- `SOURCE_EXCLUDE=...` (blocklist)

### Direct Board Mode (`board_urls`)

- `GREENHOUSE_BOARD_URLS=...`
- `LEVER_BOARD_URLS=...`
- `ASHBY_BOARD_URLS=...`
- `USE_PROVIDER_BOARD_SCRAPERS=true`

### Deep Fetch Providers

- `DEEP_FETCH_PROVIDER_ORDER=serper,serpapi,html`
- `SERPER_API_KEY=` (recommended)
- `SERPAPI_API_KEY=` (optional fallback)
- `MAX_RESULTS_PER_SOURCE=10`

### CSV Export

- `EXPORT_CSV=true`
- `EXPORT_CSV_PATH=/data/jobs_sheet.csv`
- `EXPORT_LOOKBACK_HOURS=24`
- `SHEET_ONLY_REAL_URLS=true`
- `SHEET_POSTED_WINDOWS=` (optional, e.g. `1hour` or `1hour,4hours`)

### Google Sheets Export (Optional)

- `EXPORT_GOOGLE_SHEETS=true`
- `GOOGLE_SHEETS_SPREADSHEET_ID=<sheet-id>`
- `GOOGLE_SHEETS_SHEET_NAME=Jobs`
- `GOOGLE_SERVICE_ACCOUNT_JSON_PATH=/data/google-service-account.json`

When enabled, each run refreshes `A1:G` in the target sheet.

## Data Outputs

- SQLite database: `/Users/sharma/Github/agent-for-job/data/jobs.db`
- CSV output: `/Users/sharma/Github/agent-for-job/data/jobs_sheet.csv`
- Debug page dump (optional): `/Users/sharma/Github/agent-for-job/data/last_page.html`

## Notes

- Keep `.env` private; never commit API keys.
- `.gitignore` excludes secrets and runtime artifacts.
- Final application submission remains manual by design.
