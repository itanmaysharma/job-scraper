import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from playwright.sync_api import sync_playwright


@dataclass
class Job:
    title: str
    company: str
    location: str
    posted_text: str
    posted_at: Optional[datetime]
    url: str


def env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_posted_time(text: str, reference: datetime) -> Optional[datetime]:
    if not text:
        return None

    raw = text.strip().lower()
    raw = raw.replace("posted", "").strip()

    if raw in {"just now", "now", "today"}:
        return reference
    if raw in {"yesterday"}:
        return reference - timedelta(days=1)

    m = re.search(r"(\d+)\s*(minute|min|hour|hr|day|week|month|year)s?\s*ago", raw)
    if m:
        value = int(m.group(1))
        unit = m.group(2)
        if unit in {"minute", "min"}:
            return reference - timedelta(minutes=value)
        if unit in {"hour", "hr"}:
            return reference - timedelta(hours=value)
        if unit == "day":
            return reference - timedelta(days=value)
        if unit == "week":
            return reference - timedelta(weeks=value)
        if unit == "month":
            return reference - timedelta(days=value * 30)
        if unit == "year":
            return reference - timedelta(days=value * 365)

    compact = re.search(r"(\d+)\s*([mhdw])\b", raw)
    if compact:
        value = int(compact.group(1))
        unit = compact.group(2)
        if unit == "m":
            return reference - timedelta(minutes=value)
        if unit == "h":
            return reference - timedelta(hours=value)
        if unit == "d":
            return reference - timedelta(days=value)
        if unit == "w":
            return reference - timedelta(weeks=value)

    try:
        parsed = date_parser.parse(raw, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def parse_datetime_iso(text: str) -> Optional[datetime]:
    if not text:
        return None
    try:
        parsed = date_parser.parse(text, fuzzy=True)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT,
            location TEXT,
            posted_text TEXT,
            posted_at_iso TEXT,
            url TEXT,
            discovered_at_iso TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def job_key(job: Job) -> str:
    seed = (job.url or "") + "|" + job.title + "|" + job.company + "|" + job.location
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def was_seen(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE job_key = ? LIMIT 1", (key,)).fetchone()
    return row is not None


def store_job(conn: sqlite3.Connection, key: str, job: Job, discovered_at: datetime) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO jobs (job_key, title, company, location, posted_text, posted_at_iso, url, discovered_at_iso)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            job.title,
            job.company,
            job.location,
            job.posted_text,
            job.posted_at.isoformat() if job.posted_at else None,
            job.url,
            discovered_at.isoformat(),
        ),
    )
    conn.commit()


def send_slack(webhook_url: str, lines: list[str]) -> None:
    if not webhook_url:
        return
    import json

    text = "\n".join(lines)
    data = json.dumps({"text": text}).encode("utf-8")
    req = Request(webhook_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=20) as resp:
        _ = resp.read()


def send_telegram(bot_token: str, chat_id: str, lines: list[str]) -> None:
    if not bot_token or not chat_id:
        return
    import json

    text = "\n".join(lines)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=20) as resp:
        _ = resp.read()


def notify(job: Job) -> None:
    lines = [
        "New job found on Brian's Search",
        f"Title: {job.title}",
        f"Company: {job.company or 'N/A'}",
        f"Location: {job.location or 'N/A'}",
        f"Posted: {job.posted_text or 'N/A'}",
        f"Link: {job.url or 'N/A'}",
    ]

    slack_webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    try:
        send_slack(slack_webhook, lines)
    except URLError as e:
        print(f"[warn] Slack notification failed: {e}")

    try:
        send_telegram(telegram_token, telegram_chat_id, lines)
    except URLError as e:
        print(f"[warn] Telegram notification failed: {e}")


def pick_text(card, selector: str) -> str:
    node = card.select_one(selector)
    if not node:
        return ""
    return node.get_text(" ", strip=True)


def pick_url(card, selector: str, base_url: str) -> str:
    link = card.select_one(selector)
    if not link:
        return ""
    href = link.get("href", "").strip()
    return urljoin(base_url, href)


def parse_brians_window(text: str, reference: datetime) -> Optional[datetime]:
    raw = text.strip().lower()
    mapping = {
        "past hour": timedelta(hours=1),
        "past 4 hours": timedelta(hours=4),
        "past 8 hours": timedelta(hours=8),
        "past 12 hours": timedelta(hours=12),
        "past 24 hours": timedelta(hours=24),
        "past 48 hours": timedelta(hours=48),
        "past 72 hours": timedelta(hours=72),
        "past week": timedelta(days=7),
        "past month": timedelta(days=30),
    }
    if raw in mapping:
        return reference - mapping[raw]
    return None


def parse_window_hours(text: str) -> int:
    raw = text.strip().lower()
    mapping = {
        "1hour": 1,
        "4hours": 4,
        "8hours": 8,
        "12hours": 12,
        "24hours": 24,
        "48hours": 48,
        "72hours": 72,
    }
    return mapping.get(raw, 24)


def time_window_label(text: str) -> str:
    raw = text.strip().lower()
    mapping = {
        "1hour": "Past Hour",
        "4hours": "Past 4 Hours",
        "8hours": "Past 8 Hours",
        "12hours": "Past 12 Hours",
        "24hours": "Past 24 Hours",
        "48hours": "Past 48 Hours",
        "72hours": "Past 72 Hours",
    }
    return mapping.get(raw, "Past 24 Hours")


def time_window_tbs(text: str) -> str:
    raw = text.strip().lower()
    mapping = {
        "1hour": "qdr:h",
        "4hours": "qdr:h4",
        "8hours": "qdr:h8",
        "12hours": "qdr:h12",
        "24hours": "qdr:d",
        "48hours": "qdr:d2",
        "72hours": "qdr:d3",
    }
    return mapping.get(raw, "qdr:d")


def window_key_from_text(text: str) -> str:
    raw = (text or "").strip().lower()
    mapping = {
        "past hour": "1hour",
        "past 4 hours": "4hours",
        "past 8 hours": "8hours",
        "past 12 hours": "12hours",
        "past 24 hours": "24hours",
        "past 48 hours": "48hours",
        "past 72 hours": "72hours",
        "1hour": "1hour",
        "4hours": "4hours",
        "8hours": "8hours",
        "12hours": "12hours",
        "24hours": "24hours",
        "48hours": "48hours",
        "72hours": "72hours",
    }
    return mapping.get(raw, raw)


def parse_brians_results(soup: BeautifulSoup, base_url: str) -> list[Job]:
    root = soup.select_one("#results")
    if not root:
        return []

    heading = root.select_one("h3")
    heading_text = heading.get_text(" ", strip=True) if heading else ""
    role = heading_text
    time_window = ""
    if " - " in heading_text:
        role, time_window = [part.strip() for part in heading_text.split(" - ", 1)]

    inferred_posted_at = parse_brians_window(time_window, now_utc()) if time_window else None
    jobs: list[Job] = []
    for li in root.select("li"):
        a = li.select_one("label a[href], a[href]")
        if not a:
            continue
        source_name = a.get_text(" ", strip=True)
        href = urljoin(base_url, a.get("href", "").strip())
        if not href:
            continue

        title = f"{role} ({source_name})" if role else source_name
        jobs.append(
            Job(
                title=title,
                company=source_name,
                location="Remote/Any",
                posted_text=time_window or "N/A",
                posted_at=inferred_posted_at,
                url=href,
            )
        )
    return jobs


def csv_set(name: str) -> set[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def csv_list(name: str) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def normalize_board_url(provider: str, raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return raw_url.strip()
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return raw_url.strip()
    token = parts[0]
    if provider == "greenhouse":
        return f"{parsed.scheme}://{parsed.netloc}/{token}"
    if provider == "lever":
        return f"{parsed.scheme}://{parsed.netloc}/{token}"
    if provider == "ashby":
        return f"{parsed.scheme}://{parsed.netloc}/{token}"
    return raw_url.strip()


def filter_sources(jobs: list[Job], include: set[str], exclude: set[str]) -> list[Job]:
    selected: list[Job] = []
    for job in jobs:
        key = (job.company or "").strip().lower()
        if include and key not in include:
            continue
        if exclude and key in exclude:
            continue
        selected.append(job)
    return selected


def build_global_source_links() -> list[Job]:
    role_query = os.getenv("ROLE_QUERY", "").strip()
    source_csv = os.getenv(
        "GLOBAL_BOARD_SOURCES",
        "Greenhouse,Lever,Ashby,Workday,SmartRecruiters,iCIMS,Dover,Oracle Cloud",
    )
    time_window = os.getenv("TIME_WINDOW", "24hours").strip().lower()
    time_windows_raw = os.getenv("TIME_WINDOWS", "").strip()
    remote_only = env_bool("REMOTE_ONLY", True)
    sources = [s.strip() for s in source_csv.split(",") if s.strip()]
    domains = {
        "greenhouse": "greenhouse.io",
        "lever": "lever.co",
        "ashby": "ashbyhq.com",
        "workday": "myworkdayjobs.com",
        "smartrecruiters": "jobs.smartrecruiters.com",
        "icims": "icims.com",
        "dover": "dover.io",
        "oracle cloud": "oraclecloud.com",
    }

    if time_windows_raw:
        windows = [w.strip().lower() for w in time_windows_raw.split(",") if w.strip()]
    else:
        windows = [time_window]
    # Preserve order but remove duplicates.
    windows = list(dict.fromkeys(windows))
    out: list[Job] = []

    for window in windows:
        posted_label = time_window_label(window)
        # Keep source links within lookback filter; actual posting recency is determined during deep fetch.
        posted_at = now_utc()
        tbs = time_window_tbs(window)
        for src in sources:
            key = src.lower()
            domain = domains.get(key)
            if not domain:
                continue
            if role_query:
                q = f"\"{role_query}\" site:{domain}"
            else:
                q = f"site:{domain} (jobs OR careers)"
            if remote_only:
                q += " remote"
            url = f"https://www.google.com/search?q={quote_plus(q)}&tbs={tbs}"
            out.append(
                Job(
                    title=f"{role_query or 'All Roles'} ({src})",
                    company=src,
                    location="Remote/Any" if remote_only else "Any",
                    posted_text=posted_label,
                    posted_at=posted_at,
                    url=url,
                )
            )
    return out


def extract_expected_domains(search_url: str) -> list[str]:
    parsed = urlparse(search_url)
    params = parse_qs(parsed.query)
    q_values = params.get("q", [])
    if not q_values:
        return []
    q = unquote(q_values[0])
    raw_domains = re.findall(r"site:([^\s)]+)", q)
    cleaned: list[str] = []
    for domain in raw_domains:
        d = domain.strip().lower()
        d = d.replace("*.", "")
        d = d.replace("*", "")
        d = d.strip("/")
        if d:
            cleaned.append(d)
    return cleaned


def extract_target_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/url?"):
        parsed = urlparse(href)
        q = parse_qs(parsed.query).get("q", [])
        if q:
            return q[0]
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return ""


def host_matches(url: str, expected_domains: list[str]) -> bool:
    if not expected_domains:
        return True
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    for domain in expected_domains:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def is_blocked_target(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host.endswith("google.com") or host.endswith("gstatic.com") or host.endswith("youtube.com")


def keyword_match(title: str, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    title_l = title.lower()
    tokens = [t for t in re.split(r"\W+", q) if len(t) >= 3]
    if not tokens:
        return q in title_l
    return any(tok in title_l for tok in tokens)


def extract_role_from_source_title(title: str) -> str:
    m = re.match(r"^(.*?)\s*\([^)]*\)\s*$", title.strip())
    return m.group(1).strip() if m else title.strip()


def fetch_json(url: str, timeout: int = 25) -> dict:
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def fetch_html(url: str, timeout: int = 25) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def greenhouse_token(board_url: str) -> str:
    parsed = urlparse(board_url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return ""
    # boards.greenhouse.io/<token> or job-boards.greenhouse.io/<token>
    return parts[0]


def lever_token(board_url: str) -> str:
    parsed = urlparse(board_url)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        return ""
    # jobs.lever.co/<token>
    return parts[0]


def fetch_greenhouse_jobs(role_query: str, posted_text: str, posted_at: Optional[datetime], board_urls: list[str]) -> list[Job]:
    timeout = int(os.getenv("DEEP_FETCH_TIMEOUT_SEC", "25"))
    max_total = int(os.getenv("MAX_RESULTS_PER_SOURCE", "10"))
    out: list[Job] = []
    seen: set[str] = set()

    for raw_url in board_urls:
        board_url = normalize_board_url("greenhouse", raw_url)
        token = greenhouse_token(board_url)
        if not token:
            continue
        api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        try:
            payload = fetch_json(api, timeout=timeout)
        except Exception as exc:
            print(f"[warn] greenhouse board fetch failed {board_url}: {exc}", flush=True)
            continue
        for row in payload.get("jobs", []):
            url = (row.get("absolute_url") or "").strip()
            title = (row.get("title") or "").strip()
            if not url or not title:
                continue
            if not keyword_match(title, role_query):
                continue
            if url in seen:
                continue
            seen.add(url)
            location = ((row.get("location") or {}).get("name") or "N/A").strip()
            row_posted_at = parse_datetime_iso(row.get("updated_at") or row.get("created_at")) or posted_at
            out.append(
                Job(
                    title=title,
                    company="Greenhouse",
                    location=location,
                    posted_text=posted_text,
                    posted_at=row_posted_at,
                    url=url,
                )
            )
            if len(out) >= max_total:
                return out
    return out


def fetch_lever_jobs(role_query: str, posted_text: str, posted_at: Optional[datetime], board_urls: list[str]) -> list[Job]:
    timeout = int(os.getenv("DEEP_FETCH_TIMEOUT_SEC", "25"))
    max_total = int(os.getenv("MAX_RESULTS_PER_SOURCE", "10"))
    out: list[Job] = []
    seen: set[str] = set()

    for raw_url in board_urls:
        board_url = normalize_board_url("lever", raw_url)
        token = lever_token(board_url)
        if not token:
            continue
        api = f"https://api.lever.co/v0/postings/{token}?mode=json"
        try:
            rows = fetch_json(api, timeout=timeout)
        except Exception as exc:
            print(f"[warn] lever board fetch failed {board_url}: {exc}", flush=True)
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            url = (row.get("hostedUrl") or row.get("applyUrl") or "").strip()
            title = (row.get("text") or "").strip()
            if not url or not title:
                continue
            if not keyword_match(title, role_query):
                continue
            if url in seen:
                continue
            seen.add(url)
            categories = row.get("categories") or {}
            location = (categories.get("location") or "N/A").strip()
            created_ms = row.get("createdAt")
            row_posted_at = posted_at
            if isinstance(created_ms, (int, float)) and created_ms > 0:
                row_posted_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            out.append(
                Job(
                    title=title,
                    company="Lever",
                    location=location,
                    posted_text=posted_text,
                    posted_at=row_posted_at,
                    url=url,
                )
            )
            if len(out) >= max_total:
                return out
    return out


def fetch_ashby_jobs(role_query: str, posted_text: str, posted_at: Optional[datetime], board_urls: list[str]) -> list[Job]:
    timeout = int(os.getenv("DEEP_FETCH_TIMEOUT_SEC", "25"))
    max_total = int(os.getenv("MAX_RESULTS_PER_SOURCE", "10"))
    out: list[Job] = []
    seen: set[str] = set()

    for raw_url in board_urls:
        board_url = normalize_board_url("ashby", raw_url)
        try:
            html = fetch_html(board_url, timeout=timeout)
        except Exception as exc:
            print(f"[warn] ashby board fetch failed {board_url}: {exc}", flush=True)
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href*='/job/'], a[href*='/jobs/']"):
            href = (a.get("href") or "").strip()
            url = urljoin(board_url, href)
            title = a.get_text(" ", strip=True)
            if not url or not title:
                continue
            if not keyword_match(title, role_query):
                continue
            if url in seen:
                continue
            seen.add(url)
            out.append(
                Job(
                    title=title,
                    company="Ashby",
                    location="N/A",
                    posted_text=posted_text,
                    posted_at=posted_at,
                    url=url,
                )
            )
            if len(out) >= max_total:
                return out
    return out


def fetch_provider_board_jobs(source_job: Job) -> list[Job]:
    use_provider_scrapers = env_bool("USE_PROVIDER_BOARD_SCRAPERS", True)
    if not use_provider_scrapers:
        return []

    role_query = os.getenv("ROLE_QUERY", "").strip() or extract_role_from_source_title(source_job.title)
    source = (source_job.company or "").strip().lower()
    if source == "greenhouse":
        boards = csv_list("GREENHOUSE_BOARD_URLS")
        if not boards:
            print("[warn] GREENHOUSE_BOARD_URLS is empty; skipping direct Greenhouse fetch", flush=True)
            return []
        return fetch_greenhouse_jobs(
            role_query,
            source_job.posted_text,
            source_job.posted_at,
            boards,
        )
    if source == "lever":
        boards = csv_list("LEVER_BOARD_URLS")
        if not boards:
            print("[warn] LEVER_BOARD_URLS is empty; skipping direct Lever fetch", flush=True)
            return []
        return fetch_lever_jobs(
            role_query,
            source_job.posted_text,
            source_job.posted_at,
            boards,
        )
    if source == "ashby":
        boards = csv_list("ASHBY_BOARD_URLS")
        if not boards:
            print("[warn] ASHBY_BOARD_URLS is empty; skipping direct Ashby fetch", flush=True)
            return []
        return fetch_ashby_jobs(
            role_query,
            source_job.posted_text,
            source_job.posted_at,
            boards,
        )
    return []


def fetch_page_html(url: str) -> str:
    use_playwright = env_bool("DEEP_FETCH_WITH_PLAYWRIGHT", True)
    timeout_ms = int(os.getenv("DEEP_FETCH_NAV_TIMEOUT_MS", "45000"))
    wait_after_ms = int(os.getenv("DEEP_FETCH_WAIT_AFTER_MS", "1500"))

    if use_playwright:
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=env_bool("HEADLESS", True))
                context = browser.new_context(user_agent=os.getenv("USER_AGENT", None))
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_after_ms > 0:
                    page.wait_for_timeout(wait_after_ms)
                html = page.content()
                context.close()
                browser.close()
                return html
        except Exception as exc:
            print(f"[warn] deep fetch playwright fallback for {url}: {exc}", flush=True)

    timeout = int(os.getenv("DEEP_FETCH_TIMEOUT_SEC", "25"))
    default_ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    user_agent = os.getenv("DEEP_FETCH_USER_AGENT", "").strip() or default_ua
    req = Request(url, headers={"User-Agent": user_agent})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_google_results(source_job: Job) -> list[Job]:
    max_per_source = int(os.getenv("MAX_RESULTS_PER_SOURCE", "10"))
    html = fetch_page_html(source_job.url)

    soup = BeautifulSoup(html, "html.parser")
    expected_domains = extract_expected_domains(source_job.url)
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    debug_deep_fetch = env_bool("DEBUG_DEEP_FETCH", True)

    anchors = soup.select("a:has(h3), a h3")
    # Fallback: some Google variants do not expose h3 blocks in straightforward markup.
    if not anchors:
        anchors = soup.select("a[href^='/url?'], a[href^='https://']")

    for anchor in anchors:
        a = anchor if getattr(anchor, "name", "") == "a" else anchor.parent
        if not a:
            continue
        href = (a.get("href") or "").strip()
        target = extract_target_url(href)
        if not target:
            continue
        target = urljoin(source_job.url, target)
        if is_blocked_target(target):
            continue
        if not host_matches(target, expected_domains):
            continue
        if target in seen_urls:
            continue
        seen_urls.add(target)

        h3 = a.select_one("h3")
        title = h3.get_text(" ", strip=True) if h3 else a.get_text(" ", strip=True)
        if not title:
            continue

        jobs.append(
            Job(
                title=title,
                company=source_job.company,
                location=source_job.location,
                posted_text=source_job.posted_text,
                posted_at=source_job.posted_at,
                url=target,
            )
        )
        if len(jobs) >= max_per_source:
            break

    if debug_deep_fetch:
        print(
            f"[debug] deep_fetch source={source_job.company!r} expected_domains={len(expected_domains)} found={len(jobs)}",
            flush=True,
        )

    return jobs


def fetch_google_results_serpapi(source_job: Job) -> list[Job]:
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return []

    max_per_source = int(os.getenv("MAX_RESULTS_PER_SOURCE", "10"))
    timeout = int(os.getenv("DEEP_FETCH_TIMEOUT_SEC", "25"))
    parsed = urlparse(source_job.url)
    params = parse_qs(parsed.query)
    query = params.get("q", [""])[0]
    tbs = params.get("tbs", [""])[0]

    serpapi_qs = f"engine=google&num={max_per_source}&api_key={api_key}&q={query}"
    if tbs:
        serpapi_qs += f"&tbs={tbs}"
    api_url = f"https://serpapi.com/search.json?{serpapi_qs}"
    req = Request(api_url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="ignore"))

    expected_domains = extract_expected_domains(source_job.url)
    jobs: list[Job] = []
    for row in data.get("organic_results", []):
        target = (row.get("link") or "").strip()
        title = (row.get("title") or "").strip()
        if not target or not title:
            continue
        if is_blocked_target(target):
            continue
        if not host_matches(target, expected_domains):
            continue
        jobs.append(
            Job(
                title=title,
                company=source_job.company,
                location=source_job.location,
                posted_text=source_job.posted_text,
                posted_at=source_job.posted_at,
                url=target,
            )
        )
        if len(jobs) >= max_per_source:
            break
    return jobs


def fetch_google_results_serper(source_job: Job) -> list[Job]:
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        return []

    max_per_source = int(os.getenv("MAX_RESULTS_PER_SOURCE", "10"))
    timeout = int(os.getenv("DEEP_FETCH_TIMEOUT_SEC", "25"))
    parsed = urlparse(source_job.url)
    params = parse_qs(parsed.query)
    query = params.get("q", [""])[0]
    tbs = params.get("tbs", [""])[0]

    payload = {"q": query, "num": max_per_source}
    if tbs:
        payload["tbs"] = tbs
    req = Request(
        "https://google.serper.dev/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="ignore"))

    expected_domains = extract_expected_domains(source_job.url)
    jobs: list[Job] = []
    for row in data.get("organic", []):
        target = (row.get("link") or "").strip()
        title = (row.get("title") or "").strip()
        if not target or not title:
            continue
        if is_blocked_target(target):
            continue
        if not host_matches(target, expected_domains):
            continue
        jobs.append(
            Job(
                title=title,
                company=source_job.company,
                location=source_job.location,
                posted_text=source_job.posted_text,
                posted_at=source_job.posted_at,
                url=target,
            )
        )
        if len(jobs) >= max_per_source:
            break
    return jobs


def fetch_google_results_by_provider(source_job: Job) -> list[Job]:
    order_raw = os.getenv("DEEP_FETCH_PROVIDER_ORDER", "serper,serpapi,html")
    order = [x.strip().lower() for x in order_raw.split(",") if x.strip()]
    for provider in order:
        if provider == "serper":
            jobs = fetch_google_results_serper(source_job)
        elif provider == "serpapi":
            jobs = fetch_google_results_serpapi(source_job)
        elif provider == "html":
            jobs = fetch_google_results(source_job)
        else:
            continue
        if jobs:
            print(f"[debug] deep_fetch provider={provider} source={source_job.company!r} found={len(jobs)}", flush=True)
            return jobs
    return []


def maybe_expand_source_links(source_links: list[Job]) -> list[Job]:
    deep_fetch = env_bool("DEEP_FETCH_RESULTS", True)
    keep_source_if_empty = env_bool("KEEP_SOURCE_LINK_IF_EMPTY", False)
    run_mode = os.getenv("RUN_MODE", "board_urls").strip().lower()
    if not deep_fetch:
        return source_links

    expanded: list[Job] = []
    total_found = 0
    for source in source_links:
        try:
            provider_found = [] if run_mode == "all_companies" else fetch_provider_board_jobs(source)
            if provider_found:
                found = provider_found
            elif "google.com/search" in source.url:
                found = fetch_google_results_by_provider(source)
            else:
                found = []
        except Exception as exc:
            print(f"[warn] deep fetch failed for {source.company}: {exc}", flush=True)
            found = []

        if found:
            expanded.extend(found)
            total_found += len(found)
        elif keep_source_if_empty:
            expanded.append(source)

    if total_found == 0:
        print(
            "[warn] deep fetch found 0 results. Configure SERPER_API_KEY (or SERPAPI_API_KEY) for reliable extraction.",
            flush=True,
        )

    return expanded


def build_export_rows(conn: sqlite3.Connection, lookback_hours: int) -> list[tuple]:
    cutoff = now_utc() - timedelta(hours=lookback_hours)
    only_real_urls = env_bool("SHEET_ONLY_REAL_URLS", True)
    include_sources = csv_set("SOURCE_INCLUDE")
    exclude_sources = csv_set("SOURCE_EXCLUDE")
    sheet_windows = csv_set("SHEET_POSTED_WINDOWS")
    if only_real_urls:
        rows = conn.execute(
            """
            SELECT title, company, location, posted_text, posted_at_iso, url, discovered_at_iso
            FROM jobs
            WHERE discovered_at_iso >= ?
              AND url NOT LIKE '%google.com/search%'
            ORDER BY discovered_at_iso DESC
            """,
            (cutoff.isoformat(),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT title, company, location, posted_text, posted_at_iso, url, discovered_at_iso
            FROM jobs
            WHERE discovered_at_iso >= ?
            ORDER BY discovered_at_iso DESC
            """,
            (cutoff.isoformat(),),
        ).fetchall()

    filtered_rows = []
    for row in rows:
        company_key = (row[1] or "").strip().lower()
        if include_sources and company_key not in include_sources:
            continue
        if exclude_sources and company_key in exclude_sources:
            continue
        if sheet_windows:
            row_window = window_key_from_text(row[3])
            if row_window not in sheet_windows:
                continue
        filtered_rows.append(row)

    def posted_rank(posted_text: str) -> int:
        p = (posted_text or "").strip().lower()
        mapping = {
            "past hour": 1,
            "past 4 hours": 2,
            "past 8 hours": 3,
            "past 12 hours": 4,
            "past 24 hours": 5,
            "past 48 hours": 6,
            "past 72 hours": 7,
        }
        return mapping.get(p, 99)

    # Sort by recency bucket first (Past Hour -> Past 24 Hours), then by posted/discovered timestamp desc.
    filtered_rows.sort(
        key=lambda r: (
            posted_rank(r[3]),
            -(parse_datetime_iso(r[4]).timestamp() if parse_datetime_iso(r[4]) else 0),
            -(parse_datetime_iso(r[6]).timestamp() if parse_datetime_iso(r[6]) else 0),
        )
    )
    return filtered_rows


def export_csv_sheet(rows: list[tuple], csv_path: str) -> None:
    out_dir = os.path.dirname(csv_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "company", "location", "posted_text", "posted_at_iso", "url", "discovered_at_iso"])
        writer.writerows(rows)
    print(f"[sheet-csv] exported_rows={len(rows)} path={csv_path}", flush=True)


def export_google_sheet(rows: list[tuple]) -> None:
    enabled = env_bool("EXPORT_GOOGLE_SHEETS", False)
    if not enabled:
        return

    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    sheet_name = os.getenv("GOOGLE_SHEETS_SHEET_NAME", "Jobs").strip() or "Jobs"
    creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "/data/google-service-account.json").strip()
    if not spreadsheet_id:
        print("[warn] EXPORT_GOOGLE_SHEETS=true but GOOGLE_SHEETS_SPREADSHEET_ID is empty", flush=True)
        return
    if not os.path.exists(creds_path):
        print(f"[warn] Google service account file not found: {creds_path}", flush=True)
        return

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:
        print(f"[warn] Google Sheets deps missing: {exc}", flush=True)
        return

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    values = [["title", "company", "location", "posted_text", "posted_at_iso", "url", "discovered_at_iso"]]
    values.extend([list(row) for row in rows])

    target_range = f"{sheet_name}!A1:G"
    body = {"values": values}
    service.spreadsheets().values().clear(spreadsheetId=spreadsheet_id, range=target_range).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=target_range,
        valueInputOption="RAW",
        body=body,
    ).execute()
    print(f"[sheet-gs] exported_rows={len(rows)} spreadsheet_id={spreadsheet_id} sheet={sheet_name}", flush=True)


def scrape_once(search_url: str) -> Iterable[Job]:
    headless = env_bool("HEADLESS", True)
    timeout_ms = int(os.getenv("NAV_TIMEOUT_MS", "60000"))
    wait_selector = os.getenv("WAIT_FOR_SELECTOR", "body")
    wait_after_load_ms = int(os.getenv("WAIT_AFTER_LOAD_MS", "0"))
    debug_save_html = env_bool("DEBUG_SAVE_HTML", False)
    debug_html_path = os.getenv("DEBUG_HTML_PATH", "/data/last_page.html")
    debug_log_selectors = env_bool("DEBUG_LOG_SELECTORS", False)
    use_brians_results_parser = env_bool("USE_BRIANS_RESULTS_PARSER", True)

    card_selector = os.getenv("JOB_CARD_SELECTOR", "article, li, div.job-card, div[data-job-id]")
    title_selector = os.getenv("TITLE_SELECTOR", "h2, h3, a")
    company_selector = os.getenv("COMPANY_SELECTOR", ".company, [data-testid*='company']")
    location_selector = os.getenv("LOCATION_SELECTOR", ".location, [data-testid*='location']")
    posted_selector = os.getenv("POSTED_SELECTOR", "time, .posted, .date")
    link_selector = os.getenv("LINK_SELECTOR", "a[href]")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=os.getenv("USER_AGENT", None))
        page = context.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
        page.wait_for_selector(wait_selector, timeout=timeout_ms)
        if wait_after_load_ms > 0:
            page.wait_for_timeout(wait_after_load_ms)
        html = page.content()
        context.close()
        browser.close()

    if debug_save_html:
        out_dir = os.path.dirname(debug_html_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(debug_html_path, "w", encoding="utf-8") as f:
            f.write(html)

    soup = BeautifulSoup(html, "html.parser")
    if use_brians_results_parser:
        brian_jobs = parse_brians_results(soup, search_url)
        if brian_jobs:
            if debug_log_selectors:
                print(f"[debug] brians_parser=true links={len(brian_jobs)}", flush=True)
            return brian_jobs

    cards = soup.select(card_selector)

    if debug_log_selectors:
        print(
            "[debug] selector counts "
            f"cards={len(cards)} "
            f"title={len(soup.select(title_selector))} "
            f"company={len(soup.select(company_selector))} "
            f"location={len(soup.select(location_selector))} "
            f"posted={len(soup.select(posted_selector))} "
            f"links={len(soup.select(link_selector))}",
            flush=True,
        )

    jobs: list[Job] = []
    for card in cards:
        title = pick_text(card, title_selector)
        company = pick_text(card, company_selector)
        location = pick_text(card, location_selector)
        posted_text = pick_text(card, posted_selector)
        url = pick_url(card, link_selector, search_url)

        if not title and not url:
            continue

        posted_at = parse_posted_time(posted_text, now_utc())
        jobs.append(
            Job(
                title=title or "Untitled role",
                company=company,
                location=location,
                posted_text=posted_text,
                posted_at=posted_at,
                url=url,
            )
        )

    return jobs


def should_keep(job: Job, lookback_hours: int, include_unparseable: bool) -> bool:
    if job.posted_at is None:
        return include_unparseable
    return job.posted_at >= now_utc() - timedelta(hours=lookback_hours)


def run_once() -> None:
    run_mode = os.getenv("RUN_MODE", "board_urls").strip().lower()
    search_url = os.getenv("SEARCH_URL", "")
    db_path = os.getenv("DB_PATH", "/data/jobs.db")
    lookback_hours = int(os.getenv("LOOKBACK_HOURS", "24"))
    include_unparseable = env_bool("INCLUDE_UNPARSEABLE", True)
    export_csv = env_bool("EXPORT_CSV", True)
    export_csv_path = os.getenv("EXPORT_CSV_PATH", "/data/jobs_sheet.csv")
    export_lookback_hours = int(os.getenv("EXPORT_LOOKBACK_HOURS", str(lookback_hours)))
    include_sources = csv_set("SOURCE_INCLUDE")
    exclude_sources = csv_set("SOURCE_EXCLUDE")

    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = init_db(db_path)

    discovered = now_utc()
    if run_mode == "all_companies":
        jobs = build_global_source_links()
    else:
        if not search_url:
            raise RuntimeError("SEARCH_URL is required when RUN_MODE=board_urls")
        jobs = list(scrape_once(search_url))
    kept = [job for job in jobs if should_keep(job, lookback_hours, include_unparseable)]
    source_filtered = filter_sources(kept, include_sources, exclude_sources)
    expanded = maybe_expand_source_links(source_filtered)

    new_count = 0
    for job in expanded:
        key = job_key(job)
        if was_seen(conn, key):
            continue
        store_job(conn, key, job, discovered)
        notify(job)
        new_count += 1

    print(
        f"[{discovered.isoformat()}] scanned={len(jobs)} source_filtered={len(source_filtered)} expanded={len(expanded)} new={new_count} db={db_path}",
        flush=True,
    )
    preview_count = int(os.getenv("LOG_NEW_PREVIEW_COUNT", "3"))
    if preview_count > 0:
        for j in expanded[:preview_count]:
            print(
                f"[preview] title={j.title!r} company={j.company!r} posted={j.posted_text!r} url={j.url!r}",
                flush=True,
            )
    export_rows = build_export_rows(conn, export_lookback_hours)
    if export_csv:
        export_csv_sheet(export_rows, export_csv_path)
    export_google_sheet(export_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Brian's Search job watcher")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    args = parser.parse_args()

    if not args.loop:
        run_once()
        return 0

    poll_minutes = int(os.getenv("POLL_MINUTES", "15"))
    if poll_minutes < 1:
        raise RuntimeError("POLL_MINUTES must be >= 1")

    while True:
        try:
            run_once()
        except Exception as exc:
            print(f"[error] scrape failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(poll_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
