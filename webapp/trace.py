"""
Append-only CSV traces whose column set can change between releases.

The naive version -- write a header when the file does not exist, append
rows forever after -- silently corrupts its own history the first time a
column is added. It happened twice on this project: `below_floor` on
2026-08-25 and `device_mode`/`device_mismatch` on 2026-08-30. Both times a
day's file ended up holding 9-, 11- and 12-field rows under a 9-field
header, so `csv.DictReader` mapped values to the wrong names and any
row-length check dropped exactly the newest rows -- the ones being added to
help diagnose something.

The failure is quiet and lands where it hurts most: after the change you
care about, on the file you added the column for.

So the header is compared on every append, and a file whose header no
longer matches is rolled aside to `<name>.1.csv` (`.2`, ...) rather than
appended to. Each file then holds exactly one schema and reads back with a
plain DictReader. Nothing is deleted.
"""

from __future__ import annotations

import csv
import io
import os


def _existing_header(path: str):
    """First line parsed as CSV, or None if unreadable/empty. Tolerates the
    NUL runs an unclean shutdown leaves behind (see read_telemetry.py)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.readline()
    except OSError:
        return None
    if not raw:
        return None
    text = raw.replace(b"\x00", b"").decode("utf-8", errors="replace")
    if not text.strip():
        return None
    try:
        return next(csv.reader(io.StringIO(text)))
    except (StopIteration, csv.Error):
        return None


def _roll_aside(path: str) -> None:
    base, ext = os.path.splitext(path)
    for n in range(1, 1000):
        candidate = "%s.%d%s" % (base, n, ext)
        if not os.path.exists(candidate):
            os.rename(path, candidate)
            return
    # 1000 rolls in one day means something is rewriting the schema in a
    # loop; appending anyway is still better than losing the row.


def append_row(path: str, columns, values) -> None:
    """Append one row, keeping the file's schema consistent with `columns`.

    Never raises: losing a trace row must not take down the controller that
    is writing it. Directories are created as needed.

    ValueError is caught alongside OSError because a malformed path (an
    embedded NUL, say) raises that instead, and the guarantee here is about
    the caller surviving, not about which layer objected.
    """
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        header = list(columns)
        if os.path.exists(path):
            found = _existing_header(path)
            if found is not None and found != header:
                _roll_aside(path)
        write_header = not os.path.exists(path)

        with open(path, "a", newline="") as fh:
            w = csv.writer(fh)
            if write_header:
                w.writerow(header)
            w.writerow(list(values))
    except (OSError, ValueError):
        pass
