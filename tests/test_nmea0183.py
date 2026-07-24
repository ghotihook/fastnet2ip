"""Tests for the NMEA 0183 output handler (pyfastnet v3 Signal K input).

Inputs are fed as SI values keyed by Signal K path (as pyfastnet v3 emits); the
handler converts back to the 0183 display units, so the asserted sentence strings
(knots, degrees) are unchanged from v2.
"""
import math
import unittest

from fastnet2ip.core import data_store
from fastnet2ip.core.data_store import update_live_data
from fastnet2ip.handlers import nmea0183

KN_MS = 0.514444


def _set(path, value):
    update_live_data(path, value)


def _rad(deg):
    return math.radians(deg)


def _kn(knots):
    return knots * KN_MS


class TestImport(unittest.TestCase):
    def test_module_loads(self):
        self.assertTrue(callable(nmea0183.process_vhw))
        self.assertTrue(callable(nmea0183.process_mwv_apparent))
        self.assertIn("navigation.headingMagnetic", nmea0183._TRIGGER_MAP)


# ── HDM / HDT ─────────────────────────────────────────────────────────────────

class TestHDM(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_magnetic_emits_hdm(self):
        _set("navigation.headingMagnetic", _rad(180.0))
        result = nmea0183.process_hdm()
        self.assertIsNotNone(result)
        self.assertIn("IIHDM", result)
        self.assertIn("180.0,M", result)
        self.assertNotIn("HDT", result)

    def test_true_emits_hdt(self):
        _set("navigation.headingTrue", _rad(180.0))
        result = nmea0183.process_hdm()
        self.assertIsNotNone(result)
        self.assertIn("IIHDT", result)
        self.assertIn("180.0,T", result)
        self.assertNotIn("HDM", result)

    def test_no_data_returns_none(self):
        self.assertIsNone(nmea0183.process_hdm())


# ── Send cadence ──────────────────────────────────────────────────────────────

class _FakeSocket:
    def __init__(self):
        self.sends = 0

    def sendto(self, *_):
        self.sends += 1


class TestSendCadence(unittest.TestCase):
    """The bridge reflects what the instruments provide: a repeated (unchanged) value
    is still sent, capped only by MIN_SEND_INTERVAL. No dedupe, no re-broadcast timer —
    matching fastnet2n2k. Guards against reintroducing either."""

    def setUp(self):
        data_store.live_data.clear()
        self.h = nmea0183.NMEA0183Handler()
        self.h._host, self.h._port = "127.0.0.1", 2002
        self.sock = _FakeSocket()

    def test_repeated_value_is_still_sent(self):
        import time
        _set("navigation.speedThroughWater", _kn(6.0))
        self.h.process_channel("navigation.speedThroughWater", self.sock)
        time.sleep(nmea0183.MIN_SEND_INTERVAL * 1.2)      # clear the rate cap
        _set("navigation.speedThroughWater", _kn(6.0))    # identical value
        self.h.process_channel("navigation.speedThroughWater", self.sock)
        self.assertEqual(self.sock.sends, 2)

    def test_rate_cap_still_applies(self):
        _set("navigation.speedThroughWater", _kn(6.0))
        self.h.process_channel("navigation.speedThroughWater", self.sock)
        self.h.process_channel("navigation.speedThroughWater", self.sock)  # within cap
        self.assertEqual(self.sock.sends, 1)


# ── VHW ──────────────────────────────────────────────────────────────────────

class TestVHW(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_magnetic_heading_in_mag_field(self):
        _set("navigation.headingMagnetic", _rad(180.0))
        _set("navigation.speedThroughWater", _kn(5.0))
        result = nmea0183.process_vhw()
        self.assertIn("180.0,M", result)
        self.assertIn("5.0,N", result)
        self.assertTrue(result.startswith("$IIVHW,,,"))   # True field empty

    def test_true_heading_in_true_field(self):
        _set("navigation.headingTrue", _rad(180.0))
        _set("navigation.speedThroughWater", _kn(5.0))
        result = nmea0183.process_vhw()
        self.assertIn("180.0,T", result)
        self.assertIn("5.0,N", result)
        self.assertIn("180.0,T,,,", result)              # Mag field empty


# ── MWD ──────────────────────────────────────────────────────────────────────

class TestMWD(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_direction_in_true_field(self):
        _set("environment.wind.directionTrue", _rad(180.0))
        _set("environment.wind.speedTrue", _kn(12.0))
        result = nmea0183.process_mwd()
        self.assertIn("180.0,T", result)
        self.assertIn("12.0,N", result)
        self.assertIn("180.0,T,,,", result)

    def test_magnetic_direction_in_mag_field(self):
        _set("environment.wind.directionMagnetic", _rad(180.0))
        _set("environment.wind.speedTrue", _kn(12.0))
        result = nmea0183.process_mwd()
        self.assertIn("180.0,M", result)
        self.assertIn("12.0,N", result)
        self.assertIn(",,,180.0,M,", result)


# ── MWV ──────────────────────────────────────────────────────────────────────

class TestMWV(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_wind(self):
        _set("environment.wind.angleTrueWater", _rad(45.0))
        _set("environment.wind.speedTrue", _kn(12.0))
        result = nmea0183.process_mwv_true()
        self.assertIn("IIMWV", result)
        self.assertIn("45.0,T,12.0,N,A", result)

    def test_apparent_wind(self):
        _set("environment.wind.angleApparent", _rad(30.0))
        _set("environment.wind.speedApparent", _kn(8.0))
        result = nmea0183.process_mwv_apparent()
        self.assertIn("IIMWV", result)
        self.assertIn("30.0,R,8.0,N,A", result)

    def test_negative_twa_normalised(self):
        _set("environment.wind.angleTrueWater", _rad(-45.0))
        _set("environment.wind.speedTrue", _kn(10.0))
        result = nmea0183.process_mwv_true()
        self.assertIn("315.0,T", result)

    def test_negative_awa_normalised(self):
        _set("environment.wind.angleApparent", _rad(-30.0))
        _set("environment.wind.speedApparent", _kn(8.0))
        result = nmea0183.process_mwv_apparent()
        self.assertIn("330.0,R", result)


# ── VTG ──────────────────────────────────────────────────────────────────────

class TestVTG(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_cog(self):
        _set("navigation.courseOverGroundTrue", _rad(270.0))
        _set("navigation.speedOverGround", _kn(5.0))
        result = nmea0183.process_vtg()
        self.assertIn("270.0,T", result)
        self.assertIn("5.0,N", result)
        self.assertIn("270.0,T,,,", result)

    def test_magnetic_cog(self):
        _set("navigation.courseOverGroundMagnetic", _rad(272.0))
        _set("navigation.speedOverGround", _kn(5.0))
        result = nmea0183.process_vtg()
        self.assertIn("272.0,M", result)
        self.assertIn("5.0,N", result)
        self.assertIn(",,,272.0,M,", result)

    def test_both_cog(self):
        _set("navigation.courseOverGroundTrue", _rad(270.0))
        _set("navigation.courseOverGroundMagnetic", _rad(272.0))
        _set("navigation.speedOverGround", _kn(5.0))
        result = nmea0183.process_vtg()
        self.assertIn("270.0,T", result)
        self.assertIn("272.0,M", result)


# ── VDR ──────────────────────────────────────────────────────────────────────

class TestVDR(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_set(self):
        _set("environment.current.setTrue", _rad(45.0))
        _set("environment.current.drift", _kn(0.5))
        result = nmea0183.process_vdr()
        self.assertIn("45.0,T", result)
        self.assertIn("0.50,N", result)
        self.assertIn("45.0,T,,,", result)

    def test_magnetic_set(self):
        _set("environment.current.setMagnetic", _rad(45.0))
        _set("environment.current.drift", _kn(0.5))
        result = nmea0183.process_vdr()
        self.assertIn("45.0,M", result)
        self.assertIn("0.50,N", result)
        self.assertIn(",,,45.0,M,", result)


# ── MDA temperature / pressure ────────────────────────────────────────────────

class TestMDA(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_celsius_temps(self):
        _set("environment.outside.temperature", 20.0 + 273.15)
        _set("environment.water.temperature", 18.5 + 273.15)
        _set("environment.outside.pressure", 1013.0 * 100)
        result = nmea0183.process_mda()
        self.assertIn("20.0,C", result)
        self.assertIn("18.5,C", result)

    def test_pressure_only(self):
        _set("environment.outside.pressure", 1013.0 * 100)
        result = nmea0183.process_mda()
        self.assertIn("IIMDA", result)
        self.assertIn(",,,,", result)                    # temp fields empty


if __name__ == "__main__":
    unittest.main(verbosity=2)
