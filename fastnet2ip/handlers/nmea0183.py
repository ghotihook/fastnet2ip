import argparse
import math
import socket
from datetime import datetime, timezone, timedelta

from fastnet_decoder import logger

from fastnet2ip.core.data_store import live_data, get_live_data
from fastnet2ip.handlers.base import OutputHandler

REBROADCAST_AGE = 5
MIN_SEND_INTERVAL = 0.05
DEFAULT_UDP_PORT = 2002
DEFAULT_HOST = "255.255.255.255"

# pyfastnet v3 emits SI; NMEA 0183 sentences use knots / degrees / feet, so convert.
KN_MS = 0.514444
FT_M = 0.3048
FATHOM_M = 1.8288


def _kn(ms):
    return None if ms is None else ms / KN_MS


def _deg(rad):
    return None if rad is None else math.degrees(rad)


def _wrap360(deg):
    return None if deg is None else deg % 360.0


# ── NMEA helpers ──────────────────────────────────────────────────────────────

def _checksum(sentence):
    cs = 0
    for c in sentence:
        cs ^= ord(c)
    return f"{cs:02X}"


def _sentence(body):
    return f"${body}*{_checksum(body)}\r\n"


def _ddmm(deg, is_lat):
    """Decimal degrees → (DDMM.mmm, hemisphere)."""
    hemi = ("N" if deg >= 0 else "S") if is_lat else ("E" if deg >= 0 else "W")
    deg = abs(deg)
    d = int(deg)
    m = (deg - d) * 60
    width = 2 if is_lat else 3
    return f"{d:0{width}d}{m:06.3f}", hemi


# ── Process functions ─────────────────────────────────────────────────────────

def process_vhw():
    mag = get_live_data("navigation.headingMagnetic")
    tru = get_live_data("navigation.headingTrue")
    bs_kn = _kn(get_live_data("navigation.speedThroughWater"))
    bs_str = f"{bs_kn:.1f}" if bs_kn is not None else ""
    bs_kmh_str = f"{bs_kn * 1.852:.1f}" if bs_kn is not None else ""
    hdg_true_str, hdg_mag_str = "", ""
    if mag is not None:
        hdg_mag_str = f"{_wrap360(_deg(mag)):.1f}"
    elif tru is not None:
        hdg_true_str = f"{_wrap360(_deg(tru)):.1f}"
    body = (
        f"IIVHW,"
        f"{hdg_true_str},{'T' if hdg_true_str else ''},"
        f"{hdg_mag_str},{'M' if hdg_mag_str else ''},"
        f"{bs_str},{'N' if bs_str else ''},"
        f"{bs_kmh_str},{'K' if bs_kmh_str else ''}"
    )
    return _sentence(body)


def process_dbt():
    dm = get_live_data("environment.depth.belowTransducer")   # metres
    df = dm / FT_M if dm is not None else None
    dfa = dm / FATHOM_M if dm is not None else None
    body = (
        f"IIDBT,"
        f"{f'{df:.1f}' if df is not None else ''},f,"
        f"{f'{dm:.1f}' if dm is not None else ''},M,"
        f"{f'{dfa:.1f}' if dfa is not None else ''},F"
    )
    return _sentence(body)


def process_rsa():
    ra = _deg(get_live_data("steering.rudderAngle"))
    ra_str = f"{ra:.1f}" if ra is not None else ""
    status = "A" if ra is not None else "V"
    return _sentence(f"IIRSA,{ra_str},{status},,")


def process_xdr_battv():
    bv = get_live_data("electrical.batteries.house.voltage")
    bv_str = f"{bv:.2f}" if bv is not None else ""
    return _sentence(f"IIXDR,U,{bv_str},V,BATTV")


def process_mwd():
    mag = get_live_data("environment.wind.directionMagnetic")
    tru = get_live_data("environment.wind.directionTrue")
    tws = get_live_data("environment.wind.speedTrue")   # m/s
    twd_true_str, twd_mag_str = "", ""
    if mag is not None:
        twd_mag_str = f"{_wrap360(_deg(mag)):.1f}"
    elif tru is not None:
        twd_true_str = f"{_wrap360(_deg(tru)):.1f}"
    tws_kn = _kn(tws)
    tws_str = f"{tws_kn:.1f}" if tws_kn is not None else ""
    tws_ms_str = f"{tws:.1f}" if tws is not None else ""
    body = (
        f"IIMWD,"
        f"{twd_true_str},{'T' if twd_true_str else ''},"
        f"{twd_mag_str},{'M' if twd_mag_str else ''},"
        f"{tws_str},N,{tws_ms_str},M"
    )
    return _sentence(body)


def process_mwv_true():
    twa = _wrap360(_deg(get_live_data("environment.wind.angleTrueWater")))
    twa_str = f"{twa:.1f}" if twa is not None else ""
    tws_kn = _kn(get_live_data("environment.wind.speedTrue"))
    tws_str = f"{tws_kn:.1f}" if tws_kn is not None else ""
    status = "A" if (twa_str and tws_str) else "V"
    return _sentence(f"IIMWV,{twa_str},T,{tws_str},N,{status}")


def process_mwv_apparent():
    awa = _wrap360(_deg(get_live_data("environment.wind.angleApparent")))
    awa_str = f"{awa:.1f}" if awa is not None else ""
    aws_kn = _kn(get_live_data("environment.wind.speedApparent"))
    aws_str = f"{aws_kn:.1f}" if aws_kn is not None else ""
    status = "A" if (awa_str and aws_str) else "V"
    return _sentence(f"IIMWV,{awa_str},R,{aws_str},N,{status}")


def process_mda():
    def val_unit(val, fmt, unit):
        return f"{fmt.format(val)},{unit}," if val is not None else ",,"

    bp_pa = get_live_data("environment.outside.pressure")   # Pa
    bp_hpa = bp_pa / 100 if bp_pa is not None else None
    air_k = get_live_data("environment.outside.temperature")   # Kelvin
    air_temp = air_k - 273.15 if air_k is not None else None
    water_k = get_live_data("environment.water.temperature")
    water_temp = water_k - 273.15 if water_k is not None else None
    bp_inhg = bp_hpa * 0.0295299830714 if bp_hpa is not None else None
    bp_bar = bp_hpa / 1000 if bp_hpa is not None else None
    body = (
        "IIMDA,"
        f"{val_unit(bp_inhg, '{:.4f}', 'I')}"
        f"{val_unit(bp_bar, '{:.4f}', 'B')}"
        f"{val_unit(air_temp, '{:.1f}', 'C')}"
        f"{val_unit(water_temp, '{:.1f}', 'C')}"
        ",,,"
        ",,"
        ",,"
        ",,"
        ",,"
    )
    return _sentence(body)


def process_hdm():
    mag = get_live_data("navigation.headingMagnetic")
    tru = get_live_data("navigation.headingTrue")
    if mag is not None:
        return _sentence(f"IIHDM,{_wrap360(_deg(mag)):.1f},M")
    if tru is not None:
        return _sentence(f"IIHDT,{_wrap360(_deg(tru)):.1f},T")
    return None


def process_vtg():
    tt = _wrap360(_deg(get_live_data("navigation.courseOverGroundTrue")))
    mt = _wrap360(_deg(get_live_data("navigation.courseOverGroundMagnetic")))
    sog_kn = _kn(get_live_data("navigation.speedOverGround"))
    tt_str = f"{tt:.1f}" if tt is not None else ""
    mt_str = f"{mt:.1f}" if mt is not None else ""
    kts_str = f"{sog_kn:.1f}" if sog_kn is not None else ""
    kmph_str = f"{sog_kn * 1.852:.1f}" if sog_kn is not None else ""
    mode = "A" if kts_str else "V"
    fields = [
        tt_str, "T" if tt_str else "",
        mt_str, "M" if mt_str else "",
        kts_str, "N" if kts_str else "",
        kmph_str, "K" if kmph_str else "",
        mode,
    ]
    return _sentence("IIVTG," + ",".join(fields))


def process_vpw():
    vmg = get_live_data("performance.velocityMadeGood")   # m/s (magnitude)
    vmg_kn = _kn(vmg)
    vmg_kn_str = f"{vmg_kn:.1f}" if vmg_kn is not None else ""
    vmg_ms_str = f"{vmg:.1f}" if vmg is not None else ""
    return _sentence(f"IIVPW,{vmg_kn_str},N,{vmg_ms_str},M")


def process_gll():
    pos = get_live_data("navigation.position")
    if not pos:
        return None
    lat_part, lat_dir = _ddmm(pos["latitude"], is_lat=True)
    lon_part, lon_dir = _ddmm(pos["longitude"], is_lat=False)
    time_str = datetime.now(timezone.utc).strftime("%H%M%S")
    return _sentence(f"IIGLL,{lat_part},{lat_dir},{lon_part},{lon_dir},{time_str},A")


def process_vdr():
    set_mag = get_live_data("environment.current.setMagnetic")
    set_tru = get_live_data("environment.current.setTrue")
    drift_kn = _kn(get_live_data("environment.current.drift"))
    deg_true = _wrap360(_deg(set_tru)) if set_tru is not None else None
    deg_magnetic = _wrap360(_deg(set_mag)) if set_mag is not None else None

    def fmt(val, spec, unit):
        return f"{spec.format(val)},{unit}," if val is not None else ",,"

    body = (
        "IIVDR,"
        + fmt(deg_true, "{:.1f}", "T")
        + fmt(deg_magnetic, "{:.1f}", "M")
        + fmt(drift_kn, "{:.2f}", "N")
    )
    return _sentence(body)


def process_xdr_roll():
    ra = _deg(get_live_data("navigation.attitude.roll"))
    ra_str = f"{ra:.2f}" if ra is not None else ""
    return _sentence(f"IIXDR,A,{ra_str},D,ROLL")


def process_xdr_pitch():
    pt = _deg(get_live_data("navigation.attitude.pitch"))
    pt_str = f"{pt:.2f}" if pt is not None else ""
    return _sentence(f"IIXDR,A,{pt_str},D,PITCH")


# ── Channel map ───────────────────────────────────────────────────────────────

_TRIGGER_MAP = {
    "navigation.speedThroughWater":        process_vhw,
    "environment.depth.belowTransducer":   process_dbt,
    "steering.rudderAngle":                process_rsa,
    "electrical.batteries.house.voltage":  process_xdr_battv,
    "environment.wind.directionMagnetic":  process_mwd,
    "environment.wind.directionTrue":      process_mwd,
    "environment.wind.speedTrue":          process_mwv_true,
    "environment.wind.angleTrueWater":     process_mwv_true,
    "environment.wind.speedApparent":      process_mwv_apparent,
    "environment.wind.angleApparent":      process_mwv_apparent,
    "environment.outside.temperature":     process_mda,
    "environment.water.temperature":       process_mda,
    "environment.outside.pressure":        process_mda,
    "navigation.headingMagnetic":          process_hdm,
    "navigation.headingTrue":              process_hdm,
    "navigation.speedOverGround":          process_vtg,
    "navigation.courseOverGroundMagnetic": process_vtg,
    "navigation.courseOverGroundTrue":     process_vtg,
    "navigation.position":                 process_gll,
    "environment.current.drift":           process_vdr,
    "environment.current.setMagnetic":     process_vdr,
    "environment.current.setTrue":         process_vdr,
    "navigation.attitude.roll":            process_xdr_roll,
    "navigation.attitude.pitch":           process_xdr_pitch,
    "performance.velocityMadeGood":        process_vpw,
}


def _trigger(path):
    fn = _TRIGGER_MAP.get(path)
    if not fn:
        logger.debug(f"No trigger for path: {path}")
        return None
    try:
        return fn()
    except Exception as e:
        logger.error(f"Error in trigger for {path}: {e}")
        return None


# ── Handler class ─────────────────────────────────────────────────────────────

class NMEA0183Handler(OutputHandler):
    _host: str = DEFAULT_HOST
    _port: int = DEFAULT_UDP_PORT

    def __init__(self):
        self._last_sent: dict[str, datetime] = {}

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        pass

    def setup(self, args: argparse.Namespace) -> None:
        self._host = args.host
        self._port = args.udp_port
        logger.info(f"NMEA 0183 → {self._host}:{self._port}")

    def startup(self, udp_socket: socket.socket) -> None:
        pass

    def process_channel(self, path, old_entry, udp_socket):
        current = live_data.get(path)
        if not current:
            return

        new_comparable = current.get("value")
        old_comparable = old_entry.get("value") if old_entry else None

        now = datetime.now(timezone.utc)
        last_sent = self._last_sent.get(path)
        if last_sent is not None and (now - last_sent) < timedelta(seconds=MIN_SEND_INTERVAL):
            return
        age_exceeded = last_sent is None or (
            now - last_sent > timedelta(seconds=REBROADCAST_AGE)
        )

        if (new_comparable != old_comparable) or age_exceeded:
            message = _trigger(path)
            if message:
                try:
                    udp_socket.sendto(message.encode(), (self._host, self._port))
                    self._last_sent[path] = now
                    logger.debug(f"NMEA0183: {message.strip()}")
                except socket.error as e:
                    logger.error(f"Failed to send message: {e}")

    @property
    def udp_host(self) -> str:
        return self._host

    @property
    def udp_port(self) -> int:
        return self._port
