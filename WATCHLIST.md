# Watchlist — current state of what is being monitored

Short, current, and **meant to be edited in place**. The scheduled checks read
this instead of carrying the state in their own prompts, because prompts
embedded with numbers went stale twice in three days and each stale prompt
makes the next run re-derive a conclusion that was already corrected.

[NOTES.md](NOTES.md) is the permanent record and keeps corrections next to
what they replaced. This file is the opposite: it holds only what is true
*now*, and old values are deleted rather than struck through. If something
here matters historically, it belongs in NOTES.md.

Last updated: 2026-09-11 (evening)

---

## Settled configuration — not under test

`battery_window` **01:00-08:00** nightly, floor 24.0 V, resume 26.8 V, 3
confirmations. Daytime window 08:00-19:00, enter 80 W, exit 50 W, dwell
1200 s. Pump block 19:00-21:15. `grid_charge` **exclusive**, thresholds
30/150 W.

The window was extended from 04:30 on 2026-09-03 and the verdict came in on
09-06: keep it, do not extend further. Reasoning and numbers in NOTES.md.

---

## 1. Normalised export — Wh exported per kWh generated

The metric that removes weather. `ac_solar_w` from `auto-energy`
`/api/history`; generation as kWh, export integrated from the gridcharge
trace.

    baseline  09-03  115.6      (n=1 -- 09-01/02 lost their solar data
                                 to the Shelly being out of wifi range)
    post      09-04   52.2
              09-05    2.3
              09-06   27.1
              09-07   45.6
              09-08   33.2
              09-09  113.0   <- bright day, see below
              09-10   ---     NOT COMPUTABLE, see below
              09-11   ---     also out: meter missing 00:00-11:17
              mean 45.6 | median 39.4 | spread 2.3-113.0

**09-10 stays out of the series** — the solar meter dropped off at ~09:10, so
generation is truncated while export accrues all day, and any ratio from it
divides a full day by a partial morning and comes out inflated.

**But an outage is no longer a total loss.** `solar_rad` from the weather
station is a usable proxy, using the regression fitted 2026-09-03
(`ac_solar_w ~= 0.1925 * solar_rad - 6.4`, R^2 0.854). It is well calibrated
where both exist — 09-06 est 0.88 vs measured 0.89, 09-07 est 0.92 vs 0.91 —
and within ~15% on the rest. Good enough to answer "was it bright?", not good
enough to enter the series as a measurement.

**On that basis 09-10 confirms the bright-day finding, which was n=1 and is
now n=2.** Today had the highest `solar_rad` mean of the last five days (219,
a direct observation independent of the regression), an estimated 0.86 kWh,
and exported 98.2 Wh — an implied ratio near 114, essentially identical to
09-09's measured 113.0. Two bright days, two returns to baseline.

**The benefit depends on how bright the day is, and that is the most
important thing in this section.** 09-09 generated 1.00 kWh (peak 190 W,
against an all-time record of 193 W) and returned a ratio of 113.0 —
indistinguishable from the 115.6 baseline. Not a fault: the controllers ran
normally, 124 Wh out of the pack over 2 cycles, bulk charging until 18:47.

    09-03  short window  0.95 kWh gen   59 Wh from pack  -> 109.9 Wh exported
    09-09  long window   1.00 kWh gen  124 Wh from pack  -> 112.8 Wh exported

65 Wh more absorption, essentially the same export. The pack caps at
~124 Wh/day and that is marginal against 1.00 kWh.

So the 76% reduction was measured on days of 0.42-0.91 kWh. **On bright days
the longer window buys nothing measurable — and bright days are exactly when
there is export to avoid.** Above roughly 0.9 kWh of generation the surplus
escapes whatever the battery does. This is the dump-load argument with a
threshold attached.

**Report the spread, not the mean.** Three days with near-identical
generation (09-03/06/07, all ~0.9 kWh) gave 110 / 24 / 41 Wh of export, so
day-to-day variance is large and a single high or low day means nothing.
Only a sustained move is drift.

## 2. Cycle cost

**Use the merged metric**: combine battery-mode spans less than 30 min apart
before counting. Splitting on any gap over 5 min was wrong and produced a
retracted "three discharges a day" claim on 09-05 — the nightly window ends
08:00 and a daytime window can open 08:22, which is one continuous
discharge.

Current pattern: **1-2 cycles/day**, one deep ~100 Wh overnight plus
sometimes a shallow 15-20 Wh late afternoon. About 17% daily depth. Nightly
minimum sits at 24.0-24.1 V.

Flag if deep cycles exceed ~1/day or depth passes ~20%.

**The floor counter samples 6x less often than telemetry.** `battery_window`
ticks every 60 s and reads the latest cached frame, while the poller writes
one every 10 s, so the floor sees 1 sample in 6. Observed 09-09: telemetry
caught 5 readings at or below the 24.0 V floor with the load quiet (<=10 W),
which is the first time any day has produced qualifying samples at all — and
`below_floor` still never left 0, because none of them landed on a tick.

Benign so far and arguably intended: `floor_confirmations = 3` is meant to
require a *sustained* low, not a transient, and the excursions are ~0.1 V
under 45-68 W of compressor load. But it means the software floor is
effectively a coarser instrument than it looks, and program 12 at
~23.9-24.0 V is doing most of the real backstopping. Worth remembering
before anyone concludes the floor "works" from the fact that it never fires.

Historical max `below_floor` on any day: 1, against 3 needed.

## 3. Live concern: the service stalls

The serial thread wedges; the stall detector (`STALL_EXIT_S = 300` in
`webapp/service.py`) exits and systemd restarts it.

    09-04 18:44   15.3 min   (limit 900 s)
    09-06 04:28   15.8 min   (limit 900 s)
    09-08 03:00   16.1 min   (limit 900 s)
    09-11 02:02    5.4 min   (limit 300 s)  <- the change, verified
    09-11 12:02    5.6 min   (limit 300 s)

**The lower threshold is confirmed working.** The 09-11 stall logged
`no successful read in 310s (limit 300s) -- exiting so systemd restarts us`
at 02:07:13, and systemd had it back by 02:07:24. Recovery went from ~16 min
to 5.4. Nothing else changed in its behaviour.

**Rate may have changed — do not repeat "steady, not accelerating".** That was
said on the morning of 09-11 and a second stall followed twelve hours later.
Five events in eight days, but two of them inside the last 24 h.

The two on 09-11 are 02:02:03 and 12:02:04 — 10 h and 1 s apart, and both at
:02 past the hour. Nothing scheduled on this host runs at either time
(checked: the timers are at 00:00, 00:33, 02:41, 06:30, 09:01, 09:09, 15:18,
18:05, 21:22, 03:10, 01:17). With n=2 that is most likely coincidence and no
theory should be built on it — recorded only so the next stall can confirm or
kill it. Check the minute-past-the-hour of future events.

Impact is low now: ~5.5 min each, so five events cost under 30 min in total.

Cost is now ~5 min of monitoring per event, down from ~16.

**There is no leading indicator** — checked 09-08. Sample interval in the
hour before each stall is identical to quiet reference windows (median 11 s,
p90 16 s) and identical across all days. All three caught the system at
rest: no mode change, no compressor start, normal grid voltage. It is abrupt,
not gradual, so a degradation detector would not help and the fixed timeout
is the right mechanism.

Root cause unidentified. USB degradation is the suspect but nothing in the
data points at it directly.

**Applied 2026-09-09:** `STALL_EXIT_S` lowered 900 -> 300 s, so recovery is
~5 min instead of ~15. Sized from the archive: across 107115 intervals over
16 days the gap between successful reads is p99.99 33 s and the worst on any
ordinary day 25-39 s, so 300 s is ~9x the worst normal reading.

**Also fixed the same day: `usb_watchdog` was never catching these at all.**
`_service_health()` returned early on `connected`, which made its staleness
check dead code in exactly the case it was written for — `connected` is
`_latest is not None and _latest_error is None`, so a poller wedged *inside*
a read keeps it True forever while nothing is read. All three stalls ran the
full 900 s to the process-level detector while the watchdog logged
"ok (connected)" every five minutes. Staleness is now checked first and
unconditionally; `connected` is necessary, not sufficient. Six regression
tests added — there were none before.

Two independent mechanisms now cover this: the in-process detector at
300-360 s, and the watchdog at 300-600 s (its own poll interval), whose
first remedy is a service restart.

## 4. Standing, lower priority

- **SD card: resolved at the source 2026-09-10. 56% used, 12 GB free**
  (was 88% / 3.2 GB).

  **It was never this project.** `/home/greenv` was 11 GB of which `dev/` —
  every project on the machine — was 103 MB. The growth was **VS Code
  downloading a fresh ~660 MB remote server every day or two and keeping the
  old ones**: five copies dating from 09-01 to 09-09, 3.2 GB. That is ~165
  MB/day, which matches the ~170 MB/day measured almost exactly.

  Reclaimed 5.5 GB: 4 old VS Code servers (2.6 G, keeping only the live one
  from `lru.json`, verified with `lsof` to have no open files), the Chromium
  cache (2.1 G, untouched since February, no browser running),
  `CachedExtensionVSIXs` (451 M), and Docker — 23 images down to 4, plus
  404 MB of build cache. All four containers stayed up throughout.

  **The growth is stopped at the source, not merely cleaned up.** VS Code was
  removed entirely the same day (`.vscode-server`, `.vscode-remote-containers`,
  `.config/Code`) — the server is downloaded on demand when a client connects
  over SSH, so with nothing connecting there is nothing to accumulate. If
  anyone ever opens this host in VS Code again it re-downloads ~660 MB and the
  ~165 MB/day resumes.

  `.copilot` went too (1.3 G), and had the same shape: nine versions in `pkg`.
  Note it backed a real CLI at `~/.local/bin/copilot` (173 MB binary, still
  present, inert without its runtime — it will re-download if invoked). Its two
  config files were preserved in `~/.copilot-config-backup/`.

  `/home/greenv` went 11 G -> 2.7 G. `daily_report` still warns from 85% used,
  which is now the only thing that needs to stay true. Remaining, deliberately
  untouched as application data rather than caches: `.codex` 420 M, `.local`
  1.7 G (of which `.local/share/claude` is 1.2 G), `.wine-dvr` 269 M.
- **Solar Shelly — recovered on its own 2026-09-11 ~11:17, after ~26 h down**
  (dropped 09-10 09:10). Second outage. Back at RSSI -83, marginally better
  than the -87 seen before, and uptime is 18.1 days, so it has still never
  rebooted: purely a wifi-range problem. An AP nearer the panels is the fix;
  nothing software-side can help.

  Two days of the ratio series were lost to it (09-10, 09-11).

  Consequence while it is down: the normalised-export ratio cannot be
  computed at all, so the series simply pauses — do not invent entries.
  Export control itself is unaffected: `grid_charge` falls back to
  `generating = balance < 0`, verified working today (`solar=None`,
  signal -126.9 W, state `charging`).
- **DVR USB stick** — recovers only manually; Docker resolves bind mounts at
  container start, so a remount needs a Shinobi restart. Detection is in the
  daily report.

---

## Not faults — do not report these as problems

- **`hardware_override` around 07:56-07:59** most mornings. That is program
  12 changing over at its ~24.0 V threshold near the window end, i.e. the
  window being correctly sized. Only flag it at a very different time or
  voltage.
- **A few POP/PCP write failures a day** (0-2). Garbled replies and timeouts
  are routine on this link; the controllers forget the cached priority and
  reconverge on the next tick. Only flag a sustained rise.
- **A handful of corrupt telemetry lines a day** (5-13). Skipped by
  `read_telemetry.load()`.
- **A lone high-load sample on battery**, e.g. 1129 W with the pack sagging to
  23.3 V (09-10 03:09:55, one sample in 2250). That is the fridge compressor
  starting, not a deep discharge and not corruption: 1129 W at ~25 V is ~45 A,
  and 45 A across the pack's measured 0.045 ohm is ~2 V of sag, which lands
  exactly where it landed. The floor's load gate correctly ignores these.
  Judge depth from *quiet* samples only — on 09-10, of 48 readings at or below
  24.0 V, zero were under 10 W.
