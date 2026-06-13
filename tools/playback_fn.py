#!/usr/bin/env python3
"""Play back a recorded Fastnet hex file to a serial port."""
import argparse
import sys

import serial

BAUDRATE  = 28800
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY    = serial.PARITY_ODD
CHUNK     = 256


def main():
    parser = argparse.ArgumentParser(description="Play back a Fastnet hex recording to a serial port.")
    parser.add_argument("--port",  default="/dev/ttyAMA0",      help="Serial port (default: /dev/ttyAMA0)")
    parser.add_argument("--input", default="fastnet_record.txt", help="Hex file to play back (default: fastnet_record.txt)")
    args = parser.parse_args()

    try:
        ser = serial.Serial(
            port=args.port, baudrate=BAUDRATE, bytesize=BYTE_SIZE,
            parity=PARITY, stopbits=STOP_BITS, timeout=0.01,
        )
    except serial.SerialException as e:
        print(f"Cannot open {args.port}: {e}")
        sys.exit(1)

    try:
        with open(args.input) as f:
            lines = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        print(f"File not found: {args.input}")
        sys.exit(1)

    print(f"Playing {args.input} → {args.port}  (Ctrl+C to stop)")
    total = 0
    try:
        for line in lines:
            try:
                data = bytes.fromhex(line)
            except ValueError:
                print(f"\nSkipping invalid hex line: {line[:40]!r}")
                continue
            for i in range(0, len(data), CHUNK):
                ser.write(data[i:i + CHUNK])
                total += len(data[i:i + CHUNK])
                print(f"\r{total} bytes sent", end='', flush=True)
        print(f"\nDone. {total} bytes sent.")
    except KeyboardInterrupt:
        print(f"\nStopped. {total} bytes sent.")
    except serial.SerialException as e:
        print(f"\nSerial error: {e}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
