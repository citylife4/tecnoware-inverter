#!/usr/bin/env python3
"""
Daily Telegram summary of what the installation actually did.

Why this exists separately from notify.py: that file alerts on *state
changes* -- faults appearing and clearing -- and deliberately says nothing
while everything works. Which is right for alerting and useless for the
question actually being asked here, which is "did yesterday's config change
reduce export?". That needs the ordinary numbers, every day, whether or not
anything went wrong.

It is also independent of any Claude session. Scheduled checks that live
inside a session vanish when the session does; this runs from a systemd
timer on the Pi and keeps working through reboots and absences, which is the
whole point given how often this installation is unattended.

    python3 daily_report.py                 send today's summary
    python3 daily_report.py --date 2026-09-03
    python3 daily_report.py --stdout        print it, send nothing

Reads the same CSVs everything else does, through read_telemetry.load() so
that corrupt lines are skipped rather than crashing the report (gotcha #8 --
this link really does emit NUL blocks and truncated frames).
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import os
import sys

import notify
import read_telemetry

HERE = os.path.dirname(os.path.abspath(__file__))
TELEMETRY_DIR = os.path.join(HERE, "telemetry")

# The CSVs are written with UTC timestamps; everything a person reads should
# be local. Computed rather than hardcoded so this does not silently drift an
# hour every March and October.
def _to_local(ts: str):
    try:
        parsed = dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone().replace(tzinfo=None)


def _read_csv(path: str):
    """Plain CSV rows, skipping corrupt lines. Used for the two trace files,
    which read_telemetry does not know the schema of."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as fh:
        raw = fh.read()
    clean = [ln for ln in raw.split(b"\n") if ln and b"\x00" not in ln]
    text = io.StringIO(b"\n".join(clean).decode("utf-8", errors="replace"))
    return [r for r in csv.DictReader(text) if r.get("ts")]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def export_wh(rows):
    """Energy exported today, integrated over the gridcharge trace.

    Gaps longer than 5 minutes are skipped rather than interpolated across:
    the service has been down for 14.5 h before now, and trapezoid-filling
    that would invent a number rather than admit a hole.
    """
    pts = []
    for r in rows:
        ts, net = _to_local(r["ts"]), _num(r.get("net_balance_w"))
        if ts and net is not None:
            pts.append((ts, net))
    pts.sort()
    total = 0.0
    for i in range(1, len(pts)):
        gap = (pts[i][0] - pts[i - 1][0]).total_seconds()
        if gap > 300:
            continue
        mean = (pts[i][1] + pts[i - 1][1]) / 2
        if mean < 0:
            total += -mean * gap / 3600
    negative = [p for p in pts if p[1] < 0]
    worst = min((p[1] for p in negative), default=0.0)
    return total, len(negative), len(pts), worst


def battery_spans(rows):
    """(start, end, hours, v_start, v_min, mean_load_w, wh) per battery-mode
    span. Voltage and ac_output_active_power are the only trustworthy pair
    here -- battery_discharge_current reads 0.0 A while the pack is visibly
    supplying load (measured 2026-08-25, confirmed at scale 2026-09-02)."""
    bat = []
    for r in rows:
        if r.get("mode") != "B":
            continue
        ts, v = _to_local(r["ts"]), _num(r.get("battery_voltage"))
        load = _num(r.get("ac_output_active_power"))
        if ts and v is not None and 20 < v < 30:
            bat.append((ts, v, load))
    spans, current = [], None
    for row in bat:
        if current and (row[0] - current[-1][0]).total_seconds() > 300:
            spans.append(current)
            current = None
        if current is None:
            current = [row]
        else:
            current.append(row)
    if current:
        spans.append(current)

    out = []
    for span in spans:
        hours = (span[-1][0] - span[0][0]).total_seconds() / 3600
        loads = [load for _, _, load in span if load is not None]
        mean = sum(loads) / len(loads) if loads else 0.0
        out.append({
            "start": span[0][0], "end": span[-1][0], "hours": hours,
            "v_start": span[0][1], "v_min": min(v for _, v, _ in span),
            "mean_w": mean, "wh": mean * hours,
        })
    return out


def charge_stopped_at(rows):
    """When charging current last fell to zero and stayed there.

    The number the extended-window experiment turns on: if the pack is full
    before export starts, extra overnight headroom is being spent on morning
    grid import rather than on midday surplus."""
    last_on = None
    for r in rows:
        amps = _num(r.get("battery_charging_current"))
        ts = _to_local(r["ts"])
        # >100 A is a corrupt frame, not a reading.
        if ts and amps is not None and 0 < amps < 100:
            last_on = ts
    return last_on


def build(date: str) -> str:
    tel = os.path.join(TELEMETRY_DIR, f"telemetry-{date}.csv")
    gcp = os.path.join(TELEMETRY_DIR, f"gridcharge-{date}.csv")
    bwp = os.path.join(TELEMETRY_DIR, f"batterywindow-{date}.csv")

    lines = [f"Resumo {date}"]

    if os.path.exists(gcp):
        wh, neg, total, worst = export_wh(_read_csv(gcp))
        pct = (100 * neg / total) if total else 0
        lines.append(f"Exportado: {wh:.0f} Wh ({neg}/{total} amostras, {pct:.0f}%, "
                     f"pior {worst:.0f} W)")
    else:
        lines.append("Exportado: sem dados")

    if os.path.exists(tel):
        rows, skipped = read_telemetry.load(tel)
        spans = battery_spans(rows)
        if spans:
            delivered = sum(s["wh"] for s in spans)
            lines.append(f"Bateria: {delivered:.0f} Wh em {len(spans)} janela(s)")
            for s in spans:
                lines.append(f"  {s['start']:%H:%M}-{s['end']:%H:%M} "
                             f"({s['hours']:.1f}h) {s['v_start']:.1f}->{s['v_min']:.1f}V "
                             f"{s['wh']:.0f}Wh")
        else:
            lines.append("Bateria: nao foi usada hoje")
        stopped = charge_stopped_at(rows)
        if stopped:
            lines.append(f"Carga parou as {stopped:%H:%M}")
        if skipped:
            lines.append(f"(linhas corrompidas ignoradas: {skipped})")
    else:
        lines.append("Bateria: sem dados")

    problems = []
    if os.path.exists(bwp):
        bw = _read_csv(bwp)
        reasons = collections.Counter(r.get("reason") for r in bw)
        overrides = reasons.get("hardware_override", 0)
        if overrides:
            problems.append(f"hardware_override x{overrides}")
        if reasons.get("pop_drift_stuck"):
            problems.append(f"pop_drift_stuck x{reasons['pop_drift_stuck']}")
        failed = sum(1 for r in bw if "sem ACK" in (r.get("note") or "")
                     or (r.get("note") or "").startswith("erro"))
        if failed:
            problems.append(f"escritas POP falhadas x{failed}")
        if reasons.get("daytime"):
            lines.append(f"Janela diurna abriu ({reasons['daytime']} ticks)")
    if os.path.exists(gcp):
        gc = _read_csv(gcp)
        failed = sum(1 for r in gc if "error" in (r.get("note") or "")
                     or "not acknowledge" in (r.get("note") or ""))
        if failed:
            problems.append(f"escritas PCP falhadas x{failed}")

    lines.append("Problemas: " + ("; ".join(problems) if problems else "nenhum"))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "web.json"))
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD; defaults to today (local)")
    ap.add_argument("--stdout", action="store_true",
                    help="print the report instead of sending it")
    args = ap.parse_args()

    date = args.date or dt.date.today().isoformat()
    text = build(date)

    if args.stdout:
        print(text)
        return 0

    notifier = notify.Notifier(notify.load_config(args.config))
    if not notifier.enabled:
        print("telegram not configured; nothing sent", file=sys.stderr)
        print(text)
        return 1
    # send(), not on_change(): this is a scheduled report, not an alert, and
    # deduplicating it against yesterday's identical-looking numbers would
    # defeat the point -- silence has to mean the timer stopped.
    return 0 if notifier.send(text) else 1


if __name__ == "__main__":
    raise SystemExit(main())
