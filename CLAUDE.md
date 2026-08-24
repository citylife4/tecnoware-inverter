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

### The two automations

Both drive PCP, so they must not write at the same time.

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

Both share `apply_low_battery_floor` in `webapp/safety.py` — don't
duplicate that logic.

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

- **Fridge — on the inverter's protected output.** ~45-46 W baseline
  (compressor off), ~1223 W inrush when the compressor starts. Only visible
  in `POP=02`; in bypass the inverter reports its 1 W placeholder
  (gotcha #3). At 360 Wh usable that is roughly **4-8 h of backup**,
  depending on duty cycle — the number still to be measured.
- **Water pump — NOT on the inverter output.** Confirmed: while the house
  drew 1623 W the inverter's output still read 1 W. Runs **20:00-21:00,
  cycling roughly every 10 minutes**, peaking ~2 kW. The battery cannot
  help with it: 1.5 kW from a 24 V bank is ~62 A, i.e. 2C on a 30 Ah pack,
  which would sag the voltage and trip the cutoff almost immediately.
  **If a charging schedule is ever added, keep it out of 20:00-21:00** —
  stacking ~500 W of charger on top of the pump is worth avoiding.
- Anything else on that output is **unconfirmed**.

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
   discharge rate, then back to `POP=00`. **Do not leave `POP=02`
   unattended** — it never recharges (gotcha #1), the low-battery floor is
   dead in that mode, and the fridge would eventually lose power.
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

Open, roughly by importance:

- **Charge current is ~3x too high** (18-20 A into a 30 Ah bank) and could
  not be changed over the wire — see gotcha #10. Needs the front panel.
  This is the item most likely to be shortening the battery's life.
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
- Which loads besides the fridge are on the inverter's output is still
  unconfirmed; the pump is **not** (measured 1 W output while the house
  drew 1623 W).
- The macOS `launchd` plist for the standalone `charge_schedule.py` is
  obsolete now scheduling lives in the server — probably delete it.
- `grid_charge`'s `source_url` defaults to `http://192.168.188.11:8000`
  (`palacoulo-rasp`); update it and the deployed `web_gridcharge.json` if
  `auto-energy` ever moves host.
- `QPIRI` field 14 (`max_charging_current`) reads malformed (`06P`) — never
  resolved, don't trust it.
- `battery_redischarge_voltage` (field 22, reads `52.0`) fits neither the
  raw nor the x2 scale — unexplained.
- `POP=01` has never been tested. `QPIWS` bit meanings were never verified
  against this unit; the "line fail warning" reading is the published
  convention, not a confirmed fact.
