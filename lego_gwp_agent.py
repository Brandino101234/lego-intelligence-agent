"""Tracks LEGO.com's current gift-with-purchase (GWP) promotions.

LEGO.com surfaces active GWP promos as `GwpPromotion` entries in the same
Apollo GraphQL cache used by the other pages this project scrapes (see
lego_release_calendar_agent.py for how that's parsed) — but only on some
pages, not all of them (confirmed present on "coming soon", "new sets and
products", "exclusives", and some theme pages; absent on others). So this
fetches a handful of pages known to carry it and merges what it finds.

Deliberately scoped to *current* promotions only: LEGO doesn't announce GWPs
in advance (they're revealed as they go live), and neither Brickset nor
Brick Fanatics maintain a structured "upcoming GWP" feed the way they do for
retiring sets — Brickset's GWP-tagged listing is a historical archive with
unreliable dates on its newest entries, and Brick Fanatics covers each GWP
as a one-off article rather than a running tracker. A "future GWP" section
would mean guessing rather than reporting real data.

Diffs against data/gwp.json and logs changes to data/gwp_changes_log.json,
same pattern as the other three agents.
"""

from __future__ import annotations

from lego_common import DATA_DIR, fetch_via_curl, load_json, now_iso, save_json, append_log
from lego_release_calendar_agent import parse_next_data, resolve

# Pages confirmed (by direct inspection) to carry the sitewide GWP promo
# banner in their Apollo state. Fetching a few gives redundancy in case any
# one of them stops carrying it or is temporarily unreachable.
CANDIDATE_URLS = [
    "https://www.lego.com/en-us/categories/coming-soon",
    "https://www.lego.com/en-us/categories/new-sets-and-products",
    "https://www.lego.com/en-us/categories/exclusives",
]

GWP_PATH = DATA_DIR / "gwp.json"
LOG_PATH = DATA_DIR / "gwp_changes_log.json"


def extract_gwp_promotions(apollo: dict) -> dict[str, dict]:
    found = {}
    for v in apollo.values():
        if not (isinstance(v, dict) and v.get("__typename") == "GwpPromotion"):
            continue

        code = v.get("gwpProductCode")
        if not code:
            continue

        # endDate arrives ISO-ish with a UTC offset, e.g.
        # "2026-08-17T04:59:00+01:00" — the date component is already
        # YYYY-MM-DD, no parsing needed.
        end_date_raw = v.get("endDate")
        end_date = end_date_raw[:10] if end_date_raw else None

        found[code] = {
            "gwp_code": code,
            "name": v.get("gwpName"),
            "image": v.get("image"),
            "qualifying_text": v.get("qualifyingText"),
            "insiders_only": bool(v.get("insidersOnly")),
            "end_date": end_date,
            "end_date_raw": end_date_raw,
            "url": f"https://www.lego.com/en-us/search?q={code}",
        }
    return found


def scrape_current_gwp() -> dict[str, dict]:
    all_gwp: dict[str, dict] = {}
    for url in CANDIDATE_URLS:
        print(f"Fetching {url} ...")
        html = fetch_via_curl(url)
        if html is None:
            continue
        data = parse_next_data(html)
        if data is None:
            continue
        apollo = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__")
        if apollo is None:
            continue
        found = extract_gwp_promotions(apollo)
        all_gwp.update(found)

    today = now_iso()[:10]
    return {code: g for code, g in all_gwp.items() if not g["end_date"] or g["end_date"] >= today}


def diff_and_log(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    timestamp = now_iso()
    changes = []

    for code, gwp in current.items():
        if code not in previous:
            changes.append({
                "timestamp": timestamp,
                "type": "gwp_started",
                "gwp_code": code,
                "name": gwp["name"],
                "qualifying_text": gwp["qualifying_text"],
                "end_date": gwp["end_date"],
            })

    for code, gwp in previous.items():
        if code not in current:
            changes.append({
                "timestamp": timestamp,
                "type": "gwp_ended",
                "gwp_code": code,
                "name": gwp["name"],
            })

    return changes


def report(changes: list[dict]) -> None:
    if not changes:
        print("No changes to current GWPs since last run.")
        return

    print(f"\n{len(changes)} change(s) detected:")
    for c in changes:
        if c["type"] == "gwp_started":
            print(f"  - NEW: {c['gwp_code']} {c['name']} ({c['qualifying_text']}, ends {c['end_date']})")
        else:
            print(f"  - ENDED: {c['gwp_code']} {c['name']}")


def main() -> None:
    previous = load_json(GWP_PATH, {})
    current = scrape_current_gwp()

    if not current and not previous:
        print("No GWP promotions found (none currently active, or all sources unreachable).")

    print(f"  found {len(current)} currently active GWP promotion(s)")

    changes = diff_and_log(previous, current)
    save_json(GWP_PATH, current)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
