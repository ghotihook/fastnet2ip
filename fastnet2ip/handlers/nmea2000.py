"""FastNet (Signal K) → NMEA 2000 output handler.

pyfastnet v3 emits {signalk_path: SI_value}, and NMEA 2000 is SI (radians, m/s,
Kelvin, Pascals, metres), so values pass straight through — no conversion. T/M
reference comes from the path; position arrives as an object. The B&G raw sensor
channels are the exception: they are sensor counts, and go out as counts.
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
import nmea2000.encoder_formats  # noqa: F401 — registers format handlers on import

from fastnet2ip.core.data_store import get_live_data
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

MIN_SEND_INTERVAL = 0.05    # per-path rate cap (~20 Hz); debounce only

_channel_last_sent: dict = {}
_sid = 0
_hb_seq = 0

_encoder = NMEA2000Encoder(N2KFormat.CAN_FRAME_ASCII)

_PGN_NAMES: dict[int, str] = {
    127237: "Heading/Track Control",
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
    130824: "B&G Key-Value (raw sensors)",
}


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


def _pcdin_payloads(frames: list[bytes], pgn: int) -> list[bytes]:
    """The payload(s) PCDIN carries for one encoded message.

    A PCDIN sentence holds a whole message — SeaSmart's data field "can contain
    assembled fast packets", and Signal K's parser reads it as the payload directly —
    so a fast-packet PGN is reassembled rather than sent frame by frame: drop each
    frame's sequence byte and the first frame's length byte.
    """
    data = [bytes.fromhex("".join(frame.decode().split()[1:])) for frame in frames]
    is_fast = getattr(n2k_pgns, f"is_fast_pgn_{pgn}", None)
    if not (is_fast and is_fast()):
        return data
    joined = b"".join(d[1:] for d in data)
    return [joined[1:1 + joined[0]]]


def _fmt_pcdin(frames: list[bytes], pgn: int, src: int) -> list[str]:
    now = datetime.now()
    seconds_since_midnight = now.hour * 3600 + now.minute * 60 + now.second
    ms_midnight = seconds_since_midnight * 1000 + now.microsecond // 1000
    result = []
    for payload in _pcdin_payloads(frames, pgn):
        body = f"PCDIN,{pgn:06X},{ms_midnight:08X},{src:02X},{payload.hex().upper()}"
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


def _n2k(pgn: int, variant: str | None = None, priority: int | None = None,
         **fields) -> list[str] | None:
    """Build, encode and format one PGN. ``variant`` picks one of several layouts that
    share a PGN number (proprietary PGNs, e.g. "bGKeyValueData"); ``priority``
    overrides N2K_PRI for this message."""
    name = f"decode_pgn_{pgn}_{variant}" if variant else f"decode_pgn_{pgn}"
    decode_fn = getattr(n2k_pgns, name, None)
    if decode_fn is None:
        logger.error(f"No nmea2000 decode function {name}")
        return None
    msg = decode_fn(0, 0)
    msg.source    = N2K_SRC
    msg.priority  = N2K_PRI if priority is None else priority
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


# ── Autopilot (127237 Heading/Track Control) ──────────────────────────────────
# The same mapping as fastnet2n2k. steering.autopilot.state → 127237 steeringMode.
# The standard has no wind mode, so wind steers as Heading Control, like compass:
# the pilot is holding a heading either way. B&G "Power" is steering from the +/-
# buttons, i.e. non-follow-up.
_STEERING_MODE = {
    "standby":       "Main Steering",
    "auto":          "Heading Control",
    "wind":          "Heading Control",
    "directControl": "Non-Follow-Up Device",
    "route":         "Track Control",
}

# The 127237 fields Fastnet doesn't carry, sent as not-available rather than left at
# the blank template's zeros — which would claim e.g. "rudder limit not exceeded" and
# a 0° track. Lookup fields can't take None, so they get their all-ones raw value.
_AUTOPILOT_UNKNOWN = {
    "rudderLimitExceeded": 3, "offHeadingLimitExceeded": 3,     # 2-bit lookups
    "offTrackLimitExceeded": 3, "override": 3,
    "turnMode": 7, "commandedRudderDirection": 7,               # 3-bit lookups
    **dict.fromkeys(("commandedRudderAngle", "track", "rudderLimit", "offHeadingLimit",
                     "radiusOfTurnOrder", "rateOfTurnOrder", "offTrackLimit")),
}


def process_autopilot():
    mode = _STEERING_MODE.get(get_live_data("steering.autopilot.state"))
    if mode is None:
        return None
    # The compass target outlives the engagement — it stays on the wire in standby
    # while the boat turns away from it — so only send it while the pilot is steering
    # to a heading.
    target = None
    if mode in ("Heading Control", "Track Control"):
        target = _wrap(get_live_data("steering.autopilot.target.headingMagnetic"))
    return _n2k(127237, steeringMode=mode, headingReference="Magnetic",
                headingToSteerCourse=target,
                vesselHeading=_wrap(get_live_data("navigation.headingMagnetic")),
                **_AUTOPILOT_UNKNOWN)


# ── B&G raw sensor channels (130824 key-value) ────────────────────────────────
# The uncalibrated sensor readings, for logging. The same frames fastnet2n2k sends,
# so one decoder reads both bridges: B&G's own proprietary key-value PGN, in B&G's
# layout — header 7D 99 (code 381, marine), then a 12-bit key and 4-bit byte length,
# then the value. B&G's keys are Fastnet channel numbers, so each raw channel keeps
# its own. Values are the signed 16-bit counts Fastnet carries (format 0x0A),
# unscaled. One key per message fits a single CAN frame. Sent at full rate (exempt
# from MIN_SEND_INTERVAL) and the lowest priority, so the volume never delays
# navigation data. Full description: fastnet2n2k's docs/bandg_130824_raw_channels.md.
_BANDG_RAW_KEYS = {
    "bandg.navigation.rawSpeedThroughWater": 0x42,   # Boatspeed (Raw)
    "bandg.navigation.rawHeading":           0x4A,   # Heading (Raw)
    "bandg.wind.rawSpeedApparent":           0x4E,   # Apparent Wind Speed (Raw)
    "bandg.wind.rawAngleApparent":           0x52,   # Apparent Wind Angle (Raw)
}
_BANDG_HEADER = {"manufacturerCode": 381, "reserved_11": 3, "industryCode": 4}   # 7D 99
RAW_PRI = 7
_range_warned: set = set()


def _bandg_raw(path, key):
    """The trigger that sends ``path`` as B&G key ``key``."""
    def process_bandg_raw():
        value = get_live_data(path)
        if value is None:
            return None
        raw = round(value)
        if not -0x8000 <= raw <= 0x7FFF:
            if path not in _range_warned:   # full rate: warn once, not per update
                _range_warned.add(path)
                logger.warning(f"{path} = {value} doesn't fit signed 16 bits — not sent")
            return None
        return _n2k(130824, "bGKeyValueData", priority=RAW_PRI, **_BANDG_HEADER,
                    key=key, length=2, value=raw & 0xFFFF)
    return process_bandg_raw


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
    "steering.autopilot.state":                     process_autopilot,
    "steering.autopilot.target.headingMagnetic":    "covered by steering.autopilot.state (same frame)",
    **{path: _bandg_raw(path, key) for path, key in _BANDG_RAW_KEYS.items()},
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

    def process_channel(self, path, udp_socket):
        # Send on every update — a repeated value is still live data worth putting on
        # the wire — capped only by MIN_SEND_INTERVAL so a fast-updating path can't
        # flood UDP. No dedupe: the bridge reflects what the instruments provide.
        # (The 60s gateway-identity heartbeat in tick() is unrelated to this.)
        # The raw sensor channels skip the cap: they go out at full rate, for logging.
        now = time.monotonic()
        last_sent = _channel_last_sent.get(path)
        if (path not in _BANDG_RAW_KEYS and last_sent is not None
                and (now - last_sent) < MIN_SEND_INTERVAL):
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
