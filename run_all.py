"""Runs all three scraper agents, then regenerates the dashboard.

This is what the scheduled launchd job actually invokes twice a day. Each
agent already handles its own failures gracefully (best-effort sources,
"leave saved state untouched" on total fetch failure) — this script's job
is just to run them in sequence and make sure one agent's exception doesn't
stop the others from running, then always rebuild the dashboard from
whatever state ended up in data/ afterward.

Each step runs as a genuinely separate OS process (subprocess.Popen with
its own process group) with a hard wall-clock budget, rather than in-
process via runpy — confirmed necessary in production: a step hanging (or
just going pathologically slow — e.g. the release calendar's theme crawl
retrying every request for hours under degraded network conditions) with
zero output for the *entire* run before GitHub Actions' own 6-hour job
ceiling force-cancelled it, four separate times, is what this guards
against. A timeout here kills the whole process tree via killpg and moves
on to the next step instead of blocking the rest of the pipeline (data
commit, dashboard rebuild, Pages deploy) on one stuck step."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Generous relative to normal runtimes (the full pipeline usually finishes
# in ~10-15 min) so this only ever bites when something's actually stuck,
# not on ordinary slowness.
STEP_TIME_BUDGET_SECONDS = 30 * 60

# The calendar agent is consistently the heaviest step — a full ~76-theme
# site crawl plus a real-browser visit to every tracked product — and has
# been observed hitting even a 45-minute budget under nothing worse than
# ordinary network slowness (confirmed in production: a full run watched
# live start-to-finish with zero sleep/wake events on the Mac the entire
# time still didn't finish in 45 min). When that happens the step gets
# killed before its own save_json() call, so already-released sets don't
# drop off the calendar until a run actually finishes — not a filtering
# bug, just a starved-for-time crawl. Paired with tightening
# enrich_worker.py's own per-page timeout (30s -> 15s, since that's what
# scales linearly with tracked-set count and was the likely real driver),
# this should mostly be moot now — but still generous headroom over the
# lighter agents in case it isn't.
STEP_TIME_BUDGETS = {
    "lego_release_calendar_agent": 75 * 60,
}

AGENTS = [
    # Calendar first: its crawl builds data/lego_product_images.json (every
    # currently-on-sale product it passes through, not just upcoming ones),
    # which the retirement agent then uses for official LEGO.com images.
    "lego_release_calendar_agent",
    "lego_retirement_agent",
    "lego_gwp_agent",
    # Notify last, so it sees every other agent's freshly-written change log.
    "lego_notify_agent",
]


def run_module(name: str) -> None:
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}", flush=True)
    budget = STEP_TIME_BUDGETS.get(name, STEP_TIME_BUDGET_SECONDS)

    script_path = ROOT / f"{name}.py"
    # -u: unbuffered stdout. Without it, Python block-buffers output that
    # isn't attached to a real terminal (true for both a subprocess here
    # and GitHub Actions' own log capture) — confirmed to hide genuine,
    # possibly-slow-but-real progress entirely until either the buffer
    # fills or the process exits, which made two separate hangs much
    # harder to diagnose than they needed to be (a step's own step log
    # showed *zero* output across a full 6 hours, even though it almost
    # certainly wasn't sitting fully idle that whole time).
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script_path)], cwd=str(ROOT), start_new_session=True
    )
    try:
        proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        print(
            f"! {name} hit its {budget // 60}-minute time budget — "
            f"killing it and moving on to the next step",
            flush=True,
        )
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
        proc.wait()
        return

    if proc.returncode != 0:
        print(f"! {name} exited with code {proc.returncode}", flush=True)


def main() -> None:
    start = datetime.now()
    print(f"Run started {start.isoformat(timespec='seconds')}", flush=True)

    for agent in AGENTS:
        run_module(agent)

    run_module("build_image_zips")
    run_module("build_dashboard")

    print(f"\nRun finished {datetime.now().isoformat(timespec='seconds')}", flush=True)


if __name__ == "__main__":
    main()
