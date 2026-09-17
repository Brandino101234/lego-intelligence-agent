"""Zips up full-resolution images for the "Download images" buttons on the
dashboard: one per calendar month, plus one per active BrickLink Designer
Program series.

Calendar sets: pulls each set's full image gallery (box front/back,
in-hand shots, feature call-outs — not just the single cover thumbnail
shown on the dashboard card), collected per-product by
lego_release_calendar_agent.py's Playwright pass, resized via LEGO's own
CDN (upsize_lego_image_url).

BDP finalists: pulls each design's full gallery, collected by
lego_bdp_agent.py. BrickLink's file host has no URL-based resize like
LEGO's CDN does, so these are downsized+recompressed locally with Pillow
instead (see resize_for_zip) — the raw uploads run up to ~1.8MB each at
2048x1536, and a single finalist can have 10 of them.

Each set/design gets its own folder inside its zip. Runs as part of every
scrape (see run_all.py) and writes straight into site/downloads/ — NOT
committed to git (see .gitignore), since GitHub Pages deploys from an
uploaded build artifact (actions/upload-pages-artifact) built from the
local site/ folder, not from what's tracked in the repo, and re-zipping
identical images twice a day would otherwise bloat the repo with
duplicate binary blobs forever. build_dashboard.py reads the manifests
this writes to know which months/series got a real zip before rendering
a button.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import requests
from PIL import Image

from lego_common import DATA_DIR, HEADERS, load_json, save_json, upsize_lego_image_url

ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT / "site" / "downloads"
MANIFEST_PATH = DATA_DIR / "image_zip_manifest.json"
BDP_MANIFEST_PATH = DATA_DIR / "bdp_zip_manifest.json"

# Deliberately smaller than upsize_lego_image_url()'s default (1500/90).
# These zips get deployed with the dashboard and served to whoever clicks
# the button, unlike export_month_images.py's local-only, full-res CLI
# export — a full month of galleries at full res runs 100MB+ per zip
# (confirmed: September alone was 112MB at the default). 1000px/quality 82
# is still ~3x the dashboard's 320px thumbnails and plenty sharp for a
# video overlay, at roughly a third of the file size.
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


def resize_for_zip(image_bytes: bytes) -> bytes | None:
    """BrickLink's file host (unlike LEGO's CDN) has no URL-based resize —
    the raw uploads run ~700KB-1.8MB each at 2048x1536, and a single
    finalist can have 10 of them, so this downsizes+recompresses locally
    instead of shipping the originals wholesale."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        return None
    img.thumbnail((ZIP_IMAGE_SIZE, ZIP_IMAGE_SIZE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=ZIP_IMAGE_QUALITY)
    return out.getvalue()


def build_bdp_zip(series_name: str, entries: list[dict]) -> dict | None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_series = sanitize_filename(series_name).replace(" ", "-").lower()
    zip_path = DOWNLOADS_DIR / f"bdp-{safe_series}.zip"

    entries = sorted(entries, key=lambda e: e.get("name") or "")
    designs_added, images_added = 0, 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for e in entries:
            gallery = e.get("gallery_images") or ([e["image"]] if e.get("image") else [])
            if not gallery:
                continue

            folder = sanitize_filename(e.get("name", "unknown"))
            design_had_image = False
            for i, image_url in enumerate(gallery, start=1):
                resp = requests.get(image_url, headers=HEADERS, timeout=20)
                if resp.status_code != 200:
                    continue
                resized = resize_for_zip(resp.content)
                if not resized:
                    continue
                zf.writestr(f"{folder}/{i:02d}.jpg", resized)
                images_added += 1
                design_had_image = True

            if design_had_image:
                designs_added += 1

    if images_added == 0:
        zip_path.unlink(missing_ok=True)
        return None

    return {
        "file": f"downloads/{zip_path.name}",
        "sets": designs_added,
        "images": images_added,
        "bytes": zip_path.stat().st_size,
    }


def main() -> None:
    calendar = load_json(DATA_DIR / "release_calendar.json", {"months": {}})
    months = calendar.get("months", {})
    bdp = load_json(DATA_DIR / "bdp_finalists.json", {})

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

    by_series: dict[str, list[dict]] = {}
    for entry in bdp.values():
        by_series.setdefault(entry["series_name"], []).append(entry)

    bdp_manifest = {}
    for series_name, entries in by_series.items():
        result = build_bdp_zip(series_name, entries)
        if result:
            bdp_manifest[series_name] = result
            print(f"  BDP {series_name}: {result['images']} image(s) across {result['sets']} design(s), {result['bytes'] / 1024:.0f} KB")
        else:
            print(f"  BDP {series_name}: no usable images, skipping zip")

    save_json(MANIFEST_PATH, manifest)
    save_json(BDP_MANIFEST_PATH, bdp_manifest)


if __name__ == "__main__":
    main()
