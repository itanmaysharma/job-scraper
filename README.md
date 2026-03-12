# Brian's Search Job Watcher (Option 4 MVP)

Dockerized Playwright watcher for recent jobs (<= 24 hours), with dedupe in SQLite and alert hooks for Slack/Telegram.

For quick onboarding/share instructions, see [SETUP.md](/Users/sharma/Github/agent-for-job/SETUP.md).

## What it does

- Opens your Brian's Search URL on a schedule
- Scrapes job cards from page HTML
- Filters to recent jobs (default 24h)
- Deduplicates already-seen jobs in SQLite
- Sends alerts for new jobs
- For Brian's site, it parses `#results` query links and resolves them into real posting URLs

## Quick start

1. Copy env file and update values:

```bash
cp .env.example .env
```

2. Set at least `SEARCH_URL` in `.env`.
   Optional: set `SOURCE_INCLUDE=Ashby,Greenhouse,Lever` to crawl only selected sources.

3. Run one-off calibration first:

```bash
docker compose run --rm brians-job-watcher python /app/main.py
```

4. Check output and debug HTML:

- Look for `[debug] selector counts ...` in logs
- Open `/Users/sharma/Github/agent-for-job/data/last_page.html` and verify selectors if needed
- Tune selector env vars in `.env` (`JOB_CARD_SELECTOR`, `TITLE_SELECTOR`, etc.)

5. Start the watcher loop:

```bash
docker compose up -d --build
```

6. View logs:

```bash
docker compose logs -f
```

7. Stop:

```bash
docker compose down
```

## Makefile shortcuts

```bash
make init       # create .env from template (non-destructive)
make first-run  # one-off calibration run
make up         # start loop mode in background
make logs       # stream logs
make down       # stop services
make view-data  # show total + latest rows from SQLite
make view-sheet # preview exported CSV sheet
make manual-run # interactive prompt for role + time mode
```

## One-off run (debug)

```bash
docker compose run --rm brians-job-watcher python /app/main.py
```

## First-run checklist

- `scanned` should be greater than `0`
- `source_filtered` should match the sources you included/excluded
- `expanded` should be greater than `0` when deep fetch finds actual posting URLs
- `new` should be greater than `0` on first successful run
- `./data/jobs.db` should exist after the run
- On second run, `new` should drop close to `0` (dedupe works)

## Selector tuning

Default selectors are generic and may need adjustment for Brian's current DOM.

If `USE_BRIANS_RESULTS_PARSER=true` (default), Brian's `#results` is parsed directly and selector tuning is usually not needed.

## Source controls

Use these in `.env`:

- `RUN_MODE=board_urls` or `RUN_MODE=all_companies`
- `SOURCE_INCLUDE=Ashby,Greenhouse,Lever`
- `SOURCE_EXCLUDE=LinkedIn,Glassdoor`
- `USE_PROVIDER_BOARD_SCRAPERS=true`
- `GREENHOUSE_BOARD_URLS=https://boards.greenhouse.io/company1,https://boards.greenhouse.io/company2`
- `LEVER_BOARD_URLS=https://jobs.lever.co/company1,https://jobs.lever.co/company2`
- `ASHBY_BOARD_URLS=https://jobs.ashbyhq.com/company1,https://jobs.ashbyhq.com/company2`
- `ROLE_QUERY=Software Engineer` (role filter override)

`SOURCE_INCLUDE` takes precedence by limiting to only those sources.

When board URLs are configured, the watcher fetches real job posting URLs directly from those ATS boards (free path, no API key needed).

For global discovery across all companies:

- `RUN_MODE=all_companies`
- `GLOBAL_BOARD_SOURCES=Greenhouse,Lever,Ashby`
- `TIME_WINDOW=1hour|4hours|8hours|12hours|24hours`
- `TIME_WINDOWS=1hour,4hours,8hours,12hours,24hours` (multi-window sweep in one run)
- `ROLE_QUERY=Software Engineer` (or leave blank for broad query)
- `REMOTE_ONLY=true|false`

## Deep fetch controls

- `DEEP_FETCH_RESULTS=true`: open source links and extract real result URLs
- `MAX_RESULTS_PER_SOURCE=10`: cap results per source
- `DEEP_FETCH_TIMEOUT_SEC=25`: HTTP timeout for deep fetch requests
- `KEEP_SOURCE_LINK_IF_EMPTY=false`: keep source URL if no deep results were found
- `DEEP_FETCH_WITH_PLAYWRIGHT=true`: use browser fetch first for better Google result extraction
- `DEEP_FETCH_PROVIDER_ORDER=serper,serpapi,html`
- `SERPER_API_KEY=`: optional; preferred provider for reliable extraction
- `SERPAPI_API_KEY=`: optional secondary provider

Recommended order:

1. Configure board URLs for your selected sources (free, direct scraping)
2. Keep Google deep-fetch as fallback
3. Add `SERPER_API_KEY` (recommended) or `SERPAPI_API_KEY` if needed

## CSV sheet export

Each run can export a CSV sheet:

- `EXPORT_CSV=true`
- `EXPORT_CSV_PATH=/data/jobs_sheet.csv`
- `EXPORT_LOOKBACK_HOURS=24`
- `SHEET_ONLY_REAL_URLS=true` (exclude Google query links from export)
- `SHEET_POSTED_WINDOWS=` (optional, e.g. `1hour` or `1hour,4hours`; filters exported buckets)

Open the file at `/Users/sharma/Github/agent-for-job/data/jobs_sheet.csv`.

## Google Sheets export (real-time)

1. Create a Google Cloud service account and download its JSON key.
2. Save the JSON at `/Users/sharma/Github/agent-for-job/data/google-service-account.json`.
3. Share your target Google Sheet with the service account email as Editor.
4. Set in `.env`:
   - `EXPORT_GOOGLE_SHEETS=true`
   - `GOOGLE_SHEETS_SPREADSHEET_ID=<your-sheet-id>`
   - `GOOGLE_SHEETS_SHEET_NAME=Jobs`
   - `GOOGLE_SERVICE_ACCOUNT_JSON_PATH=/data/google-service-account.json`

On each run, the script clears and rewrites `Jobs!A1:G` with the latest rows.

- `JOB_CARD_SELECTOR`
- `TITLE_SELECTOR`
- `COMPANY_SELECTOR`
- `LOCATION_SELECTOR`
- `POSTED_SELECTOR`
- `LINK_SELECTOR`

Update `.env`, then restart:

```bash
docker compose up -d --force-recreate
```

## Data

SQLite file is persisted at `./data/jobs.db`.

## Notes

- Final application submission is intentionally manual.
- Respect website terms of use and rate-limit responsibly.
- Brian's page lists source links; deep fetch resolves those into actual posting URLs when available.
