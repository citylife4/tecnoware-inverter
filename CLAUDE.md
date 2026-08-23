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

1. **`QPIRI` reports static rated values and NEVER reflects a setting
   change.** This caused a real incident: writes were (wrongly) believed to
   be non-functional because `QPIRI` looked unchanged after them. **Always
   verify a write via `QPIGS` behaviour** (e.g. `battery_charging_current`
   for charger-priority changes), never via `QPIRI`.
2. **`PCP03` = solar-only charging.** With no PV, this stops grid charging
   entirely and will drain the battery. This actually happened during
   testing (undetected for a while because of gotcha #1) and dropped the
   pack from 100%/27.0V to 80%/26.1V before being caught and reverted.
   `charge_schedule.py` has a `min_battery_voltage` safety override for
   exactly this reason — don't bypass it.
3. **Set commands need the CRC appended; queries don't.** A bare `PDa` gets
   no reply at all; `PDa` + 2-byte CRC gets `(ACK`. See `protocol.py`.
4. **Set commands are slow** — can take several seconds longer to answer
   than queries. `transport.py` uses `SET_TIMEOUT_S = 10`.
5. **The serial link occasionally returns truncated/corrupt `QPIGS`
   frames**, and the USB adapter occasionally throws a transient
   `Resource busy` right after a previous process closes it. Both are
   handled with retries in `charge_schedule.py` — don't remove those
   without re-adding equivalent handling.
6. **QPIRI battery setpoints (recharge/under/bulk/float voltage) are per
   12V block, not pack voltage.** On this 24V unit, multiply by 2 for the
   real target. `parsers.py` shows this in its output already.
7. Battery type on this unit is `2` = **user-defined**, not a factory AGM/
   Flooded preset — the setpoints were configured by hand. If the pack
   is ever swapped to lithium, those voltages are wrong for it (lead-acid
   cutoff of 21.0V would over-discharge LiFePO4) — this hasn't been
   checked/confirmed either way, just flagged.

## What's confirmed working on this unit (`QPI` → `(PI30`, `QVFW` → `00072.40`)

Run `python3 inverter_ctl.py --port <dev> --scan` to regenerate this
matrix for whatever's actually connected — 12 of 52 queries work, listed
in README's capability matrix. Set commands `PE`/`PD`/`PCP`/`POP`/`PBT`/
`PSDV`/`PBCV`/`PGR` are recognised and DO apply (proven via `PCP03`→`PCP01`
round-trip changing real charging current, not just via ACK).

## Web interface (added 2026-08-23)

`serve.py` + `webapp/` is a Flask dashboard and REST API over the same
protocol code. Deployed to `palacoulo-inverter`. Two things to know:

1. **The serial port is exclusive.** While `serve.py` runs it owns
   `/dev/ttyUSB0`, so `charge_schedule.py` and `inverter_ctl.py` cannot
   open it. **Scheduling now lives inside the server itself**
   (`webapp/scheduler.py`, added 2026-08-23) rather than as cron hitting
   `/api/charger-priority` — same low-battery override behaviour as
   `charge_schedule.py`, but no second process fighting for the port.
   Config is `web_schedule.json` (gitignored, atomic-written). Stop the
   service before using the CLI/`charge_schedule.py` on that host.
2. **`QPIRI` does not reflect priority writes either** — verified live on
   2026-08-23: `PCP02` → `(ACK`, yet `QPIRI` field 17
   (`charger_source_priority`) still read `1` afterwards, before and after
   restoring `PCP01`. So gotcha #1 is broader than the battery setpoints:
   there is **no way to read the current priority back off this unit**. The
   web UI highlights priority buttons from its own audit log and shows
   "unknown" otherwise — don't "fix" it to read QPIRI.
3. **Write policy lives in `webapp/safety.py`**, not in the routes. The
   `PCP03` low-battery interlock and the "dangerous commands need
   `confirm: true`" rule are both there, and both are unit-tested in
   `test_webapp.py` (63 tests, no hardware needed). `mock_inverter.py`
   fakes the unit on a pty for development away from the hardware.
4. **`web.json` was found zero-length after an unclean reboot** (a plain
   open/write/close isn't durable) — fixed via `webapp/atomic_write.py`
   (temp file + fsync + rename + fsync dir), shared by `web.json`,
   `web_schedule.json`, and `web_gridcharge.json`. If you ever add another
   on-disk config/state file to this project, write it through that
   helper, not a bare `open(..., "w")` — this bit us for real, not
   hypothetically.
5. **The inverter's own PV/DC input is unconnected** — confirmed by every
   `QPIGS` sample taken across this whole project, at any time of day,
   reading `pv_input_voltage: 0.0V` / `pv_charging_power: 0W`, cross-checked
   against `auto-energy` (a separate project on this network, see below)
   showing real nonzero solar production at the same moments. The panels
   are on a separate AC-coupled system feeding the house wiring directly —
   this inverter only ever sees "utility." **This means the time-of-day
   scheduler's original "daytime = PCP03 solar-only" assumption
   (inherited from `charge_schedule.py`'s pre-existing example config) does
   not track reality on this hardware** — PCP03 during daylight just stops
   charging on a timer, unrelated to whether the sun is out. Don't "fix"
   this by changing the scheduler; the correct tool is the grid-export
   automation added to address it directly, below.
6. **Grid-export-following charge control** (added 2026-08-23,
   `webapp/grid_charge.py`) — enables charging only while the house is
   exporting surplus solar, using `auto-energy`'s grid-meter reading
   (`net_balance` from its `/api/live`, at `http://192.168.188.11:8000` in
   this deployment) instead of a clock. Same low-battery floor as the
   scheduler, via a helper (`apply_low_battery_floor`) now shared by both
   in `webapp/safety.py` — don't duplicate that logic if you touch either
   automation. **Mutually exclusive with the time-of-day scheduler** by
   explicit design choice (the user picked "export-following only,
   replace the time rules" when asked) — `webapp/app.py` refuses to enable
   one via the API while the other is enabled (`409
   conflicting_automation`), rather than silently disabling the other.
   Config is `web_gridcharge.json` (gitignored, atomic-written).

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

## What's not done / open questions

- The macOS `launchd` plist for the old standalone `charge_schedule.py`
  still exists but was never loaded — moot now that scheduling lives in
  the web server; probably delete it next time this is touched.
- Neither automation (time-of-day scheduler nor grid-export charging) is
  currently enabled on the deployed server — both work (unit- and
  live-tested against the real inverter) but nobody has turned one on for
  real use yet. Per the user's explicit choice, grid-export is the
  intended one; the scheduler is available but not the recommended path.
- Grid-export charging's `source_url` defaults to
  `http://192.168.188.11:8000/api/live` (this machine, `palacoulo-rasp`) —
  if `auto-energy` ever moves hosts, that default (and the deployed
  `web_gridcharge.json` if already configured) needs updating.
- `QPIRI` field 14 (`max_charging_current`) reads as malformed (`06P`) —
  never resolved, don't trust it.
- `battery_redischarge_voltage` (field 22, reads `52.0`) doesn't fit either
  the raw or ×2 scale — unexplained.
