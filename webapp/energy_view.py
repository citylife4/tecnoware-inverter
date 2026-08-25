"""
"Where is the power coming from right now?" -- assembled from two
instruments that each see half of it.

Neither source alone answers the question:

  * The inverter knows which way its transfer relay is thrown (QMOD) and, in
    battery mode, what the protected output is drawing. In bypass it does
    not: ac_output_active_power floors at 1 W below some threshold, which
    covers every ordinary load in this house.
  * Shelly channel 1 measures what goes INTO the inverter from the grid.
    In bypass that is the protected load plus the charger plus the
    inverter's own overhead -- so it answers the question only when the
    charger is idle.

So the load is reported with its provenance attached, and reported as
unknown when it genuinely is. The one thing this must never do is print a
number that looks measured when it was inferred from a field already known
to be wrong -- that mistake has been made three times on this installation
already (see CLAUDE.md gotcha #3).

Why battery_charging_current gates the bypass estimate: subtracting the
charger's draw would be the obvious way to recover the load while charging,
but that field is not trustworthy at low current. Measured 2026-08-25 over
843 samples, it read a flat 4.0 A at 27.0 V -- 108 W -- in 10-minute windows
where channel 1 measured only 72 W going into the whole inverter. A charger
cannot draw more than the appliance containing it. Above the float range it
may well be accurate, but that has never been observed here, so the estimate
is offered only when the charger is off.
"""

from __future__ import annotations

# ac_output_active_power never reports below this, so a reading equal to it
# means "less than this", not "this much".
OUTPUT_FLOOR_W = 1

GRID = "grid"
BATTERY = "battery"


def _num(value):
    return value if isinstance(value, (int, float)) else None


def describe_energy(status, live=None) -> dict:
    """status: the parsed QPIGS dict (service.latest()["status"]).
    live: the auto-energy `latest` payload, or None if unavailable.

    Returns the source of the protected output, the load with its
    provenance, and the house-side split. Every numeric field may be None,
    which means "not known" and must be rendered as such.
    """
    status = status or {}
    live = live or {}

    mode = status.get("mode")
    charging_a = _num(status.get("battery_charging_current"))
    inverter_in = _num(live.get("inverter_input_w"))
    out_w = _num(status.get("ac_output_active_power"))

    if mode == "B":
        source = BATTERY
    elif mode == "L":
        source = GRID
    else:
        source = None

    load_w = None
    load_from = None
    load_note = None

    if source == BATTERY:
        # Proven on hardware 2026-08-25: read 46 W for the fridge, matching
        # the Shelly's independent 45-46 W for the same appliance.
        if out_w is not None and out_w > OUTPUT_FLOOR_W:
            load_w, load_from = out_w, "inverter"
        elif out_w is not None:
            load_from, load_note = "inverter", "abaixo do limiar de medição"
    elif source == GRID:
        if charging_a is not None and charging_a > 0:
            load_note = "carregador ativo — não é possível separar as cargas"
        elif inverter_in is not None:
            # No charging, so everything entering the inverter is the
            # protected load plus a small standby overhead (2.3 W measured
            # with the loads on battery).
            load_w, load_from = inverter_in, "shelly"
            load_note = "inclui o consumo próprio do inversor"
        else:
            load_note = "sem leitura do contador"

    return {
        "output_source": source,
        "output_load_w": load_w,
        "output_load_from": load_from,
        "output_load_note": load_note,
        "charging_w": (None if charging_a is None
                       else round(charging_a * (_num(status.get("battery_voltage")) or 0))),
        "charging_trusted": bool(charging_a) and charging_a > 5,
        "solar_w": _num(live.get("ac_solar_w")),
        "house_w": _num(live.get("house_power_w")),
        "grid_w": _num(live.get("net_balance")),
        "inverter_input_w": inverter_in,
    }
