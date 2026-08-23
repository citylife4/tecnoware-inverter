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
import stat
import sys

# Repo root on sys.path so the existing top-level modules (transport,
# protocol, parsers, commands) import unchanged.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp.app import create_app, generate_token          # noqa: E402
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
    """Write the config atomically and durably.

    A plain open/write/close is NOT enough here. This actually bit us: the
    Pi rebooted uncleanly and ext4 replayed the metadata but not the data,
    leaving a zero-length web.json and losing the API token. Writing to a
    temp file, fsyncing it, then renaming means a crash leaves either the
    old file or the new one -- never an empty one.
    """
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)   # 0600: it holds a bearer token
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.replace(tmp, path)
    # Also fsync the directory, so the rename itself survives a power cut.
    dir_fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


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
    )
    service.start()

    app = create_app(service, token=cfg["token"], secret_key=cfg["secret_key"])

    def _shutdown(signum, frame):
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
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
