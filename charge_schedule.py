#!/usr/bin/env python3
"""
Time-of-day charge scheduling for the Tecnoware / Voltronic PI30 inverter.

The inverter itself has NO built-in scheduling (QPKT/QLDT/QPRIO all NAK),
so this drives the charger source priority (PCP) from the host instead.
Run it periodically from launchd/cron; each run works out which rule
applies to the current time and applies it if it isn't already set.

Charger source priority values (verified on hardware):
    00  utility first
    01  solar first, falls back to utility   <- normal / safe default
    02  solar + utility
    03  solar ONLY -- utility charging is OFF

WARNING: 03 means the battery will not charge at all without sun. Only use
it during daylight, and always have a rule that returns to 01/02 at night.
This script enforces that with a low-battery safety override.

Config is a JSON file, e.g.:

{
  "port": "/dev/cu.usbserial-1460",
  "min_battery_voltage": 24.0,
  "rules": [
    {"from": "09:00", "to": "17:00", "pcp": "03", "why": "daytime: solar only"},
    {"from": "17:00", "to": "09:00", "pcp": "01", "why": "night: allow utility"}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, time as dtime

import serial

from transport import InverterConnection, InverterError
from webapp.atomic_write import write_json_atomic

VALID_PCP = {"00", "01", "02", "03"}
STATE_FILE_DEFAULT = ".charge_schedule_state.json"


def parse_hhmm(s: str) -> dtime:
    hh, mm = s.split(":")
    return dtime(int(hh), int(mm))


def rule_active(now: dtime, start: dtime, end: dtime) -> bool:
    """Inclusive of start, exclusive of end. Handles windows that wrap
    past midnight (e.g. 17:00 -> 09:00)."""
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def pick_rule(rules: list[dict], now: dtime) -> dict | None:
    for r in rules:
        if rule_active(now, parse_hhmm(r["from"]), parse_hhmm(r["to"])):
            return r
    return None


def open_with_retry(port: str, attempts: int = 5, delay: float = 4.0) -> InverterConnection:
    """The USB-serial adapter can briefly report "Resource busy" right after
    a previous process closes it. Observed in practice, and fatal for an
    unattended cron/launchd run, so retry rather than give up."""
    last = None
    for i in range(attempts):
        try:
            return InverterConnection(port)
        except (serial.SerialException, OSError) as e:
            last = e
            if i < attempts - 1:
                time.sleep(delay)
    raise InverterError(f"could not open {port} after {attempts} attempts: {last}")


def read_status(conn: InverterConnection, attempts: int = 5) -> dict:
    """Read QPIGS, tolerating the occasional truncated/corrupt frame this
    link produces. A short read used to raise IndexError and kill the run,
    so validate the field count and retry instead."""
    last_err = None
    for _ in range(attempts):
        try:
            raw = conn.query("QPIGS")
        except InverterError as e:
            last_err = e
            time.sleep(0.5)
            continue
        parts = raw.lstrip("(").split(" ")
        if len(parts) < 14:
            last_err = InverterError(f"short QPIGS frame ({len(parts)} fields): {raw!r}")
            time.sleep(0.5)
            continue
        try:
            status = {
                "battery_voltage": float(parts[8]),
                "charging_current": int(parts[9]),
                "battery_capacity": int(parts[10]),
                "pv_voltage": float(parts[13]),
            }
            if not all(math.isfinite(status[key])
                       for key in ("battery_voltage", "pv_voltage")):
                raise ValueError("non-finite numeric field")
            return status
        except (ValueError, OverflowError) as e:
            last_err = InverterError(f"unparsable QPIGS {raw!r}: {e}")
            time.sleep(0.5)
    raise InverterError(f"could not read a valid QPIGS after {attempts} tries: {last_err}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config", help="path to the JSON schedule config")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be sent, touch nothing")
    ap.add_argument("--force", action="store_true",
                    help="apply even if it matches the last applied value")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = json.load(fh)
    rules = cfg["rules"]
    for r in rules:
        if r["pcp"] not in VALID_PCP:
            print(f"config error: bad pcp value {r['pcp']!r}", file=sys.stderr)
            return 2

    now = datetime.now()
    rule = pick_rule(rules, now.time())
    if rule is None:
        print(f"{now:%Y-%m-%d %H:%M}  no rule matches; leaving inverter alone")
        return 0

    target = rule["pcp"]
    why = rule.get("why", "")

    if args.dry_run:
        print(f"{now:%Y-%m-%d %H:%M}  would set PCP{target}  ({why})")
        return 0

    try:
        with open_with_retry(cfg["port"]) as conn:
            status = read_status(conn)

            # Safety override: never leave the pack on solar-only if it is
            # already low -- that is how you flatten a battery overnight.
            floor = cfg.get("min_battery_voltage")
            if target == "03" and floor is not None and status["battery_voltage"] <= floor:
                target = "01"
                why = (f"OVERRIDE: battery {status['battery_voltage']}V at or below "
                       f"{floor}V floor, forcing utility charging")

            state_path = cfg.get("state_file", STATE_FILE_DEFAULT)
            try:
                with open(state_path) as fh:
                    last = json.load(fh).get("pcp")
            except (OSError, ValueError):
                last = None

            if last == target and not args.force:
                print(f"{now:%Y-%m-%d %H:%M}  already PCP{target}; nothing to do "
                      f"(batt {status['battery_voltage']}V, "
                      f"{status['charging_current']}A)")
                return 0

            resp = conn.send_set_command(f"PCP{target}")
            ok = resp.startswith("(ACK")
            print(f"{now:%Y-%m-%d %H:%M}  PCP{target} -> {resp}  ({why})")
            print(f"   before: batt {status['battery_voltage']}V "
                  f"chg {status['charging_current']}A "
                  f"cap {status['battery_capacity']}% pv {status['pv_voltage']}V")

            if ok:
                write_json_atomic(
                    state_path, {"pcp": target, "at": now.isoformat()})
                after = read_status(conn)
                print(f"   after : batt {after['battery_voltage']}V "
                      f"chg {after['charging_current']}A "
                      f"cap {after['battery_capacity']}%")
                # Remember: QPIRI never changes, so charging current is the
                # only honest confirmation available.
            return 0 if ok else 1

    except (InverterError, OSError) as e:
        print(f"{now:%Y-%m-%d %H:%M}  ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
