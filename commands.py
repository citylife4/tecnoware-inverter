"""
Command tables for the Voltronic / Tecnoware SolarPower inverter protocol.

Extracted directly from the constant pool of
cn/com/voltronic/solar/constants/Command.class (decompiled from the
bundled lib/SolarPower.jar). These are the literal command strings the
official Windows app sends -- nothing here is guessed.

Query commands (QXXX) are safe/read-only. SET_COMMANDS change inverter
behaviour and should be sent with care -- the app confirms with the user
before issuing any of these interactively.
"""

# Read-only "Q..." query commands, plus a couple of bare identification
# commands ("I", "T", "ID") that don't start with Q.
QUERY_COMMANDS = {
    "QPI": "Protocol ID (device replies e.g. '(PI16')",
    "QID": "Device serial number",
    "QVFW": "Main CPU firmware version",
    "QVFW2": "Secondary CPU firmware version",
    "QMD": "Device model",
    "QDM": "Device model (alt.)",
    "QPIRI": "Device rating information",
    "QVFTR": "Voltage/frequency transfer ranges",
    "QPIHF": "Home-load flags",
    "QPICF": "Charge flags",
    "QSVFW2": "Secondary CPU firmware version (SEC)",
    "QPIGS": "General status (voltages/currents/power/battery/PV)",
    "Q3GV": "3-phase grid voltage",
    "Q3GC": "3-phase grid current",
    "Q3GW": "3-phase grid wattage",
    "Q3AV": "3-phase AC output voltage",
    "Q3AC": "3-phase AC output current",
    "Q3AL": "3-phase AC output load",
    "Q3AW": "3-phase AC output wattage",
    "QMOD": "Current working mode",
    "QPIWS": "Warning/fault status bitfield",
    "QPIFS": "Fault status",
    "QSPIFS": "Fault status (SEC)",
    "QTPIFS": "Fault status before trip",
    "QT": "Device date/time",
    "QFLAG": "Enabled/disabled flags",
    "QFET": "Fault event",
    "QEY": "Energy generated this year",
    "QEM": "Energy generated this month",
    "QED": "Energy generated today",
    "QEH": "Energy generated this hour",
    "QGOV": "Grid output voltage",
    "QGOF": "Grid output frequency",
    "QOPMP": "Output mode / parallel info",
    "QMPPTV": "MPPT voltage",
    "QPVIPV": "PV input voltage",
    "QLST": "Load status",
    "QTPR": "Temperature",
    "QDI": "Default settings",
    "QDI2": "Default settings (alt.)",
    "QSTS": "Status",
    "QGLTV": "Grid loss threshold voltage",
    "QADI": "AC device info",
    "QVB": "Battery voltage",
    "QCHGC": "Max charging current setting",
    "QMCC": "Max utility charging current",
    "QII": "Inverter info",
    "QPIBI": "Battery info",
    "QCHGS": "Charging status",
    "QBSDV": "Battery start discharge voltage",
    "QPINBI": "Battery info (alt.)",
    "QFT": "Float voltage",
    "I": "CPU version info (contains 'DSP:' / 'MCU:' blobs)",
    "T": "Current time",
    "ID": "Identification",
}

# Commands that change device behaviour or settings. Most take a parameter
# appended directly to the command (e.g. "POP00", "PCP02"); a few are
# stand-alone toggles. Kept as raw strings/prefixes exactly as found in the
# decompiled Command class.
SET_COMMANDS = {
    "SON": "Turn output ON",
    "SOFF": "Turn output OFF",
    "TN": "Toggle (unspecified)",
    "CT": "Set clock/time (append value)",
    "BZOFF": "Buzzer OFF",
    "BZON": "Buzzer ON",
    "SN": "Set (unspecified)",
    "S": "Set (unspecified)",
    "SNRM": "Set normal mode",
    "CS": "Charge source setting (append value)",
    "TL": "Transfer low voltage (append value)",
    "ST": "Set time (append value)",
    "EPO": "Emergency power off",
    "DPO": "Disable power off",
    "CLR": "Clear fault/history",
    "FGE": "Enable function (append flag code)",
    "FGD": "Disable function (append flag code)",
    "CHTH": "Charge threshold high (append value)",
    "CHTL": "Charge threshold low (append value)",
    "PE": "Enable a function flag (append flag code)",
    "PD": "Disable a function flag (append flag code)",
    "BATN": "Battery number (append value)",
    "BATCN": "Battery cell number (append value)",
    "CHGC": "Set max charging current (append value)",
    "DAT": "Set date/time (append value)",
    "PSF": "Set output source priority family (append value)",
    "PGF": "Set input voltage range family (append value)",
    "PLV": "Set low voltage point (append value)",
    "PHV": "Set high voltage point (append value)",
    "GOLF": "Grid output low frequency (append value)",
    "GOHF": "Grid output high frequency (append value)",
    "GOLV": "Grid output low voltage (append value)",
    "GOHV": "Grid output high voltage (append value)",
    "OPMP": "Set output mode / parallel (append value)",
    "MPPTHV": "MPPT high voltage (append value)",
    "MPPTLV": "MPPT low voltage (append value)",
    "PVIPHV": "PV input high voltage (append value)",
    "PVIPLV": "PV input low voltage (append value)",
    "LST": "Set load status (append value)",
    "PF": "Set output priority (append value)",
    "GLTHV": "Grid loss threshold high voltage (append value)",
    "GLTLV": "Grid loss threshold low voltage (append value)",
    "GORV": "Grid output rated voltage (append value)",
    "GORF": "Grid output rated frequency (append value)",
    "V": "Set voltage (append value)",
    "MCHGC": "Set max total charging current (append value)",
    "MCHGV": "Set max charging voltage (append value)",
    "BCHGV": "Set battery charge voltage (append value)",
    "BSDV": "Battery start discharge voltage (append value)",
    "F50": "Set output frequency to 50Hz",
    "F60": "Set output frequency to 60Hz",
    "ID": "Set device ID (append value)",
    "REEP": "Restore/reset EEPROM defaults",
    "VB": "Set battery voltage (append value)",
    "POP": "Set output source priority: 00=Utility 01=Solar 02=SBU (append 2-digit code)",
    "PCP": "Set charger source priority (append 2-digit code)",
    "PBCV": "Set battery re-charge voltage (append value)",
    "PBDV": "Set battery re-discharge voltage (append value)",
    "PGP": "Set grid working range (append value)",
    "PPCP": "Set parallel charger source priority (append value)",
}

# Commands referenced in Command.class that are less common / model-specific.
# Included for completeness; semantics are inferred from naming only.
OTHER_COMMANDS = [
    "AUTO", "DSPAR", "MCUAR", "FT", "DM", "PVN", "PPS", "QPPS", "SOPF",
    "QOPF", "PDG", "QPDG", "PPD", "QPPD", "PFL", "QPFL", "GPMP", "DSUBV",
    "OFFC", "QGPMP", "QPRIO", "QENF", "QPKT", "QOFFC", "QLDT", "GNTMQ",
    "GNTM", "EO1", "LBF", "QEBGP", "BDRWF", "QACCV", "ABGP", "QACCHC",
    "ACCHC", "QMDCC", "SMDCC",
]

ALL_COMMANDS = {**QUERY_COMMANDS, **SET_COMMANDS}

# Verified by sweeping every query against a real unit (firmware
# QVFW 00072.40, QPI "(PI30"). Use `inverter_ctl.py --scan` to regenerate
# this for your own hardware -- other Tecnoware models will differ.
VERIFIED_WORKING_QUERIES = [
    "QPI", "QID", "QVFW", "QVFW2", "QMOD", "QFLAG",
    "QPIGS", "QPIRI", "QPIWS", "QPVIPV", "QDI", "QDI2",
]

# These return "(PI30" -- i.e. the firmware prefix-matches "QPI" and
# ignores the remaining characters. They are NOT actually supported, and
# would otherwise look like they work.
FALSE_POSITIVE_QUERIES = ["QPIBI", "QPICF", "QPIFS", "QPIHF", "QPINBI"]

# Set commands the firmware *recognises* (they return a CRC-valid "(ACK")
# as opposed to ones it doesn't know at all (which return nothing).
# NOTE: on the test unit, even the recognised ones do not actually apply --
# see README. Recognition is not the same as taking effect.
RECOGNISED_SET_COMMANDS = ["POP", "PCP", "PBT", "PSDV", "PBCV", "PGR", "PE", "PD"]
