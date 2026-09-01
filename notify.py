#!/usr/bin/env python3
"""
Telegram alerts for things that need a person.

Written after 2026-09-01, when the serial thread wedged at 00:34 and nothing
noticed for 14.5 hours -- no telemetry, and the 04:30 battery window never
ran. Every automatic recovery path in this project now works, but they all
fail silently when they fail, and the user is often remote.

Two rules shape this, both learned from that incident:

  * **Alert on state changes, not on conditions.** A fault that persists for
    a week must produce one message, not 2000. Ringing continuously is the
    same as not ringing at all -- it trains you to ignore it.
  * **Say when things recover.** An alert with no matching all-clear leaves
    you unable to tell "still broken" from "fixed and nobody said".

There is also an optional daily heartbeat, so silence is informative: with
one, no message means the notifier is broken; without one, no message means
nothing at all.

Configuration lives in web.json (already gitignored -- it holds the API
token), under a "telegram" key:

    "telegram": {
      "enabled": true,
      "token": "123456:ABC-DEF...",     from @BotFather
      "chat_id": "987654321",           from /getUpdates after messaging the bot
      "heartbeat_hour": 9               optional; omit to disable
    }

    python3 notify.py --test            send a test message and exit

**Exercising an alert path writes to the shared state file**, which then
disagrees with reality: the next healthy check reports a recovery that never
happened. Worse, a dry-run of the watchdog still sends -- "dry run" there
means it will not touch the USB bus, not that it stays quiet. Point
--state-file (or usb_watchdog's config) somewhere disposable first. Learned
by sending the user a false outage alert on 2026-09-01.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request

from webapp.atomic_write import write_json_atomic

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, ".notify_state.json")

# Telegram's own cap is 4096; leave room for the prefix we add.
MAX_LEN = 3800


class Notifier:
    """Sends to Telegram, or does nothing if unconfigured.

    Never raises. An alerting channel that can break the thing it watches is
    worse than no alerting channel.
    """

    def __init__(self, config: dict | None, state_file: str = STATE_FILE):
        cfg = (config or {}).get("telegram") or {}
        self.enabled = cfg.get("enabled") is True and bool(cfg.get("token")) \
            and bool(cfg.get("chat_id"))
        self.token = cfg.get("token")
        self.chat_id = cfg.get("chat_id")
        self.heartbeat_hour = cfg.get("heartbeat_hour")
        self.state_file = state_file

    # ---- transport ------------------------------------------------------

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        text = text[:MAX_LEN]
        url = "https://api.telegram.org/bot%s/sendMessage" % self.token
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=15) as r:
                return json.loads(r.read().decode()).get("ok", False)
        except Exception:                          # noqa: BLE001
            return False

    # ---- state, so a standing fault does not repeat ---------------------

    def _state(self) -> dict:
        try:
            with open(self.state_file) as fh:
                value = json.load(fh)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            write_json_atomic(self.state_file, state)
        except (OSError, TypeError):
            pass

    def on_change(self, key: str, value, text: str) -> bool:
        """Send `text` only when `value` differs from the last one seen for
        `key`. Returns whether a message went out.

        `value` is what defines "the same situation" -- usually a short
        status string. Keep it coarse: including a timestamp or a voltage
        would make every check a change and defeat the point. A failed send
        is deliberately retried on the next check; otherwise one transient
        Telegram outage could suppress the only alert for a standing fault.
        """
        state = self._state()
        entry = state.get(key)
        if (isinstance(entry, dict) and entry.get("value") == value
                and entry.get("sent") is True):
            return False
        sent = self.send(text)
        state[key] = {"value": value,
                      "at": dt.datetime.now().isoformat(timespec="seconds"),
                      "sent": sent}
        self._save_state(state)
        return sent

    def heartbeat(self, text: str) -> bool:
        """At most one per day, at heartbeat_hour. Makes silence meaningful:
        no daily message means the notifier itself has stopped working."""
        if self.heartbeat_hour is None:
            return False
        now = dt.datetime.now()
        if now.hour != int(self.heartbeat_hour):
            return False
        today = now.date().isoformat()
        state = self._state()
        if state.get("_heartbeat") == today:
            return False
        sent = self.send(text)
        # Do not consume today's heartbeat on a failed send. The daemon may
        # get another chance during this hour, which is exactly when a retry
        # is useful.
        if sent:
            state["_heartbeat"] = today
        self._save_state(state)
        return sent


def load_config(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "web.json"))
    ap.add_argument("--test", action="store_true", help="send a test message")
    ap.add_argument("--message", help="send this text and exit")
    ap.add_argument("--state-file", default=STATE_FILE,
                    help="where the last-reported condition is kept; point "
                         "this somewhere disposable when exercising alert "
                         "paths, or the run will overwrite the live state")
    args = ap.parse_args()

    n = Notifier(load_config(args.config), state_file=args.state_file)
    if not n.enabled:
        print("telegram nao configurado (ver o docstring deste ficheiro)")
        return 2
    text = args.message or (
        "Inversor: teste de notificacoes. Se estas a ler isto, funciona.")
    ok = n.send(text)
    print("enviado" if ok else "FALHOU ao enviar")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
