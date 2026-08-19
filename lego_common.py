"""Shared helpers for the LEGO intelligence agent scripts.

All three agents (retirement, new-sets, release-calendar) follow the same
pattern: scrape a source with requests + BeautifulSoup, diff the result
against a saved JSON snapshot in data/, and log what changed. This module
holds the bits that would otherwise be copy-pasted three times.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, date
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 20
REQUEST_DELAY_SECONDS = 1.5
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 3


def fetch(url: str, *, timeout: int = REQUEST_TIMEOUT) -> requests.Response | None:
    """GET a URL with browser-like headers. Returns None if every attempt
    fails (network error, non-200, or a Cloudflare JS-challenge page)
    instead of raising, so callers can treat a source as best-effort.
    Retries transient failures once after a short backoff before giving up
    — across a run this may fetch hundreds of pages, so an occasional
    single-request blip shouldn't be treated the same as the source being
    genuinely down. Sleeps briefly before each attempt to avoid hammering
    sites when paginating."""
    last_reason = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_reason = f"request failed: {exc}"
        else:
            if resp.status_code != 200:
                last_reason = f"returned HTTP {resp.status_code}"
            elif "Just a moment" in resp.text[:2000] or "cf-chl-opt" in resp.text[:4000]:
                last_reason = "served a Cloudflare challenge page instead of content"
            else:
                return resp

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS)

    print(f"  ! {url} {last_reason} (after {RETRY_ATTEMPTS} attempts)")
    return None


def fetch_via_curl(url: str, *, timeout: int = REQUEST_TIMEOUT) -> str | None:
    """Like fetch(), but shells out to curl instead of using requests.

    lego.com fingerprints the TLS handshake and blocks Python's requests/
    urllib3 with a 403 even when the headers are identical to a browser's —
    but plain curl gets through fine. This is a pragmatic workaround for
    that one site rather than a general-purpose fetcher; prefer fetch()
    unless a source specifically needs it. Returns response body text, or
    None if every attempt fails (network error, non-200, curl missing).
    Retries transient failures once after a short backoff — a calendar
    crawl alone makes 300+ of these requests, so an occasional single-page
    blip shouldn't be treated the same as the source being genuinely down."""
    status_marker = "__STATUS__:"
    last_reason = None

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-L", "--max-time", str(timeout),
                    "-A", HEADERS["User-Agent"],
                    "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
                    "-w", f"\n{status_marker}%{{http_code}}",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 5,
            )
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
            last_reason = f"curl fetch failed: {exc}"
        else:
            idx = result.stdout.rfind(f"\n{status_marker}")
            if idx == -1:
                last_reason = "returned unexpected output"
            else:
                body = result.stdout[:idx]
                status_code = result.stdout[idx + len(status_marker) + 1:].strip()
                if status_code != "200":
                    last_reason = f"returned HTTP {status_code} (via curl)"
                else:
                    return body

        if attempt < RETRY_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS)

    print(f"  ! {url} {last_reason} (after {RETRY_ATTEMPTS} attempts)")
    return None


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, obj) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True, ensure_ascii=False)


def append_log(path: Path, entries: list[dict]) -> None:
    """Append change entries to a JSON list file, creating it if needed."""
    if not entries:
        return
    log = load_json(path, [])
    log.extend(entries)
    save_json(path, log)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> date:
    return date.today()


def is_placeholder_name(name: str) -> bool:
    """True for Brickset's reserved-but-unannounced set entries, whose name
    is a literal '{?}' rather than a real set name."""
    stripped = name.strip()
    return stripped in ("{?}", "?", "") or (stripped.startswith("{") and stripped.endswith("}"))


_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.IGNORECASE)

_MONTH_DAY_YEAR_FORMATS = ("%d %b %Y", "%b %d, %Y", "%B %d, %Y")


def parse_flexible_date(raw: str) -> date | None:
    """Parse dates like '31st Dec 2026', 'Aug 1, 2026', 'December 31, 2026'.
    Returns None if the text isn't a recognizable date (e.g. 'TBA')."""
    if not raw:
        return None
    cleaned = _ORDINAL_RE.sub(r"\1", raw).strip()
    for fmt in _MONTH_DAY_YEAR_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None
