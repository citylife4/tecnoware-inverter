"""
Nightly battery window: drives POP (output source priority) so the pack
actually gets used, instead of sitting at permanent float.

Why this exists at all, given that cycling this pack saves no money:

  * Measured 2026-08-25, solar export was 0.000 kWh over 32 h and the tariff
    is flat, so there is no arbitrage to chase -- see CLAUDE.md.
  * The real reason is **headroom**. A full battery absorbs nothing: when
    export appears, grid_charge writes PCP01, the charger looks at a full
    pack, tapers to a trickle and the surplus goes to the grid anyway.
    Discharging overnight is what gives the charger somewhere to dump the
    next day, and export is a legal problem at this installation, not an
    economic one.
  * Secondary: permanent float encourages sulfation and acid stratification
    in lead-acid. Shallow cycling is proper maintenance.

Depth is the thing that has to be limited. The overnight base load is ~66 W
and the pack holds ~360 Wh usable, so an unbounded 21:00->09:00 window would
flatten it around 02:30, hand the loads back to utility at program 12's
~23 V, recharge, hand them back to battery at program 13, and repeat --
several deep cycles a night, which would kill a 30 Ah lead-acid bank inside
a year. Hence floor_voltage/resume_voltage rather than "battery until
morning".

SAFETY -- three interlocks, all of which force POP back to utility:

  1. The water pump (~1200-1300 W running, far more starting) shares the
     protected output and trips the inverter from battery. HARD_FORBIDDEN
     below is enforced on top of whatever is configured, because a trip
     drops the fridge, the light, the garage door AND the Raspberry Pi that
     runs this server.
  2. floor_voltage, latched until resume_voltage. Note that the usual
     software interlock (apply_low_battery_floor) is USELESS here: it works
     by writing PCP, and PCP is a no-op while POP=02 (gotcha #1). In battery
     mode this class is the only thing protecting the pack.
  3. An unreadable battery voltage is treated as a reason to go to utility,
     never as a reason to stay on battery.

It also yields to grid_charge: the charger only works at POP=00, so
absorbing export and running loads off the battery cannot both happen.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone

from charge_schedule import parse_hhmm, rule_active
from transport import InverterError
from webapp.atomic_write import write_json_atomic

GRID_POP = "00"       # utility first -- loads on grid, charger able to work
BATTERY_POP = "02"    # SBU -- loads on battery

# Applied in addition to the configured `forbidden` list, and deliberately
# not reachable from the API. The pump is a latent fault, not a preference:
# see CLAUDE.md "The pump is a latent fault, not just an inconvenience".
# If the pump is ever moved off the protected output, delete this -- but
# delete it on purpose, not because a config edit made it inconvenient.
HARD_FORBIDDEN = ({"from": "19:00", "to": "21:15", "why": "bomba de água"},)

# A configured floor below this is refused. 24.0 V on a 24 V lead-acid bank
# is already ~50% depth of discharge; anything under it is not a "shallow
# cycle" by any definition and would shorten the pack's life materially.
ABSOLUTE_FLOOR_V = 24.0

# A below-floor reading only counts while the output load is at or under
# this, i.e. with the fridge compressor stopped. The pack sags ~0.4 V at just
# 46 W (measured 2026-08-25), so a sample taken mid-cycle reads well below
# where the pack actually sits, and a floor set just above the inverter's own
# switch-back point would latch on it. Missing a genuine crossing this way is
# the safe direction: program 12 hands the loads back to utility at ~25.4 V
# regardless, so the hardware backstops us.
FLOOR_MAX_LOAD_W = 10

# Above this AC input voltage the grid is considered present. Used only to
# tell two very different situations apart when the device reports battery
# mode while we believe POP=00: a real outage (grid gone -- correct
# behaviour, must not be fought) versus the POP setting having drifted out
# of sync with what this server believes (our problem, and fixable).
GRID_PRESENT_MIN_V = 180.0

# Consecutive ticks of that drift before re-asserting POP. One reading is
# not enough: a brief grid sag too short to show in the 10 s telemetry
# sampling can transfer the inverter to battery momentarily, and writing POP
# at every such blip would be pointless churn on a physical relay. Three
# ticks is ~3 minutes -- against the 8 hours this went uncorrected on
# 2026-08-30, that is still effectively immediate.
POP_DRIFT_CONFIRMATIONS = 3

# How many corrective POP writes to attempt before giving up and just
# reporting. If the device has ignored this many, it is not going to comply
# and the problem needs a person: continuing would hammer a physical relay
# every few minutes for as long as nobody is watching, which during an
# absence could be weeks.
POP_DRIFT_MAX_WRITES = 5

FILE_MODE = 0o600

DEFAULT_CONFIG = {
    "enabled": False,
    # After the pump, before the morning. See HARD_FORBIDDEN.
    "from": "21:15",
    "to": "08:00",
    # ~25% depth of discharge on this pack, measured against a ~66 W load.
    # Tune from battery_test.csv rather than by feel.
    "floor_voltage": 25.5,
    # Must recover to here before battery mode is allowed again, so the
    # controller doesn't sit at the floor toggling the transfer relay.
    "resume_voltage": 26.8,
    # How many consecutive readings must be at or below floor_voltage
    # before the latch closes. One is not enough for two independent
    # reasons: this serial link is documented to return corrupt QPIGS
    # frames (gotcha #8), and the pack sags ~0.4 V while the fridge
    # compressor runs -- measured 2026-08-25 at only 46 W. A single sample
    # catching either would end the night's discharge on a number that was
    # never the pack's real state.
    "floor_confirmations": 3,
    "poll_interval": 60.0,
    # POP throws a physical relay -- audible, and mechanical wear. Much
    # more conservative than the PCP controllers' dwell.
    "min_switch_interval": 600.0,
    "forbidden": [],
}

VALID_KEYS = set(DEFAULT_CONFIG)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _validate_windows(windows, label: str) -> list:
    if not isinstance(windows, list):
        raise ValueError(f"{label} must be a list")
    out = []
    for i, w in enumerate(windows):
        if not isinstance(w, dict):
            raise ValueError(f"{label}[{i}]: must be an object")
        try:
            parse_hhmm(w["from"])
            parse_hhmm(w["to"])
        except (KeyError, ValueError, AttributeError, TypeError):
            raise ValueError(f"{label}[{i}]: from/to must be \"HH:MM\" (24h)")
        out.append({"from": w["from"], "to": w["to"],
                    "why": str(w.get("why", ""))[:200]})
    return out


def validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")
    unknown = set(cfg) - VALID_KEYS
    if unknown:
        raise ValueError(f"unknown keys: {sorted(unknown)}")
    out = dict(DEFAULT_CONFIG)
    out.update(cfg)

    out["enabled"] = bool(out["enabled"])
    for key in ("from", "to"):
        try:
            parse_hhmm(out[key])
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{key} must be \"HH:MM\" (24h), got {out[key]!r}")

    for key in ("floor_voltage", "resume_voltage"):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
    if out["floor_voltage"] < ABSOLUTE_FLOOR_V:
        raise ValueError(
            f"floor_voltage {out['floor_voltage']}V is below the "
            f"{ABSOLUTE_FLOOR_V}V hard limit -- that is a deep discharge, "
            f"not a shallow cycle")
    if out["resume_voltage"] <= out["floor_voltage"]:
        raise ValueError("resume_voltage must be above floor_voltage, "
                         "otherwise the relay would toggle at the floor")

    try:
        out["floor_confirmations"] = int(out["floor_confirmations"])
    except (TypeError, ValueError):
        raise ValueError("floor_confirmations must be an integer")
    if out["floor_confirmations"] < 1:
        raise ValueError("floor_confirmations must be at least 1")

    for key in ("poll_interval", "min_switch_interval"):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number")
        if out[key] < 0:
            raise ValueError(f"{key} must not be negative")
    if out["poll_interval"] <= 0:
        raise ValueError("poll_interval must be positive")
    # min_switch_interval == 0 disables the anti-flap dwell, matching the
    # convention scheduler.py and grid_charge.py already use in tests.

    out["forbidden"] = _validate_windows(out["forbidden"], "forbidden")
    return out


def _in_any(now_t, windows) -> dict:
    """Return the first window containing `now_t`, or None."""
    for w in windows:
        if rule_active(now_t, parse_hhmm(w["from"]), parse_hhmm(w["to"])):
            return w
    return None


class BatteryWindow:
    """Owns POP scheduling. Writes go through InverterService, sharing its
    lock, retries and audit log with every other write source."""

    def __init__(self, service, path: str, override_check=None,
                 trace_dir=None):
        self.service = service
        self.path = path
        # One CSV per day of what was decided and why. The telemetry log
        # already shows the voltage curve and the mode; this says which
        # interlock produced it, which is the part that is otherwise
        # invisible after the fact.
        self.trace_dir = trace_dir
        # Callable returning True while grid_charge is absorbing export.
        # The charger only works at POP=00, so battery mode has to yield.
        self._override_check = override_check

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

        self._config = self._load()
        self._last_applied_pop = self.service.last_known_priority("POP")
        self._last_switch_mono = 0.0
        runtime = self._load_runtime()
        self._recovering = runtime["recovering"]
        self._below_floor = runtime["below_floor"]
        self._pop_drift = 0           # consecutive ticks of POP disagreement
        self._pop_drift_writes = 0    # corrective writes already attempted
        self._last_run = None

    # ---- persistence ----------------------------------------------------

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return dict(DEFAULT_CONFIG)
        try:
            with open(self.path) as fh:
                raw = fh.read()
            if not raw.strip():
                return dict(DEFAULT_CONFIG)
            return validate_config(json.loads(raw))
        except (ValueError, OSError):
            # Never let stored automation config crash the server. Starting
            # disabled is the safe direction: no POP writes at all.
            return dict(DEFAULT_CONFIG)

    def _save(self) -> None:
        write_json_atomic(self.path, self._config, mode=FILE_MODE)

    @property
    def _runtime_path(self) -> str:
        return self.path + ".state"

    def _load_runtime(self) -> dict:
        if not os.path.exists(self._runtime_path):
            return {"recovering": False, "below_floor": 0}
        try:
            with open(self._runtime_path) as fh:
                value = json.load(fh)
            return {
                "recovering": bool(value.get("recovering", False)),
                "below_floor": max(0, int(value.get("below_floor", 0))),
            }
        except (ValueError, OSError, TypeError, AttributeError):
            # A corrupt safety latch fails towards utility. It can be cleared
            # by disabling the window or after a normal recovery outside it.
            return {"recovering": True,
                    "below_floor": self._config["floor_confirmations"]}

    def _save_runtime(self) -> None:
        try:
            write_json_atomic(self._runtime_path, {
                "recovering": self._recovering,
                "below_floor": self._below_floor,
            }, mode=FILE_MODE)
        except OSError:
            pass

    # ---- API ------------------------------------------------------------

    def get_state(self) -> dict:
        with self._lock:
            target, reason, detail = self._decide()
            return {
                "config": dict(self._config),
                "current_target": target,
                "reason": reason,
                "detail": detail,
                "recovering": self._recovering,
                "below_floor_readings": self._below_floor,
                "battery_voltage": self.service.battery_voltage(),
                # The device's own live QMOD letter, alongside our belief
                # above -- a pure read, unlike _reconcile_with_device()
                # (which only runs from a real tick), so the dashboard can
                # show both sides even between polls.
                "device_mode": self.service.mode(),
                "hard_forbidden": [dict(w) for w in HARD_FORBIDDEN],
                "absolute_floor_v": ABSOLUTE_FLOOR_V,
                "last_run": self._last_run,
            }

    def set_config(self, updates: dict, now=None) -> dict:
        """Apply immediately rather than making an edit wait out
        poll_interval. `now` overrides the clock for that first tick;
        production never passes it, tests do so the force-tick doesn't land
        wherever the wall clock happens to be when the suite runs."""
        with self._lock:
            merged = dict(self._config)
            merged.update(updates or {})
            self._config = validate_config(merged)
            self._save()
            self._tick(force=True, now=now)
        return self.get_state()

    def is_active(self) -> bool:
        """True while battery mode is the intended state -- used so the PCP
        controllers can tell that charging is pointless right now."""
        with self._lock:
            return self._last_applied_pop == BATTERY_POP

    # ---- decision --------------------------------------------------------

    def _overridden(self) -> bool:
        if self._override_check is None:
            return False
        try:
            return bool(self._override_check())
        except Exception:
            # A failure asking "are you absorbing export?" must not decide
            # anything on its own; assume not, and let the other interlocks
            # do their job.
            return False

    def _reconcile_with_device(self) -> str | None:
        """Compare what we last applied against the device's own reported
        mode (QMOD), which is always ground truth. Returns a short reason
        string describing a mismatch, or None if there is none (including
        when the mode can't be read -- say nothing rather than guess).

        The device can leave battery mode on its own: program 12's SBU
        switch-back, an internal low-battery protection, a write that
        silently didn't take on this occasionally-flaky serial link. This
        class used to have no way to notice. Observed live 2026-08-26: the
        inverter switched itself from B to L at 05:46, mid-compressor-run,
        which is exactly the load level FLOOR_MAX_LOAD_W excludes from the
        floor count -- so the software never decided anything, and this
        class kept reporting "already POP02; nothing to do" for the next
        two hours while the pack had actually been back on utility the
        whole time.
        """
        mode = self.service.mode()
        if mode is None:
            return None

        if self._last_applied_pop == BATTERY_POP and mode != "B":
            # We believe we're discharging the pack; the device disagrees.
            # Treat tonight's discharge as already over -- that matches
            # physical reality -- and forget the stale assumption so the
            # next decision issues a real write (converging POP explicitly
            # with the device) instead of a silent no-op.
            self._recovering = True
            self._last_applied_pop = None
            self._save_runtime()
            return "hardware_override"

        if self._last_applied_pop == GRID_POP and mode == "B":
            # We believe loads are on the grid; the device is on battery
            # anyway. Two very different causes, and the AC input voltage
            # separates them cleanly.
            grid = self.service.grid_voltage()
            if grid is not None and grid >= GRID_PRESENT_MIN_V:
                # The grid is up, so this is NOT an outage: the device's POP
                # is simply not what we think it is. Nothing else notices,
                # because the normal write path compares against
                # _last_applied_pop and concludes "already POP00; nothing to
                # do" -- so the disagreement can persist indefinitely.
                #
                # It did, on 2026-08-30: this fired 483 times across ~8 hours
                # while the inverter free-ran in SBU, cycling the pack 13
                # times and pulling it to 20.7 V under a 1.3 kW load -- below
                # the datasheet's own 1.75 V/cell discharge limit. It was
                # only resolved by accident, when a service restart re-seeded
                # _last_applied_pop and produced a real write. Clearing the
                # cached value here is what forces that write to happen on
                # purpose.
                self._pop_drift += 1
                if self._pop_drift < POP_DRIFT_CONFIRMATIONS:
                    return "unexpected_battery"
                if self._pop_drift_writes >= POP_DRIFT_MAX_WRITES:
                    # Tried and the device kept ignoring it. Stop writing and
                    # say so, rather than keep throwing the relay unattended.
                    return "pop_drift_stuck"
                # Counter resets here so the next attempt is another
                # POP_DRIFT_CONFIRMATIONS away, not on the very next tick.
                self._pop_drift = 0
                self._pop_drift_writes += 1
                self._last_applied_pop = None
                return "pop_drift"

            # Grid absent or out of range: a real outage. Utility-first
            # transfers to battery by design when the grid fails, which is
            # exactly what should happen. Forcing POP00 would achieve
            # nothing while there is no grid to fall back to, and the device
            # reports "L" again on its own once it returns.
            self._pop_drift = 0
            return "unexpected_battery"

        self._pop_drift = 0
        self._pop_drift_writes = 0
        return None

    def _decide(self, now=None):
        """Return (target_pop, reason, detail). Every path that is not a
        clean 'battery window is open' returns GRID_POP."""
        cfg = self._config
        now = now or datetime.now()
        t = now.time()

        if not cfg["enabled"]:
            return None, "disabled", "janela de bateria desligada"

        # Window membership is checked FIRST, before every other reason.
        # Outside it the answer is GRID_POP whatever else is true, and
        # reporting some other cause is actively misleading -- the trace
        # showed 33 ticks of "yielding" during an afternoon when the window
        # was shut and nothing was being yielded. Everything below this line
        # therefore only applies when the window is genuinely open.
        if not rule_active(t, parse_hhmm(cfg["from"]), parse_hhmm(cfg["to"])):
            return GRID_POP, "outside", (
                f"fora da janela {cfg['from']}-{cfg['to']}")

        blocked = _in_any(t, HARD_FORBIDDEN)
        if blocked is not None:
            return GRID_POP, "forbidden", (
                f"janela proibida {blocked['from']}-{blocked['to']}"
                f" ({blocked['why']}) — a bateria faria disparar o inversor")
        blocked = _in_any(t, cfg["forbidden"])
        if blocked is not None:
            return GRID_POP, "forbidden", (
                f"janela proibida {blocked['from']}-{blocked['to']}"
                f" ({blocked['why']})")

        if self._overridden():
            return GRID_POP, "yielding", (
                "carregamento por excedente a absorver — o carregador só "
                "funciona com POP=00")

        v = self.service.battery_voltage()
        if v is None:
            return GRID_POP, "unknown_voltage", (
                "tensão da bateria ilegível — em POP=02 nada mais protege "
                "o pack, por isso volta-se à rede")

        # Checked before the latch so the transition reports the more
        # specific reason: "floor" while the pack is actually under it,
        # "recovering" once it has come back up but the window stays shut.
        if self._below_floor >= cfg["floor_confirmations"]:
            return GRID_POP, "floor", (
                f"piso atingido: {v:.2f}V <= {cfg['floor_voltage']:.2f}V "
                f"em {self._below_floor} leituras seguidas")
        if self._recovering:
            # Latched for the rest of this window, deliberately: releasing on
            # voltage alone would discharge to the floor, recharge to
            # resume_voltage and discharge again -- several cycles a night,
            # which is the exact wear this controller exists to avoid. The
            # latch clears in _tick() once the window has closed.
            return GRID_POP, "recovering", (
                f"já descarregou nesta janela ({v:.2f}V) — "
                f"só volta à bateria na próxima")

        return BATTERY_POP, "window", (
            f"janela {cfg['from']}-{cfg['to']}, bateria {v:.2f}V")

    # ---- tick -------------------------------------------------------------

    def tick(self, force: bool = False, now=None) -> dict:
        with self._lock:
            return self._tick(force=force, now=now)

    def _tick(self, force: bool = False, now=None) -> dict:
        import time as _time
        cfg = self._config
        # Count and latch BEFORE deciding, so a tick acts on the reading it
        # just took rather than the previous one -- otherwise the floor
        # always fires one poll late. Kept out of _decide() so that
        # get_state() stays a pure read with no side effects.
        mismatch = self._reconcile_with_device()

        v = self.service.battery_voltage()
        previous_runtime = (self._recovering, self._below_floor)
        if isinstance(v, (int, float)):
            when = (now or datetime.now()).time()
            in_window = rule_active(when, parse_hhmm(cfg["from"]),
                                    parse_hhmm(cfg["to"]))
            # Only count while the window is open. Outside it the pack is
            # on the charger and its terminal voltage says nothing about
            # state of charge -- counting there would latch the window shut
            # before it ever opened. Seen live: the pack read 25.4 V in
            # bypass at 14:30, which a 25.6 V floor would have counted.
            load = self.service.output_load_w()
            quiet = load is None or load <= FLOOR_MAX_LOAD_W
            if in_window and quiet and v <= cfg["floor_voltage"]:
                self._below_floor += 1
            elif not in_window or (quiet and v > cfg["floor_voltage"]):
                self._below_floor = 0
            if (not self._recovering
                    and self._below_floor >= cfg["floor_confirmations"]):
                self._recovering = True
            elif (self._recovering and not in_window
                    and v >= cfg["resume_voltage"]):
                # Window closed and the pack is back up: arm for tonight.
                self._recovering = False
                self._below_floor = 0
        if previous_runtime != (self._recovering, self._below_floor):
            self._save_runtime()

        target, reason, detail = self._decide(now=now)
        if mismatch:
            reason = mismatch
            details = {
                "hardware_override":
                    "o inversor saiu do modo bateria por conta própria",
                "pop_drift":
                    "o inversor está em modo bateria com a rede presente "
                    "-- o POP não é o que julgávamos; a reescrever",
                "pop_drift_stuck":
                    "o inversor ignorou %d tentativas de repor o POP "
                    "-- precisa de intervenção manual" % POP_DRIFT_MAX_WRITES,
                "unexpected_battery":
                    "o inversor está em modo bateria sem termos pedido "
                    "-- possível falha de rede",
            }
            detail = details[mismatch] + f" (QMOD={self.service.mode()})"

        result = {"at": _utcnow(), "target": target, "reason": reason,
                  "detail": detail, "battery_voltage": v,
                  "applied": False, "note": "",
                  "device_mismatch": mismatch}

        now_mono = _time.monotonic()
        dwell_ok = (now_mono - self._last_switch_mono) >= cfg["min_switch_interval"]
        # Going back to utility is a safety action; it never waits out the
        # anti-flap timer. Only ENTERING battery mode is debounced -- that is
        # the direction that costs a relay throw for no safety benefit.
        #
        # "yielding" belongs on this list, and its absence was a live fault.
        # Observed 2026-08-25 14:2x: the house was exporting (-19 W), the
        # export controller had correctly decided to charge, and this
        # controller had correctly decided to hand it POP=00 -- and then sat
        # on that decision for the full 600 s dwell while the export
        # continued. Injecting into the grid is not permitted at this
        # installation, so absorbing it is a compliance action, not a
        # preference, and the relay-wear argument does not outrank it.
        urgent = target == GRID_POP and reason in (
            "floor", "recovering", "unknown_voltage", "forbidden", "yielding",
            # The device is already physically on the safe side (L) by the
            # time this fires; the write only converges our own belief with
            # it. No reason to make that wait out a relay-wear cooldown.
            "hardware_override",
            # Re-asserting POP after it drifted out of sync is corrective,
            # not discretionary: every tick spent waiting is a tick the pack
            # is being cycled by the device instead of by us.
            "pop_drift")

        if target is None:
            result["note"] = "desligado"
        elif not self.service.allow_writes:
            result["note"] = "servidor em leitura apenas; não aplicado"
        elif target == self._last_applied_pop and not force:
            result["note"] = f"já em POP{target}; nada a fazer"
            result["applied"] = True
        elif not force and not urgent and not dwell_ok:
            remaining = round(cfg["min_switch_interval"] - (now_mono - self._last_switch_mono), 1)
            result["note"] = f"a aguardar {remaining}s (anti-flap do relé)"
        else:
            try:
                resp = self.service.send_set(f"POP{target}", source="battery_window")
                ok = resp.startswith("(ACK")
                result["response"] = resp
                result["applied"] = ok
                result["note"] = "aplicado" if ok else f"sem ACK: {resp}"
                if ok:
                    self._last_applied_pop = target
                    self._last_switch_mono = now_mono
            except InverterError as e:
                result["note"] = f"erro: {e}"

        self._last_run = result
        self._append_trace(result)
        return result

    TRACE_COLUMNS = ("ts", "target", "reason", "battery_voltage",
                     "device_mode", "device_mismatch", "below_floor",
                     "recovering", "applied", "note", "detail")
    _TRACE_KEYS = ("at", "target", "reason", "battery_voltage",
                   "device_mode", "device_mismatch", "below_floor",
                   "recovering", "applied", "note", "detail")

    def _append_trace(self, result) -> None:
        if not self.trace_dir:
            return
        try:
            os.makedirs(self.trace_dir, exist_ok=True)
            path = os.path.join(self.trace_dir,
                                "batterywindow-%s.csv" % result["at"][:10])
            new = not os.path.exists(path)
            row = dict(result, recovering=self._recovering,
                       below_floor=self._below_floor,
                       device_mode=self.service.mode())
            with open(path, "a", newline="") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(self.TRACE_COLUMNS)
                w.writerow([row.get(k) for k in self._TRACE_KEYS])
        except OSError:
            # Losing the trace must never take down the controller that is
            # the pack's only guard while the window is open.
            pass

    # ---- lifecycle --------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop,
                                        name="inverter-battery-window",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self):
        try:
            self.tick()
        except Exception:
            pass
        while not self._stop.wait(self._config["poll_interval"]):
            try:
                self.tick()
            except Exception:
                pass
