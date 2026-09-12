# RS485 boards that transmit on their own: the boot-time bus jam

**Status.** Diagnosed and fixed on hardware in the sibling
[fastnet2n2k](https://github.com/ghotihook/fastnet2n2k) project, on a Waveshare
IPCBOX-CM5-A running a CM4 with the Fastnet tap on RS485 channel CH2 (TX = GPIO12):
the B&G H2000 raised an alarm every time the bridge rebooted, and one line in
`config.txt` cured it. The Waveshare RS485 CAN HAT that this README lists uses the
same kind of transceiver, so the same fault can apply to fastnet2ip. That
combination **has not been tested**; treat the HAT specifics below as the same
mechanism applied to its pins, not as a confirmed result.

## The symptom

With the instruments running and settled, rebooting the Pi makes the instruments
raise an alarm that latches until the Pi is powered down. Meanwhile:

- Fastnet data keeps flowing, and fastnet2ip decodes and sends it normally.
- Powering the instruments and the Pi up together is fine.
- Nothing in fastnet2ip writes to the serial port.

## Why it happens

**1. The transceiver sets its own direction.** Waveshare's RS485 boards switch the
transceiver to transmit whenever the Pi's TX line is low. In Waveshare's words: "When
P_TX is pulled low, indicating the start of data transmission, the transistor is cut
off, and the DE pin is set to a high level, enabling data transmission." The RS485 CAN
HAT's SP3485 transceiver works this way by default. A receive-only program leaves TX
idle (high), so the driver stays off and the bridge is invisible on the bus.

**2. Two things can pull TX low without fastnet2ip.** On the HAT, TX is GPIO14, the
Pi's primary UART (`serial0`).

- **Boot.** Until the UART claims the pin, it is a plain input held by its reset
  pull. On the Pi 4's BCM2711, GPIO0–8 default to pull-up and GPIO9–27 to pull-down,
  so GPIO14 sits low. The transceiver reads that as "transmitting" and drives a
  continuous space onto the Fastnet pair until the pin is claimed. The bus master
  loses its poll replies and the alarm latches. By the time data flows again the bus
  is healthy, which is why it looks like "alarm on, data fine".
- **The serial console.** Raspberry Pi OS can run a console and a login prompt on
  `serial0`. On the HAT, everything the console writes — boot messages, the login
  prompt — goes out through the transceiver onto the Fastnet bus. A login prompt also
  reads incoming Fastnet bytes as keystrokes, and competes with fastnet2ip for the port.

Powering everything up together works because the jam lands while the instruments are
still starting, before the master scans the bus.

## The fix

**1. Turn off the serial console, keep the serial port.** In `sudo raspi-config` →
Interface Options → Serial Port, answer **No** to "login shell accessible over serial"
and **Yes** to "serial port hardware enabled". Waveshare's HAT instructions ask for the
same. By hand: remove `console=serial0,115200` from `/boot/firmware/cmdline.txt`
(keep `console=tty1`), and disable the serial getty (`serial-getty@ttyS0` or
`serial-getty@ttyAMA0`, depending on the model).

**2. If the reboot alarm persists, drive TX high from the firmware.** Add to
`/boot/firmware/config.txt`:

```
gpio=14=op,dh
```

The firmware applies this before Linux starts, so TX sits at the idle (high) level
from early boot and the driver never turns on. When the UART later claims the pin it
idles high anyway, so the bus sees no change at all. On another board, use the TX GPIO
of whichever RS485 channel carries Fastnet. A TX pin in GPIO0–8 already comes up
pulled high and doesn't need this.

A short window remains between power-on and the firmware reading `config.txt`. A TX
pin in the pull-up range, or a hardware pull-up, removes it entirely. On fastnet2n2k's
board the one-line fix proved enough.

## Confirming it on the wire

With the instruments running and settled, provoke the fault deliberately:

```bash
sudo systemctl stop fastnet2ip
printf 'UUUUUUUUUUUUUUUU' | sudo tee /dev/serial0 > /dev/null
```

If the alarm fires, the transceiver's driver is reaching the Fastnet bus. `U` (0x55)
is alternating bits, so it disturbs the pair at any baud. Clear the alarm by powering
the Pi down. Do this alongside, not under way.

`pinctrl get 14-15` (which replaces the older `raspi-gpio`) shows the pins' current
function and level; TX should read `hi` while idle.

## What this doesn't cover

- **USB RS-485 adapters** (e.g. the DTECH dongle) are driven by the adapter's own
  chip, not the Pi's GPIO pins, so the Pi's boot doesn't reach them.
- **The M5Stack CoreMP135** uses a different processor and its own RS485 circuit. It
  hasn't been assessed.
