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

| Command | Meaning | Observed effect |
|---|---|---|
| `PCP00` | Utility first | ACK |
| `PCP01` | Solar first (falls back to utility) | ACK — charging resumed, 9A |
| `PCP02` | Solar + utility | ACK |
| `PCP03` | **Solar ONLY** | ACK — utility charging STOPS |

`PCP03` with no sun means the battery will not charge at all and will
slowly drain. Do not leave a unit in `PCP03` unless that is genuinely what
you want.

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
