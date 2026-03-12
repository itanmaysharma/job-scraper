# Setup Guide

This guide helps a new user run `job-scraper` quickly and safely.

## Prerequisites

- Docker Desktop installed and running
- A Serper API key from [serper.dev](https://serper.dev)

## 1) Clone and initialize

```bash
git clone <repo-url>
cd <repo-folder>
cp .env.example .env
```

## 2) Configure `.env`

Set at least the following values:

- `SERPER_API_KEY=<your-key>`
- `RUN_MODE=all_companies`
- `ROLE_QUERY=Software Engineer` (or your target role)
- `TIME_WINDOWS=1hour,4hours,8hours,12hours,24hours` (or use a single `TIME_WINDOW`)
- `GLOBAL_BOARD_SOURCES=Greenhouse,Lever,Ashby,Workday,SmartRecruiters,iCIMS,Dover,Oracle Cloud`
- `SOURCE_INCLUDE=Ashby,Greenhouse,Lever,Workday,SmartRecruiters,iCIMS,Dover,Oracle Cloud`

## 3) Verify with a one-off run

```bash
make first-run
make view-sheet
```

Expected output file:

- `data/jobs_sheet.csv`

## 4) Run continuously

```bash
make up
make logs
```

Stop services:

```bash
make down
```

## 5) Interactive run (optional)

For ad-hoc role/time selection from terminal:

```bash
make manual-run
```

Note: `manual-run` temporarily overrides role/time settings for that run only.

## Security Notes

- Never commit `.env` (contains API keys).
- `.gitignore` excludes secrets and runtime artifacts by default.
