# Setup Guide (Share With Friends)

This guide gets the project running in a few minutes.

## 1) Prerequisites

- Docker Desktop installed and running
- A Serper API key ([serper.dev](https://serper.dev))

## 2) Clone and configure

```bash
git clone <your-repo-url>
cd agent-for-job
cp .env.example .env
```

Edit `.env` and set at least:

- `SERPER_API_KEY=<your-key>`
- `RUN_MODE=all_companies`
- `ROLE_QUERY=Software Engineer` (or your role)
- `TIME_WINDOWS=1hour,4hours,8hours,12hours,24hours` (or a single `TIME_WINDOW`)
- `GLOBAL_BOARD_SOURCES=Greenhouse,Lever,Ashby,Workday,SmartRecruiters,iCIMS,Dover,Oracle Cloud`
- `SOURCE_INCLUDE=Ashby,Greenhouse,Lever,Workday,SmartRecruiters,iCIMS,Dover,Oracle Cloud`

## 3) Run once (test)

```bash
make first-run
make view-sheet
```

You should see CSV output at:

- `data/jobs_sheet.csv`

## 4) Run continuously

```bash
make up
make logs
```

Stop:

```bash
make down
```

## 5) Interactive manual mode

Use prompts to choose role and single/all time windows:

```bash
make manual-run
```

## Notes

- Do not commit `.env` (it contains keys).
- `.gitignore` already excludes secrets and generated data files.
