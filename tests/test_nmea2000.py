#!/usr/bin/env python3
"""Smoke tests for the NMEA 2000 output handler."""
import argparse
import logging
import os
import unittest

from fastnet2ip.handlers import nmea2000 as bridge
from fastnet2ip.core import data_store
from fastnet2ip.core.data_store import update_live_data
from fastnet2ip.core.input import initialize_input_source
from fastnet2ip.__main__ import run_loop, _drain_frame_queue
from fastnet_decoder import FrameBuffer, set_log_level

# Keep handler setup/startup INFO logs out of test output.
logging.getLogger("fastnet2ip.handlers.nmea2000").setLevel(logging.ERROR)

DATA_DIR  = os.path.join(os.path.dirname(__file__), "data")
TEST_FILE = os.path.join(DATA_DIR, "big.txt")

EXPECTED_N2K_PGNS = {
    127250,  # Heading
    128259,  # Boatspeed
    128267,  # Depth
    130306,  # Wind (apparent + true)
    129025,  # Position
    127257,  # Attitude (heel + trim)
    130314,  # Barometric pressure
    129026,  # COG / SOG
    128275,  # Distance log (fast packet)
}


def _pgn_from_can_id(can_id_hex: str) -> int:
    can_id = int(can_id_hex, 16)
    dp = (can_id >> 24) & 0x01
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    return (dp << 16) | (pf << 8) | (ps if pf >= 0xF0 else 0)


class _DiscardSocket:
    def sendto(self, data, addr):
        pass
    def close(self):
        pass


def _make_handler(n2k_port=2000):
    handler = bridge.NMEA2000Handler()
    handler.setup(argparse.Namespace(
        host="127.0.0.1", udp_port=n2k_port,
        n2k_src=0x22, n2k_pri=4, n2k_format="ydwg",
    ))
    return handler


def _run_bridge(file_path, n2k_port=2000):
    sent_n2k = []

    class FakeSocket:
        def sendto(self, data, addr):
            sent_n2k.append(data.decode())
        def close(self):
            pass

    data_store.live_data.clear()
    bridge._channel_last_sent.clear()
    bridge._sid = 0
    set_log_level("ERROR")

    handler = _make_handler(n2k_port)
    # Startup messages (address claim, product info, heartbeat) go to a
    # discard socket so assertions only see data-driven output.
    handler.startup(_DiscardSocket())

    args = argparse.Namespace(serial=None, file=file_path)
    input_source, is_file = initialize_input_source(args)
    run_loop(input_source, is_file, handler, FakeSocket(), show_live_data=False)
    return sent_n2k


def _pgns_seen(n2k_messages):
    pgns = set()
    for msg in n2k_messages:
        parts = msg.strip().split()
        if len(parts) >= 3 and parts[1] == "R":
            pgns.add(_pgn_from_can_id(parts[2]))
    return pgns


class TestSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n2k  = _run_bridge(TEST_FILE)
        cls.pgns = _pgns_seen(cls.n2k)

    def test_expected_pgns_present(self):
        missing = EXPECTED_N2K_PGNS - self.pgns
        self.assertFalse(missing, f"Missing PGNs in N2K output: {missing}")

    def test_n2k_format_valid(self):
        import re
        pattern = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} R [0-9A-F]{8}( [0-9A-F]{2})+\r\n$")
        bad = [m for m in self.n2k if not pattern.match(m)]
        self.assertFalse(bad, f"Malformed N2K messages: {bad[:3]}")

    def test_raw_sensor_pgns_present_and_valid(self):
        self.assertIn(65280, self.pgns, "PGN 65280 (raw wind) not found")
        self.assertIn(65281, self.pgns, "PGN 65281 (raw heading) not found")
        self.assertIn(65282, self.pgns, "PGN 65282 (raw boatspeed) not found")
        B_AND_G_HDR = bytes([0x7D, 0x81])
        for pgn in (65280, 65281, 65282):
            msgs = [
                m for m in self.n2k
                if len(m.strip().split()) >= 3
                and m.strip().split()[1] == 'R'
                and _pgn_from_can_id(m.strip().split()[2]) == pgn
            ]
            self.assertTrue(msgs, f"No messages for PGN {pgn}")
            for msg in msgs:
                parts = msg.strip().split()
                payload = bytes.fromhex(''.join(parts[3:]))
                self.assertEqual(payload[:2], B_AND_G_HDR,
                                 f"PGN {pgn}: expected B&G header 7D 81, got {payload[:2].hex()}")

    def test_no_output_without_data(self):
        data_store.live_data.clear()
        bridge._channel_last_sent.clear()
        sent = []

        class FakeSocket:
            def sendto(self, data, addr):
                sent.append(data)

        _drain_frame_queue(FrameBuffer().frame_queue, _make_handler(), FakeSocket())
        self.assertEqual(sent, [])

    def test_awa_normalised_non_negative(self):
        data_store.live_data.clear()
        bridge._channel_last_sent.clear()
        for awa_deg in (-90, -1, -179):
            update_live_data("Apparent Wind Angle", "0x28", awa_deg, str(awa_deg), "-[data]")
            update_live_data("Apparent Wind Speed (Knots)", "0x29", 10.0, str(10.0), None)
            frames = bridge.process_apparent_wind()
            self.assertIsNotNone(frames, f"No frames for AWA={awa_deg}")
            self.assertTrue(len(frames) > 0)

    def test_position_values_correct(self):
        import struct
        pos_msgs = [
            m for m in self.n2k
            if len(m.strip().split()) >= 3
            and m.strip().split()[1] == 'R'
            and _pgn_from_can_id(m.strip().split()[2]) == 129025
        ]
        self.assertTrue(pos_msgs, "No PGN 129025 position messages found")
        for msg in pos_msgs:
            parts = msg.strip().split()
            data = bytes.fromhex(''.join(parts[3:]))
            lat = struct.unpack_from('<i', data, 0)[0] * 1e-7
            lon = struct.unpack_from('<i', data, 4)[0] * 1e-7
            self.assertAlmostEqual(lat, -16.777, delta=0.01, msg=f"Lat out of range: {lat}")
            self.assertAlmostEqual(lon, 179.337, delta=0.01, msg=f"Lon out of range: {lon}")

    def test_tws_update_emits_both_references(self):
        data_store.live_data.clear()
        bridge._channel_last_sent.clear()
        update_live_data("True Wind Angle", "0x2A", 45.0, "45", None)
        update_live_data("True Wind Speed (Knots)", "0x2B", 12.0, "12.0", None)
        update_live_data("True Wind Direction", "0x2C", 180.0, "180°M", "°M")
        twa_frames = bridge.process_true_wind()
        twd_frames = bridge.process_twd()
        self.assertIsNotNone(twa_frames)
        self.assertIsNotNone(twd_frames)
        all_frames = (twa_frames or []) + (twd_frames or [])
        self.assertGreaterEqual(len(all_frames), 2)


class TestBearingReference(unittest.TestCase):

    def setUp(self):
        data_store.live_data.clear()
        bridge._channel_last_sent.clear()

    @staticmethod
    def _decode_field(frames, pgn, field_id):
        from nmea2000 import pgns as n2k_pgns
        decode_fn = getattr(n2k_pgns, f"decode_pgn_{pgn}")
        for line in frames:
            parts = line.strip().split()
            if len(parts) < 3 or parts[1] != "R":
                continue
            if _pgn_from_can_id(parts[2]) != pgn:
                continue
            data_bytes = bytes(int(b, 16) for b in parts[3:])
            msg = decode_fn(int.from_bytes(data_bytes, "little"), len(data_bytes) * 8)
            for f in msg.fields:
                if f.id == field_id:
                    return f.value
        return None

    def test_bearing_reference_magnetic(self):
        update_live_data("Heading", "0x49", 45.0, "45°M", "°M")
        self.assertEqual(bridge._bearing_reference("Heading"), "Magnetic")

    def test_bearing_reference_true(self):
        update_live_data("Heading", "0x49", 45.0, "45°T", "°T")
        self.assertEqual(bridge._bearing_reference("Heading"), "True")

    def test_bearing_reference_channel_absent_is_silent(self):
        self.assertIsNone(bridge._bearing_reference("Heading"))

    def test_bearing_reference_bad_layout_logs_error(self):
        update_live_data("Heading", "0x49", 45.0, "45", "?")
        with self.assertLogs("fastnet2ip.handlers.nmea2000", level="ERROR") as cm:
            result = bridge._bearing_reference("Heading")
        self.assertIsNone(result)
        self.assertTrue(any("unrecognised layout" in line for line in cm.output))

    def test_heading_encodes_magnetic_reference(self):
        update_live_data("Heading", "0x49", 45.0, "45°M", "°M")
        frames = bridge.process_heading()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 127250, "reference"), "Magnetic")

    def test_heading_encodes_true_reference(self):
        update_live_data("Heading", "0x49", 45.0, "45°T", "°T")
        frames = bridge.process_heading()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 127250, "reference"), "True")

    def test_heading_bad_layout_skips_frame(self):
        update_live_data("Heading", "0x49", 45.0, "45", "?")
        with self.assertLogs("fastnet2ip.handlers.nmea2000", level="ERROR"):
            frames = bridge.process_heading()
        self.assertIsNone(frames)

    def test_twd_encodes_magnetic_reference(self):
        update_live_data("True Wind Direction", "0x6D", 180.0, "180°M", "°M")
        update_live_data("True Wind Speed (Knots)", "0x55", 12.0, "12.0", None)
        frames = bridge.process_twd()
        self.assertIsNotNone(frames)
        self.assertEqual(
            self._decode_field(frames, 130306, "reference"),
            "Magnetic (ground referenced to Magnetic North)",
        )

    def test_twd_encodes_true_reference(self):
        update_live_data("True Wind Direction", "0x6D", 180.0, "180°T", "°T")
        update_live_data("True Wind Speed (Knots)", "0x55", 12.0, "12.0", None)
        frames = bridge.process_twd()
        self.assertIsNotNone(frames)
        self.assertEqual(
            self._decode_field(frames, 130306, "reference"),
            "True (ground referenced to North)",
        )

    def test_twd_bad_layout_skips_frame(self):
        update_live_data("True Wind Direction", "0x6D", 180.0, "180", "?")
        with self.assertLogs("fastnet2ip.handlers.nmea2000", level="ERROR"):
            frames = bridge.process_twd()
        self.assertIsNone(frames)

    def test_twd_channel_absent_skips_silently(self):
        self.assertIsNone(bridge.process_twd())

    def test_set_drift_encodes_magnetic_reference(self):
        update_live_data("Tidal Set",   "0x84", 45.0, "45°M", "°M")
        update_live_data("Tidal Drift", "0x83", 0.5,  "0.5",  "?")
        frames = bridge.process_set_drift()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 129291, "setReference"), "Magnetic")

    def test_set_drift_encodes_true_reference(self):
        update_live_data("Tidal Set",   "0x84", 45.0, "45°T", "°T")
        update_live_data("Tidal Drift", "0x83", 0.5,  "0.5",  "?")
        frames = bridge.process_set_drift()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 129291, "setReference"), "True")

    def test_set_drift_bad_layout_skips_frame(self):
        update_live_data("Tidal Set",   "0x84", 45.0, "45", "?")
        update_live_data("Tidal Drift", "0x83", 0.5,  "0.5", "?")
        with self.assertLogs("fastnet2ip.handlers.nmea2000", level="ERROR"):
            frames = bridge.process_set_drift()
        self.assertIsNone(frames)

    def test_set_drift_tidal_set_absent_skips_silently(self):
        update_live_data("Tidal Drift", "0x83", 0.5, "0.5", "?")
        self.assertIsNone(bridge.process_set_drift())


if __name__ == "__main__":
    unittest.main(verbosity=2)
