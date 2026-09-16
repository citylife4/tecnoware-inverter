# Review prompt — hand this to a fresh reviewer

Copy everything below the line.

---

You are reviewing a small Python project that **controls real hardware with
real consequences**. Read before changing anything, and change nothing on the
live system.

## What it is

A from-scratch controller for a Tecnoware/Voltronic inverter speaking the
PI30 ASCII protocol over USB-serial, plus a Flask dashboard and three
automations. It runs unattended on a Raspberry Pi at a rural property in
Portugal. Roughly 90 commits, 289 tests, no hardware needed to run them:

    python3 -m unittest test_webapp

## Read these first, in this order

- **`CLAUDE.md`** — orientation and numbered gotchas. Short on purpose.
  The gotchas are not hypothetical; each one cost real time.
- **`WATCHLIST.md`** — what is currently being monitored and what is
  currently wrong. This is the live state.
- **`NOTES.md`** — field notes. Every measured number, the incident log, and
  the corrections history. Long; read the sections you need.
- `README.md` — the protocol reverse-engineering writeup.

## Ground rules

1. **Do not send any command to the inverter.** Do not run `inverter_ctl.py`
   against the real port, do not PUT to the live API, do not restart
   services. The serial port is exclusive and the automations are live.
2. **Do not edit the deployed config files** (`web*.json`). They are
   gitignored and hold live state plus an API token.
3. Read-only analysis and test-only changes. If you want to propose a code
   change, write it as a diff and explain it; do not deploy.

## What I actually want

Not a style review. Find **latent defects that would only show up in some
regime the system has not been in yet.** Every serious bug in this project so
far has had that shape — the code was correct for the conditions it had seen
and wrong for conditions it had not.

Four patterns have each produced a real, damaging bug. Look for more
instances of each:

**1. A threshold compared against a voltage that may be measured while the
charger is running.** A lead-acid pack reads ~27 V on charge and ~25.6 V at
rest when equally full. Three separate thresholds were set to charging
voltages and were therefore unreachable, or never released, in the other
regime. Two are fixed (`floor_voltage`, `resume_voltage`); one is open
(`min_battery_voltage`, see WATCHLIST 2e). **Are there others?** Check every
comparison against `battery_voltage` in `webapp/` and ask which regime the
number was calibrated in.

**2. A value computed and then silently discarded.** The worst bug found so
far: `apply_low_battery_floor` correctly computed "charge this flat battery"
on every tick for four days while an earlier branch in `_tick` threw the
result away. The pack went 100% -> 50%. **Look for other places where a
safety decision is computed upstream of a branch that can drop it.**

**3. A state machine that can hold a state it should never hold.** The same
bug: a hysteresis dead-band "held the previous state", and the previous state
could be `disabled_no_solar`, which is not a control state but the controller
being switched off. One reading at dawn latched it off for the day. **Look
for other `hold the previous value` logic and ask what the full set of
possible previous values is.**

**4. A single sample treated as a fact.** This link returns corrupt frames
routinely — voltages of 0.0, loads of 45001 W, garbled ACKs. Separately, a
1.2 kW compressor start sags the pack ~5 V for one sample, which reads as a
deep discharge and is not. Three false alarms were avoided only by checking
whether readings were sustained and what the load was at the time. **Look for
decisions made on one reading that should require confirmation.**

## Specific questions worth answering

- `webapp/battery_window.py` is the safety-critical file: in battery mode it
  is the only thing protecting the pack, because the usual PCP interlock is a
  no-op there (gotcha #1). Can it be made to leave the loads on the battery
  when it should not? Can its latch deadlock again by a different route?
- The floor counter samples every 60 s against telemetry written every 10 s,
  so it sees one reading in six. Is that adequate?
- Three controllers write to one inverter (`scheduler`, `grid_charge`,
  `battery_window`). Two drive PCP, one drives POP. Can they fight? The
  interlocks are `override_check` / `is_absorbing_export()` / the
  `409 conflicting_automation` guard in `webapp/app.py`.
- Is there any path where an exception leaves the inverter in `POP=02`
  (loads on battery) with nothing subsequently correcting it?
- The tests are thorough but were written alongside the code by the same
  author. **Where are they testing the implementation rather than the
  requirement?** Several already had to be rewritten because they depended on
  a default value rather than pinning what they meant.

## Things that are deliberate, not bugs

Check `WATCHLIST.md`'s "Not faults" section before reporting anything. In
particular: a `hardware_override` most mornings around 07:57 is the inverter
hitting its own threshold at the window's end and is correct; 0-2 write
failures a day are routine on this link and the controllers reconverge; a few
corrupt telemetry lines a day are skipped by `read_telemetry.load()`.

Also: the REST API is English and the UI is pt-PT, deliberately. Mixed
language in user-facing strings is intentional; mixed language in *code
comments* is not, and those should be English.

## Output

Rank findings by how much damage they could do, not by how easy they are to
describe. For each: the failure scenario in concrete terms (what state, what
input, what happens), where it is, and what you would change. Say plainly if
you find nothing in a category — a clean answer is useful, an invented one
is not.
