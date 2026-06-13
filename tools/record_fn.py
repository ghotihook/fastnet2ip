#!/usr/bin/env python3
"""Record raw Fastnet data from a serial port to a hex file."""
import argparse
import sys
import time
from datetime import datetime

import serial

BAUDRATE  = 28800
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY    = serial.PARITY_ODD
READ_SIZE = 256


def main():
    default_output = f"rawfn-{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"

    parser = argparse.ArgumentParser(description="Record Fastnet serial data to a hex file.")
    parser.add_argument("--port",   default="/dev/ttyAMA0", help="Serial port (default: /dev/ttyAMA0)")
    parser.add_argument("--output", default=default_output,  help=f"Output file (default: {default_output})")
    args = parser.parse_args()

    try:
        ser = serial.Serial(
            port=args.port, baudrate=BAUDRATE, bytesize=BYTE_SIZE,
            parity=PARITY, stopbits=STOP_BITS, timeout=0.01,
        )
    except serial.SerialException as e:
        print(f"Cannot open {args.port}: {e}")
        sys.exit(1)

    print(f"Recording from {args.port} → {args.output}  (Ctrl+C to stop)")
    total = 0
    try:
        with open(args.output, 'w') as f:
            while True:
                data = ser.read(READ_SIZE)
                if data:
                    f.write(data.hex() + '\n')
                    f.flush()
                    total += len(data)
                    print(f"\r{total} bytes", end='', flush=True)
    except KeyboardInterrupt:
        print(f"\nStopped. {total} bytes written to {args.output}")
    except serial.SerialException as e:
        print(f"\nSerial error: {e}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
