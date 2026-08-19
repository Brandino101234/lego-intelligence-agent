"""Builds a monthly LEGO release calendar from LEGO.com itself.

LEGO.com's own "Coming soon" category (lego.com/en-us/categories/coming-soon)
turns out to be a curated marketing collection, not a live query — it misses
a lot of real upcoming sets (2026 Advent Calendars, several Avengers:
Doomsday tie-ins, etc. were confirmed missing in testing). The complete
signal lives on every product's own `availabilityStatus` attribute
(`A_PRE_ORDER_FOR_DATE`, `B_COMING_SOON_AT_DATE`, vs. `E_AVAILABLE`,
`F/G_BACKORDER*`, `H_OUT_OF_STOCK`, `K_SOLD_OUT` for stock states of
already-released sets), which is visible on every theme listing page. So
this crawls the "Coming soon" category *and* every theme's own listing page
(~76 of them, discovered from LEGO.com's sitemap so new themes are picked up
automatically), and keeps whatever has one of the two "not released yet"
statuses.

LEGO.com is a Next.js app: each page's initial data — including the full
Apollo GraphQL cache for everything rendered on it — is embedded server-side
in a <script id="__NEXT_DATA__"> tag. Rather than scrape rendered markup,
this pulls that JSON blob directly. Confusingly, LEGO.com renders listing
pages with two different underlying templates depending on the page (not
randomly — consistent per URL, seemingly a per-theme CMS configuration): the
category-page template exposes a `ProductListingPage` with a `tiles` list
(mixing real products in with marketing tiles); the theme-page template
exposes a `ProductQueryResult` with a flat `results` list of products
directly and offset/count/total pagination instead of a page list. Both are
handled here.

Results are grouped by release month (YYYY-MM) and saved to
data/release_calendar.json, diffed against the previous snapshot the same
way the other two agents work, with the diff logged to
data/release_calendar_changes_log.json.

Along the way this crawl passes through every *currently on sale* product
too (not just upcoming ones), so it also collects an official-LEGO.com-image
lookup keyed by set number and saves it to data/lego_product_images.json —
the retirement agent uses this instead of scraping images from a
third-party site.
"""

from __future__ import annotations

import json
import re

from lego_common import DATA_DIR, fetch_via_curl, load_json, now_iso, parse_flexible_date, save_json, append_log

COMING_SOON_URL = "https://www.lego.com/en-us/categories/coming-soon"
SITEMAP_INDEX_URL = "https://www.lego.com/productlisting-sitemap.xml"
MAX_PAGES_PER_LISTING = 12

CALENDAR_PATH = DATA_DIR / "release_calendar.json"
LOG_PATH = DATA_DIR / "release_calendar_changes_log.json"
IMAGES_PATH = DATA_DIR / "lego_product_images.json"
PRICES_PATH = DATA_DIR / "lego_product_prices.json"

NEXT_DATA_MARKER = '__NEXT_DATA__" type="application/json">'
# Matches the trailing date regardless of the lead-in phrase — LEGO.com
# uses both "Coming soon on September 1, 2026" and "Pre-order this item
# today, it will ship from September 1, 2026" for the same date concept.
AVAILABILITY_DATE_RE = re.compile(r"([A-Za-z]+ \d{1,2},\s*\d{4})")

# The only two statuses that mean "not released yet" — everything else
# (E_AVAILABLE, F/G_BACKORDER*, H_OUT_OF_STOCK, K_SOLD_OUT, ...) describes
# the stock state of a set that's already out, confirmed against ~540 real
# products across 8 themes before relying on this.
UPCOMING_STATUSES = {"A_PRE_ORDER_FOR_DATE", "B_COMING_SOON_AT_DATE"}

# Snapshot of top-level theme slugs from LEGO.com's sitemap (captured
# 2026-08-15), used only if the live sitemap fetch fails so a transient
# failure doesn't zero out the whole calendar.
FALLBACK_THEME_SLUGS = [
    "architecture", "art", "avatar", "bluey", "boost", "botanicals",
    "brick-sketches", "brickheadz", "city", "classic", "creator-3-in-1",
    "creator-expert", "dc", "dimensions", "disney", "dots", "dreamzzz",
    "duplo", "editions", "elves", "fantastic-beasts", "fortnite", "friends",
    "friends-tv-show-sets", "gabbys-dollhouse", "ghostbusters",
    "harry-potter", "harry-potter-t", "ideas", "indiana-jones", "juniors",
    "jurassic-world", "kpop-demon-hunters", "legend-of-zelda",
    "lego-batman-sets", "lego-education", "lego-icons", "lego-originals",
    "lego-spider-man", "lord-of-the-rings", "marvel", "mickey-mouse",
    "mindstorms", "minecraft", "minifigures", "minions", "monkie-kid",
    "nexo-knights", "nike", "ninjago", "one-piece", "overwatch", "pokemon",
    "power-functions", "powered-up", "powerpuff-girls", "serious-play",
    "shrek", "speed-champions", "star-wars", "star-wars-t",
    "stranger-things", "super-mario", "technic", "technic-t",
    "the-lego-batman-movie", "the-lego-movie-2", "the-lego-ninjago-movie",
    "toy-story", "toy-story-4", "trolls", "unikitty", "vidiyo", "wednesday",
    "wicked", "xtra",
]


def resolve(apollo: dict, ref) -> dict | None:
    """Apollo's normalized cache stores every nested object as a {'id': ...}
    reference; `ref['id']` is itself a lookup key into the same flat dict."""
    if isinstance(ref, dict) and "id" in ref:
        return apollo.get(ref["id"])
    return ref if isinstance(ref, dict) else None


def parse_next_data(html: str) -> dict | None:
    idx = html.find(NEXT_DATA_MARKER)
    if idx == -1:
        return None
    start = idx + len(NEXT_DATA_MARKER)
    end = html.find("</script>", start)
    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return None


def parse_availability_date(text: str | None) -> str | None:
    if not text:
        return None
    m = AVAILABILITY_DATE_RE.search(text)
    if not m:
        return None
    parsed = parse_flexible_date(m.group(1))
    return parsed.isoformat() if parsed else None


def product_image(product: dict) -> str | None:
    return product.get('primaryImage({"size":"THUMBNAIL"})')


def build_entry(apollo: dict, product: dict) -> dict | None:
    variant_ref = product.get("variant") or next(iter(product.get("variants") or []), None)
    variant = resolve(apollo, variant_ref)
    if not variant:
        return None

    attrs = resolve(apollo, variant.get("attributes")) or {}
    pieces = attrs.get("pieceCount")
    if not pieces:
        # No piece count means this is merch (apparel, keychains, bags),
        # not a buildable set.
        return None

    status = attrs.get("availabilityStatus")
    if status not in UPCOMING_STATUSES:
        return None

    price = resolve(apollo, variant.get("price"))
    brand_category = resolve(apollo, product.get("brandCategory"))
    availability_text = attrs.get("availabilityText")

    # LEGO Insiders (their API still calls it "VIP" internally) sometimes
    # get early access to a set before its general release date. These
    # fields exist on every product but are usually null — only populated
    # during an actual active early-access window, which is short-lived
    # and tied to specific launches rather than always-on.
    vip_text = attrs.get("vipAvailabilityText")
    vip_status = attrs.get("vipAvailabilityStatus")

    return {
        "set_num": product["productCode"],
        "name": product["name"],
        "theme": brand_category["name"].strip() if brand_category and brand_category.get("name") else None,
        "pieces": pieces,
        "price": price["formattedAmount"] if price else None,
        "availability_status": status,
        "availability_text": availability_text,
        "launch_date": parse_availability_date(availability_text),
        "insiders_early_access": vip_text or (vip_status is not None),
        "insiders_early_access_text": vip_text,
        "url": f"https://www.lego.com{product['pdpPath']}",
        "image": product_image(product),
    }


def extract_products(apollo: dict) -> tuple[list[dict], int | None, dict[str, str], dict[str, str]]:
    """Returns (upcoming_entries, next_page, images, prices). `images` and
    `prices` cover every real buildable set seen on the page — not just
    upcoming ones — keyed by set number, so other agents (e.g. the
    retirement tracker) can look up an official LEGO.com product image or
    current price without a separate crawl."""

    images: dict[str, str] = {}
    prices: dict[str, str] = {}

    def collect_info(product: dict, variant, pieces) -> None:
        if not pieces:
            return
        code = product["productCode"]
        img = product_image(product)
        if img:
            images[code] = img
        price = resolve(apollo, variant.get("price")) if variant else None
        if price and price.get("formattedAmount"):
            prices[code] = price["formattedAmount"]

    plp = next(
        (v for v in apollo.values() if isinstance(v, dict) and v.get("__typename") == "ProductListingPage"),
        None,
    )
    if plp is not None:
        entries = []
        for tile_ref in plp.get("tiles", []):
            tile = resolve(apollo, tile_ref)
            if not tile or tile.get("__typename") != "ProductTile":
                continue
            product = resolve(apollo, tile.get("product"))
            if not product:
                continue
            variant_ref = product.get("variant") or next(iter(product.get("variants") or []), None)
            variant = resolve(apollo, variant_ref)
            attrs = (resolve(apollo, variant.get("attributes")) if variant else None) or {}
            collect_info(product, variant, attrs.get("pieceCount"))
            entry = build_entry(apollo, product)
            if entry:
                entries.append(entry)
        pagination = resolve(apollo, plp.get("pagination")) or {}
        return entries, pagination.get("nextPage"), images, prices

    pqr = next(
        (v for v in apollo.values() if isinstance(v, dict) and v.get("__typename") == "ProductQueryResult"),
        None,
    )
    if pqr is not None:
        entries = []
        for ref in pqr.get("results", []):
            product = resolve(apollo, ref)
            if not product:
                continue
            variant_ref = product.get("variant") or next(iter(product.get("variants") or []), None)
            variant = resolve(apollo, variant_ref)
            attrs = (resolve(apollo, variant.get("attributes")) if variant else None) or {}
            collect_info(product, variant, attrs.get("pieceCount"))
            entry = build_entry(apollo, product)
            if entry:
                entries.append(entry)
        offset, count, total = pqr.get("offset", 0), pqr.get("count", 0), pqr.get("total", 0)
        has_more = offset + count < total
        current_page = (offset // count) + 1 if count else 1
        return entries, (current_page + 1 if has_more else None), images, prices

    return [], None, images, prices


def discover_theme_urls() -> list[str] | None:
    index_xml = fetch_via_curl(SITEMAP_INDEX_URL)
    if index_xml is None:
        return None

    m = re.search(r"<loc>(https://www\.lego\.com/sitemap-productlisting-en-US\d*\.xml)</loc>", index_xml)
    if not m:
        return None

    sub_xml = fetch_via_curl(m.group(1))
    if sub_xml is None:
        return None

    all_urls = re.findall(r"<loc>([^<]+)</loc>", sub_xml)
    top_level = sorted({
        u for u in all_urls
        if re.match(r"^https://www\.lego\.com/en-us/themes/[a-z0-9-]+$", u)
    })
    return top_level or None


def scrape_listing(start_url: str) -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    found: dict[str, dict] = {}
    all_images: dict[str, str] = {}
    all_prices: dict[str, str] = {}
    page = 1
    while page and page <= MAX_PAGES_PER_LISTING:
        url = start_url if page == 1 else f"{start_url}?page={page}"
        html = fetch_via_curl(url)
        if html is None:
            break
        data = parse_next_data(html)
        if data is None:
            break
        apollo = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__")
        if apollo is None:
            # Some sitemap theme slugs are stale and redirect to an
            # unrelated page (e.g. a discontinued-product news article)
            # instead of a product listing — nothing to scrape there.
            break
        entries, next_page, images, prices = extract_products(apollo)
        for e in entries:
            found[e["set_num"]] = e
        all_images.update(images)
        all_prices.update(prices)
        page = next_page
    return found, all_images, all_prices


def scrape_all_upcoming() -> tuple[dict[str, dict], dict[str, str], dict[str, str]]:
    all_products: dict[str, dict] = {}
    all_images: dict[str, str] = {}
    all_prices: dict[str, str] = {}

    print(f"Fetching {COMING_SOON_URL} ...")
    products, images, prices = scrape_listing(COMING_SOON_URL)
    all_products.update(products)
    all_images.update(images)
    all_prices.update(prices)

    theme_urls = discover_theme_urls()
    if theme_urls is None:
        print("  ! couldn't discover theme list from sitemap, using saved fallback list")
        theme_urls = [f"https://www.lego.com/en-us/themes/{slug}" for slug in FALLBACK_THEME_SLUGS]
    else:
        print(f"  discovered {len(theme_urls)} themes from LEGO.com's sitemap")

    for i, url in enumerate(theme_urls, 1):
        theme_products, theme_images, theme_prices = scrape_listing(url)
        if theme_products:
            new_count = sum(1 for k in theme_products if k not in all_products)
            print(f"  [{i}/{len(theme_urls)}] {url.rsplit('/', 1)[-1]}: {len(theme_products)} upcoming ({new_count} new)")
        all_products.update(theme_products)
        all_images.update(theme_images)
        all_prices.update(theme_prices)

    return all_products, all_images, all_prices


def build_calendar(products: dict[str, dict]) -> dict[str, list[dict]]:
    months: dict[str, list[dict]] = {}
    for entry in products.values():
        month_key = entry["launch_date"][:7] if entry["launch_date"] else "TBA"
        months.setdefault(month_key, []).append(entry)

    for month_key, entries in months.items():
        entries.sort(key=lambda e: (e["launch_date"] or "", e["name"]))

    dated = sorted(k for k in months if k != "TBA")
    ordered = {k: months[k] for k in dated}
    if "TBA" in months:
        ordered["TBA"] = months["TBA"]
    return ordered


def flatten(months: dict[str, list[dict]]) -> dict[str, str]:
    """set_num -> month_key, for diffing against the previous snapshot."""
    flat = {}
    for month_key, entries in months.items():
        for entry in entries:
            flat[entry["set_num"]] = month_key
    return flat


def diff_and_log(previous_months: dict[str, list[dict]], current_months: dict[str, list[dict]]) -> list[dict]:
    timestamp = now_iso()
    prev_flat = flatten(previous_months)
    curr_flat = flatten(current_months)
    curr_entries = {e["set_num"]: e for entries in current_months.values() for e in entries}
    prev_entries = {e["set_num"]: e for entries in previous_months.values() for e in entries}

    changes = []
    for set_num, month_key in curr_flat.items():
        if set_num not in prev_flat:
            entry = curr_entries[set_num]
            changes.append({
                "timestamp": timestamp,
                "type": "added_to_calendar",
                "set_num": set_num,
                "name": entry["name"],
                "theme": entry["theme"],
                "month": month_key,
            })
        elif prev_flat[set_num] != month_key:
            entry = curr_entries[set_num]
            changes.append({
                "timestamp": timestamp,
                "type": "month_changed",
                "set_num": set_num,
                "name": entry["name"],
                "theme": entry["theme"],
                "old_month": prev_flat[set_num],
                "new_month": month_key,
            })

    for set_num, month_key in prev_flat.items():
        if set_num not in curr_flat:
            entry = prev_entries[set_num]
            changes.append({
                "timestamp": timestamp,
                "type": "removed_from_calendar",
                "set_num": set_num,
                "name": entry["name"],
                "theme": entry["theme"],
                "last_known_month": month_key,
            })

    return changes


def report(changes: list[dict]) -> None:
    if not changes:
        print("No changes to the release calendar since last run.")
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
    previous = load_json(CALENDAR_PATH, {"months": {}})
    previous_months = previous.get("months", {})

    products, images, prices = scrape_all_upcoming()
    if not products:
        print("No data scraped (LEGO.com fetch failed) — leaving saved state untouched.")
        return

    print(f"\n  found {len(products)} buildable sets not yet released, across LEGO.com")
    print(f"  collected {len(images)} product images and {len(prices)} prices along the way")
    current_months = build_calendar(products)

    changes = diff_and_log(previous_months, current_months)
    save_json(CALENDAR_PATH, {"generated_at": now_iso(), "months": current_months})
    save_json(IMAGES_PATH, images)
    save_json(PRICES_PATH, prices)
    append_log(LOG_PATH, changes)
    report(changes)


if __name__ == "__main__":
    main()
