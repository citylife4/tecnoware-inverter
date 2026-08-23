#!/usr/bin/env python3
"""
Small CLI to talk to a Tecnoware SolarPower (Voltronic-based) inverter
over its USB-serial "monitoring" cable, without needing the Windows app.

Protocol details (2400 8N1, framing, CRC) were reverse-engineered by
decompiling the bundled SolarPowerApp's lib/SolarPower.jar -- see
protocol.py and transport.py for exactly what was found and where.

Usage:
    python3 inverter_ctl.py --list-ports
    python3 inverter_ctl.py --port /dev/tty.usbserial-XXXX
    python3 inverter_ctl.py --port /dev/tty.usbserial-XXXX --send QPIGS
"""

from __future__ import annotations

import argparse
import sys

import serial

from commands import QUERY_COMMANDS, SET_COMMANDS
from parsers import QPIGS_FIELDS, QPIRI_FIELDS, print_parsed
from transport import InverterConnection, InverterError, list_candidate_ports

NAMED_FIELD_COMMANDS = {
    "QPIGS": QPIGS_FIELDS,
    "QPIRI": QPIRI_FIELDS,
}


def cmd_list_ports() -> None:
    candidates = list_candidate_ports()
    if not candidates:
        print("No serial ports found. Is the inverter's USB cable plugged in?")
        return
    print("Available serial ports:")
    for c in candidates:
        star = " <- likely USB-serial adapter" if c.likely else ""
        print(f"  {c.device:30} {c.description}{star}")


def pick_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    candidates = list_candidate_ports()
    if not candidates:
        print("No serial ports found. Pass --port explicitly, or plug in the cable.")
        sys.exit(1)
    if len(candidates) == 1:
        return candidates[0].device
    print("Multiple serial ports found:")
    for i, c in enumerate(candidates):
        star = "  <- likely" if c.likely else ""
        print(f"  [{i}] {c.device}  {c.description}{star}")
    choice = input("Pick a port number: ").strip()
    try:
        return candidates[int(choice)].device
    except (ValueError, IndexError):
        print("Invalid choice.")
        sys.exit(1)


def identify(conn: InverterConnection) -> None:
    print("Identifying device...")
    for cmd, label in [("QPI", "Protocol"), ("QID", "Serial number"),
                        ("QMD", "Model"), ("QVFW", "Firmware (main)"),
                        ("QVFW2", "Firmware (secondary)")]:
        try:
            resp = conn.query(cmd)
            print(f"  {label:20} {cmd:6} -> {resp}")
        except InverterError as e:
            print(f"  {label:20} {cmd:6} -> [no reply: {e}]")


def match_set_command(raw: str) -> str | None:
    """Return the longest SET_COMMANDS key that raw starts with, e.g.
    'POP01' -> 'POP', 'F50' -> 'F50'."""
    matches = [c for c in SET_COMMANDS if raw.startswith(c)]
    return max(matches, key=len) if matches else None


def run_single_command(conn: InverterConnection, raw: str, yes: bool) -> None:
    set_cmd = match_set_command(raw)
    if set_cmd:
        if not confirm(raw, set_cmd, yes):
            print("Aborted.")
            return
        try:
            resp = conn.send_set_command(raw)
            print(f"{raw} -> {resp}")
            if resp.startswith("(ACK"):
                print("  applied. NOTE: QPIRI will NOT show the change (it reports"
                      " static rated values). Verify via QPIGS instead --")
                print("  e.g. charger changes show up in battery_charging_current.")
        except InverterError as e:
            print(f"Error: {e}")
        return
    try:
        resp = conn.query(raw)
        print(f"{raw} -> {resp}")
        maybe_show_fields(raw, resp)
    except InverterError as e:
        print(f"Error: {e}")


def confirm(raw: str, set_cmd: str, yes: bool) -> bool:
    if yes:
        return True
    desc = SET_COMMANDS.get(set_cmd, "changes device settings")
    answer = input(
        f"'{raw}' is a SET command ({desc}). This will change inverter "
        f"behaviour. Continue? [y/N] "
    ).strip().lower()
    return answer == "y"


def maybe_show_fields(command: str, response: str) -> None:
    """For space-delimited multi-field responses, print a named breakdown
    for commands we have a known field layout for (QPIGS/QPIRI) *and* the
    raw indexed breakdown, so a mismatch (this device's QPIRI has at least
    one field -- max_charging_current -- that doesn't cleanly match the
    generic public PI30 layout, likely a parallel-mode suffix) is visible
    rather than silently mislabeled. Everything else just gets the
    indexed dump."""
    field_defs = NAMED_FIELD_COMMANDS.get(command)
    if field_defs:
        print_parsed(response, field_defs)
    body = response[1:] if response.startswith("(") else response
    parts = body.split(" ")
    if len(parts) < 3:
        return
    print("  raw indexed fields (cross-check against the names above):")
    for i, p in enumerate(parts):
        print(f"    [{i:2}] {p}")


def scan_capabilities(conn: InverterConnection) -> None:
    """Probe every query command and report what this specific unit
    actually supports. Read-only -- sends no state-changing commands."""
    print("Scanning supported queries (read-only, no settings are touched)...\n")
    qpi = ""
    try:
        qpi = conn.query("QPI")
    except InverterError:
        pass

    supported, nak, silent, suspect = [], [], [], []
    for cmd in sorted(c for c in QUERY_COMMANDS if c.startswith("Q")):
        try:
            resp = conn.query(cmd)
        except InverterError as e:
            (nak if "NAK" in str(e) else silent).append(cmd)
            continue
        # Guard against the firmware's QPI prefix-matching false positives
        if cmd != "QPI" and qpi and resp == qpi:
            suspect.append(cmd)
        else:
            supported.append((cmd, resp))

    print(f"SUPPORTED ({len(supported)}):")
    for cmd, resp in supported:
        desc = QUERY_COMMANDS.get(cmd, "")
        print(f"  {cmd:8} {resp[:58]}")
        print(f"           {desc}")
    if suspect:
        print(f"\nFALSE POSITIVES ({len(suspect)}) -- echo the QPI reply "
              f"{qpi!r}, so not really supported:")
        print("  " + ", ".join(suspect))
    if nak:
        print(f"\nEXPLICITLY REJECTED / NAK ({len(nak)}):")
        print("  " + ", ".join(nak))
    if silent:
        print(f"\nNO RESPONSE ({len(silent)}):")
        print("  " + ", ".join(silent))
    print("\nNote: set commands are NOT probed here, since on the reference "
          "unit they return (ACK without applying. See README.")


def interactive(conn: InverterConnection) -> None:
    identify(conn)
    print()
    print("Common queries:", ", ".join(sorted(QUERY_COMMANDS)[:12]), "...")
    print("Type a command (e.g. QPIGS), 'list' to see all commands, or 'quit'.")
    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        if raw.lower() in ("quit", "exit", "q"):
            break
        if raw.lower() == "list":
            print("Queries:")
            for c, d in sorted(QUERY_COMMANDS.items()):
                print(f"  {c:10} {d}")
            print("Set commands (change device state):")
            for c, d in sorted(SET_COMMANDS.items()):
                print(f"  {c:10} {d}")
            continue
        run_single_command(conn, raw, yes=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial device, e.g. /dev/tty.usbserial-XXXX")
    ap.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    ap.add_argument("--send", help="send one command (e.g. QPIGS) and exit")
    ap.add_argument("--scan", action="store_true",
                    help="probe which commands this unit supports (read-only)")
    ap.add_argument("--yes", action="store_true", help="don't prompt to confirm SET commands")
    ap.add_argument("--timeout", type=float, default=3.0, help="response timeout in seconds")
    args = ap.parse_args()

    if args.list_ports:
        cmd_list_ports()
        return

    port = pick_port(args.port)
    print(f"Connecting to {port} at 2400 8N1...")
    try:
        with InverterConnection(port, timeout=args.timeout) as conn:
            if args.scan:
                scan_capabilities(conn)
            elif args.send:
                run_single_command(conn, args.send, args.yes)
            else:
                interactive(conn)
    except serial.SerialException as e:
        print(f"Could not open {port}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
