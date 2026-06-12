import argparse
import logging
import queue
import socket
import time

import serial
from fastnet_decoder import FrameBuffer, set_log_level

from fastnet2ip.core.data_store import live_data, update_live_data
from fastnet2ip.core.input import initialize_input_source, read_input_source
from fastnet2ip.core.display import print_live_data
from fastnet2ip.handlers.nmea0183 import NMEA0183Handler
from fastnet2ip.handlers.nmea2000 import NMEA2000Handler

_HANDLERS = {
    "nmea0183": NMEA0183Handler,
    "nmea2000": NMEA2000Handler,
}

_GPS_CHANNELS = frozenset({
    "LatLon",
    "Speed Over Ground",
    "Course Over Ground (True)",
    "Course Over Ground (Mag)",
})

_HEADING_CHANNELS = frozenset({
    "Heading",
    "Heading (Raw)",
})


def _drain_frame_queue(fq, handler, udp_socket, ignore_gps=False, ignore_heading=False):
    while True:
        try:
            frame = fq.get_nowait()
        except queue.Empty:
            break
        if not frame:
            continue
        for channel_name, channel_data in frame.get("values", {}).items():
            if not channel_data:
                continue
            if ignore_gps and channel_name in _GPS_CHANNELS:
                continue
            if ignore_heading and channel_name in _HEADING_CHANNELS:
                continue
            channel_id   = channel_data.get("channel_id", "??")
            value        = channel_data.get("value")
            display_text = channel_data.get("display_text", "")
            layout       = channel_data.get("layout")

            old_entry = live_data.get(channel_name)
            update_live_data(channel_name, channel_id, value, display_text, layout)
            handler.process_channel(channel_name, old_entry, udp_socket)


def run_loop(input_source, is_file, handler, udp_socket, show_live_data, ignore_gps=False, ignore_heading=False):
    fb = FrameBuffer()
    last_print = time.monotonic()
    while True:
        data = read_input_source(input_source, is_file)
        if data:
            fb.add_to_buffer(data)
            fb.get_complete_frames()
            _drain_frame_queue(fb.frame_queue, handler, udp_socket, ignore_gps, ignore_heading)

        handler.tick(udp_socket)

        if show_live_data and time.monotonic() - last_print >= 1:
            print_live_data(fb)
            last_print = time.monotonic()

        if is_file and data is None:
            break


def main():
    # First pass: resolve --output so the handler can register its own flags.
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--output", default="nmea0183", choices=list(_HANDLERS))
    pre_args, _ = pre_parser.parse_known_args()

    handler_class = _HANDLERS[pre_args.output]

    parser = argparse.ArgumentParser(
        description="FastNet Protocol Decoder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # NMEA 0183 from a serial port, broadcast on UDP 2002
  fastnet2ip --serial /dev/ttyUSB0 --output nmea0183

  # NMEA 2000 from a serial port, broadcast on UDP 2000
  fastnet2ip --serial /dev/ttyUSB0 --output nmea2000

  # Show the live channel table while running
  fastnet2ip --serial /dev/ttyUSB0 --live-data

  # Replay a recorded capture instead of reading the serial port
  fastnet2ip --file capture.txt --output nmea0183 --live-data

  # Suppress GPS/heading already present on the network (avoid feedback loops)
  fastnet2ip --serial /dev/ttyUSB0 --ignore-gps --ignore-heading
""",
    )
    parser.add_argument(
        "--output", default="nmea0183", choices=list(_HANDLERS),
        help="Output format (default: nmea0183)",
    )
    parser.add_argument("--serial",    type=str, help="Serial port (e.g., /dev/ttyUSB0)")
    parser.add_argument("--file",      type=str, help="Path to hex data file")
    parser.add_argument("--log-level", type=str, default="INFO",
                        help="Log level: DEBUG, INFO, WARNING, ERROR (default: INFO)")
    parser.add_argument("--live-data", action="store_true",
                        help="Print live channel table to console once per second")
    parser.add_argument("--ignore-gps", action="store_true",
                        help="Suppress GPS channels (LatLon, COG, SOG) — use when GPS is "
                             "already on the network to avoid duplicate/looping data")
    parser.add_argument("--ignore-heading", action="store_true",
                        help="Suppress heading channels (Heading, Heading (Raw)) — use when "
                             "a compass is already on the network to avoid duplicate/looping data")
    parser.add_argument("--host", type=str, default="255.255.255.255",
                        help="UDP destination host (default: 255.255.255.255)")
    parser.add_argument("--udp-port", type=int, default=None,
                        help="UDP port (default: 2002 for nmea0183, 2000 for nmea2000)")

    handler_class.add_arguments(parser)
    args = parser.parse_args()

    if args.udp_port is None:
        args.udp_port = 2002 if args.output == "nmea0183" else 2000

    set_log_level(args.log_level)
    logging.getLogger("fastnet2ip").setLevel(
        getattr(logging, args.log_level.upper(), logging.INFO)
    )
    logging.getLogger("fastnet2ip.handlers.nmea2000").setLevel(
        getattr(logging, args.log_level.upper(), logging.INFO)
    )

    if args.ignore_gps:
        from fastnet_decoder import logger
        logger.info(f"GPS suppressed: {', '.join(sorted(_GPS_CHANNELS))}")

    if args.ignore_heading:
        from fastnet_decoder import logger
        logger.info(f"Heading suppressed: {', '.join(sorted(_HEADING_CHANNELS))}")

    handler = handler_class()
    handler.setup(args)

    input_source, is_file = initialize_input_source(args)

    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    handler.startup(udp_socket)

    try:
        run_loop(input_source, is_file, handler, udp_socket, args.live_data, args.ignore_gps, args.ignore_heading)
    except KeyboardInterrupt:
        from fastnet_decoder import logger
        logger.info("Shutting down. Goodbye!")
    finally:
        udp_socket.close()
        if isinstance(input_source, serial.Serial) and input_source.is_open:
            input_source.close()


if __name__ == "__main__":
    main()
