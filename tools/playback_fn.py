#!/usr/bin/env python3
import subprocess
import serial
import sys
import time

# Configuration Constants
SERIAL_PORT = "/dev/ttyS0"        # Replace with your serial port (e.g., COM3 on Windows)
BAUDRATE = 28800                    # Fastnet baudrate
TIMEOUT = 0.1                       # Serial timeout in seconds
INPUT_FILE = "fastnet_record.txt"   # Input file name containing hex data

# Reset Serial Port
def reset_serial_port_with_stty(port):
    try:
        subprocess.run(['stty', '-F', port, 'sane'], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to reset serial port {port}: {e}")

def playback_file_to_serial(port=SERIAL_PORT, baudrate=BAUDRATE, timeout=TIMEOUT, input_file=INPUT_FILE):
    """
    Reads data from a file and writes it to the Fastnet serial port.
    Displays the total number of bytes sent on the screen.
    """
    try:
        # Initialize serial port
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_TWO,
            timeout=timeout
        )
        print(f"[INFO] Serial port {port} opened successfully.")
        print(f"[INFO] Playing back data from '{input_file}'. Press Ctrl+C to stop.")

        # Open the input file in read mode
        with open(input_file, 'r') as f:
            total_bytes = 0  # Counter for total bytes sent

            for line in f:
                try:
                    # Convert hex string back to bytes
                    line = line.strip()  # Remove newline and whitespace
                    if line:
                        data = bytes.fromhex(line)
                        
                        # Write data to serial port
                        ser.write(data)

                        # Update and display the total bytes sent
                        bytes_sent = len(data)
                        total_bytes += bytes_sent
                        print(f"\r[DEBUG] Total bytes sent: {total_bytes}", end='', flush=True)

                        # Add a short delay between writes to simulate real-time transmission
                        time.sleep(0.01)  # Adjust as needed

                except ValueError as e:
                    print(f"\n[ERROR] Invalid hex data in file: {e}")
                    continue
                except serial.SerialException as e:
                    print(f"\n[ERROR] Serial exception: {e}")
                    break

    except FileNotFoundError as e:
        print(f"[ERROR] Input file not found: {e}")
        sys.exit(1)
    except serial.SerialException as e:
        print(f"[ERROR] Could not open serial port {port}: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[INFO] Playback terminated by user.")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()
            print("[INFO] Serial port closed.")

if __name__ == "__main__":
    reset_serial_port_with_stty(SERIAL_PORT)
    playback_file_to_serial()
