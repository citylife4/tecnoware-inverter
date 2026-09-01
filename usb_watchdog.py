#!/usr/bin/env python3
"""
Recover the USB-serial adapter when it wedges, without a person present.

Written after 2026-08-30 18:29, when the Prolific PL2303 dropped off the bus
mid-test and came back on a different device node. Every layer below failed
in a way nothing could route around:

    termios.error: (5, 'Input/output error')     -- port open but unusable
    pl2303 ttyUSB1: pl2303_set_control_lines - failed: -32
    usb 1-1.4: can't set config #1, error -32    -- device-level unbind no help

What actually recovered it was unbinding and re-binding the **parent hub**,
not the device. That is the whole point of this script: nothing in
serve.py can do it, and the inverter was left stuck in POP=02 -- loads on
battery, discharging, with no way to command it back -- for eight minutes
until it was done by hand. During an absence that is unbounded.

Detection is deliberately not "can I open the port": a wedged PL2303 opens
fine and then errors on first use. It asks the running service whether its
last successful read is recent, which is the same question that matters.

    python3 usb_watchdog.py                 # one check, exit code says what it found
    python3 usb_watchdog.py --daemon        # keep checking (systemd timer/service)
    python3 usb_watchdog.py --dry-run       # diagnose without touching the bus

Needs passwordless sudo for the two sysfs writes, or to run as root.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from notify import Notifier, load_config

HERE = os.path.dirname(os.path.abspath(__file__))

# Kept generous: the poller retries internally and the serial link is
# expected to drop the odd frame (CLAUDE.md gotcha #8). This must only fire
# on a link that is genuinely gone, never on ordinary noise.
STALE_AFTER_S = 300.0

# Re-binding the hub disconnects every device on it. On this Pi that is the
# adapter alone, but the wait lets enumeration settle before we judge.
SETTLE_S = 12.0

# Give up rather than power-cycle a bus in a loop. If this many attempts do
# not help, the adapter or its cable is the problem and it needs hands.
MAX_ATTEMPTS = 3


def _log(msg: str) -> None:
    print("%s  %s" % (dt.datetime.now().isoformat(timespec="seconds"), msg),
          flush=True)


def _service_health(config_path: str, timeout: float = 10.0):
    """(healthy, detail). None means "cannot tell, do nothing".

    An unreachable service used to return None here, on the reasoning that
    systemd would restart it and a bus reset would not help. Both halves
    were wrong, and it cost 14.5 hours on 2026-09-01: the serial thread
    wedged holding its lock at 00:34, so /api/status blocked forever while
    the process stayed alive. systemd's Restart=always never fired -- it
    only watches for exit -- and this function reported "skipping" every
    five minutes through the night. The 04:30 battery window never ran and
    no telemetry was written at all.

    A service that does not answer is now a fault to act on. Only a config
    we cannot read is genuinely none of our business.
    """
    try:
        with open(config_path) as fh:
            cfg = json.load(fh)
        port = int(cfg.get("http_port", 8080))
        token = cfg["token"]
    except (OSError, ValueError, KeyError) as e:
        return None, "config unreadable: %s" % e

    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/status" % port,
        headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except Exception as e:                     # noqa: BLE001
        # Deliberately False, not None: see the docstring. /api/health can
        # still answer while /api/status blocks on the serial lock, so a
        # liveness probe would have missed this too.
        return False, "service unreachable: %s" % e

    if body.get("connected"):
        return True, "connected"

    last = body.get("last_success")
    if not last:
        return False, "never read since start"
    try:
        age = (dt.datetime.now(dt.timezone.utc)
               - dt.datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return False, "unparseable last_success %r" % last
    if age > STALE_AFTER_S:
        return False, "last read %.0fs ago" % age
    return True, "stale %.0fs, under threshold" % age


def _adapter_hub() -> str | None:
    """The parent hub of the Prolific adapter, e.g. "1-1" for "1-1.4".

    Resolved from sysfs rather than hardcoded: the device node moves
    (ttyUSB0 -> ttyUSB1 was what started this), and so can the port.
    """
    base = "/sys/bus/usb-serial/devices"
    try:
        names = os.listdir(base)
    except OSError:
        names = []
    for name in names:
        try:
            real = os.path.realpath(os.path.join(base, name))
            # .../usb1/1-1/1-1.4/1-1.4:1.0/ttyUSB0 -> want "1-1"
            parts = real.split(os.sep)
            for part in reversed(parts):
                if "-" in part and ":" not in part and part[0].isdigit():
                    return part.split(".")[0]
        except OSError:
            continue
    return None


def _write_sysfs(path: str, value: str) -> bool:
    try:
        cmd = ["sudo", "-n", "tee", path]
        p = subprocess.run(cmd, input=value.encode(), stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, timeout=15)
        if p.returncode == 0:
            return True
        _log("  sysfs write failed (%s): %s" % (path, p.stderr.decode().strip()))
    except Exception as e:                     # noqa: BLE001
        _log("  sysfs write error (%s): %s" % (path, e))
    return False


def reset_bus(hub: str, dry_run: bool) -> bool:
    """Unbind then re-bind the hub. This is the step that worked by hand."""
    drv = "/sys/bus/usb/drivers/usb"
    _log("resetting USB hub %s" % hub)
    if dry_run:
        _log("  (dry run -- nothing written)")
        return False
    ok = _write_sysfs(os.path.join(drv, "unbind"), hub)
    time.sleep(3)
    ok = _write_sysfs(os.path.join(drv, "bind"), hub) and ok
    time.sleep(SETTLE_S)
    return ok


def restart_service(dry_run: bool) -> None:
    """The service holds the old file descriptor and will not recover on its
    own, so it has to be restarted after the node is recreated."""
    if dry_run:
        _log("  (dry run -- service not restarted)")
        return
    try:
        subprocess.run(["sudo", "-n", "systemctl", "restart",
                        "inverter-web.service"], timeout=60, check=False)
    except Exception as e:                     # noqa: BLE001
        _log("  service restart failed: %s" % e)


def check_once(config_path: str, dry_run: bool) -> int:
    notifier = Notifier(load_config(config_path))
    healthy, detail = _service_health(config_path)
    if healthy is None:
        _log("skipping: %s" % detail)
        return 0
    if healthy:
        _log("ok (%s)" % detail)
        # Only speaks up on the transition, so a healthy month is silent.
        notifier.on_change("link", "ok",
                           "Inversor: ligacao restabelecida (%s)." % detail)
        notifier.heartbeat(
            "Inversor: tudo bem. Ligacao ativa, %s." % detail)
        return 0

    _log("link or service looks dead: %s" % detail)
    notifier.on_change("link", "down",
                       "Inversor: SEM LIGACAO ao aparelho (%s). "
                       "A tentar recuperar automaticamente." % detail)

    # Restart first, reset the bus only if that was not enough. A wedged
    # serial thread needs the process replaced, not the bus cycled, and
    # restarting is much the cheaper of the two -- it does not disturb
    # anything else on the hub.
    _log("recovery attempt 1/%d: restarting the service" % MAX_ATTEMPTS)
    restart_service(dry_run)
    if dry_run:
        return 1
    time.sleep(25)
    healthy, detail = _service_health(config_path)
    if healthy:
        _log("recovered by restart (%s)" % detail)
        notifier.on_change("link", "ok",
                           "Inversor: recuperado com um reinicio do servico.")
        return 0
    _log("  still down: %s" % detail)

    hub = _adapter_hub()
    if hub is None:
        _log("  adapter not present in sysfs at all -- cable or adapter, "
             "needs hands")
        notifier.on_change("link", "absent",
                           "Inversor: o adaptador USB desapareceu do sistema. "
                           "Nao da para recuperar por software -- precisa de "
                           "alguem no local (cabo ou adaptador).")
        return 2

    for attempt in range(2, MAX_ATTEMPTS + 1):
        _log("recovery attempt %d/%d: resetting the bus" % (attempt, MAX_ATTEMPTS))
        reset_bus(hub, dry_run)
        restart_service(dry_run)
        time.sleep(25)
        healthy, detail = _service_health(config_path)
        if healthy:
            _log("recovered (%s)" % detail)
            notifier.on_change("link", "ok",
                               "Inversor: recuperado apos reset do USB.")
            return 0
        _log("  still down: %s" % detail)

    _log("gave up after %d attempts -- needs manual intervention" % MAX_ATTEMPTS)
    notifier.on_change("link", "gave_up",
                       "Inversor: %d tentativas de recuperacao falharam. "
                       "Precisa de intervencao manual." % MAX_ATTEMPTS)
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "web.json"))
    ap.add_argument("--interval", type=float, default=300.0,
                    help="seconds between checks in --daemon mode")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.daemon:
        return check_once(args.config, args.dry_run)

    _log("watchdog started (interval %.0fs, stale after %.0fs)"
         % (args.interval, STALE_AFTER_S))
    while True:
        try:
            check_once(args.config, args.dry_run)
        except Exception as e:                 # noqa: BLE001
            # A crash here would leave the inverter unsupervised, which is
            # the exact thing this exists to prevent.
            _log("watchdog error (continuing): %r" % e)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
