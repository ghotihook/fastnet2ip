import argparse
import threading
import time
import serial
import queue
import socket
#import logging  # Ensure logging is imported

from datetime import datetime
from fastnet_decoder import FrameBuffer, logger, set_log_level, get_buffer_size




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
output_queue = queue.Queue(maxsize=1024)




# triggers
def process_boatspeed_nmea(boatspeed):
    vhw_sentence = f"IIVHW,,,,,{boatspeed:.1f},N,,"
    vhw_sentence = f"${vhw_sentence}*{calculate_nmea_checksum(vhw_sentence)}\n"
    output_queue.put(vhw_sentence)
    logger.debug(f"Boatspeed NMEA VHW sentence added: {vhw_sentence.strip()}")

def process_depth_nmea(depth):
    dbt_sentence = f"IIDBT,,,{depth:.2f},M,,"
    dbt_sentence = f"${dbt_sentence}*{calculate_nmea_checksum(dbt_sentence)}\n"
    output_queue.put(dbt_sentence)
    logger.debug(f"Depth NMEA DBT sentence added: {dbt_sentence.strip()}")

def process_rudder_angle_nmea(rudder_angle):
    direction = "A"  # "A" indicates valid data
    rsa_sentence = f"IIRSA,{rudder_angle:.1f},{direction},,{direction}"
    rsa_sentence = f"${rsa_sentence}*{calculate_nmea_checksum(rsa_sentence)}\n"
    output_queue.put(rsa_sentence)
    logger.debug(f"Rudder Angle NMEA RSA sentence added: {rsa_sentence.strip()}")

def process_battery_volts_nmea(battery_volts):
    xdr_sentence = f"IIXDR,U,{battery_volts:.2f},V,MAIN"
    xdr_sentence = f"${xdr_sentence}*{calculate_nmea_checksum(xdr_sentence)}\n"
    output_queue.put(xdr_sentence)
    logger.debug(f"Battery Volts NMEA XDR sentence added: {xdr_sentence.strip()}")


def process_twd_nmea(twd):
    mwd_sentence = f"WIMWD,,T,{twd:.1f},M,,N"
    mwd_sentence = f"${mwd_sentence}*{calculate_nmea_checksum(mwd_sentence)}\n"
    output_queue.put(mwd_sentence)
    logger.debug(f"Magnetic True Wind Direction NMEA MWD sentence added: {mwd_sentence.strip()}")

def process_twa_tws_nmea(tws):
    twa = get_live_data("True Wind Angle")
    mwv_sentence = f"IIMWV,{twa:.1f},T,{tws:.1f},N,A"
    mwv_sentence = f"${mwv_sentence}*{calculate_nmea_checksum(mwv_sentence)}\n"
    output_queue.put(mwv_sentence)
    logger.debug(f"True Wind NMEA MWV sentence added: {mwv_sentence.strip()}")
    

def process_awa_aws_nmea(aws):
    awa = get_live_data("Apparent Wind Angle")
    mwv_sentence = f"IIMWV,{awa:.1f},R,{aws:.1f},N,A"  # "R" for relative wind angle
    mwv_sentence = f"${mwv_sentence}*{calculate_nmea_checksum(mwv_sentence)}\n"
    output_queue.put(mwv_sentence)
    logger.debug(f"Apparent Wind NMEA MWV sentence added: {mwv_sentence.strip()}")

def process_sea_temperature_nmea(sea_temp):
    mtw_sentence = f"IIMTW,{sea_temp:.1f},C"
    mtw_sentence = f"${mtw_sentence}*{calculate_nmea_checksum(mtw_sentence)}\n"
    output_queue.put(mtw_sentence)
    logger.debug(f"Sea Temperature NMEA MTW sentence added: {mtw_sentence.strip()}")

def process_heading_nmea(heading):
    hdg_sentence = f"IIHDG,{heading:.1f},,,,"
    hdg_sentence = f"${hdg_sentence}*{calculate_nmea_checksum(hdg_sentence)}\n"
    output_queue.put(hdg_sentence)
    logger.debug(f"Heading NMEA HDG sentence added: {hdg_sentence.strip()}")

def process_cog_sog_nmea(sog):
    cog = get_live_data("Course Over Ground (Mag)")
    # Construct the VTG NMEA sentence
    vtg_sentence = f"IIVTG,,T,{cog:.1f},M,{sog:.1f},N,,K"
    vtg_sentence = f"${vtg_sentence}*{calculate_nmea_checksum(vtg_sentence)}\n"
    output_queue.put(vtg_sentence)
    logger.debug(f"VTG NMEA sentence added: {vtg_sentence.strip()}")


def process_gll_nmea(latlon_str):
    lat_split_idx = max(latlon_str.find('N'), latlon_str.find('S'))
    lon_split_idx = max(latlon_str.find('E'), latlon_str.find('W'))  # Fixed typo here
    if lat_split_idx == -1 or lon_split_idx == -1:
        raise ValueError("Invalid lat/lon format")
    lat_part = latlon_str[:lat_split_idx]
    lat_dir = latlon_str[lat_split_idx]
    lon_part = latlon_str[lat_split_idx + 1:lon_split_idx]
    lon_dir = latlon_str[lon_split_idx]
    current_time = datetime.utcnow().strftime("%H%M%S")
    gll_sentence = f"GPGLL,{lat_part},{lat_dir},{lon_part},{lon_dir},{current_time},A"
    gll_sentence = f"${gll_sentence}*{calculate_nmea_checksum(gll_sentence)}\n"
    output_queue.put(gll_sentence)
    logger.debug(f"GLL NMEA sentence added: {gll_sentence.strip()}")


trigger_functions = {
    "Boatspeed (Knots)": process_boatspeed_nmea,
    "Depth (Meters)": process_depth_nmea,
    "Rudder Angle": process_rudder_angle_nmea,
    "Battery Volts": process_battery_volts_nmea,
    "True Wind Direction": process_twd_nmea,
    "True Wind Speed (Knots)": process_twa_tws_nmea,        #Also relies on TWA
    "Apparent Wind Speed (Knots)": process_awa_aws_nmea,    #Also relies on AWA
    "Sea Temperature (°C)": process_sea_temperature_nmea,
    "Heading": process_heading_nmea,
    "Speed Over Ground": process_cog_sog_nmea,              #Also relies on COG
    "LatLon":process_gll_nmea
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

        
def output_monitor(udp_port):
    """Monitors the output_queue and broadcasts messages via UDP."""
    logger.info("Output Monitor started.")
    try:
        # Create and configure UDP broadcast socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", udp_port))
            logger.info(f"Broadcasting on {BROADCAST_ADDRESS}:{udp_port}")
            while True:
                try:
                    message = output_queue.get(timeout=OUTPUT_MONITOR_TIMEOUT)
                    if not message.strip():
                        logger.warning("Empty message detected; skipping broadcast.")
                        continue
                    sock.sendto(message.encode(), (BROADCAST_ADDRESS, udp_port))
                    logger.debug(f"Broadcasted message: {message.strip()}")
                except queue.Empty:
                    continue
                except socket.error as e:
                    logger.error(f"Socket error during broadcast: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error during message broadcast: {e}")
    except Exception as e:
        logger.error(f"Error initializing UDP socket: {e}")
    finally:
        logger.info("Output Monitor stopped.")



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
    Executes the corresponding trigger function for the given channel name.
    """
    trigger_function = trigger_functions.get(channel_name)
    if trigger_function:
        try:
            logger.debug(f"Triggering function for {channel_name} with value: {interpreted_value}")
            trigger_function(interpreted_value)
        except Exception as e:
            logger.error(f"Error executing trigger function for {channel_name}: {e}")

def producer_serial(port):
    with serial.Serial(port=port, baudrate=BAUDRATE, bytesize=BYTE_SIZE, stopbits=STOP_BITS, parity=PARITY, timeout=TIMEOUT) as ser:
        logger.info(f"Connected to {port} at {BAUDRATE} baud.")
        while True:
            new_data = ser.read(ser.in_waiting or 256)
            if new_data:
                frame_buffer.add_to_buffer(new_data)
            time.sleep(0.05)



def producer_file(file_path):
    try:
        with open(file_path, "r") as file:
            hex_data = file.read().strip().replace(" ", "")
            if not hex_data:
                logger.warning("The file is empty.")
                return

            binary_data = bytes.fromhex(hex_data)
            logger.info(f"Loaded {len(binary_data)} bytes of binary data from file.")

            # Emulation parameters for 22500 baud
            chunk_size = 256  # Send 256 bytes at a time
            delay_per_chunk = 0.11  # 110 ms delay per chunk (~2250 bytes/sec)

            for i in range(0, len(binary_data), chunk_size):
                chunk = binary_data[i:i + chunk_size]
                frame_buffer.add_to_buffer(chunk)
                logger.debug(f"Sent {len(chunk)} bytes of data from file (chunk {i // chunk_size + 1}).")
                time.sleep(delay_per_chunk)

            logger.info("Finished sending all data from file.")
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
    except ValueError:
        logger.error("Failed to convert hex data to binary. Ensure the file contains valid hexadecimal data.")
    except Exception as e:
        logger.error(f"Unexpected error in producer_file: {e}")



def consumer():
    """
    Processes decoded frames, updates live data, and triggers NMEA sentences if applicable.
    """
    while True:
        decoded_frames = frame_buffer.get_complete_frames()
        for frame in decoded_frames:
            logger.debug(f"Decoded frame contents: {frame}")
            values = frame.get("values", {})  # Get all channels in the frame

            for channel_name, channel_data in values.items():
                if channel_data is None:  # Skip if the value is None
                    continue
                channel_id = channel_data.get("channel_id", "??")
                interpreted_value = channel_data.get("interpreted", "N/A")

                # Update live data
                update_live_data(channel_name, channel_id, interpreted_value)

                # Trigger the NMEA sentence
                logger.debug(f"Checking if NMEA sentence should be triggered for {channel_name}")
                trigger_nmea_sentence(channel_name, interpreted_value)  # Ensure this is always called
        time.sleep(0.01)


def print_live_data():
    """
    Periodically prints the live_data dictionary in a readable, formatted table, sorted by channel name.
    """
    while True:
        with live_data_lock:  # Acquire lock to ensure thread-safe reading
            if not live_data:
                print("\033c", end="")  # Clear console
                print("No live data available.\n")
            else:
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

        time.sleep(1)  # Print live data every second


def main():
    parser = argparse.ArgumentParser(description="FastNet Protocol Decoder")
    parser.add_argument("--file", type=str, help="Specify the path to hex file")
    parser.add_argument("--serial", type=str, help="Specify serial port (e.g., /dev/ttyUSB0)")
    parser.add_argument("-u", "--udp-port", type=int, default=DEFAULT_UDP_PORT, help="UDP port for broadcasting messages")
    parser.add_argument("--log-level", type=str, default="INFO", help="Set the log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)")

    args = parser.parse_args()

    set_log_level(args.log_level)

    if args.serial:
        producer_thread = threading.Thread(target=producer_serial, args=(args.serial,), daemon=True)
    elif args.file:
        producer_thread = threading.Thread(target=producer_file, args=(args.file,), daemon=True)
    else:
        logger.error("Please specify either --serial or --file.")
        parser.print_help()
        return

    consumer_thread = threading.Thread(target=consumer, daemon=True)
    live_data_thread = threading.Thread(target=print_live_data, daemon=True)
    output_monitor_thread = threading.Thread(target=output_monitor, args=(args.udp_port,), daemon=True)

    producer_thread.start()
    consumer_thread.start()
    live_data_thread.start()
    output_monitor_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping decoder. Goodbye!")


if __name__ == "__main__":
    main()