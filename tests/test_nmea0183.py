"""Smoke tests for the NMEA 0183 output handler."""
import unittest

from fastnet2ip.handlers import nmea0183


class TestImport(unittest.TestCase):
    def test_module_loads(self):
        self.assertTrue(callable(nmea0183.process_vhw))
        self.assertTrue(callable(nmea0183.process_mwv_apparent))
        self.assertIn("Heading", nmea0183._TRIGGER_MAP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
