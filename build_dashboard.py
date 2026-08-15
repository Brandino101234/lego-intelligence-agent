"""Generates site/index.html — the LEGO Intelligence Agent dashboard — from
the three data/*.json files that the scraper agents maintain.

Run standalone (`python3 build_dashboard.py`) to re-render from whatever is
currently in data/, or via run_all.py which runs the three scrapers first.
The output is a single self-contained static HTML file (fonts embedded,
no external requests) meant to be served by serve_dashboard.py so the
browser tab can auto-reload and pick up each regeneration.
"""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from pathlib import Path

from lego_common import DATA_DIR, load_json

ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"
SITE_DIR.mkdir(exist_ok=True)
FONTS_CSS = (ROOT / "assets" / "embedded_fonts.css").read_text(encoding="utf-8")

RETIRING_PATH = DATA_DIR / "retiring_sets.json"
NEW_SETS_LOG_PATH = DATA_DIR / "new_sets_changes_log.json"
CALENDAR_PATH = DATA_DIR / "release_calendar.json"
GWP_PATH = DATA_DIR / "gwp.json"

REFRESH_MINUTES = 30
DAILY_RUN_TIMES = ((6, 0), (18, 0))
URGENT_DAYS = 60
SOON_DAYS = 180


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def next_run_after(now: datetime) -> datetime:
    candidates = []
    for h, m in DAILY_RUN_TIMES:
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates)


def month_label(month_key: str) -> tuple[str, str]:
    if month_key == "TBA":
        return "DATE", "TBA"
    d = datetime.strptime(month_key, "%Y-%m")
    return d.strftime("%B").upper(), d.strftime("%Y")


def days_between(d1: date, d2: date) -> int:
    return (d2 - d1).days


# ---------------------------------------------------------------- calendar --

def render_calendar(calendar: dict, today: date) -> tuple[str, str]:
    months = calendar.get("months", {})
    total = sum(len(v) for v in months.values())

    next_entry = None
    next_days = None
    for month_key in sorted(k for k in months if k != "TBA"):
        for entry in months[month_key]:
            if entry.get("launch_date"):
                d = datetime.strptime(entry["launch_date"], "%Y-%m-%d").date()
                if d >= today:
                    next_entry, next_days = entry, days_between(today, d)
                    break
        if next_entry:
            break

    stat = f"{total} set{'s' if total != 1 else ''} tracked"
    if next_entry:
        stat += f" &middot; next in {next_days} day{'s' if next_days != 1 else ''}"

    if not months:
        body = '<p class="empty">No dated upcoming sets yet. LEGO.com\'s &ldquo;Coming soon&rdquo; listing is currently empty or unreachable &mdash; check back after the next scheduled run.</p>'
        return stat, body

    ordered_keys = sorted(k for k in months if k != "TBA") + (["TBA"] if "TBA" in months else [])
    rows = []
    for month_key in ordered_keys:
        entries = months[month_key]
        name, year = month_label(month_key)
        cards = []
        for e in entries:
            launch = e.get("launch_date")
            day_label = datetime.strptime(launch, "%Y-%m-%d").strftime("%b %-d").upper() if launch else "TBA"
            theme = esc(e.get("theme") or "Uncategorized")
            pieces = e.get("pieces")
            piece_badge = (
                f'<div class="badge-circle"><span class="n">{pieces:,}</span><span class="u">PC</span></div>'
                if pieces else ""
            )
            image = e.get("image")
            image_html = (
                f'<img class="cal-card-img" src="{esc(image)}" alt="" loading="lazy">'
                if image else '<div class="cal-card-img cal-card-img-empty"></div>'
            )
            cards.append(f'''
              <a class="cal-card" href="{esc(e.get("url", "#"))}" target="_blank" rel="noopener">
                {image_html}
                <div class="cal-card-top">
                  <span class="cal-card-date">{esc(day_label)}</span>
                  <span class="theme-tag">{theme}</span>
                </div>
                <div class="cal-card-name">{esc(e.get("name"))}</div>
                <div class="cal-card-bottom">
                  {piece_badge}
                  <span class="cal-card-price">{esc(e.get("price") or "&mdash;")}</span>
                </div>
              </a>''')
        rows.append(f'''
          <div class="cal-month">
            <div class="cal-month-label">
              <div class="cal-m">{name}</div>
              <div class="cal-y">{year}</div>
              <div class="cal-count">{len(entries)} SET{"S" if len(entries) != 1 else ""}</div>
            </div>
            <div class="cal-cards">{"".join(cards)}</div>
          </div>''')

    return stat, "".join(rows)


# ------------------------------------------------------------- new arrivals --

def render_new_arrivals(new_log: list) -> tuple[str, str]:
    latest_by_set: dict[str, dict] = {}
    for entry in new_log:
        set_num = entry["set_num"]
        if set_num not in latest_by_set or entry["timestamp"] > latest_by_set[set_num]["timestamp"]:
            latest_by_set[set_num] = entry

    items = sorted(
        latest_by_set.values(),
        key=lambda e: (e["timestamp"], e.get("launch_date") or "", e["name"]),
        reverse=True,
    )
    total = len(items)

    most_recent_ts = items[0]["timestamp"][:10] if items else None
    today_count = sum(1 for e in items if e["timestamp"][:10] == most_recent_ts) if items else 0
    stat = f"{total} tracked"
    if items:
        stat += f" &middot; {today_count} in latest run"

    if not items:
        return stat, '<p class="empty">No new sets tracked yet &mdash; run the new-sets agent to build a baseline.</p>'

    rows = []
    for e in items:
        launch = e.get("launch_date")
        launch_label = datetime.strptime(launch, "%Y-%m-%d").strftime("%b %-d, %Y") if launch else "date TBA"
        theme = esc(e.get("theme") or "Uncategorized")
        rows.append(f'''
          <div class="feed-row">
            <div class="feed-main">
              <span class="feed-name">{esc(e.get("name"))}</span>
              <span class="theme-tag">{theme}</span>
            </div>
            <div class="feed-meta">
              <span class="mono feed-setnum">#{esc(e.get("set_num"))}</span>
              <span class="mono feed-date">{esc(launch_label)}</span>
            </div>
          </div>''')

    return stat, f'<div class="feed">{"".join(rows)}</div>'


# ------------------------------------------------------------------ retiring --

def render_retiring(retiring: dict, today: date) -> tuple[str, str]:
    flagged = [v for v in retiring.values() if v.get("retiring_soon")]
    confirmed = sum(1 for v in flagged if v.get("brickfanatics_confirmed"))
    total_tracked = len(retiring)

    stat = f"{len(flagged)} flagged of {total_tracked} tracked &middot; {confirmed} confirmed by both sources"

    if not flagged:
        return stat, '<p class="empty">Nothing currently flagged as retiring soon.</p>'

    def sort_key(v):
        d = v.get("retirement_date")
        return (d is None, d or "", v["name"])

    ordered = sorted(flagged, key=sort_key)

    rows = []
    for v in ordered:
        d_raw = v.get("retirement_date")
        urgency_class = "neutral"
        date_label = v.get("retirement_date_raw") or "unknown"
        if d_raw:
            d = datetime.strptime(d_raw, "%Y-%m-%d").date()
            delta = days_between(today, d)
            if delta <= URGENT_DAYS:
                urgency_class = "urgent"
            elif delta <= SOON_DAYS:
                urgency_class = "soon"
            date_label = d.strftime("%b %-d, %Y")

        confirm_mark = "&#10003;&#10003;" if v.get("brickfanatics_confirmed") else "&#10003;"
        confirm_title = "Confirmed by BrickRanker + Brick Fanatics" if v.get("brickfanatics_confirmed") else "BrickRanker only"

        image = v.get("image")
        image_html = f'<img class="row-thumb" src="{esc(image)}" alt="" loading="lazy">' if image else '<div class="row-thumb row-thumb-empty"></div>'

        rows.append(f'''
          <tr>
            <td class="thumb-cell">{image_html}</td>
            <td class="mono">{esc(v.get("set_num"))}</td>
            <td>{esc(v.get("name"))}</td>
            <td><span class="theme-tag">{esc(v.get("theme"))}</span></td>
            <td><span class="date-pill {urgency_class} mono">{esc(date_label)}</span></td>
            <td class="mono confirm" title="{confirm_title}">{confirm_mark}</td>
          </tr>''')

    table = f'''
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Set</th>
              <th>Name</th>
              <th>Theme</th>
              <th>Retiring</th>
              <th title="Source confirmation">Src</th>
            </tr>
          </thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>'''

    return stat, table


# ------------------------------------------------------------------------ gwp --

def render_gwp(gwp: dict, today: date) -> tuple[str, str]:
    stat = f"{len(gwp)} active"

    if not gwp:
        return stat, '<p class="empty">No gift-with-purchase promotions currently active.</p>'

    def sort_key(g):
        d = g.get("end_date")
        return (d is None, d or "", g["name"])

    ordered = sorted(gwp.values(), key=sort_key)

    cards = []
    for g in ordered:
        end_date = g.get("end_date")
        days_left = None
        end_label = "ends date unknown"
        if end_date:
            d = datetime.strptime(end_date, "%Y-%m-%d").date()
            days_left = days_between(today, d)
            end_label = f"ends {d.strftime('%b %-d')}" if days_left > 0 else "ends today"

        urgency_class = "neutral"
        if days_left is not None:
            urgency_class = "urgent" if days_left <= 3 else ("soon" if days_left <= 14 else "neutral")

        insiders_badge = '<span class="theme-tag insiders">INSIDERS ONLY</span>' if g.get("insiders_only") else ""
        image = g.get("image")
        image_html = f'<img class="gwp-img" src="{esc(image)}" alt="" loading="lazy">' if image else '<div class="gwp-img gwp-img-empty"></div>'

        cards.append(f'''
          <a class="gwp-card" href="{esc(g.get("url", "#"))}" target="_blank" rel="noopener">
            {image_html}
            <div class="gwp-card-body">
              <div class="gwp-card-top">
                <span class="date-pill {urgency_class} mono">{esc(end_label)}</span>
                {insiders_badge}
              </div>
              <div class="gwp-card-name">{esc(g.get("name"))}</div>
              <div class="gwp-card-qualify">{esc(g.get("qualifying_text"))}</div>
            </div>
          </a>''')

    return stat, f'<div class="gwp-cards">{"".join(cards)}</div>'


# ------------------------------------------------------------------ template --

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LEGO Intelligence Agent</title>
<style>
{fonts_css}

:root {{
  --paper: #F3EEE2;
  --paper-2: #FFFFFF;
  --ink: #1A1710;
  --ink-muted: #6B6457;
  --ink-faint: #A39C8A;
  --line: #DDD5C1;
  --blue: #0055BF;
  --blue-soft: #DCE7F7;
  --gold: #A9740A;
  --gold-fill: #F2B400;
  --gold-soft: #FBEFD2;
  --red: #C91A09;
  --red-soft: #FAE1DC;
  --green: #237A3F;
  --green-soft: #DEEFE1;
  --shadow: 0 1px 0 rgba(26,23,16,0.05);
}}

@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --paper: #17140F;
    --paper-2: #211D16;
    --ink: #F2ECDE;
    --ink-muted: #B0A891;
    --ink-faint: #756D59;
    --line: #3A3527;
    --blue: #6FA1FF;
    --blue-soft: #1C2A40;
    --gold: #F2B400;
    --gold-fill: #F2B400;
    --gold-soft: #3A2E10;
    --red: #FF6A55;
    --red-soft: #3A1A14;
    --green: #4CBE73;
    --green-soft: #17301F;
    --shadow: 0 1px 0 rgba(0,0,0,0.4);
  }}
}}
:root[data-theme="dark"] {{
  --paper: #17140F;
  --paper-2: #211D16;
  --ink: #F2ECDE;
  --ink-muted: #B0A891;
  --ink-faint: #756D59;
  --line: #3A3527;
  --blue: #6FA1FF;
  --blue-soft: #1C2A40;
  --gold: #F2B400;
  --gold-fill: #F2B400;
  --gold-soft: #3A2E10;
  --red: #FF6A55;
  --red-soft: #3A1A14;
  --green: #4CBE73;
  --green-soft: #17301F;
  --shadow: 0 1px 0 rgba(0,0,0,0.4);
}}

* {{ box-sizing: border-box; }}

body {{
  margin: 0;
  background-color: var(--paper);
  background-image:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 28px 28px;
  background-attachment: fixed;
  color: var(--ink);
  font-family: 'Plex Sans Var', -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
}}

.display {{ font-family: 'Rubik Var', -apple-system, sans-serif; }}
.mono {{ font-family: 'Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }}

a {{ color: inherit; }}

/* ---- status bar ---- */
.statusbar {{
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 20px;
  background: var(--paper-2);
  border-bottom: 2px solid var(--ink);
  font-size: 12px;
}}

.wordmark {{
  font-family: 'Rubik Var', sans-serif;
  font-weight: 900;
  letter-spacing: 0.02em;
  font-size: 14px;
}}
.wordmark span {{ color: var(--red); }}

.status-right {{
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--ink-muted);
}}

.status-right .mono {{ color: var(--ink); }}

button.refresh {{
  font-family: 'Plex Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  background: var(--ink);
  color: var(--paper);
  border: none;
  border-radius: 4px;
  padding: 6px 10px;
  cursor: pointer;
}}
button.refresh:hover {{ opacity: 0.85; }}
button.refresh:focus-visible {{ outline: 2px solid var(--blue); outline-offset: 2px; }}

/* ---- shell ---- */
.shell {{
  max-width: 1080px;
  margin: 0 auto;
  padding: 28px 20px 80px;
  display: grid;
  grid-template-columns: 168px 1fr;
  gap: 32px;
}}

nav.chapters {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: sticky;
  top: 62px;
  align-self: start;
}}

.chapter-btn {{
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  background: none;
  border: none;
  border-left: 3px solid var(--line);
  padding: 8px 0 8px 14px;
  cursor: pointer;
  font-family: inherit;
  color: var(--ink-muted);
}}

.chapter-btn .num {{
  font-family: 'Plex Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
}}

.chapter-btn .name {{
  font-family: 'Rubik Var', sans-serif;
  font-weight: 700;
  font-size: 14.5px;
  color: var(--ink);
}}

.chapter-btn[data-accent="blue"].active {{ border-left-color: var(--blue); }}
.chapter-btn[data-accent="gold"].active {{ border-left-color: var(--gold-fill); }}
.chapter-btn[data-accent="red"].active {{ border-left-color: var(--red); }}
.chapter-btn[data-accent="green"].active {{ border-left-color: var(--green); }}
.chapter-btn[data-accent="blue"].active .num {{ color: var(--blue); }}
.chapter-btn[data-accent="gold"].active .num {{ color: var(--gold); }}
.chapter-btn[data-accent="red"].active .num {{ color: var(--red); }}
.chapter-btn[data-accent="green"].active .num {{ color: var(--green); }}
.chapter-btn:not(.active) {{ opacity: 0.6; }}
.chapter-btn:hover {{ opacity: 1; }}

main {{ min-width: 0; }}

section.panel {{ display: none; }}
section.panel.active {{ display: block; }}

.panel-head {{
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  border-bottom: 2px solid var(--ink);
  padding-bottom: 14px;
  margin-bottom: 24px;
}}

.panel-head .title-block {{
  display: flex;
  align-items: baseline;
  gap: 12px;
}}

.panel-num {{
  font-family: 'Rubik Var', sans-serif;
  font-weight: 900;
  font-size: 34px;
  line-height: 1;
}}
section.panel[data-accent="blue"] .panel-num {{ color: var(--blue); }}
section.panel[data-accent="gold"] .panel-num {{ color: var(--gold-fill); -webkit-text-stroke: 1px var(--ink); }}
section.panel[data-accent="red"] .panel-num {{ color: var(--red); }}
section.panel[data-accent="green"] .panel-num {{ color: var(--green); }}

.panel-title h2 {{
  font-family: 'Rubik Var', sans-serif;
  font-weight: 700;
  font-size: 22px;
  margin: 0;
  text-wrap: balance;
}}
.panel-title p {{
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--ink-muted);
}}

.panel-stat {{
  font-family: 'Plex Mono', monospace;
  font-size: 12.5px;
  color: var(--ink-muted);
  text-align: right;
  white-space: nowrap;
}}

.empty {{
  color: var(--ink-muted);
  font-size: 14px;
  padding: 24px 0;
}}

/* ---- theme tag ---- */
.theme-tag {{
  display: inline-flex;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  padding: 2px 8px;
  border-radius: 100px;
  background: var(--paper);
  border: 1px solid var(--line);
  color: var(--ink-muted);
  white-space: nowrap;
}}

/* ---- calendar ---- */
.cal-month {{ display: grid; grid-template-columns: 90px 1fr; gap: 16px; margin-bottom: 28px; }}
.cal-month-label {{ padding-top: 2px; }}
.cal-m {{ font-family: 'Rubik Var', sans-serif; font-weight: 700; font-size: 13.5px; }}
.cal-y {{ font-family: 'Plex Mono', monospace; font-size: 11px; color: var(--ink-faint); }}
.cal-count {{ margin-top: 6px; font-size: 10px; font-weight: 700; letter-spacing: 0.04em; color: var(--ink-muted); }}

.cal-cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }}

a.cal-card {{
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--paper-2);
  border: 2px solid var(--line);
  border-radius: 8px;
  padding: 13px 14px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.12s ease, transform 0.12s ease;
}}
a.cal-card:hover {{ border-color: var(--blue); transform: translateY(-1px); }}
a.cal-card:focus-visible {{ outline: 2px solid var(--blue); outline-offset: 2px; }}

.cal-card-img {{ width: 100%; height: 120px; object-fit: contain; background: var(--paper); border: 1px solid var(--line); border-radius: 6px; }}
.cal-card-img-empty {{ background: var(--paper); }}

.cal-card-top {{ display: flex; justify-content: space-between; align-items: center; }}
.cal-card-date {{
  font-family: 'Plex Mono', monospace;
  font-size: 11px;
  font-weight: 600;
  color: var(--blue);
}}
.cal-card-name {{ font-size: 14px; font-weight: 600; line-height: 1.32; }}
.cal-card-bottom {{ display: flex; justify-content: space-between; align-items: center; margin-top: auto; }}
.cal-card-price {{ font-family: 'Plex Mono', monospace; font-size: 13px; font-weight: 600; }}

.badge-circle {{
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid var(--ink);
  line-height: 1;
}}
.badge-circle .n {{ font-family: 'Plex Mono', monospace; font-size: 11px; font-weight: 700; }}
.badge-circle .u {{ font-size: 7px; font-weight: 700; letter-spacing: 0.04em; color: var(--ink-muted); margin-top: 1px; }}

/* ---- new arrivals feed ---- */
.feed {{ display: flex; flex-direction: column; max-height: 68vh; overflow-y: auto; border: 2px solid var(--line); border-radius: 8px; background: var(--paper-2); }}
.feed-row {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}}
.feed-row:last-child {{ border-bottom: none; }}
.feed-main {{ display: flex; align-items: center; gap: 10px; min-width: 0; }}
.feed-name {{ font-size: 13.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.feed-meta {{ display: flex; gap: 14px; font-size: 11.5px; color: var(--ink-muted); flex-shrink: 0; }}
.feed-setnum {{ color: var(--gold); }}

/* ---- retiring table ---- */
.table-wrap {{ max-height: 68vh; overflow: auto; border: 2px solid var(--line); border-radius: 8px; background: var(--paper-2); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
thead th {{
  position: sticky;
  top: 0;
  background: var(--paper-2);
  text-align: left;
  font-size: 10.5px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--ink-muted);
  padding: 10px 14px;
  border-bottom: 2px solid var(--ink);
}}
tbody td {{ padding: 8px 14px; border-bottom: 1px solid var(--line); vertical-align: middle; }}
tbody tr:last-child td {{ border-bottom: none; }}
tbody tr:hover {{ background: var(--paper); }}

td.thumb-cell {{ padding: 6px 0 6px 14px; width: 44px; }}
.row-thumb {{ width: 36px; height: 36px; object-fit: contain; background: var(--paper); border: 1px solid var(--line); border-radius: 5px; display: block; }}
.row-thumb-empty {{ background: var(--paper); }}

.date-pill {{
  display: inline-flex;
  font-size: 11.5px;
  font-weight: 600;
  padding: 3px 9px;
  border-radius: 100px;
  border: 1px solid var(--line);
  white-space: nowrap;
}}
.date-pill.urgent {{ background: var(--red-soft); border-color: var(--red); color: var(--red); }}
.date-pill.soon {{ background: var(--gold-soft); border-color: var(--gold-fill); color: var(--gold); }}
.date-pill.neutral {{ color: var(--ink-muted); }}
td.confirm {{ text-align: center; letter-spacing: 1px; }}

/* ---- gift with purchase ---- */
.gwp-cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }}

a.gwp-card {{
  display: flex;
  gap: 14px;
  background: var(--paper-2);
  border: 2px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.12s ease, transform 0.12s ease;
}}
a.gwp-card:hover {{ border-color: var(--green); transform: translateY(-1px); }}
a.gwp-card:focus-visible {{ outline: 2px solid var(--green); outline-offset: 2px; }}

.gwp-img {{ width: 72px; height: 72px; object-fit: contain; background: var(--paper); border: 1px solid var(--line); border-radius: 6px; flex-shrink: 0; }}
.gwp-img-empty {{ background: var(--paper); }}

.gwp-card-body {{ display: flex; flex-direction: column; gap: 6px; min-width: 0; }}
.gwp-card-top {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }}
.theme-tag.insiders {{ background: var(--green-soft); border-color: var(--green); color: var(--green); }}
.gwp-card-name {{ font-size: 14px; font-weight: 600; line-height: 1.3; }}
.gwp-card-qualify {{ font-size: 12px; color: var(--ink-muted); }}

footer.page-footer {{
  max-width: 1080px;
  margin: 40px auto 0;
  padding: 16px 20px 0;
  border-top: 1px solid var(--line);
  font-size: 11.5px;
  color: var(--ink-faint);
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}}

@media (max-width: 760px) {{
  .shell {{ grid-template-columns: 1fr; gap: 18px; }}
  nav.chapters {{ position: static; flex-direction: row; overflow-x: auto; border-bottom: 2px solid var(--ink); padding-bottom: 8px; }}
  .chapter-btn {{ border-left: none; border-bottom: 3px solid var(--line); padding: 4px 12px 8px 0; flex-shrink: 0; }}
  .chapter-btn[data-accent="blue"].active {{ border-bottom-color: var(--blue); }}
  .chapter-btn[data-accent="gold"].active {{ border-bottom-color: var(--gold-fill); }}
  .chapter-btn[data-accent="red"].active {{ border-bottom-color: var(--red); }}
  .chapter-btn[data-accent="green"].active {{ border-bottom-color: var(--green); }}
  .statusbar {{ flex-wrap: wrap; }}
}}
</style>
</head>
<body>

<div class="statusbar">
  <div class="wordmark">LEGO<span>&bull;</span>INTEL</div>
  <div class="status-right">
    <span>Updated <span class="mono">{updated_label}</span></span>
    <span>Next run <span class="mono">{next_run_label}</span></span>
    <button class="refresh" onclick="location.reload()">Refresh</button>
  </div>
</div>

<div class="shell">
  <nav class="chapters">
    <button class="chapter-btn active" data-accent="blue" data-target="panel-calendar" onclick="showPanel(this)">
      <span class="num">01</span><span class="name">Calendar</span>
    </button>
    <button class="chapter-btn" data-accent="gold" data-target="panel-new" onclick="showPanel(this)">
      <span class="num">02</span><span class="name">New arrivals</span>
    </button>
    <button class="chapter-btn" data-accent="red" data-target="panel-retiring" onclick="showPanel(this)">
      <span class="num">03</span><span class="name">Retiring soon</span>
    </button>
    <button class="chapter-btn" data-accent="green" data-target="panel-gwp" onclick="showPanel(this)">
      <span class="num">04</span><span class="name">Gift w/ purchase</span>
    </button>
  </nav>

  <main>
    <section class="panel active" data-accent="blue" id="panel-calendar">
      <div class="panel-head">
        <div class="title-block">
          <div class="panel-num display">01</div>
          <div class="panel-title">
            <h2>Release calendar</h2>
            <p>Upcoming sets from LEGO.com&rsquo;s own &ldquo;Coming soon&rdquo; listing, by month.</p>
          </div>
        </div>
        <div class="panel-stat">{calendar_stat}</div>
      </div>
      {calendar_body}
    </section>

    <section class="panel" data-accent="gold" id="panel-new">
      <div class="panel-head">
        <div class="title-block">
          <div class="panel-num display">02</div>
          <div class="panel-title">
            <h2>New arrivals</h2>
            <p>Sets newly added to Brickset&rsquo;s database, most recent first.</p>
          </div>
        </div>
        <div class="panel-stat">{new_stat}</div>
      </div>
      {new_body}
    </section>

    <section class="panel" data-accent="red" id="panel-retiring">
      <div class="panel-head">
        <div class="title-block">
          <div class="panel-num display">03</div>
          <div class="panel-title">
            <h2>Retiring soon</h2>
            <p>Flagged by BrickRanker&rsquo;s tracker, cross-checked against Brick Fanatics.</p>
          </div>
        </div>
        <div class="panel-stat">{retiring_stat}</div>
      </div>
      {retiring_body}
    </section>

    <section class="panel" data-accent="green" id="panel-gwp">
      <div class="panel-head">
        <div class="title-block">
          <div class="panel-num display">04</div>
          <div class="panel-title">
            <h2>Gift with purchase</h2>
            <p>Currently active GWP promotions, straight from LEGO.com. Future GWPs aren&rsquo;t announced in advance by LEGO or reliably tracked anywhere, so this is current-only.</p>
          </div>
        </div>
        <div class="panel-stat">{gwp_stat}</div>
      </div>
      {gwp_body}
    </section>
  </main>
</div>

<footer class="page-footer">
  <span>Sources: lego.com &middot; brickset.com &middot; brickranker.com &middot; brickfanatics.com</span>
  <span>Auto-refreshes every {refresh_minutes} min &middot; data regenerates 6am &amp; 6pm daily</span>
</footer>

<script>
function showPanel(btn) {{
  document.querySelectorAll('.chapter-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(btn.dataset.target).classList.add('active');
}}
setTimeout(() => location.reload(), {refresh_minutes} * 60 * 1000);
</script>
</body>
</html>
"""


def build() -> Path:
    retiring = load_json(RETIRING_PATH, {})
    new_log = load_json(NEW_SETS_LOG_PATH, [])
    calendar = load_json(CALENDAR_PATH, {"months": {}})
    gwp = load_json(GWP_PATH, {})

    now = datetime.now()
    today = now.date()

    calendar_stat, calendar_body = render_calendar(calendar, today)
    new_stat, new_body = render_new_arrivals(new_log)
    retiring_stat, retiring_body = render_retiring(retiring, today)
    gwp_stat, gwp_body = render_gwp(gwp, today)

    page = PAGE_TEMPLATE.format(
        fonts_css=FONTS_CSS,
        updated_label=now.strftime("%b %-d, %-I:%M %p"),
        next_run_label=next_run_after(now).strftime("%-I:%M %p"),
        calendar_stat=calendar_stat,
        calendar_body=calendar_body,
        new_stat=new_stat,
        new_body=new_body,
        retiring_stat=retiring_stat,
        retiring_body=retiring_body,
        gwp_stat=gwp_stat,
        gwp_body=gwp_body,
        refresh_minutes=REFRESH_MINUTES,
    )

    out_path = SITE_DIR / "index.html"
    out_path.write_text(page, encoding="utf-8")
    print(f"Wrote {out_path} ({len(page):,} bytes)")
    return out_path


if __name__ == "__main__":
    build()
