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


def apply_low_battery_floor(service, target: str):
    """If `target` is the solar-only PCP and the battery is at/under
    `service.min_battery_voltage` (or unreadable), force utility-fallback
    ("01") instead and return why. Returns (effective_target, reason_or_None).

    Shared by webapp/scheduler.py and webapp/grid_charge.py so every
    automation that can land on PCP03 enforces the identical interlock a
    manual write gets from check_policy() below -- solar-only with no
    charging source drains the pack, and it already happened once on this
    hardware (see CLAUDE.md gotcha #2).
    """
    if target != SOLAR_ONLY_PCP:
        return target, None
    floor = service.min_battery_voltage
    if floor is None:
        return target, None
    if not is_finite_number(floor):
        return "01", ("OVERRIDE: battery safety floor is invalid -- "
                      "forcing utility charging rather than risk solar-only")
    v = service.battery_voltage()
    if not is_finite_number(v):
        return "01", ("OVERRIDE: battery voltage could not be read -- "
                      "forcing utility charging rather than risk solar-only")
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
