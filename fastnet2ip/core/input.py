import select
import time

import serial

BAUDRATE        = 28800
BYTE_SIZE       = serial.EIGHTBITS
STOP_BITS       = serial.STOPBITS_TWO
PARITY          = serial.PARITY_ODD
READ_SIZE       = 256
FILE_READ_DELAY = 0.05


def _force_baudrate(ser):
    """Make the kernel actually program the baud rate into the UART.

    Do not remove this, and do not "simplify" it to a single assignment — it looks
    redundant and is not. Without it, the FIRST open of the port after a boot leaves
    the hardware running at 9600 while termios reports 28800. Roughly two thirds of the
    bytes are lost and the rest are garbage, so nothing decodes; every later open is
    fine, which is why "just run it a second time" appears to fix it.

    28800 is not a standard rate on Linux (there is no ``B28800``), so pyserial sets it
    through the ``BOTHER`` custom-divisor path. The kernel's ``uart_set_termios()``
    skips reprogramming when the ``c_cflag`` baud bits are unchanged — and under BOTHER
    they are identical either side — so the speed change is optimised away and serial
    core falls back to its 9600 default. Bouncing to a *standard* rate and back moves
    the CBAUD bits twice, so the divisor really is written. Re-assigning 28800 on its
    own does nothing: pyserial skips a value that hasn't changed.

    Hardware-verified on a CM4 (``/dev/ttyAMA5``) over six cold boots. A standard rate
    such as 38400 is unaffected, which pins the cause on the custom-divisor path, not
    the board or wiring. (Ported from fastnet2n2k; see that project's
    ``docs/uart_first_open_baud_fix.md`` for the full diagnosis.)
    """
    ser.baudrate = 9600        # any standard rate — this is what moves the CBAUD bits
    ser.baudrate = BAUDRATE    # ...and back, which now actually programs the divisor


def initialize_input_source(args):
    if args.serial:
        from fastnet_decoder import logger
        logger.info(f"Serial port: {args.serial}")
        try:
            ser = serial.Serial(
                port=args.serial, baudrate=BAUDRATE, bytesize=BYTE_SIZE,
                stopbits=STOP_BITS, parity=PARITY, timeout=0,
            )
            _force_baudrate(ser)
            return ser, False
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
