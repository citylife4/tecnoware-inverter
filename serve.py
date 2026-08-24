#!/usr/bin/env python3
"""
Run the inverter web interface + REST API.

    python3 serve.py --config web.json

On first run this generates an API token and a session secret and writes
them into the config file, printing the token once so you can copy it.

IMPORTANT: the serial port is exclusive. While this server is running it
owns /dev/ttyUSB0, so charge_schedule.py (or a second copy of this server)
cannot open the port at the same time. Drive scheduling through the API
instead -- see README.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys

# Repo root on sys.path so the existing top-level modules (transport,
# protocol, parsers, commands) import unchanged.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp.app import create_app, generate_token          # noqa: E402
from webapp.atomic_write import write_json_atomic          # noqa: E402
from webapp.grid_charge import GridChargeController         # noqa: E402
from webapp.scheduler import Scheduler                     # noqa: E402
from webapp.service import InverterService                 # noqa: E402

DEFAULTS = {
    "port": "/dev/ttyUSB0",
    "host": "0.0.0.0",
    "http_port": 8080,
    "poll_interval": 10.0,
    # Refuse PCP03 (solar-only) below this pack voltage. Same interlock and
    # same default as charge_schedule.py -- see CLAUDE.md gotcha #2.
    "min_battery_voltage": 24.0,
    "history_size": 720,
    "allow_writes": True,
    "serial_timeout": 3.0,
    # Built-in time-of-day scheduler (replaces running charge_schedule.py
    # as a separate process -- it can't open the port while this does).
    "schedule_config": "web_schedule.json",
    "schedule_poll_interval": 60.0,
    # Grid-export-following charge control (see webapp/grid_charge.py).
    # Mutually exclusive with the scheduler above -- app.py enforces that.
    "gridcharge_config": "web_gridcharge.json",
    # Last-known POP/PCP values, so InverterService.last_known_priority()
    # survives a restart -- see CLAUDE.md gotcha #7.
    "priorities_config": "web_priorities.json",
}


class ConfigError(Exception):
    pass


def load_config(path: str) -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as fh:
            raw = fh.read()
        if raw.strip():
            try:
                cfg.update(json.loads(raw))
            except ValueError as e:
                # Refuse rather than regenerate: silently minting a new token
                # would lock out every client that already has the old one.
                raise ConfigError(
                    f"{path} exists but is not valid JSON ({e}).\n"
                    f"Fix it, or delete it to generate a fresh token.")
        # A zero-length file has no credential to preserve, so fall through
        # and mint one. This happens after an unclean shutdown -- see
        # save_config() for why it should no longer occur.

    # Mint the secrets on first run rather than shipping a default token
    # that everyone would forget to change.
    changed = False
    for key in ("token", "secret_key"):
        if not cfg.get(key):
            cfg[key] = generate_token()
            changed = True
    if changed or not os.path.exists(path):
        save_config(path, cfg)
        print(f"[serve] wrote credentials to {path}")
        print(f"[serve] API token: {cfg['token']}")
    return cfg


def save_config(path: str, cfg: dict) -> None:
    # 0600: web.json holds a bearer token. See webapp/atomic_write.py for
    # why this can't be a plain open/write/close.
    write_json_atomic(path, cfg)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="web.json", help="config file path")
    ap.add_argument("--port", help="serial device (overrides config)")
    ap.add_argument("--host", help="HTTP bind address (overrides config)")
    ap.add_argument("--http-port", type=int, help="HTTP port (overrides config)")
    ap.add_argument("--poll-interval", type=float, help="seconds between polls")
    ap.add_argument("--read-only", action="store_true",
                    help="refuse every set command, whatever the config says")
    ap.add_argument("--show-token", action="store_true",
                    help="print the API token and exit")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"[serve] config error: {e}", file=sys.stderr)
        return 2
    if args.show_token:
        print(cfg["token"])
        return 0

    for key, val in (("port", args.port), ("host", args.host),
                     ("http_port", args.http_port),
                     ("poll_interval", args.poll_interval)):
        if val is not None:
            cfg[key] = val
    if args.read_only:
        cfg["allow_writes"] = False

    service = InverterService(
        port=cfg["port"],
        poll_interval=float(cfg["poll_interval"]),
        history_size=int(cfg["history_size"]),
        min_battery_voltage=cfg.get("min_battery_voltage"),
        allow_writes=bool(cfg["allow_writes"]),
        timeout=float(cfg["serial_timeout"]),
        priorities_path=cfg["priorities_config"],
    )
    service.start()

    scheduler = Scheduler(service, path=cfg["schedule_config"],
                          poll_interval=float(cfg["schedule_poll_interval"]))
    scheduler.start()

    grid_charge = GridChargeController(service, path=cfg["gridcharge_config"])
    grid_charge.start()

    app = create_app(service, scheduler, grid_charge, token=cfg["token"],
                     secret_key=cfg["secret_key"])

    def _shutdown(signum, frame):
        scheduler.stop()
        grid_charge.stop()
        service.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    mode = "READ-ONLY" if not cfg["allow_writes"] else "read/write"
    print(f"[serve] serial {cfg['port']}  |  {mode}  |  "
          f"poll every {cfg['poll_interval']}s")
    print(f"[serve] listening on http://{cfg['host']}:{cfg['http_port']}")
    try:
        app.run(host=cfg["host"], port=int(cfg["http_port"]),
                threaded=True, use_reloader=False)
    finally:
        scheduler.stop()
        grid_charge.stop()
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
