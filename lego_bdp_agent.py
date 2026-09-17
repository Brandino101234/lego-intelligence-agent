"""Tracks the BrickLink Designer Program (BDP): fan-designed sets that
reached "Finalist" status and are working their way toward production as
real, officially-sold LEGO sets.

BDP runs in numbered "Series" (batches), each moving through its own
pipeline: Intake -> Crowdsourcing -> Validation -> Review -> Designs
Announced -> Refining -> Crowdfunding -> Production -> Closed. A series'
own page (bricklink.com/v3/designer-program/series-N/main.page) embeds two
useful JSON-RPC responses server-side:
  - "listSeries": every series that exists, with its current `phase` and
    key pipeline dates — present on every series page, not just the one
    requested.
  - "submissions_finalists": the individual finalist designs for THAT
    series specifically.

Every finalist we've checked keeps typeStage: "FINALIST" even on series
that have already closed — there's no separate "winner"/"achieved" flag
that narrows the list further. Confirmed against community coverage (e.g.
Brick Fanatics' "every finalist" roundups) that "finalist" already means
"this is becoming a real set", not a further-competed-for subset — so no
additional status to reconcile.

Plain HTTP (requests/curl) gets a 202 "challenge" response from AWS WAF on
these pages with zero body — a real headless browser is required, same
class of problem as LEGO.com's own Cloudflare protection.

Scoped to non-CLOSED series only (closed ones are fully historical — nothing
about them changes run to run) to keep the crawl fast: one page load per
active series, typically 3-4 series at a time.
"""

from __future__ import annotations

import json
import re

from lego_common import DATA_DIR, HEADERS, now_iso, load_json, save_json, append_log

BDP_PATH = DATA_DIR / "bdp_finalists.json"
LOG_PATH = DATA_DIR / "bdp_changes_log.json"

BASE_SERIES_URL = "https://www.bricklink.com/v3/designer-program/series-{slug}/main.page"

# A series still being resolved — CLOSED ones are done, nothing left to
# track (production already started or the series didn't advance).
ACTIVE_PHASES = {
    "INTAKE", "CROWDSOURCING", "CROWD_VALIDATION", "VALIDATION",
    "POST_VALIDATION", "REVIEW", "DESIGNS_ANNOUNCED", "REFINING",
    "CROWDFUNDING_ANNOUNCEMENT", "CROWDFUNDING", "PRODUCTION",
}

SERIES_OBJECT_RE = re.compile(r'\{"idSeries":\d+.*?"phase":"[A-Z_]+"\}')


def extract_json_object(html: str, marker: str) -> dict | None:
    """Finds `marker` in the page and brace-matches (string-literal aware)
    to pull out the one complete JSON object that follows it — used
    instead of regex here because the finalist objects nest HTML
    descriptions and image arrays too deep for a simple pattern."""
    idx = html.find(marker)
    if idx == -1:
        return None
    start = html.find("{", idx)
    if start == -1:
        return None

    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        c = html[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start:i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def list_series(html: str) -> list[dict]:
    matches = SERIES_OBJECT_RE.findall(html)
    series = []
    for m in matches:
        try:
            series.append(json.loads(m))
        except json.JSONDecodeError:
            continue
    # Same series appears once per script tag that references it on the
    # page — de-dupe by idSeries, keeping the first (all copies match).
    seen = {}
    for s in series:
        seen.setdefault(s["idSeries"], s)
    return list(seen.values())


def extract_finalists(html: str, series_name: str, series_slug: str, series_phase: str) -> list[dict]:
    result = extract_json_object(html, '"submissions_finalists","controller"')
    if not result or result.get("errorCode") != "EC_OK":
        return []

    entries = []
    for sub in result.get("data", {}).get("submissions", []):
        images = sub.get("arrImages") or []
        thumb = None
        if images:
            url = images[0].get("dmThumbnail", {}).get("url") or images[0].get("dmImage", {}).get("url")
            if url:
                thumb = f"https:{url}" if url.startswith("//") else url

        entries.append({
            "id": sub.get("idSubmission"),
            "name": sub.get("strSubmissionName"),
            "series_name": series_name,
            "series_phase": series_phase,
            "series_url": BASE_SERIES_URL.format(slug=series_slug),
            "pieces": sub.get("nTotalParts") or None,
            "minifigures": sub.get("nMinifigureCnt") or None,
            "image": thumb,
            "video_url": sub.get("strVideoUrl") or None,
        })
    return entries


def scrape_bdp_finalists() -> dict[str, dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ! playwright not installed — skipping BDP scrape")
        return {}

    found: dict[str, dict] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])

        # Any series page carries the full listSeries data — series-1
        # always exists and is cheap, use it just to discover what's
        # currently active before visiting those pages specifically.
        try:
            page.goto(BASE_SERIES_URL.format(slug="1"), timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(1500)
            all_series = list_series(page.content())
        except Exception as exc:
            print(f"  ! failed to discover series list: {exc}")
            browser.close()
            return {}

        active = [s for s in all_series if s.get("phase") in ACTIVE_PHASES]
        print(f"  found {len(active)} active series out of {len(all_series)} total")

        for series in sorted(active, key=lambda s: s["idSeries"]):
            slug = series["strSlug"].replace("series-", "")
            try:
                page.goto(BASE_SERIES_URL.format(slug=slug), timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(1500)
                finalists = extract_finalists(page.content(), series["strName"], slug, series["phase"])
                for f in finalists:
                    found[str(f["id"])] = f
                print(f"  {series['strName']} ({series['phase']}): {len(finalists)} finalist(s)")
            except Exception as exc:
                print(f"    ! {series['strName']}: {exc}")

        browser.close()

    return found


def diff_and_log(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    timestamp = now_iso()
    changes = []
    for sub_id, entry in current.items():
        if sub_id not in previous:
            changes.append({"type": "new_finalist", "timestamp": timestamp, **{
                k: entry[k] for k in ("id", "name", "series_name", "series_phase")
            }})
        elif previous[sub_id].get("series_phase") != entry.get("series_phase"):
            changes.append({
                "type": "phase_changed", "timestamp": timestamp,
                "id": entry["id"], "name": entry["name"], "series_name": entry["series_name"],
                "from_phase": previous[sub_id].get("series_phase"), "to_phase": entry.get("series_phase"),
            })
    return changes


def report(changes: list[dict]) -> None:
    if not changes:
        print("No changes to BDP finalists since last run.")
        return
    print(f"\n{len(changes)} change(s) detected:")
    for c in changes:
        if c["type"] == "new_finalist":
            print(f"  - NEW: {c['name']} ({c['series_name']})")
        else:
            print(f"  - {c['name']}: {c['from_phase']} -> {c['to_phase']}")


def main() -> None:
    previous = load_json(BDP_PATH, {})
    current = scrape_bdp_finalists()
    if not current:
        print("No data scraped (BrickLink fetch failed) — leaving saved state untouched.")
        return

    changes = diff_and_log(previous, current)
    save_json(BDP_PATH, current)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
