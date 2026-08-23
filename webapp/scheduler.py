"""
Time-of-day scheduling for the charger source priority (PCP), running
inside the web server instead of as a separate cron job / charge_schedule.py
process.

Why here and not external cron: the web server already owns the serial port
exclusively (see webapp/service.py), so a second process running
charge_schedule.py would be fighting it for /dev/ttyUSB0. This reuses
charge_schedule.py's pure time-window logic (parse_hhmm / rule_active /
pick_rule) rather than re-implementing it, and applies rules through the
same InverterService the dashboard and API use -- so the low-battery
interlock and the audit log are consistent everywhere a write can originate.

The safety behaviour intentionally matches charge_schedule.py: a "03"
(solar-only) rule that would apply below min_battery_voltage is downgraded
to "01" (utility fallback) and the reason is recorded, rather than refused
outright. See CLAUDE.md gotcha #2 for why solar-only with no sun is
dangerous on this hardware -- it already happened once.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from charge_schedule import VALID_PCP, parse_hhmm, pick_rule  # noqa: F401  (re-exported)
from transport import InverterError
from webapp.atomic_write import write_json_atomic
from webapp.safety import apply_low_battery_floor

DEFAULT_STATE = {"enabled": False, "rules": []}

# Not secret, but instance-specific -- readable by the owning user, same
# treatment as web.json.
FILE_MODE = 0o600


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_rules(rules) -> list:
    """Same shape charge_schedule.py's JSON config uses: a list of
    {"from": "HH:MM", "to": "HH:MM", "pcp": "00".."03", "why": str}."""
    if not isinstance(rules, list):
        raise ValueError("rules must be a list")
    out = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            raise ValueError(f"rule {i}: must be an object")
        frm, to, pcp = r.get("from"), r.get("to"), r.get("pcp")
        if pcp not in VALID_PCP:
            raise ValueError(
                f"rule {i}: pcp must be one of {sorted(VALID_PCP)}, got {pcp!r}")
        try:
            parse_hhmm(frm)
            parse_hhmm(to)
        except (ValueError, AttributeError, TypeError):
            raise ValueError(
                f"rule {i}: from/to must be \"HH:MM\" (24h), got {frm!r}/{to!r}")
        out.append({"from": frm, "to": to, "pcp": pcp,
                    "why": str(r.get("why", ""))[:200]})
    return out


class Scheduler:
    """Owns the schedule config file and a background thread that applies
    it. Writes go through InverterService, so they share its lock, retry
    behaviour, and audit log with every other write source."""

    def __init__(self, service, path: str, poll_interval: float = 60.0):
        self.service = service
        self.path = path
        self.poll_interval = poll_interval

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

        self._state = self._load()
        self._last_applied_pcp = None
        self._last_run = None

    # ---- persistence ----------------------------------------------------

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return dict(DEFAULT_STATE)
        try:
            with open(self.path) as fh:
                raw = fh.read()
            if not raw.strip():
                return dict(DEFAULT_STATE)
            data = json.loads(raw)
            return {"enabled": bool(data.get("enabled", False)),
                    "rules": validate_rules(data.get("rules", []))}
        except (ValueError, OSError):
            # A corrupt or unreadable state file shouldn't crash the server
            # over stored automation config -- start disabled, let the user
            # re-save the schedule from the dashboard.
            return dict(DEFAULT_STATE)

    def _save(self) -> None:
        write_json_atomic(self.path, self._state, mode=FILE_MODE)

    # ---- read/write API (called from webapp/app.py) ---------------------

    def get_state(self) -> dict:
        with self._lock:
            rule, target, why, override = self._evaluate()
            return {
                "enabled": self._state["enabled"],
                "rules": self._state["rules"],
                "poll_interval": self.poll_interval,
                "allow_writes": self.service.allow_writes,
                "min_battery_voltage": self.service.min_battery_voltage,
                # A live preview of what would happen right now, independent
                # of whether the scheduler is actually enabled -- lets the
                # dashboard show "rule X would apply, target PCPnn" while
                # someone is still editing.
                "current_rule": rule,
                "current_target": target,
                "override_reason": override,
                "last_run": self._last_run,
            }

    def set_state(self, enabled: bool, rules: list) -> dict:
        rules = validate_rules(rules)
        with self._lock:
            self._state = {"enabled": bool(enabled), "rules": rules}
            self._save()
            if self._state["enabled"]:
                # Apply immediately rather than making an edit wait up to
                # poll_interval to take effect.
                self._tick(force=True)
        return self.get_state()

    # ---- evaluation -------------------------------------------------------

    def _evaluate(self, now=None):
        """Return (matching_rule, effective_target, why, override_reason).
        effective_target already has the low-battery override applied."""
        now = now or datetime.now()
        rule = pick_rule(self._state["rules"], now.time())
        if rule is None:
            return None, None, None, None
        why = rule.get("why", "")
        target, override = apply_low_battery_floor(self.service, rule["pcp"])
        return rule, target, why, override

    def tick(self, force: bool = False, now=None) -> dict:
        """Evaluate the schedule and apply it if enabled. Public so the
        dashboard's "apply now" button and tests can drive it directly.
        `now` overrides the current time -- production code never passes
        it; tests use it so a rule's match doesn't depend on wall-clock
        timing at the moment the test happens to run."""
        with self._lock:
            return self._tick(force=force, now=now)

    def _tick(self, force: bool = False, now=None) -> dict:
        now = now or datetime.now()
        rule, target, why, override = self._evaluate(now)
        result = {"at": _utcnow(), "rule": rule, "target": target,
                  "why": override or why, "applied": False, "note": ""}

        if not self._state["enabled"]:
            result["note"] = "scheduler disabled"
        elif rule is None:
            result["note"] = "no rule matches the current time"
        elif not self.service.allow_writes:
            result["note"] = "server is read-only; schedule was not applied"
        elif target == self._last_applied_pcp and not force:
            result["note"] = f"already PCP{target}; nothing to do"
            result["applied"] = True
        else:
            try:
                resp = self.service.send_set(f"PCP{target}", source="scheduler")
                ok = resp.startswith("(ACK")
                result["response"] = resp
                result["applied"] = ok
                result["note"] = "applied" if ok else f"device did not acknowledge: {resp}"
                if ok:
                    self._last_applied_pcp = target
            except InverterError as e:
                result["note"] = f"error: {e}"

        self._last_run = result
        return result

    # ---- lifecycle --------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop,
                                        name="inverter-schedule", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self):
        # Apply once on startup so a restart doesn't leave the wrong
        # priority in place for a full poll_interval.
        try:
            self.tick()
        except Exception:
            pass
        while not self._stop.wait(self.poll_interval):
            try:
                self.tick()
            except Exception:
                pass
