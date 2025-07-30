import argparse
import threading
import time
import serial
import queue
import socket
import select
from math import isnan
#import logging  # Ensure logging is imported

from datetime import datetime
from fastnet_decoder import FrameBuffer, logger, set_log_level




# Configuration Constants
BAUDRATE = 28800
TIMEOUT = 0.05
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY = serial.PARITY_ODD
#BROADCAST_ADDRESS = "127.0.0.1"
BROADCAST_ADDRESS = "255.255.255.255"

DEFAULT_UDP_PORT = 2002
OUTPUT_MONITOR_TIMEOUT = 1

frame_buffer = FrameBuffer()
live_data = {}
live_data_lock = threading.Lock()




# triggers
def process_vhw():
    """
    Generate NMEA VHW sentence for magnetic heading and boatspeed,
    pulling both values via get_live_data().
    """
    # --- Magnetic Heading
    hdg = get_live_data("Heading")  # finite float or None
    # --- Boatspeed
    bs = get_live_data("Boatspeed (Knots)")  # finite float or None

    # format or blank
    if hdg is not None:
        hdg_str = f"{hdg:.1f}"
        bs_str  = f"{bs:.1f}" if bs is not None else ""
        # include the 'M' only if we have a heading
        body = f"IIVHW,,,{hdg_str},M,{bs_str},N,,"
    else:
        bs_str = f"{bs:.1f}" if bs is not None else ""
        # omit heading fields entirely
        body = f"IIVHW,,,,,{bs_str},N,,"

    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_dbt():
    """
    Generate NMEA DBT sentence for depth below transducer,
    pulling feet, meters and fathoms via get_live_data().
    """
    # Pull each depth channel (finite float or None)
    df  = get_live_data("Depth (Feet)")
    dm  = get_live_data("Depth (Meters)")
    dfa = get_live_data("Depth (Fathoms)")

    # Format to one decimal if present, else empty
    depth_feet    = f"{df:.1f}"  if df  is not None else ""
    depth_meters  = f"{dm:.1f}"  if dm  is not None else ""
    depth_fathoms = f"{dfa:.1f}" if dfa is not None else ""

    # Build DBT payload
    body = f"IIDBT,{depth_feet},f,{depth_meters},M,{depth_fathoms},F"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_rsa():
    """
    Generate NMEA RSA sentence for rudder angle,
    pulling the angle via get_live_data().
    """
    # Pull the rudder angle (finite float or None)
    ra = get_live_data("Rudder Angle")

    if ra is not None:
        # valid angle → format and mark "A"
        ra_str = f"{ra:.1f}"
        status = "A"
    else:
        # missing/invalid → leave blank and mark "V"
        ra_str = ""
        status = "V"

    # Build RSA payload: <angle>,<status>,,<status>
    body = f"IIRSA,{ra_str},{status},,{status}"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_battv():
    """
    Generate NMEA XDR sentence for battery voltage,
    pulling the voltage via get_live_data().
    """
    # Pull the battery voltage (finite float or None)
    bv = get_live_data("Battery Voltage")
    # Format to two decimals if present, else leave empty
    bv_str = f"{bv:.2f}" if bv is not None else ""
    # Build XDR payload: transducer type U (voltage), value, unit V, name BATTV
    body = f"IIXDR,U,{bv_str},V,BATTV"
    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_mwd():
    """
    Generate NMEA MWD sentence for true wind direction and speed,
    pulling both fields via get_live_data().
    """
    # --- True Wind Direction (TWD)
    twd = get_live_data("True Wind Direction")  # finite float or None
    if twd is not None and twd < 0:
        twd += 360
    twd_str = f"{twd:.1f}" if twd is not None else ""

    # --- True Wind Speed (TWS) in knots
    tws = get_live_data("True Wind Speed (Knots)")  # finite float or None
    tws_str = f"{tws:.1f}" if tws is not None else ""

    # --- Convert knots → m/s
    if tws is not None:
        tws_ms = tws * 1852.0 / 3600.0
        tws_ms_str = f"{tws_ms:.1f}"
    else:
        tws_ms_str = ""

    # --- Build MWD payload
    # Format: WI MWD ,,,{TWD},M,{TWS},N,{TWS_m/s},M
    body = f"WIMWD,,,{twd_str},M,{tws_str},N,{tws_ms_str},M"

    # --- Checksum and sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_mwv_true():
    """
    Generate NMEA MWV sentence for true wind (reference 'T'),
    pulling both angle and speed via get_live_data().
    """
    # --- True Wind Angle (TWA)
    twa = get_live_data("True Wind Angle")  # finite float or None
    if twa is not None and twa < 0:
        twa += 360
    twa_str = f"{twa:.1f}" if twa is not None else ""

    # --- True Wind Speed (TWS)
    tws = get_live_data("True Wind Speed (Knots)")  # finite float or None
    tws_str = f"{tws:.1f}" if tws is not None else ""

    # --- Status flag: A = valid, V = invalid
    status = "A" if (twa_str and tws_str) else "V"

    # --- Assemble MWV body and checksum
    body = f"IIMWV,{twa_str},T,{tws_str},N,{status}"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_mwv_apparent():
    """
    Generate NMEA MWV sentence for apparent wind (reference 'R'),
    pulling both angle and speed via get_live_data().
    """
    # --- Apparent Wind Angle (AWA)
    awa = get_live_data("Apparent Wind Angle")  # finite float or None
    if awa is not None and awa < 0:
        awa += 360
    awa_str = f"{awa:.1f}" if awa is not None else ""

    # --- Apparent Wind Speed (AWS)
    aws = get_live_data("Apparent Wind Speed (Knots)")  # finite float or None
    aws_str = f"{aws:.1f}" if aws is not None else ""

    # --- Status flag: A = valid, V = invalid
    status = "A" if (awa_str and aws_str) else "V"

    # --- Assemble MWV body and checksum
    body = f"IIMWV,{awa_str},R,{aws_str},N,{status}"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_mtw():
    """
    Generate NMEA MTW sentence for sea temperature,
    pulling the temperature via get_live_data().
    """
    # Pull sea temperature (finite float or None)
    st = get_live_data("Sea Temperature")

    # Format to one decimal if present, else leave blank
    temp_str = f"{st:.1f}" if st is not None else ""

    # Build MTW payload
    body = f"IIMTW,{temp_str},C"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_hdm():
    """
    Generate NMEA HDM sentence for magnetic heading,
    pulling the heading via get_live_data().
    """
    # Pull the magnetic heading (finite float or None)
    mag = get_live_data("Heading")

    # Format to one decimal if present, else leave blank
    mag_str = f"{mag:.1f}" if mag is not None else ""

    # Build HDM payload: <heading degrees magnetic>,M
    body = f"IIHDM,{mag_str},M"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_vtg():
    """
    Generate NMEA VTG sentence for track made good and ground speed,
    pulling all fields via get_live_data().
    """
    # --- True Track made good (degrees true)
    tt = get_live_data("Course Over Ground (True)")
    if tt is not None and tt < 0:
        tt += 360
    tt_str = f"{tt:.1f}" if tt is not None else ""

    # --- Magnetic Track made good (degrees mag)
    mt = get_live_data("Course Over Ground (Mag)")
    if mt is not None and mt < 0:
        mt += 360
    mt_str = f"{mt:.1f}" if mt is not None else ""

    # --- Speed over ground (knots) and km/h
    sog = get_live_data("Speed Over Ground (Knots)")
    kts_str  = f"{sog:.1f}"                if sog is not None else ""
    kmph_str = f"{(sog * 1.852):.1f}"      if sog is not None else ""

    # --- FAA mode indicator: “A” = valid if we have a knot speed, else “V”
    mode = "A" if kts_str else "V"

    # --- Assemble fields in order
    fields = [
        tt_str,                     # True track
        "T" if tt_str else "",      # True track symbol
        mt_str,                     # Magnetic track
        "M" if mt_str else "",      # Mag track symbol
        kts_str,                    # SOG in knots
        "N" if kts_str else "",     # Knots symbol
        kmph_str,                   # SOG in km/h
        "K" if kmph_str else "",    # km/h symbol
        mode                        # FAA mode
    ]
    body = "IIVTG," + ",".join(fields)

    # --- Checksum and wrap
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_gll():
    """
    Generate NMEA GLL sentence for geographic position,
    pulling a combined lat/lon string via get_live_data().
    """
    # Pull the raw lat/lon string (e.g. "4916.45N12311.12W")
    latlon_str = get_live_data("LatLon", as_string=True)
    if not latlon_str:
        return None

    # find where latitude ends (N or S) and longitude ends (E or W)
    lat_idx = max(latlon_str.find('N'), latlon_str.find('S'))
    lon_idx = max(latlon_str.find('E'), latlon_str.find('W'))
    if lat_idx == -1 or lon_idx == -1:
        logger.debug(f"GLL: invalid position format ({latlon_str!r})")
        return None

    # split into parts
    lat_part = latlon_str[:lat_idx]
    lat_dir  = latlon_str[lat_idx]
    lon_part = latlon_str[lat_idx+1:lon_idx]
    lon_dir  = latlon_str[lon_idx]

    # UTC time of fix
    time_str = datetime.utcnow().strftime("%H%M%S")

    # build GLL body and wrap with checksum
    body = f"GPGLL,{lat_part},{lat_dir},{lon_part},{lon_dir},{time_str},A"
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_raw_wind_angle():
    """
    Generate NMEA XDR sentence for measured wind angle (raw),
    pulling the raw angle via get_live_data().
    """
    # Pull the raw wind-angle measurement (finite float or None)
    rwa = get_live_data("Measured Wind Angle Raw")

    # Format to two decimals if present, else leave empty
    rwa_str = f"{rwa:.2f}" if rwa is not None else ""

    # Build XDR payload: type A (angle), value, unit V, name RAW_WIND_A
    body = f"IIXDR,A,{rwa_str},V,RAW_WIND_A"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_raw_wind_s():
    """
    Generate NMEA XDR sentence for measured wind speed (raw),
    pulling the raw speed via get_live_data().
    """
    # Pull the raw wind-speed measurement (finite float or None)
    rws = get_live_data("Measured Wind Speed Raw")

    # Format to two decimals if present, else leave empty
    rws_str = f"{rws:.2f}" if rws is not None else ""

    # Build XDR payload: type N (speed), value, unit V, name RAW_WIND_S
    body = f"IIXDR,N,{rws_str},V,RAW_WIND_S"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_drift():
    """
    Generate NMEA XDR sentence for tide drift speed,
    pulling the drift speed via get_live_data().
    """
    # Pull the drift speed (finite float or None)
    drift = get_live_data("Tide Drift Speed")

    # Format to two decimals if present, else leave empty
    drift_str = f"{drift:.2f}" if drift is not None else ""

    # Build XDR payload: type N (speed), value, unit V, name DRIFT
    body = f"IIXDR,N,{drift_str},V,DRIFT"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_xdr_set():
    """
    Generate NMEA XDR sentence for tide set angle,
    pulling the set angle via get_live_data().
    """
    # Pull the tide set angle (finite float or None)
    ts = get_live_data("Tide Set Angle")

    # Format to two decimals if present, else leave empty
    ts_str = f"{ts:.2f}" if ts is not None else ""

    # Build XDR payload: type A (angle), value, unit V, name SET
    body = f"IIXDR,A,{ts_str},V,SET"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_xdr_raw_bsp():
    """
    Generate NMEA XDR sentence for raw boat speed (BSP),
    pulling the raw value via get_live_data().
    """
    # Pull the raw BSP measurement (finite float or None)
    raw = get_live_data("Raw BSP")

    # Format to two decimals if present, else leave empty
    raw_str = f"{raw:.2f}" if raw is not None else ""

    # Build XDR payload: type N (speed), value, unit V, name RAW_BSP
    body = f"IIXDR,N,{raw_str},V,RAW_BSP"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"

def process_xdr_roll():
    """
    Generate NMEA XDR sentence for roll angle,
    pulling the roll value via get_live_data().
    """
    # Pull the roll angle (finite float or None)
    ra = get_live_data("Roll")

    # Format to two decimals if present, else leave empty
    ra_str = f"{ra:.2f}" if ra is not None else ""

    # Build XDR payload: type A (angle), value, unit D (degrees), name ROLL
    body = f"IIXDR,A,{ra_str},D,ROLL"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


def process_xdr_pitch():
    """
    Generate NMEA XDR sentence for pitch angle,
    pulling the pitch value via get_live_data().
    """
    # Pull the pitch angle (finite float or None)
    pt = get_live_data("Pitch")

    # Format to two decimals if present, else leave empty
    pt_str = f"{pt:.2f}" if pt is not None else ""

    # Build XDR payload: type A (angle), value, unit D (degrees), name PITCH
    body = f"IIXDR,A,{pt_str},D,PITCH"

    # Calculate checksum and return full sentence
    checksum = calculate_nmea_checksum(body)
    return f"${body}*{checksum}\n"


trigger_functions = {
    "Boatspeed (Knots)": process_vhw,
    "Depth (Meters)": process_dbt,
    "Rudder Angle": process_rsa,
    "Battery Volts": process_xdr_battv,
    "True Wind Direction": process_mwd,
    
    "True Wind Speed (Knots)": process_mwv_true,
    "True Wind Angle": process_mwv_true,     

    "Apparent Wind Speed (Knots)": process_mwv_apparent,
    "Apparent Wind Angle": process_mwv_apparent,
    
    "Sea Temperature (°C)": process_mtw,
    "Heading": process_hdm,
  
    "Speed Over Ground": process_vtg,  
    "Course Over Ground (Mag)": process_vtg,  
    "Course Over Ground (True)": process_vtg,  

    "LatLon":process_gll,
    "Apparent Wind Angle (Raw)":process_xdr_raw_wind_angle,
    "Apparent Wind Speed (Raw)":process_xdr_raw_wind_s,
    "Tidal Drift":process_xdr_drift,
    "Tidal Set":process_xdr_set,
    "Boatspeed (Raw)":process_xdr_raw_bsp,
    "Heel Angle":process_xdr_roll,
    "Fore/Aft Trim":process_xdr_pitch
}



def calculate_nmea_checksum(sentence):
    """Calculate the NMEA checksum."""
    checksum = 0
    for char in sentence:
        checksum ^= ord(char)
    return f"{checksum:02X}"


#def get_live_data(name):
#    """
#    Retrieve live data by channel name.
#    Returns the latest interpreted value or None if not available.
#    """
#    with live_data_lock:
#        data = live_data.get(name)
#        if data:
#            return data.get("interpreted_value")
#        return None



def get_live_data(name, as_string=False):
    with live_data_lock:
        entry = live_data.get(name)
    if not entry:
        return None

    raw = entry.get("interpreted_value")
    if raw is None:
        return None

    if as_string:
        # Just return whatever was stored, as a string
        return str(raw)

    # Otherwise, try to parse + validate a float
    try:
        num = float(raw)
    except (TypeError, ValueError):
        logger.debug(f"get_live_data: {name!r} not a number ({raw!r})")
        return None
    return num





def update_live_data(channel_name, channel_id, interpreted_value):
    """
    Thread-safe function to update live_data with the latest frame data.
    """
    timestamp = datetime.utcnow().isoformat()
    with live_data_lock:  # Acquire lock to ensure thread safety
        live_data[channel_name] = {
            "channel_id": channel_id,
            "interpreted_value": interpreted_value,
            "timestamp": timestamp
        }
    #logger.debug(f"Live Data Updated: {channel_name} (ID: {channel_id}) = {interpreted_value} at {timestamp}")


#def trigger_nmea_sentence(channel_name, interpreted_value):
#    """
#    Executes the corresponding trigger function for the given channel name
#    and returns the generated NMEA sentence.
#
#    Args:
#        channel_name (str): The name of the channel (e.g., "Boatspeed (Knots)").
#        interpreted_value (any): The interpreted value to process.#
#
#    Returns:
#        str or None: The NMEA sentence, or None if no sentence is generated.
#    """
#    trigger_function = trigger_functions.get(channel_name)
#    if not trigger_function:
#        logger.warning(f"No trigger function defined for channel: {channel_name}. Skipping.")
#        return None#
##
#   try:
#        logger.debug(f"Triggering function for {channel_name} with value: {interpreted_value}")
#        message = trigger_function(interpreted_value)
#        if not message:
#            logger.warning(f"Trigger function for {channel_name} returned no message. Value: {interpreted_value}")
#        return message
#    except Exception as e:
#        logger.info(f"Error executing trigger function for {channel_name} with value {interpreted_value}: {e}")
#        return None



def trigger_nmea_sentence(channel_name):
    """
    Executes the corresponding trigger function for the given channel name
    and returns the generated NMEA sentence.

    Args:
        channel_name (str): The name of the channel (e.g., "Boatspeed (Knots)").
        interpreted_value (any): The interpreted value (now unused).

    Returns:
        str or None: The NMEA sentence, or None if no sentence is generated.
    """
    trigger_function = trigger_functions.get(channel_name)
    if not trigger_function:
        logger.warning(f"No trigger function defined for channel: {channel_name}. Skipping.")
        return None

    try:
        logger.debug(f"Triggering function for {channel_name}")
        # call with no arguments
        message = trigger_function()
        if not message:
            logger.warning(f"Trigger function for {channel_name} returned no message.")
        return message

    except Exception as e:
        logger.error(f"Error executing trigger for {channel_name}: {e}")
        return None



def print_live_data():

    # Clear console for clean output
    print("\033c", end="")

    # Print table header
    header = f"{'Channel Name':<30} {'Channel ID':<12} {'Value':<25} {'Timestamp':<30}"
    print(header)
    print("-" * len(header))

    # Sort live_data by channel name and print each row
    for channel_name, data in sorted(live_data.items()):
        channel_id = str(data.get("channel_id", "??"))
        value = str(data.get("interpreted_value", "N/A"))
        timestamp = str(data.get("timestamp", "N/A"))

        channel_name = str(channel_name) if channel_name else "Unknown"

        row = f"{channel_name:<30} {channel_id:<12} {value:<25} {timestamp:<30}"
        print(row)

    print("Buffer Size:", frame_buffer.get_buffer_size())  # Output: Buffer Size: X
    print("\n")  # Add a blank line for readability


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
            return input_source.read(256)
    return None


def process_frame_queue(frame_queue, udp_socket, udp_port):
    while not frame_queue.empty():
        try:
            frame = frame_queue.get_nowait()
            if not frame:
                logger.warning("Received None frame from queue. Skipping.")
                continue

            logger.debug(f"Processing decoded frame: {frame}")
            values = frame.get("values", {})
            for channel_name, channel_data in values.items():
                if channel_data:
                    channel_id = channel_data.get("channel_id", "??")
                    interpreted_value = channel_data.get("interpreted", "N/A")

                    # Update live data
                    update_live_data(channel_name, channel_id, interpreted_value)

                    # Generate and broadcast NMEA sentence
                    #message = trigger_nmea_sentence(channel_name, interpreted_value)
                    message = trigger_nmea_sentence(channel_name)
                    if message:
                        try:
                            udp_socket.sendto(message.encode(), (BROADCAST_ADDRESS, udp_port))
                            logger.debug(f"Broadcasted message: {message.strip()}")
                        except socket.error as e:
                            logger.error(f"Failed to send message: {e}")
        except queue.Empty:
            break
        except Exception as e:
            logger.error(f"Unexpected error while processing frame: {e}")



def setup_udp_socket():
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return udp_socket


def initialize_input_source(args):
    """
    Initialize the input source based on the arguments.

    Args:
        args (argparse.Namespace): Parsed command-line arguments.

    Returns:
        tuple: (input_source, is_file)
            - input_source: The input source (serial.Serial or file iterator).
            - is_file: True if the input source is a file, False otherwise.
    """
    if args.serial:
        try:
            logger.info(f"Reading data from serial port: {args.serial}")
            input_source = serial.Serial(
                port=args.serial,
                baudrate=BAUDRATE,
                bytesize=BYTE_SIZE,
                stopbits=STOP_BITS,
                parity=PARITY,
                timeout=0
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
    """
    Single-threaded main loop for processing FastNet frames and broadcasting NMEA sentences.
    Supports input from a serial port or a hex file.
    """
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

    last_live_data_print = time.time()  # Track the last time live data was printed

    try:
        while True:
            # Read data from the input source
            new_data = read_input_source(input_source, is_file)
            if new_data:
                frame_buffer.add_to_buffer(new_data)
                frame_buffer.get_complete_frames()

                process_frame_queue(frame_buffer.frame_queue, udp_socket, args.udp_port)

            # Check if live data should be printed
            if args.live_data and time.time() - last_live_data_print >= 1:
                print_live_data()
                last_live_data_print = time.time()

            if is_file and new_data is None:
                break  # End of file reached
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