"""Tracks LEGO sets that are retiring soon or confirmed retired.

- Primary source: Brick Tap's community-maintained "LEGO Set List" Google
  Sheet (https://bricktap.org/#retirement links to it), sourced by its
  maintainer from Brick Hound. It's a plain public Google Sheet, so it's
  fetched as CSV export (no bot-protection dance needed, unlike the old
  BrickRanker source or LEGO.com/BrickLink) and covers far more sets
  (~990 tracked vs. BrickRanker's ~570) with richer per-set data: age
  rating, piece count, and exclusivity notes, none of which BrickRanker
  provided at all.
- Cross-checks against Brick Fanatics' running "every LEGO set retiring
  this year and beyond" article as a second-source confirmation signal,
  same as before. Brick Fanatics sits behind Cloudflare's JS challenge,
  so this step is best-effort: if it can't be fetched, the run continues
  without the confirmation signal rather than failing.
- Diffs the result against data/retiring_sets.json to detect newly-flagged,
  confirmed-retired, and date-changed sets, and logs those changes to
  data/retiring_changes_log.json.

Brick Tap's sheet doesn't have an explicit "retiring soon" flag the way
BrickRanker did — most rows carry a generic year-end placeholder date
(LEGO sets are typically estimated to retire "by end of year N" until a
firmer date is known), covering the whole current catalog years out. So
`retiring_soon` here is derived: true if the retirement date falls within
SOON_DAYS of today, reusing the same threshold build_dashboard.py already
uses for its own urgency color-coding, rather than inventing a second
threshold to keep in sync.

Product images and prices still come from LEGO.com itself (via the
release-calendar agent's crawl, data/lego_product_images.json and
data/lego_product_prices.json) rather than Brick Tap, which doesn't list
either. That same crawl now also saves a product-page URL lookup
(data/lego_product_urls.json) — Brick Tap's own "LEGO.com Link" column is
a set of per-region hyperlinks, but Google's CSV export drops the actual
hyperlink target and keeps only the visible cell text ("US", "CA", ...),
so those links aren't recoverable from the CSV at all. A set that's no
longer sold on LEGO.com (fully gone, not just retiring) won't have an
entry in that lookup, and falls back to a LEGO.com search link instead of
a dead product page.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from urllib.parse import quote

from bs4 import BeautifulSoup

from lego_common import (
    DATA_DIR,
    fetch,
    load_json,
    now_iso,
    parse_flexible_date,
    save_json,
    append_log,
)

BRICKTAP_SHEET_ID = "1rlYfEXtNKxUOZt2Mfv0H17DvK7bj6Pe0CuYwq6ay8WA"
BRICKTAP_GID = "382685820"  # the "Sorted by Retirement Date" tab
BRICKTAP_CSV_URL = f"https://docs.google.com/spreadsheets/d/{BRICKTAP_SHEET_ID}/export?format=csv&gid={BRICKTAP_GID}"
BRICKFANATICS_URL = "https://www.brickfanatics.com/every-lego-set-retiring-this-year-and-beyond/"

SETS_PATH = DATA_DIR / "retiring_sets.json"
LOG_PATH = DATA_DIR / "retiring_changes_log.json"
IMAGES_PATH = DATA_DIR / "lego_product_images.json"
PRICES_PATH = DATA_DIR / "lego_product_prices.json"
URLS_PATH = DATA_DIR / "lego_product_urls.json"

# Matches build_dashboard.py's own "soon" urgency threshold — see module
# docstring for why this is reused rather than a second magic number.
SOON_DAYS = 180


def scrape_bricktap() -> dict[str, dict]:
    """Returns {set_num: {set_num, name, theme, subtheme, age, pieces,
    retirement_date_raw, retirement_date, notes}}."""
    resp = fetch(BRICKTAP_CSV_URL)
    if resp is None:
        return {}

    sets: dict[str, dict] = {}
    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)

    # Row 0 is a blank banner-image row, row 1 is the real header (with a
    # merged "LEGO.com Link" spanning several blank-named columns) — skip
    # both and read the rest positionally rather than trust column names.
    for row in rows[2:]:
        if len(row) < 7:
            continue
        set_num = row[2].strip()
        if not set_num.isdigit():
            # Skips stray non-data rows (a trailing disclaimer sentence
            # in column A, any fully blank row) without needing a
            # separate "is this a real row" check.
            continue

        theme, subtheme, name = row[0].strip(), row[1].strip(), row[3].strip()
        age = row[4].strip() or None
        pieces_raw = row[5].strip()
        pieces = int(pieces_raw) if pieces_raw.isdigit() else None
        retirement_raw = row[6].strip()
        retirement_date = parse_flexible_date(retirement_raw)
        notes = row[12].strip() if len(row) > 12 else ""

        sets[set_num] = {
            "set_num": set_num,
            "name": name,
            "theme": theme,
            "subtheme": subtheme if subtheme and subtheme != "-" else None,
            "age": age,
            "pieces": pieces,
            "retirement_date_raw": retirement_raw,
            "retirement_date": retirement_date.isoformat() if retirement_date else None,
            "notes": notes or None,
        }

    return sets


def scrape_brickfanatics() -> dict[str, dict]:
    """Returns {base_set_num: {theme, retirement_date_heading}} for cross-
    checking. Returns {} (not an error) if Brick Fanatics can't be reached."""
    resp = fetch(BRICKFANATICS_URL)
    if resp is None:
        print("  (Brick Fanatics cross-check unavailable this run)")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    confirmations: dict[str, dict] = {}

    theme = None
    date_heading = None
    for el in soup.find_all(["h2", "h4", "table"]):
        if el.name == "h2":
            # The page has two kinds of h2: the theme banner ("Retiring LEGO
            # Star Wars sets") and per-year sub-headings ("LEGO Star Wars
            # sets retiring in 2026"). Only the former marks a new theme —
            # the latter should leave the current theme in place.
            text = el.get_text(" ", strip=True)
            if text.startswith("Retiring LEGO"):
                theme = text.replace("Retiring LEGO ", "").replace(" sets", "").strip()
                date_heading = None
        elif el.name == "h4":
            date_heading = el.get_text(" ", strip=True)
        elif el.name == "table" and theme is not None:
            for a in el.select("a[data-set-number]"):
                base_num = a["data-set-number"].strip()
                if base_num:
                    confirmations[base_num] = {
                        "theme": theme,
                        "retirement_date_heading": date_heading,
                    }

    return confirmations


def build_current_state() -> dict[str, dict]:
    print(f"Fetching {BRICKTAP_CSV_URL} ...")
    sets = scrape_bricktap()
    print(f"  found {len(sets)} tracked sets on Brick Tap's sheet")

    print(f"Fetching {BRICKFANATICS_URL} ...")
    confirmations = scrape_brickfanatics()
    if confirmations:
        print(f"  found {len(confirmations)} sets referenced on Brick Fanatics")

    images = load_json(IMAGES_PATH, {})
    prices = load_json(PRICES_PATH, {})
    urls = load_json(URLS_PATH, {})
    if not images:
        print("  (no LEGO.com image/price lookup found yet — run the release-calendar agent first to build one)")

    today = date.today()
    for set_num, entry in sets.items():
        match = confirmations.get(set_num)
        entry["brickfanatics_confirmed"] = match is not None
        entry["brickfanatics_retirement_heading"] = match["retirement_date_heading"] if match else None
        entry["last_checked"] = now_iso()
        entry["image"] = images.get(set_num)
        entry["price"] = prices.get(set_num)
        entry["url"] = urls.get(set_num) or f"https://www.lego.com/en-us/search?q={quote(entry['name'] or set_num)}"

        d = entry["retirement_date"]
        entry["retiring_soon"] = bool(d and (date.fromisoformat(d) - today).days <= SOON_DAYS)

    return sets


def diff_and_log(previous: dict[str, dict], current: dict[str, dict]) -> list[dict]:
    changes = []
    timestamp = now_iso()

    for set_num, curr in current.items():
        prev = previous.get(set_num)

        if prev is None:
            changes.append({
                "timestamp": timestamp,
                "type": "new_set_tracked",
                "set_num": set_num,
                "name": curr["name"],
                "theme": curr["theme"],
                "retiring_soon": curr["retiring_soon"],
            })
            continue

        if curr["retiring_soon"] and not prev.get("retiring_soon"):
            changes.append({
                "timestamp": timestamp,
                "type": "newly_flagged",
                "set_num": set_num,
                "name": curr["name"],
                "theme": curr["theme"],
                "retirement_date": curr["retirement_date"],
            })

        if curr["retirement_date"] and prev.get("retirement_date") and curr["retirement_date"] != prev["retirement_date"]:
            changes.append({
                "timestamp": timestamp,
                "type": "date_changed",
                "set_num": set_num,
                "name": curr["name"],
                "theme": curr["theme"],
                "old_date": prev["retirement_date"],
                "new_date": curr["retirement_date"],
            })

    for set_num, prev in previous.items():
        if set_num not in current:
            changes.append({
                "timestamp": timestamp,
                "type": "confirmed_retired",
                "set_num": set_num,
                "name": prev["name"],
                "theme": prev["theme"],
                "last_known_retirement_date": prev.get("retirement_date"),
            })

    return changes


def report(changes: list[dict]) -> None:
    if not changes:
        print("No changes since last run.")
        return

    by_type: dict[str, list[dict]] = {}
    for c in changes:
        by_type.setdefault(c["type"], []).append(c)

    print(f"\n{len(changes)} change(s) detected:")
    for change_type, items in by_type.items():
        print(f"\n  {change_type} ({len(items)}):")
        for item in items:
            print(f"    - {item['set_num']} {item['name']} ({item['theme']})")


def main() -> None:
    previous = load_json(SETS_PATH, {})
    current = build_current_state()

    if not current:
        print("No data scraped (Brick Tap fetch failed) — leaving saved state untouched.")
        return

    changes = diff_and_log(previous, current)
    save_json(SETS_PATH, current)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
