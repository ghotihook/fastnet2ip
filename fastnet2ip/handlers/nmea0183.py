import argparse
import socket
from datetime import datetime, timezone, timedelta

from fastnet_decoder import logger

from fastnet2ip.core.data_store import live_data, get_live_data, get_live_layout
from fastnet2ip.handlers.base import OutputHandler

REBROADCAST_AGE = 5
DEFAULT_UDP_PORT = 2002
DEFAULT_HOST = "255.255.255.255"


# ── NMEA helpers ──────────────────────────────────────────────────────────────

def _checksum(sentence):
    cs = 0
    for c in sentence:
        cs ^= ord(c)
    return f"{cs:02X}"


def _sentence(body):
    return f"${body}*{_checksum(body)}\n"


# ── Process functions ─────────────────────────────────────────────────────────

def process_vhw():
    hdg = get_live_data("Heading")
    bs = get_live_data("Boatspeed (Knots)")
    hdg_layout = get_live_layout("Heading")
    bs_str = f"{bs:.1f}" if bs is not None else ""
    hdg_true_str, hdg_mag_str = "", ""
    if hdg is not None:
        if hdg_layout == "°M":
            hdg_mag_str = f"{hdg:.1f}"
        elif hdg_layout == "°T":
            hdg_true_str = f"{hdg:.1f}"
        else:
            logger.debug(f"process_vhw: unknown heading layout {hdg_layout!r}")
    body = (
        f"IIVHW,"
        f"{hdg_true_str},{'T' if hdg_true_str else ''},"
        f"{hdg_mag_str},{'M' if hdg_mag_str else ''},"
        f"{bs_str},N,,"
    )
    return _sentence(body)


def process_dbt():
    df  = get_live_data("Depth (Feet)")
    dm  = get_live_data("Depth (Meters)")
    dfa = get_live_data("Depth (Fathoms)")
    body = (
        f"IIDBT,"
        f"{f'{df:.1f}' if df is not None else ''},f,"
        f"{f'{dm:.1f}' if dm is not None else ''},M,"
        f"{f'{dfa:.1f}' if dfa is not None else ''},F"
    )
    return _sentence(body)


def process_rsa():
    ra = get_live_data("Rudder Angle")
    ra_str = f"{ra:.1f}" if ra is not None else ""
    status = "A" if ra is not None else "V"
    return _sentence(f"IIRSA,{ra_str},{status},,")


def process_xdr_battv():
    bv = get_live_data("Battery Volts")
    bv_str = f"{bv:.2f}" if bv is not None else ""
    return _sentence(f"IIXDR,U,{bv_str},V,BATTV")


def process_mwd():
    twd = get_live_data("True Wind Direction")
    twd_layout = get_live_layout("True Wind Direction")
    tws = get_live_data("True Wind Speed (Knots)")
    if twd is not None and twd < 0:
        twd += 360
    twd_str = f"{twd:.1f}" if twd is not None else ""
    tws_str = f"{tws:.1f}" if tws is not None else ""
    tws_ms_str = f"{tws * 1852.0 / 3600.0:.1f}" if tws is not None else ""
    twd_true_str, twd_mag_str = "", ""
    if twd_str:
        if twd_layout == "°T":
            twd_true_str = twd_str
        elif twd_layout == "°M":
            twd_mag_str = twd_str
        else:
            logger.debug(f"process_mwd: TWD layout {twd_layout!r} unknown — omitting direction")
    body = (
        f"IIMWD,"
        f"{twd_true_str},{'T' if twd_true_str else ''},"
        f"{twd_mag_str},{'M' if twd_mag_str else ''},"
        f"{tws_str},N,{tws_ms_str},M"
    )
    return _sentence(body)


def process_mwv_true():
    twa = get_live_data("True Wind Angle")
    if twa is not None and twa < 0:
        twa += 360
    twa_str = f"{twa:.1f}" if twa is not None else ""
    tws = get_live_data("True Wind Speed (Knots)")
    tws_str = f"{tws:.1f}" if tws is not None else ""
    status = "A" if (twa_str and tws_str) else "V"
    return _sentence(f"IIMWV,{twa_str},T,{tws_str},N,{status}")


def process_mwv_apparent():
    awa = get_live_data("Apparent Wind Angle")
    if awa is not None and awa < 0:
        awa += 360
    awa_str = f"{awa:.1f}" if awa is not None else ""
    aws = get_live_data("Apparent Wind Speed (Knots)")
    aws_str = f"{aws:.1f}" if aws is not None else ""
    status = "A" if (awa_str and aws_str) else "V"
    return _sentence(f"IIMWV,{awa_str},R,{aws_str},N,{status}")


def process_mda():
    def val_unit(val, fmt, unit):
        return f"{fmt.format(val)},{unit}," if val is not None else ",,"

    bp_hpa     = get_live_data("Barometric Pressure")
    air_temp   = get_live_data("Air Temperature (°C)")
    if air_temp is None:
        t_f = get_live_data("Air Temperature (°F)")
        if t_f is not None:
            air_temp = (t_f - 32) * 5 / 9
    water_temp = get_live_data("Sea Temperature (°C)")
    if water_temp is None:
        t_f = get_live_data("Sea Temperature (°F)")
        if t_f is not None:
            water_temp = (t_f - 32) * 5 / 9
    bp_inhg = bp_hpa * 0.0295299830714 if bp_hpa is not None else None
    bp_bar  = bp_hpa / 1000 if bp_hpa is not None else None
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
    cs = _checksum(body)
    return f"${body}*{cs}\r\n"


def process_hdm():
    hdg = get_live_data("Heading")
    hdg_layout = get_live_layout("Heading")
    hdg_str = f"{hdg:.1f}" if hdg is not None else ""
    if hdg_layout == "°M":
        return _sentence(f"IIHDM,{hdg_str},M")
    elif hdg_layout == "°T":
        return _sentence(f"IIHDT,{hdg_str},T")
    else:
        logger.debug(f"process_hdm: unknown heading layout {hdg_layout!r} — skipping")
        return None


def process_vtg():
    tt = get_live_data("Course Over Ground (True)")
    if tt is not None and tt < 0:
        tt += 360
    mt = get_live_data("Course Over Ground (Mag)")
    if mt is not None and mt < 0:
        mt += 360
    sog = get_live_data("Speed Over Ground")
    tt_str   = f"{tt:.1f}" if tt is not None else ""
    mt_str   = f"{mt:.1f}" if mt is not None else ""
    kts_str  = f"{sog:.1f}" if sog is not None else ""
    kmph_str = f"{sog * 1.852:.1f}" if sog is not None else ""
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
    vmg = get_live_data("Velocity Made Good (Knots)")
    vmg_layout = get_live_layout("Velocity Made Good (Knots)")
    if vmg is not None and vmg_layout == "d[data]":
        vmg = -vmg
    vmg_kn_str = f"{vmg:.1f}" if vmg is not None else ""
    vmg_ms_str = f"{vmg * 0.514444:.1f}" if vmg is not None else ""
    return _sentence(f"IIVPW,{vmg_kn_str},N,{vmg_ms_str},M")


def process_gll():
    latlon_str = get_live_data("LatLon", as_string=True)
    if not latlon_str:
        return None
    lat_idx = max(latlon_str.find('N'), latlon_str.find('S'))
    lon_idx = max(latlon_str.find('E'), latlon_str.find('W'))
    if lat_idx == -1 or lon_idx == -1:
        logger.debug(f"GLL: invalid position format ({latlon_str!r})")
        return None
    lat_part = latlon_str[:lat_idx]
    lat_dir  = latlon_str[lat_idx]
    lon_part = latlon_str[lat_idx + 1:lon_idx]
    lon_dir  = latlon_str[lon_idx]
    time_str = datetime.now(timezone.utc).strftime("%H%M%S")
    return _sentence(f"IIGLL,{lat_part},{lat_dir},{lon_part},{lon_dir},{time_str},A")


def process_xdr_raw_wind_angle():
    rwa = get_live_data("Apparent Wind Angle (Raw)")
    rwa_str = f"{rwa:.2f}" if rwa is not None else ""
    return _sentence(f"IIXDR,A,{rwa_str},V,RAW_WIND_A")


def process_xdr_raw_wind_speed():
    rws = get_live_data("Apparent Wind Speed (Raw)")
    rws_str = f"{rws:.2f}" if rws is not None else ""
    return _sentence(f"IIXDR,N,{rws_str},V,RAW_WIND_S")


def process_vdr():
    tidal_set = get_live_data("Tidal Set")
    tidal_layout = get_live_layout("Tidal Set")
    speed_knots = get_live_data("Tidal Drift")
    deg_true     = tidal_set if tidal_layout == "°T" else None
    deg_magnetic = tidal_set if tidal_layout == "°M" else None
    if tidal_set is not None and tidal_layout not in ("°T", "°M"):
        logger.debug(f"process_vdr: Tidal Set layout {tidal_layout!r} unknown — omitting direction")

    def fmt(val, spec, unit):
        return f"{spec.format(val)},{unit}," if val is not None else ",,"

    body = (
        "IIVDR,"
        + fmt(deg_true, "{:.1f}", "T")
        + fmt(deg_magnetic, "{:.1f}", "M")
        + fmt(speed_knots, "{:.2f}", "N")
    )
    cs = _checksum(body)
    return f"${body}*{cs}\r\n"


def process_xdr_raw_bsp():
    raw = get_live_data("Boatspeed (Raw)")
    raw_str = f"{raw:.2f}" if raw is not None else ""
    return _sentence(f"IIXDR,N,{raw_str},V,RAW_BSP")


def process_xdr_roll():
    ra = get_live_data("Heel Angle")
    ra_str = f"{ra:.2f}" if ra is not None else ""
    return _sentence(f"IIXDR,A,{ra_str},D,ROLL")


def process_xdr_pitch():
    pt = get_live_data("Fore/Aft Trim")
    pt_str = f"{pt:.2f}" if pt is not None else ""
    return _sentence(f"IIXDR,A,{pt_str},D,PITCH")


# ── Channel map ───────────────────────────────────────────────────────────────

_TRIGGER_MAP = {
    "Boatspeed (Knots)":           process_vhw,
    "Depth (Meters)":              process_dbt,
    "Rudder Angle":                process_rsa,
    "Battery Volts":               process_xdr_battv,
    "True Wind Direction":         process_mwd,
    "True Wind Speed (Knots)":     process_mwv_true,
    "True Wind Angle":             process_mwv_true,
    "Apparent Wind Speed (Knots)": process_mwv_apparent,
    "Apparent Wind Angle":         process_mwv_apparent,
    "Air Temperature (°C)":        process_mda,
    "Air Temperature (°F)":        process_mda,
    "Sea Temperature (°C)":        process_mda,
    "Sea Temperature (°F)":        process_mda,
    "Barometric Pressure":         process_mda,
    "Heading":                     process_hdm,
    "Speed Over Ground":           process_vtg,
    "Course Over Ground (Mag)":    process_vtg,
    "Course Over Ground (True)":   process_vtg,
    "LatLon":                      process_gll,
    "Apparent Wind Angle (Raw)":   process_xdr_raw_wind_angle,
    "Apparent Wind Speed (Raw)":   process_xdr_raw_wind_speed,
    "Tidal Drift":                 process_vdr,
    "Tidal Set":                   process_vdr,
    "Boatspeed (Raw)":             process_xdr_raw_bsp,
    "Heel Angle":                  process_xdr_roll,
    "Fore/Aft Trim":               process_xdr_pitch,
    "Velocity Made Good (Knots)":  process_vpw,
}


def _trigger(channel_name):
    fn = _TRIGGER_MAP.get(channel_name)
    if not fn:
        logger.debug(f"No trigger for channel: {channel_name}")
        return None
    try:
        return fn()
    except Exception as e:
        logger.error(f"Error in trigger for {channel_name}: {e}")
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

    def process_channel(self, channel_name, old_entry, udp_socket):
        current = live_data.get(channel_name)
        if not current:
            return

        new_val = current.get("value")
        new_comparable = new_val if new_val is not None else current.get("display_text")

        if old_entry:
            old_val = old_entry.get("value")
            old_comparable = old_val if old_val is not None else old_entry.get("display_text")
        else:
            old_comparable = None

        last_sent = self._last_sent.get(channel_name)
        age_exceeded = last_sent is None or (
            datetime.now(timezone.utc) - last_sent > timedelta(seconds=REBROADCAST_AGE)
        )

        if (new_comparable != old_comparable) or age_exceeded:
            message = _trigger(channel_name)
            if message:
                try:
                    udp_socket.sendto(message.encode(), (self._host, self._port))
                    self._last_sent[channel_name] = datetime.now(timezone.utc)
                    logger.debug(f"NMEA0183: {message.strip()}")
                except socket.error as e:
                    logger.error(f"Failed to send message: {e}")

    @property
    def udp_host(self) -> str:
        return self._host

    @property
    def udp_port(self) -> int:
        return self._port
