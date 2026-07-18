#!/usr/bin/env python3
"""Play back a recorded Fastnet hex file to a serial port at wire speed."""
import argparse
import sys
import time

import serial

BAUDRATE  = 28800
BYTE_SIZE = serial.EIGHTBITS
STOP_BITS = serial.STOPBITS_TWO
PARITY    = serial.PARITY_ODD
CHUNK     = 64


def bits_per_byte(bytesize=BYTE_SIZE, parity=PARITY, stopbits=STOP_BITS):
    """Wire bits per transmitted byte: start + data + parity + stop."""
    return 1 + bytesize + (0 if parity == serial.PARITY_NONE else 1) + stopbits


def main():
    parser = argparse.ArgumentParser(description="Play back a Fastnet hex recording to a serial port.")
    parser.add_argument("--port",  default="/dev/ttyAMA0",      help="Serial port (default: /dev/ttyAMA0)")
    parser.add_argument("--input", default="fastnet_record.txt", help="Hex file to play back (default: fastnet_record.txt)")
    parser.add_argument("--baud",  type=int, default=BAUDRATE,   help=f"Baud rate (default: {BAUDRATE})")
    parser.add_argument("--speed", type=float, default=1.0,      help="Playback speed multiplier (default: 1.0 = wire speed)")
    args = parser.parse_args()

    if args.speed <= 0:
        print("--speed must be greater than 0")
        sys.exit(1)

    # Bytes/sec the wire can carry, scaled by the requested playback speed.
    bytes_per_sec = args.baud / bits_per_byte() * args.speed

    try:
        ser = serial.Serial(
            port=args.port, baudrate=args.baud, bytesize=BYTE_SIZE,
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

    # Parse up front so bad lines are reported before playback starts, and the
    # capture becomes one continuous stream rather than per-line writes.
    chunks = []
    for line in lines:
        try:
            chunks.append(bytes.fromhex(line))
        except ValueError:
            print(f"Skipping invalid hex line: {line[:40]!r}")
    stream = b''.join(chunks)

    if not stream:
        print(f"No usable hex data in {args.input}")
        ser.close()
        sys.exit(1)

    expected = len(stream) / bytes_per_sec
    print(f"Playing {args.input} → {args.port}  "
          f"({len(stream)} bytes, {bytes_per_sec:.0f} B/s, ~{expected:.1f}s)  (Ctrl+C to stop)")

    total = 0
    start = time.monotonic()
    try:
        for i in range(0, len(stream), CHUNK):
            chunk = stream[i:i + CHUNK]
            ser.write(chunk)
            total += len(chunk)
            # Pace against an absolute schedule so sleep jitter cannot accumulate.
            delay = start + total / bytes_per_sec - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            print(f"\r{total}/{len(stream)} bytes sent", end='', flush=True)
        ser.flush()  # tcdrain: wait for the UART to actually put it on the wire
        elapsed = time.monotonic() - start
        print(f"\nDone. {total} bytes in {elapsed:.1f}s ({total / elapsed:.0f} B/s).")
    except KeyboardInterrupt:
        elapsed = time.monotonic() - start
        print(f"\nStopped. {total} bytes in {elapsed:.1f}s.")
    except serial.SerialException as e:
        print(f"\nSerial error: {e}")
    finally:
        try:
            ser.flush()
        except (serial.SerialException, OSError):
            pass
        ser.close()


if __name__ == "__main__":
    main()
