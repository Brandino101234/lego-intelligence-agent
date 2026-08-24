"""Zips up full-resolution set images for every month on the dashboard,
so the "Download images" button on each month's calendar section has
something to link to.

Runs as part of every scrape (see run_all.py) and writes straight into
site/downloads/ — NOT committed to git (see .gitignore), since Netlify
deploys directly from the local site/ folder regardless of what's tracked,
and re-zipping identical images twice a day would otherwise bloat the repo
with duplicate binary blobs forever. build_dashboard.py reads the manifest
this writes to know which months got a real zip (vs. e.g. every image in
that month being broken on LEGO's own CDN) before rendering a button.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import requests

from lego_common import DATA_DIR, HEADERS, load_json, save_json, upsize_lego_image_url

ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT / "site" / "downloads"
MANIFEST_PATH = DATA_DIR / "image_zip_manifest.json"

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*™®]')


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("", name).strip()
    return re.sub(r"\s+", " ", cleaned)


def build_month_zip(month: str, entries: list[dict]) -> dict | None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS_DIR / f"{month}.zip"

    entries = sorted(entries, key=lambda e: e.get("launch_date") or "")
    added = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            image_url = e.get("image")
            if not image_url:
                continue
            resp = requests.get(upsize_lego_image_url(image_url), headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                continue
            filename = f"{e.get('set_num', 'unknown')} {sanitize_filename(e.get('name', ''))}.jpg"
            zf.writestr(filename, resp.content)
            added += 1

    if added == 0:
        zip_path.unlink(missing_ok=True)
        return None

    return {"file": f"downloads/{month}.zip", "count": added, "bytes": zip_path.stat().st_size}


def main() -> None:
    calendar = load_json(DATA_DIR / "release_calendar.json", {"months": {}})
    months = calendar.get("months", {})

    if DOWNLOADS_DIR.exists():
        for old_zip in DOWNLOADS_DIR.glob("*.zip"):
            old_zip.unlink()

    manifest = {}
    for month, entries in months.items():
        result = build_month_zip(month, entries)
        if result:
            manifest[month] = result
            print(f"  {month}: {result['count']} image(s), {result['bytes'] / 1024:.0f} KB")
        else:
            print(f"  {month}: no usable images, skipping zip")

    save_json(MANIFEST_PATH, manifest)


if __name__ == "__main__":
    main()
