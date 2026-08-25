#!/usr/bin/env python3
"""
Supervised battery-mode (POP=02) test with automatic revert.

Switching this installation to battery priority is not a "set it and watch"
operation -- three separate things can make it dangerous, and two of them
are silent:

  * The software low-battery interlock is DEAD in POP=02. It works by
    writing PCP01, and PCP is a no-op unless POP=00 (CLAUDE.md gotcha #1).
    So nothing in the web server will protect the pack while this runs.
  * The Raspberry Pi is powered FROM the inverter. A flat pack or a trip
    takes down the logger, the web server, and this watchdog with it.
  * The water pump (~1200-1300 W running, far more starting) shares the
    protected output and trips the inverter from battery. It runs roughly
    19:30-20:30 local.

Hence this script rather than a manual write: it owns the revert. Every
exit path -- floor reached, deadline, Ctrl-C, SIGTERM, an unhandled
exception, or the API going away -- ends by putting POP back to 00 and
verifying it, with retries.

    python3 battery_test.py                        # defaults: floor 25.5V, until 18:30
    python3 battery_test.py --floor 25.8 --until 17:00
    python3 battery_test.py --dry-run              # no writes, just log

The default floor is deliberately shallow. A lead-acid bank cycled to 50%
DoD lasts a few hundred cycles; kept to ~20-30% it lasts thousands, and
this pack is only 30 Ah.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:8080"
SAFE_POP = "00"          # utility first -- the only state proven to charge
TEST_POP = "02"          # battery first
REVERT_ATTEMPTS = 6


class Aborted(Exception):
    """Raised to unwind into the revert path with a reason attached."""


def _token(path: str) -> str:
    with open(path) as fh:
        return json.load(fh)["token"]


def _api(method: str, path: str, token: str, payload=None, timeout=20):
    data = None
    headers = {"Authorization": "Bearer " + token}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def set_pop(token: str, value: str, dry: bool) -> str:
    if dry:
        return "(DRY-RUN"
    # POP throws the physical transfer relay, so it is on the confirm list.
    out = _api("POST", "/api/output-priority", token,
               {"value": value, "confirm": True})
    return str(out.get("response", out))


def revert(token: str, dry: bool, log) -> bool:
    """Put POP back to 00. Never raises -- this is the last thing that runs."""
    for attempt in range(1, REVERT_ATTEMPTS + 1):
        try:
            resp = set_pop(token, SAFE_POP, dry)
            log("revert", "POP=%s tentativa %d -> %s" % (SAFE_POP, attempt, resp))
            if "ACK" in resp or dry:
                return True
        except Exception as e:                    # noqa: BLE001 -- must not escape
            log("revert", "tentativa %d falhou: %s" % (attempt, e))
        time.sleep(5)
    log("revert", "FALHOU apos %d tentativas -- INTERVENCAO MANUAL" % REVERT_ATTEMPTS)
    return False


def parse_hhmm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--floor", type=float, default=25.5,
                    help="revert to POP=00 at or below this pack voltage (default 25.5)")
    ap.add_argument("--until", default="18:30",
                    help="hard deadline, local HH:MM (default 18:30, before the pump)")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--config", default=os.path.join(HERE, "web.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "battery_test.csv"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    deadline_t = parse_hhmm(args.until)
    now = dt.datetime.now()
    deadline = now.replace(hour=deadline_t.hour, minute=deadline_t.minute,
                           second=0, microsecond=0)
    if deadline <= now:
        print("deadline %s ja passou" % args.until, file=sys.stderr)
        return 2
    # The pump window is not negotiable -- refuse rather than trust the operator.
    if deadline.time() >= dt.time(19, 0):
        print("deadline tem de ser antes das 19:00 (bomba)", file=sys.stderr)
        return 2

    token = _token(args.config)
    logf = open(args.out.replace(".csv", ".log"), "a", buffering=1)

    def log(tag, msg):
        line = "%s [%s] %s" % (dt.datetime.now().isoformat(timespec="seconds"), tag, msg)
        print(line, flush=True)
        logf.write(line + "\n")

    new = not os.path.exists(args.out)
    csvf = open(args.out, "a", newline="", buffering=1)
    w = csv.writer(csvf)
    if new:
        w.writerow(["ts", "battery_voltage", "battery_capacity",
                    "battery_discharge_current", "battery_charging_current",
                    "ac_output_active_power", "output_load_percent", "mode"])

    stop = {"why": None}

    def _sig(signum, frame):
        stop["why"] = "sinal %s" % signum
    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    log("start", "piso=%.1fV limite=%s intervalo=%ss dry_run=%s"
        % (args.floor, args.until, args.interval, args.dry_run))

    try:
        resp = set_pop(token, TEST_POP, args.dry_run)
        log("start", "POP=%s -> %s" % (TEST_POP, resp))
        if "ACK" not in resp and not args.dry_run:
            raise Aborted("inversor nao aceitou POP=%s" % TEST_POP)

        fails = 0
        while True:
            if stop["why"]:
                raise Aborted(stop["why"])
            if dt.datetime.now() >= deadline:
                raise Aborted("limite de tempo %s atingido" % args.until)

            try:
                st = _api("GET", "/api/status", token)["status"]
                fails = 0
            except Exception as e:                # noqa: BLE001
                fails += 1
                log("warn", "leitura falhou (%d/5): %s" % (fails, e))
                if fails >= 5:
                    raise Aborted("perdi contacto com o servidor")
                time.sleep(args.interval)
                continue

            v = st.get("battery_voltage")
            w.writerow([st.get("ts"), v, st.get("battery_capacity"),
                        st.get("battery_discharge_current"),
                        st.get("battery_charging_current"),
                        st.get("ac_output_active_power"),
                        st.get("output_load_percent"), st.get("mode")])
            log("tick", "V=%s cap=%s%% desc=%sA carga_saida=%sW modo=%s"
                % (v, st.get("battery_capacity"),
                   st.get("battery_discharge_current"),
                   st.get("ac_output_active_power"), st.get("mode")))

            if isinstance(v, (int, float)) and v <= args.floor:
                raise Aborted("piso de %.1fV atingido (V=%.1f)" % (args.floor, v))

            time.sleep(args.interval)

    except Aborted as e:
        log("stop", "a terminar: %s" % e)
    except Exception as e:                        # noqa: BLE001
        log("stop", "erro inesperado: %r" % e)
    finally:
        ok = revert(token, args.dry_run, log)
        log("end", "POP restaurado" if ok else "POP *** NAO *** restaurado")
        csvf.close()
        logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
