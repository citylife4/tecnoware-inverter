"""
Named-field parsing for the most useful queries.

QPI on the actual connected unit replied "(PI30" -- this is Voltronic's
public, widely-documented "PI30" protocol (used by many inverter brands:
Axpert, PowMr, EASUN, MPP Solar, and this Tecnoware SolarPower unit among
them). The field layouts below are that well-known PI30 field order, and
were cross-checked against this specific inverter's real QPIGS/QPIRI
output (e.g. BUS voltage ~400V, battery ~27V on a 24V bank, PV fields at
0 when no sun on the panels -- all consistent).

If your unit is a different Tecnoware model (P15/P17/P18/P175K/...), QPI
may reply with a different string (e.g. "(PI18"), and the field layout
could differ -- always sanity-check against the indexed field dump the
CLI prints alongside the parsed view.

Known imperfection: on the unit this was tested against, QPIRI's
max_charging_current field (index 14) came back as e.g. "06P" instead of
a plain number -- likely a parallel-operation-mode suffix letter, since
the app bundles parallel-unit support (cn/com/voltronic/solar/
comusbprocessor/ParallSubProcessor.class). Don't trust that one field
blindly; the raw indexed dump is printed alongside the named view for
exactly this reason.
"""

from __future__ import annotations

QPIGS_FIELDS = [
    ("grid_voltage", "V"),
    ("grid_frequency", "Hz"),
    ("ac_output_voltage", "V"),
    ("ac_output_frequency", "Hz"),
    ("ac_output_apparent_power", "VA"),
    ("ac_output_active_power", "W"),
    ("output_load_percent", "%"),
    ("bus_voltage", "V"),
    ("battery_voltage", "V"),
    ("battery_charging_current", "A"),
    ("battery_capacity", "%"),
    ("heatsink_temperature", "C"),
    ("pv_input_current", "A"),
    ("pv_input_voltage", "V"),
    ("battery_voltage_from_scc", "V"),
    ("battery_discharge_current", "A"),
    ("device_status_flags", ""),  # 8-bit field, see decode_device_status_flags
    ("battery_voltage_offset_for_fans", "10mV"),
    ("eeprom_version", ""),
    ("pv_charging_power", "W"),
    ("device_status2_flags", ""),
]

QPIRI_FIELDS = [
    ("ac_input_voltage", "V"),
    ("ac_input_current", "A"),
    ("ac_output_voltage", "V"),
    ("ac_output_frequency", "Hz"),
    ("ac_output_current", "A"),
    ("ac_output_apparent_power", "VA"),
    ("ac_output_active_power", "W"),
    ("battery_voltage", "V"),
    # The four battery setpoints below are reported PER 12V BLOCK, not as
    # absolute pack volts. On this 24V unit multiply by 2 for the real
    # target. Confirmed on hardware: float reads 13.5 and the pack floats
    # at exactly 27.00V (13.5 x 2). See BATTERY_SETPOINT_SCALE below.
    ("battery_recharge_voltage", "V/12Vblk"),
    ("battery_under_voltage", "V/12Vblk"),
    ("battery_bulk_voltage", "V/12Vblk"),
    ("battery_float_voltage", "V/12Vblk"),
    ("battery_type", "code"),
    ("max_ac_charging_current", "A"),
    ("max_charging_current", "A"),
    ("input_voltage_range", "code"),
    ("output_source_priority", "code"),
    ("charger_source_priority", "code"),
    ("parallel_max_num", ""),
    ("machine_type", "code"),
    ("topology", "code"),
    ("output_mode", "code"),
    ("battery_redischarge_voltage", "V"),
    ("pv_ok_condition", "code"),
    ("pv_power_balance", "code"),
]

DEVICE_STATUS_BITS = [
    "b0_add_sbu_priority_version",
    "b1_configuration_status_changed",
    "b2_scc_firmware_updated",
    "b3_load_on",
    "b4_battery_voltage_to_steady_while_charging",
    "b5_charging_status_scc_or_ac",
    "b6_charging_status_scc",
    "b7_charging_status_ac",
]


# QPIRI battery setpoints are given per 12V block; multiply by this to get
# the real pack-level target. 2 for a 24V bank, 4 for a 48V bank.
BATTERY_SETPOINT_SCALE = 2

_SCALED_SETPOINTS = {
    "battery_recharge_voltage", "battery_under_voltage",
    "battery_bulk_voltage", "battery_float_voltage",
}

# PI30 battery type codes (field 12). 2 = user-defined, meaning the
# setpoints above were configured by hand rather than by a factory preset.
BATTERY_TYPES = {"0": "AGM", "1": "Flooded", "2": "User-defined"}


def parse_fields(response: str, field_defs: list[tuple[str, str]]) -> dict:
    body = response[1:] if response.startswith("(") else response
    parts = body.split(" ")
    result = {}
    for i, (name, unit) in enumerate(field_defs):
        if i >= len(parts):
            break
        result[name] = (parts[i], unit)
    if len(parts) > len(field_defs):
        result["_extra_fields"] = parts[len(field_defs):]
    return result


def decode_device_status_flags(bits: str) -> list[str]:
    """bits: the 8-char '0'/'1' string from QPIGS field 16."""
    active = []
    for ch, name in zip(bits, DEVICE_STATUS_BITS):
        if ch == "1":
            active.append(name)
    return active


def print_parsed(response: str, field_defs: list[tuple[str, str]]) -> None:
    parsed = parse_fields(response, field_defs)
    extra = parsed.pop("_extra_fields", None)
    for name, (value, unit) in parsed.items():
        unit_str = f" {unit}" if unit else ""
        note = ""
        if name in _SCALED_SETPOINTS:
            try:
                note = f"   -> {float(value) * BATTERY_SETPOINT_SCALE:.1f} V actual"
            except ValueError:
                pass
        elif name == "battery_type":
            note = f"   ({BATTERY_TYPES.get(value, 'unknown')})"
        print(f"    {name:32} {value}{unit_str}{note}")
    if extra:
        print(f"    (unparsed trailing fields: {extra})")
