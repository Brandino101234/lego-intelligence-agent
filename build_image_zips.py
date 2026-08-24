"""Zips up full-resolution set images for every month on the dashboard,
so the "Download images" button on each month's calendar section has
something to link to.

Pulls each set's full image gallery (box front/back, in-hand shots, feature
call-outs — not just the single cover thumbnail shown on the dashboard
card), collected per-product by lego_release_calendar_agent.py's Playwright
pass. Each set gets its own folder inside the zip.

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

# Deliberately smaller than upsize_lego_image_url()'s default (1500/90).
# These zips get deployed to Netlify and served to whoever clicks the
# button, unlike export_month_images.py's local-only, full-res CLI export
# — a full month of galleries at full res runs 100MB+ per zip (confirmed:
# September alone was 112MB at the default), which is real hosted
# bandwidth on a metered free plan. 1000px/quality 82 is still ~3x the
# dashboard's 320px thumbnails and plenty sharp for a video overlay, at
# roughly a third of the file size.
ZIP_IMAGE_SIZE = 1000
ZIP_IMAGE_QUALITY = 82

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*™®]')


def sanitize_filename(name: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("", name).strip()
    return re.sub(r"\s+", " ", cleaned)


def build_month_zip(month: str, entries: list[dict]) -> dict | None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DOWNLOADS_DIR / f"{month}.zip"

    entries = sorted(entries, key=lambda e: e.get("launch_date") or "")
    sets_added, images_added = 0, 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            gallery = e.get("gallery_images") or ([e["image"]] if e.get("image") else [])
            if not gallery:
                continue

            folder = f"{e.get('set_num', 'unknown')} {sanitize_filename(e.get('name', ''))}"
            set_had_image = False
            for i, image_url in enumerate(gallery, start=1):
                url = upsize_lego_image_url(image_url, size=ZIP_IMAGE_SIZE, quality=ZIP_IMAGE_QUALITY)
                resp = requests.get(url, headers=HEADERS, timeout=20)
                if resp.status_code != 200:
                    continue
                zf.writestr(f"{folder}/{i:02d}.jpg", resp.content)
                images_added += 1
                set_had_image = True

            if set_had_image:
                sets_added += 1

    if images_added == 0:
        zip_path.unlink(missing_ok=True)
        return None

    return {
        "file": f"downloads/{month}.zip",
        "sets": sets_added,
        "images": images_added,
        "bytes": zip_path.stat().st_size,
    }


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
            print(f"  {month}: {result['images']} image(s) across {result['sets']} set(s), {result['bytes'] / 1024:.0f} KB")
        else:
            print(f"  {month}: no usable images, skipping zip")

    save_json(MANIFEST_PATH, manifest)


if __name__ == "__main__":
    main()
