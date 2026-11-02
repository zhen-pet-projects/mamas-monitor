#!/usr/bin/env python3
"""
Watch Mama's Fish House (SevenRooms) for open tables and push alerts to your phone via ntfy.

How it works
  * Calls the same public JSON endpoint the SevenRooms booking widget uses.
  * A slot is open when its "type" is "book"; fully booked slots come back as "request".
  * Remembers what was open last time (state.json) and only alerts on NEWLY opened slots.
  * Sends a daily "still watching" heartbeat, alerts if the endpoint keeps failing,
    and goes quiet once the last date has passed.

All settings come from environment variables (see CONFIG below). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- CONFIG
VENUE = os.environ.get("VENUE", "mamasfishhouserestaurantinn")
PARTY_SIZE = int(os.environ.get("PARTY_SIZE", "2"))
START_DATE = date.fromisoformat(os.environ.get("START_DATE", "2026-11-22"))
END_DATE = date.fromisoformat(os.environ.get("END_DATE", "2026-11-25"))  # inclusive
# Optional time window in 24h "HH:MM" (restaurant local time). Empty = any time.
EARLIEST = os.environ.get("EARLIEST_TIME", "").strip()
LATEST = os.environ.get("LATEST_TIME", "").strip()

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
HEARTBEAT_HOUR = int(os.environ.get("HEARTBEAT_HOUR", "9"))  # in YOUR_TZ
YOUR_TZ = ZoneInfo(os.environ.get("YOUR_TZ", "America/Chicago"))
VENUE_TZ = ZoneInfo("Pacific/Honolulu")
ERROR_ALERT_AFTER = 3  # consecutive failed checks before alerting you

API = "https://www.sevenrooms.com/api-yoa/availability/ng/widget/range"
MAX_DAYS_PER_CALL = 3  # SevenRooms rejects num_days > 3 ("invalid num_days")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def booking_link(d: str) -> str:
    q = urllib.parse.urlencode({"date": d, "party_size": PARTY_SIZE})
    return f"https://www.sevenrooms.com/reservations/{VENUE}?{q}"


# ---------------------------------------------------------------- FETCH
def fetch_range(start: date, num_days: int) -> dict:
    params = {
        "venue": VENUE,
        "time_slot": "6:00 PM",
        "party_size": PARTY_SIZE,
        "halo_size_interval": 100,  # wide halo = whole day returned
        "start_date": start.isoformat(),
        "num_days": num_days,
        "channel": "SEVENROOMS_WIDGET",
        "selected_lang_code": "en",
        "exclude_pdr": "true",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": f"https://www.sevenrooms.com/explore/{VENUE}/reservations/create/search",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)  # raises if we got an HTML block page
    if data.get("status") != 200 or "availability" not in data.get("data", {}):
        raise RuntimeError(f"Unexpected response: {body[:300]}")
    return data


def date_chunks(start: date, end: date):
    d = start
    while d <= end:
        n = min(MAX_DAYS_PER_CALL, (end - d).days + 1)
        yield d, n
        d += timedelta(days=n)


def fetch_all(start: date, end: date) -> list[dict]:
    responses = []
    for i, (d, n) in enumerate(date_chunks(start, end)):
        if i:
            time.sleep(random.uniform(1.5, 3.5))  # be polite between calls
        responses.append(fetch_range(d, n))
    return responses


# ---------------------------------------------------------------- PARSE
def _in_window(time_iso: str) -> bool:
    hhmm = time_iso[11:16]  # "2026-11-22 18:30:00" -> "18:30"
    if EARLIEST and hhmm < EARLIEST:
        return False
    if LATEST and hhmm > LATEST:
        return False
    return True


def open_slots(responses: list[dict], start: date, end: date) -> list[dict]:
    """Return open slots as [{"date","time","time_iso","shift"}], sorted, deduped."""
    seen, out = set(), []
    for r in responses:
        for day, shifts in r["data"]["availability"].items():
            if not (start.isoformat() <= day <= end.isoformat()):
                continue
            for shift in shifts or []:
                if shift.get("is_closed"):
                    continue
                for t in shift.get("times", []):
                    if t.get("type") != "book":
                        continue
                    iso = t.get("time_iso", "")
                    if not iso or not _in_window(iso) or iso in seen:
                        continue
                    seen.add(iso)
                    out.append(
                        {
                            "date": day,
                            "time": t.get("time", iso[11:16]),
                            "time_iso": iso,
                            "shift": shift.get("name", ""),
                        }
                    )
    return sorted(out, key=lambda s: s["time_iso"])


# ---------------------------------------------------------------- NOTIFY
def notify(title: str, message: str, *, priority: str = "default",
           tags: str = "", click: str = "", dry_run: bool = False) -> None:
    print(f"[notify:{priority}] {title}\n{message}\n")
    if dry_run or not NTFY_TOPIC:
        if not NTFY_TOPIC and not dry_run:
            print("  (NTFY_TOPIC not set - notification not sent)")
        return
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).read()


def pretty_day(d: str) -> str:
    return date.fromisoformat(d).strftime("%a %b %-d")


def format_new_slots(slots: list[dict]) -> tuple[str, str, str]:
    by_day: dict[str, list[str]] = {}
    for s in slots:
        by_day.setdefault(s["date"], []).append(s["time"])
    lines = [f"{pretty_day(d)}: {', '.join(times)}" for d, times in by_day.items()]
    first_day = next(iter(by_day))
    title = f"Mama's: {len(slots)} table{'s' if len(slots) != 1 else ''} open for {PARTY_SIZE}!"
    body = "\n".join(lines) + "\n\nTap to book (times are Hawaii time). Slots go fast."
    return title, body, booking_link(first_day)


# ---------------------------------------------------------------- STATE
def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------- ONE CHECK
def check_once(state: dict, *, now: datetime | None = None,
               responses: list[dict] | None = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(tz=YOUR_TZ)
    today_hst = now.astimezone(VENUE_TZ).date()

    # Past the last date? Say goodbye once, then do nothing.
    if today_hst > END_DATE:
        if not state.get("finished"):
            notify("Mama's monitor finished",
                   f"{END_DATE:%b %-d} has passed. You can disable the GitHub workflow now.",
                   priority="low", tags="checkered_flag", dry_run=dry_run)
            state["finished"] = True
        print("Monitoring window is over.")
        return state

    # Don't look at days that are already over in Hawaii.
    start = max(START_DATE, today_hst)

    try:
        if responses is None:
            responses = fetch_all(start, END_DATE)
        current = open_slots(responses, start, END_DATE)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            RuntimeError, KeyError, TimeoutError) as e:
        state["error_count"] = state.get("error_count", 0) + 1
        print(f"Check failed ({state['error_count']} in a row): {e!r}")
        if state["error_count"] >= ERROR_ALERT_AFTER and not state.get("error_alerted"):
            notify("Mama's monitor: checks failing",
                   f"{state['error_count']} checks in a row failed. Latest error:\n{str(e)[:300]}\n\n"
                   "SevenRooms may be blocking GitHub, or the site changed. Check manually meanwhile.",
                   priority="high", tags="warning", click=booking_link(start.isoformat()),
                   dry_run=dry_run)
            state["error_alerted"] = True
        return state

    # Success path.
    if state.get("error_alerted"):
        notify("Mama's monitor recovered", "Checks are working again.",
               priority="low", tags="white_check_mark", dry_run=dry_run)
    state["error_count"] = 0
    state["error_alerted"] = False

    previous = set(state.get("open", []))
    current_keys = [s["time_iso"] for s in current]
    new = [s for s in current if s["time_iso"] not in previous]

    if new:
        title, body, link = format_new_slots(new)
        notify(title, body, priority="urgent", tags="rotating_light,fish",
               click=link, dry_run=dry_run)
    gone = previous - set(current_keys)
    print(f"{now:%Y-%m-%d %H:%M %Z}: {len(current)} open, {len(new)} new, {len(gone)} gone.")

    state["open"] = current_keys

    # Daily heartbeat so silence never means "it broke".
    today_local = now.date().isoformat()
    if now.hour >= HEARTBEAT_HOUR and state.get("last_heartbeat") != today_local:
        msg =(f"Watching {START_DATE:%b %-d}-{END_DATE:%b %-d} for {PARTY_SIZE}. "
               f"Open right now: {len(current)}. "
               f"Most cancellations happen 24-48h before the date.")
        notify("Mama's monitor: still watching", msg, priority="min",
               tags="eyes", dry_run=dry_run)
        state["last_heartbeat"] = today_local
    return state


# ---------------------------------------------------------------- MAIN
def main() -> int:
    global START_DATE, END_DATE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--loop-minutes", type=float, default=0,
                    help="keep checking for this many minutes (0 = one check)")
    ap.add_argument("--interval", type=float, default=120,
                    help="seconds between checks when looping (jitter is added)")
    ap.add_argument("--test-notify", action="store_true",
                    help="send a test push to your phone and exit")
    ap.add_argument("--test-date",
                    help="one-off live check of another date (e.g. one with openings) "
                         "to prove detection + alerts end to end; doesn't touch state")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    args = ap.parse_args()

    if args.test_notify:
        notify("Mama's monitor: test", "If you see this, alerts reach your phone.",
               priority="high", tags="tada", click=booking_link(START_DATE.isoformat()),
               dry_run=args.dry_run)
        return 0

    if args.test_date:
        d = date.fromisoformat(args.test_date)
        START_DATE = END_DATE = d
        slots = open_slots(fetch_all(d, d), d, d)
        print(f"{d}: {len(slots)} open slot(s): {[s['time'] for s in slots]}")
        if slots:
            title, body, link = format_new_slots(slots)
            notify("[TEST] " + title, body, priority="high", tags="test_tube",
                   click=link, dry_run=args.dry_run)
        return 0

    state = load_state()
    deadline = time.monotonic() + args.loop_minutes * 60
    while True:
        state = check_once(state, dry_run=args.dry_run)
        save_state(state)
        if state.get("finished"):
            break
        remaining = deadline - time.monotonic()
        wait = args.interval + random.uniform(-15, 15)
        if remaining <= wait + 20:
            break
        time.sleep(wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
