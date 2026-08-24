"""
Grid-export-following charge control.

Turns the charger source priority (PCP) on and off based on whether the
house is currently exporting surplus solar to the grid, using the existing
`auto-energy` energy-monitoring dashboard (a separate project on this same
Raspberry Pi network, http://<host>:8000) as the source of truth for that.

Why this exists, and why it's not just another time-of-day schedule rule:
this Tecnoware unit's own PV/DC input reads 0V and 0W in every QPIGS sample
taken this session, including at midday with the dashboard showing real
solar production -- the panels are on a separate, AC-coupled system that
feeds the house wiring directly, not this inverter's DC input. So there is
no signal the inverter itself can see that means "the sun is out"; PCP03
("solar only") on this hardware doesn't select a solar source, it just
turns charging off entirely, regardless of time of day. The auto-energy
dashboard's grid meter (a Shelly EM on the house's grid connection) is the
only thing that actually knows whether solar production currently exceeds
house load -- that's `net_balance` in its /api/live response: positive
means the house is buying from the grid, negative means it's selling
surplus back. See auto-energy/src/shelly_service.py for where that number
comes from.

The idea: enable charging (draw from "utility", which is really the shared
AC bus solar is already feeding) only while there's a surplus being
exported anyway -- so the battery soaks up power that would otherwise be
sold to the grid at the feed-in tariff, instead of buying it back later at
the retail rate. Stop charging once the house goes back to importing, so
this never causes the inverter to pull additional grid power beyond what
was already being exported.

Runs the exact same low-battery floor as webapp/scheduler.py
(apply_low_battery_floor in safety.py) -- a battery that's actually low
still gets utility charging regardless of grid export state.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import requests

from transport import InverterError
from webapp.atomic_write import write_json_atomic
from webapp.safety import PCP_VALUES, apply_low_battery_floor

# Como é que este controlador se relaciona com o agendamento horário:
#
#   "exclusive" -- comportamento original: controla o PCP sozinho (carrega
#       quando há excedente, não carrega caso contrário). Mutuamente
#       exclusivo com o agendamento, porque os dois escreveriam PCP.
#
#   "override"  -- só atua QUANDO há exportação: nessa altura força o
#       carregamento para consumir o excedente. Sem excedente, não escreve
#       nada e deixa a decisão ao agendamento. Serve o caso em que exportar
#       para a rede não é permitido e é preciso acrescentar carga à casa
#       sempre que isso acontece, mantendo na mesma um horário de
#       carregamento normal por baixo.
MODES = ("exclusive", "override")

# A única prioridade de saída em que o carregador comprovadamente funciona
# nesta instalação -- ver _pop_warning() para a medição. POP=02 foi testado
# e NÃO carrega; POP=01 não foi testado.
CHARGING_POP = "00"

DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "exclusive",
    # The auto-energy dashboard's live-telemetry endpoint. Runs on
    # palacoulo-rasp in this deployment; adjust if that ever moves.
    "source_url": "http://192.168.188.11:8000/api/live",
    "poll_interval": 30.0,       # matches the dashboard's own refresh cadence
    "http_timeout": 5.0,
    # Hysteresis band, in watts of net_balance (negative = exporting).
    # Must export past this to start charging...
    #
    # -50/+20 (a 70W band) was the original guess and it was wrong: live on
    # this house, net_balance swings by hundreds of watts routinely (10-min
    # averages from 80W up to 1400W import, one instantaneous spike over
    # 2500W -- ordinary appliances cycling, not a fault) with battery
    # charging current staying at 0A the whole time (the pack was already
    # above its recharge threshold), so none of that flapping even charged
    # anything. It re-triggered every ~120s for 5+ hours before anyone
    # noticed. 150/150 (a 300W band) is sized off that real data.
    "export_threshold_w": -150.0,
    # ...and come back up to this before stopping. The gap between the two
    # absorbs normal household load noise without flapping.
    "import_threshold_w": 150.0,
    # Don't flip state more than once per this many seconds, even if the
    # hysteresis says to -- PCP writes take up to ~10s on this hardware and
    # rapid toggling serves no purpose. The low-battery safety override
    # bypasses this deliberately (see _tick). Widened from 120s alongside
    # the thresholds above, as a second line of defense against the same
    # flapping incident.
    "min_switch_interval": 300.0,
    # If no successful read in this long, treat the export state as
    # unknown rather than trusting a stale number.
    "stale_after": 120.0,
    "charge_pcp": "01",   # applied while exporting
    "idle_pcp": "03",     # applied otherwise (subject to the low-battery floor)
}

VALID_KEYS = set(DEFAULT_CONFIG)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")
    out = dict(DEFAULT_CONFIG)
    unknown = set(cfg) - VALID_KEYS
    if unknown:
        raise ValueError(f"unknown config key(s): {sorted(unknown)}")
    out.update(cfg)
    out["enabled"] = bool(out["enabled"])

    if out["mode"] not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}, got {out['mode']!r}")

    if not isinstance(out["source_url"], str) or not out["source_url"].startswith(
            ("http://", "https://")):
        raise ValueError("source_url must be an http(s):// URL")

    for key in ("poll_interval", "http_timeout", "export_threshold_w",
               "import_threshold_w", "min_switch_interval", "stale_after"):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number, got {cfg.get(key)!r}")

    if out["poll_interval"] < 5:
        raise ValueError("poll_interval must be at least 5 seconds")
    if out["export_threshold_w"] >= out["import_threshold_w"]:
        raise ValueError("export_threshold_w must be lower (more negative) "
                         "than import_threshold_w -- they define a hysteresis band")

    for key in ("charge_pcp", "idle_pcp"):
        if out[key] not in PCP_VALUES:
            raise ValueError(f"{key} must be one of {sorted(PCP_VALUES)}, got {out[key]!r}")

    return out


class GridChargeController:
    """Owns the grid-export-following config and a background polling
    thread. Writes go through InverterService, sharing its lock, retry
    behaviour, and audit log with every other write source."""

    def __init__(self, service, path: str, fetch_fn=None):
        self.service = service
        self.path = path
        # Defaults to the real HTTP call; tests inject a fake so they don't
        # need network access or a running auto-energy instance.
        self._fetch_fn = fetch_fn or self._http_fetch

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

        self._config = self._load()
        self._desired = None            # "charging" | "idle" | None (unknown, startup)
        self._last_applied_pcp = None
        self._last_switch_mono = 0.0
        self._last_fetch_ok_mono = None
        self._last_net_balance = None
        self._last_remote_ts = None
        self._last_run = None

    # ---- persistence ------------------------------------------------------

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
            return dict(DEFAULT_CONFIG)

    def _save(self) -> None:
        write_json_atomic(self.path, self._config)

    # ---- read/write API (called from webapp/app.py) -----------------------

    def get_state(self) -> dict:
        with self._lock:
            age = (None if self._last_fetch_ok_mono is None
                   else round(time.monotonic() - self._last_fetch_ok_mono, 1))
            return {
                **self._config,
                "allow_writes": self.service.allow_writes,
                "min_battery_voltage": self.service.min_battery_voltage,
                "pop_warning": self._pop_warning(),
                "current": {
                    "net_balance_w": self._last_net_balance,
                    "remote_timestamp": self._last_remote_ts,
                    "age_s": age,
                    "desired_state": self._desired,
                },
                "last_run": self._last_run,
            }

    def _pop_warning(self):
        """None if the output priority (POP) we've most recently observed is
        one that actually lets the charger run. A warning string otherwise.

        MEASURED ON THE REAL UNIT, 2026-08-24 -- and it is the opposite of
        what this code assumed until now:

            POP=00 (utility first) + PCP=01  ->  charges, 20 A, 24.0 -> 28.2 V
            POP=02 (SBU)           + PCP=01  ->  0 A for 2 min, battery FELL

        So charging requires POP=00, not POP=02. The earlier note here had
        it inverted, which meant this warned when things were fine and
        stayed silent when the charger was dead -- and every PCP write the
        automations made while POP=02 was a silent no-op. POP=01 has not
        been tested; it is treated as unsafe-to-assume and warned about.

        This also disables the low-battery floor: forcing PCP01 on a low
        pack does nothing at all while POP=02, so that interlock cannot
        rescue the battery either. That is why this warns loudly rather
        than being a footnote.
        """
        pop = self.service.last_known_priority("POP")
        if pop is None:
            return ("a prioridade de saída (POP) ainda não foi observada nesta "
                    "instalação -- se não estiver em 00, o carregamento não funciona")
        if pop != CHARGING_POP:
            return (f"POP está em {pop}: o carregador NÃO funciona assim. "
                    f"Medido a 2026-08-24: só com POP={CHARGING_POP} é que o PCP "
                    f"faz o inversor carregar (20 A); com POP=02 fica a 0 A e a "
                    f"bateria desce. Isto também anula o limite mínimo de bateria.")
        return None

    def set_config(self, updates: dict) -> dict:
        cfg = validate_config(updates)
        with self._lock:
            self._config = cfg
            self._save()
            if cfg["enabled"]:
                self._tick(force=True)
        return self.get_state()

    def is_overriding(self) -> bool:
        """True quando este controlador está, neste momento, a impor o
        carregamento e o agendamento deve ficar de fora.

        Usa a decisão do último tick (`self._desired`) em vez de ir buscar
        uma leitura nova: o agendamento corre a cada 60s e este a cada 30s,
        por isso o valor está sempre fresco, e assim evita-se um pedido HTTP
        extra (e uma possível divergência) a cada verificação do
        agendamento.
        """
        with self._lock:
            return (self._config["enabled"]
                    and self._config["mode"] == "override"
                    and self._desired == "charging")

    def set_enabled(self, enabled: bool) -> dict:
        """Flip the enabled flag alone, keeping every other setting --
        used for the schedule/grid-export mutual-exclusion check in
        webapp/app.py so disabling one doesn't discard its config."""
        with self._lock:
            cfg = dict(self._config)
            cfg["enabled"] = bool(enabled)
            return self.set_config(cfg)

    # ---- evaluation ---------------------------------------------------------

    def _http_fetch(self):
        """Return (net_balance_watts, remote_timestamp), or (None, None) if
        the auto-energy dashboard couldn't be reached or answered oddly."""
        cfg = self._config
        try:
            resp = requests.get(cfg["source_url"], timeout=cfg["http_timeout"])
            resp.raise_for_status()
            latest = resp.json()["latest"]
            return float(latest["net_balance"]), latest.get("timestamp")
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return None, None

    def _evaluate(self):
        cfg = self._config
        net, remote_ts = self._fetch_fn()
        now_mono = time.monotonic()

        if net is not None:
            self._last_fetch_ok_mono = now_mono
            self._last_net_balance = net
            self._last_remote_ts = remote_ts

        known = (self._last_fetch_ok_mono is not None and
                (now_mono - self._last_fetch_ok_mono) <= cfg["stale_after"])

        if not known:
            # No recent reading -- assume we are not exporting rather than
            # trust a stale or missing number. This is the conservative
            # direction: it means "stop charging", never "start charging
            # blind".
            desired = "idle"
        else:
            balance = self._last_net_balance
            if balance <= cfg["export_threshold_w"]:
                desired = "charging"
            elif balance >= cfg["import_threshold_w"]:
                desired = "idle"
            else:
                # Inside the hysteresis dead-band: hold the previous state,
                # or default to idle if this is the very first evaluation.
                desired = self._desired or "idle"

        raw_target = cfg["charge_pcp"] if desired == "charging" else cfg["idle_pcp"]
        target, override = apply_low_battery_floor(self.service, raw_target)
        return desired, target, override, known

    def tick(self, force: bool = False) -> dict:
        with self._lock:
            return self._tick(force=force)

    def _tick(self, force: bool = False) -> dict:
        cfg = self._config
        desired, target, override, known = self._evaluate()
        self._desired = desired

        result = {
            "at": _utcnow(), "desired_state": desired, "target": target,
            "net_balance_w": self._last_net_balance, "known": known,
            "why": override, "applied": False, "note": "",
            "pop_warning": self._pop_warning(),
        }

        now_mono = time.monotonic()
        dwell_elapsed = (now_mono - self._last_switch_mono) >= cfg["min_switch_interval"]

        if not cfg["enabled"]:
            result["note"] = "grid-export charging disabled"
        elif cfg["mode"] == "override" and desired != "charging":
            # Modo "override": sem excedente não escrevemos nada -- quem
            # manda é o agendamento. Limpamos o último PCP aplicado para
            # que a próxima exportação volte mesmo a escrever, em vez de
            # concluir "já está em PCPxx" a partir de um valor que
            # entretanto o agendamento já substituiu.
            self._last_applied_pcp = None
            result["note"] = "sem excedente — decisão entregue ao agendamento"
        elif not self.service.allow_writes:
            result["note"] = "server is read-only; not applied"
        elif target == self._last_applied_pcp and not force:
            result["note"] = f"already PCP{target}; nothing to do"
            result["applied"] = True
        elif not force and not dwell_elapsed and override is None:
            # Debounced -- the safety override (override is not None) is the
            # one case allowed to bypass this, since a low battery can't
            # wait out a cooldown timer.
            remaining = round(cfg["min_switch_interval"] - (now_mono - self._last_switch_mono), 1)
            result["note"] = f"target changed but holding for {remaining}s (anti-flap)"
        else:
            try:
                resp = self.service.send_set(f"PCP{target}", source="grid_export")
                ok = resp.startswith("(ACK")
                result["response"] = resp
                result["applied"] = ok
                result["note"] = "applied" if ok else f"device did not acknowledge: {resp}"
                if ok:
                    self._last_applied_pcp = target
                    self._last_switch_mono = now_mono
            except InverterError as e:
                result["note"] = f"error: {e}"

        self._last_run = result
        return result

    # ---- lifecycle ----------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop,
                                        name="inverter-grid-charge", daemon=True)
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
        while True:
            interval = self._config["poll_interval"]
            if self._stop.wait(interval):
                return
            try:
                self.tick()
            except Exception:
                pass
