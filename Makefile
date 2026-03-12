.PHONY: init first-run up logs down rebuild view-data view-sheet manual-run

init:
	cp -n .env.example .env || true

first-run:
	docker compose run --rm --build brians-job-watcher python /app/main.py

up:
	docker compose up -d --build

logs:
	docker compose logs -f

down:
	docker compose down

rebuild:
	docker compose up -d --build --force-recreate

view-data:
	@echo "Total jobs:"
	@sqlite3 data/jobs.db "SELECT COUNT(*) FROM jobs;"
	@echo ""
	@echo "Latest 20 jobs:"
	@sqlite3 -header -column data/jobs.db "SELECT title, company, posted_text, substr(url,1,120) AS url, discovered_at_iso FROM jobs ORDER BY discovered_at_iso DESC LIMIT 20;"

view-sheet:
	@echo "CSV path: data/jobs_sheet.csv"
	@echo ""
	@head -n 25 data/jobs_sheet.csv

manual-run:
	@bash scripts/manual_run.sh
