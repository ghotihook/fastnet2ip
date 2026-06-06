"""Tests for the NMEA 0183 output handler."""
import unittest

from fastnet2ip.core import data_store
from fastnet2ip.core.data_store import update_live_data
from fastnet2ip.handlers import nmea0183


def _set(*args):
    """update_live_data shorthand: channel, value, layout (display_text derived)."""
    channel, value, layout = args
    update_live_data(channel, "0x00", value, f"{value}{layout or ''}", layout)


class TestImport(unittest.TestCase):
    def test_module_loads(self):
        self.assertTrue(callable(nmea0183.process_vhw))
        self.assertTrue(callable(nmea0183.process_mwv_apparent))
        self.assertIn("Heading", nmea0183._TRIGGER_MAP)


# ── HDM / HDT ─────────────────────────────────────────────────────────────────

class TestHDM(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_magnetic_emits_hdm(self):
        _set("Heading", 180.0, "°M")
        result = nmea0183.process_hdm()
        self.assertIsNotNone(result)
        self.assertIn("IIHDM", result)
        self.assertIn("180.0,M", result)
        self.assertNotIn("HDT", result)

    def test_true_emits_hdt(self):
        _set("Heading", 180.0, "°T")
        result = nmea0183.process_hdm()
        self.assertIsNotNone(result)
        self.assertIn("IIHDT", result)
        self.assertIn("180.0,T", result)
        self.assertNotIn("HDM", result)

    def test_bad_layout_returns_none(self):
        _set("Heading", 180.0, "?")
        self.assertIsNone(nmea0183.process_hdm())

    def test_no_data_returns_sentence_with_empty_value(self):
        result = nmea0183.process_hdm()
        self.assertIsNone(result)


# ── VHW ──────────────────────────────────────────────────────────────────────

class TestVHW(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_magnetic_heading_in_mag_field(self):
        _set("Heading", 180.0, "°M")
        _set("Boatspeed (Knots)", 5.0, None)
        result = nmea0183.process_vhw()
        self.assertIn("180.0,M", result)
        self.assertIn("5.0,N", result)
        # True field should be empty
        self.assertTrue(result.startswith("$IIVHW,,,"))

    def test_true_heading_in_true_field(self):
        _set("Heading", 180.0, "°T")
        _set("Boatspeed (Knots)", 5.0, None)
        result = nmea0183.process_vhw()
        self.assertIn("180.0,T", result)
        self.assertIn("5.0,N", result)
        # Mag field should be empty: ,T,,, pattern
        self.assertIn("180.0,T,,,", result)


# ── MWD ──────────────────────────────────────────────────────────────────────

class TestMWD(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_direction_in_true_field(self):
        _set("True Wind Direction", 180.0, "°T")
        _set("True Wind Speed (Knots)", 12.0, None)
        result = nmea0183.process_mwd()
        self.assertIn("180.0,T", result)
        self.assertIn("12.0,N", result)
        # Mag direction field should be empty
        self.assertIn("180.0,T,,,", result)

    def test_magnetic_direction_in_mag_field(self):
        _set("True Wind Direction", 180.0, "°M")
        _set("True Wind Speed (Knots)", 12.0, None)
        result = nmea0183.process_mwd()
        self.assertIn("180.0,M", result)
        self.assertIn("12.0,N", result)
        # True direction field should be empty
        self.assertIn(",,,180.0,M,", result)


# ── MWV ──────────────────────────────────────────────────────────────────────

class TestMWV(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_wind(self):
        _set("True Wind Angle", 45.0, None)
        _set("True Wind Speed (Knots)", 12.0, None)
        result = nmea0183.process_mwv_true()
        self.assertIn("IIMWV", result)
        self.assertIn("45.0,T,12.0,N,A", result)

    def test_apparent_wind(self):
        _set("Apparent Wind Angle", 30.0, None)
        _set("Apparent Wind Speed (Knots)", 8.0, None)
        result = nmea0183.process_mwv_apparent()
        self.assertIn("IIMWV", result)
        self.assertIn("30.0,R,8.0,N,A", result)

    def test_negative_twa_normalised(self):
        _set("True Wind Angle", -45.0, None)
        _set("True Wind Speed (Knots)", 10.0, None)
        result = nmea0183.process_mwv_true()
        self.assertIn("315.0,T", result)

    def test_negative_awa_normalised(self):
        _set("Apparent Wind Angle", -30.0, None)
        _set("Apparent Wind Speed (Knots)", 8.0, None)
        result = nmea0183.process_mwv_apparent()
        self.assertIn("330.0,R", result)


# ── VTG ──────────────────────────────────────────────────────────────────────

class TestVTG(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_cog(self):
        _set("Course Over Ground (True)", 270.0, None)
        _set("Speed Over Ground", 5.0, None)
        result = nmea0183.process_vtg()
        self.assertIn("270.0,T", result)
        self.assertIn("5.0,N", result)
        # Mag COG field should be empty
        self.assertIn("270.0,T,,,", result)

    def test_magnetic_cog(self):
        _set("Course Over Ground (Mag)", 272.0, None)
        _set("Speed Over Ground", 5.0, None)
        result = nmea0183.process_vtg()
        self.assertIn("272.0,M", result)
        self.assertIn("5.0,N", result)
        # True COG field should be empty
        self.assertIn(",,,272.0,M,", result)

    def test_both_cog(self):
        _set("Course Over Ground (True)", 270.0, None)
        _set("Course Over Ground (Mag)", 272.0, None)
        _set("Speed Over Ground", 5.0, None)
        result = nmea0183.process_vtg()
        self.assertIn("270.0,T", result)
        self.assertIn("272.0,M", result)


# ── VDR ──────────────────────────────────────────────────────────────────────

class TestVDR(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_true_set(self):
        _set("Tidal Set", 45.0, "°T")
        _set("Tidal Drift", 0.5, None)
        result = nmea0183.process_vdr()
        self.assertIn("45.0,T", result)
        self.assertIn("0.50,N", result)
        # Mag set field should be empty
        self.assertIn("45.0,T,,,", result)

    def test_magnetic_set(self):
        _set("Tidal Set", 45.0, "°M")
        _set("Tidal Drift", 0.5, None)
        result = nmea0183.process_vdr()
        self.assertIn("45.0,M", result)
        self.assertIn("0.50,N", result)
        # True set field should be empty
        self.assertIn(",,,45.0,M,", result)


# ── MDA temperature ───────────────────────────────────────────────────────────

class TestMDA(unittest.TestCase):
    def setUp(self):
        data_store.live_data.clear()

    def test_celsius_temps(self):
        _set("Air Temperature (°C)", 20.0, None)
        _set("Sea Temperature (°C)", 18.5, None)
        _set("Barometric Pressure", 1013.0, None)
        result = nmea0183.process_mda()
        self.assertIn("20.0,C", result)
        self.assertIn("18.5,C", result)

    def test_fahrenheit_fallback_air(self):
        # 68°F = 20.0°C
        _set("Air Temperature (°F)", 68.0, None)
        result = nmea0183.process_mda()
        self.assertIn("20.0,C", result)

    def test_fahrenheit_fallback_sea(self):
        # 65.3°F = 18.5°C
        _set("Sea Temperature (°F)", 65.3, None)
        result = nmea0183.process_mda()
        self.assertIn("18.5,C", result)

    def test_celsius_takes_priority_over_fahrenheit(self):
        _set("Air Temperature (°C)", 20.0, None)
        _set("Air Temperature (°F)", 212.0, None)   # 100°C — should be ignored
        result = nmea0183.process_mda()
        self.assertIn("20.0,C", result)
        self.assertNotIn("100.0,C", result)

    def test_pressure_only(self):
        _set("Barometric Pressure", 1013.0, None)
        result = nmea0183.process_mda()
        self.assertIn("IIMDA", result)
        # Temp fields should be empty
        self.assertIn(",,,,", result)

    def test_trigger_map_includes_fahrenheit(self):
        self.assertIn("Air Temperature (°F)", nmea0183._TRIGGER_MAP)
        self.assertIn("Sea Temperature (°F)", nmea0183._TRIGGER_MAP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
