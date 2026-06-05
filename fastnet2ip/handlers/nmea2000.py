"""FastNet → NMEA 2000 output handler."""

import argparse
import logging
import math
import queue
import socket
import struct
import time
from collections.abc import Callable
from datetime import datetime, timezone

import serial
from nmea2000 import pgns as n2k_pgns
from nmea2000.encoder import NMEA2000Encoder
from nmea2000.input_formats import N2KFormat
import nmea2000.encoder_formats  # registers format handlers

from fastnet_decoder import set_log_level

from fastnet2ip.core.data_store import live_data, update_live_data, get_live_data, get_live_display
from fastnet2ip.core.input import initialize_input_source, read_input_source
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
_KN_MS            = 0.514444

_channel_last_sent: dict = {}
_ignored_channels: set   = set()
_sid = 0
_hb_seq = 0

_GPS_CHANNELS = frozenset({
    "LatLon",
    "Speed Over Ground",
    "Course Over Ground (True)",
    "Course Over Ground (Mag)",
})

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
    65280:  "Proprietary: Raw Wind+Speed",
    65281:  "Proprietary: Raw Heading",
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
    _sid = (_sid + 1) % 252
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


# Proprietary PGN manufacturer header: B&G (code 381), Marine industry (code 4).
_PROP_MFR_HDR = struct.pack('<H', (4 << 13) | 381)


def _p_u16(val) -> int:
    if val is None:
        return 0xFFFF
    return int(val) & 0xFFFF


def _f_to_k(f: float) -> float:
    return (f - 32) * 5 / 9 + 273.15


# ── live_data access ──────────────────────────────────────────────────────────

_LAYOUT_REFERENCE = {"°M": "Magnetic", "°T": "True"}
_LAYOUT_WIND_REFERENCE = {
    "°M": "Magnetic (ground referenced to Magnetic North)",
    "°T": "True (ground referenced to North)",
}


def _bearing_reference(name) -> str | None:
    entry = live_data.get(name)
    if entry is None:
        return None
    ref = _LAYOUT_REFERENCE.get(entry["layout"])
    if ref is None:
        logger.error(f"{name}: unrecognised layout {entry['layout']!r}, skipping frame")
    return ref


# ── Trigger functions ─────────────────────────────────────────────────────────

def _process_wind(angle_ch, speed_ch, reference):
    angle = get_live_data(angle_ch)
    speed = get_live_data(speed_ch)
    if angle is None and speed is None:
        return None
    if angle is not None and angle < 0:
        angle += 360
    return _n2k(130306, sid=_next_sid(),
                windSpeed=speed * _KN_MS if speed is not None else None,
                windAngle=math.radians(angle) if angle is not None else None,
                reference=reference)


def process_heading():
    hdg = get_live_data("Heading")
    if hdg is None:
        return None
    ref = _bearing_reference("Heading")
    if ref is None:
        return None
    return _n2k(127250, sid=_next_sid(), heading=math.radians(hdg),
                reference=ref, deviation=None, variation=None)


def process_boatspeed():
    bs = get_live_data("Boatspeed (Knots)")
    if bs is None:
        return None
    return _n2k(128259, sid=_next_sid(), speedWaterReferenced=bs * _KN_MS,
                speedGroundReferenced=None, speedDirection=None)


def process_depth():
    dm = get_live_data("Depth (Meters)")
    if dm is None:
        return None
    return _n2k(128267, sid=_next_sid(), depth=dm, offset=None, range=None)


def process_rudder():
    ra = get_live_data("Rudder Angle")
    if ra is None:
        return None
    return _n2k(127245, position=math.radians(ra), angleOrder=None)


def process_apparent_wind():
    return _process_wind("Apparent Wind Angle", "Apparent Wind Speed (Knots)", "Apparent")


def process_true_wind():
    return _process_wind("True Wind Angle", "True Wind Speed (Knots)", "True (boat referenced)")


def process_twd():
    entry = live_data.get("True Wind Direction")
    if entry is None:
        return None
    ref = _LAYOUT_WIND_REFERENCE.get(entry["layout"])
    if ref is None:
        logger.error(f"True Wind Direction: unrecognised layout {entry['layout']!r}, skipping frame")
        return None
    return _process_wind("True Wind Direction", "True Wind Speed (Knots)", ref)


def process_cog_sog():
    cog_true = get_live_data("Course Over Ground (True)")
    cog_mag  = get_live_data("Course Over Ground (Mag)")
    sog      = get_live_data("Speed Over Ground")
    if sog is None:
        return None
    sog_ms = sog * _KN_MS
    if cog_true is not None:
        return _n2k(129026, sid=_next_sid(), cogReference="True",
                    cog=math.radians(cog_true % 360), sog=sog_ms)
    if cog_mag is not None:
        return _n2k(129026, sid=_next_sid(), cogReference="Magnetic",
                    cog=math.radians(cog_mag % 360), sog=sog_ms)
    return _n2k(129026, sid=_next_sid(), cog=None, sog=sog_ms)


def process_position():
    latlon = get_live_display("LatLon")
    if not latlon:
        return None
    lat_idx = latlon.find('N') if 'N' in latlon else latlon.find('S')
    lon_idx = latlon.find('E') if 'E' in latlon else latlon.find('W')
    if lat_idx == -1 or lon_idx == -1:
        return None
    try:
        lat_part = latlon[:lat_idx]
        lat_dir  = latlon[lat_idx]
        lon_part = latlon[lat_idx + 1:lon_idx]
        lon_dir  = latlon[lon_idx]
        lat = int(lat_part[:2]) + float(lat_part[2:]) / 60
        lon = int(lon_part[:3]) + float(lon_part[3:]) / 60
    except (ValueError, IndexError):
        logger.debug(f"position: could not parse {latlon!r}")
        return None
    if lat_dir == 'S':
        lat = -lat
    if lon_dir == 'W':
        lon = -lon
    return _n2k(129025, latitude=lat, longitude=lon)


def process_attitude():
    roll  = get_live_data("Heel Angle")
    pitch = get_live_data("Fore/Aft Trim")
    if roll is None and pitch is None:
        return None
    return _n2k(127257,
                sid=_next_sid(),
                roll=math.radians(roll) if roll is not None else None,
                pitch=math.radians(pitch) if pitch is not None else None,
                yaw=None)


def process_pressure():
    bp = get_live_data("Barometric Pressure")
    if bp is None:
        return None
    return _n2k(130314, sid=_next_sid(), pressure=bp * 100)


def process_sea_temp():
    t = get_live_data("Sea Temperature (°C)")
    if t is None:
        return None
    return _n2k(130312, sid=_next_sid(), actualTemperature=t + 273.15, setTemperature=None)


def process_air_temp():
    t = get_live_data("Air Temperature (°C)")
    if t is None:
        return None
    return _n2k(130312, sid=_next_sid(), source="Outside Temperature",
                actualTemperature=t + 273.15, setTemperature=None)


def process_battery():
    v = get_live_data("Battery Volts")
    if v is None:
        return None
    return _n2k(127508, sid=_next_sid(), voltage=v, current=None, temperature=None)


def process_set_drift():
    set_deg = get_live_data("Tidal Set")
    drift   = get_live_data("Tidal Drift")
    if set_deg is None and drift is None:
        return None
    ref = _bearing_reference("Tidal Set")
    if ref is None:
        return None
    return _n2k(129291, sid=_next_sid(), setReference=ref,
                set=math.radians(set_deg % 360) if set_deg is not None else None,
                drift=max(0.0, drift) * _KN_MS if drift is not None else None)


def process_leeway():
    lw = get_live_data("Leeway")
    if lw is None:
        return None
    return _n2k(128000, sid=_next_sid(), leewayAngle=math.radians(lw))


def process_rate_of_turn():
    yr = get_live_data("Yaw rate")
    if yr is None:
        return None
    return _n2k(127251, sid=_next_sid(), rate=math.radians(yr))


def process_distance_log():
    stored = get_live_data("Stored Log (NM)")
    trip   = get_live_data("Trip Log (NM)")
    if stored is None and trip is None:
        return None
    now = datetime.now(timezone.utc)
    return _n2k(128275,
                date=now.date(), time=now.time(),
                log=stored * 1852 if stored is not None else None,
                tripLog=trip * 1852 if trip is not None else None)


def process_xte():
    xte = get_live_data("Cross Track Error")
    if xte is None:
        return None
    return _n2k(129283, sid=_next_sid(), xte=xte * 1852)


def process_sea_temp_f():
    if get_live_data("Sea Temperature (°C)") is not None:
        return None
    t_f = get_live_data("Sea Temperature (°F)")
    if t_f is None:
        return None
    return _n2k(130312, sid=_next_sid(), actualTemperature=_f_to_k(t_f), setTemperature=None)


def process_air_temp_f():
    if get_live_data("Air Temperature (°C)") is not None:
        return None
    t_f = get_live_data("Air Temperature (°F)")
    if t_f is None:
        return None
    return _n2k(130312, sid=_next_sid(), source="Outside Temperature",
                actualTemperature=_f_to_k(t_f), setTemperature=None)


def _prop_raw_wind_speed() -> list[str] | None:
    ws = get_live_data("Apparent Wind Speed (Raw)")
    wa = get_live_data("Apparent Wind Angle (Raw)")
    bs = get_live_data("Boatspeed (Raw)")
    if ws is None and wa is None:
        return None
    return _n2k_proprietary(65280, _PROP_MFR_HDR + struct.pack('<HHH', _p_u16(ws), _p_u16(wa), _p_u16(bs)))


def _prop_raw_heading() -> list[str] | None:
    hd = get_live_data("Heading (Raw)")
    if hd is None:
        return None
    return _n2k_proprietary(65281, _PROP_MFR_HDR + struct.pack('<HHH', _p_u16(hd), 0xFFFF, 0xFFFF))


# ── Channel map ───────────────────────────────────────────────────────────────

_CHANNEL_MAP: dict[str, Callable[[], list[str] | None] | str] = {
    "Heading":                      process_heading,
    "Rudder Angle":                 process_rudder,
    "Heading (Raw)":                _prop_raw_heading,
    "Boatspeed (Knots)":            process_boatspeed,
    "Boatspeed (Raw)":              "read from live_data by 'Apparent Wind Speed (Raw)' → PGN 65280",
    "Depth (Meters)":               process_depth,
    "Depth (Feet)":                 "duplicate of Depth (Meters) in different units — not sent",
    "Depth (Fathoms)":              "duplicate of Depth (Meters) in different units — not sent",
    "Apparent Wind Angle":          process_apparent_wind,
    "Apparent Wind Speed (Knots)":  "covered by 'Apparent Wind Angle' trigger (same frame)",
    "Apparent Wind Speed (Raw)":    _prop_raw_wind_speed,
    "Apparent Wind Angle (Raw)":    "covered by 'Apparent Wind Speed (Raw)' trigger (same frame)",
    "True Wind Angle":              process_true_wind,
    "True Wind Direction":          process_twd,
    "True Wind Speed (Knots)":      "covered by 'True Wind Angle' + 'True Wind Direction' triggers (same frame)",
    "True Wind Speed (m/s)":        "covered by 'True Wind Angle' + 'True Wind Direction' triggers (same frame)",
    "Leeway":                       process_leeway,
    "Speed Over Ground":            process_cog_sog,
    "Course Over Ground (True)":    "covered by 'Speed Over Ground' trigger (same frame)",
    "Course Over Ground (Mag)":     "covered by 'Speed Over Ground' trigger (same frame)",
    "Battery Volts":                process_battery,
    "Heel Angle":                   process_attitude,
    "Fore/Aft Trim":                "covered by 'Heel Angle' trigger (same frame)",
    "Stored Log (NM)":              process_distance_log,
    "Trip Log (NM)":                "covered by 'Stored Log (NM)' trigger (same frame)",
    "Sea Temperature (°C)":         process_sea_temp,
    "Sea Temperature (°F)":         process_sea_temp_f,
    "LatLon":                       process_position,
    "Barometric Pressure":          process_pressure,
    "Air Temperature (°C)":         process_air_temp,
    "Air Temperature (°F)":         process_air_temp_f,
    "Tidal Set":                    process_set_drift,
    "Tidal Drift":                  "covered by 'Tidal Set' trigger (same frame)",
    "Yaw rate":                     process_rate_of_turn,
    "Cross Track Error":            process_xte,
}


# ── Frame processing ──────────────────────────────────────────────────────────

def trigger_n2k_frame(channel_name: str) -> list[str] | None:
    entry = _CHANNEL_MAP.get(channel_name)
    if entry is None:
        logger.debug(f"No trigger for {channel_name!r}")
    elif isinstance(entry, str):
        logger.debug(f"No trigger for {channel_name!r} — {entry}")
    else:
        return entry()
    return None


def process_frame_queue(fq, udp_socket, host, n2k_port):
    """Process a frame queue directly — used by tests and the legacy _run path."""
    now = time.monotonic()
    while True:
        try:
            frame = fq.get_nowait()
        except queue.Empty:
            break
        if not frame:
            continue
        for channel_name, channel_data in frame.get("values", {}).items():
            if not channel_data:
                continue
            if channel_name in _ignored_channels:
                continue
            channel_id   = channel_data.get("channel_id", "??")
            value        = channel_data.get("value")
            display_text = channel_data.get("display_text", "")
            layout       = channel_data.get("layout")

            old_entry = live_data.get(channel_name)
            old_key   = (old_entry["value"], old_entry["display_text"]) if old_entry else (None, None)

            update_live_data(channel_name, channel_id, value, display_text, layout)

            last_sent = _channel_last_sent.get(channel_name)
            if last_sent is not None:
                if (now - last_sent) < MIN_SEND_INTERVAL:
                    continue
                if ((value, display_text) == old_key) and (now - last_sent) < REBROADCAST_AGE:
                    continue

            _channel_last_sent[channel_name] = now

            frames = trigger_n2k_frame(channel_name)
            if frames:
                for msg in frames:
                    try:
                        udp_socket.sendto(msg.encode(), (host, n2k_port))
                        logger.debug(f"N2K:{_pgn_label(msg)} {msg.strip()}")
                    except socket.error as e:
                        logger.error(f"N2K send error: {e}")


def _run(input_source, is_file, udp_socket, host, n2k_port, show_live_data, fb):
    """Self-contained run loop — used by tests."""
    from fastnet2ip.core.display import print_live_data
    last_print = time.monotonic()
    while True:
        data = read_input_source(input_source, is_file)
        if data:
            fb.add_to_buffer(data)
            fb.get_complete_frames()
            process_frame_queue(fb.frame_queue, udp_socket, host, n2k_port)
        if show_live_data and time.monotonic() - last_print >= 1:
            print_live_data(fb)
            last_print = time.monotonic()
        if is_file and data is None:
            break


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
        global N2K_SRC, N2K_PRI, _n2k_formatter, _ignored_channels
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
        self._last_heartbeat = time.monotonic()

    def tick(self, udp_socket: socket.socket) -> None:
        if time.monotonic() - self._last_heartbeat >= 60.0:
            _send_heartbeat(udp_socket, self._host, self._n2k_port)
            self._last_heartbeat = time.monotonic()

    def process_channel(self, channel_name, old_entry, udp_socket):
        now = time.monotonic()
        current = live_data.get(channel_name)
        new_key = (current["value"], current["display_text"]) if current else (None, None)
        old_key = (old_entry["value"], old_entry["display_text"]) if old_entry else (None, None)

        last_sent = _channel_last_sent.get(channel_name)
        if last_sent is not None:
            if (now - last_sent) < MIN_SEND_INTERVAL:
                return
            if new_key == old_key and (now - last_sent) < REBROADCAST_AGE:
                return

        _channel_last_sent[channel_name] = now
        frames = trigger_n2k_frame(channel_name)
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
