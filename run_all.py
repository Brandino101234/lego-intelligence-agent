"""Runs all three scraper agents, then regenerates the dashboard.

This is what the scheduled launchd job actually invokes twice a day. Each
agent already handles its own failures gracefully (best-effort sources,
"leave saved state untouched" on total fetch failure) — this script's job
is just to run them in sequence and make sure one agent's exception doesn't
stop the others from running, then always rebuild the dashboard from
whatever state ended up in data/ afterward.
"""

from __future__ import annotations

import runpy
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

AGENTS = [
    # Calendar first: its crawl builds data/lego_product_images.json (every
    # currently-on-sale product it passes through, not just upcoming ones),
    # which the retirement agent then uses for official LEGO.com images.
    "lego_release_calendar_agent",
    "lego_retirement_agent",
    "lego_new_sets_agent",
    "lego_gwp_agent",
]


def run_module(name: str) -> None:
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
    try:
        runpy.run_module(name, run_name="__main__")
    except Exception:
        print(f"! {name} raised an exception:")
        traceback.print_exc()


def main() -> None:
    sys.path.insert(0, str(ROOT))
    start = datetime.now()
    print(f"Run started {start.isoformat(timespec='seconds')}")

    for agent in AGENTS:
        run_module(agent)

    run_module("build_dashboard")

    print(f"\nRun finished {datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
