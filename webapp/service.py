"""
Thread-safe owner of the inverter's serial link, plus a background poller.

Why this exists
---------------
The serial port is a single exclusive resource, but a web server is
concurrent by nature: several browser tabs and API clients can ask for
status at the same moment. So exactly one object owns the connection here,
every exchange is serialised behind a lock, and the dashboard reads a
cached snapshot rather than hitting the wire per request.

The retry behaviour is not defensive boilerplate -- each case was actually
observed on this hardware and is documented in CLAUDE.md:
  * the adapter transiently reports "Resource busy" just after another
    process closes it (hence open-with-retry),
  * the 2400-baud link occasionally returns a truncated/corrupt QPIGS
    frame (hence validate-then-retry rather than trusting the first read).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import serial

from parsers import (BATTERY_SETPOINT_SCALE, BATTERY_TYPES,
                     DEVICE_STATUS_BITS, QPIRI_FIELDS, parse_fields)
from transport import InverterConnection, InverterError
from webapp.atomic_write import write_json_atomic

# QPIGS field index -> (json name, converter). Indexes follow parsers.QPIGS_FIELDS;
# this table exists separately because the web layer wants real numbers
# (24.2) rather than the protocol's zero-padded strings ("24.20").
QPIGS_NUMERIC = [
    ("grid_voltage", float),
    ("grid_frequency", float),
    ("ac_output_voltage", float),
    ("ac_output_frequency", float),
    ("ac_output_apparent_power", int),
    ("ac_output_active_power", int),
    ("output_load_percent", int),
    ("bus_voltage", int),
    ("battery_voltage", float),
    ("battery_charging_current", int),
    ("battery_capacity", int),
    ("heatsink_temperature", int),
    ("pv_input_current", int),
    ("pv_input_voltage", float),
    ("battery_voltage_from_scc", float),
    ("battery_discharge_current", int),
    ("device_status_flags", str),
    ("battery_voltage_offset_for_fans", int),
    ("eeprom_version", str),
    ("pv_charging_power", int),
    ("device_status2_flags", str),
]

# A QPIGS frame shorter than this is a truncated read, not a short device
# reply -- charge_schedule.py uses the same threshold for the same reason.
MIN_QPIGS_FIELDS = 14

# If the poller has not managed a single successful read in this long, the
# process is considered stuck and exits so systemd restarts it.
#
# This exists because 2026-09-01 showed the two obvious safety nets both
# miss the case. The serial thread wedged inside a read at 00:34, holding
# its lock: /api/status blocked forever, but the process stayed alive, so
# systemd's Restart=always never fired (it only watches for exit), and
# /api/health kept answering 200, so a liveness probe saw nothing wrong.
# The system was dead for 14.5 hours -- no telemetry, and the 04:30 battery
# window never ran.
#
# Generous on purpose: the link drops frames constantly and the poller
# already backs off to 60 s when erroring, so this must only fire on a link
# that is genuinely gone, never on ordinary noise.
STALL_EXIT_S = 900.0

# QMOD single-letter working modes (PI30).
DEVICE_MODES = {
    "P": "Power on",
    "S": "Standby",
    "L": "Line / grid",
    "B": "Battery",
    "F": "Fault",
    "H": "Power saving",
    "Y": "Bypass",
    "D": "Shutdown",
    "G": "Grid mode",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_qpigs(raw: str) -> dict:
    """Turn a raw QPIGS reply into typed JSON-friendly values."""
    parts = raw.lstrip("(").split(" ")
    if len(parts) < MIN_QPIGS_FIELDS:
        raise InverterError(f"short QPIGS frame ({len(parts)} fields): {raw!r}")

    out = {}
    for i, (name, conv) in enumerate(QPIGS_NUMERIC):
        if i >= len(parts):
            break
        try:
            out[name] = conv(parts[i])
        except ValueError:
            # Keep the raw text rather than dropping the field, so a
            # firmware quirk shows up in the UI instead of silently vanishing.
            out[name] = parts[i]

    flags = out.get("device_status_flags", "")
    if isinstance(flags, str):
        out["status_flags"] = {
            name: (flags[i] == "1" if i < len(flags) else None)
            for i, name in enumerate(DEVICE_STATUS_BITS)
        }
    # Net battery current: positive charging, negative discharging.
    chg = out.get("battery_charging_current")
    dis = out.get("battery_discharge_current")
    if isinstance(chg, int) and isinstance(dis, int):
        out["battery_net_current"] = chg - dis
        # Battery power in watts, same sign convention. The protocol only
        # gives amps, but watts is what you actually want to compare against
        # PV output, house load or the export figure -- and on this
        # installation it is the only charge/discharge number that varies at
        # all (pv_charging_power is a flat 0, the PV input being
        # unconnected). Deliberately NOT added to the telemetry CSV: it is
        # derivable from the current and voltage columns already there, and
        # appending changes the file format for no gain.
        volts = out.get("battery_voltage")
        if isinstance(volts, (int, float)):
            out["battery_power_w"] = round(out["battery_net_current"] * volts)
    return out


def parse_qpiri(raw: str) -> dict:
    """QPIRI with the per-12V-block setpoints scaled to real pack volts.

    QPIRI reports STATIC rated values and never reflects a setting change --
    do not use it to confirm a write (see CLAUDE.md gotcha #1). It is shown
    in the UI as configuration, clearly separated from live status.
    """
    parsed = parse_fields(raw, QPIRI_FIELDS)
    parsed.pop("_extra_fields", None)
    out = {}
    scaled = {"battery_recharge_voltage", "battery_under_voltage",
              "battery_bulk_voltage", "battery_float_voltage"}
    for name, (value, unit) in parsed.items():
        entry = {"value": value, "unit": unit}
        if name in scaled:
            try:
                entry["actual_volts"] = round(
                    float(value) * BATTERY_SETPOINT_SCALE, 2)
            except ValueError:
                pass
        if name == "battery_type":
            entry["label"] = BATTERY_TYPES.get(value, "unknown")
        out[name] = entry
    return out


class InverterService:
    """Owns the serial connection; all access is serialised behind a lock."""

    def __init__(self, port: str, poll_interval: float = 10.0,
                 history_size: int = 720, min_battery_voltage=None,
                 allow_writes: bool = True, timeout: float = 3.0,
                 priorities_path: str | None = None,
                 audit_path: str | None = None,
                 telemetry_dir: str | None = None):
        self.port = port
        self.poll_interval = poll_interval
        self.min_battery_voltage = min_battery_voltage
        self.allow_writes = allow_writes
        self.timeout = timeout

        self._lock = threading.RLock()
        self._conn = None
        self._stop = threading.Event()
        self._thread = None

        self._latest = None
        self._latest_error = None
        self._last_success = None
        self._device_info = None
        self._ratings = None
        self._history = deque(maxlen=history_size)
        self._audit_path = audit_path
        self._audit = deque(self._load_audit(), maxlen=200)
        self._consecutive_failures = 0
        self._last_success_mono = 0.0

        # Which routine set commands are worth remembering "what we last
        # set this to" for -- currently just the two priority codes,
        # since QPIRI can't answer that (gotchas #1/#2) and the in-memory
        # audit log alone doesn't survive a restart. Found this gap live
        # (2026-08-24): a routine service restart wiped the audit log, so
        # grid_charge.py's POP-awareness warning came back "unknown"
        # despite POP genuinely having been set moments earlier -- see
        # CLAUDE.md gotcha #7.
        self._priorities_path = priorities_path
        self._last_known = self._load_last_known()

        # Onde gravar a telemetria em disco. O histórico em memória
        # (self._history) é um deque que se perde em cada reinício -- o que
        # já aconteceu duas vezes a 2026-08-24 e apagou justamente os
        # minutos que interessava analisar. Um CSV por dia, append-only,
        # resolve isso sem depender de base de dados nenhuma.
        self._telemetry_dir = telemetry_dir

    def _load_last_known(self) -> dict:
        if not self._priorities_path or not os.path.exists(self._priorities_path):
            return {}
        try:
            with open(self._priorities_path) as fh:
                raw = fh.read()
            return json.loads(raw) if raw.strip() else {}
        except (ValueError, OSError):
            return {}

    def _save_last_known(self) -> None:
        if self._priorities_path:
            write_json_atomic(self._priorities_path, self._last_known)

    def _load_audit(self) -> list:
        if not self._audit_path or not os.path.exists(self._audit_path):
            return []
        try:
            with open(self._audit_path) as fh:
                value = json.load(fh)
            return value if isinstance(value, list) else []
        except (ValueError, OSError):
            return []

    def _save_audit(self) -> None:
        if not self._audit_path:
            return
        try:
            write_json_atomic(self._audit_path, list(self._audit))
        except (OSError, TypeError):
            # Recording a command must never turn an acknowledged inverter
            # write into an API failure merely because its audit cannot be
            # persisted.
            pass

    # Colunas gravadas no CSV diário. Ordem fixa -- não reordenar, senão os
    # ficheiros antigos deixam de casar com os novos.
    TELEMETRY_COLUMNS = (
        "ts", "mode", "battery_voltage", "battery_capacity",
        "battery_charging_current", "battery_discharge_current",
        "ac_output_active_power", "output_load_percent",
        "grid_voltage", "pv_charging_power", "heatsink_temperature",
    )

    def _append_telemetry(self, snap: dict) -> None:
        """Grava uma linha no CSV do dia. Falhas aqui nunca podem derrubar o
        poller -- perder uma linha de log é muito menos grave do que parar
        de ler o inversor."""
        if not self._telemetry_dir:
            return
        try:
            os.makedirs(self._telemetry_dir, exist_ok=True)
            day = snap["ts"][:10]
            path = os.path.join(self._telemetry_dir, f"telemetry-{day}.csv")
            new = not os.path.exists(path)
            with open(path, "a") as fh:
                if new:
                    fh.write(",".join(self.TELEMETRY_COLUMNS) + "\n")
                fh.write(",".join(
                    "" if snap.get(c) is None else str(snap.get(c))
                    for c in self.TELEMETRY_COLUMNS) + "\n")
        except OSError:
            pass

    # ---- connection handling -------------------------------------------

    def _open(self):
        """Open the port, retrying the transient 'Resource busy' the adapter
        throws right after another process releases it."""
        last = None
        for attempt in range(3):
            try:
                return InverterConnection(self.port, timeout=self.timeout)
            except (serial.SerialException, OSError) as e:
                last = e
                if attempt < 2:
                    time.sleep(1.5)
        raise InverterError(f"could not open {self.port}: {last}")

    def _ensure(self):
        if self._conn is None:
            self._conn = self._open()
        return self._conn

    def _drop(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def close(self):
        with self._lock:
            self._drop()

    # ---- raw exchanges --------------------------------------------------

    def query(self, command: str, attempts: int = 3) -> str:
        """Send a read-only command. Reconnects once if the link has died."""
        with self._lock:
            last = None
            for attempt in range(attempts):
                try:
                    conn = self._ensure()
                    resp = conn.query(command)
                    self._consecutive_failures = 0
                    return resp
                except (InverterError, serial.SerialException, OSError) as e:
                    last = e
                    # A NAK is a real answer from a healthy link -- the
                    # device simply doesn't support that command. Retrying
                    # or reconnecting would just waste seconds.
                    if isinstance(e, InverterError) and "NAK" in str(e):
                        raise
                    self._drop()
                    if attempt < attempts - 1:
                        time.sleep(0.5)
            self._consecutive_failures += 1
            raise InverterError(f"{command} failed: {last}")

    def send_set(self, command: str, source: str = "manual") -> str:
        """Send a state-changing command (CRC appended, longer timeout).

        `source` is purely descriptive -- it lands in the audit log so the
        dashboard can distinguish a command a person sent from one the
        built-in scheduler applied.
        """
        with self._lock:
            try:
                conn = self._ensure()
                resp = conn.send_set_command(command)
            except (serial.SerialException, OSError) as e:
                self._drop()
                raise InverterError(f"{command} failed: {e}")
            self._record_audit(command, resp, source)
            return resp

    # Set commands worth remembering "what we last set this to" for --
    # see _load_last_known()'s docstring note.
    _TRACKED_PRIORITIES = ("POP", "PCP")

    def _record_audit(self, command: str, response: str, source: str = "manual"):
        at = _utcnow()
        ok = response.startswith("(ACK")
        self._audit.appendleft({
            "at": at, "command": command, "response": response,
            "ok": ok, "source": source,
        })
        self._save_audit()
        if ok:
            for prefix in self._TRACKED_PRIORITIES:
                if command.startswith(prefix):
                    self._last_known[prefix] = {
                        "value": command[len(prefix):len(prefix) + 2], "at": at,
                    }
                    self._save_last_known()
                    break

    def audit_log(self) -> list:
        return list(self._audit)

    def last_known_priority(self, prefix: str):
        """The 2-digit value of the most recent successful `prefix` write
        (e.g. "POP", "PCP") this service has seen, from any source -- manual,
        scheduler, or grid-export. None if it's never observed one at all.

        QPIRI can't answer this (static rated values, never reflects a
        setting change -- CLAUDE.md gotcha #1/#2), and the in-memory audit
        log alone doesn't survive a restart -- confirmed live (2026-08-24)
        that a routine restart made this come back "unknown" moments after
        POP had genuinely been set (gotcha #7). Persisted to
        `priorities_path` for exactly that reason.
        """
        entry = self._last_known.get(prefix)
        return entry["value"] if entry else None

    # ---- higher-level reads ---------------------------------------------

    def read_status(self, attempts: int = 3) -> dict:
        """QPIGS + QMOD as one typed snapshot, tolerating truncated frames."""
        last = None
        for _ in range(attempts):
            try:
                snap = parse_qpigs(self.query("QPIGS"))
            except InverterError as e:
                last = e
                time.sleep(0.4)
                continue
            try:
                mode = self.query("QMOD").lstrip("(").strip()
                snap["mode"] = mode
                snap["mode_label"] = DEVICE_MODES.get(mode, mode or "unknown")
            except InverterError:
                snap["mode"] = None
                snap["mode_label"] = "unknown"
            snap["ts"] = _utcnow()
            return snap
        raise InverterError(f"no valid QPIGS after {attempts} tries: {last}")

    def device_info(self, refresh: bool = False) -> dict:
        """Static identification. Cached -- these never change at runtime."""
        if self._device_info is not None and not refresh:
            return self._device_info
        info = {}
        for key, cmd in [("protocol", "QPI"), ("serial_number", "QID"),
                         ("firmware", "QVFW"), ("firmware_secondary", "QVFW2"),
                         ("model", "QMD")]:
            try:
                info[key] = self.query(cmd).lstrip("(").strip()
            except InverterError:
                info[key] = None
        info["port"] = self.port
        self._device_info = info
        return info

    def ratings(self, refresh: bool = False) -> dict:
        if self._ratings is not None and not refresh:
            return self._ratings
        self._ratings = parse_qpiri(self.query("QPIRI"))
        return self._ratings

    # ---- cached snapshot for the UI -------------------------------------

    def latest(self) -> dict:
        return {
            "status": self._latest,
            "error": self._latest_error,
            "last_success": self._last_success,
            "connected": self._latest is not None and self._latest_error is None,
            "poll_interval": self.poll_interval,
            "allow_writes": self.allow_writes,
            "min_battery_voltage": self.min_battery_voltage,
            # {"POP": {"value": "02", "at": ...}, "PCP": {...}} -- what this
            # server last set and had acknowledged, persisted across
            # restarts. Included here (rather than left to /api/audit) so
            # the dashboard can highlight the right priority button on
            # every poll: the audit log is in-memory and empties on
            # restart, which is why the POP button showed nothing while
            # PCP did -- the automation rewrites PCP constantly, so it was
            # always in the recent log, but POP only changes when a person
            # sets it.
            "last_known_priorities": {k: dict(v) for k, v in self._last_known.items()},
        }

    def history(self) -> list:
        return list(self._history)

    def mode(self):
        """The device's own last-polled QMOD letter (see DEVICE_MODES), or
        None if unknown **or not a mode this firmware defines**.

        The validity check is not decoration. This serial link returns
        garbled frames regularly (gotcha #8), and a mode of "BçÉ" is not
        "B" -- so a caller comparing against "B" reads corruption as the
        inverter having changed state. BatteryWindow does exactly that, and
        would end a night's discharge on it. The raw value is still what
        goes into the telemetry CSV, where seeing the garbage is the point;
        it is only decisions that get the filtered view. This is ground truth for what the inverter is
        actually doing -- unlike last_known_priority(), which only records
        what this server last *wrote* and has no way to notice the device
        overriding it on its own (program 12's SBU switch-back, an
        internal low-battery protection, or a genuine grid outage forcing
        automatic transfer to battery regardless of POP). Used by
        battery_window to reconcile its assumption against reality --
        see CLAUDE.md "the dashboard used to silently disagree with the
        device" incident, 2026-08-26."""
        snap = self._latest
        if not snap:
            return None
        mode = snap.get("mode")
        return mode if mode in DEVICE_MODES else None

    def grid_voltage(self):
        """The device's own last-polled AC input voltage, or None.

        Used by battery_window to tell a genuine grid outage (the one case
        where the inverter transferring to battery under POP=00 is correct
        and must not be fought) apart from a POP setting that has drifted
        out of sync with what this server believes. See
        BatteryWindow._reconcile_with_device().
        """
        snap = self._latest
        if not snap:
            return None
        v = snap.get("grid_voltage")
        return v if isinstance(v, (int, float)) else None

    def output_load_w(self):
        """ac_output_active_power from the cached snapshot, or None.

        Only meaningful in battery mode -- in bypass this field floors at
        1 W for every ordinary load in this house. BatteryWindow uses it to
        avoid reading the pack's voltage while the fridge compressor is
        running, since that sags it ~0.4 V at only 46 W.
        """
        snap = self._latest
        if not snap:
            return None
        v = snap.get("ac_output_active_power")
        return v if isinstance(v, (int, float)) else None

    def battery_voltage(self):
        """Best known battery voltage, for the PCP03 safety interlock.

        Falls back to a fresh read when the cache is stale or empty, and
        returns None if it genuinely cannot be determined -- safety.py
        treats None as 'not safe to go solar-only'.
        """
        snap = self._latest
        if snap and self._last_success:
            age = time.monotonic() - self._last_success_mono
            if age < max(self.poll_interval * 3, 30):
                v = snap.get("battery_voltage")
                return v if isinstance(v, (int, float)) else None
        try:
            v = self.read_status().get("battery_voltage")
            return v if isinstance(v, (int, float)) else None
        except InverterError:
            return None

    def refresh(self) -> dict:
        """Force a live read and update the cache."""
        try:
            snap = self.read_status()
        except InverterError as e:
            self._latest_error = str(e)
            raise
        self._latest = snap
        self._latest_error = None
        self._last_success = snap["ts"]
        self._last_success_mono = time.monotonic()
        self._append_telemetry(snap)
        self._history.append({
            "ts": snap["ts"],
            "battery_voltage": snap.get("battery_voltage"),
            "battery_capacity": snap.get("battery_capacity"),
            "pv_charging_power": snap.get("pv_charging_power"),
            "pv_input_voltage": snap.get("pv_input_voltage"),
            "ac_output_active_power": snap.get("ac_output_active_power"),
            "output_load_percent": snap.get("output_load_percent"),
            "grid_voltage": snap.get("grid_voltage"),
            "battery_net_current": snap.get("battery_net_current"),
            "battery_power_w": snap.get("battery_power_w"),
        })
        return snap

    # ---- background poller ----------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._poll_loop,
                                        name="inverter-poll", daemon=True)
        self._thread.start()
        # Separate thread on purpose: it has to keep running when the poll
        # thread is the one that is stuck. See STALL_EXIT_S.
        self._stall_thread = threading.Thread(target=self._stall_loop,
                                              name="inverter-stall",
                                              daemon=True)
        self._stall_thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.close()

    def _stall_loop(self):
        """Exit the process if the poller stops succeeding entirely.

        Deliberately os._exit: a normal exit would wait on the very thread
        that is stuck. Only fires once there has been at least one success,
        so an adapter missing at boot does not become a restart loop --
        that case belongs to usb_watchdog.py, which has attempt limits.
        """
        while not self._stop.wait(60.0):
            if not self._last_success_mono:
                continue
            stalled = time.monotonic() - self._last_success_mono
            if stalled > STALL_EXIT_S:
                sys.stderr.write(
                    "[service] no successful read in %.0fs (limit %.0fs) -- "
                    "exiting so systemd restarts us\n"
                    % (stalled, STALL_EXIT_S))
                sys.stderr.flush()
                os._exit(1)

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except (InverterError, serial.SerialException, OSError) as e:
                self._latest_error = str(e)
            # Back off when the link is down so a missing adapter doesn't
            # spin the log or the CPU.
            delay = self.poll_interval
            if self._latest_error:
                delay = min(self.poll_interval * 4, 60.0)
            self._stop.wait(delay)
