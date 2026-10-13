"""
Write-command policy for the web/API layer.

The CLI protects the user with an interactive y/N prompt before any SET
command. An HTTP API has no such prompt, so the same protection has to be
expressed as data: which commands are writes at all, which are dangerous
enough to demand an explicit confirm flag, and which are refused outright
unless the caller overrides a safety interlock.

Nothing here re-implements the protocol -- it only decides whether a
command is allowed to reach transport.py.
"""

from __future__ import annotations

import math

from commands import QUERY_COMMANDS, SET_COMMANDS
from protocol import validate_command

# Set commands that are recoverable and routinely useful: priorities,
# buzzer, charge voltages. These go through on a plain authenticated
# request.
#
# Everything NOT in this list still works, but only with confirm=true --
# see needs_confirmation(). The split is deliberately conservative: the
# decompiled Command class contains many entries whose semantics were only
# inferred from their name (see commands.py OTHER_COMMANDS), and this unit
# is a live power system feeding a real house.
ROUTINE_SET_COMMANDS = {
    "POP",    # output source priority
    "PCP",    # charger source priority
    "PBCV",   # battery re-charge voltage
    "PBDV",   # battery re-discharge voltage
    "PGR",    # grid working range
    "PE",     # enable a function flag
    "PD",     # disable a function flag
    "BZON",   # buzzer on
    "BZOFF",  # buzzer off
    "PSDV",   # battery cut-off (shutdown) voltage
    "MCHGC",  # max total charging current
    "CHGC",   # max charging current
}

# Commands that cut power, rewrite persistent configuration, or change the
# output frequency. Allowed, but only when the caller passes confirm=true,
# so a mistyped curl can't take the house off supply.
DANGEROUS_SET_COMMANDS = {
    "REEP":  "restores EEPROM/factory defaults, wiping the hand-tuned battery setpoints",
    "EPO":   "emergency power off",
    "SOFF":  "turns the inverter output OFF (loads lose power)",
    "SON":   "turns the inverter output ON",
    "CLR":   "clears fault/history records",
    "F50":   "changes output frequency to 50Hz",
    "F60":   "changes output frequency to 60Hz",
    "SNRM":  "sets normal mode",
    "ID":    "changes the device ID",
    "PF":    "resets settings to defaults on many PI30 units",
}

# Charger source priority values, as verified on this hardware. Mirrors the
# table in charge_schedule.py -- keep the two in step.
PCP_VALUES = {
    "00": "Utility first",
    "01": "Solar first, utility fallback",
    "02": "Solar and utility",
    "03": "Solar ONLY (no utility charging)",
}

OUTPUT_PRIORITY_VALUES = {
    "00": "Utility first",
    "01": "Solar first",
    "02": "SBU (solar / battery / utility)",
}

# PCP03 stops grid charging entirely. With no PV that flattens the pack --
# it actually happened on this unit (100%/27.0V -> 80%/26.1V before it was
# caught). The scheduler guards against it with min_battery_voltage; the
# API guards against it here, for the same reason.
SOLAR_ONLY_PCP = "03"


def is_finite_number(value) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value))


# Release condition for LowBatteryLatch: the charger has taken the pack to
# float. Measured 2026-09-20..22 in line mode: bulk runs at 10-11 A while the
# voltage climbs, then the current tapers 8 -> 6 -> 5 and settles at 4 A once
# the pack sits at the 27.4 V float (program 27). Both conditions together,
# because either alone is also true at the wrong moment: a charger that has
# not spun up yet reads 0 A, and bulk passes 27 V at the full 10 A.
FLOOR_RELEASE_MIN_V = 27.0
FLOOR_RELEASE_MAX_A = 4.0
# Consecutive observations. Corrupt QPIGS frames happen (gotcha #8) -- the
# telemetry has "2750.0" volts in it -- and one must not end a charge.
FLOOR_RELEASE_CONFIRMATIONS = 3

# Arming needs the same: consecutive plausible low readings. On 2026-10-11 a
# frame reading V=0.0 / I=100 on line mode (16:30:54; two more at 4.0 V the
# same day) armed the latch on its own, and sunset left PCP01 standing. After a
# night floor that would refill the pack from the grid in the dark -- what the
# 10-08 change exists to stop. Below PLAUSIBLE_MIN_V a reading is corruption,
# not state: a 24 V lead-acid bank on line cannot read that, and the deepest
# real sag on record (2889 W motor start, on battery) was 20.9 V. Such a
# reading neither counts nor resets.
ARM_CONFIRMATIONS = 3
PLAUSIBLE_MIN_V = 18.0


class LowBatteryLatch:
    """Holds the low-battery override until the pack is actually full.

    min_battery_voltage is compared against terminal voltage, and that is a
    resting threshold only while nothing is charging. The moment the override
    starts the charger the pack reads ~27 V even at ~50% state of charge, so a
    stateless check releases on its own success: PCP01, charge, 27 V, PCP03,
    settle, PCP01... a ~5-minute oscillation that does charge the pack, with
    churn. Observed 2026-09-16 (see WATCHLIST.md 2e). Same class of mistake as
    floor_voltage and resume_voltage: a threshold compared against a pack that
    may be on charge must be a resting voltage, or must release on something
    other than voltage. This releases on the charger reaching float.

    observe() mutates and belongs in a controller's tick; `active` is a pure
    read, so get_state() paths can pass the latch without side effects. Not
    persisted: a restart falls back to the stateless check, which still
    charges -- it just churns.
    """

    def __init__(self):
        self.active = False
        self._at_float = 0
        self._low = {}

    def confirm_low(self, key: str, v, threshold: float, on_battery: bool) -> bool:
        """Count a reading towards arming; arm on ARM_CONFIRMATIONS in a row.
        `key` keeps separate counts per rule (daylight floor, night deep).
        Call once per controller tick per rule. Implausible readings and
        battery-mode ones (sag, not state) are ignored."""
        if on_battery or not is_finite_number(v) or v < PLAUSIBLE_MIN_V:
            return False
        if v > threshold:
            self._low[key] = 0
            return False
        self._low[key] = self._low.get(key, 0) + 1
        if self._low[key] >= ARM_CONFIRMATIONS:
            self._low[key] = 0
            self.arm()
            return True
        return False

    def arm(self) -> None:
        """Arm for a reason other than min_battery_voltage (a deep pack at
        night, an overdue maintenance charge). Released the same way."""
        self.active = True
        self._at_float = 0

    def observe(self, service) -> None:
        """Release only. Arming is apply_low_battery_floor()'s job (daylight,
        once per tick, confirmed) and grid_charge's night rules -- never on a
        single reading."""
        if not self.active:
            self._at_float = 0
            return
        if at_float(service):
            self._at_float += 1
            if self._at_float >= FLOOR_RELEASE_CONFIRMATIONS:
                self.active = False
                self._at_float = 0
        else:
            self._at_float = 0


def at_float(service) -> bool:
    """True when the charger has the pack at float: >= FLOOR_RELEASE_MIN_V
    and <= FLOOR_RELEASE_MAX_A, and not in battery mode."""
    v = service.battery_voltage()
    current_fn = getattr(service, "battery_charging_current", None)
    amps = current_fn() if current_fn else None
    mode_fn = getattr(service, "mode", None)
    if (mode_fn() if mode_fn else None) == "B":
        return False
    return (is_finite_number(v) and is_finite_number(amps)
            and amps <= FLOOR_RELEASE_MAX_A and v >= FLOOR_RELEASE_MIN_V)


def apply_low_battery_floor(service, target: str, latch=None, count: bool = True,
                            floor=None):
    """If `target` is the solar-only PCP and the battery is at/under
    `service.min_battery_voltage` (or unreadable), force utility-fallback
    ("01") instead and return why. Returns (effective_target, reason_or_None).

    With a LowBatteryLatch that has been armed, the override also holds after
    the voltage has recovered, until the charger reaches float.

    Shared by webapp/scheduler.py and webapp/grid_charge.py so every
    automation that can land on PCP03 enforces the identical interlock a
    manual write gets from check_policy() below -- solar-only with no
    charging source drains the pack, and it already happened once on this
    hardware (see CLAUDE.md gotcha #2).
    """
    if latch is not None and count and target != SOLAR_ONLY_PCP:
        # Count while already charging too, so the latch is armed by the time
        # the target flips to PCP03 -- otherwise that flip would stop the
        # charger for ARM_CONFIRMATIONS ticks before the latch caught up.
        level = service.min_battery_voltage if floor is None else floor
        if is_finite_number(level):
            mode_fn = getattr(service, "mode", None)
            latch.confirm_low("floor", service.battery_voltage(), level,
                              (mode_fn() if mode_fn else None) == "B")
    if target != SOLAR_ONLY_PCP:
        return target, None
    if latch is not None and latch.active:
        v = service.battery_voltage()
        shown = f"{v:.2f}V" if is_finite_number(v) else "?"
        return "01", (f"OVERRIDE: holding utility charging until the pack "
                      f"reaches float ({shown}) -- the voltage alone reads "
                      f"high while charging")
    floor = service.min_battery_voltage if floor is None else floor
    if floor is None:
        return target, None
    if not is_finite_number(floor):
        return "01", ("OVERRIDE: battery safety floor is invalid -- "
                      "forcing utility charging rather than risk solar-only")
    v = service.battery_voltage()
    if not is_finite_number(v):
        return "01", ("OVERRIDE: battery voltage could not be read -- "
                      "forcing utility charging rather than risk solar-only")
    if latch is not None:
        # With a latch, the latch decides: ARM_CONFIRMATIONS plausible
        # line-mode readings at or under the floor arm it, and it then holds
        # until float. A single reading used to force PCP01 here directly,
        # which both churned (2026-10-09 08:16: PCP01 at 24.2 V, PCP03 30 s
        # later at the charger's 27.0 V) and let one corrupt frame through
        # (2026-10-11 16:30:54, V=0.0). `count=False` is for read-only callers
        # such as a get_state(), so looking at the dashboard arms nothing.
        mode_fn = getattr(service, "mode", None)
        on_battery = (mode_fn() if mode_fn else None) == "B"
        if count and latch.confirm_low("floor", v, floor, on_battery):
            return "01", (f"OVERRIDE: battery {v:.2f}V at or below {floor:.2f}V "
                          f"floor -- forcing utility charging until float")
        return target, None
    if v <= floor:
        return "01", (f"OVERRIDE: battery {v:.2f}V at or below {floor:.2f}V floor "
                      f"-- forcing utility charging")
    return target, None


class CommandRejected(Exception):
    """Raised when policy refuses to send a command to the inverter."""

    def __init__(self, message: str, hint: str = "", code: str = "rejected"):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.code = code


def match_set_command(raw: str):
    """Longest SET_COMMANDS key that raw starts with ('POP01' -> 'POP').

    Same rule inverter_ctl.match_set_command() uses, so the CLI and the web
    layer agree on what counts as a write.
    """
    matches = [c for c in SET_COMMANDS if raw.startswith(c)]
    return max(matches, key=len) if matches else None


def classify(raw: str) -> dict:
    """Describe a raw command without sending it."""
    raw = raw.strip().upper()
    set_cmd = match_set_command(raw)
    is_query = raw in QUERY_COMMANDS

    # "ID" is both a query and a set command in the decompiled tables. An
    # exact "ID" is the harmless identification query; "ID" plus a payload
    # is the write. Resolve in favour of the query so read-only stays read-only.
    if is_query and (set_cmd is None or raw == set_cmd == "ID"):
        return {"command": raw, "kind": "query", "prefix": raw,
                "description": QUERY_COMMANDS.get(raw, "")}
    if set_cmd:
        return {"command": raw, "kind": "set", "prefix": set_cmd,
                "description": SET_COMMANDS.get(set_cmd, ""),
                "dangerous": set_cmd in DANGEROUS_SET_COMMANDS,
                "routine": set_cmd in ROUTINE_SET_COMMANDS}
    if is_query:
        return {"command": raw, "kind": "query", "prefix": raw,
                "description": QUERY_COMMANDS.get(raw, "")}
    return {"command": raw, "kind": "unknown", "prefix": None, "description": ""}


def needs_confirmation(info: dict) -> str:
    """Return a reason string if this command requires confirm=true, else ''."""
    if info["kind"] != "set":
        return ""
    prefix = info["prefix"]
    if prefix in DANGEROUS_SET_COMMANDS:
        return DANGEROUS_SET_COMMANDS[prefix]
    if prefix not in ROUTINE_SET_COMMANDS:
        return (f"{prefix} is a set command whose exact effect on this model was "
                f"inferred from the decompiled command table, not verified on hardware")
    return ""


def check_policy(raw: str, confirm: bool, allow_writes: bool,
                 battery_voltage=None, min_battery_voltage=None) -> dict:
    """Gate a command. Returns its classification, or raises CommandRejected.

    battery_voltage/min_battery_voltage implement the PCP03 interlock; pass
    battery_voltage=None when it could not be read, which is itself treated
    as "not safe to go solar-only".
    """
    if raw.strip():
        try:
            validate_command(raw.strip(" "))
        except ValueError as e:
            raise CommandRejected(str(e), code="invalid_command") from e
    info = classify(raw)

    if not info["command"]:
        raise CommandRejected("empty command", code="empty")

    if info["kind"] == "unknown":
        raise CommandRejected(
            f"{info['command']!r} is not a known command",
            hint="GET /api/commands lists everything this build recognises",
            code="unknown_command")

    if info["kind"] == "query":
        return info

    if not allow_writes:
        raise CommandRejected(
            "this server is running read-only",
            hint="restart without --read-only to enable writes",
            code="read_only")

    priorities = {"PCP": PCP_VALUES, "POP": OUTPUT_PRIORITY_VALUES}
    if (info["prefix"] in priorities
            and info["command"][3:] not in priorities[info["prefix"]]):
        raise CommandRejected("invalid priority parameter", code="invalid_command")

    reason = needs_confirmation(info)
    if reason and not confirm:
        raise CommandRejected(
            f"{info['command']} needs explicit confirmation: {reason}",
            hint='resend with {"confirm": true}',
            code="confirmation_required")

    if info["prefix"] == "PCP" and info["command"][3:5] == SOLAR_ONLY_PCP:
        if min_battery_voltage is not None:
            if not is_finite_number(min_battery_voltage):
                raise CommandRejected(
                    "refusing PCP03 (solar-only) because the configured battery "
                    "safety floor is invalid",
                    hint="fix min_battery_voltage in the server config",
                    code="battery_floor_invalid")
            if not is_finite_number(battery_voltage):
                raise CommandRejected(
                    "refusing PCP03 (solar-only) because battery voltage could "
                    "not be read to check it against the safety floor",
                    hint="fix the QPIGS read first, or lower/remove min_battery_voltage",
                    code="battery_unknown")
            if battery_voltage <= min_battery_voltage:
                raise CommandRejected(
                    f"refusing PCP03 (solar-only): battery is {battery_voltage:.2f}V, "
                    f"at or below the {min_battery_voltage:.2f}V safety floor. Solar-only "
                    f"charging with no sun will keep draining the pack.",
                    hint="use PCP01 (solar first, utility fallback) instead",
                    code="battery_too_low")
    return info
