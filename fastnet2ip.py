import argparse
import threading
import time
import serial
import queue
import socket
import select
from datetime import datetime, timezone, timedelta

from fastnet_decoder import FrameBuffer, logger, set_log_level

BAUDRATE = 28800
TIMEOUT = 0.05
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY = serial.PARITY_ODD
BROADCAST_ADDRESS = "255.255.255.255"
READ_SIZE = 256

DEFAULT_UDP_PORT = 2002
OUTPUT_MONITOR_TIMEOUT = 1
REBROADCAST_AGE = 5

live_data = {}
live_data_lock = threading.Lock()


def calculate_nmea_checksum(sentence):
    checksum = 0
    for char in sentence:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def get_live_data(name, as_string=False):
    with live_data_lock:
        entry = live_data.get(name)
    if not entry:
        return None
    raw = entry.get("interpreted_value")
    if raw is None:
        return None
    if as_string:
        return str(raw)
    try:
        num = float(raw)
    except (TypeError, ValueError):
        logger.debug(f"get_live_data: {name!r} not a number ({raw!r})")
        return None
    return num


def get_live_layout(name):
    with live_data_lock:
        entry = live_data.get(name)
    return entry.get("layout") if entry else None


def update_live_data(channel_name, channel_id, interpreted_value, layout=None):
    timestamp = datetime.now(timezone.utc).isoformat()
    with live_data_lock:
        live_data[channel_name] = {
            "channel_id": channel_id,
            "interpreted_value": interpreted_value,
            "layout": layout,
            "timestamp": timestamp,
        }


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
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_dbt():
    df  = get_live_data("Depth (Feet)")
    dm  = get_live_data("Depth (Meters)")
    dfa = get_live_data("Depth (Fathoms)")

    depth_feet    = f"{df:.1f}"  if df  is not None else ""
    depth_meters  = f"{dm:.1f}"  if dm  is not None else ""
    depth_fathoms = f"{dfa:.1f}" if dfa is not None else ""

    body = f"IIDBT,{depth_feet},f,{depth_meters},M,{depth_fathoms},F"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_rsa():
    ra = get_live_data("Rudder Angle")

    if ra is not None:
        ra_str = f"{ra:.1f}"
        status = "A"
    else:
        ra_str = ""
        status = "V"

    body = f"IIRSA,{ra_str},{status},,"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_battv():
    bv = get_live_data("Battery Volts")
    bv_str = f"{bv:.2f}" if bv is not None else ""
    body = f"IIXDR,U,{bv_str},V,BATTV"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


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
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_mwv_true():
    twa = get_live_data("True Wind Angle")
    if twa is not None and twa < 0:
        twa += 360
    twa_str = f"{twa:.1f}" if twa is not None else ""

    tws = get_live_data("True Wind Speed (Knots)")
    tws_str = f"{tws:.1f}" if tws is not None else ""

    status = "A" if (twa_str and tws_str) else "V"
    body = f"IIMWV,{twa_str},T,{tws_str},N,{status}"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_mwv_apparent():
    awa = get_live_data("Apparent Wind Angle")
    if awa is not None and awa < 0:
        awa += 360
    awa_str = f"{awa:.1f}" if awa is not None else ""

    aws = get_live_data("Apparent Wind Speed (Knots)")
    aws_str = f"{aws:.1f}" if aws is not None else ""

    status = "A" if (awa_str and aws_str) else "V"
    body = f"IIMWV,{awa_str},R,{aws_str},N,{status}"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_mda():
    def val_unit(val, fmt, unit):
        return f"{fmt.format(val)},{unit}," if val is not None else ",,"

    bp_hpa     = get_live_data("Barometric Pressure")
    air_temp   = get_live_data("Air Temperature (°C)")
    water_temp = get_live_data("Sea Temperature (°C)")

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
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\r\n"


def process_hdm():
    mag = get_live_data("Heading")
    hdg_layout = get_live_layout("Heading")
    if hdg_layout != "°M":
        logger.debug(f"process_hdm: heading layout {hdg_layout!r} is not magnetic — skipping")
        return None
    mag_str = f"{mag:.1f}" if mag is not None else ""
    body = f"IIHDM,{mag_str},M"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_vtg():
    tt = get_live_data("Course Over Ground (True)")
    if tt is not None and tt < 0:
        tt += 360
    tt_str = f"{tt:.1f}" if tt is not None else ""

    mt = get_live_data("Course Over Ground (Mag)")
    if mt is not None and mt < 0:
        mt += 360
    mt_str = f"{mt:.1f}" if mt is not None else ""

    sog = get_live_data("Speed Over Ground")
    kts_str  = f"{sog:.1f}"           if sog is not None else ""
    kmph_str = f"{(sog * 1.852):.1f}" if sog is not None else ""

    mode = "A" if kts_str else "V"

    fields = [
        tt_str,
        "T" if tt_str else "",
        mt_str,
        "M" if mt_str else "",
        kts_str,
        "N" if kts_str else "",
        kmph_str,
        "K" if kmph_str else "",
        mode,
    ]
    body = "IIVTG," + ",".join(fields)
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_vpw():
    vmg = get_live_data("Velocity Made Good (Knots)")
    vmg_layout = get_live_layout("Velocity Made Good (Knots)")
    if vmg is not None and vmg_layout == "d[data]":
        vmg = -vmg
    vmg_kn_str = f"{vmg:.1f}" if vmg is not None else ""
    vmg_ms_str = f"{vmg * 0.514444:.1f}" if vmg is not None else ""
    body = f"IIVPW,{vmg_kn_str},N,{vmg_ms_str},M"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


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
    lon_part = latlon_str[lat_idx+1:lon_idx]
    lon_dir  = latlon_str[lon_idx]

    time_str = datetime.now(timezone.utc).strftime("%H%M%S")
    body = f"IIGLL,{lat_part},{lat_dir},{lon_part},{lon_dir},{time_str},A"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_raw_wind_angle():
    rwa = get_live_data("Apparent Wind Angle (Raw)")
    rwa_str = f"{rwa:.2f}" if rwa is not None else ""
    body = f"IIXDR,A,{rwa_str},V,RAW_WIND_A"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_raw_wind_speed():
    rws = get_live_data("Apparent Wind Speed (Raw)")
    rws_str = f"{rws:.2f}" if rws is not None else ""
    body = f"IIXDR,N,{rws_str},V,RAW_WIND_S"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


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
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\r\n"


def process_xdr_raw_bsp():
    raw = get_live_data("Boatspeed (Raw)")
    raw_str = f"{raw:.2f}" if raw is not None else ""
    body = f"IIXDR,N,{raw_str},V,RAW_BSP"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_roll():
    ra = get_live_data("Heel Angle")
    ra_str = f"{ra:.2f}" if ra is not None else ""
    body = f"IIXDR,A,{ra_str},D,ROLL"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_pitch():
    pt = get_live_data("Fore/Aft Trim")
    pt_str = f"{pt:.2f}" if pt is not None else ""
    body = f"IIXDR,A,{pt_str},D,PITCH"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


trigger_functions = {
    "Boatspeed (Knots)":          process_vhw,
    "Depth (Meters)":             process_dbt,
    "Rudder Angle":               process_rsa,
    "Battery Volts":              process_xdr_battv,
    "True Wind Direction":        process_mwd,
    "True Wind Speed (Knots)":    process_mwv_true,
    "True Wind Angle":            process_mwv_true,
    "Apparent Wind Speed (Knots)": process_mwv_apparent,
    "Apparent Wind Angle":        process_mwv_apparent,
    "Air Temperature (°C)":       process_mda,
    "Sea Temperature (°C)":       process_mda,
    "Barometric Pressure":        process_mda,
    "Heading":                    process_hdm,
    "Speed Over Ground":          process_vtg,
    "Course Over Ground (Mag)":   process_vtg,
    "Course Over Ground (True)":  process_vtg,
    "LatLon":                     process_gll,
    "Apparent Wind Angle (Raw)":  process_xdr_raw_wind_angle,
    "Apparent Wind Speed (Raw)":  process_xdr_raw_wind_speed,
    "Tidal Drift":                process_vdr,
    "Tidal Set":                  process_vdr,
    "Boatspeed (Raw)":            process_xdr_raw_bsp,
    "Heel Angle":                 process_xdr_roll,
    "Fore/Aft Trim":              process_xdr_pitch,
    "Velocity Made Good (Knots)": process_vpw,
}


def trigger_nmea_sentence(channel_name):
    trigger_function = trigger_functions.get(channel_name)
    if not trigger_function:
        logger.debug(f"No trigger function defined for channel: {channel_name}. Skipping.")
        return None
    try:
        logger.debug(f"Triggering function for {channel_name}")
        message = trigger_function()
        if not message:
            logger.debug(f"Trigger function for {channel_name} returned no message.")
        return message
    except Exception as e:
        logger.error(f"Error executing trigger for {channel_name}: {e}")
        return None


def print_live_data(frame_buffer):
    print("\033c", end="")
    header = f"{'Channel Name':<30} {'Channel ID':<12} {'Value':<25} {'Timestamp':<30}"
    print(header)
    print("-" * len(header))
    for channel_name, data in sorted(live_data.items()):
        channel_id = str(data.get("channel_id", "??"))
        value = str(data.get("interpreted_value", "N/A"))
        timestamp = str(data.get("timestamp", "N/A"))
        channel_name = str(channel_name) if channel_name else "Unknown"
        row = f"{channel_name:<30} {channel_id:<12} {value:<25} {timestamp:<30}"
        print(row)
    print("Buffer Size:", frame_buffer.get_buffer_size())
    print("\n")


def read_input_source(input_source, is_file):
    if is_file:
        try:
            time.sleep(TIMEOUT)
            return next(input_source)
        except StopIteration:
            logger.info("Finished reading from file.")
            return None
    else:
        rlist, _, _ = select.select([input_source], [], [], 1)
        if input_source in rlist:
            return input_source.read(READ_SIZE)
    return None


def process_frame_queue(frame_queue, udp_socket, udp_port):
    while not frame_queue.empty():
        try:
            frame = frame_queue.get_nowait()
            if not frame:
                logger.debug("Received None frame from queue. Skipping.")
                continue

            logger.debug(f"Processing decoded frame: {frame}")
            values = frame.get("values", {})
            for channel_name, channel_data in values.items():
                if not channel_data:
                    continue

                channel_id = channel_data.get("channel_id", "??")
                value = channel_data.get("value")
                layout = channel_data.get("layout")
                interpreted_value = value if value is not None else channel_data.get("display_text", "N/A")

                with live_data_lock:
                    old_entry = live_data.get(channel_name)
                    old_value = old_entry.get("interpreted_value") if old_entry else None
                    old_ts_str = old_entry.get("timestamp") if old_entry else None

                age_exceeded = True
                if old_ts_str:
                    try:
                        old_ts = datetime.fromisoformat(old_ts_str)
                        age_exceeded = (datetime.now(timezone.utc) - old_ts) > timedelta(seconds=REBROADCAST_AGE)
                    except ValueError:
                        age_exceeded = True

                update_live_data(channel_name, channel_id, interpreted_value, layout)
                logger.debug(f"{channel_name!r}: old={old_value!r}, new={interpreted_value!r}")

                if (interpreted_value != old_value) or age_exceeded:
                    message = trigger_nmea_sentence(channel_name)
                    if message:
                        try:
                            udp_socket.sendto(message.encode(), (BROADCAST_ADDRESS, udp_port))
                            logger.debug(f"Broadcasted message: {message.strip()}")
                        except socket.error as e:
                            logger.error(f"Failed to send message: {e}")
                else:
                    logger.debug(f"No rebroadcast for {channel_name!r}: value unchanged and < REBROADCAST_AGE")

        except queue.Empty:
            break
        except Exception as e:
            logger.error(f"Unexpected error while processing frame: {e}")


def setup_udp_socket():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return udp_socket


def initialize_input_source(args):
    if args.serial:
        try:
            logger.info(f"Reading data from serial port: {args.serial}")
            input_source = serial.Serial(
                port=args.serial,
                baudrate=BAUDRATE,
                bytesize=BYTE_SIZE,
                stopbits=STOP_BITS,
                parity=PARITY,
                timeout=0,
            )
            return input_source, False
        except serial.SerialException as e:
            logger.error(f"Failed to open serial port {args.serial}: {e}")
            logger.error("Please check the port name, permissions, or if the port is already in use.")
            return None, False
        except PermissionError:
            logger.error(f"Permission denied: Unable to access serial port {args.serial}.")
            logger.error("Try running with elevated privileges or check user permissions for the device.")
            return None, False
    elif args.file:
        try:
            logger.info(f"Reading data from file: {args.file}")
            with open(args.file, "r") as file:
                hex_data = file.read().strip().replace(" ", "")
                if not hex_data:
                    logger.error("The specified file is empty.")
                    return None, True
                binary_data = bytes.fromhex(hex_data)
                input_source = iter([binary_data[i:i+256] for i in range(0, len(binary_data), 256)])
                return input_source, True
        except FileNotFoundError:
            logger.error(f"File not found: {args.file}")
            return None, True
        except ValueError:
            logger.error("Invalid file format: Ensure the file contains valid hexadecimal data.")
            return None, True
    else:
        logger.error("No valid input source specified. Use --serial or --file.")
        return None, False


def main():
    parser = argparse.ArgumentParser(description="FastNet Protocol Decoder")
    parser.add_argument("--serial", type=str, help="Specify serial port (e.g., /dev/ttyUSB0)")
    parser.add_argument("--file", type=str, help="Specify the path to a hex file")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT, help="UDP port for broadcasting messages")
    parser.add_argument("--log-level", type=str, default="INFO", help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")
    parser.add_argument("--live-data", action="store_true", help="Enable live data display once per second")
    args = parser.parse_args()

    set_log_level(args.log_level)

    input_source, is_file = initialize_input_source(args)
    if not input_source:
        return

    udp_socket = setup_udp_socket()
    frame_buffer = FrameBuffer()
    last_live_data_print = time.time()

    try:
        while True:
            new_data = read_input_source(input_source, is_file)
            if new_data:
                frame_buffer.add_to_buffer(new_data)
                frame_buffer.get_complete_frames()
                process_frame_queue(frame_buffer.frame_queue, udp_socket, args.udp_port)

            if args.live_data and time.time() - last_live_data_print >= 1:
                print_live_data(frame_buffer)
                last_live_data_print = time.time()

            if is_file and new_data is None:
                break
    except KeyboardInterrupt:
        logger.info("Shutting down. Goodbye!")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
    finally:
        if isinstance(input_source, serial.Serial) and input_source.is_open:
            input_source.close()
        udp_socket.close()


if __name__ == "__main__":
    main()
