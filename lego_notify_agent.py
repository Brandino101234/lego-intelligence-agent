"""Sends a push notification via ntfy.sh when the other agents' change logs
picked up something worth knowing about since the last notification.

ntfy is free and needs no account: pick a topic name, install the ntfy app
(iOS/Android/web) or subscribe at ntfy.sh/<topic>, and anything posted here
shows up as a push notification. Run this after the other agents (see
run_all.py's AGENTS order) so their change logs are fresh.

Maintains its own state file (data/last_notified.json) recording the cutoff
timestamp of the last notification, so re-runs only pick up genuinely new
changes instead of re-announcing history. On the very first run there's no
prior cutoff, so this just establishes a baseline (now) and sends nothing —
otherwise every change logged during earlier development/testing would show
up as one giant notification.

Not every logged change type is worth a notification (e.g. the calendar
agent's "month_changed" is usually just a date correction, not news) — see
NOTABLE_TYPES below for what actually triggers one.
"""

from __future__ import annotations

import requests

from lego_common import DATA_DIR, load_json, save_json, now_iso

NTFY_TOPIC = "lego-intel-4e111a"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
DASHBOARD_URL = "https://lego-intelligence-agent.netlify.app"

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
SECTION_TAG = {
    "calendar": "calendar",
    "retiring": "rotating_light",
    "gwp": "gift",
}

MAX_NAMES_PER_SECTION = 5


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


def format_notification(grouped: dict[str, list[dict]]) -> tuple[str, str, list[str]]:
    total = sum(len(v) for v in grouped.values())
    title = f"LEGO Intel: {total} update{'s' if total != 1 else ''}"

    lines = []
    tags = []
    for source in ("calendar", "retiring", "gwp"):
        entries = grouped.get(source)
        if not entries:
            continue
        tags.append(SECTION_TAG[source])
        lines.append(f"{SECTION_LABEL[source]} ({len(entries)}):")
        for e in entries[:MAX_NAMES_PER_SECTION]:
            lines.append(f"  • {e.get('name')}")
        if len(entries) > MAX_NAMES_PER_SECTION:
            lines.append(f"  …and {len(entries) - MAX_NAMES_PER_SECTION} more")
        lines.append("")

    return title, "\n".join(lines).strip(), tags


def send_ntfy(title: str, body: str, tags: list[str]) -> bool:
    try:
        resp = requests.post(
            NTFY_URL,
            data=body.encode("utf-8"),
            headers={
                "Title": title,
                "Tags": ",".join(tags) if tags else "bricks",
                "Click": DASHBOARD_URL,
            },
            timeout=15,
        )
        return resp.status_code == 200
    except requests.RequestException as exc:
        print(f"  ! failed to send notification: {exc}")
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

    title, body, tags = format_notification(grouped)
    print(f"Sending notification: {title}")
    if send_ntfy(title, body, tags):
        print("  sent.")
        save_json(STATE_PATH, {"last_notified": now_iso()})
    else:
        print("  send failed — will retry these changes next run (state not advanced).")


if __name__ == "__main__":
    main()
