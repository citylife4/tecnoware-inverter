# Watchlist — current state of what is being monitored

Short, current, and **meant to be edited in place**. The scheduled checks read
this instead of carrying the state in their own prompts, because prompts
embedded with numbers went stale twice in three days and each stale prompt
makes the next run re-derive a conclusion that was already corrected.

[NOTES.md](NOTES.md) is the permanent record and keeps corrections next to
what they replaced. This file is the opposite: it holds only what is true
*now*, and old values are deleted rather than struck through. If something
here matters historically, it belongs in NOTES.md.

Last updated: 2026-09-18

---

## Regression watch, 2026-09-16 to ~09-23

Nine fixes were deployed on 09-16 and none had run a full production cycle.
One scheduled check runs at 21:37 asking only: **did the deployed fixes
behave, and did anything regress?** What it watches:

- the nightly window actually runs 01:00-08:00 (it failed twice before)
- `release_pending` is false whenever the window is shut — true while shut
  means a hand-back is failing and nothing else says so
- `hardware_override` needs 3 consecutive readings now; a single-reading one
  is a regression
- relay throws stay at 4-6/day (09-14 was 36); +2 per restart is intentional
- stalls recover in ~5 min, not ~16
- `loop_error` and `pop_drift_stuck` stay null/false
- the pack recharges — it drained 100% -> 50% over four days before the
  dead-band fix

**Run the suite with `python3 run_tests.py`**, not `unittest test_webapp`:
the latter silently skips 22 tests, including every one guarding the battery
hand-back.

After a week of clean days the job should be stopped — the two systemd
reports (09:00, 21:22) are the durable layer and need no session.

**Day 1 (09-16), post-deploy behaviour clean.** The fixes went live 17:29 and
18:01, so the split matters:

    relay throws   39 before the deploy, 3 after
                   (18:10 x2 = the intentional restart pair, 19:00 = window close)
    release_pending  True with the window open, False once shut — first time
                     that invariant has been observable, and it held
    pack             25.0 V / 50% -> 28.2 V / 100%, so the dead-band fix is
                     recharging where four days of drain preceded it
    stalls 0 | loop_error null | pop_drift_stuck false | 337 tests green

**Not yet testable:** the `hardware_override` debounce. All four overrides
today (01:01, 16:14, 16:38, 17:02) predate the 18:01 deploy, which is why
`hardware_override_pending` is 0 — not evidence the debounce fails. Tonight's
01:00 window is the first post-fix nightly run.

**Day 2 (09-17), first full post-fix day. Clean, and two fixes verified.**

- **The nightly window ran** 01:00-07:39, 82 Wh, quiet-sample minimum 24.1 V.
  First post-fix nightly run; it had failed twice before. It ended on a
  `hardware_override` at 23.8 V, which is correct — low voltage bypasses the
  debounce and hands back on the first reading.
- **The debounce works exactly as designed.** 14 pending states, 7 fired,
  and the pattern is unambiguous: two pending then fire on the third
  (15:45, 15:46 -> 15:47; 17:04, 17:05 -> 17:06; 17:27, 17:28 -> 17:29).
  **But be honest about what it bought today: nothing.** All seven clusters
  were sustained, so every one would have fired under the old code too. It
  added ~2 minutes of latency and changed no outcome. Its value is against
  transients, and today had none.
- relay throws 23 (down from 42), no stalls, `loop_error` null,
  `pop_drift_stuck` false, `release_pending` false with the window shut,
  337 tests green.
- Pack is charging normally: 1776 charging samples, last 17:00. The evening
  25.6 -> 25.3 V drift is settling off float, not the drain pattern, which
  had *zero* charging samples.

**Front-panel programs confirmed 2026-09-18** (read off the unit, since
`QPIRI` misreports all three): **11 = 10 A, 12 = 24 V, 13 = 27 V**. Program 12
confirms `PBCV24.0` took. Program 13 at 27 V is why the inverter refused
battery mode at 25.0 V on 09-16 — and it should stay there: the 3 V gap to
program 12 is what damps the 1.2 kW chatter, and a rested-full pack is only
25.6 V so lowering it towards reachable would cut the gap to ~1.5 V against a
~2 V sag. Full reasoning in NOTES.md.

Consequence worth carrying: `floor_voltage` (24.0) and program 12 (24 V) are
now known to be *identical*. The software floor cannot act first, and its load
gate stops it counting under the 1.2 kW load at all. Raising it to ~24.5 V
would let the software act first with its debounce and latch instead of
taking a `hardware_override` every time. **Not changed — needs a decision.**

**The 1.2 kW load is now the dominant problem and still has no block.** It ran
hours 9, 10, 11, 15, 16, 17, 19 and 20 today — 72 samples in hour 17 alone.
All seven overrides were it knocking the daytime window back, roughly every
23 minutes through the afternoon. The window keeps trying to open into a load
it cannot carry. The debounce makes that survivable; it does not make it
right.

---

## Settled configuration — not under test

`battery_window` **01:00-08:00** nightly, floor 24.0 V, resume 25.3 V, 3
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
              09-10   ---     NOT COMPUTABLE (138 of 144 buckets null)
              09-11  105.0   <- bright day, 0.99 kWh
              09-12   ---     NOT COMPUTABLE (100 of 144 buckets null)
              09-13   ---     NOT COMPUTABLE (86 of 144 buckets null)
              09-14    1.9   <- 4 cycles absorbed almost everything
              09-15    1.2   <- lowest, but NOT the battery: see 2b
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

**The bright-day finding now rests on three days, two of them measured:**

    09-09  1.00 kWh gen -> ratio 113.0  (measured)
    09-11  0.99 kWh gen -> ratio 105.0  (measured)
    09-10  0.86 kWh est -> implied ~114 (solar_rad proxy only)

Against a baseline of 115.6. Three bright days, three returns to baseline.

**Correction: 09-11 was wrongly written off.** It was recorded as unusable
because a probe at 11:17 returned `ac_solar_w=None`. The meter had in fact
been back since ~02:10 and only 8 of 144 buckets are null. A single failed
poll was mistaken for a continuing outage — the same single-sample trap as
the compressor-inrush readings. **Check the bucket coverage before excluding
a day, not a live probe.**

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
sometimes a shallow 15-20 Wh late afternoon. Nightly minimum 24.0-24.1 V.

**Window depth swings ~40% depending on whether the charger was still
floating the pack when it opened, and that is not a fault.** Measured:

    09-08/09/10  window opened at 27.0 V (float)  -> 110-115 Wh, ran to ~08:00
    09-12        window opened at 25.6 V (rested) ->     67 Wh, cut out 05:44

25.6 V at rest is a *full* pack (the lead-acid table puts 25.4 V at 100%), so
this is not capacity loss. The 27.0 V nights were float voltage with the
charger still pulsing. Program 12 triggers on *terminal* voltage, so starting
1.4 V higher buys roughly 2.3 hours before the hardware cuts in.

The trigger is `grid_charge` going idle: it wrote `PCP03` at 17:24 on 09-11,
so charging stopped at 17:30 instead of the usual 19:05 and the pack had
settled by 01:00.

**Confirmed by prediction on 09-13.** The charger ran to 19:05:46 on 09-12,
the pack was still at 27.0 V float when the window opened, and it ran the
full 01:00-08:00 for 103 Wh — back to the long pattern, exactly as the
mechanism said it would. Two nights, two outcomes, both predicted by when
charging stopped the evening before:

    charger stopped 17:30 -> opened 25.6 V rested -> 67 Wh, cut 05:44
    charger stopped 19:05 -> opened 27.0 V float  -> 103 Wh, ran to 08:00

So a short window is not a fault and not pack health — check when charging
last stopped before looking anywhere else.

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

## 2b. NEW 2026-09-14: a ~1.2 kW load now runs 17:00-19:00, unprotected

The pump block covers 19:00-21:15 and the evening pump still runs there
(hours 19-20, ~30 samples/day, unchanged). But a **second** large load has
appeared in the 17:00-19:00 slot — first traces 09-13, then on 09-14:

    typical day   ~30 samples >800 W, almost all in hour 20
    09-14        242 samples: 82 in hour 17, 96 in hour 18, 43 in hour 20

The daytime window runs 08:00-19:00, so it overlaps this completely and has
no block against it.

**What happened on 09-14.** At 17:15:57 the daytime window opened (`daytime`,
signal above threshold) into a ~1.2 kW load already running. The pack cannot
hold that, so program 12 threw the loads back to line, the controller still
believed POP02, and the inverter oscillated: **14 B<->L transitions in 21
minutes**, each a physical transfer relay throw under 1.2 kW. The pack sagged
to 20.9 V at a 2889 W motor start (that is sag, not depth — 113 A across
0.045 ohm is ~5 V, and every quiet sample stayed at 25.5 V).

**The software eventually did the right thing but took ~5 minutes.**
`hardware_override` fired at 17:20:58, latched, and the anti-flap dwell then
correctly refused three further attempts. The delay is structural: the
reconciler detects a mismatch by comparing belief against the device's mode,
and **an oscillating device reads as agreeing about half the time**, so the
mismatch is not seen consistently.

**The load is growing, so any fixed block will chase it.** By hour:

    09-13   15,16,17 traces only
    09-14   17 (82), 18 (96)        + normal 20 (43)
    09-15   16 (62), 17 (99), 18 (23), 19 (84)  + 20 (39), peak 4156 W

Yesterday's suggestion of blocking 17:00-19:00 is already out of date — it now
runs 16:00-20:00. **Identify the load before choosing a block**, or end the
daytime window early enough to sit clear of all of it.

**It is also, incidentally, absorbing the surplus.** 16:00-20:00 on 09-15:
**zero exporting samples in 472**, mean +623 W importing. Today's record-low
ratio of 1.2 is mostly this load, not the battery — the pack only moved 35 Wh
because the nightly window was skipped. Worth keeping in mind before reading
those ratios as the battery working well; and if this load is permanent, it
does the dump load's job for free.

**Relay wear continues:** 28 throws on 09-15 against 4-6 on a normal day,
including clusters of 5 in 3 minutes (11:18-11:21) and 4 in 1 minute
(17:05-17:06). Less pathological than 09-14's 14-in-21-minutes, but daily.

## 2c. BUG 2026-09-15: `resume_voltage` is a float voltage, so the latch can deadlock

**The whole nightly window was skipped on 09-15** — 0 Wh instead of ~110.

The chain: the 1.2 kW load chatter (2b) fired `hardware_override` at 17:42 on
09-14, setting the latch. `grid_charge` then wrote `PCP03` at 18:00 as its
last act of the day, and overnight it is `disabled_no_solar` and writes
nothing — so PCP03 stood all night and **nothing charged** (0 charging
samples before 08:00, against 255 the night before). The pack sat at
25.2-25.5 V. At 01:00 the window found the latch still set and refused. It
released only at 08:17, once daylight charging pushed the pack to 26.9 V.

**The bug is the threshold.** Release is
`self._recovering and not in_night and v >= resume_voltage`
(`webapp/battery_window.py:734`) with `resume_voltage = 26.8`. But 26.8 V is
a *charging* voltage — a **full pack at rest reads ~25.6 V**, established
09-12. So the latch can only ever release while the charger is running, and
the charger never runs at night. **Any evening `hardware_override` costs the
entire following night's window.**

This is the same error as the original `floor_voltage = 25.5`, documented in
CLAUDE.md: a float voltage used where a resting one is needed. It went
unnoticed because the latch had never before been set with the charger idle.

**FIXED 2026-09-15.** `resume_voltage` 26.8 -> **25.3 V**, live and in
`DEFAULT_CONFIG`. High enough to mean "recovered", low enough to be reachable
at rest, still above the floor. Release is gated on `not in_night`, so it
cannot re-arm mid-window and "one discharge per night" survives.

`DEFAULT_CONFIG["floor_voltage"]` had to move too, 25.5 -> 24.0: 25.5 was the
original float-voltage mistake CLAUDE.md records, never brought into line
because a stored config always overrode it, and with resume at 25.3 it made
the defaults **fail their own validation**.

Three regression tests, all verified to fail against 26.8: the latch must
clear on a rested full pack with no charger; `resume_voltage` must not exceed
the rested-full 25.6 V; and `DEFAULT_CONFIG` must pass `validate_config`.

**Untested as of 09-15 evening.** The fix landed ~11:20 on 09-15, after that
night's window had already been missed, so 09-15/16 is the first real test.
It should run 01:00-08:00 normally.

## 2d. FIXED 2026-09-16: the dead-band held "disabled_no_solar" and drained the pack

Every night ends in `disabled_no_solar`. The dead-band branch held the
previous state, and that is not a state to hold — it is the controller being
switched off. One dead-band reading at dawn closed the trap door for the day.

Observed: **14 consecutive ticks of `disabled_no_solar` with solar at 33 W**,
far above SOLAR_ON_W, long after the debounce had flipped. Four-day cost:

    09-13  V 27.0 -> 26.1   cap 100% -> 80%
    09-14  V 26.2 -> 25.2   cap  80% -> 80%
    09-15  V 25.2 -> 25.0   cap  80% -> 50%
    09-16  V 25.0 -> 24.4   cap  50% -> 50%   ZERO charging samples

`apply_low_battery_floor` was computing `PCP01` with "OVERRIDE: battery
24.40V at or below 25.50V floor" **every tick and having it discarded** —
`_tick` branches on `disabled_no_solar` before it can apply anything. The one
interlock meant to prevent this was running and being thrown away.

Fixed: the dead-band holds only `charging` or `idle`, else falls back to
`idle`. Deployed; the pack went to charging at 10 A within a minute.

**Why 09-16's nightly window never opened** (separate from the latch bug,
which was genuinely fixed — the latch read `rec=False` at 01:00): the pack
was at 25.0 V and **the inverter itself refused battery mode**. It stayed in
`L` for the whole window, never once reading `B`. Program 13's re-discharge
threshold (~27 V / FUL) is the likely cause. A low pack therefore disables
the window in hardware, whatever the software decides.

## 2e. OPEN: `min_battery_voltage` has the same float-vs-resting flaw

Not changed — needs a decision. `min_battery_voltage = 25.5` is compared
against terminal voltage, which jumps to 27.0 V the moment charging starts
even at a real ~50% SoC. So it works as an *entry* threshold and fails as an
*exit* one: observed 09-16, PCP03 queued (held only by the 300 s dwell) with
the pack genuinely half empty.

The expected result is a ~5-minute PCP01/PCP03 oscillation that does charge
the pack, just with churn — the same shape as the 2026-08-26 cycles noted
above SOLAR_FLOOR_W. It is the third instance of this class after
`floor_voltage` and `resume_voltage`: **a threshold compared against a pack
that may be on charge must be a resting voltage, or must release on something
other than voltage** (tapering current, or a minimum charge duration).

## 2f. External review, 2026-09-16 — seven defects found and fixed, deployed

A fresh reviewer working from `REVIEW_PROMPT.md` found six; a later pass
found a hole in one of the fixes. All are now in and running. Highlights
worth remembering rather than the full list (see git log from `34b8692`):

- **Disabling the window abandoned the inverter in POP02.** The disabled
  branch returned no target, so a window that had already put the loads on
  the pack simply stopped deciding. The obligation to hand back is now
  recorded *before* the POP02 write, persisted, and discharged only by an
  ACKed POP00 — and the write is refused if it cannot be persisted.
  **QMOD=L does not discharge it**: with POP=02 still set, program 12 puts
  the loads on line at ~24 V and the device returns to battery when voltage
  recovers.
- **A hardware override was undone in the tick that detected it.** The
  recovery check cleared the latch the reconciler had just set, and the
  controller wrote POP02 back while reporting `hardware_override`. Release
  now needs `RESUME_CONFIRMATIONS = 3` and a fresh override resets the
  count — the load coming off is exactly what causes the rebound.
- **The dwell allow-list is gone**: any exit to utility is urgent. It had
  been wrong three times, and its failure mode is silence.
- The scheduler now forgets an unacknowledged PCP like the other two
  controllers (missed when that was fixed on 09-03), and `grid_charge`
  freshness ages the **measurement**, not the HTTP call.

**Follow-up (2026-09-16, local changes):** unexpected loop errors now appear
in the journal and API, latch recovery, and attempt POP00. An unacknowledged
release is retried before another decision. Shutdown attempts the same
hand-back and prevents subsequent ticks from re-entering battery mode;
if the controller lock cannot be acquired, it logs the failure and retains
the persisted obligation. Abrupt process death or a wedged serial transport
still cannot guarantee a successful hand-back.

`test_battery_safety.py` covers persistence before entry, refusal on disk
failure, restart after a lost reply, QMOD=L during disable, loop errors,
and shutdown. Regression checks also run these tests against the earlier
implementations to establish that the old failure paths are detected.

**Also addressed locally:** a line transfer above the configured floor now
needs three consecutive controller observations before `hardware_override`
latches the night shut. Battery/unknown mode resets the count. Low or unknown
voltage, and non-line fault modes, still trigger immediate utility-first;
the window, pump and disable interlocks remain immediate.

`pop_drift_stuck` now exposes a persisted alert flag. The watchdog queries
the battery-window API while the serial link is healthy and uses the existing
Telegram notifier for deduplicated fault/recovery messages and failed-send
retries. Unknown mode/API failure cannot clear the alert. Recovery requires
the controller to believe POP00 and observe line mode. Delivery depends on
the watchdog interval and a configured, working notifier; no live message
has been sent to validate delivery. See `HANDOFF_CLAUDE.md` for the full handoff.

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
- **Solar Shelly — signal improved on its own 2026-09-14, and the outages
  stopped with it.** Now a steady **-76 to -77 dBm** (five probes over 20 s),
  against the -83 to -87 it had sat at. Uptime is 21.1 days, so it did not
  reboot: the radio conditions changed, not the device. Cause unknown.

  Bucket coverage tells the same story:

        09-08    0 null / 144   clean
        09-09   13 null / 144   first sign
        09-10  138 null / 144   whole day lost
        09-11    8 null / 144   back briefly
        09-12  100 null / 144   lost
        09-13   86 null / 144   lost
        09-14    3 null /  60   essentially clean

  Three of 09-10..09-13 were unusable for the ratio. Today should be
  computable, resuming the series.

  **Nothing was fixed, so treat this as reprieve rather than resolution** —
  7-10 dB could go back as easily as it came. -77 is workable; -87 was not.
  An AP nearer the panels remains the durable fix, but it is no longer
  urgent while this holds. Watch the null count, not the RSSI.

  Export control was unaffected throughout — `grid_charge` falls back to
  `generating = balance < 0`.

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
- **A few POP/PCP write failures a day. COUNT EPISODES, NOT TICKS.** Garbled
  replies and timeouts are routine; the controllers forget the cached
  priority and reconverge on the next tick.

  A failing link takes out consecutive ticks, so the raw count exaggerates.
  09-13 logged 4 failures and looked like a sharp rise from 1 and 2 the days
  before — but three of them were one episode, `POP00` timing out at
  19:00:31, 19:01:43 and 19:02:55, successive ticks of the same 3-minute
  spell. Episodes per day are flat: 1, 1, 1, 2 across 09-05, 11, 12, 13.
  Group failures less than 5 minutes apart before judging a trend.

  **They are NOT the stalls, and must not be conflated.** During that episode
  telemetry never stopped: 35 samples in the window, longest gap 27 s. Reads
  were fine while sets timed out. That fits gotcha #7 — set commands need a
  10 s window where a query needs far less, so on a marginal link writes fail
  first while reads sail through. A stall is the link going away entirely;
  this is the link being slow.
- **A handful of corrupt telemetry lines a day** (5-13). Skipped by
  `read_telemetry.load()`.
- **A lone high-load sample on battery**, e.g. 1129 W with the pack sagging to
  23.3 V (09-10 03:09:55, one sample in 2250). That is the fridge compressor
  starting, not a deep discharge and not corruption: 1129 W at ~25 V is ~45 A,
  and 45 A across the pack's measured 0.045 ohm is ~2 V of sag, which lands
  exactly where it landed. The floor's load gate correctly ignores these.
  Judge depth from *quiet* samples only — on 09-10, of 48 readings at or below
  24.0 V, zero were under 10 W.
