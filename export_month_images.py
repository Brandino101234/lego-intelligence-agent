"""Download full-resolution product images for a given month's LEGO sets.

Reuses the release calendar data the dashboard already scrapes (data/
release_calendar.json) instead of hitting LEGO.com again. The images stored
there are 320x320 thumbnails (sized for the dashboard cards); this bumps the
same CDN URL's width/height/quality query params to pull the same asset at
a much higher resolution, since LEGO's image CDN happily serves whatever
size you ask for.

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
        image_url = e.get("image")

        if not image_url:
            print(f"  ! {set_num} {name}: no image available, skipping")
            skipped += 1
            continue

        filename = f"{set_num} {sanitize_filename(name)}.jpg"
        dest = out_dir / filename

        resp = requests.get(upsize_lego_image_url(image_url), headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  ! {set_num} {name}: image request returned HTTP {resp.status_code}")
            skipped += 1
            continue

        dest.write_bytes(resp.content)
        print(f"  {dest.name}")
        downloaded += 1

    print(f"\nSaved {downloaded} image(s) to {out_dir}" + (f" ({skipped} skipped)" if skipped else ""))


if __name__ == "__main__":
    main()
