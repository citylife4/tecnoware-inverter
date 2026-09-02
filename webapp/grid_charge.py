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
(apply_low_battery_floor in safety.py) while solar generation is available.
At night the export controller is inactive and sends no PCP commands: there
is no export to control, and battery maintenance belongs to the inverter or
another explicitly enabled controller rather than this one.
"""

from __future__ import annotations

import csv
import json
import math
import os
import threading
import time
from datetime import datetime, timezone

import requests

from transport import InverterError
from webapp import config_error
from webapp.atomic_write import write_json_atomic
from webapp.trace import append_row
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

# Below this much solar generation there is, by definition, no export to
# control. The controller becomes inactive and sends no PCP command at all.
# In particular it must not alternate idle_pcp with the low-battery override
# through the night -- that caused repeated PCP03 -> PCP01 -> PCP03 charger
# cycles on the live installation on 2026-08-26.
SOLAR_FLOOR_W = 5.0

DEFAULT_CONFIG = {
    "enabled": False,
    "mode": "exclusive",
    # EcoPi / auto-energy live telemetry. Same host as this service after
    # the 2026-08-28 move; fail-safe is unchanged (stale → idle).
    "source_url": "http://127.0.0.1:8000/api/live",
    "poll_interval": 30.0,       # matches the dashboard's own refresh cadence
    "http_timeout": 5.0,
    # Hysteresis band, in watts of the SURPLUS SIGNAL -- which is
    # net_balance minus inverter_input_w, i.e. what the grid balance would
    # be if the inverter drew nothing. See _evaluate() for why that matters:
    # the charger's own 350-560W draw is inside inverter_input_w, so
    # subtracting it means turning the charger ON no longer moves the number
    # that decided to turn it on. Without that, the signal chases itself --
    # which is exactly the 5-hour PCP01<->PCP03 flap of 2026-08-24.
    #
    # Positive, not negative, and deliberately so. Waiting for net_balance
    # to actually go negative means export has ALREADY happened by the time
    # we detect it, write PCP (seconds on this hardware) and the charger
    # spins up. Starting while the house is still importing ~50W pre-empts
    # it. Exporting is a legal problem at this installation; over-importing
    # is merely wasteful, so the asymmetry is on purpose.
    "export_threshold_w": 50.0,
    # ...and come back up to this before stopping.
    "import_threshold_w": 250.0,
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
    if not isinstance(out["enabled"], bool):
        raise ValueError("enabled must be a boolean")

    if out["mode"] not in MODES:
        raise ValueError(f"mode must be one of {list(MODES)}, got {out['mode']!r}")

    if not isinstance(out["source_url"], str) or not out["source_url"].startswith(
            ("http://", "https://")):
        raise ValueError("source_url must be an http(s):// URL")

    for key in ("poll_interval", "http_timeout", "export_threshold_w",
               "import_threshold_w", "min_switch_interval", "stale_after"):
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{key} must be a number, got {cfg.get(key)!r}")
        if not math.isfinite(out[key]):
            raise ValueError(f"{key} must be finite, got {cfg.get(key)!r}")

    if out["poll_interval"] < 5:
        raise ValueError("poll_interval must be at least 5 seconds")
    if out["http_timeout"] <= 0:
        raise ValueError("http_timeout must be positive")
    for key in ("min_switch_interval", "stale_after"):
        if out[key] < 0:
            raise ValueError(f"{key} must not be negative")
    if out["export_threshold_w"] >= out["import_threshold_w"]:
        raise ValueError("export_threshold_w must be lower than "
                         "import_threshold_w -- they define a hysteresis band")

    for key in ("charge_pcp", "idle_pcp"):
        if out[key] not in PCP_VALUES:
            raise ValueError(f"{key} must be one of {sorted(PCP_VALUES)}, got {out[key]!r}")

    return out


class GridChargeController:
    """Owns the grid-export-following config and a background polling
    thread. Writes go through InverterService, sharing its lock, retry
    behaviour, and audit log with every other write source."""

    def __init__(self, service, path: str, fetch_fn=None, trace_dir=None):
        self.service = service
        self.path = path
        # Where to append the decision trace (one CSV per day), or None to
        # keep none. The thresholds in DEFAULT_CONFIG have now been guessed
        # wrong twice on this installation; the point of this file is that
        # the next revision is argued from recorded numbers instead.
        self.trace_dir = trace_dir
        # Defaults to the real HTTP call; tests inject a fake so they don't
        # need network access or a running auto-energy instance.
        self._fetch_fn = fetch_fn or self._http_fetch

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

        self._config = self._load()
        # "charging" | "idle" | "disabled_no_solar" | None (startup)
        self._desired = None
        self._last_applied_pcp = None
        self._last_switch_mono = 0.0
        self._last_fetch_ok_mono = None
        self._last_net_balance = None
        self._last_inverter_input = None
        self._last_signal = None
        self._last_solar = None
        self._last_payload = None
        self._last_remote_ts = None
        self._last_run = None

    # ---- persistence ------------------------------------------------------

    def _load(self) -> dict:
        # Cleared here rather than only on failure, so a later reload that
        # succeeds takes the warning back off the dashboard.
        self._config_error = None
        if not os.path.exists(self.path):
            return dict(DEFAULT_CONFIG)
        try:
            with open(self.path) as fh:
                raw = fh.read()
            if not raw.strip():
                raise ValueError("config file is empty")
            return validate_config(json.loads(raw))
        except (ValueError, OSError) as e:
            # Falling back to defaults means enabled=False. For this
            # controller that is not the safe direction -- see
            # webapp/config_error.py -- so say so instead of going quiet.
            self._config_error = config_error.report(self.path, e)
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
                "config_error": self._config_error,
                "current": {
                    "net_balance_w": self._last_net_balance,
                    "inverter_input_w": self._last_inverter_input,
                    "surplus_signal_w": self._last_signal,
                    "solar_w": self._last_solar,
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
            previous = self._config
            self._config = cfg
            try:
                self._save()
            except Exception:
                # A failed API save must not change the running controller or
                # dismiss the warning for the rejected file still on disk.
                self._config = previous
                raise
            # Only a config that was both validated and written replaces the
            # one that got rejected at boot.
            self._config_error = None
            if cfg["enabled"]:
                self._tick(force=True)
        return self.get_state()

    def last_signal(self):
        """(signal_w, fresh) -- the surplus signal and whether the reading
        behind it is recent enough to act on.

        The signal is `net_balance - inverter_input_w`, which makes it
        exactly the grid balance the house would show **if this inverter
        drew nothing** -- i.e. what it becomes the moment the loads move to
        the battery. That is why BatteryWindow uses it to decide whether a
        daytime discharge is safe: a positive signal means the house would
        still be importing without the inverter, so removing its load cannot
        push the meter into export.
        """
        with self._lock:
            if self._last_signal is None or self._last_fetch_ok_mono is None:
                return None, False
            age = time.monotonic() - self._last_fetch_ok_mono
            return self._last_signal, age <= self._config["stale_after"]

    def last_live_payload(self):
        """The most recent auto-energy `latest` dict, or None. Read-only
        view for the dashboard -- see webapp/energy_view.py."""
        with self._lock:
            return dict(self._last_payload) if self._last_payload else None

    def is_absorbing_export(self) -> bool:
        """True quando o carregador está, NESTE momento, a absorver
        excedente. Independente do `mode`, que só decide o que acontece
        quando *não* está.

        É esta a pergunta que a janela de bateria precisa de fazer. O
        carregador não funciona em POP=02 (gotcha #1), por isso absorver
        excedente e pôr as cargas no pack são mutuamente exclusivos --
        e isso é verdade em `exclusive` tanto como em `override`.

        Usa a decisão do último tick (`self._desired`) em vez de ir buscar
        uma leitura nova: quem pergunta corre a cada 60s e este a cada 30s,
        por isso o valor está sempre fresco, e assim evita-se um pedido HTTP
        extra (e uma possível divergência) a cada verificação.

        `_desired` sozinho NÃO chega. A banda morta segura o estado anterior
        por desenho, e à noite -- sem sol -- o sinal assenta lá dentro (~80-95W
        nesta casa). Um dia que acabasse em "charging" ficaria a impor o
        carregamento até de manhã, e a janela de bateria cederia a noite
        inteira sem nunca chegar a usar o pack. Ceder exige portanto um
        excedente REAL na leitura atual, não uma intenção retida.
        """
        with self._lock:
            if not (self._config["enabled"] and self._desired == "charging"):
                return False
            if (self._last_fetch_ok_mono is None
                    or time.monotonic() - self._last_fetch_ok_mono
                    > self._config["stale_after"]):
                return False
            signal = self._last_signal
            return (signal is not None
                    and signal <= self._config["export_threshold_w"])

    def is_overriding(self) -> bool:
        """True quando este controlador está a sobrepor-se ao AGENDAMENTO e
        este deve ficar de fora.

        Só faz sentido em `mode="override"`. Em `exclusive` os dois nunca
        estão ligados ao mesmo tempo -- webapp/app.py recusa com
        409 conflicting_automation -- por isso não há ninguém a quem
        sobrepor-se, e a resposta é sempre False.

        Não confundir com `is_absorbing_export()`: a janela de bateria move
        o POP, não o PCP, coexiste com este controlador em qualquer dos
        modos, e por isso pergunta a outra coisa.
        """
        with self._lock:
            if self._config["mode"] != "override":
                return False
        return self.is_absorbing_export()

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
        """Return (net_balance_w, inverter_input_w, remote_timestamp), or
        (None, None, None) if the auto-energy dashboard couldn't be reached
        or answered oddly.

        inverter_input_w is Shelly channel 1 -- the feed into this
        inverter's AC input, which includes whatever the charger is drawing.
        It is what makes the surplus signal immune to its own effect; see
        _evaluate(). A reading without it is treated as unknown rather than
        falling back to raw net_balance, because that fallback is precisely
        the self-chasing signal that flapped for five hours."""
        cfg = self._config
        try:
            resp = requests.get(cfg["source_url"], timeout=cfg["http_timeout"])
            resp.raise_for_status()
            latest = resp.json()["latest"]
            if not isinstance(latest, dict):
                raise ValueError("latest must be an object")
            inv = latest.get("inverter_input_w")
            sol = latest.get("ac_solar_w")
            values = (float(latest["net_balance"]),
                      None if inv is None else float(inv),
                      None if sol is None else float(sol))
            if any(v is not None and not math.isfinite(v) for v in values):
                raise ValueError("non-finite live reading")
            # Stashed whole for webapp/energy_view.py. The control path only
            # needs two numbers, but the dashboard wants the solar/house
            # split too, and re-polling the same endpoint for it would be
            # a second HTTP call to say the same thing.
            with self._lock:
                self._last_payload = latest
            return (*values, latest.get("timestamp"))
        except (requests.RequestException, ValueError, KeyError, TypeError,
                OverflowError):
            return None, None, None, None

    def _evaluate(self):
        cfg = self._config
        net, inverter_w, solar_w, remote_ts = self._fetch_fn()
        now_mono = time.monotonic()

        # The signal is the grid balance the house would show if this
        # inverter drew nothing: net_balance already equals
        # (house_power - solar), so subtracting the inverter's own feed
        # leaves the rest of the house's balance. Charging then cannot move
        # its own input -- the property the old raw-net_balance version
        # lacked. Both numbers must be present; a missing one is unknown,
        # never a fallback to the self-chasing signal.
        signal = None
        if net is not None and inverter_w is not None:
            signal = net - inverter_w

        if signal is not None:
            self._last_fetch_ok_mono = now_mono
            self._last_net_balance = net
            self._last_inverter_input = inverter_w
            self._last_signal = signal
            self._last_solar = solar_w
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
            balance = self._last_signal
            solar = self._last_solar

            # Is anything generating? Answered before the hysteresis, which
            # would otherwise hold the day's last state right through the
            # night -- the signal settles at +80-100 W on this house after
            # dark, which is inside the dead-band.
            if solar is not None:
                # A working meter is direct evidence and takes precedence.
                generating = solar >= SOLAR_FLOOR_W
            else:
                # No meter: its Shelly has been offline since 2026-08-31,
                # and auto-energy reports ac_solar_w as null. Export still
                # settles it -- you cannot export without generating. The
                # signal is net_balance minus the inverter's own draw, so
                # the charger cannot drive it negative by running.
                #
                # The rule this replaces read "proceed unless we know there
                # is no sun", which with a missing reading disarmed the
                # night-time protection entirely: backwards for a safety
                # check.
                generating = balance < 0

            if not generating:
                # No generation means no export-control work exists. This is
                # distinct from "idle", which owns PCP03 in exclusive mode.
                # Returning no target also bypasses the low-battery PCP01
                # override: battery maintenance is outside this controller's
                # job while the sun is down.
                return "disabled_no_solar", None, None, known

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
            "net_balance_w": self._last_net_balance,
            "inverter_input_w": self._last_inverter_input,
            "surplus_signal_w": self._last_signal, "known": known,
            "why": override, "applied": False, "note": "",
            "pop_warning": self._pop_warning(),
        }

        now_mono = time.monotonic()
        dwell_elapsed = (now_mono - self._last_switch_mono) >= cfg["min_switch_interval"]

        if not cfg["enabled"]:
            result["note"] = "grid-export charging disabled"
        elif desired == "disabled_no_solar":
            # Forget the cached PCP because another controller or a person
            # may change it overnight. The first daylight decision must be
            # applied to the inverter rather than incorrectly treated as an
            # idempotent no-op.
            self._last_applied_pcp = None
            result["note"] = "no solar generation — grid-export control inactive"
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
                else:
                    # A garbled reply is not proof the write failed -- on this
                    # link it usually means it worked and the reply came back
                    # mangled (gotcha #8). Keeping the old value would let the
                    # next tick conclude "already PCPxx; nothing to do" about a
                    # state nothing has confirmed. None means unknown, and the
                    # next tick writes for real. There is no way to read PCP
                    # back from this unit, so this cache is the only belief
                    # there is -- all the more reason not to fill it with a
                    # guess.
                    self._last_applied_pcp = None
            except InverterError as e:
                result["note"] = f"error: {e}"
                self._last_applied_pcp = None

        self._last_run = result
        self._append_trace(result)
        return result

    # Header names, and the result-dict keys they come from. The timestamp
    # lives under "at" in the tick result but is called "ts" everywhere the
    # telemetry is read, so the two are mapped rather than assumed equal.
    TRACE_COLUMNS = ("ts", "net_balance_w", "inverter_input_w",
                     "surplus_signal_w", "known", "desired_state", "target",
                     "applied", "note")
    _TRACE_KEYS = ("at", "net_balance_w", "inverter_input_w",
                   "surplus_signal_w", "known", "desired_state", "target",
                   "applied", "note")

    def _append_trace(self, result) -> None:
        """One row per tick. Plain append, like the telemetry CSV -- this
        machine loses power without warning, so read it back with
        read_telemetry.py, which tolerates the NUL runs that causes."""
        if not self.trace_dir:
            return
        path = os.path.join(self.trace_dir,
                            "gridcharge-%s.csv" % result["at"][:10])
        append_row(path, self.TRACE_COLUMNS,
                   [result.get(k) for k in self._TRACE_KEYS])

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
