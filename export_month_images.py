"""Download each set's full image gallery for a given month, not just the
single cover photo shown on the dashboard.

Reuses the release calendar data the dashboard already scrapes (data/
release_calendar.json) instead of hitting LEGO.com again — including each
set's gallery_images list (box front/back, in-hand shots, feature
call-outs), collected per-product by lego_release_calendar_agent.py's
Playwright pass. Each set gets its own subfolder. Every image is pulled at
a capped high resolution (LEGO's CDN will serve any size on request) rather
than the dashboard's 320x320 thumbnails.

Usage:
    python3 export_month_images.py            # current month
    python3 export_month_images.py next        # next month
    python3 export_month_images.py 2026-09     # specific month
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import requests

from lego_common import DATA_DIR, HEADERS, load_json, upsize_lego_image_url

EXPORTS_DIR = Path(__file__).resolve().parent / "exports"

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*™®™®]')


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("", name).strip()
    return re.sub(r"\s+", " ", cleaned)


def resolve_month_arg(arg: str | None) -> str:
    today = date.today()
    if arg is None:
        return f"{today.year:04d}-{today.month:02d}"
    if arg == "next":
        year, month = today.year, today.month + 1
        if month > 12:
            year, month = year + 1, 1
        return f"{year:04d}-{month:02d}"
    if re.fullmatch(r"\d{4}-\d{2}", arg):
        return arg
    raise SystemExit(f"Unrecognized month argument: {arg!r} (use YYYY-MM or 'next')")


def main() -> None:
    month = resolve_month_arg(sys.argv[1] if len(sys.argv) > 1 else None)

    calendar = load_json(DATA_DIR / "release_calendar.json", {"months": {}})
    entries = calendar.get("months", {}).get(month)
    if not entries:
        available = ", ".join(sorted(calendar.get("months", {}).keys())) or "(none)"
        raise SystemExit(f"No tracked sets for {month}. Months currently tracked: {available}")

    entries = sorted(entries, key=lambda e: e.get("launch_date") or "")

    out_dir = EXPORTS_DIR / month
    out_dir.mkdir(parents=True, exist_ok=True)

    downloaded, skipped = 0, 0
    for e in entries:
        set_num = e.get("set_num", "unknown")
        name = e.get("name", set_num)
        gallery = e.get("gallery_images") or ([e["image"]] if e.get("image") else [])

        if not gallery:
            print(f"  ! {set_num} {name}: no image available, skipping")
            skipped += 1
            continue

        set_dir = out_dir / f"{set_num} {sanitize_filename(name)}"
        set_dir.mkdir(parents=True, exist_ok=True)

        for i, image_url in enumerate(gallery, start=1):
            resp = requests.get(upsize_lego_image_url(image_url), headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"  ! {set_num} {name} [{i}]: image request returned HTTP {resp.status_code}")
                skipped += 1
                continue
            (set_dir / f"{i:02d}.jpg").write_bytes(resp.content)
            downloaded += 1

        print(f"  {set_dir.name}/ ({len(gallery)} image(s))")

    print(f"\nSaved {downloaded} image(s) to {out_dir}" + (f" ({skipped} skipped)" if skipped else ""))


if __name__ == "__main__":
    main()
