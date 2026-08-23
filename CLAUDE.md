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

As of the last session, the USB-serial adapter (shows up as
`/dev/cu.usbserial-XXXX` on macOS, `/dev/ttyUSB0`-ish on Linux) was
physically plugged into **the Mac**, not either Pi. Neither
`palacoulo-inverter` nor `palacoulo-rasp` had a `/dev/ttyUSB*` device when
last checked. Before assuming you can talk to the inverter from a Pi:

```bash
ls /dev/ttyUSB* /dev/serial/by-id/* 2>&1
```

If nothing shows up, the adapter isn't here — either SSH to wherever it is,
or ask the user to move it.

## Machines involved

| Host | Arch | Role |
|---|---|---|
| Mac (this session originated here) | — | original dev machine, has the adapter historically |
| `palacoulo-inverter.platy-cliff.ts.net` (user `valverde`) | **armv7 (32-bit)** | Raspberry Pi, named for this project — Claude Code does NOT run here |
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

## What's not done / open questions

- No scheduler is currently installed/running anywhere (the macOS
  `launchd` plist exists but per the last session was NOT loaded).
- Whether `palacoulo-inverter` (32-bit) is meant to be the permanent host
  for the physical adapter + scheduler, with `palacoulo-rasp` used only
  for development via SSH, hasn't been decided.
- `QPIRI` field 14 (`max_charging_current`) reads as malformed (`06P`) —
  never resolved, don't trust it.
- `battery_redischarge_voltage` (field 22, reads `52.0`) doesn't fit either
  the raw or ×2 scale — unexplained.
