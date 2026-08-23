"""
Serial transport for the Tecnoware SolarPower inverter.

Mirrors cn/com/voltronic/solar/communicate/SerialHandler.java as
decompiled from lib/SolarPower.jar:
  - 2400 baud, 8 data bits, 1 stop bit, no parity (SerialHandler.<init>)
  - TX: raw ASCII command bytes, then a single CR (0x0D). No CRC is
    required on the request -- the reference app never appends one.
  - RX: read byte-by-byte until CR (0x0D) or a timeout, whichever first.
  - If the response is long enough and its trailing 2 bytes pass the
    protocol CRC (see protocol.py), those 2 bytes are stripped before
    the text is handed back -- some firmware echoes a CRC, some doesn't.
  - A response starting with "(NAK" means the device rejected the command.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial
import serial.tools.list_ports

from protocol import crc_bytes, strip_crc_if_valid

BAUDRATE = 2400
BYTESIZE = serial.EIGHTBITS
STOPBITS = serial.STOPBITS_ONE
PARITY = serial.PARITY_NONE
READ_TIMEOUT_S = 3.0

# Set commands behave differently from queries on this hardware, confirmed
# by testing against a real unit:
#   * they are IGNORED unless the 2-byte CRC is appended (a bare "PDa"
#     gets no reply at all, while "PDa"+CRC gets a valid "(ACK"),
#   * and the reply can take well over the 3s that queries answer within.
# Queries, by contrast, are answered fine with no CRC at all.
SET_TIMEOUT_S = 10.0

# The USB-serial adapter commonly used with these inverters shows up as a
# Prolific PL2303 (VID 0x067b, PID 0x2303). Other adapters (FTDI, CH340)
# work too -- this is just used to put a likely candidate first.
KNOWN_ADAPTER_VID_PIDS = {
    (0x067B, 0x2303): "Prolific PL2303",
    (0x0403, 0x6001): "FTDI FT232",
    (0x1A86, 0x7523): "CH340",
}


@dataclass
class PortCandidate:
    device: str
    description: str
    likely: bool


def list_candidate_ports() -> list[PortCandidate]:
    candidates = []
    for p in serial.tools.list_ports.comports():
        likely = (p.vid, p.pid) in KNOWN_ADAPTER_VID_PIDS
        candidates.append(PortCandidate(p.device, p.description or "", likely))
    candidates.sort(key=lambda c: not c.likely)
    return candidates


class InverterError(Exception):
    pass


class InverterConnection:
    def __init__(self, port: str, timeout: float = READ_TIMEOUT_S):
        self.ser = serial.Serial(
            port=port,
            baudrate=BAUDRATE,
            bytesize=BYTESIZE,
            stopbits=STOPBITS,
            parity=PARITY,
            timeout=timeout,
        )
        # give the adapter a moment after opening, mirroring real-world
        # USB-serial enumeration delay
        time.sleep(0.2)
        self._clear_buffer()

    def _clear_buffer(self) -> None:
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "InverterConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def send_raw(self, command: str, append_crc: bool = False,
                  wait: float | None = None) -> str:
        """Send a command as SerialHandler.excuteCommand() does: write the
        command bytes, optionally the 2-byte CRC, then CR, then read until
        CR/timeout. See SET_TIMEOUT_S for why set commands need append_crc."""
        self._clear_buffer()
        payload = command.encode("ascii", errors="replace")
        if append_crc:
            payload += crc_bytes(command)
        self.ser.write(payload + b"\x0d")
        self.ser.flush()

        chars: list[str] = []
        deadline = time.monotonic() + (wait if wait is not None else self.ser.timeout)
        while time.monotonic() < deadline:
            byte = self.ser.read(1)
            if not byte:
                continue
            if byte == b"\x0d":
                break
            chars.append(byte.decode("latin1"))

        response = "".join(chars)
        text, had_crc = strip_crc_if_valid(response)
        return text

    def send_set_command(self, command: str) -> str:
        """Send a state-changing command, with the CRC and longer timeout
        this hardware requires. Returns the raw reply ("(ACK" / "(NAK").

        An "(ACK" does mean the setting was applied -- but do NOT try to
        confirm it by re-reading QPIRI, which reports static rated values
        and never changes. Confirm via QPIGS behaviour instead (e.g. a
        charger-priority change shows up in battery_charging_current)."""
        response = self.send_raw(command, append_crc=True, wait=SET_TIMEOUT_S)
        if not response:
            raise InverterError(f"no response to set command {command!r} (timeout)")
        return response

    def query(self, command: str) -> str:
        response = self.send_raw(command)
        if not response:
            raise InverterError(f"no response to {command!r} (timeout)")
        if response.startswith("(NAK"):
            raise InverterError(f"device rejected {command!r}: NAK")
        return response
