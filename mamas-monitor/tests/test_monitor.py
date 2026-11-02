"""Offline tests. Fixtures mirror real SevenRooms responses captured on 2026-11-01
(Nov 22-25 fully booked = every slot type "request"; open slots are type "book")."""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ["NTFY_TOPIC"] = ""  # never send real pushes from tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import monitor  # noqa: E402

CT = ZoneInfo("America/Chicago")


def day_times(day, open_hhmm=()):
    """40 quarter-hour slots 11:00-20:45, like the real 'ALL DAY' shift."""
    times = []
    for i in range(40):
        h, m = 11 + (i * 15) // 60, (i * 15) % 60
        hhmm = f"{h:02d}:{m:02d}"
        h12 = h if h <= 12 else h - 12
        label = f"{h12}:{m:02d} {'AM' if h < 12 else 'PM'}"
        times.append({
            "type": "book" if hhmm in open_hhmm else "request",
            "time": label,
            "time_iso": f"{day} {hhmm}:00",
        })
    return [{"name": "ALL DAY", "shift_category": "LUNCH", "is_closed": False, "times": times}]


def resp(days):
    return {"status": 200, "data": {"availability": days}}


BOOKED = [
    resp({d: day_times(d) for d in ("2026-11-22", "2026-11-23", "2026-11-24")}),
    resp({"2026-11-25": day_times("2026-11-25")}),
]


def with_open(opens):
    """opens: {"2026-11-23": ["18:30"], ...}"""
    a = {d: day_times(d, opens.get(d, ())) for d in ("2026-11-22", "2026-11-23", "2026-11-24")}
    b = {"2026-11-25": day_times("2026-11-25", opens.get("2026-11-25", ()))}
    return [resp(a), resp(b)]


class Notifications(list):
    def __call__(self, title, message, **kw):
        self.append((title, message, kw))


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.sent = Notifications()
        self._orig = monitor.notify
        monitor.notify = self.sent
        monitor.EARLIEST = monitor.LATEST = ""
        self.now = datetime(2026, 11, 2, 8, 0, tzinfo=CT)  # before heartbeat hour

    def tearDown(self):
        monitor.notify = self._orig

    def test_chunks_respect_three_day_limit(self):
        chunks = list(monitor.date_chunks(monitor.START_DATE, monitor.END_DATE))
        self.assertEqual([(d.isoformat(), n) for d, n in chunks],
                         [("2026-11-22", 3), ("2026-11-25", 1)])

    def test_fully_booked_sends_nothing(self):
        state = monitor.check_once({}, now=self.now, responses=BOOKED)
        self.assertEqual(state["open"], [])
        self.assertEqual(self.sent, [])

    def test_new_slot_alerts_once(self):
        r = with_open({"2026-11-23": ["18:30", "18:45"], "2026-11-25": ["11:00"]})
        state = monitor.check_once({}, now=self.now, responses=r)
        self.assertEqual(len(self.sent), 1)
        title, body, kw = self.sent[0]
        self.assertIn("3 tables open for 2", title)
        self.assertIn("Mon Nov 23: 6:30 PM, 6:45 PM", body)
        self.assertIn("Wed Nov 25: 11:00 AM", body)
        self.assertEqual(kw["priority"], "urgent")
        self.assertIn("date=2026-11-23", kw["click"])
        # Same slots still open next check -> no repeat alert.
        monitor.check_once(state, now=self.now, responses=r)
        self.assertEqual(len(self.sent), 1)

    def test_only_newly_opened_slots_are_reported(self):
        state = monitor.check_once({}, now=self.now,
                                   responses=with_open({"2026-11-23": ["18:30"]}))
        self.sent.clear()
        monitor.check_once(state, now=self.now,
                           responses=with_open({"2026-11-23": ["18:30"], "2026-11-24": ["17:00"]}))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Tue Nov 24: 5:00 PM", self.sent[0][1])
        self.assertNotIn("Nov 23", self.sent[0][1])

    def test_slot_that_reopens_alerts_again(self):
        r_open = with_open({"2026-11-22": ["19:00"]})
        state = monitor.check_once({}, now=self.now, responses=r_open)
        state = monitor.check_once(state, now=self.now, responses=BOOKED)
        monitor.check_once(state, now=self.now, responses=r_open)
        self.assertEqual(len(self.sent), 2)

    def test_time_window_filter(self):
        monitor.EARLIEST, monitor.LATEST = "17:00", "19:00"
        r = with_open({"2026-11-22": ["12:00", "17:30", "20:00"]})
        state = monitor.check_once({}, now=self.now, responses=r)
        self.assertEqual(state["open"], ["2026-11-22 17:30:00"])

    def test_closed_shift_ignored(self):
        r = with_open({"2026-11-22": ["18:00"]})
        r[0]["data"]["availability"]["2026-11-22"][0]["is_closed"] = True
        state = monitor.check_once({}, now=self.now, responses=r)
        self.assertEqual(state["open"], [])

    def test_heartbeat_once_per_day(self):
        nine = datetime(2026, 11, 2, 9, 5, tzinfo=CT)
        state = monitor.check_once({}, now=nine, responses=BOOKED)
        state = monitor.check_once(state, now=nine, responses=BOOKED)
        beats = [s for s in self.sent if "still watching" in s[0]]
        self.assertEqual(len(beats), 1)
        self.assertEqual(beats[0][2]["priority"], "min")

    def test_errors_alert_after_threshold_then_recover(self):
        orig = monitor.fetch_all

        def boom(*a):
            raise RuntimeError("Unexpected response: <html>blocked</html>")
        monitor.fetch_all = boom
        try:
            state = {}
            for _ in range(5):
                state = monitor.check_once(state, now=self.now)
        finally:
            monitor.fetch_all = orig
        errs = [s for s in self.sent if "failing" in s[0]]
        self.assertEqual(len(errs), 1)  # alerted once, not five times
        monitor.check_once(state, now=self.now, responses=BOOKED)
        self.assertTrue(any("recovered" in s[0] for s in self.sent))

    def test_skips_days_already_over_in_hawaii(self):
        # Nov 23 20:00 Central = Nov 23 16:00 HST -> Nov 22 no longer checked
        now = datetime(2026, 11, 23, 20, 0, tzinfo=CT)
        state = monitor.check_once({"last_heartbeat": "2026-11-23"}, now=now,
                                   responses=with_open({"2026-11-22": ["18:00"]}))
        self.assertEqual(state["open"], [])

    def test_finishes_after_end_date(self):
        after = datetime(2026, 11, 26, 12, 0, tzinfo=CT)
        state = monitor.check_once({}, now=after, responses=BOOKED)
        self.assertTrue(state["finished"])
        monitor.check_once(state, now=after, responses=BOOKED)
        self.assertEqual(len([s for s in self.sent if "finished" in s[0]]), 1)

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            monitor.STATE_FILE = os.path.join(d, "state.json")
            monitor.save_state({"open": ["2026-11-22 18:00:00"]})
            self.assertEqual(monitor.load_state()["open"], ["2026-11-22 18:00:00"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
