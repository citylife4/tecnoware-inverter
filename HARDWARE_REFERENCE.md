# Hardware reference — inverter programs and battery datasheet

Transcribed 2026-09-18 from the printed user's manual (photographed on site)
and the Xunzel SOLARX datasheet. Until now this project had **four** of ~30
inverter settings documented, all inferred from behaviour, because `QPIRI`
misreports several of them.

This file is reference material: the numbers as the manufacturers state them.
What we *chose* and why lives in [NOTES.md](NOTES.md); what is currently
being watched lives in [WATCHLIST.md](WATCHLIST.md).

Unit: **ATA Solar Inverter 3.5–5.5 kW** (PI30 protocol). Ours is the
**3.5 kW / 24 V** model, so the 3.5K column applies everywhere below.

---

## How to enter settings

Hold **ENTER** for 3 seconds. **UP/DOWN** to select a program, **ENTER** to
confirm, **ESC** to exit.

> **"All settings must be modified in battery mode and must be rebooted to
> be valid."**

That sentence is worth more than it looks. It is the direct explanation of
**gotcha #2** — why `QPIRI` reports settings as of the last boot rather than
live. The unit does not apply a changed setting until it restarts, so a
snapshot taken at boot is exactly what `QPIRI` returns.

---

## Program table (3.5 kW / 24 V model)

Bold rows are ones this installation has deliberately set or that matter to
the automations.

| # | Setting | Options / range | Default | **Ours** |
|---|---|---|---|---|
| 00 | Exit setting mode | `ESC` | — | — |
| **01** | **Output source priority** | `UtI` utility first / `SOL` solar first / `SbU` battery first | `UtI` | driven over serial (`POP`) |
| 02 | Max **total** charging current (solar + utility) | 10–100 A, 10 A steps | 60 A | not checked |
| 03 | AC input voltage range | `APL` 90–280 V / `UPS` 170–280 V | `APL` | not checked |
| **05** | **Battery type** | `AGn` AGM / `FLd` flooded / `USE` user-defined | `AGn` | `USE` (QPIRI code 2) — **required for 26/27 to be settable** |
| 06 | Auto-restart on overload | `Ltd` disable / `LtE` enable | `Ltd` | not checked |
| 07 | Auto-restart on over-temperature | `ttd` disable / `ttE` enable | `ttd` | not checked |
| 09 | Output frequency | 50 / 60 Hz | 50 Hz | — |
| 10 | Output voltage | 220 / 230 / 240 V | 230 V | — |
| **11** | **Max *utility* charging current** | 2 A, then 10–80 A in 10 A steps | 30 A | **10 A** |
| **12** | **Voltage point back to *utility*** | 22.0–25.5 V, 0.5 V steps | 23.0 V | **24 V** |
| **13** | **Voltage point back to *battery*** | 24.0–29.0 V, 0.5 V steps, or `FUL` | 27.0 V | **27 V** |
| **16** | **Charger source priority** | `CSO` utility first / `CUt` solar first / `SNU` solar+utility / `OSO` only solar | `SNU` | driven over serial (`PCP`) |
| 18 | Alarm | `bOn` / `bOF` | `bOn` | not checked |
| 19 | Auto-return to default screen | `ESP` / `KEP` | `ESP` | — |
| 20 | Backlight | `LOn` / `LOF` | `LOn` | — |
| 22 | Beep when primary source interrupted | `AOn` / `AOF` | `AOn` | not checked |
| 23 | Overload bypass | `byd` disable / `byE` enable | `byd` | not checked |
| 25 | Record fault code | `FEn` / `FdS` | `FEn` | — |
| **26** | **Bulk / absorption charging voltage (C.V.)** | 25.0–31.5 V, 0.1 V steps | 28.2 V | **28.2 V** (= default) |
| **27** | **Float charging voltage** | 25.0–31.5 V, 0.1 V steps | 27.0 V | **27.0 V** (= default) |
| **29** | **Low DC cut-off voltage** | 21.0–24.0 V, 0.1 V steps | 21.0 V | **not checked** |
| 30 | Battery equalization | `EEn` / `EdS` | `EdS` disable | not checked |
| 31 | Equalization voltage | 25.0–31.5 V | 29.2 V | n/a while 30 disabled |
| 33 | Equalized time | 5–900 min | 60 min | n/a |
| 34 | Equalized timeout | 5–900 min | 120 min | n/a |
| 35 | Equalization interval | 0–90 days | 30 days | n/a |
| 36 | Equalize immediately | `AEn` / `AdS` | `AdS` | n/a |
| 40 | Discharge current limit | `OFF`, or 10–200 A in 5 A steps | `OFF` | not checked |
| 41 | Lithium discharge stop | 1–60 % | 6 % | n/a (lead-acid) |
| 42 | Lithium charge stop | 60–100 % | 96 % | n/a (lead-acid) |

Notes carried from the manual:

- **02 vs 11**: if program 02 is *smaller* than program 11, the unit uses 02's
  value for the utility charger. So 02 caps 11.
- **26/27 are only settable when 05 = `USE`.** Selecting `AGn` or `FLd`
  forces the preset voltages instead.
- **29 is absolute**: "Low DC cut-off voltage will be fixed to setting value
  no matter what percentage of load is connected."
- **30 (equalization) is only available when 05 is `FLd` or `USE`.**

---

## Fault and warning codes

| Fault | Meaning | | Warning | Meaning |
|---|---|---|---|---|
| 01 | Fan locked, inverter off | | 01 | Fan locked, inverter on |
| 02 | Over temperature | | 02 | Over temperature |
| 03 | Battery voltage too high | | 03 | Battery over-charged |
| 04 | Battery voltage too low | | 04 | Low battery |
| 05 | Output short / internal over-temp | | 07 | Overload |
| 06 | Output voltage too high | | 10 | Output power derating |
| 07 | Overload timeout | | 15 | PV energy low |
| 08 | Bus voltage too high | | 16 | High AC input (>280 V) at BUS soft start |
| 09 | Bus soft start failed | | `E9` | Battery equalization |
| 51 | Over current or surge | | `bP` | Battery not connected |
| 52 | Bus voltage too low | | | |
| 53 | Inverter soft start failed | | | |
| 55 | Over DC voltage in AC output | | | |
| 57 | Current sensor failed | | | |
| 58 | Output voltage too low | | | |
| 59 | PV voltage over limit | | | |

Fault **01** is the one seen here (2026-08-24), alongside the old Pi's
undervoltage reboot.

---

## Battery: Xunzel SOLARX-30, ×2 in series = 24 V

Sealed deep-cycle **AGM**. Datasheet `XU-92220226-MA`.

### Per 12 V unit

| Spec | Value |
|---|---|
| Nominal | 12 V, 6 cells |
| Capacity C10 / C20 / C100 / C120 | 25.20 / 26.00 / 29.20 / **30.00** Ah |
| Internal resistance | **9 mΩ** |
| Max discharge current (5 s) | **260 A** |
| **Recommended max charge current** | **7.80 A** |
| Weight | 8.50 kg |
| Cycles (IEC61427) | 2000 |
| Service life | 10–12 years at 20 °C |

### Charging voltages @ 25 °C — the numbers that matter

| | Per 12 V unit | **24 V pack (×2)** |
|---|---|---|
| **Absorption / bulk** | **14.40–14.70 V** | **28.80–29.40 V** |
| Float | 13.60–13.90 V | 27.20–27.80 V |
| Equalization | 14.60–14.80 V | 29.20–29.60 V |
| Temp. compensation | −3 mV/K per cell | ≈ −36 mV/K for the pack |

### Depth of discharge vs cycle life (from the curve, p.3)

| DoD | ~cycles |
|---|---|
| 10 % | ~2000 |
| 30 % | ~1300 |
| 50 % | ~900 |
| 63 % | ~650–700 |
| 80 % | ~500 |

### Resting voltage vs state of charge (from the curve, p.3)

Per cell 1.93 V at 0 % rising to ~2.16 V at 100 %. For this 12-cell pack:

| Pack at rest | ~SoC |
|---|---|
| **25.9 V** | **100 %** |
| 25.6 V | ~86 % |
| 25.2 V | ~75 % |
| 24.0 V | ~37 % |

Other: self-discharge 2.5–3 %/month at 20 °C, recharge every 6 months in
storage. Capacity is temperature-dependent — 100 % at 25 °C, 98 % at 20 °C,
90 % at 10 °C, 76 % at 0 °C.

---

## Where configured and specified disagree

Three gaps, found by putting the two documents side by side. **None of them
has been changed** — see NOTES.md for the reasoning and any decision.

**1. The pack is chronically undercharged.** Bulk is at the inverter's
default 28.2 V; the datasheet wants **28.80–29.40 V**. Float is 27.0 V
against a specified **27.20–27.80 V**. Both sit just below spec, and the
evidence matches: this pack rests at 25.3–25.6 V every evening, which the
datasheet's own curve puts at **75–86 % SoC, not full**. An AGM that never
saturates sulfates over time.

Corrects a claim repeated several times in this project's history, including
in the `resume_voltage` reasoning: **"a full pack at rest reads ~25.6 V" is
wrong.** 25.6 V is ~86 %. Full at rest is ~25.9 V.

**2. Charge current is above the recommended maximum.** Program 11 is 10 A;
the datasheet's recommended maximum is **7.80 A** (the batteries are in
series, so pack current = cell current). Observed charging runs 10–11 A,
i.e. ~130–140 % of recommended.

This corrects the note in NOTES.md that called 10 A "the lowest useful
option" and lamented that C/5 ≈ 6 A was not selectable — the real issue is
not that 10 A is coarse but that it is **over spec**. Program 11 does offer
**2 A**, which is under spec but would take ~15 h for a full recharge and
would break the daily cycle the battery window depends on. Program 02 (total
charging current) bottoms out at 10 A, so it cannot help either. The choice
is genuinely 2 A or 10 A, with nothing in between.

**3. Program 29 (low DC cut-off) has never been checked.** Default is
21.0 V. That is below `floor_voltage` (24.0 V) and below program 12 (24 V),
so in normal operation nothing should reach it — but it is an absolute floor
that ignores load percentage, and it is the last line before damage. Worth
reading next time someone is at the panel.

---

## Settings deliberately left alone

- **11 (10 A)** — over spec, but 2 A is the only lower option and it breaks
  the daily recharge.
- **12 (24 V)** — a deliberate deep-cycle choice, ~37 % SoC, ~650–700 cycles.
  Reviewed against the datasheet and chosen knowingly.
- **13 (27 V)** — looks like a float voltage used as a threshold, but the 3 V
  gap to program 12 is what damps the pump-induced relay chatter. Lowering it
  towards a rest-reachable value would reproduce the 2026-09-14 churn.
