#!/usr/bin/env python3
"""
A fake PI30 inverter on a pseudo-terminal, for developing against without
the hardware (or without risking a live power system).

    python3 mock_inverter.py
    # prints e.g. /dev/pts/7 -- point the server at it:
    python3 serve.py --port /dev/pts/7

It mimics the real unit's quirks on purpose, because those quirks are what
the client code has to survive:
  * queries answer with no CRC, set commands answer "(ACK" WITH a CRC,
  * unsupported queries stay silent rather than replying NAK,
  * QPI prefix-matching false positives (QPIBI etc. echo "(PI30"),
  * QPIRI never changes after a write, even though the write took effect,
  * an occasional truncated QPIGS frame (--glitch).
"""

from __future__ import annotations

import argparse
import os
import pty
import random
import time

from protocol import crc_bytes

STATE = {
    "battery_voltage": 24.20,
    "battery_capacity": 50,
    "charging_current": 0,
    "pv_power": 0,
    "pv_voltage": 0.0,
    "load_w": 120,
    "pcp": "01",
    "pop": "00",
    "mode": "L",
}

SUPPORTED_SILENT = {"QMD", "QVFTR", "QT", "QEY"}   # answer nothing, like the real unit


def qpigs() -> str:
    s = STATE
    return ("({:05.1f} 49.9 231.0 50.0 {:04d} {:04d} {:03d} 401 {:05.2f} {:03d} "
            "{:03d} 0026 {:04d} {:05.1f} {:05.2f} {:05d} 00010110 00 00 {:05d} 010").format(
        232.0, s["load_w"], s["load_w"], min(99, s["load_w"] // 30),
        s["battery_voltage"], s["charging_current"], s["battery_capacity"],
        int(s["pv_power"] / 24) if s["pv_power"] else 0, s["pv_voltage"],
        s["battery_voltage"], 0, s["pv_power"])


def qpiri() -> str:
    # Static rated values -- deliberately does NOT reflect PCP/POP writes,
    # except for the two priority code fields, matching the real unit.
    return ("(230.0 26.1 230.0 50.0 26.1 6000 6000 24.0 11.0 10.5 14.1 13.5 2 60 "
            "06P 0 {} {} 0 1 6 01 52.0 0 0").format(STATE["pop"][-1], STATE["pcp"][-1])


def handle(cmd: str, glitch: bool):
    if cmd in SUPPORTED_SILENT:
        return None
    if cmd == "QPI":
        return "(PI30"
    # The firmware prefix-matches QPI and ignores trailing characters.
    if cmd.startswith("QPI") and cmd not in ("QPIGS", "QPIRI", "QPIWS"):
        return "(PI30"
    if cmd == "QID":
        return "(96332210100037"
    if cmd == "QVFW":
        return "(VERFW:00072.40"
    if cmd == "QVFW2":
        return "(VERFW2:00072.40"
    if cmd == "QMOD":
        return "(" + STATE["mode"]
    if cmd == "QPIGS":
        out = qpigs()
        if glitch and random.random() < 0.15:
            return out[:random.randint(10, 40)]   # truncated frame
        return out
    if cmd == "QPIRI":
        return qpiri()
    if cmd == "QPIWS":
        return "(00000000000000000000000000000000"
    if cmd == "QFLAG":
        return "(EbjkuvxzDaygz"
    if cmd in ("QDI", "QDI2"):
        return "(230.0 50.0 0030 10.5 27.0 28.2 12.0 0 30 060 0 0 0 0 0 0"
    if cmd == "QPVIPV":
        return "({:05.1f}".format(STATE["pv_voltage"])

    # --- set commands ---
    if cmd.startswith("PCP") and len(cmd) >= 5:
        STATE["pcp"] = cmd[3:5]
        # Solar-only with no PV means the charger stops -- the behaviour
        # that actually flattened the real pack.
        STATE["charging_current"] = 0 if cmd[3:5] == "03" else 12
        return "(ACK"
    if cmd.startswith("POP") and len(cmd) >= 5:
        STATE["pop"] = cmd[3:5]
        return "(ACK"
    if cmd in ("BZON", "BZOFF", "SON", "SOFF", "REEP", "PF"):
        if cmd == "SOFF":
            STATE["mode"] = "S"
        if cmd == "SON":
            STATE["mode"] = "L"
        return "(ACK"
    if cmd[:1] in ("P", "M", "B", "F", "C") and len(cmd) > 2:
        return "(ACK"
    return None      # unknown -> silence, like the real firmware


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glitch", action="store_true",
                    help="randomly truncate QPIGS frames, as the real link does")
    ap.add_argument("--link", help="also symlink the pty to this stable path")
    args = ap.parse_args()

    master, slave = pty.openpty()
    name = os.ttyname(slave)
    if args.link:
        if os.path.islink(args.link):
            os.unlink(args.link)
        os.symlink(name, args.link)
        print(f"mock inverter on {name} (symlinked {args.link})", flush=True)
    else:
        print(name, flush=True)

    buf = b""
    while True:
        try:
            data = os.read(master, 256)
        except OSError:
            break
        if not data:
            break
        buf += data
        while b"\r" in buf:
            line, buf = buf.split(b"\r", 1)
            if not line:
                continue
            # Strip a trailing CRC if the client sent one (set commands do).
            text = line.decode("latin1")
            for candidate in (text[:-2], text):
                if crc_bytes(candidate) == line[-2:] and len(candidate) >= 2:
                    text = candidate
                    break
            cmd = text.strip()
            reply = handle(cmd, args.glitch)
            if reply is None:
                continue
            time.sleep(0.05)
            payload = reply.encode("latin1")
            # Set commands echo a CRC; queries don't -- exactly as observed.
            if reply == "(ACK":
                payload += crc_bytes(reply)
            os.write(master, payload + b"\r")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
