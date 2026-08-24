"""
Flask app: dashboard UI + REST API for the inverter.

Auth model
----------
One shared API token guards everything except /api/health. Programmatic
callers present it as a header (X-API-Key, or Authorization: Bearer ...);
the browser exchanges it once at /login for a signed session cookie so the
dashboard doesn't have to keep the token in JavaScript.

CSRF: the session cookie is SameSite=Lax and every write demands a JSON
content type. A cross-origin form post can send neither, and a
cross-origin fetch with application/json triggers a CORS preflight that
this server never approves -- so a session cookie alone can't be abused
from another site.
"""

from __future__ import annotations

import functools
import hmac
import secrets

from flask import (Flask, jsonify, render_template, request, session,
                   redirect, url_for)

from commands import QUERY_COMMANDS, SET_COMMANDS, VERIFIED_WORKING_QUERIES
from transport import InverterError
from webapp import safety, ui_labels
from webapp.safety import CommandRejected

# Endpoints reachable without a token: liveness probing and the login flow.
PUBLIC_ENDPOINTS = {"health", "login", "static"}


def _fail(message, code="error", status=400, **extra):
    payload = {"ok": False, "error": message, "code": code}
    payload.update(extra)
    return jsonify(payload), status


def create_app(service, scheduler=None, grid_charge=None, *,
               token: str, secret_key: str) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=64 * 1024,
        # Flask's default is a 12-hour Cache-Control on static files. On a
        # small single-instance dashboard like this, that means every UI
        # deploy is invisible to an already-open browser tab for up to half
        # a day -- hit in practice while iterating on the grid-charge panel.
        # 0 keeps ETag/Last-Modified (still cheap, 304s on repeat loads)
        # but forces revalidation every time instead of trusting a stale copy.
        SEND_FILE_MAX_AGE_DEFAULT=0,
    )
    app.service = service
    app.scheduler = scheduler
    app.grid_charge = grid_charge
    app.api_token = token

    # ---- auth ----------------------------------------------------------

    def _token_ok(candidate) -> bool:
        # compare_digest, not ==, so a wrong token can't be recovered by
        # timing the comparison.
        return bool(candidate) and hmac.compare_digest(str(candidate), token)

    def _authenticated() -> bool:
        header = request.headers.get("X-API-Key")
        if _token_ok(header):
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and _token_ok(auth[7:].strip()):
            return True
        return session.get("authed") is True

    @app.before_request
    def _guard():
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if _authenticated():
            return None
        if request.path.startswith("/api/"):
            return _fail("authentication required", code="unauthorized", status=401,
                         hint="send X-API-Key: <token> or Authorization: Bearer <token>")
        return redirect(url_for("login", next=request.path))

    def require_json_write(fn):
        """Writes must be JSON. This is the CSRF interlock -- see module docstring."""
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not request.is_json:
                return _fail("writes require Content-Type: application/json",
                             code="bad_content_type", status=415)
            return fn(*a, **kw)
        return wrapper

    # ---- UI ------------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if _token_ok(request.form.get("token", "").strip()):
                session["authed"] = True
                session.permanent = True
                nxt = request.args.get("next") or url_for("dashboard")
                # Only ever redirect within this app -- never to an
                # attacker-supplied absolute URL.
                if not nxt.startswith("/") or nxt.startswith("//"):
                    nxt = url_for("dashboard")
                return redirect(nxt)
            error = "Token incorreto."
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def dashboard():
        # Etiquetas em pt-PT só para o browser -- a API mantém-se em
        # inglês para quem automatiza. Ver webapp/ui_labels.py.
        return render_template("index.html",
                               pcp_values=ui_labels.PCP_LABELS_PT,
                               pop_values=ui_labels.POP_LABELS_PT,
                               mode_labels=ui_labels.MODE_LABELS_PT,
                               rating_labels=ui_labels.RATING_LABELS_PT,
                               battery_type_labels=ui_labels.BATTERY_TYPE_LABELS_PT,
                               allow_writes=service.allow_writes,
                               scheduler_available=scheduler is not None,
                               grid_charge_available=grid_charge is not None)

    # ---- API: read -----------------------------------------------------

    @app.route("/api/health")
    def health():
        """Unauthenticated liveness probe. Deliberately leaks nothing about
        the inverter beyond whether the poller has a fresh reading."""
        latest = service.latest()
        return jsonify({"ok": True, "service": "tecnoware-inverter",
                        "connected": latest["connected"]})

    @app.route("/api/status")
    def api_status():
        """Cached poller snapshot. ?live=1 forces a fresh read off the wire."""
        if request.args.get("live") in ("1", "true", "yes"):
            try:
                service.refresh()
            except InverterError as e:
                return _fail(str(e), code="read_failed", status=503)
        payload = service.latest()
        payload["ok"] = True
        return jsonify(payload)

    @app.route("/api/history")
    def api_history():
        rows = service.history()
        try:
            limit = int(request.args.get("limit", 0))
        except ValueError:
            limit = 0
        if limit > 0:
            rows = rows[-limit:]
        return jsonify({"ok": True, "count": len(rows), "history": rows})

    @app.route("/api/device")
    def api_device():
        refresh = request.args.get("refresh") in ("1", "true", "yes")
        try:
            return jsonify({"ok": True, "device": service.device_info(refresh)})
        except InverterError as e:
            return _fail(str(e), code="read_failed", status=503)

    @app.route("/api/ratings")
    def api_ratings():
        """QPIRI. Static rated values -- NEVER reflects a setting change
        (CLAUDE.md gotcha #1), so don't use it to confirm a write."""
        refresh = request.args.get("refresh") in ("1", "true", "yes")
        try:
            return jsonify({
                "ok": True,
                "ratings": service.ratings(refresh),
                "note": ("QPIRI reports static rated values and does not reflect "
                         "setting changes; confirm writes via /api/status instead"),
            })
        except InverterError as e:
            return _fail(str(e), code="read_failed", status=503)

    @app.route("/api/commands")
    def api_commands():
        return jsonify({
            "ok": True,
            "queries": QUERY_COMMANDS,
            "set_commands": SET_COMMANDS,
            "verified_working_queries": VERIFIED_WORKING_QUERIES,
            "routine_set_commands": sorted(safety.ROUTINE_SET_COMMANDS),
            "dangerous_set_commands": safety.DANGEROUS_SET_COMMANDS,
            "charger_priority_values": safety.PCP_VALUES,
            "output_priority_values": safety.OUTPUT_PRIORITY_VALUES,
        })

    @app.route("/api/audit")
    def api_audit():
        """Every set command this process has sent, newest first."""
        return jsonify({"ok": True, "audit": service.audit_log()})

    # ---- API: schedule + grid-export charging ----------------------------
    #
    # These two automations both drive PCP and must never run at once -- see
    # webapp/grid_charge.py's module docstring for why the time-schedule's
    # "solar hours" assumption doesn't hold on this hardware. Enabling one
    # while the other is enabled is refused outright (409) rather than
    # silently disabling the other, so nothing changes state as a surprising
    # side effect of an unrelated request.

    def _no_scheduler():
        return _fail("scheduler not configured on this server",
                     code="no_scheduler", status=501)

    def _no_grid_charge():
        return _fail("grid-export charging not configured on this server",
                     code="no_grid_charge", status=501)

    def _conflict(other_name: str):
        raise CommandRejected(
            f"cannot enable this while {other_name} is enabled -- they both "
            f"drive charger priority and would fight each other",
            hint=f'disable {other_name} first, or set grid-export charging to '
                 f'mode "override" so they can work together',
            code="conflicting_automation")

    def _check_schedule_enable(enabling: bool):
        """Ao ativar o agendamento: só há conflito se o carregamento por
        excedente estiver ativo E em modo "exclusive". Em "override" foram
        feitos para coexistir -- ver webapp/grid_charge.py MODES."""
        if not enabling or grid_charge is None:
            return
        st = grid_charge.get_state()
        if st["enabled"] and st.get("mode") == "exclusive":
            _conflict("grid-export charging")

    def _check_gridcharge_enable(enabling: bool, mode: str):
        """Ao ativar o carregamento por excedente: só há conflito se estiver
        a ser posto em modo "exclusive". `mode` é o modo PEDIDO, não o
        guardado -- olhar para o guardado dava sempre "não exclusivo" quando
        ainda está desativado, e deixava passar o conflito."""
        if not enabling or scheduler is None or mode != "exclusive":
            return
        if scheduler.get_state()["enabled"]:
            _conflict("the time-of-day schedule")

    @app.route("/api/schedule")
    def api_schedule_get():
        if scheduler is None:
            return _no_scheduler()
        return jsonify({"ok": True, **scheduler.get_state()})

    @app.route("/api/schedule", methods=["PUT"])
    @require_json_write
    def api_schedule_put():
        """Replace the whole rule set. Storing rules always succeeds even on
        a read-only server -- only *applying* them is gated, same as any
        other write, so a read-only instance can still be configured ahead
        of time."""
        if scheduler is None:
            return _no_scheduler()
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        try:
            _check_schedule_enable(enabled)
            state = scheduler.set_state(enabled, body.get("rules", []))
        except CommandRejected as e:
            return _fail(e.message, code=e.code, status=409, hint=e.hint)
        except ValueError as e:
            return _fail(str(e), code="invalid_rules", status=400)
        return jsonify({"ok": True, **state})

    @app.route("/api/schedule/apply-now", methods=["POST"])
    @require_json_write
    def api_schedule_apply_now():
        if scheduler is None:
            return _no_scheduler()
        body = request.get_json(silent=True) or {}
        result = scheduler.tick(force=bool(body.get("force", True)))
        return jsonify({"ok": True, "result": result})

    @app.route("/api/grid-charge")
    def api_gridcharge_get():
        if grid_charge is None:
            return _no_grid_charge()
        return jsonify({"ok": True, **grid_charge.get_state()})

    @app.route("/api/grid-charge", methods=["PUT"])
    @require_json_write
    def api_gridcharge_put():
        """Replace the whole config. Same read-only/store-vs-apply split as
        the schedule endpoint above."""
        if grid_charge is None:
            return _no_grid_charge()
        body = request.get_json(silent=True) or {}
        try:
            _check_gridcharge_enable(bool(body.get("enabled", False)),
                                     str(body.get("mode", "exclusive")))
            state = grid_charge.set_config(body)
        except CommandRejected as e:
            return _fail(e.message, code=e.code, status=409, hint=e.hint)
        except ValueError as e:
            return _fail(str(e), code="invalid_config", status=400)
        return jsonify({"ok": True, **state})

    @app.route("/api/grid-charge/apply-now", methods=["POST"])
    @require_json_write
    def api_gridcharge_apply_now():
        if grid_charge is None:
            return _no_grid_charge()
        body = request.get_json(silent=True) or {}
        result = grid_charge.tick(force=bool(body.get("force", True)))
        return jsonify({"ok": True, "result": result})

    # ---- API: write ----------------------------------------------------

    def _run_command(raw: str, confirm: bool):
        """Shared path for every write: policy check, send, then report."""
        info = safety.check_policy(
            raw, confirm=confirm,
            allow_writes=service.allow_writes,
            battery_voltage=service.battery_voltage() if raw.startswith("PCP") else None,
            min_battery_voltage=service.min_battery_voltage,
        )
        if info["kind"] == "query":
            return {"ok": True, "command": info["command"], "kind": "query",
                    "response": service.query(info["command"])}

        response = service.send_set(info["command"])
        acked = response.startswith("(ACK")
        result = {
            "ok": acked,
            "command": info["command"],
            "kind": "set",
            "response": response,
            "acknowledged": acked,
            "note": ("Applied. Verify via /api/status (e.g. battery_charging_current) "
                     "-- QPIRI will NOT show the change."),
        }
        if not acked:
            result["error"] = f"device did not acknowledge: {response}"
            result["code"] = "nak"
        return result

    @app.route("/api/command", methods=["POST"])
    @require_json_write
    def api_command():
        """Send any command. Queries pass straight through; set commands go
        through the policy in safety.py first."""
        body = request.get_json(silent=True) or {}
        raw = str(body.get("command", "")).strip().upper()
        confirm = bool(body.get("confirm", False))
        try:
            result = _run_command(raw, confirm)
        except CommandRejected as e:
            return _fail(e.message, code=e.code, status=409, hint=e.hint)
        except InverterError as e:
            return _fail(str(e), code="io_error", status=503)
        return jsonify(result), (200 if result["ok"] else 502)

    def _priority_endpoint(prefix: str, valid: dict, name: str):
        body = request.get_json(silent=True) or {}
        value = str(body.get("value", "")).strip()
        if value not in valid:
            return _fail(f"invalid {name} {value!r}", code="invalid_value",
                         valid=valid)
        try:
            result = _run_command(prefix + value, bool(body.get("confirm", False)))
        except CommandRejected as e:
            return _fail(e.message, code=e.code, status=409, hint=e.hint)
        except InverterError as e:
            return _fail(str(e), code="io_error", status=503)
        result["value"] = value
        result["label"] = valid[value]
        return jsonify(result), (200 if result["ok"] else 502)

    @app.route("/api/charger-priority", methods=["POST"])
    @require_json_write
    def api_charger_priority():
        """PCP. 03 (solar-only) is refused below min_battery_voltage."""
        return _priority_endpoint("PCP", safety.PCP_VALUES, "charger priority")

    @app.route("/api/output-priority", methods=["POST"])
    @require_json_write
    def api_output_priority():
        return _priority_endpoint("POP", safety.OUTPUT_PRIORITY_VALUES,
                                  "output priority")

    # ---- errors ---------------------------------------------------------

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith("/api/"):
            return _fail("no such endpoint", code="not_found", status=404)
        return redirect(url_for("dashboard"))

    @app.errorhandler(500)
    def _500(e):
        return _fail("internal error", code="internal", status=500)

    return app


def generate_token() -> str:
    return secrets.token_urlsafe(24)
