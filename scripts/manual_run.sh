#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "Manual run setup"
echo ""

read -r -p "Role keyword (blank = broad/all roles): " ROLE_QUERY_INPUT
ROLE_QUERY="${ROLE_QUERY_INPUT:-}"

echo ""
echo "Choose time mode:"
echo "  1) Single window"
echo "  2) All windows (1h,4h,8h,12h,24h)"
read -r -p "Enter 1 or 2 [default 2]: " MODE_INPUT
MODE_INPUT="${MODE_INPUT:-2}"

TIME_WINDOW="24hours"
TIME_WINDOWS=""
SHEET_POSTED_WINDOWS=""
EXPORT_LOOKBACK_HOURS="24"

if [[ "$MODE_INPUT" == "1" ]]; then
  echo ""
  echo "Select single time window:"
  echo "  1) 1hour"
  echo "  2) 4hours"
  echo "  3) 8hours"
  echo "  4) 12hours"
  echo "  5) 24hours"
  read -r -p "Enter 1-5 [default 5]: " WIN_INPUT
  WIN_INPUT="${WIN_INPUT:-5}"
  case "$WIN_INPUT" in
    1) TIME_WINDOW="1hour" ;;
    2) TIME_WINDOW="4hours" ;;
    3) TIME_WINDOW="8hours" ;;
    4) TIME_WINDOW="12hours" ;;
    5) TIME_WINDOW="24hours" ;;
    *) TIME_WINDOW="24hours" ;;
  esac
  TIME_WINDOWS=""
  SHEET_POSTED_WINDOWS="$TIME_WINDOW"
  case "$TIME_WINDOW" in
    1hour) EXPORT_LOOKBACK_HOURS="1" ;;
    4hours) EXPORT_LOOKBACK_HOURS="4" ;;
    8hours) EXPORT_LOOKBACK_HOURS="8" ;;
    12hours) EXPORT_LOOKBACK_HOURS="12" ;;
    24hours) EXPORT_LOOKBACK_HOURS="24" ;;
    *) EXPORT_LOOKBACK_HOURS="24" ;;
  esac
else
  TIME_WINDOW="24hours"
  TIME_WINDOWS="1hour,4hours,8hours,12hours,24hours"
  SHEET_POSTED_WINDOWS="$TIME_WINDOWS"
  EXPORT_LOOKBACK_HOURS="24"
fi

echo ""
echo "Running with:"
echo "  ROLE_QUERY=${ROLE_QUERY:-<blank>}"
echo "  TIME_WINDOW=${TIME_WINDOW}"
echo "  TIME_WINDOWS=${TIME_WINDOWS:-<empty>}"
echo "  SHEET_POSTED_WINDOWS=${SHEET_POSTED_WINDOWS:-<empty>}"
echo ""

RUN_MODE=all_companies \
docker compose run --rm --build \
  -e RUN_MODE=all_companies \
  -e ROLE_QUERY="$ROLE_QUERY" \
  -e TIME_WINDOW="$TIME_WINDOW" \
  -e TIME_WINDOWS="$TIME_WINDOWS" \
  -e SHEET_POSTED_WINDOWS="$SHEET_POSTED_WINDOWS" \
  -e EXPORT_LOOKBACK_HOURS="$EXPORT_LOOKBACK_HOURS" \
  brians-job-watcher python /app/main.py

echo ""
echo "Done. Latest CSV: $ROOT_DIR/data/jobs_sheet.csv"
