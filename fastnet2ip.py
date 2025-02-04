import argparse
import threading
import time
import serial
import queue
import socket
import select
#import logging  # Ensure logging is imported

from datetime import datetime
from fastnet_decoder import FrameBuffer, logger, set_log_level




# Configuration Constants
BAUDRATE = 28800
TIMEOUT = 0.1
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY = serial.PARITY_ODD
BROADCAST_ADDRESS = "255.255.255.255"
DEFAULT_UDP_PORT = 2002
OUTPUT_MONITOR_TIMEOUT = 1

frame_buffer = FrameBuffer()
live_data = {}
live_data_lock = threading.Lock()




# triggers
def process_boatspeed_nmea(boatspeed):
    """
    Generate NMEA sentence for boatspeed.
    """
    hdg_m = get_live_data("Heading")
    if hdg_m is not None:
        vhw_sentence = f"IIVHW,,,{hdg_m},M,{boatspeed:.1f},N,,"
    else:
        vhw_sentence = f"IIVHW,,,,,{boatspeed:.1f},N,,"
    return f"${vhw_sentence}*{calculate_nmea_checksum(vhw_sentence)}\n"


def process_depth_nmea(depth):
    """
    Generate NMEA sentence for depth.
    """
    dbt_sentence = f"IIDBT,,,{depth:.2f},M,,"
    return f"${dbt_sentence}*{calculate_nmea_checksum(dbt_sentence)}\n"


def process_rudder_angle_nmea(rudder_angle):
    """
    Generate NMEA sentence for rudder angle.
    """
    direction = "A"  # "A" indicates valid data
    rsa_sentence = f"IIRSA,{rudder_angle:.1f},{direction},,{direction}"
    return f"${rsa_sentence}*{calculate_nmea_checksum(rsa_sentence)}\n"


def process_battery_volts_nmea(battery_volts):
    """
    Generate NMEA sentence for battery voltage.
    """
    xdr_sentence = f"IIXDR,U,{battery_volts:.2f},V,BATTV"
    return f"${xdr_sentence}*{calculate_nmea_checksum(xdr_sentence)}\n"


def process_twd_nmea(twd):
    """
    Generate NMEA sentence for true wind direction.
    """
    mwd_sentence = f"WIMWD,,,{twd:.1f},M,,N"
    return f"${mwd_sentence}*{calculate_nmea_checksum(mwd_sentence)}\n"


def process_tws_nmea(tws):
    """
    Generate NMEA sentence for true wind speed and angle.
    """
    twa = get_live_data("True Wind Angle")
    if twa is not None and twa < 0:
        twa += 360  # Convert -180 to 180 range to 0 to 360
    mwv_sentence = f"IIMWV,{twa:.1f},T,{tws:.1f},N,A"
    return f"${mwv_sentence}*{calculate_nmea_checksum(mwv_sentence)}\n"

def process_twa_nmea(twa):
    """
    Generate NMEA sentence for true wind speed and angle.
    """
    tws = get_live_data("True Wind Speed (Knots)")
    if twa is not None and twa < 0:
        twa += 360  # Convert -180 to 180 range to 0 to 360
    mwv_sentence = f"IIMWV,{twa:.1f},T,{tws:.1f},N,A"
    return f"${mwv_sentence}*{calculate_nmea_checksum(mwv_sentence)}\n"

def process_aws_nmea(aws):
    """
    Generate NMEA sentence for apparent wind speed and angle.
    """
    awa = get_live_data("Apparent Wind Angle")
    if awa is not None and awa < 0:
        awa += 360  # Convert -180 to 180 range to 0 to 360
    mwv_sentence = f"IIMWV,{awa:.1f},R,{aws:.1f},N,A"  # "R" for relative wind angle
    return f"${mwv_sentence}*{calculate_nmea_checksum(mwv_sentence)}\n"

def process_awa_nmea(awa):
    """
    Generate NMEA sentence for apparent wind speed and angle.
    """
    aws = get_live_data("Apparent Wind Speed (Knots)")
    if awa is not None and awa < 0:
        awa += 360  # Convert -180 to 180 range to 0 to 360
    mwv_sentence = f"IIMWV,{awa:.1f},R,{aws:.1f},N,A"  # "R" for relative wind angle
    return f"${mwv_sentence}*{calculate_nmea_checksum(mwv_sentence)}\n"


def process_sea_temperature_nmea(sea_temp):
    """
    Generate NMEA sentence for sea temperature.
    """
    mtw_sentence = f"IIMTW,{sea_temp:.1f},C"
    return f"${mtw_sentence}*{calculate_nmea_checksum(mtw_sentence)}\n"


def process_heading_nmea(heading):
    """
    Generate NMEA sentence for heading.
    """
    hdg_sentence = f"IIHDG,{heading:.1f},,,,"
    return f"${hdg_sentence}*{calculate_nmea_checksum(hdg_sentence)}\n"


def process_cog_sog_nmea(sog):
    """
    Generate NMEA sentence for course over ground and speed over ground.
    """
    cog = get_live_data("Course Over Ground (Mag)")
    vtg_sentence = f"IIVTG,,,{cog:.1f},M,{sog:.1f},N,,K"
    return f"${vtg_sentence}*{calculate_nmea_checksum(vtg_sentence)}\n"


def process_gll_nmea(latlon_str):
    """
    Generate NMEA sentence for latitude and longitude.
    """
    lat_split_idx = max(latlon_str.find('N'), latlon_str.find('S'))
    lon_split_idx = max(latlon_str.find('E'), latlon_str.find('W'))
    if lat_split_idx == -1 or lon_split_idx == -1:
        raise ValueError("Invalid lat/lon format")
    lat_part = latlon_str[:lat_split_idx]
    lat_dir = latlon_str[lat_split_idx]
    lon_part = latlon_str[lat_split_idx + 1:lon_split_idx]
    lon_dir = latlon_str[lon_split_idx]
    current_time = datetime.utcnow().strftime("%H%M%S")
    gll_sentence = f"GPGLL,{lat_part},{lat_dir},{lon_part},{lon_dir},{current_time},A"
    return f"${gll_sentence}*{calculate_nmea_checksum(gll_sentence)}\n"


def measured_wind_angle_raw(wind_angle_raw):
    """
    Generate NMEA sentence for measured wind angle (raw).
    """
    xdr_sentence = f"IIXDR,A,{wind_angle_raw:.2f},V,Wind_A_Raw"
    return f"${xdr_sentence}*{calculate_nmea_checksum(xdr_sentence)}\n"


def measured_wind_angle_speed(wind_angle_speed):
    """
    Generate NMEA sentence for measured wind speed (raw).
    """
    xdr_sentence = f"IIXDR,N,{wind_angle_speed:.2f},V,Wind_S_Raw"
    return f"${xdr_sentence}*{calculate_nmea_checksum(xdr_sentence)}\n"


def tide_drift(tide_drift_spd):
    """
    Generate NMEA sentence for measured wind speed (raw).
    """
    xdr_sentence = f"IIXDR,N,{tide_drift_spd:.2f},V,DRIFT"
    return f"${xdr_sentence}*{calculate_nmea_checksum(xdr_sentence)}\n"

def tide_set(tide_set_angle):
    """
    Generate NMEA sentence for measured wind angle (raw).
    """
    xdr_sentence = f"IIXDR,A,{tide_set_angle:.2f},V,SET"
    return f"${xdr_sentence}*{calculate_nmea_checksum(xdr_sentence)}\n"



trigger_functions = {
    "Boatspeed (Knots)": process_boatspeed_nmea,
    "Depth (Meters)": process_depth_nmea,
    "Rudder Angle": process_rudder_angle_nmea,
    "Battery Volts": process_battery_volts_nmea,
    "True Wind Direction": process_twd_nmea,
    
    "True Wind Speed (Knots)": process_tws_nmea,        #Also relies on TWA
    "True Wind Angle": process_twa_nmea,        #Also relies on TWA

    "Apparent Wind Speed (Knots)": process_aws_nmea,    #Also relies on AWA
    "Apparent Wind Angle": process_awa_nmea,
    
    "Sea Temperature (°C)": process_sea_temperature_nmea,
    "Heading": process_heading_nmea,
    "Speed Over Ground": process_cog_sog_nmea,              #Also relies on COG
    "LatLon":process_gll_nmea,
    "Measured Wind Angle (Raw)":measured_wind_angle_raw,
    "Measured Wind Speed (Raw)":measured_wind_angle_speed,
    "Tidal Drift":tide_drift,
    "Tidal Set":tide_set
}



def calculate_nmea_checksum(sentence):
    """Calculate the NMEA checksum."""
    checksum = 0
    for char in sentence:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def get_live_data(name):
    """
    Retrieve live data by channel name.
    Returns the latest interpreted value or None if not available.
    """
    with live_data_lock:
        data = live_data.get(name)
        if data:
            return data.get("interpreted_value")
        return None



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


def trigger_nmea_sentence(channel_name, interpreted_value):
    """
    Executes the corresponding trigger function for the given channel name
    and returns the generated NMEA sentence.

    Args:
        channel_name (str): The name of the channel (e.g., "Boatspeed (Knots)").
        interpreted_value (any): The interpreted value to process.

    Returns:
        str or None: The NMEA sentence, or None if no sentence is generated.
    """
    trigger_function = trigger_functions.get(channel_name)
    if not trigger_function:
        logger.warning(f"No trigger function defined for channel: {channel_name}. Skipping.")
        return None

    try:
        logger.debug(f"Triggering function for {channel_name} with value: {interpreted_value}")
        message = trigger_function(interpreted_value)
        if not message:
            logger.warning(f"Trigger function for {channel_name} returned no message. Value: {interpreted_value}")
        return message
    except Exception as e:
        logger.error(f"Error executing trigger function for {channel_name} with value {interpreted_value}: {e}")
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
            time.sleep(.1)
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
                    message = trigger_nmea_sentence(channel_name, interpreted_value)
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