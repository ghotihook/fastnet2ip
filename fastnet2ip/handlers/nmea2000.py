"""FastNet (Signal K) → NMEA 2000 output handler.

pyfastnet v3 emits {signalk_path: SI_value}, and NMEA 2000 is SI (radians, m/s,
Kelvin, Pascals, metres), so values pass straight through — no conversion. T/M
reference comes from the path; position arrives as an object.
"""

import argparse
import logging
import math
import socket
import struct
import time
from collections.abc import Callable
from datetime import datetime, timezone

from nmea2000 import pgns as n2k_pgns
from nmea2000.encoder import NMEA2000Encoder
from nmea2000.input_formats import N2KFormat
import nmea2000.encoder_formats  # registers format handlers

from fastnet2ip.core.data_store import live_data, get_live_data
from fastnet2ip.handlers.base import OutputHandler

logger = logging.getLogger("fastnet2ip.handlers.nmea2000")
if not logger.hasHandlers():
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s [fastnet2ip_n2k] %(levelname)-5s %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ── N2K defaults (overridden by setup() / tests) ──────────────────────────────
N2K_SRC           = 201
N2K_PRI           = 4

REBROADCAST_AGE   = 5
MIN_SEND_INTERVAL = 0.05

_channel_last_sent: dict = {}
_sid = 0
_hb_seq = 0

_encoder = NMEA2000Encoder(N2KFormat.CAN_FRAME_ASCII)

_PGN_NAMES: dict[int, str] = {
    127245: "Rudder",
    127250: "Vessel Heading",
    127251: "Rate of Turn",
    127257: "Attitude",
    127508: "Battery Status",
    128000: "Leeway",
    128259: "Boat Speed",
    128267: "Water Depth",
    128275: "Distance Log",
    129025: "Position (Lat/Lon)",
    129026: "COG & SOG",
    129283: "Cross Track Error",
    129291: "Set & Drift",
    130306: "Wind Data",
    130312: "Temperature",
    130314: "Pressure",
    65280:  "Proprietary: Raw Wind",
    65281:  "Proprietary: Raw Heading",
    65282:  "Proprietary: Raw Boatspeed",
}

# Proprietary PGN manufacturer header: B&G (code 381), Marine industry (code 4).
_PROP_MFR_HDR = struct.pack('<H', (4 << 13) | 381)


def _p_u16(val) -> int:
    return 0xFFFF if val is None else int(val) & 0xFFFF


def _pgn_label(msg: str) -> str:
    try:
        parts = msg.split()
        if parts[0].startswith("$PCDIN"):
            pgn = int(parts[0].split(",")[1], 16)
        else:
            can_id = int(parts[2], 16)
            pgn = (can_id >> 8) & 0x3FFFF
    except (IndexError, ValueError):
        return ""
    name = _PGN_NAMES.get(pgn, "Unknown")
    return f" PGN {pgn} ({name})"


# ── N2K output formatters ─────────────────────────────────────────────────────

def _fmt_ydwg(frames: list[bytes], _pgn: int, _src: int) -> list[str]:
    now = datetime.now()
    ts = f"{now:%H:%M:%S}.{now.microsecond // 1000:03d}"
    return [f"{ts} R {frame.decode()}" for frame in frames]


def _fmt_pcdin(frames: list[bytes], pgn: int, src: int) -> list[str]:
    now = datetime.now()
    seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second
    ms_midnight = seconds_since_midnight * 1000 + now.microsecond // 1000
    result = []
    for frame in frames:
        parts = frame.decode().split()
        data_hex = "".join(parts[1:])
        body = f"PCDIN,{pgn:06X},{ms_midnight:08X},{src:02X},{data_hex}"
        cs = 0
        for c in body:
            cs ^= ord(c)
        result.append(f"${body}*{cs:02X}\r\n")
    return result


_N2K_FORMATTERS = {"ydwg": _fmt_ydwg, "pcdin": _fmt_pcdin}
_n2k_formatter = _fmt_ydwg


# ── Helpers ───────────────────────────────────────────────────────────────────

def _next_sid() -> int:
    global _sid
    _sid = (_sid + 1) % 253
    return _sid


def _n2k(pgn: int, **fields) -> list[str] | None:
    decode_fn = getattr(n2k_pgns, f"decode_pgn_{pgn}", None)
    if decode_fn is None:
        logger.error(f"No nmea2000 decode function for PGN {pgn}")
        return None
    msg = decode_fn(0, 0)
    msg.source    = N2K_SRC
    msg.priority  = N2K_PRI
    msg.timestamp = datetime.now(timezone.utc)
    for f in msg.fields:
        if f.id in fields:
            f.raw_value = None
            f.value     = fields[f.id]
    try:
        frames = _encoder.encode(msg)
    except ValueError as e:
        logger.error(f"PGN {pgn} encode error: {e}")
        return None
    return _n2k_formatter(frames, pgn, N2K_SRC)


def _n2k_proprietary(pgn: int, data: bytes) -> list[str]:
    dp  = (pgn >> 16) & 0x03
    pf  = (pgn >> 8)  & 0xFF
    ps  = pgn & 0xFF if pf >= 0xF0 else 0xFF
    pgn_field = (dp << 16) | (pf << 8) | ps
    frame_id  = ((N2K_PRI & 0x7) << 26) | ((pgn_field & 0x3FFFF) << 8) | (N2K_SRC & 0xFF)
    frame = f"{frame_id:08X} {' '.join(f'{b:02X}' for b in data)}\r\n".encode()
    return _n2k_formatter([frame], pgn, N2K_SRC)


def _send_iso_address_claim(udp_socket, host, n2k_port):
    name = (
        (N2K_SRC & 0x1FFFFF)
        | ((0 & 0x7FF) << 21)
        | ((150 & 0xFF) << 40)
        | ((25 & 0x7F) << 49)
        | ((4 & 0x7) << 60)
    )
    for msg in _n2k_proprietary(60928, struct.pack('<Q', name)):
        try:
            udp_socket.sendto(msg.encode(), (host, n2k_port))
        except socket.error as e:
            logger.error("ISO address claim send error: %s", e)
    logger.info("ISO address claim sent (PGN 60928, src=%d)", N2K_SRC)


def _send_heartbeat(udp_socket, host, n2k_port):
    global _hb_seq
    frames = _n2k(126993, dataTransmitOffset=60.0, sequenceCounter=_hb_seq)
    _hb_seq = (_hb_seq + 1) % 253
    if frames:
        for msg in frames:
            try:
                udp_socket.sendto(msg.encode(), (host, n2k_port))
            except socket.error as e:
                logger.error("Heartbeat send error: %s", e)
    logger.info("Heartbeat sent (PGN 126993, seq=%d)", (_hb_seq - 1) % 253)


def _send_product_info(udp_socket, host, n2k_port):
    frames = _n2k(
        126996,
        nmea2000Version=1.3,
        productCode=1,
        modelId="fastnet2ip",
        softwareVersionCode="dev",
        modelVersion="1.0",
        modelSerialCode="000001",
        certificationLevel="Level A",
        loadEquivalency=1,
    )
    if frames:
        for msg in frames:
            try:
                udp_socket.sendto(msg.encode(), (host, n2k_port))
            except socket.error as e:
                logger.error("Product info send error: %s", e)
    logger.info("Product information sent (PGN 126996)")


def _wrap(angle):
    """Normalise a radian angle into [0, 2π) for N2K angle fields."""
    if angle is None:
        return None
    return angle % math.tau


# ── Trigger functions (SI pass-through) ───────────────────────────────────────

def _process_wind(angle_path, speed_path, reference):
    angle = get_live_data(angle_path)   # rad
    speed = get_live_data(speed_path)   # m/s
    if angle is None and speed is None:
        return None
    return _n2k(130306, sid=_next_sid(), windSpeed=speed,
                windAngle=_wrap(angle), reference=reference)


def process_heading():
    mag = get_live_data("navigation.headingMagnetic")
    tru = get_live_data("navigation.headingTrue")
    if mag is not None:
        heading, ref = mag, "Magnetic"
    elif tru is not None:
        heading, ref = tru, "True"
    else:
        return None
    return _n2k(127250, sid=_next_sid(), heading=_wrap(heading),
                reference=ref, deviation=None, variation=None)


def process_boatspeed():
    bs = get_live_data("navigation.speedThroughWater")
    if bs is None:
        return None
    return _n2k(128259, sid=_next_sid(), speedWaterReferenced=bs,
                speedGroundReferenced=None, speedDirection=None)


def process_depth():
    dm = get_live_data("environment.depth.belowTransducer")
    if dm is None:
        return None
    return _n2k(128267, sid=_next_sid(), depth=dm, offset=None, range=None)


def process_rudder():
    ra = get_live_data("steering.rudderAngle")
    if ra is None:
        return None
    return _n2k(127245, position=ra, angleOrder=None)


def process_apparent_wind():
    return _process_wind("environment.wind.angleApparent",
                         "environment.wind.speedApparent", "Apparent")


def process_true_wind():
    return _process_wind("environment.wind.angleTrueWater",
                         "environment.wind.speedTrue", "True (boat referenced)")


def process_twd():
    mag = get_live_data("environment.wind.directionMagnetic")
    tru = get_live_data("environment.wind.directionTrue")
    if mag is not None:
        direction, ref = mag, "Magnetic (ground referenced to Magnetic North)"
    elif tru is not None:
        direction, ref = tru, "True (ground referenced to North)"
    else:
        return None
    speed = get_live_data("environment.wind.speedTrue")
    return _n2k(130306, sid=_next_sid(), windSpeed=speed,
                windAngle=_wrap(direction), reference=ref)


def process_cog_sog():
    cog_true = get_live_data("navigation.courseOverGroundTrue")
    cog_mag  = get_live_data("navigation.courseOverGroundMagnetic")
    sog      = get_live_data("navigation.speedOverGround")
    if sog is None:
        return None
    if cog_true is not None:
        return _n2k(129026, sid=_next_sid(), cogReference="True",
                    cog=_wrap(cog_true), sog=sog)
    if cog_mag is not None:
        return _n2k(129026, sid=_next_sid(), cogReference="Magnetic",
                    cog=_wrap(cog_mag), sog=sog)
    return _n2k(129026, sid=_next_sid(), cog=None, sog=sog)


def process_position():
    pos = get_live_data("navigation.position")
    if not pos:
        return None
    return _n2k(129025, latitude=pos["latitude"], longitude=pos["longitude"])


def process_attitude():
    roll  = get_live_data("navigation.attitude.roll")
    pitch = get_live_data("navigation.attitude.pitch")
    if roll is None and pitch is None:
        return None
    return _n2k(127257, sid=_next_sid(), roll=roll, pitch=pitch, yaw=None)


def process_pressure():
    bp = get_live_data("environment.outside.pressure")   # Pa
    if bp is None:
        return None
    return _n2k(130314, sid=_next_sid(), pressure=bp)


def process_sea_temp():
    k = get_live_data("environment.water.temperature")   # Kelvin
    if k is None:
        return None
    return _n2k(130312, sid=_next_sid(), actualTemperature=k, setTemperature=None)


def process_air_temp():
    k = get_live_data("environment.outside.temperature")   # Kelvin
    if k is None:
        return None
    return _n2k(130312, sid=_next_sid(), source="Outside Temperature",
                actualTemperature=k, setTemperature=None)


def process_battery():
    v = get_live_data("electrical.batteries.house.voltage")
    if v is None:
        return None
    return _n2k(127508, sid=_next_sid(), voltage=v, current=None, temperature=None)


def process_set_drift():
    set_mag = get_live_data("environment.current.setMagnetic")
    set_tru = get_live_data("environment.current.setTrue")
    if set_mag is not None:
        set_val, ref = set_mag, "Magnetic"
    elif set_tru is not None:
        set_val, ref = set_tru, "True"
    else:
        return None
    drift = get_live_data("environment.current.drift")
    return _n2k(129291, sid=_next_sid(), setReference=ref, set=_wrap(set_val),
                drift=max(0.0, drift) if drift is not None else None)


def process_leeway():
    lw = get_live_data("navigation.leewayAngle")
    if lw is None:
        return None
    return _n2k(128000, sid=_next_sid(), leewayAngle=lw)


def process_rate_of_turn():
    yr = get_live_data("navigation.rateOfTurn")
    if yr is None:
        return None
    return _n2k(127251, sid=_next_sid(), rate=yr)


def process_distance_log():
    stored = get_live_data("navigation.log")        # m
    trip   = get_live_data("navigation.trip.log")   # m
    if stored is None and trip is None:
        return None
    now = datetime.now(timezone.utc)
    return _n2k(128275, date=now.date(), time=now.time(),
                log=int(stored) if stored is not None else None,
                tripLog=int(trip) if trip is not None else None)


def process_xte():
    xte = get_live_data("navigation.courseGreatCircle.crossTrackError")   # m
    if xte is None:
        return None
    return _n2k(129283, sid=_next_sid(), xte=xte)


# Raw (pre-calibration) sensor values → B&G proprietary PGNs (opaque u16 counts).
def _prop_raw_wind_speed() -> list[str] | None:
    ws = get_live_data("bandg.wind.rawSpeedApparent")
    wa = get_live_data("bandg.wind.rawAngleApparent")
    if ws is None and wa is None:
        return None
    return _n2k_proprietary(65280, _PROP_MFR_HDR + struct.pack('<HH', _p_u16(ws), _p_u16(wa)))


def _prop_raw_heading() -> list[str] | None:
    hd = get_live_data("bandg.navigation.rawHeading")
    if hd is None:
        return None
    return _n2k_proprietary(65281, _PROP_MFR_HDR + struct.pack('<H', _p_u16(hd)))


def _prop_raw_boatspeed() -> list[str] | None:
    bs = get_live_data("bandg.navigation.rawSpeedThroughWater")
    if bs is None:
        return None
    return _n2k_proprietary(65282, _PROP_MFR_HDR + struct.pack('<H', _p_u16(bs)))


# ── Channel map ───────────────────────────────────────────────────────────────

_CHANNEL_MAP: dict[str, Callable[[], list[str] | None] | str] = {
    "navigation.headingMagnetic":                   process_heading,
    "navigation.headingTrue":                       process_heading,
    "steering.rudderAngle":                         process_rudder,
    "navigation.speedThroughWater":                 process_boatspeed,
    "environment.depth.belowTransducer":            process_depth,
    "environment.wind.angleApparent":               process_apparent_wind,
    "environment.wind.speedApparent":               "covered by angleApparent (same frame)",
    "environment.wind.angleTrueWater":              process_true_wind,
    "environment.wind.directionMagnetic":           process_twd,
    "environment.wind.directionTrue":               process_twd,
    "environment.wind.speedTrue":                   "covered by TWA/TWD (same frame)",
    "navigation.leewayAngle":                       process_leeway,
    "navigation.speedOverGround":                   process_cog_sog,
    "navigation.courseOverGroundTrue":              "covered by speedOverGround (same frame)",
    "navigation.courseOverGroundMagnetic":          "covered by speedOverGround (same frame)",
    "electrical.batteries.house.voltage":           process_battery,
    "navigation.attitude.roll":                     process_attitude,
    "navigation.attitude.pitch":                    "covered by attitude.roll (same frame)",
    "navigation.log":                               process_distance_log,
    "navigation.trip.log":                          "covered by navigation.log (same frame)",
    "environment.water.temperature":                process_sea_temp,
    "environment.outside.temperature":              process_air_temp,
    "navigation.position":                          process_position,
    "environment.outside.pressure":                 process_pressure,
    "navigation.rateOfTurn":                        process_rate_of_turn,
    "navigation.courseGreatCircle.crossTrackError": process_xte,
    "environment.current.setMagnetic":              process_set_drift,
    "environment.current.setTrue":                  process_set_drift,
    "environment.current.drift":                    "covered by current.set* (same frame)",
    "bandg.wind.rawSpeedApparent":                  _prop_raw_wind_speed,
    "bandg.wind.rawAngleApparent":                  "covered by rawSpeedApparent (PGN 65280)",
    "bandg.navigation.rawHeading":                  _prop_raw_heading,
    "bandg.navigation.rawSpeedThroughWater":        _prop_raw_boatspeed,
}


# ── Frame processing ──────────────────────────────────────────────────────────

def trigger_n2k_frame(path: str) -> list[str] | None:
    entry = _CHANNEL_MAP.get(path)
    if entry is None:
        logger.debug(f"No trigger for {path!r}")
    elif isinstance(entry, str):
        logger.debug(f"No trigger for {path!r} — {entry}")
    else:
        return entry()
    return None


# ── Handler class ─────────────────────────────────────────────────────────────

DEFAULT_HOST     = "255.255.255.255"
DEFAULT_N2K_PORT = 2000


class NMEA2000Handler(OutputHandler):
    _host: str = DEFAULT_HOST
    _n2k_port: int = DEFAULT_N2K_PORT

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--n2k-src", type=lambda x: int(x, 0), default=201,
            help="N2K source address 0–253 (default: 201)",
        )
        parser.add_argument(
            "--n2k-pri", type=int, default=4,
            choices=range(8), metavar="0-7",
            help="N2K message priority 0 (highest) – 7 (lowest) (default: 4)",
        )
        parser.add_argument(
            "--n2k-format", type=str, default="ydwg",
            choices=list(_N2K_FORMATTERS),
            help="N2K UDP wire format: ydwg (default) or pcdin",
        )

    def setup(self, args: argparse.Namespace) -> None:
        global N2K_SRC, N2K_PRI, _n2k_formatter
        N2K_SRC = args.n2k_src
        N2K_PRI = args.n2k_pri
        _n2k_formatter = _N2K_FORMATTERS[args.n2k_format]
        self._host = args.host
        self._n2k_port = args.udp_port
        logger.info(
            f"NMEA 2000 → {args.host}:{args.udp_port}  "
            f"src={N2K_SRC}  pri={N2K_PRI}  fmt={args.n2k_format}"
        )

    def startup(self, udp_socket: socket.socket) -> None:
        _send_iso_address_claim(udp_socket, self._host, self._n2k_port)
        _send_product_info(udp_socket, self._host, self._n2k_port)
        _send_heartbeat(udp_socket, self._host, self._n2k_port)
        now = time.monotonic()
        self._last_heartbeat = now
        self._last_product_info = now

    def tick(self, udp_socket: socket.socket) -> None:
        now = time.monotonic()
        if now - self._last_heartbeat >= 60.0:
            _send_heartbeat(udp_socket, self._host, self._n2k_port)
            self._last_heartbeat = now
        if now - self._last_product_info >= 60.0:
            _send_iso_address_claim(udp_socket, self._host, self._n2k_port)
            _send_product_info(udp_socket, self._host, self._n2k_port)
            self._last_product_info = now

    def process_channel(self, path, old_entry, udp_socket):
        now = time.monotonic()
        current = live_data.get(path)
        new_key = current["value"] if current else None
        old_key = old_entry["value"] if old_entry else None

        last_sent = _channel_last_sent.get(path)
        if last_sent is not None:
            if (now - last_sent) < MIN_SEND_INTERVAL:
                return
            if new_key == old_key and (now - last_sent) < REBROADCAST_AGE:
                return

        _channel_last_sent[path] = now
        frames = trigger_n2k_frame(path)
        if frames:
            for msg in frames:
                try:
                    udp_socket.sendto(msg.encode(), (self._host, self._n2k_port))
                    logger.debug(f"N2K:{_pgn_label(msg)} {msg.strip()}")
                except socket.error as e:
                    logger.error(f"N2K send error: {e}")

    @property
    def udp_host(self) -> str:
        return self._host

    @property
    def udp_port(self) -> int:
        return self._n2k_port
