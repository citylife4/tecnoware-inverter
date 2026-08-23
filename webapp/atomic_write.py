"""
Atomic, durable JSON writes for on-disk config/state files.

A plain open/write/close is NOT enough for a file whose loss matters. This
project hit that in practice: serve.py's original web.json writer left a
zero-length file after palacoulo-inverter rebooted uncleanly, losing the API
token. Writing to a temp file, fsyncing it, renaming over the target, then
fsyncing the containing directory means a crash at any point leaves either
the old file intact or the new one complete -- never an empty one.

Shared by serve.py (web.json, holds the API token) and webapp/scheduler.py
(schedule rules) so this durability fix lives in exactly one place.
"""

from __future__ import annotations

import json
import os
import stat

DEFAULT_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600


def write_json_atomic(path: str, data, mode: int = DEFAULT_MODE) -> None:
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    os.replace(tmp, path)
    dir_path = os.path.dirname(os.path.abspath(path)) or "."
    dir_fd = os.open(dir_path, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
