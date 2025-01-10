import threading
import queue
import serial
import subprocess
import logging
import logging.handlers
import time
import socket
import sys
import datetime
import os  # only using to clear the screen for printing
import argparse  # For command-line arguments


# Constants
RAW_QUEUE_SIZE = 1024
OUTPUT_QUEUE_SIZE = 1024


# Serial configuration
DEFAULT_SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 28800
TIMEOUT = 0.1
BYTE_SIZE = serial.EIGHTBITS  # 8 data bits
STOP_BITS = serial.STOPBITS_TWO  # 2 stop bits
PARITY = serial.PARITY_ODD  # Odd parity


# Output monitor configuration
BROADCAST_ADDRESS = "255.255.255.255"  # Broadcast address
DEFAULT_UDP_PORT = 2002  # Default port for broadcasting NMEA sentences
OUTPUT_MONITOR_TIMEOUT = 1  # Timeout for output queue below we loop again

# Syslog Setup
syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
logging.basicConfig(level=logging.DEBUG, handlers=[syslog_handler])
logger = logging.getLogger("FastnetLogger")

# Lock for thread-safe updates
live_data_lock = threading.Lock()

# Live data snapshot
live_data = {}

# Queues
raw_queue = queue.Queue(maxsize=RAW_QUEUE_SIZE)
output_queue = queue.Queue(maxsize=OUTPUT_QUEUE_SIZE)


ADDRESS_LOOKUP = {
    0xFA: "All 20/20s",
    0xFB: "All Halcyon FFDs",
    0xFC: "All Pilot FFDs",
    0xFD: "All Processor Nodes",
    0xFE: "All FFDs",
    0xFF: "Entire System",
    
    0x01: "Normal CPU (Depth Board in H2000)",
    0x05: "Normal CPU (Wind Board in H2000)",
    0x09: "Performance Processor",
    
    0x0D: "Expansion Unit",
    0x0E: "Expansion Unit",
    0x0F: "Expansion Unit",
    
    0x10: "Halcyon 2000 Compass (Directly Connected to FastNet Bus)",
    0x11: "Halcyon Gyro-Stabilised Compass (via ACP)",
    0x12: "Halcyon Gyro-Stabilised Compass (via Pilot ACP)",
    
    # FastNet Display Groups
    0x20: "FFD (20)",
    0x21: "FFD (21)",
    0x22: "FFD (22)",
    0x23: "FFD (23)",
    0x24: "FFD (24)",
    0x25: "FFD (25)",
    0x26: "FFD (26)",
    0x27: "FFD (27)",
    0x28: "FFD (28)",
    0x29: "FFD (29)",
    0x2A: "FFD (2A)",
    0x2B: "FFD (2B)",
    0x2C: "FFD (2C)",
    0x2D: "FFD (2D)",
    0x2E: "FFD (2E)",
    0x2F: "FFD (2F)",
    
    # Halcyon FFD Group
    0x30: "Halcyon FFD (30)",
    0x31: "Halcyon FFD (31)",
    0x32: "Halcyon FFD (32)",
    0x33: "Halcyon FFD (33)",
    0x34: "Halcyon FFD (34)",
    0x35: "Halcyon FFD (35)",
    0x36: "Halcyon FFD (36)",
    0x37: "Halcyon FFD (37)",
    0x38: "Halcyon FFD (38)",
    0x39: "Halcyon FFD (39)",
    0x3A: "Halcyon FFD (3A)",
    0x3B: "Halcyon FFD (3B)",
    0x3C: "Halcyon FFD (3C)",
    0x3D: "Halcyon FFD (3D)",
    0x3E: "Halcyon FFD (3E)",
    0x3F: "Halcyon FFD (3F)",

    # Display 20/20 Group
    0x40: "Display 20/20 (40)",
    0x41: "Display 20/20 (41)",
    0x42: "Display 20/20 (42)",
    0x43: "Display 20/20 (43)",
    0x44: "Display 20/20 (44)",
    0x45: "Display 20/20 (45)",
    0x46: "Display 20/20 (46)",
    0x47: "Display 20/20 (47)",
    
    # Pilot FFD Group
    0x50: "Pilot FFD (50)",
    0x51: "Pilot FFD (51)",
    0x52: "Pilot FFD (52)",
    0x53: "Pilot FFD (53)",
    0x54: "Pilot FFD (54)",
    0x55: "Pilot FFD (55)",
    0x56: "Pilot FFD (56)",
    0x57: "Pilot FFD (57)",
    0x58: "Pilot FFD (58)",
    0x59: "Pilot FFD (59)",
    0x5A: "Pilot FFD (5A)",
    0x5B: "Pilot FFD (5B)",
    0x5C: "Pilot FFD (5C)",
    0x5D: "Pilot FFD (5D)",
    0x5E: "Pilot FFD (5E)",
    0x5F: "Pilot FFD (5F)",
    
    # External Compass
    0x60: "External Compass (NMEA FFD 60)",
    0x61: "External Compass (NMEA FFD 61)",
    0x62: "External Compass (NMEA FFD 62)",
    0x63: "External Compass (NMEA FFD 63)",
    0x64: "External Compass (NMEA FFD 64)",
    0x65: "External Compass (NMEA FFD 65)",
    0x66: "External Compass (NMEA FFD 66)",
    0x67: "External Compass (NMEA FFD 67)",
    0x68: "External Compass (NMEA FFD 68)",
    0x69: "External Compass (NMEA FFD 69)",
    0x6A: "External Compass (NMEA FFD 6A)",
    0x6B: "External Compass (NMEA FFD 6B)",
    0x6C: "External Compass (NMEA FFD 6C)",
    0x6D: "External Compass (NMEA FFD 6D)",
    0x6E: "External Compass (NMEA FFD 6E)",
    0x6F: "External Compass (NMEA FFD 6F)"
}

COMMAND_LOOKUP = {
    0x01: "Broadcast",
    0x0C: "Keep Alive",
    0x03: "LatLon",
    0xC9: "Light Intensity",
}


CHANNEL_LOOKUP = {
    0x00: "Node Reset",
    0x06: "Something to do with ACP (0x06)",
    0x0B: "Rudder Angle",
    0x1C: "Air Temperature (°F)",
    0x1D: "Air Temperature (°C)",
    0x1E: "Sea Temperature (°F)",
    0x1F: "Sea Temperature (°C)",
    0x27: "Head/Lift Trend",
    0x29: "Off Course",
    0x32: "Tacking Performance",
    0x33: "Reaching Performance",
    0x34: "Heel Angle",
    0x35: "Optimum Wind Angle",
    0x36: "Depth Sounder Receiver Gain",
    0x37: "Depth Sounder Noise",
    0x3B: "Linear 4",
    0x3C: "Rate Motion",
    0x41: "Boatspeed (Knots)",
    0x42: "Boatspeed (Raw)",
    0x46: "Something to do with ACP (0x46)",
    0x47: "LatLon",
    0x49: "Heading",
    0x4A: "Heading (Raw)",
    0x4D: "Apparent Wind Speed (Knots)",
    0x4E: "Apparent Wind Speed (Raw)",
    0x4F: "Apparent Wind Speed (m/s)",
    0x51: "Apparent Wind Angle",
    0x52: "Apparent Wind Angle (Raw)",
    0x53: "Target TWA",
    0x55: "True Wind Speed (Knots)",
    0x56: "True Wind Speed (m/s)",
    0x57: "Measured Wind Speed (Knots)",
    0x59: "True Wind Angle",
    0x5A: "Measured Wind Angle Deg",
    0x69: "Course", #heading and leeway
    0x64: "Average Speed (Knots)",
    0x65: "Aberage Speed (raw)",
    0x6D: "True Wind Direction",
    0x6F: "Next Leg Apparent Wind Angle",
    0x75: "Timer",
    0x7D: "Target Boatspeed",
    0x7F: "Velocity Made Good (Knots)",
    0x81: "Dead Reckoning Distance",
    0x82: "Leeway",
    0x83: "Tidal Drift",
    0x84: "Tidal Set",
    0x85: "Upwash",
    0x86: "Barometric Pressure Trend",
    0x87: "Barometric Pressure",
    0x8D: "Battery Volts",
    0x9A: "Heading on Next Tack",
    0x9B: "Fore/Aft Trim",
    0x9C: "Mast Angle",
    0x9D: "Wind Angle to the Mast",
    0x9E: "Pitch Rate (Motion)",
    0xA6: "Autopilot Compass Target",
    0xAF: "Autopilot Off Course",
    0xC1: "Depth (Meters)",
    0xC2: "Depth (Feet)",
    0xC3: "Depth (Fathoms)",
    0xCD: "Stored Log (NM)",
    0xCF: "Trip Log (NM)",
    0xD3: "Dead Reckoning Course",
    0xE0: "Bearing Wpt. to Wpt. (True)",
    0xE1: "Bearing Wpt. to Wpt. (Mag)",
    0xE3: "Bearing to Waypoint (Rhumb True)",
    0xE4: "Bearing to Waypoint (Rhumb Mag)",
    0xE5: "Bearing to Waypoint (G.C. True)",
    0xE6: "Bearing to Waypoint (G.C. Mag)",
    0xE7: "Distance to Waypoint (Rhumb)",
    0xE8: "Distance to Waypoint (G.C.)",
    0xE9: "Course Over Ground (True)",
    0xEA: "Course Over Ground (Mag)",
    0xEB: "Speed Over Ground",
    0xEC: "Velocity Made Good (Course)",
    0xED: "Time to Waypoint",
    0xEE: "Cross Track Error",
    0xEF: "Remote 0",
    0xF0: "Remote 1",
    0xF1: "Remote 2",
    0xF2: "Remote 3",
    0xF3: "Remote 4",
    0xF4: "Remote 5",
    0xF5: "Remote 6",
    0xF6: "Remote 7",
    0xF7: "Remote 8",
    0xF8: "Remote 9",
    0xFA: "Next Waypoint Distance",
    0xFB: "Time to Layline"
}


FORMAT_SIZE_MAP = {
    0x00: 4,  # 32 bits (4 bytes)
    0x01: 2,  # 16 bits (2 bytes)
    0x02: 2,  # 16 bits (2 bytes)
    0x03: 2,  # 16 bits (2 bytes)
    0x04: 4,  # 32 bits (4 bytes)
    0x05: 4,  # 32 bits (4 bytes)
    0x06: 4,  # 16 bits (2 bytes)
    0x07: 4,  # 16 bits (2 bytes)
    0x08: 2,  # 16 bits (2 bytes)
    0x0A: 4   # 32 bits (4 bytes)
}


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
    lon_split_idx = max(latlon_str.find('E'), latlon_str.find('W'))
    if lat_split_idx == -1 or lon_split_idx == -1:
        raise ValueError("Invalid lat/lon format")
    lat_part = latlon_str[:lat_split_idx]
    lat_dir = latlon_str[lat_split_idx]
    lon_part = latlon_str[lat_split_idx + 1:lon_split_idx]
    lon_dir = latlon_str[lon_split_idx]
    current_time = datetime.datetime.utcnow().strftime("%H%M%S")
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







def parse_args():
    """
    Parse command-line arguments to get the serial port and UDP port.
    """
    parser = argparse.ArgumentParser(description="FastNet Logger for serial data processing.")
    parser.add_argument(
        "-s", "--serial-port",
        default=DEFAULT_SERIAL_PORT,
        help="Serial port to use (e.g., /dev/ttyUSB0, /dev/ttyAMA0)"
    )
    parser.add_argument(
        "-u", "--udp-port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help="UDP port to use for broadcasting (default: 2002)"
    )
    args = parser.parse_args()
    return args.serial_port, args.udp_port


# Checksum Calculation
def calculate_checksum(data):
    """Calculate checksum for a given data block."""
    checksum = (0x100 - sum(data) % 0x100) & 0xFF
    return checksum


def reset_serial_port(port):
    """Reset the serial port using stty."""
    try:
        subprocess.run(['stty', '-F', port, 'sane'], check=True)
        logger.info(f"Serial port {port} reset successfully.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to reset serial port {port}: {e}")



def serial_reader(port):
    """Reads data from the serial port and pushes it into raw_queue."""
    ser = None  # Ensure ser is defined for the finally block
    try:
        logger.info(f"Attempting to open serial port {port} at {BAUDRATE} baud.")

        # Open serial port with specified configuration
        ser = serial.Serial(
            port=port,
            baudrate=BAUDRATE,
            bytesize=BYTE_SIZE,
            stopbits=STOP_BITS,
            parity=PARITY,
            timeout=TIMEOUT,
        )

        logger.info("Serial Reader started.")
        while True:
            data = ser.read(ser.in_waiting or 256)
            if data:
                raw_queue.put(data)
                logger.debug(f"Read {len(data)} bytes from serial port.")
            else:
                time.sleep(0.01)  # Prevent busy-waiting

    except serial.SerialException as e:
        logger.error(f"Serial error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in serial_reader: {e}", exc_info=True)
    finally:
        if ser and ser.is_open:
            ser.close()
            logger.info("Serial port closed.")




def frame_scanner():
    """
    Scans the buffer for a valid frame, extracts the header and body,
    validates both the header checksum and body checksum. If valid, passes the frame to process_frame.
    """
    buffer = bytearray()
    logger.info("frame_scanner: Started.")
    
    while True:
        try:
            # Get data from raw queue and extend the buffer
            data = raw_queue.get(timeout=1)
            buffer.extend(data)
            logger.debug(f"frame_scanner: Buffer length: {len(buffer)} bytes after reading data.")
            
            while len(buffer) >= 6:  # Minimum size (header + body checksum)
                # Extract header components
                to_address = buffer[0]
                from_address = buffer[1]
                body_size = buffer[2]
                command = buffer[3]
                header_checksum = buffer[4]

                # Calculate header checksum
                calculated_header_checksum = calculate_checksum(buffer[:4])  # Checksum for To, From, Size, Command
                if calculated_header_checksum != header_checksum:
                    logger.warning(f"frame_scanner: Header checksum mismatch. Dropping first byte: {buffer[0]:02X}")
                    buffer.pop(0)  # Drop the first byte and try again
                    continue #We continue here instead of break so we don't wait for more bytes

                # Check if the full frame (header + body + body checksum) is available
                full_frame_length = 5 + body_size + 1  # 5 bytes for header, body_size bytes for body, 1 byte for body checksum
                if len(buffer) < full_frame_length:
                    logger.debug("frame_scanner: Waiting for more bytes to complete the frame.")
                    break  # Exit the loop and wait for more bytes

                # Extract body and body checksum directly from buffer
                body = buffer[5:full_frame_length - 1]  # Extract body (excluding checksum)
                body_checksum = buffer[full_frame_length - 1]  # The last byte is the body checksum
                frame = buffer[:full_frame_length] #header + Body without body CS

                # Calculate body checksum
                calculated_body_checksum = calculate_checksum(body)
                if calculated_body_checksum != body_checksum:
                    logger.warning(f"frame_scanner: Body checksum mismatch. Dropping frame.")
                    buffer.pop(0)  # Drop the first byte and try again
                    continue

                # If everything is valid, pass the frame (header + body) for processing
                logger.debug(f"frame_scanner: Valid frame received: {frame.hex()} from buffer {buffer.hex()} passing to process_frame")
                if command == 0x01:
                    process_frame(frame)
                elif command == 0x03:
                    process_ascii_frame(frame)


                # Remove processed frame (including body checksum) from buffer
                buffer = buffer[full_frame_length:]

        except queue.Empty:
            logger.debug("frame_scanner: Waiting for more data")
            continue
        except Exception as e:
            logger.error(f"frame_scanner: Error: {e}")


def process_frame(frame):
    """
    Processes a full frame (header + body) after checksum validation.
    Decodes the format byte to determine the correct size of data bytes.
    """
    try:
        #before we start, remove the body checksum off the frame, we don't need it anymore
        frame = frame[:-1]

        # Parse header
        to_address = frame[0]
        from_address = frame[1]
        body_size = frame[2]
        command = frame[3]
        body = frame[5:]  # Body starts after the checksum byte

        to_name = ADDRESS_LOOKUP.get(to_address, f"Unknown ({to_address:02X})")
        from_name = ADDRESS_LOOKUP.get(from_address, f"Unknown ({from_address:02X})")
        command_name = COMMAND_LOOKUP.get(command, f"Unknown ({command:02X})")

        logger.debug(f"process_frame: To: {to_name:}, From: {from_name}, Command: {command_name}")

        # Process each channel/format/data set in the body
        index = 0
        while index < len(body):
            if index + 2 > len(body):  # Ensure at least channel ID and format byte are available
                logger.warning(f"process_frame: Incomplete channel/format/data set at the end of body. index={index} len(body)={len(body)}")
                break

            # Extract channel ID and format byte
            channel_id = body[index]
            format_byte = body[index + 1]
            index += 2
            

            # Decode the format byte to get the expected data size
            format_type = format_byte & 0x0F  # Last 4 bits determine the format type
            data_length = FORMAT_SIZE_MAP.get(format_type, 0)  # Look up the byte size

            if data_length == 0:
                logger.error(f"process_frame: Unknown format type: {format_type:02X}. Cannot determine data size.")
                break

            if index + data_length > len(body):
                logger.warning(f"process_frame: Incomplete data for channel 0x{channel_id:02X}. Expected {data_length} bytes.")
                break

            # Extract only the data bytes for this channel
            data_bytes = body[index:index + data_length]
            index += data_length  # Move to the next set

            # Decode the data using decode_format_and_data
            decoded_result = decode_format_and_data(channel_id,format_byte, data_bytes)
            if not decoded_result:
                logger.error(f"process_frame: Failed to decode data for channel 0x{channel_id:02X}.")
                continue

            # Extract interpreted value from the decoded result
            
            channel_name = CHANNEL_LOOKUP.get(channel_id, f"process_frame: Unknown Channel (0x{channel_id:02X})")
            format_byte = decoded_result.get("format_byte")
            format_bits = decoded_result.get("format_bits")
            raw_value = decoded_result.get("raw")          
            interpreted_value = decoded_result.get("interpreted")

        

            # Log the decoded value
            logger.debug(f"process_frame: inside frame decoder loop: Channel: {channel_name}, Format bits: {format_byte}, Raw Value: {raw_value} Decoded Value: {interpreted_value}")
            update_live_data(channel_name, interpreted_value)

    except Exception as e:
        logger.error(f"process_frame: Error processing frame: {e}")







def process_ascii_frame(frame):
        channel_id=frame[5]
        format_byte=frame[6]
        unsure=frame[7]
        data_bytes=frame[7:-1]
        channel_name = CHANNEL_LOOKUP.get(channel_id, f"process_ascii_frame: Unknown Channel (0x{channel_id:02X})")
        logger.debug(f"process_ascii_frame: inside frame decoder loop: Channel: {channel_name}, data_bytes{data_bytes}")

        try:
            ascii_text = data_bytes.decode("ascii").strip()
            logger.debug(f"decode_format_and_data: Decoded ASCII Text: {ascii_text}")
            interpreted_value = ascii_text
            raw_value = ascii_text
        except UnicodeDecodeError:
            logger.error("decode_format_and_data: Failed to decode ASCII text.")
            return None

        logger.debug(f"process_ascii_frame: inside frame decoder loop: Channel: {channel_name}, Format bits: {format_byte}, Raw Value: {raw_value} Decoded Value: {interpreted_value}")
        update_live_data(channel_name, interpreted_value)



def decode_format_and_data(channel_id, format_byte, data_bytes):
    """
    Decodes the format byte and interprets the data accordingly.

    Parameters:
        format_byte (int): The format byte indicating divisor, digits, and data interpretation.
        data_bytes (bytes): The raw data to decode.

    Returns:
        dict: Decoded results including format details and the final interpreted value.
    """
    try:

        # Extract format information from the format byte
        divisor_bits = (format_byte >> 6) & 0b11  # First two bits
        digits_bits = (format_byte >> 4) & 0b11   # Next two bits
        format_bits = format_byte & 0b1111        # Last four bits

        # Map divisor and digits bits to actual values
        divisor_map = {0b00: 1, 0b01: 10, 0b10: 100, 0b11: 1000}
        digits_map = {0b00: 1, 0b01: 2, 0b10: 3, 0b11: 4}

        divisor = divisor_map.get(divisor_bits, 1)
        digits = digits_map.get(digits_bits, 1)

        if len(data_bytes) == 0:
            logger.warning("decode_format_and_data: Empty data bytes; cannot decode.")
            return None



        if format_bits == 0x01:  # 16-bit signed integer
            if len(data_bytes) != 2:
                logger.warning("decode_format_and_data: Data length mismatch for 16-bit signed integer (expected 2 bytes).")
                return None
            raw_value = int.from_bytes(data_bytes, byteorder="big", signed=True)
            interpreted_value = raw_value / divisor

        elif format_bits == 0x02:  # 6-bit segment code + 10-bit unsigned value
            if len(data_bytes) != 2:
                logger.warning("decode_format_and_data: Data length mismatch for 6-bit segment code + 10-bit unsigned (expected 2 bytes).")
                return None
            segment_code = (data_bytes[0] >> 2) & 0b111111  # 6-bit segment code
            unsigned_value = ((data_bytes[0] & 0b11) << 8) | data_bytes[1]  # 10-bit unsigned value
            interpreted_value = unsigned_value / divisor
            raw_value = {"segment_code": segment_code, "unsigned_value": unsigned_value}

        elif format_bits == 0x03:  # 7-bit segment + 9-bit unsigned
            if len(data_bytes) != 2:
                logger.warning("decode_format_and_data: Data length mismatch for 7-bit segment + 9-bit unsigned (expected 2 bytes).")
                return None
            segment_code = (data_bytes[0] >> 1) & 0b01111111  # 7-bit segment
            unsigned_value = ((data_bytes[0] & 0b1) << 8) | data_bytes[1]  # 9-bit unsigned value
            interpreted_value = unsigned_value / divisor
            raw_value = {"segment_code": segment_code, "unsigned_value": unsigned_value}

        elif format_bits == 0x04:  # 8-bit segment + 24-bit unsigned value
            if len(data_bytes) != 4:
                logger.warning("decode_format_and_data: Data length mismatch for 8-bit + 24-bit unsigned (expected 4 bytes).")
                return None
            segment_code = data_bytes[0]  # 8-bit segment code
            unsigned_value = int.from_bytes(data_bytes[1:], byteorder="big", signed=False)  # 24-bit unsigned value
            interpreted_value = unsigned_value / divisor
            raw_value = {"segment_code": segment_code, "unsigned_value": unsigned_value}


        elif format_bits == 0x05:  # Timer format (XX YY ZZ WW)
            if len(data_bytes) != 4:
                logger.warning("decode_format_and_data: Data length mismatch for timer format (expected 4 bytes).")
                return None

            # Extract timer values
            useless = data_bytes[0]  # Useless byte (can be ignored)
            hours = data_bytes[1]  # Hours (may exceed 24)
            minutes = data_bytes[2]  # Minutes
            seconds = data_bytes[3]  # Seconds
            # Return a timedelta object for durations > 24hrs
            interpreted_value = datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
            raw_value = {"useless": useless, "hours": hours, "minutes": minutes, "seconds": seconds}


        elif format_bits == 0x06:  # 7-segment display text
            if len(data_bytes) != 4:
                logger.warning("decode_format_and_data: Data length mismatch for 7-segment display text (expected 4 bytes).")
                return None
            segment_text = "".join(convert_segment_b_to_char(byte) for byte in data_bytes)
            logger.debug(f"decode_format_and_data: Decoded 7-segment text: {segment_text}")
            raw_value = [f"{byte:02X}" for byte in data_bytes]  # Raw bytes as hex strings
            interpreted_value = segment_text


        elif format_bits == 0x07:  # 15-bit unsigned value with 4-byte input
            if len(data_bytes) != 4:
                logger.warning("decode_format_and_data: Data length mismatch for 15-bit unsigned (expected 4 bytes).")
                return None
            msb = (data_bytes[2] >> 1) & 0b01111111  # 7 bits from third byte
            lsb = data_bytes[3]  # Full 8 bits from fourth byte
            unsigned_value = (msb << 8) | lsb  # Combine MSB and LSB into 15-bit value
            interpreted_value = unsigned_value / divisor
            raw_value = unsigned_value
            
        
        elif format_bits == 0x08:  # 7-bit segment + 9-bit unsigned (0x08 format)
            if len(data_bytes) != 2:
                logger.warning("decode_format_and_data: Data length mismatch for 0x08 (7-bit segment + 9-bit unsigned).")
                return None
            segment_code = (data_bytes[0] >> 1) & 0b01111111  # 7-bit segment
            unsigned_value = ((data_bytes[0] & 0b1) << 8) | data_bytes[1]  # 9-bit unsigned value
            interpreted_value = unsigned_value / divisor
            raw_value = {"segment_code": segment_code, "unsigned_value": unsigned_value}

        elif format_bits == 0x0A:  # 16-bit signed + 16-bit signed
            if len(data_bytes) != 4:
                logger.warning("decode_format_and_data: Data length mismatch for 16-bit + 16-bit signed (expected 4 bytes).")
                return None
            first_value = int.from_bytes(data_bytes[:2], byteorder="big", signed=True)  # First 16-bit signed integer
            second_value = int.from_bytes(data_bytes[2:], byteorder="big", signed=True)  # Second 16-bit signed integer
            interpreted_first_value = first_value / divisor
            interpreted_second_value = second_value / divisor
            interpreted_value = {"first": interpreted_first_value, "second": interpreted_second_value}
            raw_value = {"first_raw": first_value, "second_raw": second_value}
        else:
            logger.error(f"decode_format_and_data: Unsupported format: {format_bits:04b}.")
            return None

        # Return the result
        result = {
            "channel_id": f"{channel_id:02X}",
            "format_byte": f"{format_byte:08b}",
            "data bytes": f"{data_bytes.hex()}",
            "divisor": divisor,
            "digits": digits,
            "format_bits": format_bits,
            "raw": raw_value,
            "interpreted": interpreted_value
        }
        #logger.debug(f"decode_format_and_data: Decoded result: {result}")
        return result

    except Exception as e:
        logger.error(f"decode_format_and_data: Error decoding format and data: {e}")
        return None




def convert_segment_b_to_char(segment_byte):
    """
    Converts a 7-segment display byte into a human-readable character.
    
    Parameters:
        segment_byte (int): The byte representing the 7-segment display.

    Returns:
        str: The corresponding character or '?' if unknown.
    """
    segment_mapping = {
        0x00: " ",
        0xBE: "O",
        0xE8: "F",
        0x62: "n",
        0x72: "o"
    }
    return segment_mapping.get(segment_byte, "?")


def calculate_nmea_checksum(sentence):
    """Calculate NMEA checksum."""
    checksum = 0
    for char in sentence:
        checksum ^= ord(char)
    return f"{checksum:02X}"




def output_monitor(udp_port):
    """Monitors the output_queue and broadcasts NMEA sentences via UDP."""
    logger.info("Output Monitor started.")

    try:
        # Create and configure the UDP broadcast socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)  # Enable broadcast
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # Allow address reuse
            sock.bind(("", udp_port))  # Bind to the local port for proper broadcasting

            logger.info(f"Broadcasting on {BROADCAST_ADDRESS}:{udp_port}")

            while True:
                try:
                    # Retrieve message from the queue
                    message = output_queue.get(timeout=OUTPUT_MONITOR_TIMEOUT)
                    if not message.strip():
                        logger.warning("Empty message detected; skipping broadcast.")
                        continue

                    # Send message via UDP broadcast
                    sock.sendto(message.encode(), (BROADCAST_ADDRESS, udp_port))
                    logger.debug(f"Broadcasted message: {message.strip()}")
                except queue.Empty:
                    logger.debug("Output queue is empty; no message to broadcast.")
                    continue
                except socket.error as e:
                    logger.error(f"Socket error during broadcast: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error during message broadcast: {e}")

    except Exception as e:
        logger.error(f"Error initializing UDP socket: {e}")
    finally:
        logger.info("Output Monitor stopped.")



def update_live_data(channel_name, raw_value):
    """
    Updates live data with the latest value and timestamp.
    Calls the trigger function if one exists for the channel.
    """
    timestamp = datetime.datetime.utcnow().isoformat()

    # Add or update the live data
    if channel_name not in live_data:
        logger.info(f"update_live_date: New channel added to live data: {channel_name}")

    live_data[channel_name] = {"value": raw_value, "timestamp": timestamp}
    logger.debug(f"update_live_date: Updated {channel_name}: Value = {raw_value}, Timestamp = {timestamp}")

    # Trigger the corresponding function if it exists
    trigger_function = trigger_functions.get(channel_name)
    if trigger_function:
        try:
            logger.debug(f"update_live_date: Triggering function for {channel_name}")
            trigger_function(raw_value)
        except Exception as e:
            logger.error(f"update_live_date: Error while executing trigger function for {channel_name}: {e}")


def get_live_data(name):
    """
    Retrieves the live data value for the given name.
    Logs an error if the key doesn't exist and returns None.
    """
    if name not in live_data:
        logger.error(f"get_live_data: Invalid live_data key: {name}. No data retrieved.")
        return None
    
    with live_data_lock:
        data = live_data.get(name)
        if not isinstance(data, dict):
            logger.error(f"get_live_data: Unexpected data format for {name}: {data}")
            return None

        value = data.get("value")  # Retrieve only the value
        timestamp = data.get("timestamp")  # Retrieve the timestamp for logging
        logger.debug(f"get_live_data: Live Data Retrieved | {name}: {value}, Timestamp: {timestamp}")
        return value  # Return only the value


def apply_correction(bsp, twa):
    """
    Applies a correction to the Boat Speed (BSP) based on True Wind Angle (TWA).
    
    Parameters:
        bsp (float): Boat Speed through the water.
        twa (float): True Wind Angle.

    Returns:
        float: Corrected Boat Speed.
    """
    # If BSP < 2, return the original BSP
    if bsp < 2:
        logging.info(f"apply_correction: BSP too low to apply correction. BSP: {bsp}")
        return bsp

    # Apply correction based on TWA range
    if 30 <= abs(twa) < 40:
        result = bsp + (-0.25 * bsp + 1.19)
    elif 40 <= abs(twa) < 50:
        result = bsp + (-0.20 * bsp + 0.95)
    elif 50 <= abs(twa) < 60:
        result = bsp + (-0.14 * bsp + 0.70)
    elif 60 <= abs(twa) < 70:
        result = bsp + (-0.09 * bsp + 0.48)
    elif 70 <= abs(twa) < 80:
        result = bsp + (-0.08 * bsp + 0.53)
    elif 80 <= abs(twa) < 90:
        result = bsp + (-0.04 * bsp + 0.39)
    elif 90 <= abs(twa) < 100:
        result = bsp + (-0.03 * bsp + 0.35)
    elif 100 <= abs(twa) < 110:
        result = bsp + (-0.04 * bsp + 0.38)
    elif 110 <= abs(twa) < 120:
        result = bsp + (-0.05 * bsp + 0.45)
    elif 120 <= abs(twa) < 130:
        result = bsp + (-0.08 * bsp + 0.66)
    elif 130 <= abs(twa) < 140:
        result = bsp + (-0.06 * bsp + 0.51)
    elif 140 <= abs(twa) < 150:
        result = bsp + (-0.09 * bsp + 0.69)
    elif 150 <= abs(twa) < 160:
        result = bsp + (-0.10 * bsp + 0.71)
    elif 160 <= abs(twa) <= 180:
        result = bsp + (-0.11 * bsp + 0.77)
    else:
        # Default case if TWA doesn't fall into any range
        logging.debug(f"apply_correction: TWA out of range. TWA: {twa}")
        result = bsp

    # Round the result to 2 decimal places
    result = round(result, 2)
    logging.debug(f"apply_correction: Correction applied: BSP={bsp}, Corrected BSP={result}")
    return result



def print_live_data_old():
        while True:
            print("Ping")
            time.sleep(1)

def print_live_data():
    """
    Continuously prints the entire live_data dictionary in a readable format.
    Clears the screen each time and sorts the data by channel name.
    Includes the channel code (in hex) next to the channel name.
    """
    try:
        while True:
            with live_data_lock:
                if not live_data:
                    print("\033c", end="")  # Clears the screen (cross-platform)
                    print("Live Data is empty.\n")
                else:
                    # Clear the screen for each update
                    print("\033c", end="")  # Equivalent to "clear" in the terminal

                    # Define column widths
                    max_channel_width = max(len(key) for key in live_data.keys()) + 10  # Extra space for "(0xXX)"
                    max_value_width = 35  # Increase to accommodate longer string values
                    max_timestamp_width = 25  # Fixed width for timestamps

                    # Prepare the header
                    print("=" * (max_channel_width + max_value_width + max_timestamp_width))
                    print(f"{'Channel Name (Code)':<{max_channel_width}} {'Value':<{max_value_width}} {'Last Updated':<{max_timestamp_width}}")
                    print("-" * (max_channel_width + max_value_width + max_timestamp_width))

                    # Sort and format each data entry
                    for channel_name, data in sorted(live_data.items()):
                        channel_code = next((f"{channel_id:02X}" for channel_id, name in CHANNEL_LOOKUP.items() if name == channel_name), "??")
                        value = data.get("value", "N/A")
                        if isinstance(value, dict):
                            # Convert dictionaries to a string for printing
                            value = ", ".join(f"{k}: {v}" for k, v in value.items())
                        timestamp = data.get("timestamp", "N/A")
                        print(f"{channel_name} (0x{channel_code})".ljust(max_channel_width) +
                              f"{str(value):<{max_value_width}} {timestamp:<{max_timestamp_width}}")
                    print("=" * (max_channel_width + max_value_width + max_timestamp_width))

            # Sleep for a second before refreshing the screen
            time.sleep(1)
    except Exception as e:
        print(f"Error in Live Data Monitor: {e}", flush=True)

      



def main():
    """Main function to start threads and exit if any thread fails."""
    # Get the serial port and UDP port from command-line arguments
    serial_port, udp_port = parse_args()
    reset_serial_port(serial_port)
    logger.info(f"Starting application with serial port {serial_port} and UDP port {udp_port}.")

    # Start threads one by one
    try:
        serial_reader_thread = threading.Thread(target=serial_reader, args=(serial_port,), daemon=True, name="SerialReader")
        serial_reader_thread.start()
        logger.info("Serial Reader thread started.")
        if not serial_reader_thread.is_alive():
            logger.error("Serial Reader thread failed to start. Exiting...")
            sys.exit(1)

        frame_scanner_thread = threading.Thread(target=frame_scanner, daemon=True, name="FrameScanner")
        frame_scanner_thread.start()
        logger.info("Frame Scanner thread started.")
        if not frame_scanner_thread.is_alive():
            logger.error("Frame Scanner thread failed to start. Exiting...")
            sys.exit(1)

        output_monitor_thread = threading.Thread(target=output_monitor, args=(udp_port,), daemon=True, name="OutputMonitor")
        output_monitor_thread.start()
        logger.info("Output Monitor thread started.")
        if not output_monitor_thread.is_alive():
            logger.error("Output Monitor thread failed to start. Exiting...")
            sys.exit(1)

        live_data_monitor_thread = threading.Thread(target=print_live_data, daemon=True, name="LiveDataMonitor")
        live_data_monitor_thread.start()
        logger.info("Live Data Monitor thread started.")
        if not live_data_monitor_thread.is_alive():
            logger.error("Live Data Monitor thread failed to start. Exiting...")
            sys.exit(1)

        # Keep the program running and check threads for unexpected termination
        while True:
            if not serial_reader_thread.is_alive():
                logger.error("Serial Reader thread stopped unexpectedly. Exiting...")
                sys.exit(1)
            if not frame_scanner_thread.is_alive():
                logger.error("Frame Scanner thread stopped unexpectedly. Exiting...")
                sys.exit(1)
            if not output_monitor_thread.is_alive():
                logger.error("Output Monitor thread stopped unexpectedly. Exiting...")
                sys.exit(1)
            if not live_data_monitor_thread.is_alive():
                logger.error("Live Data Monitor thread stopped unexpectedly. Exiting...")
                sys.exit(1)
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down application.")
        sys.exit(0)  # Graceful shutdown on Ctrl+C


if __name__ == "__main__":
    main()
