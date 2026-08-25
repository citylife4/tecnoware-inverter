# Tecnoware/Voltronic inverter project — continuation notes

Read this first in a new session. Full protocol writeup is in [README.md](README.md) —
this file is orientation + gotchas, not a duplicate of it.

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

**THE PI IS POWERED FROM THE INVERTER.** Confirmed 2026-08-24: the user
was asked to power-cycle the inverter for ~10 s to apply a front-panel
setting, and `palacoulo-inverter` rebooted with `hwmon1: Undervoltage
detected!` in its kernel log. This also explains the earlier 09:05
"coincidence" — the inverter's Fault 01 event and the Pi's undervoltage
reboot were not two symptoms of a shared circuit, they were one event:
the inverter hiccuped, so the Pi lost power.

Consequences, all learned the hard way:
- **Never tell anyone to power-cycle the inverter without saying the Pi
  goes down with it.** The monitoring dies exactly when something
  interesting is happening.
- Anything the Pi is mid-write on gets truncated. `web.json` was found
  zero-length this way once; the telemetry CSV picked up a NUL block
  (56 bytes, 1 row lost out of 1578). JSON config is written atomically;
  the CSV is plain append by design, so **use `read_telemetry.py`**, which
  skips corrupt lines instead of letting Python's csv module reject the
  whole file over a few bytes.
- The inverter cannot be rebooted to apply a setting without also
  rebooting the logger, so expect a gap in `telemetry/` around every
  front-panel change.

**Changed on 2026-08-23:** the USB-serial adapter now lives on
**`palacoulo-inverter`** (`valverde@192.168.188.20`, also reachable over
Tailscale) as `/dev/ttyUSB0` — confirmed by a live `QPIGS`. It is no longer
on the Mac. `valverde` is already in the `dialout` group there.

That machine is 32-bit armv7 running Python 3.9.2 with Flask 1.1.2 and
pyserial 3.5b0 from the distro packages — **keep everything Python 3.9
compatible** (`from __future__ import annotations` in every module; no
`match`, no `X | Y` at runtime). Claude Code does not run on that host, so
work on it over SSH from `palacoulo-rasp`.

Before assuming you can talk to the inverter from whichever Pi you're on:

```bash
ls /dev/ttyUSB* /dev/serial/by-id/* 2>&1
```

If nothing shows up, the adapter isn't here — either SSH to wherever it is,
or ask the user to move it.

## Machines involved

| Host | Arch | Role |
|---|---|---|
| Mac (this session originated here) | — | original dev machine, has the adapter historically |
| `palacoulo-inverter.platy-cliff.ts.net` / `192.168.188.20` (user `valverde`) | **armv7 (32-bit)** | Raspberry Pi, named for this project — **holds the USB adapter and runs the web server**; Claude Code does NOT run here |
| `palacoulo-rasp.platy-cliff.ts.net` (user `greenv`) | aarch64 (64-bit) | **this machine** — general dev Pi, Claude Code works here |

All three should now have the repo. GitHub is the source of truth:
`git@github.com:citylife4/tecnoware-inverter.git`, branch `main`. Both Pis
already have working SSH keys registered with GitHub (`citylife4` account)
— confirm with `ssh -T git@github.com` if push fails.

`palacoulo-inverter`'s copy was `git reset --hard origin/main`'d to match
GitHub exactly (its first local commit had identical content to what the
Mac had already pushed, just as a different commit — resolved by aligning
to origin rather than keeping two histories of the same thing).

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
protocol code, deployed to `palacoulo-inverter` under systemd
(`inverter-web.service`).

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

1. **`HARD_FORBIDDEN` (19:00-21:15)** — the pump. Applied on top of whatever
   is configured and not reachable from the API, so a config edit cannot
   put the loads on battery while the pump runs. Delete it only if the pump
   is physically moved off the protected output.
2. **`floor_voltage` (25.5 V), latched.** The latch releases only once the
   window has *closed* and the pack is back above `resume_voltage` — i.e.
   **one discharge per night**. Releasing on voltage alone would discharge
   to the floor, recharge, and discharge again: several cycles a night,
   which is the exact wear the controller exists to prevent.
3. **Unreadable battery voltage** → utility. Never a reason to stay on
   battery.
4. **Yields to `grid_charge`** via `override_check` — the charger only runs
   at `POP=00`, so absorbing export and running loads off the pack are
   mutually exclusive.

`ABSOLUTE_FLOOR_V = 24.0` is refused at validation: below that is a deep
discharge, not a shallow cycle.

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

Thresholds are now **positive** (+50 W start, +250 W stop) and that is
deliberate: waiting for `net_balance` to go negative means export has
already happened by the time it is detected, PCP is written (seconds on this
hardware) and the charger spins up. Starting while the house still imports
~50 W pre-empts it. Over-importing is merely wasteful; exporting is a legal
problem here, so the asymmetry is on purpose.

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

## Loads on this installation

Everything below is on the inverter's **protected output** — stated by the
user 2026-08-24:

| Load | Notes |
|---|---|
| Fridge | ~45-46 W idle, ~1223 W compressor inrush |
| **Water pump + remote** | **~2 kW, every ~10 min from 20:00-21:00. TRIPS THE INVERTER if run from battery.** |
| Light | small |
| Remote garage door | intermittent |
| **Raspberry Pi** | the logger itself — see the topology warning above |

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

## What's not done / open questions

Deployment state as of end of 2026-08-24:

- **`POP=00`, `PCP=01`, schedule disabled, grid-export enabled in
  `override` mode** (start 0 W export, stop 400 W import, 60 s dwell). The
  battery charged to full for the first time this session once `POP=00` was
  set. Grid-export is currently inert because `PCP01` is already set.

### Applied but NOT yet verified

Both were accepted by the inverter and neither has been *seen* working.
Do not record either as done until observed:

- **Charge current 20 A -> 10 A** (front panel, program 11; done by the
  user 2026-08-24). Still unverified as of 2026-08-25, and the overnight
  data could not settle it: the only real recharge was 09:10-11:50 local on
  2026-08-24, which is *before* both the setting change and the start of
  `telemetry-2026-08-24.csv` (11:08 **UTC** = 12:08 local). The inverter's
  own log never saw it.
  **What the Shelly did see, and what to compare against:** that recharge
  drew up to **596 W AC**, i.e. roughly 18-19 A DC at 27 V — consistent with
  the old 20 A limit. So the next real recharge should cap the charger's AC
  draw around **~320 W** instead of ~600 W. That is checkable from
  `auto-energy`'s `inverter_input_w` alone, with no inverter telemetry and
  nothing to switch — just look at the next morning recharge after an
  outage. `battery_charging_current` capping at 10 rather than 20 would
  confirm it directly if the logger happens to be up for it.
- **`PBCV24.0`** — ACKed, intent was to raise the recharge point from a
  near-flat 22.0 V to ~50%. `QPIRI` still reports 22.0 V, which proves
  nothing either way (gotcha #2). Confirming it needs the pack near 24 V
  in battery mode and seen to start recharging.

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
  "Why grid-export exists here". Options are generation curtailment via the
  solar Shelly's relay (user has ruled it out for now) or a dump load.
- **The pump shares the protected output and trips the inverter on
  battery** — see "Loads on this installation". Moving it off is the
  highest-value physical change available and blocks any reliable backup
  until done.
- The macOS `launchd` plist for the standalone `charge_schedule.py` is
  obsolete now scheduling lives in the server — probably delete it.
- `grid_charge`'s `source_url` defaults to `http://192.168.188.11:8000`
  (`palacoulo-rasp`); update it and the deployed `web_gridcharge.json` if
  `auto-energy` ever moves host.
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
