import select
import time

import serial

BAUDRATE        = 28800
BYTE_SIZE       = serial.EIGHTBITS
STOP_BITS       = serial.STOPBITS_TWO
PARITY          = serial.PARITY_ODD
READ_SIZE       = 256
FILE_READ_DELAY = 0.05


def initialize_input_source(args):
    from fastnet_decoder import set_log_level as _sl  # avoid circular at module load
    if args.serial:
        from fastnet_decoder import logger
        logger.info(f"Serial port: {args.serial}")
        try:
            return serial.Serial(
                port=args.serial, baudrate=BAUDRATE, bytesize=BYTE_SIZE,
                stopbits=STOP_BITS, parity=PARITY, timeout=0,
            ), False
        except (serial.SerialException, OSError) as e:
            logger.error(f"Cannot open {args.serial}: {e}")
            raise SystemExit(1)
    elif args.file:
        from fastnet_decoder import logger
        logger.info(f"File: {args.file}")
        try:
            with open(args.file) as f:
                hex_data = f.read().strip().replace(" ", "")
            if not hex_data:
                raise ValueError("File is empty")
            binary = bytes.fromhex(hex_data)
        except (OSError, ValueError) as e:
            logger.error(f"File error: {e}")
            raise SystemExit(1)
        return iter([binary[i:i + READ_SIZE] for i in range(0, len(binary), READ_SIZE)]), True
    else:
        from fastnet_decoder import logger
        logger.error("Specify --serial or --file")
        raise SystemExit(1)


def read_input_source(input_source, is_file):
    if is_file:
        try:
            time.sleep(FILE_READ_DELAY)
            return next(input_source)
        except StopIteration:
            return None
    else:
        rlist, _, _ = select.select([input_source], [], [], 1)
        if input_source in rlist:
            return input_source.read(READ_SIZE)
    return None
