# Tecnoware SolarPower inverter — command-line controller

A small Python replacement for the bundled Windows "SolarPower" monitoring
app, for talking to the inverter directly from the terminal over its
USB-serial monitoring cable.

## How the protocol was found

Nothing here is guessed. The USB cable turned out to be a **Prolific
PL2303 USB-to-serial adapter** (`system_profiler SPUSBDataType` showed
VID `0x067b` / PID `0x2303`), i.e. the inverter speaks a plain serial
protocol, not USB-HID as the app's `USBDevice.dll` code path might suggest
at first glance.

The wire protocol (baud rate, framing, CRC, command set) was recovered by
decompiling the Java `.class` files inside
`SolarPowerApp/lib/SolarPower.jar` (a small from-scratch class-file parser
was used since no JDK was installed — see the constant pool / bytecode
dumps that drove each finding):

- **Serial settings** — `cn/com/voltronic/solar/communicate/SerialHandler.class`,
  constructor: `2400 baud, 8 data bits, 1 stop bit, no parity`.
- **Framing** — same class, `excuteCommand`/`excuteSimpleCommand`: write the
  raw ASCII command bytes, then a single `\r` (0x0D), then read bytes until
  `\r` or a timeout.
- **CRC** — `cn/com/voltronic/solar/util/CRCUtil.class`: a CRC-16/CCITT
  (XModem, poly `0x1021`) table, consumed 4 bits at a time, with a quirk
  where a resulting CRC byte equal to `(`, CR or LF gets incremented by 1
  (so the CRC bytes never collide with frame-delimiter characters).
  Verified by reconstructing the table from the bytecode and confirming it
  matches the standard CRC-16/CCITT table exactly.
- **Command set** — `cn/com/voltronic/solar/constants/Command.class`: the
  full literal list of query (`Q...`) and set commands the real app sends.
  See [`commands.py`](commands.py).
- **CRC is per-response, not fixed** — confirmed against the real inverter:
  some replies (`QPI`, `QPIGS`, `QPIRI`) come back *without* a CRC suffix,
  others (`QMOD`) *do* carry one. This app checks each response
  independently rather than assuming one mode for the whole session.

Once connected, `QPI` replied `(PI30` — meaning this unit speaks
Voltronic's public, widely-documented **PI30** protocol (used across many
inverter brands built on Voltronic's platform). That let the QPIGS/QPIRI
field layouts in [`parsers.py`](parsers.py) be filled in with real field
names instead of just numbered slots, and those names were cross-checked
against this unit's actual live output (e.g. BUS voltage ~400V, battery
~27V on a 24V bank, PV fields reading 0 with no sun on the panels).

One field (QPIRI's `max_charging_current`) came back as e.g. `06P` instead
of a plain number on this unit — likely a parallel-mode suffix, since the
app also bundles parallel-unit support
(`comusbprocessor/ParallSubProcessor.class`). Don't trust that field
blindly. The CLI always prints the raw indexed field list alongside the
named breakdown for exactly this kind of case.

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# find the right serial port
python3 inverter_ctl.py --list-ports

# interactive mode (identifies the device, then a command prompt)
python3 inverter_ctl.py --port /dev/tty.usbserial-XXXX

# one-shot query
python3 inverter_ctl.py --port /dev/tty.usbserial-XXXX --send QPIGS

# one-shot "set" command (will ask for confirmation unless --yes is passed)
python3 inverter_ctl.py --port /dev/tty.usbserial-XXXX --send SOFF
```

In interactive mode, type `list` to see every known query/set command, or
type any raw command (e.g. `QPIGS`, `QMOD`, `POP01`) directly.

## Important: the bundled Windows app does NOT support this inverter

Decompiling every protocol class in `SolarPower.jar` shows the app
implements protocol IDs **15, 16, 17 and 18 only** (`P15`, `P16`, `P17`,
`P175K`, `P1734K`, `P18`, `P181TO2K` — each `matchProtocol()` tests for
`"(PI"` + its own ID). The string **`PI30` appears nowhere in the jar**.

This inverter answers `QPI` with `(PI30`. So `matchProtocol()` returns
false for every protocol the app knows, and **SolarPower could never have
managed this unit** — the bundled `SolarPowerApp` and its PDFs belong to a
different Voltronic family (the hybrid P15–P18 series).

`PI30` is the Axpert-style protocol, whose vendor tool is typically
**WatchPower**, not SolarPower. Consequences:

- The Java in `SolarPowerApp` is *not* a reference implementation for this
  hardware. Useful for the CRC and framing (those are shared), misleading
  for anything model-specific.
- Writes to this unit nevertheless **work fine** with the CRC framing this
  tool uses (proven on hardware — see the set-command section below), so no
  vendor app is needed as a reference.

Two other findings from that decompile worth recording:

- `SerialHandler.excuteSetCommand()` is a stub (`return null`); all traffic
  goes through `excuteCommand()`, which writes the command plus `CR` and
  **no CRC**. This unit, by contrast, ignores CRC-less set commands
  entirely — more evidence it isn't the app's target hardware.
- The app also implements a second, framed protocol via `SECFormat`:
  `"^P" + %03d(len+1) + payload` for polls and `"^S" + ...` for sets, with
  replies `^D<len><data><crc>` / `^1`=ACK / `^0`=NAK. This unit does not
  speak it (a `^P004QPI` probe returns a 3-byte non-conforming reply), but
  it may be what other Tecnoware models need.
- `P15ComUSBControlModule.setCapability()` sends exactly `"PE"/"PD" + flag`
  and judges success purely by the reply being `(ACK)`. That matches what
  this tool sends, and — as it turns out — trusting the `(ACK` is correct:
  the writes really are applied.

## Capability matrix for this unit (firmware `00072.40`, `QPI` = `(PI30`)

Regenerate for any unit with:

```bash
python3 inverter_ctl.py --port /dev/tty.usbserial-XXXX --scan
```

**Working reads — 12 of 52 queries:**

| Command | What you get |
|---|---|
| `QPIGS` | live status: grid/output V+Hz, load W/VA/%, BUS V, battery V, charge+discharge A, battery %, heatsink °C, PV V/A/W |
| `QPIRI` | rated settings: voltages, currents, 3600VA/3600W rating, battery thresholds, priorities |
| `QPIWS` | 32-bit warning/fault bitfield |
| `QMOD` | working mode (`L` = Line/utility, `B` = Battery, `S` = Standby…) |
| `QFLAG` | feature flags, enabled vs disabled |
| `QDI` / `QDI2` | factory default settings |
| `QPVIPV` | PV input, two MPPT trackers |
| `QPI`, `QID`, `QVFW`, `QVFW2` | protocol, serial number, firmware versions |

**Not available:** 35 queries return an explicit `(NAK` — including all
3-phase queries (`Q3GV`/`Q3AC`/…, this is a single-phase unit), all energy
history (`QED`/`QEM`/`QEY`/`QEH`), temperature (`QTPR`), clock (`QT`), and
model (`QMD`). Energy totals and history therefore have to be accumulated
by logging `QPIGS` yourself over time — the inverter won't report them.

**Beware 5 false positives:** `QPIBI`, `QPICF`, `QPIFS`, `QPIHF`, `QPINBI`
all return `(PI30`, because the firmware prefix-matches `QPI` and discards
the rest. They look supported but aren't. `--scan` flags these for you.

## Set commands: they DO work — but `QPIRI` will not show it

1. **Set commands need the CRC; queries don't.** A bare `PDa` gets *no reply
   at all*. `PDa` + the 2-byte CRC gets a proper `(ACK` (CRC-verified).
   Queries like `QPIGS` are answered fine with no CRC.
2. **Set commands are slow to answer** — well over the 3s queries reply
   within, hence the separate `SET_TIMEOUT_S = 10s`.
3. **`QPIRI` does NOT reflect setting changes on this firmware.** It returns
   static *rated* values. Reading `QPIRI` back after a write and seeing no
   change proves nothing — this cost a lot of wasted effort here, and led to
   a wrong conclusion that writes were being ignored. They are not.
4. **Verify writes by their EFFECT in `QPIGS`, not by `QPIRI`.** This was
   proven conclusively: sending `PCP03` (charge from solar only) with no PV
   available made `battery_charging_current` drop `004A -> 000A` and the
   pack drain `27.0V/100%` to `26.1V/80%`. Sending `PCP01` restored it
   instantly: `000A -> 009A`, back to `27.1V/100%`. `QPIRI` was byte-identical
   throughout, while the inverter's real behaviour changed completely.
5. Commands the firmware **recognises** (`POP`, `PCP`, `PBT`, `PSDV`, `PBCV`,
   `PGR`, `PE`, `PD`) return a CRC-valid `(ACK`; ones it doesn't know
   (`F50`, `MCHGC`, `MUCHGC`) return **nothing**. Complete nonsense with a
   valid CRC (`XYZZY`, `HELLO`) also returns nothing — so an `(ACK` is a
   genuine signal that the command was understood *and applied*.

### Charger source priority (`PCP`) — verified on hardware

> **`PCP` does nothing unless `POP` is `00`.** Measured on this unit
> 2026-08-24, with everything else held still:
>
> | | Result |
> |---|---|
> | `POP=02` (SBU) + `PCP=01` | **0 A** for 2 minutes, battery *fell* 24.2 → 23.9 V |
> | `POP=00` (utility) + `PCP=01` | **20 A** within ~20 s, battery 24.0 → 28.2 V |
>
> Every `PCP` write still returns `(ACK` under `POP=02` — it is simply a
> no-op. This cost a full day of a "working" automation charging nothing at
> all. It also disables the low-battery interlock, which acts by writing
> `PCP01`. `POP=01` was never tested; treat it as unknown. Read the whole
> table below as "given `POP=00`".

| Command | Meaning | Observed effect (with `POP=00`) |
|---|---|---|
| `PCP00` | Utility first | ACK |
| `PCP01` | Solar first (falls back to utility) | ACK — charging resumed, 9–20 A |
| `PCP02` | Solar + utility | ACK |
| `PCP03` | **Solar ONLY** | ACK — utility charging STOPS |

`PCP03` with no sun means the battery will not charge at all and will
slowly drain. Do not leave a unit in `PCP03` unless that is genuinely what
you want.

Note also that on the reference installation the inverter's own PV input is
**not connected**, so "solar" is not a source it can ever select — `PCP03`
there means "do not charge", full stop.

## Scheduled / timed charging — possible via the host

The inverter has **no built-in time-of-day scheduling**: `QPKT`, `QLDT`,
`QACCV`, `QCHGC`, `QMCC`, `QGPMP`, `QOFFC`, `QPRIO` and `QENF` all return
`(NAK`. The app's AC-charging window feature (`PKT`/`QPKT`) is P16/P17-only
and this unit is `PI30`.

But since `PCP` writes **do** work, scheduling is achievable from the host.
That is implemented in [`charge_schedule.py`](charge_schedule.py): it picks
a charger source priority by time of day and applies it.

```bash
# edit schedule.json first (port, times, min_battery_voltage)
python3 charge_schedule.py schedule.json --dry-run   # show, change nothing
python3 charge_schedule.py schedule.json             # apply
```

Config (`schedule.example.json`):

```json
{
  "port": "/dev/cu.usbserial-1460",
  "min_battery_voltage": 24.0,
  "rules": [
    {"from": "09:00", "to": "17:00", "pcp": "03", "why": "daytime: solar only"},
    {"from": "17:00", "to": "09:00", "pcp": "01", "why": "night: allow utility"}
  ]
}
```

Behaviour that matters:

- **Low-battery safety override** — a `pcp: "03"` (solar-only) rule is
  downgraded to `01` whenever the pack is below `min_battery_voltage`, so a
  cloudy day can't flatten the battery. Tested by forcing the floor high.
- **Idempotent** — it records the last applied value and no-ops if the
  inverter is already set, so it's safe to run every few minutes.
- **Midnight-wrapping windows** work (`17:00 -> 09:00`); unit-tested.
- **Robust to this link's quirks** — retries the occasional truncated
  `QPIGS` frame, and retries the `Resource busy` the USB adapter sometimes
  returns right after another process closes it. Both were hit in practice
  and would otherwise kill an unattended run.
- Confirms via `QPIGS` `battery_charging_current`, never `QPIRI`.

To run it automatically, install the bundled launchd job (runs every 10
minutes, logs to `charge_schedule.log`):

```bash
cp com.tecnoware.chargeschedule.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.tecnoware.chargeschedule.plist
```

Stop it with `launchctl unload ~/Library/LaunchAgents/com.tecnoware.chargeschedule.plist`.

> **If the web server is running, use its built-in automation instead.**
> `serve.py` holds the serial port exclusively, so `charge_schedule.py`
> cannot open it at the same time. See [Built-in
> schedule](#built-in-schedule-recommended-over-external-cron) — and, for
> why the time-of-day approach doesn't actually track solar production on
> this hardware, [Grid-export charging](#grid-export-charging). The
> low-battery interlock is enforced server-side either way.

## Web interface and REST API

`serve.py` runs a dashboard and a JSON API on top of the same protocol code
the CLI uses. It is meant to live on whichever machine physically holds the
USB-serial adapter, so other hosts can read and control the inverter over
the network.

```bash
python3 serve.py --config web.json
#  -> writes web.json on first run and prints the API token
#  -> http://<host>:8080
```

Open that URL, paste the token once, and the browser keeps a session cookie.
Recover the token later with `python3 serve.py --show-token`.

### The port is exclusive — this matters

Only one process can hold `/dev/ttyUSB0`. While `serve.py` is running,
`charge_schedule.py` and `inverter_ctl.py` **cannot open the port**. Pick one
of these:

- run the web server and drive scheduling through the API (below), or
- stop the web server while running the CLI/scheduler.

The server polls in the background (default every 10s) and serves the browser
from that cached snapshot, so extra tabs and API clients cost nothing on the
wire. At 2400 baud a `QPIGS` exchange takes roughly half a second, which is
why per-request reads would queue up.

### What the dashboard shows

- live tiles: battery volts and state of charge, PV power, load, grid, AC out,
  heatsink temperature, DC bus
- three trend charts (battery voltage, PV power, load) with a table view
- controls for charger priority (`PCP`) and output priority (`POP`), buzzer,
  and output on/off
- a raw command console, the rated `QPIRI` configuration, and a log of every
  set command the server has sent

**The dashboard is in European Portuguese (pt-PT); the REST API is not.**
Display strings live in [`webapp/ui_labels.py`](webapp/ui_labels.py) and are
injected into the page, deliberately separate from the values the API
returns -- scripts consuming `/api/commands` or `/api/status` keep getting
stable English. Adding a new protocol code means adding its label there too;
`test_webapp.py` asserts the tables stay in sync so an untranslated code
can't slip through silently.

**The inverter never reports its current priority back.** Confirmed live on
this unit: `PCP02` returns `(ACK`, and `QPIRI`'s `charger_source_priority`
still reads the old value afterwards — the "static rated values" trap applies
to the priority code fields too, not just the battery setpoints. The dashboard
therefore highlights a priority button from **this server's own write log**,
and says "current setting unknown" when it has not set one since starting.
Nothing in the UI claims a current setting the hardware did not confirm.

That record is `last_known_priorities` in `/api/status`, and it is
**persisted** to `web_priorities.json` rather than kept in the audit log --
the audit log is in-memory and empties on restart, which made the `POP`
button show nothing while `PCP` (rewritten constantly by the automation)
looked fine.

### API

Every endpoint except `/api/health` needs the token, as either header:

```
X-API-Key: <token>
Authorization: Bearer <token>
```

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | liveness; no auth |
| GET | `/api/status` | latest polled snapshot (`?live=1` forces a fresh read), plus `last_known_priorities` |
| GET | `/api/history?limit=N` | recent samples for charting |
| GET | `/api/device` | model / firmware / serial number |
| GET | `/api/ratings` | parsed `QPIRI`, with 12V-block setpoints scaled |
| GET | `/api/commands` | full command catalogue and write policy |
| GET | `/api/audit` | set commands sent by this process |
| POST | `/api/command` | send any command |
| POST | `/api/charger-priority` | set `PCP` |
| POST | `/api/output-priority` | set `POP` |

Reading:

```bash
TOKEN=$(python3 serve.py --show-token)
curl -s -H "X-API-Key: $TOKEN" http://inverter.local:8080/api/status \
  | python3 -c 'import json,sys; s=json.load(sys.stdin)["status"]; \
      print(s["battery_voltage"], "V", s["battery_capacity"], "%", s["pv_charging_power"], "W")'
```

Writing (writes must be `Content-Type: application/json`):

```bash
curl -s -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
     -d '{"value":"01"}' http://inverter.local:8080/api/charger-priority

curl -s -X POST -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
     -d '{"command":"QPIGS"}' http://inverter.local:8080/api/command
```

Every response carries `"ok": true|false`; failures add a machine-readable
`code` and usually a `hint`.

### Write policy

The CLI protects you with a `y/N` prompt. An API has no prompt, so the same
protection is expressed as policy in [`webapp/safety.py`](webapp/safety.py):

- **Queries** pass straight through.
- **Routine set commands** (`PCP`, `POP`, `PBCV`, `PGR`, buzzer, charge
  currents) go through on a plain authenticated request.
- **Dangerous ones** (`REEP`, `EPO`, `SOFF`/`SON`, `F50`/`F60`, `CLR`, `PF`,
  `ID`, `SNRM`) are refused with `409 confirmation_required` unless the body
  carries `"confirm": true`. So does any set command whose effect on this
  model was only inferred from the decompiled table rather than verified.
- **`PCP03` (solar-only) is refused below `min_battery_voltage`** — the same
  interlock `charge_schedule.py` has, for the same reason: solar-only with no
  sun drained this pack from 100% to 80% once already. If the battery voltage
  cannot be read at all, the command is refused rather than allowed.
- `--read-only` refuses every set command regardless of config.

Codes you can branch on: `unauthorized`, `read_only`, `unknown_command`,
`confirmation_required`, `battery_too_low`, `battery_unknown`,
`invalid_value`, `bad_content_type`, `nak`, `io_error`.

### Built-in schedule (recommended over external cron)

The server can apply charger-priority rules by time of day itself, so you
don't need `charge_schedule.py` or external cron at all -- and since the
server already owns the serial port exclusively, an external scheduler
process couldn't open it anyway. It's the "Automatic schedule" panel on the
dashboard, or the API directly:

```bash
TOKEN=$(python3 serve.py --show-token)
curl -s -X PUT -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"enabled": true, "rules": [
        {"from": "09:00", "to": "17:00", "pcp": "03", "why": "daytime: solar only"},
        {"from": "17:00", "to": "09:00", "pcp": "01", "why": "night: allow utility"}
      ]}' \
  http://inverter.local:8080/api/schedule
```

Rules are the same shape `charge_schedule.py`'s JSON config uses (`from`,
`to`, `pcp`, `why`), checked once a minute by a background thread inside
`serve.py` (`webapp/scheduler.py`) -- no cron, no second process. Behaviour
matches `charge_schedule.py`'s documented safety rule exactly: a `pcp: "03"`
rule is downgraded to `01` whenever the battery is below
`min_battery_voltage` (including when the voltage can't be read at all), and
the reason is recorded rather than the rule being silently skipped. Writes
this makes are tagged `"source": "scheduler"` in `/api/audit`, so they're
distinguishable from a person's writes.

An edit takes effect immediately (no waiting for the next poll), and it's
idempotent -- reapplying the same target doesn't resend the command. Storing
a schedule always succeeds, even on a `--read-only` server; only *applying*
it is gated, same as any other write, so a read-only instance can still be
configured ahead of time.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/schedule` | current rules, whether enabled, last-run result |
| PUT | `/api/schedule` | replace the whole rule set: `{"enabled": bool, "rules": [...]}` |
| POST | `/api/schedule/apply-now` | force an immediate evaluation, bypassing idempotency |

Rules and enabled state persist to `web_schedule.json` (gitignored,
instance-specific). Writes go through the same atomic temp-file-then-rename
helper as `web.json` (`webapp/atomic_write.py`) -- see the durability note
below for why that matters on this hardware.

`GET /api/schedule` also returns `current_rule`/`current_target`, a live
preview of what would apply right now even if the scheduler is disabled or
you're mid-edit -- useful for checking a new rule before turning it on.

**Superseded by grid-export charging below for the original "solar hours"
use case.** This time-of-day scheduler predates a discovery made while
building the next feature: this inverter's own PV input reads 0V in every
sample taken so far, including at midday with real solar production showing
elsewhere -- see [Grid-export charging](#grid-export-charging). A `pcp: "03"`
rule during "daytime" doesn't select a solar source on this hardware, it
just stops charging outright, on a timer that has nothing to do with
whether the sun is actually out. The scheduler still works exactly as
documented above and is unit-tested; it's kept for other uses (e.g. a fixed
off-peak-tariff charging window) and because deleting a working, tested
feature to fix a documentation-level assumption felt like the wrong trade.
Whether the two automations can run together depends on grid-export's
`mode`: in `exclusive` they are mutually exclusive (`409
conflicting_automation`), in `override` they are designed to run together.
See [Grid-export charging](#grid-export-charging).

Driving it from external cron instead of the built-in thread above still
works (`PUT /api/schedule` or plain `POST /api/charger-priority` calls), but
isn't documented further here -- the built-in scheduler does the same job
without a second process to keep running, and the low-battery interlock
lives in the server either way.

### Grid-export charging

*(see [`webapp/grid_charge.py`](webapp/grid_charge.py) for the full
rationale, restated briefly here.)*

This Tecnoware unit's own DC/PV input is unconnected -- confirmed by every
`QPIGS` sample taken this session reading `pv_input_voltage: 0.0V`,
`pv_charging_power: 0W`, including at midday while a separate energy
monitor on the same network (`auto-energy`, a different project on this Pi)
showed real solar production. The actual panels are on a separate,
AC-coupled system feeding the house wiring directly; from this inverter's
point of view there is no "solar" input, only "utility" -- which is really
the same shared AC bus the panels are already feeding.

Given that, "charge only during daylight hours" (the time-of-day
scheduler's original assumption) doesn't track reality. What does: whether
the house is currently *exporting* surplus solar to the grid. `auto-energy`
already computes that from a Shelly EM on the grid connection
(`net_balance` in its `/api/live` response -- positive means buying,
negative means selling). This feature polls that endpoint and enables
charging only while there's a surplus being exported anyway, so the battery
soaks up power that would otherwise leave the property, and never causes
the inverter to draw *additional* grid power beyond what was already
flowing out.

**Why you would actually run this: compliance, not savings.** On the
reference installation exporting to the grid is not permitted, and the
feed-in tariff is `0.0` -- exported energy earns nothing. The saving is
therefore whatever export you avoid: measured at ~0.13 kWh/day, or roughly
**EUR 10/year**. Do not adopt this expecting it to pay for anything. It is
worth having when export is a legal or contractual problem, and the honest
answer otherwise is that it is not worth the complexity.

Two limits worth knowing before relying on it:

- **The charger cannot modulate.** It draws 350-560 W or nothing. Absorbing
  50 W of export means pulling ~500 W, i.e. swinging straight into
  importing. If the surplus is much smaller than the charger, you buy the
  difference.
- **A full battery cannot absorb anything.** At float the charger tapers to
  a few watts, so this mechanism silently stops working exactly when the
  battery is topped up. For a *hard* zero-export guarantee you need
  something that curtails generation or a dump load; this feature alone
  cannot promise it.

```bash
curl -X PUT -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" -d '{
      "enabled": true,
      "mode": "override",
      "source_url": "http://<host-running-auto-energy>:8000/api/live",
      "export_threshold_w": 0,
      "import_threshold_w": 400,
      "charge_pcp": "01",
      "idle_pcp": "03"
    }' http://inverter.local:8080/api/grid-charge
```

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/grid-charge` | current config, live reading, last-run result |
| PUT | `/api/grid-charge` | replace the whole config (same store-vs-apply split as `/api/schedule`) |
| POST | `/api/grid-charge/apply-now` | force an immediate poll + decision, bypassing the anti-flap timer |

Behaviour, all configurable, defaults shown:

- **Hysteresis, not a single threshold.** Charging starts once `net_balance`
  drops below `export_threshold_w` (default -50W) and stops once it rises
  above `import_threshold_w` (default +20W); inside that band the previous
  state holds. Without a gap between the two, ordinary load noise (a fridge
  cycling) would flip the state constantly.
- **Anti-flap timer** (`min_switch_interval`, default 120s): even past the
  hysteresis band, won't switch again this soon after the last switch --
  PCP writes take up to ~10s on this hardware, so there's no reason to spam
  them. The low-battery safety override below is the one thing allowed to
  bypass this.
- **Fails to "not exporting," never fails open.** If the `auto-energy`
  dashboard can't be reached, or its last reading is older than
  `stale_after` (default 120s), this assumes the house is *not* exporting
  and stops charging -- it never guesses "probably fine, keep charging"
  from missing data.
- **Same low-battery floor as the scheduler**, via the same code
  (`apply_low_battery_floor` in `webapp/safety.py`): `idle_pcp` (default
  `03`) is upgraded to `01` if the battery is at/under
  `min_battery_voltage`, including when it can't be read at all -- this
  interlock is what the anti-flap timer is allowed to skip.
- **`mode` decides how it coexists with the schedule** (both drive PCP, so
  something has to give):
  - `"exclusive"` (default) -- owns PCP outright. Enabling either
    automation while the other is on is refused (`409
    conflicting_automation`); disabling one is never blocked.
  - `"override"` -- only writes PCP *while exporting*; otherwise writes
    nothing and lets the schedule decide. The scheduler asks
    `is_overriding()` before each of its own writes and stands down, so
    they take turns instead of fighting (they poll at 60s vs 30s and would
    otherwise overwrite each other). This is the mode to use when export is
    not permitted and you still want a normal charging schedule underneath.

### Running it as a service

`inverter-web.service` is a systemd unit for the Pi that holds the adapter:

```bash
sudo cp inverter-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now inverter-web
journalctl -u inverter-web -f
```

The service user must be in the `dialout` group for serial access.

### Telemetry logging

Every successful poll is appended to
`telemetry/telemetry-YYYY-MM-DD.csv` (one file per day, gitignored,
`telemetry_dir` in `web.json`). Columns are fixed in
`InverterService.TELEMETRY_COLUMNS` -- do not reorder them, or old files
stop matching new ones.

This exists because `/api/history` is an in-memory ring buffer that is
**lost on every restart**, which happened twice in one day (an undervoltage
reboot plus ordinary deploys) and both times erased exactly the window
worth analysing. Write failures are swallowed deliberately: losing a log
line matters far less than stopping the poller.

One caveat when reading those files: **`ac_output_active_power` is only
meaningful in battery mode.** With `POP=00` the inverter is in bypass --
grid passes through the transfer relay, it isn't inverting, and it reports
a constant 1 W regardless of what is connected to its output. In bypass,
use the grid meter's inverter-input channel instead.

### Security

The token is a bearer credential and `web.json` is written `0600` and
gitignored. Traffic is plain HTTP, so keep this on a trusted LAN or behind a
VPN (Tailscale) rather than port-forwarding it to the internet. Session
cookies are `SameSite=Lax` and writes require a JSON content type, so a
cross-origin page cannot forge one against a logged-in browser.

### Config durability

`web.json` (the token), `web_schedule.json` (schedule rules), and
`web_gridcharge.json` (grid-export config) are all written through
`webapp/atomic_write.py`: temp file, `fsync`, rename, then
`fsync` the directory. This isn't defensive boilerplate -- it's a fix for an
incident hit while building this. `palacoulo-inverter` rebooted uncleanly
mid-session and a plain open/write/close had left `web.json` zero-length,
losing the API token. A crash at any point during an atomic write now leaves
either the old file intact or the new one complete, never an empty one, and
`serve.py` refuses to start on a config file that exists but fails to
parse (rather than silently minting a new token and locking out existing
clients).

### Developing without the hardware

`mock_inverter.py` puts a fake PI30 inverter on a pseudo-terminal, imitating
this unit's quirks deliberately: no CRC on query replies but a CRC on `(ACK`,
silence for unsupported commands, the `QPI` prefix-matching false positives,
a `QPIRI` that never reflects a write, and (with `--glitch`) the occasional
truncated `QPIGS` frame.

```bash
python3 mock_inverter.py            # prints e.g. /dev/pts/7
python3 serve.py --port /dev/pts/7 --http-port 8080
```

Tests need no hardware at all:

```bash
python3 -m unittest test_webapp -v
```

## Safety

`SET_COMMANDS` in `commands.py` (things like `SON`/`SOFF`, `POP`, `PCP`,
`EPO`, `REEP`, ...) change the inverter's actual behaviour — output
on/off, source priority, charging limits, EEPROM defaults. The CLI always
asks for a `y/N` confirmation before sending one of these unless you pass
`--yes`. Several of these were only inferred from their command-name
strings, not test-fired against the hardware, so treat unfamiliar set
commands with real caution before using them on a live system.

## Files

- `protocol.py` — CRC-16 implementation (ported from `CRCUtil.class`)
- `transport.py` — serial connection + framing (ported from `SerialHandler.class`)
- `commands.py` — full command table (extracted from `Command.class`)
- `parsers.py` — named field layouts for `QPIGS`/`QPIRI` (public PI30 protocol)
- `inverter_ctl.py` — the CLI
- `charge_schedule.py` — time-of-day charger-priority scheduler
- `serve.py` — web interface + REST API entry point
- `webapp/service.py` — thread-safe serial owner and background poller
- `webapp/safety.py` — write policy (what needs confirming, what is refused) and the shared low-battery floor both automations use
- `webapp/scheduler.py` — built-in time-of-day PCP scheduler (runs inside the server)
- `webapp/grid_charge.py` — built-in grid-export-following PCP control (runs inside the server)
- `webapp/atomic_write.py` — crash-safe JSON writes, shared by `web.json` and both automations' config
- `webapp/app.py` — Flask routes, auth, JSON API
- `mock_inverter.py` — fake inverter on a pty, for development without hardware
- `test_webapp.py` — tests for the API, parsers, both automations, and write policy
