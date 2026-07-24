#!/usr/bin/env python3
"""Serial input setup.

The one thing worth pinning here is _force_baudrate: on a Pi built-in UART the first
open after a boot leaves the hardware at 9600 unless the baud is bounced through a
different value. Collapsing the bounce to a single assignment silently reintroduces
that (pyserial skips an unchanged value), so this asserts the shape rather than the
side effect. See _force_baudrate's docstring / fastnet2n2k's
docs/uart_first_open_baud_fix.md.
"""
import unittest

from fastnet2ip.core.input import BAUDRATE, _force_baudrate


class _BaudRecorder:
    """Records baudrate assignments, like pyserial's property does."""

    def __init__(self):
        self.assignments = []

    @property
    def baudrate(self):
        return self.assignments[-1] if self.assignments else None

    @baudrate.setter
    def baudrate(self, value):
        self.assignments.append(value)


class TestForceBaudrate(unittest.TestCase):
    def test_bounces_through_a_different_rate_and_ends_at_the_target(self):
        ser = _BaudRecorder()
        _force_baudrate(ser)
        self.assertEqual(len(ser.assignments), 2,
                         "must be a bounce, not a single assignment")
        self.assertNotEqual(ser.assignments[0], BAUDRATE,
                            "first step must actually change CBAUD")
        self.assertEqual(ser.assignments[-1], BAUDRATE,
                         "must end at the Fastnet line rate")


if __name__ == "__main__":
    unittest.main()
