# The first serial open after boot runs at the wrong baud (and the fix)

## Symptom

On a Raspberry Pi built-in UART (`/dev/ttyAMA*`), the **first** run after a boot reads
nothing usable — bytes arrive but nothing decodes into Fastnet frames — while every
run after that works. Closing and re-running "fixes" it. Under systemd this shows up
as: the service starts, logs look healthy, but no NMEA goes out until a restart.

## Cause

The first open of the port does not program the requested baud into the PL011
hardware. The port stays at the tty default of 9600 while `termios` reports 28800.
Receiving a 28800-baud stream with the hardware at 9600 captures roughly **one byte in
three**, and those are garbled — hence "arrives but won't decode".

Why: 28800 is not a standard rate on Linux (there is no `B28800`), so pyserial
configures it through the `BOTHER` custom-divisor path. The kernel's
`uart_set_termios()` skips reprogramming the hardware when nothing "relevant" changed —
and its notion of "relevant" is the `c_cflag` baud bits, not `c_ispeed`/`c_ospeed`.
Under `BOTHER` those bits are identical either side of the change, so the new speed is
optimised away and never reaches the divisor; serial core then falls back to its 9600
default.

This is a property of the Pi's UART + kernel, not of Fastnet or this bridge. A
**standard** rate (e.g. 38400) is unaffected, and **USB RS-485 adapters** (a different
driver) are unaffected — only the built-in PL011 at a non-standard rate is hit.

## The fix

In `fastnet2ip/core/input.py`, `_force_baudrate()` is called right after the port
opens. It forces one genuine `c_cflag` change:

```python
ser.baudrate = 9600        # a standard rate — this actually moves the CBAUD bits
ser.baudrate = BAUDRATE    # ...and back, which now programs the divisor
```

Re-assigning 28800 on its own does nothing — pyserial skips a value that hasn't
changed, so no ioctl is issued. Bouncing via a standard rate is what forces the real
reprogramming. Do not collapse it to a single assignment; `tests/test_input.py` guards
against that.

## Provenance

First diagnosed in the sibling `fastnet2n2k` project (same Fastnet hardware, same
28800 rate), hardware-verified on a CM4 over six cold boots with a counter-pattern
sender: without the fix the first open ran at ~34% of wire speed and decoded nothing;
with it, ~99.8% and clean. A standard 38400 was clean on the same board/cable/carrier,
which pins the cause on the custom-divisor path rather than the wiring. `fastnet2ip`
opens the identical port at the identical rate, so it carries the identical fix.

## How to verify

1. Reboot the Pi.
2. As the **first** thing after boot, start the bridge against a live Fastnet source
   (or a serial player), and confirm NMEA output appears within a second or two, with
   no garbage phase and no need for a second run.
3. As a control, a **standard** rate (`--baud 38400` on both ends of a test rig) is
   unaffected either way — that difference is what confirms this is the custom-divisor
   path, not the hardware.
