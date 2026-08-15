"""Tracks newly announced LEGO sets.

Scrapes Brickset's "New additions" listing (sets most recently added to its
database, which is effectively Brickset's announcement feed) and diffs the
result against data/new_sets.json to detect what's genuinely new since the
last run. Changes are logged to data/new_sets_changes_log.json.

Only fetches as many pages as needed: it walks pages newest-first and stops
once it reaches a page containing no sets absent from the saved state (i.e.
it has caught up to what was already known). On the very first run there's
nothing saved yet, so it walks up to MAX_PAGES pages to build a baseline.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from lego_common import (
    DATA_DIR,
    fetch,
    is_placeholder_name,
    load_json,
    now_iso,
    parse_flexible_date,
    save_json,
    append_log,
)

BASE_URL = "https://brickset.com/sets?query=new-additions"
PAGE_URL = "https://brickset.com/sets/page-{page}?query=new-additions"
MAX_PAGES = 10

SETS_PATH = DATA_DIR / "new_sets.json"
LOG_PATH = DATA_DIR / "new_sets_changes_log.json"


def parse_article(article) -> dict | None:
    meta = article.select_one("div.meta")
    if meta is None:
        return None

    h1_a = meta.select_one("h1 a")
    if h1_a is None or not h1_a.get("href"):
        return None

    href = h1_a["href"]
    m = re.match(r"^/sets/([0-9]+-[0-9]+)/", href)
    if not m:
        return None
    set_num = m.group(1)

    name = re.sub(r"^\d+:\s*", "", h1_a.get_text(" ", strip=True))

    theme = subtheme = year = None
    tags_div = meta.select_one("div.tags")
    if tags_div:
        for a in tags_div.find_all("a", href=True):
            classes = a.get("class", [])
            if "subtheme" in classes:
                subtheme = a.get_text(strip=True)
            elif "year" in classes:
                year = a.get_text(strip=True)
            elif a["href"].startswith("/sets/theme-"):
                theme = a.get_text(strip=True)

    pieces = None
    pieces_dt = meta.find("dt", string="Pieces")
    if pieces_dt:
        dd = pieces_dt.find_next_sibling("dd")
        if dd:
            digits = re.sub(r"[^\d]", "", dd.get_text())
            pieces = int(digits) if digits else None

    price = None
    price_dt = meta.find("dt", string="Value new")
    if price_dt:
        dd = price_dt.find_next_sibling("dd")
        if dd:
            price = dd.get_text(strip=True)

    launch_date = None
    launch_dt = meta.find("dt", string="Launch/exit")
    if launch_dt:
        dd = launch_dt.find_next_sibling("dd")
        if dd:
            raw = dd.get_text(strip=True)
            parsed = parse_flexible_date(raw.split(" - ")[0])
            launch_date = parsed.isoformat() if parsed else None

    return {
        "set_num": set_num,
        "name": name,
        "theme": theme,
        "subtheme": subtheme,
        "year": int(year) if year and year.isdigit() else None,
        "pieces": pieces,
        "price": price,
        "launch_date": launch_date,
        "url": f"https://brickset.com{href}",
    }


def scrape_new_additions(known_set_nums: set[str]) -> dict[str, dict]:
    scraped: dict[str, dict] = {}
    first_run = not known_set_nums

    for page in range(1, MAX_PAGES + 1):
        url = BASE_URL if page == 1 else PAGE_URL.format(page=page)
        print(f"Fetching {url} ...")
        resp = fetch(url)
        if resp is None:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        articles = soup.select("section.setlist article.set")
        if not articles:
            break

        page_found_new = False
        for article in articles:
            entry = parse_article(article)
            if entry is None or is_placeholder_name(entry["name"]):
                continue
            scraped[entry["set_num"]] = entry
            if entry["set_num"] not in known_set_nums:
                page_found_new = True

        if not first_run and not page_found_new:
            break

    return scraped


def diff_and_log(previous: dict[str, dict], scraped: dict[str, dict]) -> list[dict]:
    timestamp = now_iso()
    changes = []
    for set_num, entry in scraped.items():
        if set_num not in previous:
            changes.append({
                "timestamp": timestamp,
                "type": "new_set_announced",
                "set_num": set_num,
                "name": entry["name"],
                "theme": entry["theme"],
                "year": entry["year"],
                "launch_date": entry["launch_date"],
            })
    return changes


def report(changes: list[dict]) -> None:
    if not changes:
        print("No new sets since last run.")
        return

    print(f"\n{len(changes)} new set(s) detected:")
    for c in changes:
        launch = f" (launches {c['launch_date']})" if c["launch_date"] else ""
        print(f"  - {c['set_num']} {c['name']} [{c['theme']}]{launch}")


def main() -> None:
    previous = load_json(SETS_PATH, {})
    scraped = scrape_new_additions(set(previous.keys()))

    if not scraped:
        print("No data scraped (Brickset fetch failed) — leaving saved state untouched.")
        return

    changes = diff_and_log(previous, scraped)
    merged = {**previous, **scraped}
    save_json(SETS_PATH, merged)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
