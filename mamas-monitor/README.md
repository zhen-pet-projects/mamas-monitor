# Mama's Fish House table monitor

Checks Mama's Fish House (Paia, Maui) for an open table for **2 guests, Nov 22–25, 2026**
and pushes an alert to your phone the moment one opens. It runs free on GitHub Actions.

- **Source:** the SevenRooms availability endpoint behind the restaurant's own booking widget
  (venue `mamasfishhouserestaurantinn`). A slot is open when SevenRooms marks it `book`.
- **Coverage:** checks about every 2 minutes, all day, every day, until Nov 25 ends in Hawaii.
- **Alerts:** you get a push only when a slot **newly** opens. Each alert lists the day and
  times, and tapping it opens the booking page for that date. It does **not** book for you.
- **Status messages:** one quiet "still watching" push each morning at 9am Central, a
  warning if checks fail 3 times in a row, and a "finished" message after Nov 25.

As of Nov 1, every slot from Nov 22 to Nov 25 was booked. The restaurant says most
cancellations come in 24–48 hours before the date, so the busiest window for alerts
is **Nov 20–25**.

---

## Setup (about 10 minutes)

### 1. Phone alerts with ntfy
1. Install **ntfy** on your phone. It's free: search "ntfy" in the App Store or Google Play.
   The developer is Philipp Heckel.
2. Tap **+** and subscribe to this topic (keep it private; anyone with the name can see the alerts):

   ```
   mamas-d602ea2b4da6
   ```
3. Allow notifications when asked. On iPhone, also allow **Time Sensitive**
   notifications for ntfy, so alerts get through Focus modes.

### 2. GitHub repo
1. Create a new repo on GitHub, for example `mamas-monitor`. Make it **Public**.
   Public repos get unlimited free Actions minutes. This monitor runs almost
   continuously, so a private repo would use up the free 2,000 minutes a month in about
   two weeks. Nothing personal is in the code, and the topic name goes in a secret.
2. Upload every file from this folder, keeping the `.github/workflows/monitor.yml` path.
   On the GitHub website: **Add file → Upload files**, then drag the whole folder in.

### 3. Add the secret
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `NTFY_TOPIC`
- Value: `mamas-d602ea2b4da6`

### 4. Test it (important)
Repo → **Actions** tab (click "enable workflows" if prompted) → **Mama's Fish House monitor** →
**Run workflow**, and run these three in order:

| Mode | What should happen |
|---|---|
| `test-notify` | A "Mama's monitor: test" push shows up on your phone |
| `test-date` (default `2027-03-02`, which had openings on Nov 1) | A "[TEST] … tables open" push. This proves GitHub can reach SevenRooms and that detection works |
| `check` | The run succeeds. The log says `0 open` (or lists openings), and a `state.json` commit appears |

If `test-date` fails with an error like "Unexpected response" or HTTP 403, SevenRooms is
blocking GitHub's servers. Tell me and I'll switch it to run on your computer instead.

### 5. Leave it running
Scheduled runs start on their own; the first one may take 10–30 minutes to appear.
After Nov 25 you'll get a "finished" push. Then go to **Actions → the workflow → ⋯ →
Disable workflow**, or delete the repo.

---

## Changing settings
Edit the `env:` block in `.github/workflows/monitor.yml`:

| Setting | Default | Meaning |
|---|---|---|
| `PARTY_SIZE` | `2` | Guests |
| `START_DATE` / `END_DATE` | `2026-11-22` / `2026-11-25` | Dates to watch (inclusive) |
| `EARLIEST_TIME` / `LATEST_TIME` | empty (any time) | Limit alerts to a window, in Hawaii time, 24h format, e.g. `16:00` / `19:00` for sunset dinners |
| `HEARTBEAT_HOUR` | `9` | Hour for the daily "still watching" push (in `YOUR_TZ`) |

## Things to know
- **Times in alerts are Hawaii time** (4 hours behind Central in November).
- **Timing:** GitHub sometimes starts scheduled runs late, so there can occasionally be
  a gap of 10–20 minutes between checks. Usually it's about every 2 minutes.
- **Booking:** a SevenRooms booking needs a credit card on file. The restaurant charges
  $50 per person for cancellations or no-shows less than 24 hours before. If you can,
  save your card in a SevenRooms account ahead of time, so booking from the alert is fast.
- **Run it locally:** `python3 monitor.py --dry-run` prints what it would send.
  `python3 tests/test_monitor.py` runs the offline tests.

## Files
- `monitor.py`: the checker (Python standard library only)
- `.github/workflows/monitor.yml`: the schedule
- `tests/test_monitor.py`: offline tests, built from response shapes captured from SevenRooms
- `state.json`: created automatically; remembers which slots were already open
