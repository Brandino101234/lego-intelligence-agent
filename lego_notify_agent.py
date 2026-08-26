"""Sends an email via Resend when the other agents' change logs picked up
something worth knowing about since the last notification.

Run this after the other agents (see run_all.py's AGENTS order) so their
change logs are fresh.

Maintains its own state file (data/last_notified.json) recording the cutoff
timestamp of the last notification, so re-runs only pick up genuinely new
changes instead of re-announcing history. On the very first run there's no
prior cutoff, so this just establishes a baseline (now) and sends nothing —
otherwise every change logged during earlier development/testing would show
up as one giant email.

Not every logged change type is worth emailing about (e.g. the calendar
agent's "month_changed" is usually just a date correction, not news) — see
NOTABLE_TYPES below for what actually triggers one.

Requires RESEND_API_KEY in the environment. Uses Resend's shared
onboarding@resend.dev sender, which works without verifying a domain but
can only deliver to the email address the Resend account itself was signed
up with.
"""

from __future__ import annotations

import html
import os

import requests

from lego_common import DATA_DIR, load_json, save_json, now_iso

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_URL = "https://api.resend.com/emails"
FROM_EMAIL = "LEGO Intel <onboarding@resend.dev>"
TO_EMAIL = "brannew110@gmail.com"
DASHBOARD_URL = "https://brandino101234.github.io/lego-intelligence-agent/"

STATE_PATH = DATA_DIR / "last_notified.json"

LOG_SOURCES = {
    "calendar": DATA_DIR / "release_calendar_changes_log.json",
    "retiring": DATA_DIR / "retiring_changes_log.json",
    "gwp": DATA_DIR / "gwp_changes_log.json",
}

NOTABLE_TYPES = {
    "calendar": {"added_to_calendar"},
    "retiring": {"newly_flagged", "confirmed_retired"},
    "gwp": {"gwp_started"},
}

SECTION_LABEL = {
    "calendar": "Release calendar",
    "retiring": "Retiring soon",
    "gwp": "Gift with purchase",
}
SECTION_COLOR = {
    "calendar": "#0055BF",
    "retiring": "#C91A09",
    "gwp": "#237A3F",
}

MAX_NAMES_PER_SECTION = 8


def collect_notable_changes(since: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for source, path in LOG_SOURCES.items():
        log = load_json(path, [])
        notable = NOTABLE_TYPES[source]
        for entry in log:
            if entry.get("timestamp", "") <= since or entry.get("type") not in notable:
                continue
            grouped.setdefault(source, []).append(entry)
    return grouped


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def format_email(grouped: dict[str, list[dict]]) -> tuple[str, str]:
    total = sum(len(v) for v in grouped.values())
    subject = f"LEGO Intel: {total} update{'s' if total != 1 else ''}"

    sections_html = []
    for source in ("calendar", "retiring", "gwp"):
        entries = grouped.get(source)
        if not entries:
            continue
        color = SECTION_COLOR[source]
        items = "".join(f"<li style='margin:4px 0'>{esc(e.get('name'))}</li>" for e in entries[:MAX_NAMES_PER_SECTION])
        more = ""
        if len(entries) > MAX_NAMES_PER_SECTION:
            more = f"<div style='color:#666;font-size:13px;margin-top:4px'>…and {len(entries) - MAX_NAMES_PER_SECTION} more</div>"
        sections_html.append(f'''
          <div style="margin-bottom:20px">
            <div style="font-weight:700;color:{color};font-size:15px;margin-bottom:6px">
              {esc(SECTION_LABEL[source])} ({len(entries)})
            </div>
            <ul style="margin:0;padding-left:20px;font-size:14px;color:#222">{items}</ul>
            {more}
          </div>''')

    body = f'''
      <div style="font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:520px;margin:0 auto">
        <h2 style="margin:0 0 16px">{esc(subject)}</h2>
        {"".join(sections_html)}
        <a href="{DASHBOARD_URL}" style="display:inline-block;margin-top:8px;padding:10px 16px;background:#1A1710;color:#fff;text-decoration:none;border-radius:6px;font-size:14px">
          View dashboard
        </a>
      </div>'''

    return subject, body


def send_email(subject: str, html_body: str) -> bool:
    if not RESEND_API_KEY:
        print("  ! RESEND_API_KEY not set — can't send email")
        return False

    try:
        resp = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": FROM_EMAIL,
                "to": [TO_EMAIL],
                "subject": subject,
                "html": html_body,
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"  ! Resend returned {resp.status_code}: {resp.text[:300]}")
        return resp.status_code < 300
    except requests.RequestException as exc:
        print(f"  ! failed to send email: {exc}")
        return False


def main() -> None:
    state = load_json(STATE_PATH, {})
    last_notified = state.get("last_notified")

    if last_notified is None:
        print("No prior notification state — establishing baseline, nothing sent this run.")
        save_json(STATE_PATH, {"last_notified": now_iso()})
        return

    grouped = collect_notable_changes(last_notified)
    total = sum(len(v) for v in grouped.values())

    if total == 0:
        print("Nothing notification-worthy since last check.")
        save_json(STATE_PATH, {"last_notified": now_iso()})
        return

    subject, body = format_email(grouped)
    print(f"Sending email: {subject}")
    if send_email(subject, body):
        print("  sent.")
        save_json(STATE_PATH, {"last_notified": now_iso()})
    else:
        print("  send failed — will retry these changes next run (state not advanced).")


if __name__ == "__main__":
    main()
