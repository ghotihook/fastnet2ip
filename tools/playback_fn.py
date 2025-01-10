import serial
import time
import argparse

# Define the serial port parameters
serial_port = "/dev/ttyUSB0"  # Default port, can be changed by user
baudrate = 28800  # Fastnet standard baudrate

def send_data_from_file(file_path, serial_port, baudrate):
    """Reads hexadecimal data from a file and sends it via serial port."""
    try:
        with open(file_path, "r") as file:
            # Read all lines from the file
            hex_lines = file.readlines()
        
        with serial.Serial(
            port=serial_port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,   # Fastnet uses 8 data bits
            parity=serial.PARITY_ODD,   # Fastnet uses odd parity
            stopbits=serial.STOPBITS_TWO,  # Fastnet uses 2 stop bits
            timeout=1                    # Timeout for reading/writing
        ) as ser:
            print(f"[INFO] Serial port configured and opened: {serial_port}")
            
            for line_number, hex_string in enumerate(hex_lines, start=1):
                # Clean and convert the hex string to binary
                hex_string = hex_string.strip()  # Remove any whitespace or newlines
                if not hex_string:
                    continue  # Skip empty lines
                
                try:
                    binary_data = bytes.fromhex(hex_string)
                except ValueError:
                    print(f"[ERROR] Invalid hex string on line {line_number}: {hex_string}. Skipping...")
                    continue
                
                # Send the binary data
                print(f"[INFO] Sending data from line {line_number}: {hex_string}")
                ser.write(binary_data)
                print(f"[INFO] Data sent successfully from line {line_number}.")
                
                # Introduce a small delay between sends (e.g., 100ms)
                time.sleep(0.1)
                
    except FileNotFoundError:
        print(f"[ERROR] File not found: {file_path}")
    except serial.SerialException as e:
        print(f"[ERROR] Serial error: {e}")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")

# Main function to handle command-line arguments
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send hexadecimal data from a file over a serial port.")
    parser.add_argument("file_path", help="Path to the file containing hex strings.")
    parser.add_argument("--serial_port", default="/dev/ttyUSB0", help="Serial port to use (default: /dev/ttyUSB0)")
    parser.add_argument("--baudrate", type=int, default=28800, help="Baudrate for serial communication (default: 28800)")

    args = parser.parse_args()

    # Call the function to send data
    send_data_from_file(args.file_path, args.serial_port, args.baudrate)