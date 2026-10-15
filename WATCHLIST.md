# Watchlist — current state of what is being monitored

Short, current, and **meant to be edited in place**. The scheduled checks read
this instead of carrying the state in their own prompts, because prompts
embedded with numbers went stale twice in three days and each stale prompt
makes the next run re-derive a conclusion that was already corrected.

[NOTES.md](NOTES.md) is the permanent record and keeps corrections next to
what they replaced. This file is the opposite: it holds only what is true
*now*, and old values are deleted rather than struck through. If something
here matters historically, it belongs in NOTES.md.

Last updated: 2026-10-13 (15:17: occupied again; per-profile recharge, skip-near-floor)

---

## 2026-10-13 15:04: house occupied again, profile `occupied`

The user switched back at 15:04. Deployed at the same time (final at 15:17):
- **`recharge_below_v` per profile**: occupied 24.8 V, away 24.0 V. It is
  grid_charge's daylight recharge threshold, never below web.json's 24.0.
  It applied at once: the pack had sat at 24.1-24.2 V (~45%) since the
  night, the latch armed at 15:19, and it charged at 11 A towards float.
- **No entry within 0.3 V of the floor** (`too_low_to_start`). It stops the
  occupied no-op nights (13, 0, 1, 0, 0, 1 Wh, two relay throws each).
- **Outage I caused, 15:09-15:20.** The first deploy deadlocked the two
  controllers (lock-order inversion, see CLAUDE.md). The API hung, and
  `usb_watchdog` restarted the service twice, reset the USB hub and gave up
  at 15:13 ("needs manual intervention"). Fixed and redeployed at 15:17,
  watchdog `ok` from 15:18. The loads were on grid (POP00, outside the
  windows) throughout, so the house was unaffected. There may be a Telegram
  alert from 15:13.

**What to watch under `occupied`:** tonight should be a real discharge to
25.0 V from a fuller pack, or `too_low_to_start` if it is still under
25.3 V. No dark charging, except to finish this latch's charge to float.
Export on sunny days.

**10-15 morning (the jobs' final run). Clean, same pattern as 10-14.**
The window was skipped at 01:00 (`too_low_to_start`, 25.1 V): 0 POP02
writes, 0 relay throws, no `hardware_override`. **No dark charging:** 0 bulk
samples, 0 night PCP01, no corrupt frames. Standby drift 25.1 -> 24.6 V by
08:00. The daylight top-up (24.6 <= 24.8) is charging: 29.0 V at 10 A by
09:00, latch active as designed. `profile` `occupied`, all four flags clear.

**10-14 evening. Clean. Last scheduled check; the jobs expire tonight.**
House 7637 Wh, solar 240 Wh (0 nulls, peak 114 W), export 0 Wh, battery 0 Wh
(the night was skipped). 0 relay throws all day. The morning top-up latch
(armed 08:25) held through cloud gaps and released at float at 16:14. Then
PCP03, and sunset PCP03. `battery_maintenance` sane, no corrupt frames, no
`hardware_override`, no stall, 397 tests green. `profile` `occupied`, all
four flags clear. Pack 25.2 V at 22:13, so tonight should also skip.

**Where the 10-08..14 work stands.**
- *Established on hardware:* no dark grid refill on any night since the 10-08
  fix (seven nights). The sunset PCP03 holds. Surplus refills the pack on
  `away` days. The occupied recharge and skip-near-floor work. The corrupt
  frame and deadlock regressions are fixed, with tests.
- *Not established:* the export test. No day since 10-08 reached 400 Wh of
  solar (220-349 Wh), so the pass condition was never testable. The one
  export over 5 Wh (10-11, 7 Wh, `away`) came from a pack full by
  mid-afternoon.
- *Open:* `occupied` creates no night headroom (it skips). On a sunny occupied
  day export may get through once the pack fills. Candidate: lower its floor
  to 24.8 V. Also the stall-in-a-tick issue (09-24, 10-01, and the slow
  shutdowns on 10-13). The systemd reports (09:00, 21:22) keep running.

**10-14 morning. The night was skipped as predicted; no regressions.** At
01:00 the pack was at 25.2 V, inside the 0.3 V margin, so it logged
`too_low_to_start`. 0 POP02 writes, 0 relay throws, 0 Wh, no
`hardware_override`. **No dark charging:** 0 bulk samples, 0 night PCP01, no
corrupt frames. (Cosmetic, as before: the floor counter ran in `L` and set
`recovering` at 01:44, with no discharge behind it.) The pack drifted
25.2 -> 24.6 V by 07:00 on standby draw with PCP03 standing. At sunrise
24.6 V <= `occupied`'s 24.8 V armed the latch in daylight, and it is
grid-charging to float (28.5 V at 11 A by 09:00). `profile` `occupied`, all
four flags clear.

**What that means for `occupied`:** with no discharge, the only thing the
morning recharge replaces is the night's standby drift (~0.6 V). That is a
small grid top-up, not cycling, and the pack starts each day full, as a
backup should. But it also has no headroom, so `occupied` now relies
entirely on grid charge's live absorption, which a full pack limits (10-03).

**10-13 evening. Clean since the 15:17 fix.** House **4712 Wh** (occupied
again, up from 2.6-2.8 kWh/day), solar 256 Wh (0 nulls, peak 118 W), export
0 Wh, battery 44 Wh (the night). The occupied recharge worked: the latch armed
at 15:20 on 24.1 V <= 24.8 V, charged at 10-11 A (`charging` for part of
it), and reached float at 17:40. It held PCP01 through sunset (17:34) as
designed, then released to **PCP03** on float. `battery_maintenance` sane,
latch inactive. No corrupt frames. Throws 4, no `hardware_override`, no
stall after 15:17, 397 tests green. `profile` `occupied`, all four flags
clear. The day's earlier restarts were the deploy and the deadlock (above).

**Expect tonight to be skipped.** The pack rests at 25.4 V at 22:13 and
drifts ~0.07 V/h, so ~25.2 V at 01:00, inside the 0.3 V margin
(`too_low_to_start`). That is the intended replacement for a 0-Wh no-op. But
it also means `occupied` will create no headroom on most nights, which 10-03
showed lets export through on brighter days. If sunny-day export appears
under `occupied`, revisit 24.8 V as its floor (still unapplied).

## Deployed 2026-10-08 21:35 — night charging rules. Profile `away` since 10-07 21:24

The house has been empty since 10-07 (consumption 3.7 and 2.8 kWh/day,
against 6.4-7.5). The user switched to `away` on 10-07 at 21:24.

**Why the change:** on 10-08 the night discharged 27.7 -> 24.5 V (floor
05:58), and from 06:06, in the dark, the charger refilled it to 29 V by
08:09. The reason: PCP01 had been left standing since the afternoon, because
`grid_charge` wrote nothing after dark. The afternoon surplus (149 Wh with
the inverter idle, the most since September) then hit a full pack and
**19 Wh** escaped, all 15:00-17:00 with PCP01 set at float. Whether the
night's headroom survived had come down to the last PCP before sunset (lost
on 09-25, 27, 28 and 10-08).

**Changed:**
- `grid_charge` writes **PCP03 at sunset** (exclusive mode), once.
- `min_battery_voltage` **25.5 -> 24.0 V** (`web.json`; 24.3 V for 10-08..09,
  too close, see 10-09 morning), so a planned discharge no longer triggers a
  grid refill. It arms `LowBatteryLatch` only in daylight and only on
  line-mode readings. Since 10-09 21:28 the stateless check in
  `apply_low_battery_floor` arms the latch too (the sunrise churn bug).
  **Since 10-12 21:28 arming needs 3 consecutive plausible readings, and
  anything < 18 V is ignored** (the 10-11 corrupt-frame regression). Watch
  for a dark charge after a corrupt frame (V < 18 V with I=100 in telemetry);
  there should be none.
- At night the latch arms only on a resting pack <= **24.0 V**
  (`NIGHT_DEEP_V`), or **48 h without float** (`MAINTENANCE_AFTER_S`, clock
  persisted in `web_gridcharge.json.state`). It then charges to float.

**What should happen now:** after a floor-ended night the pack stays low
until the sun. `grid_charge` refills it only while the surplus signal says
`charging`, and export stays ~0 on sunny empty-house days. Watch for:
- any PCP01 in the dark (`gridcharge` trace: target 01 with desired
  `disabled_no_solar`) without `latch_active` in `/api/grid-charge`
  `battery_maintenance`;
- the pack sitting under ~24.5 V for more than a day, which the 48 h rule
  should catch;
- export on sunny days.

**Night-charging table** (counts: >= ~400 Wh solar, export <= 5 Wh, no
unexplained dark charge; 3 needed):

    date    house Wh  solar Wh  export Wh  battery Wh  profile  counts?
    10-08     2763       349        19        154       away     before the fix
    10-09     2470       329         0        116       away     no: < 400 Wh
    10-10     2743       236         0         59       away     no: < 400 Wh
    10-11     2797       300         7         65       away     no: < 400 Wh, and 7 Wh
    10-12     2598       253         0         44       away     no: < 400 Wh
    10-13     4712       256         0         44       occupied from 15:04; no: < 400 Wh
    10-14     7637       240         0          0       occupied; no: < 400 Wh

**10-13 morning. Clean: the first night on confirmed arming.** The window
opened rested at 25.1 V. `floor` latched at 03:07 at 24.2 V after 3 readings,
**43 Wh**, quiet minimum 24.2 V, no `hardware_override`. **No dark
charging:** 0 bulk samples, 0 night PCP01, no corrupt frames. The pack drifted
24.6 -> 24.2 V by 09:00. Sun-up at 08:21 went to `idle` PCP03 with no latch:
24.2 V is above the 24.0 V threshold. `profile` `away`, all four flags clear.
`battery_maintenance`: latch inactive, last_float_at 10-12 17:35.

**10-12 evening. Clean apart from one pre-fix corrupt frame.** Solar 253 Wh
(0 nulls, peak 116 W), house 2598 Wh, export **0 Wh**, battery 44 Wh. The
refill was from surplus (`charging` from 09:09). Float was reached at 17:35,
and sunset wrote **PCP03** (17:35). One corrupt frame, **11:28:16 V=6.0 /
I=100** on line, armed the latch on the old single-reading code. The 12:18
cloud gap therefore held PCP01 until float released it at 12:20 (27.4 V,
4 A). Harmless in daylight. It's the 10-11 regression once more, fixed at
21:28, so it should not recur. `battery_maintenance` sane: latch inactive,
last_float_at 17:35. Throws 8, no `hardware_override`, no
`daytime_heavy_load`. No stall (21:27 was the deploy). 389 tests green. 1 POP
write failure, recovered. `profile` `away`, all four flags clear. Sunny days
counted: 0 of 3.

**10-12 morning. Clean.** The window opened rested at 25.2 V. `floor`
latched at 03:07 at 24.5 V after 3 readings, **28 Wh**, quiet minimum 24.5 V,
no `hardware_override`. **No dark charging:** 0 bulk samples, 0 night PCP01,
no corrupt low-voltage frames overnight, so the single-frame flaw from 10-11
had no chance to fire. The pack drifted 24.7 -> 24.4 V by 09:00 and is waiting
for the sun. `profile` `away`, all four flags clear. `battery_maintenance`:
latch inactive, last_float_at 10-11 18:23. The corrupt-frame fix is still
awaiting the user.

**10-11 evening. Two things misbehaved.**

**1. Export 7 Wh: the headroom ran out.** Solar 300 Wh (0 nulls, peak 115 W),
house 2797 Wh, export **7 Wh**: 90 samples 15:29-16:37, worst -22 W. All of
it came with PCP01 set (`charging`) and the pack at **float, 27.4-27.5 V,
0 A** from ~15:40. The refill was from surplus (`charging` 09:26 onwards), so
that part worked. But the night gave only 39 Wh (opened rested at 25.1 V),
and the pack was full by mid-afternoon with sun still to come. A full pack
absorbs nothing; that limit is unchanged.

**2. A regression in the 10-08/09 change: one corrupt frame arms the
latch.** Telemetry shows impossible frames on line mode: 14:19:08
V=4.0/I=100, **16:30:54 V=0.0/I=100**, 17:20:48 V=4.0/I=100 (gotcha #8). The
`NIGHT_DEEP_V` check and the stateless check in `apply_low_battery_floor`
both arm on a **single** reading <= 24.0 V, so the 16:30:54 frame armed it.
Sunset therefore left **PCP01** (17:36, desired `disabled_no_solar`), and the
charger ran until float released it at 18:23. Harmless tonight (the pack was
nearly full), but the same frame after a night floor would refill the pack
from the grid in the dark. **Fix needed:** ignore implausible readings (a
24 V pack still on line cannot read < ~18 V) and require consecutive
confirmations before arming, like `floor_confirmations`. **Applied 10-12
21:28.**

Otherwise: sunset ended on PCP03 once the latch released (18:23). Throws 6,
no `hardware_override`, no `daytime_heavy_load`, no stall, 382 tests green.
`profile` `away`, all four flags clear. `battery_maintenance` now: latch
inactive, last_float_at 18:23.

**10-11 morning. Clean.** The window opened rested at 25.1 V. The POP02 reply
was garbled, but the device was on battery by the next tick. `floor` latched
at 03:02 at 24.3 V after 3 readings, **39 Wh**, quiet minimum 24.3 V, no
`hardware_override`. **No dark charging:** 0 bulk samples, 0 night PCP01. The
pack drifted 24.6 -> 24.2 V from 03:03 to 09:07, with no sun yet at 09:07
(`disabled_no_solar`, PCP03). That's above the 24.0 V threshold, so it waits
for the surplus, as intended. `profile` `away`, all four flags clear.
`battery_maintenance`: latch inactive, last_float_at 10-10 17:57.

**10-10 evening. Clean.** Solar 236 Wh (0 nulls, peak 104 W), house 2743 Wh,
export **0 Wh** (7 samples 13:24-16:37, worst -13 W), battery 59 Wh (42
overnight plus the daytime window). The refill was entirely from surplus:
`charging` from 08:18, 0 latch ticks. Float was reached at 17:57. Sunset wrote
**PCP03** (17:57-18:01). Cloud gaps at 10:03 and 13:40 also dropped into
`disabled_no_solar` and so wrote PCP03 mid-day. That's expected under the new
rule and harmless (PCP moves no relay); `charging` resumed each time.
`battery_maintenance` sane. Throws 8, no `hardware_override`, no
`daytime_heavy_load`, no stall, 382 tests green. `profile` `away`, all four
flags clear. Sunny days counted: 0 of 3.

**10-10 morning. Clean: the first full night and morning under the
corrected rules.** The window opened rested at 25.1 V. `floor` latched at
02:16 at 24.5 V after 3 readings, **42 Wh**, quiet minimum 24.4 V, no
`hardware_override`. **No dark charging:** 0 bulk samples before sunrise and
0 night PCP01 writes. The pack drifted 24.7 -> 24.2 V from 02:17 to 08:05
(~0.08 V/h). At sunrise the 24.0 V threshold was not crossed, so no latch and
no churn (unlike 10-09). The first write of the day was genuine surplus
`charging` at 08:18, and the pack was at 28.7 V and 10 A by 09:00. `profile`
`away`, all four flags clear. `battery_maintenance`: latch inactive,
last_float_at 10-09 17:25.

**10-09 evening. Clean. The pack refilled from surplus and reached float.**
Solar 329 Wh (0 nulls, peak 127 W), house 2470 Wh, export **0 Wh**, battery
116 Wh (79 overnight plus the daytime window). After the night floor (24.2 V)
the pack waited in the dark. The only grid write was the sunrise churn at
08:16, fixed at 21:28. Refill was from surplus: `charging` 08:40-10:08 and
14:27-17:36. Float was reached at 17:25 (`last_float_at`). Sunset wrote
**PCP03** (17:37). `battery_maintenance` is sane: latch inactive,
last_float_at today. Throws 5. No `hardware_override`, no
`daytime_heavy_load`. No stall (the 21:27 restart was the deploy). 382 tests
green. `profile` `away`, all four flags clear.

**10-09 morning. The key check passes: no dark charging. But two flaws
in the 10-08 change.** The window opened rested at 25.5 V and `floor` latched
at 05:14 at **24.2 V** (`away`, 24.5 V floor; the tick sees 1 sample in 6 plus
3 confirmations), after **79 Wh**. No `hardware_override`. PCP03 held all
night: 0 dark bulk samples, 0 night PCP01 writes, the pack resting at
24.4 -> 24.2 V until sunrise, `latch_active` false. `profile` `away`, all
four flags clear.

**Flaw 1, a bug: the old churn at sunrise.** The first daylight tick
(08:16:32) read 24.2 V <= 24.3. The stateless check in
`apply_low_battery_floor` wrote PCP01, but the latch did **not** arm, because
`arm_on_floor` uses the previous tick's `_generating`, which was still
False. 30 s later the charger had the pack at 27.0 V, so the stateless check
passed and the target went back to PCP03 (held only by the dwell). Fix: arm
the latch whenever the stateless check fires.

**Flaw 2: 24.3 V is too close to where an `away` night ends.** The 24.5 V
floor actually latches around 24.2 V and the pack rests at 24.2-24.4 V. With
flaw 1 fixed, every morning after an `away` night would grid-charge to float,
which is the headroom loss 10-08 was meant to end. Today the bug saved it:
the flip back to PCP03 let surplus charging take over at 08:40. Proposed:
`min_battery_voltage` -> **24.0 V** (= `NIGHT_DEEP_V`, program 12). The 48 h
maintenance rule then covers a pack that never reaches float. **Both applied
10-09 21:28.** (The pack had reached float at 17:25 that day, from surplus.)

## TRIAL from 2026-09-29 06:03 — profile "occupied" (Casa ocupada)

The dashboard now has two profiles (`PROFILES` in `webapp/battery_window.py`,
`PUT /api/battery-window/profile`). **Live: `occupied`**, meaning
`floor_voltage` 25.0 V and the daytime window off. `away` is the deep setting
that ran 09-23..29 (24.5 V, daytime on). Why: over 09-20..28 the house used
~93% of its ~500 Wh/day solar directly. With no battery it would export only
~37 Wh/day, while the battery was cycling ~111 Wh/day, mostly grid energy at
~25-35% loss. Numbers in NOTES.md.

**What the trial should show (the checks judge against this):**
- Nights end on `floor` at ~25.0 V after **~40-50 Wh**, around 03:00-04:00
  when the window opens at float. That is the intended result, not a short
  night.
- **Export stays ~0-5 Wh/day.** This is the test. If it rises on sunny days,
  45 Wh of headroom is too little for this house and the floor should drop
  towards 24.9-24.8 V (~55-60 Wh).
- No `daytime` or `daytime_heavy_load` ticks (the window is off), so the
  hold-off and daytime latch are not exercised.
- Battery Wh/day falls from ~111 to ~45.
- `profile` in `/api/battery-window` reads `occupied`. `custom` means someone
  edited a setting by hand.

**Trial table.** Only sunny days count towards the pass (3 needed, export
<= 5 Wh). The first trial night is 09-29/30.

    date    solar Wh  export Wh  battery Wh  counts?
    09-29      116        0         30       no: overcast (peak 35 W), and the
                                             night was still `away`
    09-30      229        0         13       no: dim (peak 83 W); first
                                             `occupied` night, rested start
    10-01      416        0          0       borderline: moderate (peak 143 W);
                                             night was a no-op. Not counted.
    10-02      460        1          1       borderline: moderate (peak 122 W);
                                             night a no-op. Not counted.
    10-03      341        5          0       dim, yet 5 Wh at the limit:
                                             pack full, see below
    10-04      256        0          0       dim (peak 90 W); night a no-op
    10-05      291        0          1       dim (peak 97 W); night ~1 Wh
    10-06      279        0          0       dim (peak 105 W); night a no-op

**10-07/08, house empty.** See the 10-08 deployment at the top: profile
`away` from 10-07 21:24, 154 Wh from the battery on 10-08, and 19 Wh export
caused by the dark refill. The occupied trial is closed; its 24.8 V
recommendation still applies to `occupied` when the house is in use again.

**10-06 evening. Clean, dim day. Last scheduled check; the jobs expire
tonight.** Solar 279 Wh (0 nulls, peak 105 W), export 0 Wh, battery 0 Wh.
No `daytime` ticks, throws 2 (the night), no `hardware_override`, no stall,
373 tests green. `profile` `occupied`, all four flags clear. **The trial ends
inconclusive on its pass condition** (no sunny day in eight). Its finding,
recorded under 10-05 evening, stands: `occupied` at 25.0 V creates no
headroom. **Awaiting the user:** 24.8 V plus skip-near-floor; deliberate
no-sun maintenance charging; the stall in a battery-window tick. `occupied`
stays live until then.

**10-06 morning. Sixth no-op night, then an accidental pre-dawn charge.**
The window opened rested at **25.2 V**. `floor` fired at 01:03 at 25.0 V:
**0 Wh**. That POP00 reply was garbled and re-applied at 01:04. No
`hardware_override`, quiet minimum 25.0 V. The pack then drained in `L` to
24.7 V by 04:30. At 05:28 one stale auth-energy reading (`known=False`) took
`grid_charge` down the "unknown -> idle" path with `LowBatteryLatch` armed.
It wrote PCP01 (and again at 06:07), and the pack charged to float
(27.4 V at 9 A by 05:37) until ~08:07, when the latch released to PCP03.
Same mechanism as after the 10-03 outage. It is the night maintenance charge
the no-sun gap lacks, but it depends on a glitch. `profile` `occupied`, all
four flags clear.

**10-05 evening. Clean, dim day. The trial is closing without a sunny
day.** Solar 291 Wh (0 nulls, peak 97 W), export 0 Wh, battery ~1 Wh. No
`daytime` ticks, throws 2 (the night), no `hardware_override`, no stall (up
since 10-01 16:03), 373 tests green. `profile` `occupied`, all four flags
clear. The scheduled checks expire 10-06.

**Trial summary, 09-29..10-05:** seven days, none sunny (116-460 Wh solar
against ~500-730 on the September baseline days). Export 0, 0, 0, 1, 5, 0, 0
Wh. Battery 30*, 13, 0, 1, 0, 0, 1 Wh (*the pre-trial night). The pass
condition (3 sunny days, export <= 5 Wh) was **never testable**. One finding
stands regardless: at 25.0 V the `occupied` floor creates no headroom on
these days. The pack rests at 25.2-25.4 V by 01:00, nights deliver 0-13 Wh,
and on 10-03 a full pack let 5 Wh escape on only 341 Wh of solar.
**Recommendation unchanged: 24.8 V plus skip-near-floor.**

**10-05 morning. Fifth no-op night.** It opened rested at **25.4 V**, and
`floor` fired at 01:43 at 25.0 V: **~1 Wh**. That POP00 reply was garbled and
re-applied at 01:44. No `hardware_override`, quiet minimum 25.0 V. Charging
resumed this morning (27.4 V at 5 A by 09:07). Trial nights: 13, 0, 1, 0, 0,
1 Wh. `profile` `occupied`, all four flags clear.

**10-04 evening. Clean, dim day.** Solar 256 Wh (0 nulls, peak 90 W),
export 0 Wh (no exporting samples), battery 0 Wh. `LowBatteryLatch` held
PCP01 08:15-08:47, then genuine charging. No `daytime` ticks, throws 2 (the
night), no `hardware_override`, no stall, 373 tests green. `profile`
`occupied`, all four flags clear. Sunny days counted: 0 of 3. The checks
expire on 10-06. The 10-03 verdict stands: too shallow, so 24.8 V is
recommended.

**10-04 morning. Fourth no-op night.** It opened rested at **25.2 V**, and
`floor` fired at 01:03 at 25.0 V after three ticks: **0 Wh**. That POP00 reply
was garbled; it was re-applied at 01:04. No `hardware_override`, quiet minimum
25.0 V. Charging resumed this morning (27.5 V at 6 A by 09:07). Trial nights:
13, 0, 1, 0, 0 Wh. `profile` `occupied`, all four flags clear. The
recommended change (24.8 V and skip near the floor) is still awaiting the
user.

**10-03 evening. The trial's failure mode has shown up, on a dim day.**
Solar only 341 Wh (0 nulls, peak 128 W), yet export reached **5 Wh**
(36 samples, worst -57 W). That's the trial's limit, on less sun than 10-01
(416 Wh, 0 Wh) and 10-02 (460 Wh, 1 Wh). Every exporting sample came **with
PCP01 already set**: 13:45-14:10 and 15:48-15:58, peaking at -57 W. The pack
was at **float, 27.4 V, 100%, taking 0-4 A**. The charger was on and had
nowhere to put the energy. This is CLAUDE.md's "known limitation" (a full
battery absorbs nothing), and it is exactly what night headroom is for.
Under `occupied` the nights have been no-ops (13, 0, 1, 0 Wh), so the pack
is full by late morning every day.

**Verdict: `occupied` at 25.0 V is too shallow for this house.** On a
genuinely sunny day this would be well over 5 Wh. Recommended: lower its
floor to **24.8 V**, which gives ~30-45 Wh from a rested start, and skip nights
that would open within ~0.3 V of the floor. Not applied; awaiting the user.

Otherwise clean: battery 0 Wh. Throws 4: the night's entry and floor, plus
the outage's B<->L. No `daytime` ticks, no `hardware_override`, no stall,
373 tests green. `profile` `occupied`, all four flags clear.

**10-03 morning. Third no-op night, and a real grid outage handled
correctly.** The window opened rested at **25.2 V** and `floor` fired at 01:08
at 25.0 V: **0 Wh**, no `hardware_override`. Trial nights so far: 13, 0, 1,
0 Wh.

**Grid outage ~04:36-04:42** (`grid_voltage` 0 V at 04:36:39, 230 V again by
04:42:15). The inverter carried the loads on the pack, as utility-first does
when the grid fails. The reconciler classed it as `unexpected_battery` (grid
absent), **not** `pop_drift`, and wrote nothing, which is correct. Pack 24.8 V
at the time; the quiet minimum of 24.7 V came during the outage. Afterwards
`grid_charge` wrote PCP01 at 04:38 (audit: `grid_export`). That fits the
measurement going stale during the outage ("unknown -> idle") while
`LowBatteryLatch` was armed. The pack then charged to 29.1 V before dawn,
the night-time maintenance charge the no-sun gap lacks, by accident.
`profile` `occupied`, all four flags clear.

**10-02 evening. Clean, moderate day, still no real test.** Solar 460 Wh
(0 nulls, peak 122 W), export **1 Wh** (9 samples, worst -20 W), battery 1 Wh.
Grid charge absorbed the surplus alone: genuine `charging` 408 ticks across
6 spells, 09:40-16:42. `LowBatteryLatch` held PCP01 08:16-09:40, then genuine
charging. No `daytime` ticks, throws 2 (the night), no `hardware_override`,
no stall, 373 tests green. `profile` `occupied`, all four flags clear.
3 PCP write failures, all recovered. Sunny days counted: **0 of 3**. Four
trial days so far, with no day over ~460 Wh.

**10-02 morning. A no-op again, the second night running.** It opened rested
at **25.1 V**, and `floor` fired three ticks later (01:03, 24.9 V): **1 Wh**
for 2 relay throws. Quiet minimum 24.9 V, no `hardware_override`. `profile`
`occupied`, all four flags clear. Charging resumed this morning (29.0 V at
7 A by 09:07). Three trial nights so far: 13, 0 and 1 Wh. **As configured,
`occupied` creates essentially no headroom on these days**, so the battery is
contributing nothing to export control. Grid charge is doing all of it. See
the two options under 10-01 morning.

**10-01 evening. The best solar of the trial so far, still 0 export, but
the battery contributed nothing.** Solar 416 Wh (0 nulls, peak 143 W), export
**0 Wh** (3 samples, worst -23 W). Battery 0 Wh. Grid charge absorbed the
surplus itself, flipping into genuine `charging` 5 times between 13:27 and
17:14 on a pack already near float. So today's zero export was the charger's
work, with no help from night headroom. The pre-trial baseline on days of
~400 Wh was only 2-7 Wh/day, so this day cannot separate the two. Not counted
as sunny. `LowBatteryLatch` held PCP01 08:29-09:32, then genuine charging. No
`daytime` ticks. Throws 2 (the night). No `hardware_override`. 373 tests
green. `profile` `occupied`, all four flags clear.

**A stall, the second in 8 days, with the same signature as 09-24.** No
telemetry from 15:57:48 to 16:02:55 (307 s), and the API was unreachable.
`usb_watchdog` caught it at 16:01:18. Its restart hung on "shutdown hand-back
blocked by active tick" (a tick wedged inside a serial call, holding the
lock), and systemd completed the stop at 16:02:49. Recovery ~5 min. Loads on
grid throughout (`outside`, POP00), so harmless. Two occurrences with the
same pattern now make a stall inside a battery-window tick worth looking at,
for example a timeout on the lock or on the serial call.

**10-01 morning. The window was a no-op.** It opened rested at **25.2 V**
(25.4 V at 22:13, drifting at the standby rate noted on 09-30). It went to
`B` and `floor` fired three ticks later, at 01:03 at 24.9 V: **0 Wh** for 2
relay throws. Quiet minimum 24.9 V, no `hardware_override`. `profile`
`occupied`, all four flags clear. Charging resumed this morning (27.4 V at
6 A by 09:07, solar 16 W).

Correct per the rules, but pointless: under `occupied` a dim day leaves the
pack resting at ~25.2-25.4 V by 01:00, within a few tenths of the 25.0 V
floor. Two possible changes, **not applied**:
- **Skip the entry when the pack is already near the floor** (e.g. rested
  within 0.3 V of it). That saves the relay throws on nights like this.
- **Lower the `occupied` floor towards 24.8 V**, so a rested start still
  creates ~30-45 Wh of headroom. The sunny-day export numbers decide whether
  this is needed.

**09-30 evening. Clean, second dim day. The trial is untested so far.** Solar
229 Wh (0 nulls, peak 83 W), export 0, battery 13 Wh (the night). No `daytime`
ticks. Throws 2 (the night's entry and floor). No `hardware_override`, no
stall, 373 tests green. `profile` `occupied`, all four flags clear.
`LowBatteryLatch` held PCP01 from 10:35. Clouds dropped `grid_charge` into
`disabled_no_solar` twice (PCP01 stood meanwhile). It released at 12:25 as the
current reached 4 A at 27.4 V, which is correct.

**Headroom is smaller than the trial assumed.** The ~45 Wh came from nights
that opened at **float**, and those were nights when the charger ran into the
evening absorbing export. On `occupied` days the charger reaches float by
early afternoon and then idles (PCP03). The pack rests down to ~25.4 V by
01:00 (25.4 V at 22:13 tonight), and the window gets only **~13 Wh** before
25.0 V. That is well under the ~37 Wh/day this house could export. **The
sunny-day export numbers are the real test.** If they rise, lower the floor
towards 24.8 V. From a rested 25.4 V start that gives roughly 30-45 Wh.

**09-30 morning. First `occupied` night: correct, but short.** It opened
**rested at 25.4 V** (charging stopped 15:54 on 09-29 and the pack settled).
`below_floor` counted 1, 2, 3 and `floor` handed back at 01:42 at 25.0 V after
only **13 Wh**. Quiet minimum 25.0 V, no `hardware_override`. Expected: the
~45 Wh figure is for a window that opens at float. From a rested pack, 25.0 V
is only ~0.4 V away. `profile` `occupied`, `release_pending` false,
`loop_error` null, `pop_drift_stuck` false, `config_error` null.

**The pack drains in line mode while nothing charges it.** After the floor it
fell steadily, 25.2 V at 02:17 to 24.6 V at 09:07, with 0 A charging, the
loads on grid, and `grid_charge` still `disabled_no_solar` (PCP03 standing).
~0.07 V/h is consistent with the inverter drawing its own standby from the
pack while the charger is blocked. That is not measured directly. It also
matches the slower sag on 09-26 and 09-29 mornings. It turns the no-sun gap
above from "sits half-empty" into "keeps draining", and it is the likely
mechanism of the 09-13..16 drain. It strengthens the case for letting an
armed `LowBatteryLatch` charge regardless of sun.

**09-29 evening. Clean, but a dark day, so no test of the trial.** Solar was
116 Wh (0 nulls, peak 35 W), export 0. The only battery use was the pre-trial
night (30 Wh). No `daytime` ticks, as the window is off. Throws 2 (the night's
entry and floor). No `hardware_override`. Charging only began at 11:34, once
solar crossed 8 W; the morning gap above is real. `LowBatteryLatch` held PCP01
from 11:34 and released ~14:00 as the current fell to 4 A at 29.0 V, which is
the float condition. PCP03 then timed out and grid charge flipped to genuine
`charging` until 15:54. The pack rested at 25.6 V at 22:13. `profile`
`occupied`, `release_pending` false, `loop_error` null, `pop_drift_stuck`
false, `config_error` null. No stall (restarted for the deploy at 06:02).
373 tests green.

**09-29 morning — the night before the trial (still `away`).** Opened
rested at 25.1 V (the daytime window drew the pack down until 19:00 on 09-28
and nothing charged after). Floor at 02:30 at 24.4 V after **31 Wh**, quiet
minimum 24.4 V, no `hardware_override`. `profile` `occupied` (applied 06:03),
`release_pending` false, `loop_error` null, `pop_drift_stuck` false,
`config_error` null.

**Open gap: nothing charges a low pack while there is no sun.** At 09:07 the
pack had been at 24.3-24.5 V (~50%) since 02:30. The morning is heavily
overcast (`ac_solar_w` 0, `solar_rad` ~20-25 W/m²), so `grid_charge` is still
`disabled_no_solar` and writes nothing. PCP03 from the evening stands, and the
`LowBatteryLatch` override never gets applied, because that branch returns
before it. On a dark day the pack could sit half-empty all day, and several
dark days in a row is the 09-13..16 drain pattern (2d). Not changed yet;
needs a decision. The likely fix: let an armed `LowBatteryLatch` write PCP01
even in `disabled_no_solar`, so it stops at float. That's battery maintenance
from the grid, which costs energy but protects the pack.

## Deployed 2026-09-23 21:35 — watch these next

Three changes, all from the open decisions below, all untested in
production. What each should look like if it works:

- **`floor_voltage` 24.0 -> 24.5 V** (live and `DEFAULT_CONFIG`). Nights should
  now end on reason `floor` around 05:30-06:30, not on a `hardware_override`
  at ~07:40. Expect ~20-25% less energy per night (~100-115 Wh, not 130-145).
  If they still end on `hardware_override`, the floor still is not acting
  first. Look at `below_floor` in the trace before anything else.
- **Override debounce: 3 of the last 5 readings**, not 3 in a row
  (`HARDWARE_OVERRIDE_LOOKBACK`). Under an oscillating load the latch should
  come within ~5 ticks of the first transfer, not the 2h11m of 09-19. A single
  `hardware_override_pending` that never fires is still correct.
- **Daytime heavy-load hold-off.** Reason `daytime_heavy_load`: the daytime
  window stays shut for 1 h after 2+ readings above 800 W within 10 min (the
  inverter's 10 s telemetry in any mode, or the Shelly; the larger count,
  since 09-26 22:55), or after any daytime
  `hardware_override`. `heavy_load_until` in the API shows it. On a gardening
  day relay throws should stay near ~16, not 96. **Not persisted**: a restart
  forgets it.
- **`grid_charge` low-battery latch** (`safety.LowBatteryLatch`). Below
  `min_battery_voltage` it holds PCP01 until the charger reaches float
  (>= 27.0 V and <= 4 A, 3 readings in a row). `why` in `/api/grid-charge`
  reads "holding utility charging until the pack reaches float". The trace CSV
  has no `why` column, so find it there as desired `idle` with target `01`.
  Watch for PCP01 holding past
  float, which would mean the release condition never matches on this unit's
  current readings.

**09-24 morning. The nightly window was refused, and the program-13 rule
predicted it (3 refusals, 3 hardware-ended days).** On 09-23 charging
stopped at 16:59. The last battery span ended 17:15, followed by hardware
overrides at 18:04, 18:27 and 18:50. The pack then sat rested at 25.6 to
25.3 V all night and never reached 27 V. At 01:00 the POP02 reply came back
garbled and was re-applied at 01:01. There were 2 pending ticks, and the
latch came at 01:04 at 25.3 V. So the new floor was not tested at night.

**The 24.5 V floor fired for the first time ever, in the daytime window.**
The window opened at 08:12 into a pack that had only been surface-charged
(capacity 50%). It fell to 24.5 V by 08:44 under ~50 W. `floor` latched at
08:46 after 3 readings, before program 12. That is the software acting first,
as intended.

**New flaw, exposed by that, not a regression.** The latch released at 08:49,
3 minutes after it fired. In daytime, release is `v >= resume_voltage` (25.3),
and the charger had the pack at 27.0 V. This is the fourth
float-vs-resting-voltage threshold. Only the 1200 s dwell and then
`daytime_surplus` (signal 55-78 W, under 80) have kept a ~50% pack off
battery since. The floor bounds each discharge, but a sunny day could cycle
it floor-to-charge repeatedly. **Fixed 09-26 22:37** (release on the
charger reaching float); see the entry above it.

`release_pending` false, `loop_error` null, `pop_drift_stuck` false,
`config_error` null, and no single-reading override. `LowBatteryLatch` never
engaged: grid charging was already wanted. The heavy-load hold-off was not
triggered.

**09-28 evening. Clean day (2 of 5).**

- **Hold-off armed on a short pump burst.** Two ~1.2 kW transfers 80 s apart
  (08:28:03 at 1189 W, 08:29:21 at 1235 W, the pump's signature, not the
  fridge's) put the pack briefly on line twice while on battery. The next tick
  (08:29:57) armed `daytime_heavy_load` and handed back. The POP00 reply was
  garbled, but the device was in `L` by 08:30:10 and the following tick
  confirmed it. 66 ticks of hold-off in total. No false triggers.
- **Relay throws 16**: 6 in hour 8, 4 of them that burst. >800 W samples by
  local hour: 8 (2), 10 (1), 17 (1), 20 (28).
- No `hardware_override`. `LowBatteryLatch` override spans at 10:45 (one tick,
  then genuine charging) and 16:17-17:04 (then charging, then PCP03 at 17:26),
  none past float.
- `release_pending` false, `loop_error` null, `pop_drift_stuck` false,
  `config_error` null. No stall (up since 09-26 22:55). 365 tests green.
  Export **2 Wh** (22 samples, worst -35 W). 2 PCP write failures, recovered.

**09-28 morning. Clean.** The window
opened at 01:00 at 27.3 V (float). `below_floor` counted 1, 2, 3 at 06:09,
06:10 and 06:11, and `floor` handed back at 06:11:55 at 24.5 V. No override
all night: `recovering` stayed False and the window stayed on POP02 until the
floor. The latch then held through bulk to 29.1 V and released at 08:02:57.
The daytime window entered from a full pack, the same as 09-27. The persisted
state shows `release_pending` false and `pop_drift_stuck` false. Filled in by the evening
run, since Bash was unavailable in the morning: **67 Wh** overnight, quiet
minimum 24.5 V at 06:08, `loop_error` null, `config_error` null.

**09-27 evening. Clean day (1 of 5, counting from 09-27). The new hold-off
sampling worked live on a pump day.**

- **Hold-off.** The pump started at 12:46:49 while the pack was on battery.
  The next tick read `hardware_override_pending` (12:47:16), and the one after
  armed `daytime_heavy_load` and handed back (12:48:16): ~1.5 min and 5 relay
  throws, against 09-24's missed episode and 09-19's 2h11m. It then re-armed
  on each later pump cluster through 17:16 (176 ticks, interleaved with
  `yielding`). Heavy samples by local hour: 12 (4), 13 (1), 14 (23), 15 (4),
  16 (9), 19 (7), 20 (41). Every arming matched real heavy readings; no false
  triggers. **Relay throws 18** on a pump day, against 96 on 09-19. One sag to
  23.3 V at 1333 W was sag, not depth.
- **Debounce:** no `hardware_override`. The one pending at 12:47 was superseded
  by the hold-off, which is correct.
- **`LowBatteryLatch`:** override spans 08:51-09:25 (then genuine charging),
  12:22-13:14, and 17:39 to sunset. At 13:14 it released ~1.5 min after the
  current reached 4 A at 27.4 V, the float condition, to PCP03. **One odd
  tick:** at 04:17:31 a reading marked not-fresh made `grid_charge` evaluate
  `idle` instead of `disabled_no_solar`. The latch was armed (pack 25.0 V on
  battery), so it wrote PCP01 once. That's a no-op under POP02 and harmless,
  but it's the existing "unknown -> idle" path writing at night, noted rather
  than fixed.
- `release_pending` false, `loop_error` null, `pop_drift_stuck` false,
  `config_error` null. No stall (up since 09-26 22:55). 365 tests green.
  Export 0 Wh. 1 POP + 3 PCP write failures, all recovered.

**09-27 morning. Clean, and the 09-26 latch fix confirmed live.** The window
opened at 01:00 at 27.6 V (float). `below_floor` reached 3 and `floor` handed
back at 06:08 at 24.5 V (quiet minimum 24.5 V at 06:05), after **76 Wh**
overnight. There was no `hardware_override`. The charger started at once
(10 A by 06:20, PCP01 standing from the evening). **`recovering` held through
bulk** (27.1 -> 29.0 V at 10 A) and released only at 08:02 at 29.1 V / 4 A,
exactly the float condition. The daytime window then entered from a full
pack. On 09-26 the same sequence released at 27.0 V after 3 minutes.
`release_pending` True at 09:07, which is correct because the daytime window
has the pack on battery. `loop_error` null, `pop_drift_stuck` false,
`config_error` null.

**Deployed 2026-09-26 22:55: the hold-off counts the inverter's 10 s
telemetry, not the tick's one sample.** Any mode counts, because a reading
above 800 W is real even in bypass. It takes the larger of the inverter and
Shelly counts, not the sum, so one inrush seen by both doesn't arm it.
Replayed on 09-18..26: it arms only on pump clusters (6x on 09-19, none on
09-20/22/26), never on isolated spikes, and it would have caught 09-24 18:56.
**What to look for:** on a pump day, `daytime_heavy_load` within ~1 minute
of the load starting while on battery, and at most a few throws per episode.

**Deployed 2026-09-26 22:37: the daytime latch releases on float, not on
charging voltage.** `BatteryWindow._recovered()`: while the charger runs
(current > 0), the latch re-arms only at >= 27.0 V and <= 4 A, the same
condition as `LowBatteryLatch`. With 0 A it is `resume_voltage` as before, so
the 09-15 rested-pack release survives. An unknown current releases nothing.
Replay tests of the 09-26 morning fail against the old code. **What to look
for:** after a night that ends on the floor, `recovering` should hold through
bulk and release only once the current reads 4 A at ~29 V. No `daytime`
entry should happen into a pack below ~25.5 V. Clean-day count restarts
from 09-27.

**09-26 evening. Not a clean day (the morning's extra discharge, below); the
rest behaved.** Clean-day count still 1 (09-25).

- **`LowBatteryLatch` never held PCP01 past float.** There were three override
  spans (desired `idle`, target 01): 08:22-09:29 during bulk at 10-11 A;
  12:36-13:19 after a daytime discharge ended at 25.4 V; and 17:58 until
  sunset. Each ended in genuine `charging` (export absorption) or with PCP03
  on the next `idle` tick. The trace has no `why` column, so the exact release
  tick isn't visible. **Side effect to watch:** at `min_battery_voltage` 25.5
  it re-arms after almost every daytime discharge (those end at 25.4-25.5 V)
  and then grid-charges the pack to float in daylight. Export was **5 Wh**
  today (45 samples, worst -40 W), the first non-zero day since 09-19. One
  day, so this is not established as the cause.
- After 08:32 the daytime window only entered from a full pack (11:05 from
  26.4 V, 17:35 from 25.8 V), so there was no repeat of the morning's flaw.
- Heavy-load hold-off: 0 daytime ticks. It armed on the evening pump
  (19:04-20:33, outside the daytime window) and did not arm on a lone
  night-time reading at 03:53. Throws 14. No `hardware_override`.
- `release_pending` false, `loop_error` null, `pop_drift_stuck` false,
  `config_error` null. No stall (up since 09-24 16:34). 357 tests green.
  3 POP write failures, all recovered.

**09-26 morning. Not clean: the daytime-latch flaw caused a real extra
discharge.**

- **The night confirmed the program-13 prediction.** It ran from a rested
  25.2 V (a software close the previous evening), the second rested night to
  run after a software close. `below_floor` counted 1, 2, 3 and `floor` fired
  at 03:10 at 24.4 V. POP00 timed out and was re-applied at 03:11. It
  delivered **36 Wh**, with a quiet minimum of 24.4 V. There was no
  `hardware_override`.
- **The pack then sat at 24.5-24.6 V from 03:10 to 08:23 with nothing
  charging.** `grid_charge` is `disabled_no_solar` overnight and writes
  nothing. That's shallower than the old 24.0 V end, but it's 5 h at ~50%.
  (Cosmetic: `below_floor` kept counting in `L` mode, reaching 122, and the
  reason flips `floor`/`recovering` all night. It's harmless because the
  latch was already set.)
- **`LowBatteryLatch` engaged for the first time and worked.** From 08:22
  `grid_charge` wanted `idle` (PCP03) but held PCP01 with "holding utility
  charging until the pack reaches float". At 09:07 it was still holding at
  27.8 V and 10 A, where the old stateless check would have released at
  27 V. (Correction: the trace has no `why` column, so the "0 latch ticks"
  reported on 09-24/25 measured nothing. Read `why` from `/api/grid-charge`.)
- **The daytime-latch flaw fired.** 3 minutes into that charge, the
  battery-window latch released on 27.0 V (charging voltage, not resting,
  against `resume_voltage` 25.3). At 08:25 the daytime window put the ~50%
  pack back on battery. It fell 25.4 -> 24.2 V in 7 min under 45-92 W, and
  `floor` latched at 08:32 at **24.2 V**, under the 24.5 floor because the
  tick sees 1 sample in 6. That's one extra discharge and 2 relay throws.
  Since then it has tried to re-enter three more times, held off only by the
  1200 s dwell and `daytime_surplus`. **It will repeat on any day that starts
  with a discharged pack.** The fix proposed on 09-24 (release on the charger
  reaching float) is now justified by an actual occurrence. Not applied:
  awaiting the user.

`release_pending` false, `loop_error` null, `pop_drift_stuck` false,
`config_error` null. No single-reading override.

**09-25 evening. Clean day (1 of 5; 09-24 was not clean).**

- **The hold-off worked as intended.** The load ran 16:53-17:04 while the
  window was yielding to grid charge (loads on grid, so no throws; the
  inverter reported ~1190 W in `L`). The hold-off armed on it and kept the
  daytime window shut from 17:08 to 18:08 (63 ticks). It reopened at 18:11
  with no load. No false triggers: the two lone Shelly readings at 08:53 and
  10:47 did not arm it.
- **Relay throws 18**, back in the normal range. >800 W samples by local
  hour: 16 (16), 17 (13), 18 (1), 19 (7), 20 (22).
- No `hardware_override` today, so the debounce wasn't exercised.
  `LowBatteryLatch` never engaged (0 ticks).
- `release_pending` false, `loop_error` null, `pop_drift_stuck` false,
  `config_error` null. No restart or stall (up since 09-24 16:34). 357 tests
  green. Export 0 Wh. 5 POP + 2 PCP write failures, all recovered. The 19:00
  POP00 timed out, but the device obeyed (`L` at 19:01:16) and the next tick
  confirmed it.
- **Prediction for tonight (program-13 rule):** charging stopped at 17:07,
  but the day ended in a software close (`B` until 19:00:41, then POP00). So
  the window should **run** from a rested ~25.5-25.8 V, as on 09-21, and hit
  the 24.5 V floor early.

**09-25 morning. The first night ever to end on the software floor.** The
window opened at 01:00 at 27.6 V (float; the charger ran to 22:12). It
discharged until `below_floor` counted 1, 2, 3 at 06:05, 06:06 and 06:07, and
`floor` handed back at 06:07:50. There was no `hardware_override` all night,
and none on a single reading. Quiet-sample minimum was 24.5 V at 06:03, and
it delivered **71 Wh**, below the 100-115 Wh predicted from 09-20/22. That is
one night, so draw no conclusion yet. The grid then recharged it in bulk
(10-11 A, 29.0 V) and tapered to 4 A by ~08:00. The daytime window re-entered
at 08:49, so today the daytime latch release happened to coincide with float.
The flaw from 09-24 is still there in principle. `release_pending` is True at
09:07, which is correct because the daytime window has the pack on battery.
`loop_error` null, `pop_drift_stuck` false, `config_error` null.

**09-24 evening. Day 1 of 5 — not clean. Two things misbehaved.**

- **The heavy-load hold-off missed an oscillation.** The load came on at 18:55
  while the daytime window had the pack on battery. The inverter oscillated
  B/L with 1194-1986 W readings about every 20 s until the 19:00 pump block
  closed the window: 16 of the day's 30 relay throws came in hour 18. The
  hold-off never armed. It samples the inverter's load only once per 60 s
  tick, and every tick landed on a `B`/45 W or `L` reading. The Shelly saw
  only one heavy reading before 19:00. **Fix proposed, not applied:** count
  heavy battery-mode samples from the service's 10 s telemetry history, not
  from the tick's single sample.
- **It did work once, correctly.** It armed at 16:51 on two Shelly readings
  (1309 and 1289 W at 16:43) with the load really there, and held 60 ticks.
  It never fired without a heavy load, and no fridge inrush triggered it.
- **A stall, the first since 09-11.** No telemetry from 16:28:18 to 16:34:48
  (390 s), with the HTTP API also unresponsive, so the whole process was
  wedged. `usb_watchdog` caught it at 16:33:10. Its restart hung 60 s
  ("shutdown hand-back blocked by active tick; persisted obligation
  retained"), so it rebound USB and restarted at 16:34:57. The loads were on
  utility throughout (POP00, `release_pending` false), so it was harmless.
  Recovery took ~6.5 min. The in-process 300 s detector did not get there
  first. Minute :28, so it does not fit the :02 pattern.

Debounce: one override (01:04, 2 min from the first pending). A single
pending at 18:57 did not fire. `LowBatteryLatch` never engaged (0 ticks).
`release_pending` false, `loop_error` null, `pop_drift_stuck` false,
`config_error` null, 357 tests green. Export 0 Wh. 5 POP write failures
(one 2-tick episode at 18:24-18:25), all recovered.

## Regression watch, 2026-09-16 to 09-22 — closed

Closed after seven days with every check clean. The session job had already
lapsed; the systemd reports (09:00, 21:22) are the durable layer.

Nine fixes were deployed on 09-16 and none had run a full production cycle.
One scheduled check ran at 21:37 asking only: **did the deployed fixes
behave, and did anything regress?** What it watches:

- the nightly window actually runs 01:00-08:00 (it failed twice before)
- `release_pending` is false whenever the window is shut — true while shut
  means a hand-back is failing and nothing else says so
- `hardware_override` needs 3 line readings now (3 in a row until
  2026-09-23, 3 of the last 5 since); a single-reading one is a regression
- relay throws stay at 4-6/day (09-14 was 36); +2 per restart is intentional
- stalls recover in ~5 min, not ~16
- `loop_error` and `pop_drift_stuck` stay null/false
- the pack recharges — it drained 100% -> 50% over four days before the
  dead-band fix

**Run the suite with `python3 run_tests.py`**, not `unittest test_webapp`:
the latter silently skips 22 tests, including every one guarding the battery
hand-back.


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

**Day 3 (09-18). Clean on every deployed fix, but the nightly window did not
run — for the hardware reason in 2d, not a regression.**

- **No nightly window.** The pack was at **25.1 V rested** at 01:00 and the
  inverter refused battery mode, exactly as on 09-16. The software did each
  step right: the latch was **clear** (`rec=False` at 01:00:35, so the 2c fix
  held), the window opened and wrote POP02, the reconciler saw the line
  transfer, debounced it across 01:01 and 01:02, and latched on the third at
  01:03:37. One override, no thrash, and `release_pending` cleared. (The
  program-13 explanation here was later refined; see days 5-7.)
- **The debounce is behaving exactly as specified.** Three overrides today
  (01:03 at 25.0 V, 14:32 at 26.1 V, 14:55 at 25.9 V), each preceded by
  **exactly 2 pending ticks** — every one fired on the third reading, none on
  a single one, no bursts. As on 09-17 all three were sustained, so it again
  changed no outcome; its value remains untested against a true transient.
- relay throws **24** (23 on 09-17), **no service restarts**, and the spread
  is benign: max **6 in any 21-minute span** against 09-14's 14. Hours 8, 10-13,
  16, 17, 19 — the daytime window being knocked back, as on 09-17.
- `release_pending` **false** with the window shut, `loop_error` null,
  `pop_drift_stuck` false, `config_error` null, `below_floor` 0.
- **0 telemetry gaps over 240 s** — no stalls.
- Pack healthy: 2309 charging samples, charging until 21:53, holding the new
  27.4 V float at 22:08. Not the drain pattern.
- 337 tests green.

**Day 4 (09-19). Every software check clean; relay throws hit a record 96.**

- **The window ran 01:00-07:20**, 380 min, quiet-sample min 24.0 V —
  confirming the prediction below. The rule is now 4 for 4.
- `release_pending` false with the window shut, `loop_error` null,
  `pop_drift_stuck` false, `config_error` null, 0 stalls, 337 tests green.
- One `hardware_override` (18:24:50, after exactly 2 pending). No
  single-reading fires.
- Pack healthy: 2127 charging samples, charging to 20:37, float 27.4 V.

**Relay throws 96 — the worst day recorded, against 4-6 normal and 09-14's
36.** By hour: 16 (16), 17 (24), 18 (37). Max 36 in a 21-minute span, where
09-14's cluster was 14.

**It is the load, not the controller.** The 1.2 kW load ran **336 on/off
cycles** today against 82 on 09-18 and a previous maximum of 237, and it has
spread from 16:00-20:00 to **10:00-20:00**. Controller behaviour was correct
throughout: it entered battery mode only 5 times in 16:00-19:00, and the
1200 s anti-flap dwell held a re-entry for a full 20 minutes (17:51-18:09,
`applied=False`, "a aguardar ... anti-flap do relé"). Each entry into an
already-cycling load then costs many *inverter-level* throws, which no
controller tick at 60 s can damp.

Normalised, today is elevated but not an outlier on its own: throws per
load-cycle-while-POP02 ran 1.28, against 0.90 on 09-14 and 0.89 on 09-18, in a
noisy 0.25-1.28 range. **The load count is what moved, not the ratio.**

**The debounce is systematically defeated by an oscillating device, and this
is the first hard measurement of it.** It needs 3 *consecutive* line readings;
the device reads `B` about half the time while oscillating, so the counter
resets. Visible at 18:16/18:17 pending, 18:18 back to `B` (reset), 18:22/18:23
pending, 18:24 fire. First entry into the load was 16:13 and the single latch
came at 18:24 — **2h11m**, against ~5 minutes for the same situation pre-fix
on 09-14 (window opened 17:15:57, override 17:20:58).

This is the cost side of the 09-16 fix, and 2b predicted the mechanism before
the fix existed. The debounce remains right for transients; it is the wrong
instrument against a duty-cycling load. **Decided and deployed 2026-09-23:**
3 of the last 5 readings. See the top of this file.

**Live Telegram delivery is no longer untested.** The day-4 alert was sent and
acknowledged (`enviado`) at ~22:15 on 09-19 — the first real message, where
everything before had only verified the API contract. Remove this from the
open list.

**Days 5-7 (09-20, 09-21, 09-22). Every software check clean on all three.**
These were checked together on 09-22: the 21:37 session job had lapsed, so
days 5 and 6 were never checked on the day.

    09-20  window 01:00-07:41  130 Wh  opened 27.7 V  throws 16  1 override
    09-21  window 01:00-04:08   90 Wh  opened 25.2 V  throws 18  2 overrides
    09-22  window 01:00-07:36  145 Wh  opened 27.3 V  throws 16  1 override

- `release_pending` false with the window shut, `loop_error` null,
  `pop_drift_stuck` false, `config_error` null, **no service restart since
  09-16 18:09** (so no stalls, 0 telemetry gaps over 240 s), no tracebacks in
  the journal, 337 tests green.
- Every override came after exactly 2 pending ticks. None fired on a single one.
- **Relay throws 16 / 18 / 16**, down from 96. Not because of the controller:
  **the 1.2 kW load has largely gone** (see 2b).
- Export 0 / 0 / 0 Wh (09-19: 2 Wh).
- Write failures are isolated episodes, each recovered on the next tick:
  2, 1, 5. 09-22's 5 is above the usual 1-2. Watch it, don't act on it yet.
  One was the 09-22 07:36 override's `POP00` timing out; the next tick
  applied it 72 s later.

**The debounce has now saved a real transient, the first time it has.** On
09-21 at 14:47:58 the telemetry showed a single `L` sample at 1190 W, back to
`B` 10 s later. One tick landed on it, reading `hardware_override_pending`,
and the next reset the count. The old code would have latched on that one
reading and shut the daytime window for the rest of the afternoon.

**The rule for whether the nightly window runs has been replaced.** The old
rule ("runs only if the charger was still on it at 01:00") was broken on 09-21:
the window opened at a **rested 25.2 V**, charging having stopped at 18:04,
and the inverter went to `B` within a minute and ran for 3 h. The new rule fits
all six nights on record, but it is **n=1 on the case that separates the two
rules**:

    night of   opened at      last exit from B the previous day      result
    09-16      25.0 rested    hardware transfer (override 18:47)     REFUSED
    09-18      25.1 rested    hardware transfer (override 18:38)     REFUSED
    09-21      25.2 rested    software POP00 at 19:00 window close   RAN
    09-17/19/20/22  27+ float software POP00 at 19:00 window close   RAN

So **program 13 seems to act as a lockout that the inverter's own low-battery
transfer arms**, not as a general entry threshold. After a program-12 transfer
(or a load sag that trips it) the unit will not go back to battery below 27 V.
After a commanded `POP00` it accepts battery mode at any voltage above program
12. The two refusals happened to fall on nights when both conditions held (a
rested pack *and* a day ending in a hardware transfer), which is how the old
rule fit four nights.

To confirm: a night that opens rested after a day ending in a hardware
transfer should be refused. A night that opens rested after a clean 19:00
close should run, but shorter (09-21: cut at 04:08 by program 12, 90 Wh).
**Check how the previous day's last battery span ended before looking anywhere
else.**

**Front-panel programs confirmed 2026-09-18** (read off the unit, since
`QPIRI` misreports all three): **11 = 10 A, 12 = 24 V, 13 = 27 V**. Program 12
confirms `PBCV24.0` took. Program 13 at 27 V is why the inverter refused
battery mode at 25.0 V on 09-16 — and it should stay there: the 3 V gap to
program 12 is what damps the 1.2 kW chatter, and a rested-full pack is only
~25.9 V so lowering it towards reachable would cut the gap to ~1.5 V against a
~2 V sag. Full reasoning in NOTES.md.

**Programs 26/27 changed and verified 2026-09-18** — the first setting change
on this installation ever *confirmed* rather than assumed. Bulk **28.2 ->
29.0 V**, float **27.0 -> 27.4 V**, both factory defaults below the SOLARX
datasheet's specified 28.80-29.40 / 27.20-27.80. Confirmation path: change it,
**reboot the unit**, then `GET /api/ratings?refresh=1` — without `refresh` the
service serves ratings cached at its own startup and shows the old value.

Two consequences to watch:

- **Float used to sit exactly on program 13.** 27.0 V float against a 27.0 V
  return-to-battery threshold was zero margin; it is now 0.4 V above, which
  should make the "still on float at 01:00" case above more reliable. Whether
  it also lets a *nearly* rested pack qualify is unknown — do not assume it.
- The pack should start resting **higher** in the evenings as it finally
  saturates, moving towards ~25.9 V. **Rested evening voltage is the measure
  of whether the undercharging is fixed**, not the charging voltage. Several
  charge cycles before this means anything.

Consequence worth carrying: `floor_voltage` (24.0) and program 12 (24 V) are
now known to be *identical*. The software floor cannot act first, and its load
gate stops it counting under the 1.2 kW load at all. Raising it to ~24.5 V
would let the software act first with its debounce and latch instead of
taking a `hardware_override` every time. **Raised to 24.5 V 2026-09-23.** The
load gate turned out not to matter: ~74% of night readings are under 10 W, so
there are plenty of quiet samples. What stopped the floor firing was that it
sat exactly on program 12.

**The 1.2 kW load is now the dominant problem and still has no block.** It ran
hours 9, 10, 11, 15, 16, 17, 19 and 20 today — 72 samples in hour 17 alone.
All seven overrides were it knocking the daytime window back, roughly every
23 minutes through the afternoon. The window keeps trying to open into a load
it cannot carry. The debounce makes that survivable; it does not make it
right.

---

## Settled configuration — not under test

`battery_window` **01:00-08:00** nightly, floor **24.5 V** (from 2026-09-23;
see the top of this file), resume 25.3 V, 3 confirmations. Daytime window
08:00-19:00, enter 80 W, exit 50 W, dwell 1200 s, heavy-load hold-off 800 W /
3600 s. Pump block 19:00-21:15. `grid_charge` **exclusive**, thresholds
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
              09-16   14.1
              09-17    1.7
              09-18   11.9   <- all three clean coverage, 0 null/144
              09-19    2 Wh exported   } ratio not computed: auto-energy's
              09-20    0 Wh exported   } /api/history now serves only the
              09-21    0 Wh exported   } current day, so past generation
              09-22    0.0   (0 Wh / ~0.72 kWh, 1 null/127)
              mean 45.6 | median 39.4 | spread 1.2-113.0  (through 09-18)

**Five consecutive days at 1.2-14.1 against a 115.6 baseline.** That is well
outside the earlier 2.3-113.0 spread and no longer looks like day-to-day
variance — but 2b's warning stands and is the first thing to rule out: the
1.2 kW load is soaking the surplus for free, so this is not yet evidence the
battery is doing it. The two are separable by hour (that load runs 16:00-20:00;
export accrues 11:00-16:00) and that separation is worth making properly before
anything is concluded from this run of low numbers.

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
    09-16   16 (76), 17 (37)                    + 19 (70), 20 (31)
    09-17    9 (38), 10,11, 15 (27), 16 (58), 17 (72)  + 20 (30)
    09-18   14 (43), 16 (23)                    + 20 (27)   <- quiet day, 100 total
    09-19   10,11 (83), 13 (60), 14, 16 (66), 17 (91), 18 (48)  + 20 (27)
    09-20   15 (1)                              + 19 (15), 20 (15)   33 total
    09-21    9 (5), 13, 14, 16 (1-2 each)       + 19 (9), 20 (26)    44 total
    09-22   11 (1)                              + 20 (7)              8 total

**From 09-20 the load has almost gone**: 415 samples on 09-19, then 33 / 44 /
8, and nearly all of that is the evening pump in hours 19-20. That fits the
09-18 finding that the pump is also started by hand whenever someone is
gardening. It is intermittent, not a new permanent load, and it cannot be
blocked by time of day. Its absence is why relay throws fell from 96 to ~16.
**The block/move decision is less urgent while it stays away, but it is
unresolved.** It will come back the next time someone gardens.

**Export stayed at 0 Wh on all three days without it.** That is the first
evidence against the idea that the load was producing the low ratios in §1 by
absorbing the surplus for free. With the load gone, the surplus was still
absorbed. Moderate days only (09-22: ~0.72 kWh), so this says nothing yet
about bright ones.

**09-19 was the peak day: 414 samples and 336 on/off cycles, both records,
and it started at 10:00.** A block that covered 16:00-20:00 would have
missed more than half of it. This is the third consecutive revision of these
hours, which is the argument against choosing a block at all.

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

**Confirmed on 09-18, and program 13 is no longer "likely" but measured** —
the unit was read off the panel at 27 V the same day. The window was refused
again at 25.1 V, and this time the whole sequence is on record in the trace
(01:00:35 `window` -> 01:01/01:02 `hardware_override_pending` -> 01:03:37
`hardware_override`). So this is a standing hardware constraint, not a bug and
not something the nine fixes address, and the software's response is the
correct one. **But "below ~27 V the window cannot run" is too strong.** 09-21
ran from 25.2 V. The refusal seems to need the previous day to have ended in
a hardware transfer. See the days 5-7 entry above.

## 2e. FIXED 2026-09-23: `min_battery_voltage` had the same float-vs-resting flaw

Fixed by releasing on the charger reaching float rather than on voltage. See
`safety.LowBatteryLatch` and the top of this file. `min_battery_voltage = 25.5` is compared
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
