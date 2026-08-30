"""Standalone worker invoked as a subprocess by
lego_release_calendar_agent.enrich_from_product_pages() — does the actual
per-product Playwright work in a genuinely separate OS process so a hang
can be killed outright by the parent via subprocess.run's own timeout
handling, regardless of what's wedged inside (Chromium, Playwright's
driver process, its stdio pipe, ...).

A previous attempt at this used multiprocessing's "spawn" context to run
the same work in-process. That's the more common approach, but it has a
real pickling footgun with how this project actually invokes agents:
run_all.py runs each one via runpy.run_module(name, run_name="__main__"),
which stamps every function defined during that execution with
__module__ == "__main__" — multiprocessing's spawn bootstrap then can't
correctly locate the target function in the child process, since
sys.modules["__main__"] there is actually run_all.py itself, not this
module. A plain subprocess sidesteps that entirely: no pickling of code
objects, just JSON in and JSON out, the same pattern already used by
fetch_via_curl() in lego_common.py for reliability.

Usage: python3 enrich_worker.py <input.json> <output.json>
  input.json: [{"set_num": "...", "url": "..."}, ...]
  output.json (written only on a clean finish): {"entries": [...enriched...],
  "found_early_access": N, "found_galleries": N, "found_future_gwp": N}
"""

from __future__ import annotations

import json
import re
import sys

from lego_common import HEADERS
from lego_release_calendar_agent import (
    extract_future_gwp,
    extract_gallery_images,
    extract_pdp_apollo_state,
    extract_primary_image,
)

DATE_RANGE_RE = re.compile(r"\d{1,2}/\d{1,2}")


def run(entries: list[dict]) -> dict:
    from playwright.sync_api import sync_playwright

    found_early_access = found_galleries = found_future_gwp = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        for entry in entries:
            try:
                page.goto(entry["url"], timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1200)  # let client-rendered promo content settle

                banner = page.locator('[data-test="markup"]', has_text="Early Access").first
                if banner.count() > 0:
                    text = banner.inner_text().strip()
                    if DATE_RANGE_RE.search(text):
                        entry["insiders_early_access"] = True
                        entry["insiders_early_access_text"] = text
                        found_early_access += 1

                apollo = extract_pdp_apollo_state(page.content())
                if not apollo:
                    continue

                gallery = extract_gallery_images(apollo)
                if gallery:
                    entry["gallery_images"] = gallery
                    found_galleries += 1

                future_gwp = extract_future_gwp(apollo)
                if future_gwp:
                    if future_gwp.get("url"):
                        try:
                            page.goto(future_gwp["url"], timeout=30000, wait_until="domcontentloaded")
                            page.wait_for_timeout(1200)
                            gwp_apollo = extract_pdp_apollo_state(page.content())
                            if gwp_apollo:
                                future_gwp["image"] = extract_primary_image(gwp_apollo)
                        except Exception:
                            pass  # still worth keeping the promo without an image
                    if not future_gwp.get("image"):
                        future_gwp["image"] = future_gwp.pop("hero_image", None)
                    else:
                        future_gwp.pop("hero_image", None)
                    entry["future_gwp"] = future_gwp
                    found_future_gwp += 1
            except Exception as exc:
                print(f"    ! {entry.get('set_num')}: {exc}", file=sys.stderr)
        browser.close()

    return {
        "entries": entries,
        "found_early_access": found_early_access,
        "found_galleries": found_galleries,
        "found_future_gwp": found_future_gwp,
    }


def main() -> None:
    input_path, output_path = sys.argv[1], sys.argv[2]
    with open(input_path, encoding="utf-8") as f:
        entries = json.load(f)

    result = run(entries)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
