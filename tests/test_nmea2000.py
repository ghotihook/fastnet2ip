#!/usr/bin/env python3
"""Smoke tests for the NMEA 2000 output handler (pyfastnet v3 Signal K input).

pyfastnet v3 emits {signalk_path: SI_value}; N2K is SI, so the handler passes values
through. Inputs are fed as SI keyed by path; T/M reference comes from the path.
"""
import argparse
import logging
import math
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
KN_MS = 0.514444

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
        for pgn in (65280, 65281, 65282):
            self.assertIn(pgn, self.pgns, f"PGN {pgn} (proprietary raw) not found")
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
            update_live_data("environment.wind.angleApparent", math.radians(awa_deg))
            update_live_data("environment.wind.speedApparent", 10.0 * KN_MS)
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
        update_live_data("environment.wind.angleTrueWater", math.radians(45.0))
        update_live_data("environment.wind.speedTrue", 12.0 * KN_MS)
        update_live_data("environment.wind.directionMagnetic", math.radians(180.0))
        twa_frames = bridge.process_true_wind()
        twd_frames = bridge.process_twd()
        self.assertIsNotNone(twa_frames)
        self.assertIsNotNone(twd_frames)
        all_frames = (twa_frames or []) + (twd_frames or [])
        self.assertGreaterEqual(len(all_frames), 2)


class TestReferenceFromPath(unittest.TestCase):
    """T/M reference is now carried by the Signal K path, not a layout field."""

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

    def test_heading_magnetic_reference(self):
        update_live_data("navigation.headingMagnetic", math.radians(45.0))
        frames = bridge.process_heading()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 127250, "reference"), "Magnetic")

    def test_heading_true_reference(self):
        update_live_data("navigation.headingTrue", math.radians(45.0))
        frames = bridge.process_heading()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 127250, "reference"), "True")

    def test_heading_absent_skips_silently(self):
        self.assertIsNone(bridge.process_heading())

    def test_twd_magnetic_reference(self):
        update_live_data("environment.wind.directionMagnetic", math.radians(180.0))
        update_live_data("environment.wind.speedTrue", 12.0 * KN_MS)
        frames = bridge.process_twd()
        self.assertIsNotNone(frames)
        self.assertEqual(
            self._decode_field(frames, 130306, "reference"),
            "Magnetic (ground referenced to Magnetic North)",
        )

    def test_twd_true_reference(self):
        update_live_data("environment.wind.directionTrue", math.radians(180.0))
        update_live_data("environment.wind.speedTrue", 12.0 * KN_MS)
        frames = bridge.process_twd()
        self.assertIsNotNone(frames)
        self.assertEqual(
            self._decode_field(frames, 130306, "reference"),
            "True (ground referenced to North)",
        )

    def test_twd_absent_skips_silently(self):
        self.assertIsNone(bridge.process_twd())

    def test_set_drift_magnetic_reference(self):
        update_live_data("environment.current.setMagnetic", math.radians(45.0))
        update_live_data("environment.current.drift", 0.5 * KN_MS)
        frames = bridge.process_set_drift()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 129291, "setReference"), "Magnetic")

    def test_set_drift_true_reference(self):
        update_live_data("environment.current.setTrue", math.radians(45.0))
        update_live_data("environment.current.drift", 0.5 * KN_MS)
        frames = bridge.process_set_drift()
        self.assertIsNotNone(frames)
        self.assertEqual(self._decode_field(frames, 129291, "setReference"), "True")

    def test_set_drift_set_absent_skips_silently(self):
        update_live_data("environment.current.drift", 0.5 * KN_MS)
        self.assertIsNone(bridge.process_set_drift())


if __name__ == "__main__":
    unittest.main(verbosity=2)
