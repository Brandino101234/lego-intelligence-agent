"""Tracks LEGO sets that are retiring soon or confirmed retired.

- Scrapes BrickRanker's retirement tracker (https://brickranker.com/retirement-tracker),
  a table of currently-sold sets grouped by theme with expected retirement dates
  and a "Retiring soon!" flag.
- Cross-checks against Brick Fanatics' running "every LEGO set retiring this
  year and beyond" article as a second-source confirmation signal. Brick
  Fanatics sits behind Cloudflare's JS challenge, so this step is best-effort:
  if it can't be fetched, the run continues without the confirmation signal
  rather than failing.
- Diffs the result against data/retiring_sets.json to detect newly-flagged,
  confirmed-retired, and date-changed sets, and logs those changes to
  data/retiring_changes_log.json.

Product images and prices come from LEGO.com itself rather than
BrickRanker (which never listed price at all): the release-calendar
agent's crawl passes through every currently-on-sale product too and
saves lookups to data/lego_product_images.json and
data/lego_product_prices.json — run_all.py runs that agent before this
one so the lookups are fresh. A set that's no longer sold on LEGO.com at
all (fully gone, not just retiring) won't have an entry there, and gets
no image/price rather than a substitute.
"""

from __future__ import annotations

import re

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

BRICKRANKER_URL = "https://brickranker.com/retirement-tracker"
BRICKFANATICS_URL = "https://www.brickfanatics.com/every-lego-set-retiring-this-year-and-beyond/"

SETS_PATH = DATA_DIR / "retiring_sets.json"
LOG_PATH = DATA_DIR / "retiring_changes_log.json"
IMAGES_PATH = DATA_DIR / "lego_product_images.json"
PRICES_PATH = DATA_DIR / "lego_product_prices.json"


def scrape_brickranker() -> dict[str, dict]:
    """Returns {set_num: {set_num, name, theme, year_released,
    retirement_date_raw, retirement_date, retiring_soon, url}}."""
    resp = fetch(BRICKRANKER_URL)
    if resp is None:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    sets: dict[str, dict] = {}

    for h2 in soup.select("h2.text-3xl.font-bold.mb-4"):
        theme = h2.get_text(strip=True)
        table = h2.find_next_sibling("table")
        if table is None:
            continue

        for tr in table.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue

            name_cell = tds[0]
            links = name_cell.find_all("a", href=True)
            set_link = next((a for a in links if "/rankings/set/" in a["href"]), None)
            if set_link is None:
                continue

            set_num = set_link["href"].split("/rankings/set/")[1].split("/")[0]
            name = links[-1].get_text(strip=True)
            retiring_soon = "Retiring soon" in name_cell.get_text()

            year_text = tds[1].get_text(strip=True)
            year_released = int(year_text) if year_text.isdigit() else None

            retirement_raw = tds[2].get_text(strip=True)
            retirement_date = parse_flexible_date(retirement_raw)

            sets[set_num] = {
                "set_num": set_num,
                "name": name,
                "theme": theme,
                "year_released": year_released,
                "retirement_date_raw": retirement_raw,
                "retirement_date": retirement_date.isoformat() if retirement_date else None,
                "retiring_soon": retiring_soon,
                "url": set_link["href"],
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
    print(f"Fetching {BRICKRANKER_URL} ...")
    sets = scrape_brickranker()
    print(f"  found {len(sets)} currently-sold sets across BrickRanker's tracker")

    print(f"Fetching {BRICKFANATICS_URL} ...")
    confirmations = scrape_brickfanatics()
    if confirmations:
        print(f"  found {len(confirmations)} sets referenced on Brick Fanatics")

    images = load_json(IMAGES_PATH, {})
    prices = load_json(PRICES_PATH, {})
    if not images:
        print("  (no LEGO.com image/price lookup found yet — run the release-calendar agent first to build one)")

    for set_num, entry in sets.items():
        base_num = set_num.split("-")[0]
        match = confirmations.get(base_num)
        entry["brickfanatics_confirmed"] = match is not None
        entry["brickfanatics_retirement_heading"] = match["retirement_date_heading"] if match else None
        entry["last_checked"] = now_iso()
        entry["image"] = images.get(base_num)
        entry["price"] = prices.get(base_num)

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
        print("No data scraped (BrickRanker fetch failed) — leaving saved state untouched.")
        return

    changes = diff_and_log(previous, current)
    save_json(SETS_PATH, current)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
