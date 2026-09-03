# Field notes — measurements, incidents, plans

Everything here is **reference**, not orientation. [CLAUDE.md](CLAUDE.md) is
what a new session needs to avoid breaking things; this is what it needs when
a specific question comes up. Look here for: what was actually measured on
this installation, which telemetry fields can be trusted, what went wrong and
why, the current deployed configuration and the reasoning behind it, and what
is planned or still open.

Numbered gotchas referenced throughout ("gotcha #1", "#3"...) are in
[CLAUDE.md](CLAUDE.md), not here.

Corrections are kept next to the values they replace rather than being edited
away. Several numbers in this project were read wrongly more than once, and
knowing *how* a number was misread has repeatedly turned out to be as useful
as the number itself.

---

### Measured in battery mode, 2026-08-25 — two field corrections

First live `POP=02` run with the loads actually on the pack:

| Minute | V | Output | What |
|---|---|---|---|
| 0 | 26.7 | 1 W | switch to `POP=02` |
| 1-3 | 26.1 → 25.8 | 1 W | float surface charge collapsing |
| 4-16 | 25.8 | 1 W | flat — fridge was disconnected for maintenance |
| 17-19 | 25.5 → 25.4 | **46 W** | fridge compressor running |
| 20 | 25.7 | 1 W | stops, voltage recovers |

- **`ac_output_active_power` DOES work in battery mode** — it read 46 W,
  matching the Shelly's independent 45-46 W for this fridge. Combined with
  the earlier finding that it also reports large pass-through loads in
  bypass, the field is best described as *floored at 1 W below some
  threshold*, not as a placeholder.
- **`battery_discharge_current` does NOT work.** It read **0.0 A while 46 W
  was leaving the pack** (~1.8 A at 25.5 V). Since `battery_power_w` is
  derived from `battery_net_current`, the dashboard's battery-power chart is
  blind in battery mode too. Do not use either to prove a discharge; use the
  voltage trend and `ac_output_active_power`.
- **Internal resistance is around 0.045 Ω, which is NORMAL for this pack.**
  Measured from the fridge compressor's inrush, which is instantaneous and
  therefore actually ohmic: **1150 W took the pack from 25.7 V to 23.5 V**,
  i.e. ~49 A for 2.2 V.
  Two earlier readings of this number were wrong and are corrected here.
  First, 0.2 Ω, derived from "0.4 V of sag at 46 W" — but most of that 0.4 V
  was real discharge accumulating over minutes, not an ohmic step. Second,
  "roughly double what is healthy, aged but serviceable" — that compared a
  **pack** figure against a **single-battery** one. This is two SOLARX 30 in
  **series**, so their resistances add: the right reference is ~0.02-0.04 Ω,
  and the measurement is taken at the *inverter terminals*, so it also
  includes cabling, connections and the unit's own DC-side drop. **The pack
  is new and nothing here suggests otherwise.** Do not cite this number as
  evidence of degradation.
  Its real use is the surge headroom below, not battery health.
- **That 23.5 V matters operationally.** Program 12 hands the loads back to
  utility at ~23.0 V, so a 46 W fridge's *starting* surge comes within half a
  volt of tripping the changeover. It is direct evidence for the pump
  argument: if ~1.2 kW gets that close, a ~2 kW motor will not start from
  this pack. Note this is a statement about **pack size versus surge**, not
  about pack condition — a healthy 30 Ah bank simply cannot deliver a 2 kW
  motor's starting current without the voltage collapsing.
- **A voltage read while the charger is on says nothing about state of
  charge, and neither does the percentage.** Watched live: `POP` went to
  utility and within 5 minutes the pack read 27.0 V / 100% having been
  25.4 V / 80% — no meaningful energy went in, the terminal voltage simply
  rose under charge and the SoC readout, being voltage-derived, followed it.
  This is the same trap as the float-vs-resting one above and it was fallen
  into twice in one day. **Only read the pack with the charger off.**

---

## Incident log

- **2026-08-24 ~09:03-09:06 local — beeping, panel "Fault 01".** The
  manual (section 5.5) confirms **Fault 01 = "Fan is locked when inverter
  is off"** — the earlier guess was right but was unverified at the time.
  Worth physically checking the fan spins freely. The Pi's
  own kernel log shows `hwmon1: Undervoltage detected!` at boot plus an
  unclean-shutdown journald message, so this was a genuine brief electrical
  disturbance on the shared circuit, not a software glitch. "Fault 01" is
  *commonly* a fan-lock fault on this family but that was **never verified**
  against this unit's fault table. The persistent `QPIWS` bit 5 (commonly
  "line fail warning") seen since 2026-08-23 is probably the same cause.
  Root cause unconfirmed — check the wiring around the Pi's PSU and the
  inverter's AC input if it recurs. Config and service survived cleanly.
- **2026-08-24 — grid-export flapped PCP01<->PCP03 every ~2 min for 5+
  hours** before anyone noticed. The original -50/+20 W hysteresis was a
  guess; real `net_balance` swings by hundreds of watts. Widened to
  -150/+150 W with a 300 s dwell. Battery current stayed 0 A throughout, so
  nothing was actually being charged — pure wasted SET churn.
- **2026-08-24 — `auto-energy` was reporting a fabricated 0.0 W** for over
  an hour while its grid Shelly was powered off, because
  `fetch_shelly_grid()` returned `0.0` on failure. Fixed there to return
  `None`; see that project's repo.
- **2026-08-25 night to 2026-08-26 ~00:25 — `grid_charge` flapped
  PCP03<->PCP01 roughly every 25-30 min all night**, ~10 grid-bought
  recharge bursts, confirmed in `telemetry-2026-08-25.csv`. Root cause: the
  previous day's "no sun -> idle" fix (see the surplus-signal section below)
  only changed the *desired* state — the actual write still routed through
  `apply_low_battery_floor`, which force-charges below
  `min_battery_voltage` (25.5 V) with no day/night distinction. The pack
  rests right at that line overnight (no discharge in bypass, tiny standby
  draw), so it kept tripping. **Diagnosed and fixed by a second AI session
  working directly on `palacoulo-inverter`** (the user was out of credits
  here) while this session was elsewhere: `grid_charge` now sends **no PCP
  command at all** when solar is below `SOLAR_FLOOR_W`, bypassing the
  low-battery override entirely at night. Verified safe for this specific
  installation: there is no PV connected (gotcha #4), so `PCP03` at night
  was already a no-op, and in bypass the pack neither charges nor
  discharges regardless of PCP — removing the override loses nothing here.
  That session also added **persistent write audit** (`web_audit.json`,
  `InverterService.audit_path`) and **persisted the battery window's
  recovering/below-floor latch** (`web_battery_window.json.state`) so a
  restart mid-window can't forget tonight's discharge already happened and
  re-open the pack to a second cycle.
  The fix was live and working (`serve.py` restarted 2026-08-26 00:39,
  confirmed via `/api/grid-charge` showing `desired_state:
  disabled_no_solar`) but **uncommitted** — found via `git status` on the
  Pi showing four modified files and three untracked ones. Reviewed,
  pulled into this repo, given test coverage, and committed. Process note:
  the pull (`rsync` from the Pi) briefly discarded genuine uncommitted
  local edits to `README.md`/`test_webapp.py` sitting in *this* machine's
  working tree — apparently the other session's own local work here,
  clobbered by not checking `git status` before overwriting. Equivalent
  coverage was rewritten from scratch; nothing about the fix itself was
  lost, only that specific draft.
  **Separately, `battery_window`'s own single-discharge-per-night logic
  worked exactly as designed** the same night: one discharge 21:15→21:35,
  latched at the floor, held `POP=00` for the rest of the night since the
  window can't release mid-window (spans 21:15→08:00). That part was never
  the problem.
- **2026-08-26 ~01:11-07:57 — the 24.0 V floor was reached, but not by the
  software, and the class had no way to notice.** The window was manually
  re-armed at 01:11 (user request, to get a clean overnight test after the
  restart above ate the first attempt) and discharged cleanly for 4.5 hours
  down to **23.9 V** — deeper than the ~25.4-25.6 V ceiling seen on prior
  nights, confirming the floor genuinely is reachable. But the switch back
  to utility at **05:46:22** was the inverter's own decision, not
  `battery_window`'s: the fridge compressor ran continuously for the ~4.5
  minutes leading up to it, which is exactly the load level
  `FLOOR_MAX_LOAD_W` excludes from the floor count (by design, to avoid
  latching on the compressor's own 0.4 V sag) — so `below_floor` stayed at
  0 throughout and the software's own `floor` reason never fired. For the
  next **two hours**, `battery_window` kept reporting `target=02, reason:
  window, "already POP02; nothing to do"`, because it only tracks what it
  last *wrote*, with no check against what the device is actually doing.
  Not dangerous (grid is the safe side), but the dashboard was silently
  wrong for two hours. **Fixed the same day**: `InverterService.mode()`
  exposes the live `QMOD` letter, and `BatteryWindow._reconcile_with_device()`
  compares it against what was last applied on every tick, before deciding
  anything else. A mismatch where we believe battery but the device says
  otherwise closes the latch immediately (matches reality: tonight's
  discharge is over) and forgets the stale belief so the next decision
  issues a real, dwell-exempt `POP00` to converge state explicitly. The
  reverse case -- device on battery while we believe grid, most likely a
  real outage -- is surfaced (`reason: unexpected_battery`) but
  deliberately not fought, since forcing `POP00` would do nothing useful
  while the grid is actually down. Both are shown on the dashboard next to
  the believed state. 203 tests.

- **2026-08-28 to 2026-08-30 — the nightly window quietly stopped working,
  and the inverter cycled the pack unsupervised instead.** Found by the user
  ("it is not using the batteries much"), which was exactly right about the
  window and exactly backwards about the pack.

  | Day | `window` ticks | `unexpected_battery` | B-cycles | wrote POP02? |
  |---|---|---|---|---|
  | 08-26 | 633 | 0 | 2 | yes |
  | 08-27 | 443 | 0 | 2 | yes |
  | 08-28 | 270 | 0 | 2 | yes |
  | 08-29 | **2** | 306 | 4 | yes |
  | 08-30 | **0** | **483** | **13** | **none** |

  **Root cause was a design mistake in the 2026-08-26 reconciliation work.**
  `_reconcile_with_device()` correctly detected the device sitting in battery
  mode while this server believed `POP=00` — 483 times, across ~8 hours — and
  deliberately did nothing, on the reasoning that it was "most likely a real
  grid outage" and forcing `POP00` would be useless. Mains was healthy the
  entire time (227-233 V). The device's `POP` had simply drifted out of sync,
  and because the normal write path compares against `_last_applied_pop` it
  kept concluding *"já em POP00; nada a fazer"*. Nothing could break the
  deadlock. It resolved **by accident** when a service restart at 15:01
  re-seeded `_last_applied_pop` and produced a real write.

  Cost to the pack: 13 cycles in a day against the 2 by design, and
  **20.7 V under a 1.3 kW load** (08:55 22.2 V/1266 W, 10:41 20.7 V/1328 W,
  11:04 21.9 V/1305 W) — below the datasheet's own 1.75 V/cell (21.0 V)
  discharge limit. Note the resting-voltage floor gives no protection at all
  during a large load's inrush; it was calibrated for a ~66 W draw.

  **Fixed:** `InverterService.grid_voltage()` now lets the reconciler tell the
  two cases apart. Grid present (>= `GRID_PRESENT_MIN_V`, 180 V) plus device
  on battery means drift, not outage: after `POP_DRIFT_CONFIRMATIONS` (3
  ticks, ~3 min, so a brief sag doesn't cause relay churn) it clears the
  cached POP and re-asserts, bypassing the anti-flap dwell. Grid genuinely
  absent still returns `unexpected_battery` and is never fought. The trace
  gained `device_mode` and `device_mismatch` columns — the post-mortem above
  had to infer the mismatch from the `reason` column because neither was
  recorded.

  **Also observed, unexplained:** serial frame corruption jumped from 2-5
  garbled `QMOD`/`QPIGS` frames per day on the old Pi to **29 and 33** on
  2026-08-29/30, i.e. immediately after the 08-28 move to `palacoulo-rasp`.
  Worth checking the USB cable/port, or easing `poll_interval`. Does not
  explain the above, but is a real degradation of the link.

- **2026-08-30 18:29 — the USB-serial adapter wedged with the inverter in
  `POP=02`, and nothing in software could reach it.** During a short
  supervised test window the Prolific PL2303 dropped off the bus and
  re-enumerated (`ttyUSB0` → `ttyUSB1`). Every layer failed:

      termios.error: (5, 'Input/output error')      -- port opens, then errors
      pl2303 ttyUSB1: pl2303_set_control_lines - failed: -32
      usb 1-1.4: can't set config #1, error -32     -- device unbind no help

  The `by-id` path was already in use and correctly followed the new node,
  so that was not the problem — the adapter itself was unresponsive. What
  recovered it was **unbinding and re-binding the parent hub** (`1-1`, not
  `1-1.4`), then restarting the service. The inverter had been sitting on
  battery for ~8 minutes with no way to command it back.

  Two things follow, and the second reverses earlier advice.

  **`usb_watchdog.py` + `usb-watchdog.service`** now do that recovery
  unattended: poll `/api/status`, and if `connected` is false with the last
  successful read older than `STALE_AFTER_S` (300 s, well clear of ordinary
  frame loss), reset the hub and restart the service, up to `MAX_ATTEMPTS`
  before giving up and saying it needs hands. Detection deliberately asks
  the service rather than trying to open the port: a wedged PL2303 opens
  fine and only errors on first use.

  **For an unattended absence, disable the battery window.** The reasoning
  given a few hours earlier — that an enabled window keeps POP supervision
  active, so disabling it is the less safe choice — does not survive this
  incident. It assumed the link stays up. It does not:

  | | window enabled | window disabled |
  |---|---|---|
  | Writes `POP=02` nightly | yes | never |
  | Adapter dies mid-window | **stuck on battery, discharging** | already `POP=00` |
  | Resulting state | pack cycled uncontrolled | loads on grid, pack full |

  With the window off, the resting state *is* the safe state and needs no
  successful write to reach or hold.

  **Applied, then reversed the same evening at the user's request** — they
  want the pack used, and the argument for the safe-state config was weaker
  than it looked at the time it was made. Two reasons it was reconsidered:

  1. `usb_watchdog.py` did not exist when the recommendation was made. It
     does now, and it closes exactly the failure mode the recommendation was
     hedging against.
  2. The inverter's own program 12 is a hard backstop underneath everything.
     Even with the link dead and the watchdog exhausted, the pack does not
     run flat — it changes over to utility around 23.9-25.4 V on its own.
     The realistic worst case is uncontrolled cycling (wear), not a
     destroyed pack.

  **So the deployed state for the absence is the normal one**: window
  21:15-08:00 at a 24.0 V floor, grid-export control on in `exclusive` mode.
  The residual risk is that if the adapter wedges mid-window *and* the
  watchdog cannot recover it, the pack cycles unsupervised until someone
  looks — costing cycle life, bounded by program 12.

- **2026-08-30 — the pump's real window, measured rather than recalled.**
  Loads above 600 W on the inverter output, all days, local time:

      19:00-19:30    4 samples   1183-1435 W   (days 28, 29)
      19:30-20:00   37 samples   1160-1280 W   (days 24, 25, 26)
      20:00-20:45  154 samples   1128-1293 W   (days 24, 25, 26)

  The user recalls the pump as starting at 20:00 and asked for
  `HARD_FORBIDDEN` to be moved accordingly. **It was not moved**: there is
  measured 1.2-1.4 kW activity from 19:00 on recent days, so the existing
  19:00-21:15 block is doing real work at its lower edge. Keep it unless
  the pump is physically re-timed or moved off the protected output.

  Worth noting the block is belt-and-braces for the *normal* schedule, not
  load-bearing: the window opens at 21:15, after it closes, so the two never
  meet. It only bites on a manually-shortened test window, which is what
  the exchange above was actually about. The 19:00-19:30 samples are also
  few (4 across two days, versus 154 after 20:00), so they may well be some
  other appliance rather than the pump running early — the block is kept
  because nothing is lost by keeping it, not because that is settled.

- **2026-08-31 — the window moved from night to early morning (04:30-08:00),
  because the headroom it creates was being destroyed before dawn.**
  Reconstructed from the 08-30 night, which ran cleanly and still achieved
  nothing useful:

      21:15 - 23:38   battery, 25.5 -> 24.8 V   (2h23, genuinely discharging)
      23:38 - 04:15   grid, NOT charging, resting at 24.7-24.8 V   (4h30 idle)
      04:15 - 04:30   charges at 10 A -> 28.2 V, 100%
      04:30 - 08:00   float, full

  Verified the discharge was real rather than nominal, from two independent
  instruments: the inverter reported mode B with 0.0 A charging current
  throughout, and Shelly channel 1 measured **0.3-0.9 W** entering the
  inverter for the whole two hours (against 34 W just before it opened). The
  fridge, light and Pi were genuinely running off the pack.

  The problem is the timing, not the mechanism. By sunrise (~07:10) the pack
  was back at 100%, so there was **zero absorption headroom** exactly when
  solar starts — which is the entire reason the discharge exists. The 4h30
  in the middle is worse than wasted: the loads are on grid, the pack is
  neither supplying nor receiving, and a lead-acid resting part-charged is
  the one state to avoid.

  Moved to **04:30-08:00** so the discharge lands against dawn: full at
  04:30, program 12 cuts around 06:50 at ~24.8 V, and the pack meets the sun
  with room in it. Same amount of battery use, placed where it is useful,
  and the part-charged idle time drops from 4h30 to about 20 minutes. Also
  clear of the pump window entirely.

  Note the 04:15 recharge was **accidental**, not designed: `grid_charge` is
  `disabled_no_solar` overnight and writes nothing, so the pack should have
  stayed low. A momentary solar reading let a normal evaluation through, the
  low-battery floor saw 24.7 V under `min_battery_voltage` (25.5 V), and
  raised `idle_pcp` to `01`. Worth understanding properly before relying on
  the pack being full at 04:30.

- **2026-09-01 00:34 to 15:02 — the whole system was dead for 14.5 hours and
  both safety nets watched it happen.** The serial thread wedged inside a
  read while holding its lock. Consequences: no telemetry file was written
  for the day at all, and the **04:30 battery window never ran** — the first
  night of the new schedule, missed entirely.

  What makes this worth recording is that the process stayed *alive*:

  - **systemd** `Restart=always` never fired. It watches for the process
    exiting; this one was hung, not dead.
  - **`usb_watchdog.py` saw it and skipped on purpose.** It logged
    `skipping: service unreachable: timed out` every five minutes through
    the night, because an unreachable service returned `None` ("cannot
    tell, do nothing") on the reasoning that systemd would handle it and a
    bus reset would not help. Both halves of that reasoning were wrong.
  - **A liveness probe would have missed it too.** `/api/health` kept
    answering 200 the whole time; only `/api/status`, which needs the
    serial lock, blocked.

  Fixed at both levels, because either alone still leaves a gap:

  1. `_service_health()` now returns **False** for an unreachable service —
     a fault to act on. Only an unreadable config is still "none of our
     business". Recovery tries a service restart first (a wedged thread
     needs the process replaced, not the bus cycled, and it disturbs
     nothing else on the hub), and falls back to the bus reset.
  2. `InverterService` grew a `_stall_loop` thread that calls `os._exit(1)`
     if no read has succeeded in `STALL_EXIT_S` (900 s), so systemd's
     restart becomes reachable. Separate thread on purpose: it has to keep
     running when the poll thread is the stuck one. `os._exit` rather than a
     clean exit for the same reason. It only arms after a first success, so
     an adapter missing at boot does not become a restart loop — that case
     belongs to the external watchdog, which has attempt limits.

  The pack was at `POP=00` throughout, so loads stayed on grid and nothing
  was at risk. That was luck of timing, not design: had it wedged during a
  window, the pack would have sat discharging with nothing able to command
  it back.

- **2026-09-03 correction: the solar Shelly was never offline, it is out of
  wifi range.** Recorded below for two days as physically absent and needing
  someone on site. It is not. Queried directly on 2026-09-03:

      uptime            864711 s  =  240 h  =  10 days
      restart_required  False
      RSSI              -87 dBm
      model             SNSW-001P16EU (Shelly Plus 1PM, Gen2)
      fw                20260311-095847/1.7.5-g9979d16

  It has been powered and running continuously right through the period we
  called it dead — no reboot, no DHCP change, same IP. **-87 dBm is the
  cause**: marginal signal, where a device drops off the AP and returns on
  its own. The fix is an AP or repeater nearer the panels, not a site visit.

  It also explains an old note in CLAUDE.md that direct Shelly HTTP "did not
  work when tried from either Pi (empty response)". It is a **Gen2** device:
  `/status` returns 404, `/rpc/Shelly.GetStatus` works. Wrong URL, not wrong
  network. It does not answer ICMP either, so `ping` is not a test of
  whether it is alive — use the RPC endpoint.

  Consequence for the fallback below: it was written for a *clean* outage
  (meter gone, `ac_solar_w` null, export decides). At this signal level the
  real failure is dropped frames, so `ac_solar_w` alternates between a
  number and null tick by tick — which switches which *test* is used, not
  merely where the reading sits. That is why `generating` is now debounced;
  see the 2026-09-03 dawn flap entry.

- **2026-08-31 — the solar Shelly (192.168.188.25) appeared offline** and did
  so since at least that morning: no ping, no HTTP, and `auto-energy` reports
  `ac_solar_w: null` / `house_power_w: null` while its other channels still
  read fine. This **disarms the night-time protection**: `grid_charge` skips
  the `disabled_no_solar` branch entirely when solar is `None`, because the
  test is `solar is not None and solar < SOLAR_FLOOR_W`. The safety rule was
  written as *proceed unless we know there is no sun*, when it should be
  *hold unless we know there is sun*.

  **Fixed 2026-09-01, and the fallback is better than simply holding.**
  Export is proof of generation — you cannot export without generating — so
  with no solar reading the controller uses a negative surplus signal to
  settle the question instead. The signal is `net_balance` minus the
  inverter's own draw, so the charger cannot drive it negative by running,
  and after dark it sits at +80-100 W on this house, which correctly reads
  as no generation. A *working* meter below `SOLAR_FLOOR_W` still takes
  precedence: direct evidence beats inference. Confirmed live the same
  afternoon with the Shelly still down: `solar: None`, signal −126.5 W,
  decision `charging`, `PCP01` applied.

  The Shelly itself is still offline and worth recovering — the fallback
  only works while something is actually exporting, so it cannot tell
  "cloudy midday" from "night".

---

## Loads on this installation

Everything below is on the inverter's **protected output** — stated by the
user 2026-08-24:

| Load | Notes |
|---|---|
| Fridge | ~45-46 W idle, ~1223 W compressor inrush |
| **Water pump + remote** | **~2 kW, every ~10 min from 20:00-21:00. TRIPS THE INVERTER if run from battery.** |
| Light | small |
| Remote garage door | intermittent |
| **Raspberry Pi (`palacoulo-inverter`)** | was the logger, on this output. After 2026-08-28 the logger is `palacoulo-rasp`, not on this circuit. |

**An earlier note here claimed the pump was NOT on the output. That was
wrong.** It came from reading `ac_output_active_power` as 1 W at 20:58
while the house drew 1623 W — but the inverter was in bypass, where that
field is a fixed placeholder (gotcha #3). The measurement proved nothing;
the conclusion was invented. Third time the bypass placeholder caused a
wrong call, which is why the dashboard now renders it as "—".

### The pump is a latent fault, not just an inconvenience

The inverter is rated **3600 VA**. A ~2 kW motor's starting surge is
several times its running draw, so the inverter cannot start it from
battery — the user reports it trips.

That risk is **not avoided by staying on `POP=00`**. In utility-first mode
the inverter still transfers to battery automatically when the grid fails.
So *any* grid outage between 20:00 and 21:00 will have the pump attempt to
start on battery, trip the inverter, and drop the whole protected output —
**fridge, light, garage door and the Pi included**. The backup is
guaranteed to fail in exactly the window it is most likely to be tested.

**Recommendation: move the pump off the protected output.** It is the one
change that makes this battery do its job. The remaining loads (fridge +
light + Pi, ~50-60 W) are a comfortable fit for ~360 Wh usable — roughly
6 hours — and none of them have a surge the inverter can't handle.

This also very likely explains the 2026-08-24 09:05 Fault 01: the unit was
in `POP=02` (battery priority) at the time, so any large load starting
would trip it, killing the output and with it the Pi — which is precisely
what the kernel log shows. That is a better fit than the "brief electrical
disturbance" originally guessed, though it remains unproven.

---

## Measured 2026-08-25 — the POP question is answered

A full 32 h of Shelly channel 1 (`inverter_input_w`) plus the inverter's own
telemetry, with **no reboots** (Pi up 15 h, service since 2026-08-24 16:43)
and nothing touched on the hardware. This is better data than the planned
`POP=02` test would have produced, and at zero risk.

**Note the clocks differ:** `telemetry/*.csv` timestamps are **UTC**;
`auto-energy`'s history buckets are **local (WEST, UTC+1)**. Cross-referencing
the two without the shift will misplace every event by an hour.

| Measure | Value |
|---|---|
| Protected output, base load (fridge + Pi + light) | **~66 W → 1.59 kWh/day** |
| Pump, 19:30-20:30 local | ~1200-1300 W running, 0.36 kWh/day |
| Grid charger, 09:10-11:50 local | 250-596 W (the morning recharge) |
| Solar generated | ~0.39 kWh/day |
| **Exported to grid** | **0.000 kWh — zero negative `net_balance` blocks in 32 h** |
| Battery | 27.0-27.1 V, 100%, discharge current **0.0 A throughout** |

**On the economics: alternating POP saves nothing.** The case for it was
capturing surplus solar, and there is no surplus — solar is already 100%
self-consumed, so every watt-hour put into the pack is bought from the grid.
Round-tripping ~0.36 kWh through lead-acid at ~75% costs ~0.12 kWh, about
**EUR 10/year thrown away**, plus cycle wear, to move consumption that has
no cheaper window to move it to (flat 0.22 EUR/kWh, no bi-horário).

**The user decided to use the pack anyway, and for a reason the economics
miss: compliance.** Exporting is not permitted here, and *a full battery
absorbs nothing* — `PCP01` into a floated pack draws a trickle while the
surplus goes to the grid. Discharging overnight is what creates somewhere
to dump the next day. The measured worst export day is 0.14 kWh against
~0.36 kWh of headroom, so the capacity is sufficient with margin.

That is what `webapp/battery_window.py` implements, with the depth limited
to a shallow cycle (see its four interlocks above). Do not "simplify" it
back to permanent float on the grounds that it saves no money — saving
money was never the point, and permanent float is not ideal for lead-acid
either.

Autonomy on the base load: 360 Wh / 66 W = **~5.5 h**, if the pump is not
on the output.

### Refinement to gotcha #3 — the 1 W is a floor, not a constant

`ac_output_active_power` reported a sustained **1160-1293 W** for 26 samples
at 19h local and 32 at 20h — the pump, passing through the transfer relay.
It was **not** inverting: discharge current stayed 0.0 A and the pack held
27.0 V, which 1200 W from 24 V (50 A) could not do for a second. So the
field does report real pass-through load in bypass, above some threshold
between ~90 W and ~1160 W. It reads 1 W for everything below — which covers
the fridge, so **the operational advice is unchanged: use Shelly channel 1
for load in bypass.** 98.9% of samples were still exactly 1 W. The
threshold has not been located and the mechanism is a guess.

Also: the pump's *running* draw is ~1200-1300 W, not the ~2 kW estimated.
The trip risk stands anyway — a 1200 W motor's starting surge is several
times that, against a 3600 VA unit.

---

## Planned next session (2026-08-25)

Data is accumulating on its own overnight: `telemetry/` on the Pi (battery,
mode, charging) and `auto-energy`'s DB (grid, solar, inverter AC input).
Nothing needs to be left running on the assistant side.

1. **Read what was collected** (no risk): fridge/inverter draw over 24 h
   from the Shelly's channel 1, battery behaviour, whether any export
   happened and when. Note the inverter's own output column is blind while
   in bypass — use channel 1 for load.
2. **Supervised battery-mode test** (~1-2 h, user present): switch to
   `POP=02`, measure real fridge consumption and duty cycle and the actual
   discharge rate, then back to `POP=00`.
   - **Never inside 20:00-21:00** — the pump would try to start on battery
     and trip the inverter, dropping the fridge, the light, the garage door
     and the Pi at once.
   - **Do not leave `POP=02` unattended.** The pack only recharges once it
     falls to program 12's threshold (~23 V), the software low-battery
     floor is dead in that mode (gotcha #1), and the Pi is on the same
     output — so a flat battery takes the logger down with it.
3. **Decide the POP strategy** with real numbers: if the fridge's daily
   draw is small enough that ~0.13 kWh/day of surplus meaningfully refills
   the pack, alternating POP is worth building; if it drains far faster
   than solar can refill, that is just buying grid power at night to run a
   fridge at ~90% efficiency, and `POP=00` stays correct.
4. **Re-verify `PBCV24.0`** once the pack is resting rather than charging.

User's own jobs, independent of the above: set the charge current from the
**front panel** (gotcha #10 — the single change most likely to extend the
battery's life), and confirm what else is wired to the inverter's output.

---

### Deployed configuration, end of 2026-08-25

Three settings, chosen together to serve one goal the user stated plainly:
**export mode by day, battery window by night, grid while the pump runs.**

| Setting | Value | Why |
|---|---|---|
| `grid_charge.mode` | **`exclusive`** | was `override`, which was inert here |
| `export_threshold_w` / `import_threshold_w` | **+30 / +150 W** | positive = pre-empt export |
| `min_battery_voltage` | **25.5 V** | was 24.0 |
| Battery window | **21:15-08:00** | after the pump, before the sun |
| `floor_voltage` | **25.6 V** x 3 readings | above the inverter's own 25.4 V |

**Why `exclusive` and not `override`.** In `override` the controller writes
nothing when there is no surplus, deferring to the scheduler — but the
scheduler is *disabled* on this installation, so "write nothing" means "leave
`PCP01` where it is". Measured over one afternoon: **169 ticks, 1 write**, the
charger drawing 4 A from the grid continuously with no relation to whether
there was any surplus at all. It was not absorbing export; it was simply
always on, and it spent the pack's absorption headroom on grid power. Use
`override` only when a schedule is actually enabled to defer to.

**Why `min_battery_voltage` went to 25.5 V.** `exclusive` writes `PCP03` when
there is no surplus, which on this unit (no PV connected) means "do not
charge". Left alone, the pack would sit part-charged for days whenever
surplus is scarce — it was 0.000 kWh on 2026-08-24 — and lead-acid sulfates
that way. The interlock now gives two bands: **above 25.5 V** charge only on
surplus, preserving headroom; **below 25.5 V** charge regardless.

**Why `floor_voltage` is 25.6 V and not lower.** Program 12 hands the loads
back to utility at **~25.4 V**, so any floor below that never fires — the
hardware acts first, and what you get is the inverter cycling battery/line
every 20-40 minutes all night, buying grid power each time. Measured
2026-08-25: **six cycles in four hours.** At 25.6 V the software decides
first and the latch closes, giving the intended single shallow discharge.

### What to expect, honestly

About **1.5 h of battery per night**, not the whole night — the pack reached
25.5 V in 1h33 on 2026-08-25 — creating roughly **100 Wh** of headroom
against a measured worst-case export day of **140 Wh**. That is adequate but
has no margin, and once the headroom is spent in the morning the afternoon
has none. This reduces export substantially; it does not guarantee zero. A
hard guarantee still needs curtailment or a dump load.

### The discharge floor, against the manufacturer's own curves — 2026-08-26

The pack is confirmed as **SOLARX-30** (Xunzel datasheet, "12V 30Ah C120",
`https://media.adeo.com/media/3295588/media.pdf`) — matches the nameplate
already assumed. Two numbers from it worth keeping:

- **Internal resistance, per the datasheet: 9 mΩ per 12 V unit.** Two in
  series ≈ 18 mΩ nominal. The 45 mΩ measured live 2026-08-25 (see below)
  includes cabling, connectors and the inverter's own DC-side drop, so it
  being roughly 2-2.5× the pure-battery figure is unremarkable, not a sign
  of wear.
- **These are true deep-cycle AGM units, explicitly rated to 100% DoD** —
  "concebidas...para aplicações cíclicas de carga e descarga profunda
  repetida e contínua", up to 2000 cycles, with the 100% capacity figure in
  the spec table defined *at* 100% DoD. Going deep is within the design
  envelope, not an abuse case.

The datasheet's resting open-circuit-voltage-vs-SoC curve (p.5), read
approximately off the graph and converted from per-cell to this 12-cell
(2× 12 V series) pack:

| Pack voltage (rest) | ~SoC | ~DoD |
|---|---|---|
| 25.6 V | ~86% | ~14% |
| 24.0 V | ~37% | ~63% |

And the cycles-vs-DoD curve (same page), read the same way:

| DoD | ~cycles | ~years at 1 discharge/night |
|---|---|---|
| ~14% (25.6 V) | ~1900-2000 | ~5-5.5 |
| ~63% (24.0 V) | ~650-700 | ~1.8-1.9 |

**Decision: keep `floor_voltage` at 24.0 V.** The user reviewed the
datasheet and chose the deeper, shorter-lived cycle deliberately, with the
trade-off above stated plainly rather than discovered later. Not dangerous
per the manufacturer's own spec — a real service-life choice, not a safety
one. `ABSOLUTE_FLOOR_V = 24.0` in `webapp/battery_window.py` is exactly this
value, so there is no headroom below it; the config validator refuses
anything lower.

Caveat on precision: both tables above are read off a printed graph, not a
data sheet of numbers, and the live readings they're compared against are
under whatever standby load happened to be present, not a clean rested
measurement. Treat the percentages as approximate, not calibrated.

### Verified 2026-08-25

- **Charge current 20 A -> 10 A** (front panel, program 11). **Confirmed on
  hardware**, two independent ways, while the inverter cycled itself in and
  out of battery mode: `battery_charging_current` capped at **10 A (max 11)**
  where it previously reached 18-20, and the Shelly showed the charger's AC
  draw at **~320 W while actually charging** against the 596 W measured under
  the old limit. Nothing further to check.

### Applied but NOT yet verified

- **`PBCV24.0`** — ACKed, intent was to raise the recharge point from a
  near-flat 22.0 V to ~50%. `QPIRI` still reports 22.0 V, which proves
  nothing either way (gotcha #2).
  **Complicated by an observation on 2026-08-25:** left in `POP=02`, the unit
  handed the loads back to utility at **~25.4-25.5 V**, repeatedly and
  consistently (six times in four hours). That matches neither 22.0 nor the
  24.0 that was written, and it is well above the 23.0 V the manual gives as
  program 12's default. So either `PBCV` is not the setting that governs this
  changeover, or the value in effect is neither of the two known candidates.
  Unresolved — but **25.4 V is the number that actually governs behaviour**,
  and `webapp/battery_window.py` is configured against it, not against
  `QPIRI`.

---

### Host move 2026-08-28 — USB + automations onto `palacoulo-rasp`

Supersedes the 2026-08-25 "headless inverter Pi, UI only" sketch. The
USB adapter, `serve.py`, and the three automations move to
`palacoulo-rasp` (independent power, same host as EcoPi).
`palacoulo-inverter` is then powered off. Two processes, not one
container: EcoPi Docker `:8000`, inverter-web systemd `:9090`.

Logs from the old Pi: live files in `telemetry/` (the service appends to
the same `telemetry-YYYY-MM-DD.csv` / `gridcharge-*` / `batterywindow-*`
names). Frozen snapshot at cutover in
`telemetry/archive-palacoulo-inverter/`. Read with `read_telemetry.py`.

Still open after the hardware move: one dashboard in EcoPi that renders
this project's `/api/*` (token in `web.json`). Keep the inverter UI on
`:9090` as the emergency page until that exists.

`grid_charge` `source_url` is `http://127.0.0.1:8000/api/live`. Stale
reading still means idle.

### The daytime window's threshold was set from the wrong end — 2026-09-01

`daytime_enter_w` shipped at 150 W. Measured over the afternoon of
2026-09-01 (15:04-19:00, all the data there is for that day — the service
was dead until 15:04), the surplus signal never got near it:

    min -128   p25 -102   median -25   p75 +26   max +94   (n=467)

    above 150 W:   0 samples   (0%)
    above  80 W:  96 samples  (21%)

So the window could not have opened on any sample that day. 150 W was
above the observed maximum, not merely conservative. Lowered to **80 W**;
`daytime_exit_w` stays at 50 W.

The thresholds still cannot collide with the charger: the window leaves
battery mode below 50 W and the charger only starts at or below 30 W, so
there is a 20 W gap between them. That separation is what makes the
number safe to lower, not the yield interlock — though as of today that
interlock is finally live too (it was wired to `is_overriding()`, which is
always False in `mode="exclusive"`; it now asks `is_absorbing_export()`).

One afternoon is not a season. Re-read
`telemetry/gridcharge-*.csv` with `read_telemetry.py` after a few full
days before moving it again — this is the third threshold on this
installation to be guessed wrong on the first try.

Same day, the export charger did work correctly: one clean span
15:04 → 17:30 (2 h 26), two state changes all day, no flapping.


### First clean run of the 04:30-08:00 window — 2026-09-02

The configuration deployed on 2026-09-01 completed a full night. It behaved
exactly as designed, and produced two results that contradict numbers
recorded here earlier.

**The run itself.** One POP write, at 04:30:19, "aplicado"; the device
followed L->B within a minute and stayed there. No `hardware_override`, no
`pop_drift`, no floor event, the latch never closed, `config_error` clear on
all three controllers. Four corrupt `QMOD` frames arrived during the night
and were filtered rather than read as a mode change (the validation added
2026-08-27, working). `grid_charge` sat at `disabled_no_solar` for all 816
ticks and wrote no PCP, which is what it should do in the dark.

**The discharge is load-limited, not pack-limited.**

    26.7 V -> 25.8 V in 15 min      surface charge, not capacity
    min 25.0 V, 25.2 V at 07:53     floor is 24.0 -- never approached
    load 1 W for 72% of samples, 47 W for 28% (compressor duty cycle)
    mean 13.8 W over 3.37 h  ->  ~46 Wh delivered

46 Wh out of ~360 Wh usable. The window is doing its job; there is simply
almost nothing on the protected output to discharge into. **Judge that
against export, not against the pack**: measured export here is 0-140
Wh/day, so one night covers a typical day outright and about a third of a
bad one. The constraint on headroom is the load, and the obvious lever is
window *length*, not anything about the battery.

**Contradiction 1 — program 12 did not change over at ~25.4 V.** This file
states (twice, and `webapp/battery_window.py:94` with it) that the unit
hands the loads back to utility at ~25.4 V, measured as six cycles in four
hours on 2026-08-25; `floor_voltage` was originally sized at 25.6 V *because
of* that number, and the 04:30 window was planned expecting "program 12 cuts
around 06:50 at ~24.8 V". Tonight the pack sat at **25.0 V and the inverter
stayed in battery mode**, for 3 h 23 and counting.

The likeliest explanation is that **`PBCV24.0` did take effect** after all.
It was ACKed on 2026-08-25 and has been carried under "applied but NOT yet
verified" ever since, unconfirmable because `QPIRI` still reports 22.0 V
(gotcha #2) and because no run since has taken the pack low enough to test
it. This is the first evidence either way, and it is consistent: a
changeover moved from ~25.4 V to 24.0 V explains both the old six cycles and
tonight's absence of any.

**Not yet proven, and do not record it as proven.** Tonight bounds the
threshold below 25.0 V; it does not locate it. The window is time-bounded
and the load is tiny, so the pack cannot reach 24 V inside 3.5 hours -- this
configuration will never find the number on its own.

**Contradiction 2 — nothing, but a confirmation at scale.**
`battery_discharge_current` read **0.0 A on 931 of 935 samples** while the
pack was demonstrably supplying 47 W, with two absurd frames above 100 A.
That matches the 2026-08-25 finding exactly and settles it: the field is not
merely unreliable, it is not populated in battery mode. `battery_capacity`
is nearly as bad -- 80% on 913 samples, with 18 spurious 50s, two 100s and
two 26s -- so the SoC readout is quantised and noisy, not a measurement.
Voltage trend and `ac_output_active_power` remain the only usable pair.


### The dawn flap — the last threshold without hysteresis, 2026-09-03

`SOLAR_FLOOR_W` was a bare 5.0 W comparison. Every other threshold in this
system has a band and a confirmation count, and each one grew them after a
flap; this one had its turn at sunrise.

The measured ramp, from `auto-energy` (10-minute buckets):

    07:20  0.4 W    07:40  4.3 W    07:50  7.3 W    08:00  10.2 W

Twenty minutes sitting on the threshold. `generating` flipped on and off
eight times between 07:45 and 07:57. **The surplus signal never moved** —
+17 to +23 W throughout — so nothing about the actual export decision
changed; only this one test did.

It reached the relay, because everything downstream keys off it:

    desired_state flaps -> is_absorbing_export() follows
      -> battery_window alternates yielding/window -> POP is written

Three POP writes in five minutes (07:46, 07:47, 07:50), and the night's
discharge was cut short — the pack went back to 27.0 V at 07:47 having been
at 25.1 V. Only the asymmetric dwell kept it from being worse: `yielding` is
dwell-exempt, returning to `window` is not, so most of the oscillation was
absorbed as "a aguardar 419s".

**Fixed with a band (3 W off, 8 W on) plus 3 confirmations.** Two causes,
one mechanism: the threshold had no hysteresis, *and* the meter is at
-87 dBm and drops frames, so `ac_solar_w` alternates between a number and
null — which switches which test is used, not just where the reading sits.
A confirmation count covers both, because it requires consecutive agreement
regardless of why the samples disagree.

90 s to change its mind, against a sunrise that takes 40 minutes to cross
the band. The first definite evidence after a restart is adopted outright:
there is no previous answer to defend, and refusing to decide for 90 s is
just a slower way of saying "no sun".


### Dump load — sizing it from three days of real export, 2026-09-03

Not built. This is the option CLAUDE.md's "Known limitation, unsolved" and
the Open list below have pointed at since 2026-08-25 without numbers behind
it. There are now three consecutive days of export to size from, so it is
worth writing down properly before it turns into another guessed threshold.

**Why the battery route has a hard ceiling, not just room to tune.** The
protected output averages ~14 W (measured 2026-09-02, fridge duty-cycling at
28%), so headroom created overnight can only ever be ~50-170 Wh depending on
window length -- the load is the limit, not the pack. And the charger has no
middle gear: ~90-110 W at float, ~350-560 W actually charging, nothing in
between. Extending the nightly window is still worth doing and still free,
but it cannot close this gap; it only buys a few extra minutes of charging
against a multi-hour export window.

**Three full days, all following the same shape** -- pack full by
mid-morning, charger tapers to float, midday solar exceeds float draw:

    2026-09-01:  57.5 Wh exported |  160/1178 samples negative | worst -70.6 W
    2026-09-02: 123.8 Wh exported |  464/2842 samples negative | worst -78.6 W
    2026-09-03: 105.1 Wh exported |  311/1809 samples negative | worst -70.3 W (partial day)

Worst instantaneous draw across all three days: **-78.6 W**. The inverter's
own float draw (~90-110 W) already covers a good fraction of a typical
sample; what is missing is consistently under 100 W, not the multi-hundred-W
gap PCP alone would suggest.

**Sizing conclusion: this does not need a big load.** Something resistive in
the 100-200 W range -- a towel rail, a small heater, an immersion element on
a timer window -- switched on the same surplus signal the charger already
uses would cover the measured worst case with margin, without needing
anything close to kW-scale.

**Control shape, not yet built:** the mechanism already exists in
`grid_charge.py` -- `surplus_signal = net_balance - inverter_input_w`, the
same debounced generation check, the same hysteresis pattern. A dump load
would be a second, independent consumer of that signal (own thresholds, own
relay, own dwell), not a modification to the charger. It needs a
Shelly-class relay on the load and nothing more from the existing
automations -- `battery_window` and `grid_charge` do not need to know it
exists, because it does not touch POP or PCP.

**Still the user's call, not a default to build toward:** generation
curtailment via the solar Shelly's relay was ruled out earlier for
unrelated reasons (see CLAUDE.md), and a dump load has a real running cost
of its own -- it turns unusable surplus into heat somewhere, which is only
worth it if that heat is useful (hot water) or the EUR 10/year of avoided
export-adjacent risk is judged worth the hardware. Recorded here so the
decision can be made from these numbers rather than none.


### The nightly window was leaving capacity unused — 2026-09-03

Extended from **04:30-08:00 to 01:00-08:00**. The reason it had been cut
short no longer existed, and nobody had gone back to check.

**Why it was 04:30 in the first place.** With `floor_voltage` at 25.5 V the
window hit the floor in 1h33, latched, and then sat part-charged for 4h30 —
the loads on grid, the pack neither supplying nor receiving, which is the
one state to avoid with lead-acid. Moving the start to 04:30 put the
discharge against dawn instead.

**Why that no longer applies.** The floor is 24.0 V now, and the pack is not
getting anywhere near it. Measured 2026-09-03:

    04:30 -> 07:50   3.33 h   27.0 -> 25.0 V   ~45 Wh   floor 24.0 V never approached

It is stopping on the clock, not on the floor, with a whole volt of margin
left. That is unused capacity, and the latch — the mechanism the short
window was protecting against — never even armed.

**What the extension buys.** Seven hours at the measured ~14 W gives ~95 Wh
instead of ~45 Wh. The full 21:15-08:00 would give ~150 Wh, against measured
export of 58-124 Wh/day, so the order of magnitude is right. Going to 01:00
first rather than straight to 21:15 keeps one clean comparison against the
nights already recorded.

**The wear cost is small, and worth stating because it sounds worse than it
is.** 98 Wh out of a 720 Wh pack is ~14% depth of discharge — still the
shallow-cycling regime the SOLARX-30 datasheet rates at ~2000 cycles, not
the ~650-700 at 63% DoD. This moves from *very* shallow to shallow.

**What it does NOT fix, and the thing to actually watch.** Headroom does not
convert 1:1 into absorbed export, because the charger cannot throttle: it
draws ~350-560 W or nothing, refills opportunistically through the morning,
and is full before export starts. On 2026-09-03 the pack was full at 09:37
and export ran from 10:07. So the question the next few days answer is
narrow: **is the pack still accepting charge past 10:00, and does daily
export fall below ~110 Wh?** If it is full by 09:30 regardless, the extra
headroom is being spent on morning grid power rather than on midday surplus,
and the answer is the dump load after all.

**Also seen the same day, and in the wrong direction:** the daytime window
discharged the pack 17:33-18:35, and it then recharged from the grid at 11 A
between 18:35 and 19:20 — after sunset. `PCP01` was already standing, so the
return to `POP=00` recharged immediately rather than waiting for surplus.
Only ~14 Wh, but it is a round-trip loss, not a saving. Worth a rule that
stops the charger refilling a pack that was deliberately emptied, once there
is data to argue the thresholds from.


### The DVR recorded nothing for four days, and everything looked fine — 2026-09-03

Found by accident while looking at SD-card pressure. The Pi has a **28.6 GB
SanDisk USB stick** (label `dvr-usb`, ext4) that the DVR records to:
`/opt/dvr/shinobi/videos` is a symlink to `/mnt/usb/shinobi/videos`, and the
Shinobi container bind-mounts that symlink.

**It had been unmounted since 2026-08-30 18:29:18.** `mnt-usb.mount` ran for
3h28m from boot and then went inactive; the by-id symlink was recreated four
seconds later, so the stick dropped off the bus and re-enumerated — from
`/dev/sda` to `/dev/sdb` — after systemd had already torn the mount down.
`nofail` in fstab did exactly what it is for and said nothing.

**Every surface said it was healthy.** The fstab UUID matched the stick. The
mountpoint existed. Inside the container `df` reported
`/dev/sda1 29G 2.3G 25G 9%` — plausible numbers, read from stale mount
metadata pointing at a device node that no longer existed. Only actual I/O
failed:

    ls /home/Shinobi/videos  ->  Input/output error

Four days of recordings lost, with a working NVR UI and a container marked
`Up 3 days`.

**Two things worth taking from it.** First, nothing was written to the SD
card underneath: checked by bind-mounting `/` elsewhere and looking beneath
the mountpoint, which was empty. So this was not silently eating the SD card
— it was simply losing the footage. Second, **`os.path.ismount()` would not
have caught it either.** The stale case is *mounted but dead*: statvfs
answers happily and only a real write fails. `daily_report.storage_problems()`
therefore writes a probe file rather than trusting the flag.

**Fixed** by mounting the stick (`sudo mount /mnt/usb`) and restarting the
Shinobi container so it re-resolved the bind mount to `/dev/sdb1`. Verified:
reads work, writes work, 25 GB free.

**Not fixed: the recovery is manual.** Docker resolves bind mounts at
container start, so even an automount that remounted the stick would leave
the container pointing at the dead node until restarted. Detection is now in
the daily report; automatic repair (a unit that restarts Shinobi when the
mount reappears) is not built.

**Likely cause, unproven:** `usb_watchdog.py` recovers the inverter adapter
by unbinding and rebinding the parent hub `1-1`, which resets *every* device
on that hub — the DVR stick included. The journal has rotated past 08-30, so
this cannot be confirmed. Worth checking the next time the watchdog fires,
and worth considering whether the hub-level reset should be narrowed.


---

### Open, roughly by importance

- **`POP` strategy is undecided.** `POP=00` charges but leaves loads on
  grid; `POP=02` runs the fridge off the battery but never recharges.
  Doing both needs alternating `POP`, which throws a physical relay each
  time. Blocked on measuring the fridge's real daily consumption — a full
  day of `telemetry/` plus `auto-energy` data was being collected
  overnight for exactly this.
- **`PBCV24.0` is ACKed but unverified** (gotcha #2/#5). Intent was to
  raise the recharge point from a near-flat 22.0 V to 50%. Confirming it
  needs the pack near 24 V in battery mode and seen to start recharging.
  **Do not record as done until observed.**
- **Zero export cannot currently be guaranteed** — see the limitation under
  CLAUDE.md's "Why grid-export exists here", and "Dump load — sizing it from
  three days of real export" above for the numbers. Generation curtailment via
  the solar Shelly's relay has been ruled out; a dump load is sized (100-200 W)
  but not built, and is the user's call given its own running cost.
- **The pump shares the protected output and trips the inverter on
  battery** — see "Loads on this installation". Moving it off is the
  highest-value physical change available and blocks any reliable backup
  until done.
- The macOS `launchd` plist for the standalone `charge_schedule.py` is
  obsolete now scheduling lives in the server — probably delete it.
- `grid_charge`'s `source_url` defaults to `http://127.0.0.1:8000/api/live`
  (same host as EcoPi). Update it if EcoPi's published port changes.
- `QPIRI` field 14 (`max_charging_current`) reads malformed (`06P`) — never
  resolved, don't trust it.
- `battery_redischarge_voltage` (field 22, reads `52.0`) fits neither the
  raw nor the x2 scale — unexplained.
- `POP=01` has never been tested. `QPIWS` *warning-bit* meanings were never
  verified against this unit; the "line fail warning" reading is the
  published convention, not a confirmed fact. (The *fault* codes are
  different and ARE documented — see below.)
- The charge rate is still ~C/3, above the C/10-C/5 deep-cycle lead-acid
  wants. Program 11's only lower option is 2 A (~C/15), which would make a
  full recharge take most of a day. 10 A was the pragmatic choice, not the
  ideal one.

---

### Source documents

The user supplied the official manual (**"ATA SOLAR INVERTER 3.5KW/5.5KW
User's Manual"**, Tecnoware) on 2026-08-24. It is not in this repo, but it
settled several things that had been guesswork:

- **Fault code table (section 5.5).** `01 = "Fan is locked when inverter is
  off"`, `02` over temperature, `03/04` battery voltage too high/low,
  `07` overload timeout, `51` over current or surge, `59` PV over
  limitation. This is the *fault* table, not the `QPIWS` warning bits.
- **Front-panel program list (section 5.4)** — program 01 output priority,
  02 total max charging current (10-100 A), **11 max utility charging
  current (2 A, then 10-80 A)**, 12/13 the SBU switch-back voltages,
  16 charger source priority, 26/27/29 bulk/float/cut-off voltages.
- **"All settings must be modified in battery mode and must be rebooted to
  be valid."** Worth remembering before concluding a front-panel change
  didn't take.
- Programs **12 (default 23.0 V)** and **13 (default 27.0 V / FUL)** are
  the SBU switch-back points — which is why `POP=02` does eventually
  recharge, just not until the pack falls to ~23 V. See gotcha #1's note.
