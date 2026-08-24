#!/usr/bin/env python3
"""
Read the daily telemetry CSVs written by webapp/service.py.

Why this exists rather than a plain csv.reader: this installation loses
power without warning (the Pi is fed from the inverter, so anything that
interrupts the inverter power-cycles the logger mid-append). The result is
a run of NUL bytes at the tail, and sometimes a few lines replayed out of
order by the filesystem. Python's csv module raises
`_csv.Error: line contains NUL` and refuses the whole file, which would
throw away thousands of good rows over a handful of corrupt bytes.

Observed on 2026-08-24: 56 NUL bytes at 97.8% through the file, costing
exactly 1 row out of 1578.

    python3 read_telemetry.py                      # summarise today
    python3 read_telemetry.py telemetry/*.csv      # specific files
    python3 read_telemetry.py --column battery_voltage
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import sys

NUMERIC = (
    "battery_voltage", "battery_capacity", "battery_charging_current",
    "battery_discharge_current", "ac_output_active_power",
    "output_load_percent", "grid_voltage", "pv_charging_power",
    "heatsink_temperature",
)


def load(path: str):
    """Rows from one telemetry CSV, skipping anything corrupt.

    Returns (rows, skipped). Numeric columns are converted where possible;
    a field that won't parse is left as the raw string rather than being
    dropped, so a firmware quirk stays visible (this unit really does emit
    things like "06P").
    """
    with open(path, "rb") as fh:
        raw = fh.read()

    # Drop any line containing a NUL rather than the whole file.
    clean, skipped = [], 0
    for line in raw.split(b"\n"):
        if not line:
            continue
        if b"\x00" in line:
            skipped += 1
            continue
        clean.append(line)

    text = io.StringIO(b"\n".join(clean).decode("utf-8", errors="replace"))
    rows = []
    for row in csv.DictReader(text):
        if not row.get("ts"):
            skipped += 1
            continue
        for col in NUMERIC:
            v = row.get(col)
            if v in (None, ""):
                row[col] = None
                continue
            try:
                row[col] = float(v)
            except ValueError:
                pass          # keep the raw string, don't invent a number
        rows.append(row)

    # An unclean shutdown can leave the tail replayed out of order.
    rows.sort(key=lambda r: r["ts"])
    return rows, skipped


def summarise(rows) -> None:
    if not rows:
        print("  (sem linhas)")
        return
    print(f"  {len(rows)} linhas   {rows[0]['ts']}  ->  {rows[-1]['ts']}")
    for col in NUMERIC:
        vals = [r[col] for r in rows if isinstance(r.get(col), float)]
        if not vals:
            continue
        distinct = len(set(vals))
        flat = "  (SEMPRE IGUAL)" if distinct == 1 else ""
        print(f"    {col:28} min={min(vals):>8.1f} max={max(vals):>8.1f} "
              f"media={sum(vals)/len(vals):>8.1f} distintos={distinct}{flat}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="CSV files (default: telemetry/*.csv)")
    ap.add_argument("--column", help="print just this column as ts,value")
    args = ap.parse_args()

    paths = args.files or sorted(glob.glob("telemetry/*.csv"))
    if not paths:
        print("nenhum ficheiro de telemetria encontrado", file=sys.stderr)
        return 1

    for path in paths:
        rows, skipped = load(path)
        if args.column:
            for r in rows:
                print(f"{r['ts']},{r.get(args.column)}")
            continue
        print(f"{path}")
        if skipped:
            print(f"  {skipped} linha(s) corrompida(s) ignorada(s) "
                  f"(corte de energia durante a escrita)")
        summarise(rows)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
