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

Alongside the finalists (bdp_finalists.json), also saves one record per
active series (bdp_series.json) — including series with zero finalists
so far, like an INTAKE-phase series still in crowdsourcing — carrying its
`phase` and the next upcoming pipeline date (next_milestone_label/_at,
picked from the series' own udt* timestamp fields, whichever is soonest
and still in the future) plus its eventual production_start_at. This is
what lets the dashboard show "what's coming up and when" per series, not
just which designs already exist.

Each series record also keeps every pipeline field's own raw date under
`dates` (not just the "next" one), so diff_series_dates() can catch
BrickLink itself moving a date — e.g. crowdfunding getting pushed back —
run to run, logged as a milestone_date_changed change.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

from lego_common import DATA_DIR, HEADERS, now_iso, load_json, save_json, append_log

BDP_PATH = DATA_DIR / "bdp_finalists.json"
SERIES_PATH = DATA_DIR / "bdp_series.json"
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

# Every `udt*` millisecond-timestamp field on a series object, in pipeline
# order, paired with a human label — used to find "what's the next thing
# that happens for this series" regardless of its current `phase` (phase
# alone doesn't say how close the next date is, and a couple of these
# (e.g. crowdsourcing open) can lag the timestamp by a few days in
# practice, so we scan dates directly rather than switching on phase).
MILESTONE_FIELDS = [
    ("udtCrowdSourcingStart", "Crowdsourcing opens"),
    ("udtCrowdSourcingEnd", "Crowdsourcing closes"),
    ("udtCrowdValidationStart", "Community validation begins"),
    ("udtCrowdValidationEnd", "Community validation ends"),
    ("udtReviewStart", "LEGO review begins"),
    ("udtReviewEnd", "LEGO review ends"),
    ("udtDesignsAnnounced", "Finalists announced"),
    ("udtRefiningStart", "Refining begins"),
    ("udtRefiningEnd", "Refining ends"),
    ("udtCrowdFundingAnnouncement", "Crowdfunding announcement"),
    ("udtCrowdFundingStart", "Crowdfunding opens"),
    ("udtCrowdFundingEnd", "Crowdfunding closes"),
    ("udtProductionStart", "Production begins"),
]


def next_milestone(series: dict, now_ms: float) -> dict | None:
    """Earliest still-upcoming pipeline date on this series, with its label."""
    upcoming = [
        (series[field], label) for field, label in MILESTONE_FIELDS
        if series.get(field) and series[field] > now_ms
    ]
    if not upcoming:
        return None
    at_ms, label = min(upcoming, key=lambda pair: pair[0])
    return {"label": label, "at": datetime.fromtimestamp(at_ms / 1000).isoformat()}


def milestone_dates(series: dict) -> dict[str, str | None]:
    """Every pipeline field's own date (ISO, or None if BrickLink hasn't set
    it yet), keyed by field name — the raw record diff_series_dates() compares
    run to run to catch BrickLink pushing a date back (or moving it up), as
    opposed to next_milestone()'s single "what's next" pointer which advances
    on its own as time passes and isn't a useful diff target."""
    return {
        field: (datetime.fromtimestamp(series[field] / 1000).isoformat() if series.get(field) else None)
        for field, _ in MILESTONE_FIELDS
    }


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

    def to_https(url: str | None) -> str | None:
        if not url:
            return None
        return f"https:{url}" if url.startswith("//") else url

    entries = []
    for sub in result.get("data", {}).get("submissions", []):
        images = sub.get("arrImages") or []
        # dmImage is the real upload (2048x1536 on everything checked) —
        # dmThumbnail (640x480) is only used as the dashboard card's own
        # small preview, see build_dashboard.py.
        gallery = [to_https(img.get("dmImage", {}).get("url")) for img in images]
        gallery = [u for u in gallery if u]
        thumb = to_https(images[0].get("dmThumbnail", {}).get("url")) if images else None

        entries.append({
            "id": sub.get("idSubmission"),
            "name": sub.get("strSubmissionName"),
            "series_name": series_name,
            "series_phase": series_phase,
            "series_url": BASE_SERIES_URL.format(slug=series_slug),
            "pieces": sub.get("nTotalParts") or None,
            "minifigures": sub.get("nMinifigureCnt") or None,
            "image": thumb,
            "gallery_images": gallery,
            "video_url": sub.get("strVideoUrl") or None,
        })
    return entries


def scrape_bdp_finalists() -> tuple[dict[str, dict], dict[str, dict]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ! playwright not installed — skipping BDP scrape")
        return {}, {}

    found: dict[str, dict] = {}
    series_info: dict[str, dict] = {}
    now_ms = time.time() * 1000

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
            return {}, {}

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

                milestone = next_milestone(series, now_ms)
                production_ts = series.get("udtProductionStart")
                series_info[series["strName"]] = {
                    "id": series["idSeries"],
                    "name": series["strName"],
                    "phase": series["phase"],
                    "url": BASE_SERIES_URL.format(slug=slug),
                    "finalist_count": len(finalists),
                    "next_milestone_label": milestone["label"] if milestone else None,
                    "next_milestone_at": milestone["at"] if milestone else None,
                    "production_start_at": (
                        datetime.fromtimestamp(production_ts / 1000).isoformat() if production_ts else None
                    ),
                    "dates": milestone_dates(series),
                }

                print(f"  {series['strName']} ({series['phase']}): {len(finalists)} finalist(s)")
            except Exception as exc:
                print(f"    ! {series['strName']}: {exc}")

        browser.close()

    return found, series_info


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


def diff_series_dates(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    """Flags BrickLink itself moving a pipeline date — e.g. crowdfunding
    getting pushed back — by comparing each series' raw per-field dates
    run to run, not just the "next upcoming" pointer (which changes on its
    own as time passes and would falsely look like a change every time an
    old milestone rolls off)."""
    timestamp = now_iso()
    changes = []
    for name, entry in current.items():
        prev_entry = previous.get(name)
        if not prev_entry or "dates" not in prev_entry:
            # No baseline yet (new series, or the first run after this
            # tracking was added) — nothing to compare against, and every
            # field would otherwise look like it just "appeared".
            continue
        prev_dates = prev_entry["dates"]
        cur_dates = entry.get("dates") or {}
        for field, label in MILESTONE_FIELDS:
            old_at, new_at = prev_dates.get(field), cur_dates.get(field)
            if old_at == new_at:
                continue
            changes.append({
                "type": "milestone_date_changed", "timestamp": timestamp,
                "series_name": name, "milestone_label": label,
                "from_at": old_at, "to_at": new_at,
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
        elif c["type"] == "phase_changed":
            print(f"  - {c['name']}: {c['from_phase']} -> {c['to_phase']}")
        else:
            old, new = c["from_at"], c["to_at"]
            if not old:
                print(f"  - {c['series_name']}: {c['milestone_label']} now scheduled for {new}")
            elif not new:
                print(f"  - {c['series_name']}: {c['milestone_label']} date removed (was {old})")
            else:
                direction = "pushed back" if new > old else "moved up"
                print(f"  - {c['series_name']}: {c['milestone_label']} {direction} to {new} (was {old})")


def main() -> None:
    previous = load_json(BDP_PATH, {})
    previous_series = load_json(SERIES_PATH, {})
    current, series_info = scrape_bdp_finalists()
    if not current and not series_info:
        print("No data scraped (BrickLink fetch failed) — leaving saved state untouched.")
        return

    changes = diff_and_log(previous, current)
    changes += diff_series_dates(previous_series, series_info)
    save_json(BDP_PATH, current)
    save_json(SERIES_PATH, series_info)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
