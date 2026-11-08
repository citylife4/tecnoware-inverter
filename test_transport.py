"""The serial write side must be bounded (2026-11-08).

Until then ser.write() had no timeout and ser.flush() (tcdrain) none at all;
a wedged USB adapter could block a command forever while its caller held the
serial and controller locks -- the signature of every recorded stall.
"""
import time
import unittest
from unittest import mock

import serial

import transport
from transport import InverterConnection, InverterError


class FakeSerial:
    def __init__(self, reply=b"(ACK9 \r", write_exc=None, stuck_out=False):
        self.timeout = 0.2
        self._reply = list(reply)
        self._write_exc = write_exc
        self._stuck_out = stuck_out
        self.written = b""

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def write(self, data):
        if self._write_exc:
            raise self._write_exc
        self.written += data
        return len(data)

    def flush(self):
        raise AssertionError("flush() is unbounded and must not be called")

    @property
    def out_waiting(self):
        return 5 if self._stuck_out else 0

    def read(self, n):
        return bytes([self._reply.pop(0)]) if self._reply else b""


def conn_with(fake):
    c = InverterConnection.__new__(InverterConnection)
    c.ser = fake
    return c


class TestBoundedWrite(unittest.TestCase):
    def test_normal_command_still_works(self):
        c = conn_with(FakeSerial(reply=b"(PI30\r"))
        self.assertEqual(c.send_raw("QPI"), "(PI30")

    def test_write_timeout_becomes_an_inverter_error(self):
        c = conn_with(FakeSerial(write_exc=serial.SerialTimeoutException("wedged")))
        with self.assertRaises(InverterError):
            c.send_raw("QPI")

    def test_a_buffer_that_never_drains_gives_up(self):
        c = conn_with(FakeSerial(stuck_out=True))
        with mock.patch.object(transport, "WRITE_TIMEOUT_S", 0.2):
            t0 = time.monotonic()
            with self.assertRaises(InverterError):
                c.send_raw("QPI")
            self.assertLess(time.monotonic() - t0, 2.0)

    def test_the_port_is_opened_with_a_write_timeout(self):
        with mock.patch.object(transport.serial, "Serial") as ser, \
                mock.patch.object(transport.time, "sleep"):
            InverterConnection("/dev/null")
        self.assertEqual(ser.call_args.kwargs.get("write_timeout"),
                         transport.WRITE_TIMEOUT_S)


if __name__ == "__main__":
    unittest.main()
