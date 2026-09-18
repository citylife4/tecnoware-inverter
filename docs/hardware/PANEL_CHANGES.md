# Front-panel change list — 2026-09-18

A checklist to work through standing at the inverter. Full context in
[../../HARDWARE_REFERENCE.md](../../HARDWARE_REFERENCE.md).

Access: hold **ENTER** 3 s, **UP/DOWN** to pick a program, **ENTER** to
confirm, **ESC** to exit. The manual says settings **must be changed in
battery mode and the unit rebooted** before they take effect.

---

## 0. Check this first — nothing else works without it

| # | Setting | Must read | Why |
|---|---|---|---|
| **05** | Battery type | **`USE`** | Programs 26 and 27 are **only settable when 05 is `USE`**. If it shows `AGn` or `FLd`, the unit forces its preset voltages and the two changes below are impossible. `QPIRI` reports code 2, which should mean `USE` — confirm on the panel. |

---

## 1. Changes proposed

| # | Setting | Now | Change to | Why |
|---|---|---|---|---|
| **26** | Bulk / absorption charging voltage | **28.2 V** | **29.0 V** | 28.2 V is the inverter's factory default, not a value chosen for this battery. The SOLARX datasheet specifies **28.80–29.40 V** absorption, so the pack is charged below spec and never fully saturates. It rests at 25.3–25.6 V every evening, which the datasheet's own curve puts at **75–86 % SoC** — full at rest is ~25.9 V. A sealed AGM that never saturates sulfates and loses capacity. **29.0 V, not 29.4**: this unit has no battery temperature sensor, and the datasheet's −3 mV/K per cell means the ideal drops ~0.18 V for every 5 °C above 25 °C. The low end of the range leaves headroom for a warm shed. |
| **27** | Float charging voltage | **27.0 V** | **27.4 V** | Also the factory default, also below spec — the datasheet wants **27.20–27.80 V**. A float below spec means the pack slowly self-discharges instead of being held topped up, which compounds the problem above. 27.4 V is mid-range. It also sits just above program 13 (27.0 V), so a floating pack reliably satisfies the return-to-battery threshold. |

**After changing, the unit must be rebooted for these to apply.** That is
also the opportunity to verify: `QPIRI` reports settings as of the last boot,
so once it restarts the API will finally show the true values instead of the
stale ones. Tell me when it is back and I will confirm.

---

## 2. Read and report — do not change

| # | Setting | Expect | Why it matters |
|---|---|---|---|
| **29** | Low DC cut-off voltage | 21.0 V (default) | Never checked. The manual says it is fixed "no matter what percentage of load is connected", so it is an absolute last line before damage. It should sit below `floor_voltage` (24.0 V) and program 12 (24 V), so nothing reaches it in normal operation — but we have never confirmed that it does. |
| **02** | Max total charging current | 60 A (default) | Caps program 11: if 02 is *smaller* than 11, the unit uses 02's value for the utility charger. Worth knowing before anyone tries to tune charge current. |

---

## 3. Deliberately leaving alone

| # | Setting | Value | Why not |
|---|---|---|---|
| **11** | Max utility charging current | 10 A | **Over spec** — the datasheet's recommended maximum is 7.80 A, so 10 A is ~130 % and observed charging at 10–11 A is ~140 %. But the only lower option is **2 A**, which needs ~15 h for a full recharge and would break the daily cycle the battery window depends on. Program 02 bottoms out at 10 A, so there is nothing in between. A known overshoot, not an oversight. |
| **12** | Voltage point back to utility | 24 V | A deliberate deep-cycle choice: ~37 % SoC, ~650–700 cycles by the datasheet curve, against ~2000 at shallow depth. Reviewed and chosen knowingly. |
| **13** | Voltage point back to battery | 27 V | Looks like a float voltage misused as a threshold — a rested full pack is only ~25.9 V, so it is reachable only while charging. But the **3 V gap to program 12 is what damps the pump-induced relay chatter**: closing it would reproduce the 2026-09-14 episode of 14 battery/grid transitions in 21 minutes. |
| **30** | Battery equalization | `EdS` disabled | The datasheet lists an equalization voltage (29.2–29.6 V), but these are **sealed** AGM. Equalizing a sealed cell vents gas it cannot replace and dries it out. It is a recovery procedure for flooded cells, not routine maintenance here. Leave disabled. |

---

## 4. Not a panel setting, but the biggest item on site

**Move the water pump off the protected output.** Confirmed 2026-09-18 that
it is hand-started whenever someone is gardening, at no predictable hour — so
no time window can protect against it. It has hit the pack in battery mode on
four consecutive days at hours 6, 13 and 17, peaking at **2889 W** and pulling
the pack to 20.9 V (~113 A through 0.045 Ω). It is the cause of every
battery-mode override and most of the relay wear, and it is the one problem
that software cannot fix.
