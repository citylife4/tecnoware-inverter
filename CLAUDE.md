# Tecnoware/Voltronic inverter project — continuation notes

Read this first in a new session. This file is **orientation and gotchas**,
deliberately kept short because it loads into context every time.

- [README.md](README.md) — the protocol writeup: commands, CRC, capability matrix.
- [NOTES.md](NOTES.md) — **field notes**: everything measured on this
  installation, which telemetry fields lie, the incident log, the deployed
  configuration and why, plans and open questions. Look there when you need a
  specific number or some history; don't copy it back into this file.

## Language

**Talk to the developer in English.** That is their preference for
conversation, commit messages, code comments and these notes.

**The application itself is pt-PT**, with one deliberate exception: the
**REST API stays English**. Display strings live in `webapp/ui_labels.py`,
kept separate from what the API returns so that scripts have a stable
contract while the dashboard reads naturally to the people using it. The
tests assert those label tables stay in step with the code tables they
mirror. Anything a person reads in the browser is Portuguese; anything a
program parses is English.

## What this is

A from-scratch Python CLI (`inverter_ctl.py`) + charge scheduler
(`charge_schedule.py`) for a Tecnoware SolarPower inverter that speaks the
Voltronic **PI30** ASCII protocol over USB-serial (Prolific PL2303 adapter,
2400 8N1). The vendor's own Windows app (`SolarPowerApp`) turned out not to
support this protocol at all — everything here was reverse-engineered by
decompiling that app's `.class` files and then validated live against the
real hardware. See README's "How the protocol was found" and "Important:
the bundled Windows app does NOT support this inverter" sections.

## Hardware topology — CHECK THIS, it may have changed

**Moved 2026-08-28:** the USB-serial adapter and `inverter-web.service`
live on **`palacoulo-rasp`** (`greenv@192.168.188.11`, this machine) as
`/dev/ttyUSB0` (prefer `/dev/serial/by-id/usb-Prolific_*`). EcoPi
(`auto-energy`, Docker `:8000`) is on the same host. `greenv` is in
`dialout`.

The **retired** box `palacoulo-inverter` (`valverde@192.168.188.20`) was
powered **from the inverter output**. Confirmed 2026-08-24: a ~10 s
inverter power-cycle rebooted that Pi with `hwmon1: Undervoltage
detected!`. Fault 01 and the Pi reboot were one event. **This logger is
not on that circuit**, so an inverter trip should not kill monitoring or
the automations. Still do not power-cycle the inverter casually: the
serial adapter is plugged into it, and any leftover `palacoulo-inverter`
still on the protected output *will* die with it.

Consequences that still apply to files this process writes:
- Anything this Pi is mid-write on can be truncated on *this* host's own
 unclean reboot. `web.json` was found zero-length that way on the old Pi;
 the telemetry CSV picked up a NUL block once. JSON config is written
 atomically; the CSV is plain append, so **use `read_telemetry.py`**,
 which skips corrupt lines.
- Historical CSVs from the old Pi are in `telemetry/` (live, appends
 continue) and a frozen copy in `telemetry/archive-palacoulo-inverter/`.

Runtime here is Python 3.13. Keep `from __future__ import annotations`
and avoid `match` / runtime `X | Y` until we deliberately drop 3.9-safe
style. Do not start a second `serve.py` on `palacoulo-inverter` if that
box is still on.

Before assuming you can talk to the inverter from this session:

```bash
ls /dev/ttyUSB* /dev/serial/by-id/* 2>&1
```

If nothing shows up, the adapter isn't plugged in here.

## Machines involved

| Host | Arch | Role |
|---|---|---|
| Mac | — | original dev machine, had the adapter historically |
| `palacoulo-rasp` / `192.168.188.11` (user `greenv`) | aarch64 | **this machine** — USB adapter, `inverter-web.service` `:9090`, EcoPi Docker `:8000`, Claude Code |
| `palacoulo-inverter` / `192.168.188.20` (user `valverde`) | armv7 | **retired** (was USB + web; powered from the inverter). Do not run `serve.py` there after cutover |

GitHub is the source of truth:
`git@github.com:citylife4/tecnoware-inverter.git`, branch `main`.

## Critical gotchas — read before touching the real inverter

Ordered by how much time they cost when forgotten. #1 and #2 have each
already burned a full day.

1. **`PCP` only charges while `POP` is `00` (utility first).** Measured
   2026-08-24, everything else held still:

       POP=02 (SBU) + PCP=01  ->  0 A for 2 min, battery FELL 24.2->23.9 V
       POP=00 (util) + PCP=01 ->  20 A within ~20 s, 24.0 -> 28.2 V

   Every `PCP` write still returns `(ACK` under `POP=02`; it is simply a
   no-op. An earlier version of this file asserted the **opposite** (that
   `POP` had to be `02`), from misreading a remark instead of testing —
   the deployed config then sat at `POP=02` for a day charging nothing.
   **This also disables the low-battery interlock**, which works by
   writing `PCP01`. `POP=01` was never tested; treat as unknown.
   `webapp/grid_charge.py` has `CHARGING_POP = "00"` and warns otherwise.
2. **`QPIRI` shows settings as of the last BOOT, not live.** Writes were
   once (wrongly) believed non-functional because `QPIRI` looked unchanged
   after them. **Verify a write via `QPIGS` behaviour** (e.g.
   `battery_charging_current`), never via `QPIRI`. An `(ACK` alone proves
   the command was accepted, not that it did anything — see #1.

   Refined 2026-08-24: it is not that `QPIRI` never updates. After the
   inverter was rebooted (front-panel setting change), its
   `output_source_priority` and `charger_source_priority` correctly read
   `0` and `1`, matching the `POP=00`/`PCP=01` that had been set over
   serial. So `QPIRI` is a snapshot taken at boot. Live changes are
   invisible until a restart — which is still useless for confirming a
   write, but explains why the field sometimes *does* look right.

   The two charging-current fields remain untrustworthy: with front-panel
   program 11 at 20 A (later 10 A), `max_ac_charging_current` read `60`
   throughout and `max_charging_current` read the malformed `06P`. Neither
   tracks program 11. Don't use them.
3. **`ac_output_active_power` is only valid in battery mode.** With
   `POP=00` the inverter is in bypass (grid through the transfer relay,
   not inverting) and reports a constant **1 W** whatever is connected —
   a placeholder, not a measurement. Quantified from the telemetry log:
   **251 of 252 consecutive samples were exactly 1.0 W**, the single
   exception being 1223 W (34% load) — almost certainly the fridge
   compressor's inrush sagging the line hard enough that the inverter
   briefly took over and, for that instant, actually measured the load.
   This led to a wrong claim that nothing was attached to the output; the
   installation has a fridge on it, and `POP=02` showed 45-46 W
   immediately. In bypass, read the Shelly's inverter-input channel
   instead. The dashboard now renders this as "—" rather than "1 W", so
   the placeholder isn't mistaken for data.
4. **`PCP03` = solar-only charging.** With no PV this stops grid charging
   entirely and drains the battery — it happened during testing
   (undetected for a while because of #2) and dropped the pack from
   100%/27.0V to 80%/26.1V. `min_battery_voltage` guards this; don't
   bypass it. **On this installation the inverter's PV input is not
   connected at all**, so `PCP03` means "never charge", full stop.
5. **Setpoint reads and writes use different scales.** `QPIRI` reports
   battery setpoints **per 12V block** (multiply by 2 on this 24V unit;
   `parsers.py` already does). But `PBCV` **takes the pack voltage** —
   proven by `PBCV11.0` (exactly what QPIRI reports) being NAKed while
   `PBCV22.0` and `PBCV24.0` ACK. Assume the same asymmetry for the other
   setpoint writes until tested.
6. **Set commands need the CRC appended; queries don't.** A bare `PDa`
   gets no reply at all; `PDa` + 2-byte CRC gets `(ACK`. See `protocol.py`.
7. **Set commands are slow** — several seconds longer than queries.
   `transport.py` uses `SET_TIMEOUT_S = 10`.
8. **The serial link returns truncated/corrupt `QPIGS` frames**
   occasionally, and the USB adapter throws a transient `Resource busy`
   right after another process closes it. Both are handled with retries —
   don't remove them without equivalent handling. Garbled replies to set
   commands also occur; retry rather than assuming failure.
9. **Battery type is `2` = user-defined**, not a factory preset — the
   setpoints were entered by hand. The pack is **2x Xunzel SOLARX 30
   (12V 30Ah deep-cycle lead-acid) in series = 24V 30Ah**, i.e. 720 Wh
   total, ~360 Wh usable. The configured 21.0V cutoff suits lead-acid and
   would over-discharge LiFePO4, so re-check these if the pack is ever
   swapped.
10. **Charging current is set from the front panel, program 11.**
    Resolved 2026-08-24: it was at **20 A** into a 30 Ah bank (~C/1.5, and
    the observed 18-20 A was the *limit*, not the battery's acceptance —
    it was taking everything offered). Reduced to **10 A** (~C/3), the
    lowest useful option; program 11 offers 2 A then 10/20/…/80 A, so the
    ideal C/5 (~6 A) isn't selectable. Not reachable over serial —
    `QCHGC`/`QMCC` NAK, `CHGC`/`MCHGC` aren't in the verified set, and
    `QPIRI` doesn't expose it (see #2). **Verify by watching the charge
    current cap at 10 A next time the pack is actually accepting charge**;
    it was already at float when the change was made, so it has not yet
    been observed working.

## What's confirmed working on this unit (`QPI` → `(PI30`, `QVFW` → `00072.40`)

Run `python3 inverter_ctl.py --port <dev> --scan` to regenerate this
matrix for whatever's actually connected — 12 of 52 queries work, listed
in README's capability matrix. Set commands `PE`/`PD`/`PCP`/`POP`/`PBT`/
`PSDV`/`PBCV`/`PGR` are recognised and ACK.

"Recognised" is not "does something": `PCP` ACKs happily under `POP=02`
while charging nothing at all (gotcha #1), and `PBCV24.0` ACKed with no way
to confirm it applied (gotcha #2). Treat an `(ACK` as "the command parsed",
and prove effect through `QPIGS` behaviour. Of these, only `PCP` and `POP`
have been observed changing real behaviour on hardware.

## Web interface and automations

`serve.py` + `webapp/` is a Flask dashboard and REST API over the same
protocol code, deployed to `palacoulo-rasp` under systemd
(`inverter-web.service`, port 9090). EcoPi stays a separate process on
port 8000; `grid_charge` polls `http://127.0.0.1:8000/api/live`.

1. **The serial port is exclusive.** While `serve.py` runs it owns
   `/dev/ttyUSB0`, so `charge_schedule.py` and `inverter_ctl.py` cannot
   open it. Scheduling therefore lives *inside* the server
   (`webapp/scheduler.py`) rather than as external cron. Stop the service
   before using the CLI on that host.
2. **Write policy lives in `webapp/safety.py`**, not in the routes — the
   `PCP03` low-battery interlock and "dangerous commands need
   `confirm: true`" are both there, unit-tested in `test_webapp.py`
   (132 tests, no hardware needed). `mock_inverter.py` fakes the unit on a
   pty for development away from the hardware.
3. **All on-disk config goes through `webapp/atomic_write.py`** (temp file
   + fsync + rename + fsync dir). `web.json` was once found zero-length
   after an unclean reboot, losing the API token. If you add another
   config/state file, use that helper, not a bare `open(..., "w")`.
4. **The UI is pt-PT; the API is English.** Display strings are in
   `webapp/ui_labels.py`, injected into the page, deliberately separate
   from what the API returns so scripts keep a stable contract. Tests
   assert the label tables stay in sync with the code tables they mirror.
5. **Nothing in the UI claims a setting the hardware confirmed**, because
   it can't — see gotcha #2. Priority buttons are highlighted from
   `last_known_priorities` in `/api/status`, persisted to
   `web_priorities.json`. Don't "fix" this to read `QPIRI`.
6. **Telemetry is persisted** to `telemetry/telemetry-YYYY-MM-DD.csv`,
   because `/api/history` is in-memory and dies on restart — which
   happened twice on 2026-08-24 and both times erased the window worth
   analysing. `TELEMETRY_COLUMNS` order is fixed.

### The three automations

Two drive PCP and must not write at the same time; the third drives POP.

- `webapp/scheduler.py` — time-of-day rules, config `web_schedule.json`.
- `webapp/grid_charge.py` — charges while the house exports, polling
  `auto-energy`'s `net_balance`. Config `web_gridcharge.json`.

`grid_charge` has a **`mode`**:

- `"exclusive"` — owns PCP outright; `webapp/app.py` refuses to enable
  either automation while the other is on (`409 conflicting_automation`).
- `"override"` — **what this installation uses.** Only writes PCP while
  exporting; otherwise writes nothing and defers. `is_overriding()` is
  passed to `Scheduler(override_check=...)` in `serve.py` so the scheduler
  stands down instead of fighting (they poll at 60s vs 30s). Both clear
  their cached `_last_applied_pcp` when yielding, so the other's write
  doesn't leave them thinking "already set".

- `webapp/battery_window.py` — **nightly POP window**, config
  `web_battery_window.json`, added 2026-08-25. Puts the loads on the pack
  overnight so it is not on permanent float, and — the real reason —
  **creates the headroom the charger needs to absorb export the next day.**
  A full battery absorbs nothing: `PCP01` into a floated pack draws a
  trickle and the surplus goes to the grid regardless.

Both PCP controllers share `apply_low_battery_floor` in `webapp/safety.py`
— don't duplicate that logic. **It does nothing for `battery_window`**: it
works by writing PCP, and PCP is a no-op while `POP=02` (gotcha #1), so in
battery mode `BatteryWindow` is the *only* thing protecting the pack.

### `battery_window` — the four interlocks

All fail towards utility, and all exist because the Pi is on the same
output as the loads:

1. **The pump window (`pump_window`, default 19:00-21:15)** — configurable,
   and deliberately so as of 2026-08-30. It used to be hard-coded and
   unreachable from the API, on the reasoning that a config edit should not
   be able to put the loads on battery while the pump runs. That guards the
   wrong thing: the pump is on a timer the user can change, and a block
   frozen at the wrong hours reads as protection on the dashboard while the
   pump actually runs outside it. **If the pump's timer moves, move this
   too.** Setting it empty is legitimate only once the pump is off the
   protected output; `get_state()` then reports `pump_unprotected` so the
   dashboard says so out loud.
2. **`floor_voltage`, latched.** The latch releases only once the
   window has *closed* and the pack is back above `resume_voltage` — i.e.
   **one discharge per night**. Releasing on voltage alone would discharge
   to the floor, recharge, and discharge again: several cycles a night,
   which is the exact wear the controller exists to prevent.
3. **Unreadable battery voltage** → utility. Never a reason to stay on
   battery.
4. **Yields to `grid_charge`** via `override_check` — the charger only runs
   at `POP=00`, so absorbing export and running loads off the pack are
   mutually exclusive. **That yield bypasses the anti-flap dwell**, like the
   other three. It did not at first, and the consequence was live on
   2026-08-25: the house exported at -19 W, the export controller correctly
   decided to charge, this controller correctly decided to hand over
   `POP=00`, and then held that decision for the full 600 s while the export
   continued. Injecting is not permitted here, so absorbing it outranks
   relay wear. Only *entering* battery mode is debounced.

`ABSOLUTE_FLOOR_V = 24.0` is refused at validation: below that is a deep
discharge, not a shallow cycle.

**Calibrating `floor_voltage` — the first estimate was wrong.** It shipped at
25.5 V, which is a *float* voltage, not a *resting* one. On this 24 V
lead-acid bank the resting full voltage is only ~25.4-25.6 V, so 25.5 V is
roughly "still full" and the floor fired almost immediately. Observed live
2026-08-25: the moment `POP=02` was applied the pack read 26.7 V and fell to
25.8 V within 3.6 minutes — that is the float surface charge collapsing, not
capacity leaving the battery. Lowered to **25.0 V** (~20% DoD by the standard
lead-acid table: 25.4 V = 100%, 24.8 V = 75%, 24.4 V = 50%).

Two things follow. Always let the surface charge settle before reading
anything into a voltage, and **never calibrate a resting threshold from a
number taken while the charger was on.**

`floor_confirmations` (default 3) requires consecutive readings under the
floor before latching, for two independent reasons: this serial link returns
corrupt `QPIGS` frames (gotcha #8), and **the pack sags ~0.4 V while the
fridge compressor runs at only 46 W** (measured — see NOTES.md). Either would end a
night's discharge on a value that was never the pack's real state.

### A daytime battery window can CAUSE export

Observed 2026-08-25 while the test window was open in daylight: with the
protected loads on the pack, `inverter_input_w` fell to **0 W** and the whole
house dropped to importing **11.6 W** against 75 W of solar. A little more sun
or one appliance switching off and that goes negative — **exporting, which is
the thing this installation must not do.**

And `grid_charge` cannot correct it, because the charger does not run at
`POP=02` (gotcha #1). The two mechanisms are mutually exclusive by
construction: battery mode removes the only load available to absorb surplus.

This is why the window belongs at **21:15-08:00** and not merely "some hours".
It is not just about the pump — a battery window overlapping daylight works
directly against the compliance goal it exists to serve. Keep it nocturnal.

Demonstrated the same afternoon. With the window closed back to 21:15, the
inverter went to `POP=00` on the next tick, the charger came up at **10 A**,
and the grid balance went from **-19 W (exporting) to 0 W** immediately. The
daytime window was the only thing preventing that.

### The surplus signal — why `grid_charge` no longer chases itself

Changed 2026-08-25. The controller decides on

    surplus_signal = net_balance - inverter_input_w

not on `net_balance` alone. `net_balance` is already
`house_power - solar`, so subtracting Shelly channel 1 leaves what the rest
of the house would import if this inverter drew nothing. **The charger's own
350-560 W is inside `inverter_input_w`, so switching it on no longer moves
the number that switched it on** — which is what made the 2026-08-24 flap
possible in the first place. A reading missing either field is treated as
unknown (→ idle), never as a fallback to raw `net_balance`.

**No generation means no surplus, whatever the band says.** Below
`SOLAR_FLOOR_W` (5 W) the controller idles unconditionally, decided before
the hysteresis. Without that rule the design had a hole big enough to defeat
itself: after dark the surplus signal settles around **80-100 W** on this
house, which is *inside* the 30-150 W dead-band, so the band holds whatever
the day ended on. A day ending in "charging" left `PCP01` set all night with
the charger topping the pack up from the grid — destroying exactly the
absorption headroom the nightly battery window exists to create. A *missing*
solar reading is not treated as nightfall.

Thresholds are now **positive** (+50 W start, +250 W stop) and that is
deliberate: waiting for `net_balance` to go negative means export has
already happened by the time it is detected, PCP is written (seconds on this
hardware) and the charger spins up. Starting while the house still imports
~50 W pre-empts it. Over-importing is merely wasteful; exporting is a legal
problem here, so the asymmetry is on purpose.

**The dashboard used to flip the sign of `export_threshold_w`.** Its field
had type `negnum`, from when the threshold was negative: it displayed the
absolute value and stored the negation, so the user would not have to type a
minus. Once the thresholds became deliberately positive that made a positive
one **unreachable from the UI** — typing `30` stored `-30`, and the
automation then never fired, because a signal of +3 W is not below -30 W.
Hit on the live installation 2026-08-25. The field is now a plain signed
number and the label explains both directions. If another threshold ever
changes sign convention, check `GC_FIELDS` in `webapp/static/app.js` first.

Every tick is appended to `telemetry/gridcharge-YYYY-MM-DD.csv`
(`trace_dir` in `serve.py`). These thresholds have been guessed wrong twice
— the point of that file is that the next revision is argued from recorded
numbers. Read it with `read_telemetry.py`.

### Why grid-export exists here: compliance, not savings

**Exporting is not permitted at this installation** and the feed-in tariff
is `0.0`. This is a legal requirement, not an optimisation — do not
"simplify" it away on efficiency grounds. Measured numbers:

- Panels: **193 W peak ever**, ~150 W typical. Charger draws **350-560 W**
  and won't throttle below ~350 W — the panels can *never* power it.
- Export: **0.0-0.14 kWh/day** (~3.2 of 18.7 kWh/month). Solar is already
  ~100% self-consumed. Capturing all of it is worth roughly **EUR 10/year**.

**Known limitation, unsolved:** the override absorbs export by starting the
charger. Once the battery is full the charger tapers to float and draws
almost nothing, so there is no way to absorb export at that point via PCP.
A hard zero-export guarantee needs generation curtailment (the solar Shelly
at `192.168.188.25` is a Plus 1PM with a controllable relay) or a dump
load. The user has ruled out cutting the Shelly for now.

## Related project on this network: `auto-energy`

`~/dev/auto-energy` on `palacoulo-rasp` (this machine) — a Flask dashboard
+ Docker container (`ecopi-dashboard`, port 8000) that reads two Shelly
energy meters (`SHELLY_SOLAR_IP=192.168.188.25` on the panels,
`SHELLY_GRID_IP=192.168.188.5` on the house's grid connection) and logs
telemetry to a database. `/api/live` is what `webapp/grid_charge.py` polls;
its `latest.net_balance` field is **positive = importing/buying, negative =
exporting/selling** (see `src/shelly_service.py` and `src/routes.py`
`/weather` handler for where that sign convention comes from — it's the
Shelly EM's raw reading, not derived). Direct Shelly HTTP access
(`http://192.168.188.5/status`) did **not** work when tried from either Pi
during this session (empty response) — the dashboard's own `/api/live` was
the only integration point that worked, which is why `grid_charge.py` goes
through it rather than querying the meter directly.

**2026-08-23, same session:** the grid Shelly was found to be powered off.
`fetch_shelly_solar()`/`fetch_shelly_grid()` used to return `0.0` on any
failure — indistinguishable from a genuine zero reading. Fixed to return
`None`, propagated through `routes.py`, `collector.py`, `metrics.py`, and
`dashboard.html` (which needed two null-guards of its own:
`formatPowerValue(null)` threw, and `null >= 0` is `true` in JS, so a
missing reading was silently displaying as "importing"). Also fixed
`.gitignore` — `wallet**/**` isn't the recursive glob it looks like (only a
full path segment), so it missed `.wallet_unzipped/`, a real unzipped
Oracle wallet with `cwallet.sso`/`ewallet.p12`/`ewallet.pem` in it, sitting
untracked-but-not-ignored. Now `*[Ww]allet*`. Pushed to
`citylife4/palacoulo-homedash`.

**Once the Shelly came back with real data, grid-export charging flapped
PCP01↔PCP03 every ~2 minutes for 5+ hours** (14:03–19:09) before anyone
noticed — the original ±50W/+20W hysteresis band was a guess, and this
house's `net_balance` swings by hundreds of watts routinely (confirmed via
`auto-energy`'s own `/api/history`: 10-min averages 80W–1400W, one spike
over 2500W). Cross-checked `battery_net_current` from the inverter's own
history to rule out a charging-induced feedback loop — it stayed at 0A
almost the whole time (pack was already above its recharge threshold), so
this was pure wasted SET-command churn, not unsafe, just pointless.
Widened to -150W/+150W and the anti-flap dwell from 120s to 300s, in both
the live deployed config and `DEFAULT_CONFIG` in `webapp/grid_charge.py`.
**The export side of that number is still unvalidated** — no real export
data has been observed yet (fixed after sunset the same day). Worth
checking `/api/audit` for `grid_export` entries the next time there's
actually sun, to see whether -150W turns out to need tuning the same way.

## Alerts

`notify.py` sends Telegram messages for things that need a person, wired
into `usb_watchdog.py`. Configure under a `"telegram"` key in `web.json`
(already gitignored — it holds the API token):

```json
"telegram": {"enabled": true, "token": "...", "chat_id": "...",
             "heartbeat_hour": 9}
```

Test with `python3 notify.py --test`.

Two rules it follows, both from the 2026-09-01 incident where the system was
dead for 14.5 hours with nobody watching:

- **Alerts fire on state changes, never on conditions.** A fault standing
  for a week produces one message. Ringing continuously trains you to ignore
  it, which is the same as not ringing.
- **Recoveries are announced too**, so "still broken" and "fixed, nobody
  said" are distinguishable.

The daily heartbeat is what makes silence informative: with it, no message
means the notifier itself broke. Without it, no message means nothing.

## Where the rest went

Deliberately **not** in this file, to keep it short enough to load every
session — all in [NOTES.md](NOTES.md):

- what has actually been measured here (loads, battery behaviour, export)
- which `QPIGS` fields are trustworthy and which are not
- the incident log, and the corrections history behind several numbers
- the deployed configuration and the reasoning for each setting
- planned work, and open questions

If you learn something new about this installation, it belongs there. Only
add to this file if forgetting it would break something.
