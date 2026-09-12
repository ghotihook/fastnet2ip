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
    127237,  # Autopilot mode (standby in this capture)
    130824,  # B&G key-value: raw sensor channels
}


def _payload_from_lines(lines):
    """Reassemble one fast-packet message from its YDWG lines: drop each frame's
    sequence byte and the first frame's length byte."""
    data = [bytes.fromhex("".join(line.strip().split()[3:])) for line in lines]
    joined = b"".join(d[1:] for d in data)
    return joined[1:1 + joined[0]]


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

    def test_raw_sensor_channels_are_bandg_key_value(self):
        """Raw channels go out as B&G key-value data (PGN 130824), the same frames
        fastnet2n2k sends: header 7D 99, one key per message, one CAN frame each,
        at priority 7 — and all four keys appear in the capture."""
        raw = [
            m.strip().split() for m in self.n2k
            if len(m.strip().split()) >= 3
            and m.strip().split()[1] == 'R'
            and _pgn_from_can_id(m.strip().split()[2]) == 130824
        ]
        self.assertTrue(raw, "No PGN 130824 raw-channel messages")
        keys = set()
        for parts in raw:
            self.assertEqual(int(parts[2], 16) >> 26, 7, "raw channels go at priority 7")
            data = bytes.fromhex(''.join(parts[3:]))
            self.assertEqual(data[0] & 0x1F, 0, "one CAN frame per message")
            payload = data[2:2 + data[1]]
            self.assertEqual(payload[:2], bytes([0x7D, 0x99]))
            key_len = int.from_bytes(payload[2:4], "little")
            self.assertEqual(key_len >> 12, 2, "value length 2")
            keys.add(key_len & 0xFFF)
        self.assertEqual(keys, {0x42, 0x4A, 0x4E, 0x52})

    def test_raw_value_bytes_match_fastnet2n2k(self):
        """-8246 raw heading → 7D 99 4A 20 CA DF, byte for byte what fastnet2n2k
        sends (signed 16-bit, little-endian)."""
        update_live_data("bandg.navigation.rawHeading", -8246)
        [line] = bridge._CHANNEL_MAP["bandg.navigation.rawHeading"]()
        self.assertEqual(_payload_from_lines([line]), bytes.fromhex("7D994A20CADF"))

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


class TestRawFullRate(unittest.TestCase):
    """Raw channels skip MIN_SEND_INTERVAL; everything else is still capped."""

    def setUp(self):
        data_store.live_data.clear()
        bridge._channel_last_sent.clear()
        self.h = _make_handler()
        self.sent = []
        sent = self.sent

        class Sock:
            def sendto(self, data, addr):
                sent.append(data)
        self.sock = Sock()

    def test_raw_channel_skips_the_rate_cap(self):
        update_live_data("bandg.navigation.rawHeading", 1234)
        self.h.process_channel("bandg.navigation.rawHeading", self.sock)
        self.h.process_channel("bandg.navigation.rawHeading", self.sock)
        self.assertEqual(len(self.sent), 2)

    def test_standard_channel_is_still_capped(self):
        update_live_data("navigation.speedThroughWater", 3.0)
        self.h.process_channel("navigation.speedThroughWater", self.sock)
        self.h.process_channel("navigation.speedThroughWater", self.sock)
        self.assertEqual(len(self.sent), 1)


class TestAutopilot(unittest.TestCase):
    """steering.autopilot.state → PGN 127237, as fastnet2n2k sends it."""

    def setUp(self):
        data_store.live_data.clear()
        self.addCleanup(setattr, bridge, "_n2k_formatter", bridge._n2k_formatter)
        bridge._n2k_formatter = bridge._fmt_ydwg

    @staticmethod
    def _decoded(lines):
        from nmea2000 import pgns as n2k_pgns
        payload = _payload_from_lines(lines)
        msg = n2k_pgns.decode_pgn_127237(int.from_bytes(payload, "little"), len(payload) * 8)
        return {f.id: f.value for f in msg.fields}

    def test_engaged_sends_mode_and_target(self):
        update_live_data("steering.autopilot.state", "auto")
        update_live_data("steering.autopilot.target.headingMagnetic", math.radians(331))
        fields = self._decoded(bridge.process_autopilot())
        self.assertEqual(fields["steeringMode"], "Heading Control")
        self.assertEqual(fields["headingReference"], "Magnetic")
        self.assertAlmostEqual(math.degrees(fields["headingToSteerCourse"]), 331, places=1)
        for field in bridge._AUTOPILOT_UNKNOWN:
            self.assertIsNone(fields[field], f"{field} should be not-available")

    def test_standby_sends_no_target(self):
        """Fastnet keeps sending the old target after disengaging; it isn't passed on."""
        update_live_data("steering.autopilot.state", "standby")
        update_live_data("steering.autopilot.target.headingMagnetic", math.radians(331))
        fields = self._decoded(bridge.process_autopilot())
        self.assertEqual(fields["steeringMode"], "Main Steering")
        self.assertIsNone(fields["headingToSteerCourse"])

    def test_power_steer_is_non_follow_up(self):
        update_live_data("steering.autopilot.state", "directControl")
        self.assertEqual(self._decoded(bridge.process_autopilot())["steeringMode"],
                         "Non-Follow-Up Device")

    def test_unknown_state_sends_nothing(self):
        self.assertIsNone(bridge.process_autopilot())


class TestPcdin(unittest.TestCase):
    """PCDIN carries one whole message per sentence: Signal K's parser reads the data
    field as the payload, so fast-packet PGNs must be reassembled, not split per CAN
    frame with their sequence bytes."""

    def setUp(self):
        data_store.live_data.clear()
        self.addCleanup(setattr, bridge, "_n2k_formatter", bridge._n2k_formatter)
        bridge._n2k_formatter = bridge._fmt_pcdin

    @staticmethod
    def _data(sentence):
        return bytes.fromhex(sentence.split(",")[4].split("*")[0])

    def test_fast_packet_pgn_is_one_sentence_with_the_whole_payload(self):
        update_live_data("navigation.log", 22835048.0)
        update_live_data("navigation.trip.log", 1000.0)
        [sentence] = bridge.process_distance_log()
        payload = self._data(sentence)
        self.assertEqual(len(payload), 14)                                    # 128275
        self.assertEqual(int.from_bytes(payload[6:10], "little"), 22835048)   # log, m
        self.assertEqual(int.from_bytes(payload[10:14], "little"), 1000)      # trip, m

    def test_single_frame_pgn_is_unchanged(self):
        update_live_data("navigation.speedThroughWater", 3.0)
        [sentence] = bridge.process_boatspeed()
        self.assertEqual(len(self._data(sentence)), 8)

    def test_raw_channel_payload(self):
        update_live_data("bandg.navigation.rawHeading", -8246)
        [sentence] = bridge._CHANNEL_MAP["bandg.navigation.rawHeading"]()
        self.assertTrue(sentence.startswith("$PCDIN,01FF08,"))
        self.assertEqual(self._data(sentence), bytes.fromhex("7D994A20CADF"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
