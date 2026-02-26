#!/usr/bin/env python3
import argparse
import subprocess
import serial
import sys
import time

# Configuration Constants
SERIAL_PORT = "/dev/ttyAMA0"        # Default serial port
BAUDRATE = 28800                    # Fastnet baudrate
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY = serial.PARITY_ODD
TIMEOUT = 0.01                       # Serial read timeout in seconds
BUFFER_SIZE = 256                   # Number of bytes to read per serial read
OUTPUT_FILE = "fastnet_record.txt"  # Output file name


# Reset Serial Port
def reset_serial_port_with_stty(port):
    try:
        subprocess.run(['stty', '-F', port, 'sane'], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to reset serial port {port}: {e}")

def listen_and_record(port=SERIAL_PORT, baudrate=BAUDRATE, timeout=TIMEOUT, output_file=OUTPUT_FILE):
    """
    Listens to the Fastnet serial port and records all incoming data to a text file.
    Displays the total number of bytes received on the screen.
    """
    try:
        # Initialize serial port
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=BYTE_SIZE,
            parity=PARITY,
            stopbits=STOP_BITS,
            timeout=timeout
        )
        print(f"[INFO] Serial port {port} opened successfully.")
        print(f"[INFO] Recording data to '{output_file}'. Press Ctrl+C to stop.")
        
        # Open the output file in append mode
        with open(output_file, 'a') as f:
            total_bytes = 0  # Counter for total bytes received

            while True:
                try:
                    # Read data from serial port
                    data = ser.read(BUFFER_SIZE)
                    
                    if data:
                        # Write data to file in hexadecimal format
                        hex_data = data.hex()
                        f.write(hex_data + '\n')  # Each read separated by a newline for readability
                        f.flush()  # Ensure data is written to disk immediately
                        
                        # Update and display the total bytes received
                        bytes_received = len(data)
                        total_bytes += bytes_received
                        print(f"\r[DEBUG] Total bytes received: {total_bytes}", end='', flush=True)
                    
                except serial.SerialException as e:
                    print(f"\n[ERROR] Serial exception: {e}")
                    break
                except Exception as e:
                    print(f"\n[ERROR] Unexpected error: {e}")
                    break

    except serial.SerialException as e:
        print(f"[ERROR] Could not open serial port {port}: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[INFO] Recording terminated by user.")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("[INFO] Serial port closed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record Fastnet data from serial port to file.")
    parser.add_argument("--port", default=SERIAL_PORT, help=f"Serial port (default: {SERIAL_PORT})")
    parser.add_argument("--baud", type=int, default=BAUDRATE, help=f"Baud rate (default: {BAUDRATE})")
    args = parser.parse_args()

    reset_serial_port_with_stty(args.port)
    listen_and_record(port=args.port, baudrate=args.baud)
