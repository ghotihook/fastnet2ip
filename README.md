# fastnet2ip

Fastnet is the proprietary serial protocol used by B&G on older instruments (tested on Hydra/H2000). This application reads raw Fastnet data from a serial port, decodes it using [pyfastnet](https://github.com/ghotihook/pyfastnet), and broadcasts it over UDP in your choice of output format:

| Output | Flag | Default port | Use with |
|---|---|---|---|
| NMEA 0183 | `--output nmea0183` | 2002 | Chart plotters, OpenCPN, most navigation software |
| NMEA 2000 | `--output nmea2000` | 2000 | Actisense, Yacht Devices, Signal K server (via UDP) |

NMEA 0183 is the default when `--output` is omitted.


## What this app does vs. the pyfastnet library

This repo is a ready-to-run application. It depends on [pyfastnet](https://github.com/ghotihook/pyfastnet) as its decoding engine.

**[pyfastnet](https://github.com/ghotihook/pyfastnet) handles the Fastnet protocol layer:**
- Frame synchronisation and boundary detection in the raw byte stream
- Checksum validation
- Decoding each frame into named instrument channels with interpreted values (e.g. `Apparent Wind Speed (Knots): 7.0`)

**This app (fastnet2ip) handles everything above that:**
- Reading bytes from a serial port or a recorded hex file
- Feeding bytes to pyfastnet and consuming its decoded channel queue
- Maintaining a live data store (most recent value per channel, with timestamp)
- Mapping each Fastnet channel to the correct output sentence/frame and formatting it
- Only broadcasting when a value changes, with a 5-second rebroadcast fallback for unchanged values
- UDP broadcast to the local network
- Systemd service integration for unattended operation


## Hardware

Fastnet uses two-wire differential transmission. RS-485 adapters work well; the CAN Hat option includes 120 ohm termination which is recommended.

**Tested hardware**
- Raspberry 4/5/Zero2w
- Mac
- [M5Stack Core MP135](https://shop.m5stack.com/products/m5stack-coremp135-w-stm32mp135d) - this great little package has a RS422/485 built in. A bit more fiddly to setup but worthwhile for a permanent install, recommend getting it working with a USB dongle/RPi first
- [DTECH USB RS422/RS485 USB dongle](https://www.amazon.com.au/DTECH-Converter-Adapter-Supports-Windows/dp/B076WVFXN8) — works out of the box
- [Waveshare RS485 CAN HAT](https://www.waveshare.com/wiki/RS485_CAN_HAT) — add to `/boot/firmware/config.txt`:
  ```
  dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=2000000
  ```

**Wiring**

| Fastnet wire | RS-485 |
|---|---|
| White | Data + |
| Green | Data - |

**Serial settings**: 28,800 baud, 8 data bits, odd parity, 2 stop bits


## Installation

Tested on Raspberry Pi with a stock OS install. Many systems will ask you to run from a Python venv:

```bash
python3 -m venv --system-site-packages ~/python_environment
source ~/python_environment/bin/activate
```

Then clone the repo and install dependencies:

```bash
cd ~
git clone https://github.com/ghotihook/fastnet2ip
cd fastnet2ip
pip3 install -r requirements.txt
```


## Running

**From a serial port (live data)**

```bash
python3 -m fastnet2ip --serial /dev/ttyUSB0 --output nmea0183 --live-data
python3 -m fastnet2ip --serial /dev/ttyUSB0 --output nmea2000 --live-data
```

**From a recorded hex file (simulation / testing)**

```bash
python3 -m fastnet2ip --file tests/data/example1_fastnet_data.txt --output nmea0183 --live-data
python3 -m fastnet2ip --file tests/data/example1_fastnet_data.txt --output nmea2000 --live-data
```


## Upgrading

```bash
source ~/python_environment/bin/activate
cd ~/fastnet2ip
git pull origin main
pip3 install -r requirements.txt --upgrade
```


## Systemd Service

Two example service files are provided:

| File | Output mode |
|---|---|
| `fastnet2ip.service` | NMEA 0183 |
| `fastnet2ip-n2k.service` | NMEA 2000 |

> **Note:** Update the `BindsTo=`, `After=`, `WorkingDirectory=`, and `ExecStart=` lines to match your serial port and installation path before deploying.

```bash
sudo cp fastnet2ip.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fastnet2ip
sudo systemctl start fastnet2ip
```


## Command-line arguments

**Shared arguments**

| Argument | Default | Description |
|---|---|---|
| `--output FORMAT` | `nmea0183` | Output format: `nmea0183` or `nmea2000` |
| `--serial PORT` | — | Serial port (e.g. `/dev/ttyUSB0`) |
| `--file PATH` | — | Path to a recorded hex file |
| `--log-level LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--live-data` | off | Print a live data table to the console once per second |

**NMEA 0183 arguments** (`--output nmea0183`)

| Argument | Default | Description |
|---|---|---|
| `--udp-port N` | `2002` | UDP broadcast port |
| `--broadcast-host ADDR` | `255.255.255.255` | UDP broadcast address |

**NMEA 2000 arguments** (`--output nmea2000`)

| Argument | Default | Description |
|---|---|---|
| `--host ADDR` | `255.255.255.255` | UDP destination host |
| `--udp-port N` | `2000` | UDP port |
| `--n2k-src N` | `201` | N2K source address (0–253); accepts hex (`0xC9`) |
| `--n2k-pri N` | `4` | Message priority 0 (highest) – 7 (lowest) |
| `--n2k-format FMT` | `ydwg` | Wire format: `ydwg` (Yacht Devices RAW) or `pcdin` (Signal K) |
| `--ignore-gps` | off | Suppress GPS channels (LatLon, COG, SOG) |


## NMEA 0183 Output

| Sentence | Content |
|---|---|
| VHW | Boatspeed; heading in True or Magnetic field per instrument layout |
| DBT | Depth below transducer |
| RSA | Rudder angle |
| HDM | Magnetic heading (only emitted when instrument layout confirms magnetic reference) |
| MWD | True wind direction and speed; direction in True or Magnetic field per instrument layout |
| MWV | True wind angle/speed (ref T) |
| MWV | Apparent wind angle/speed (ref R) |
| MDA | Air temp, sea temp, barometric pressure |
| VTG | COG and SOG |
| VPW | Velocity made good (positive = upwind, negative = downwind) |
| VDR | Tidal set and drift; set direction in True or Magnetic field per instrument layout |
| GLL | Latitude/Longitude |

XDR transducers:

| XDR name | Type | Unit | Content |
|---|---|---|---|
| `BATTV` | U (voltage) | V | Battery voltage |
| `ROLL` | A (angular) | D (degrees) | Heel angle |
| `PITCH` | A (angular) | D (degrees) | Fore/aft trim |
| `RAW_WIND_A` | A (angular) | V (raw) | Apparent wind angle raw sensor value |
| `RAW_WIND_S` | N (generic) | V (raw) | Apparent wind speed raw sensor value |
| `RAW_BSP` | N (generic) | V (raw) | Boatspeed raw sensor value |


## NMEA 2000 Output

Standard PGNs:

| PGN | Name | Fastnet channel(s) |
|---|---|---|
| 127245 | Rudder | Rudder Angle |
| 127250 | Vessel Heading | Heading |
| 127251 | Rate of Turn | Yaw rate |
| 127257 | Attitude | Heel Angle, Fore/Aft Trim |
| 127508 | Battery Status | Battery Volts |
| 128000 | Leeway | Leeway |
| 128259 | Boat Speed | Boatspeed (Knots) |
| 128267 | Water Depth | Depth (Meters) |
| 128275 | Distance Log | Stored Log, Trip Log |
| 129025 | Position | LatLon |
| 129026 | COG & SOG | Course Over Ground, Speed Over Ground |
| 129283 | Cross Track Error | Cross Track Error |
| 129291 | Set & Drift | Tidal Set, Tidal Drift |
| 130306 | Wind Data | Apparent wind, true wind (boat + ground ref) |
| 130312 | Temperature | Sea Temp, Air Temp |
| 130314 | Pressure | Barometric Pressure |

Proprietary PGNs (B&G manufacturer header):

| PGN | Content |
|---|---|
| 65280 | Raw wind speed, raw wind angle, raw boatspeed |
| 65281 | Raw heading |

**Wire formats** (`--n2k-format`):
- `ydwg` — Yacht Devices RAW UDP: `HH:MM:SS.mmm R XXXXXXXX DD DD DD...`
- `pcdin` — PCDIN sentences for Signal K server UDP input: `$PCDIN,PPPPPP,TTTTTTTT,SS,DDDD...*CC`


**Console output — `--live-data` flag**

![Example console output](images/console_output.jpg "Fastnet live data console")


## Bench Testing Tools

The `tools/` directory contains utilities for capturing and replaying live Fastnet data offline.

**Record raw Fastnet data to file**

```bash
python3 tools/record_fn.py --port /dev/ttyUSB0 --output my_capture.txt
```

| Argument | Default | Description |
|---|---|---|
| `--port PORT` | `/dev/ttyAMA0` | Serial port to read from |
| `--baud N` | `28800` | Baud rate |
| `--output FILE` | `fastnet_record.txt` | Output file (hex format) |

**Play back a recording to a serial port**

Useful for testing with hardware that receives serial input. Note: due to the differential nature of the bus, a loopback is not feasible — separate transmit and receive ports are required for physical bench testing.

```bash
python3 tools/playback_fn.py --port /dev/ttyUSB1 --input my_capture.txt
```

| Argument | Default | Description |
|---|---|---|
| `--port PORT` | `/dev/ttyAMA0` | Serial port to write to |
| `--baud N` | `28800` | Baud rate |
| `--input FILE` | `fastnet_record.txt` | Hex file to play back |

Recorded files can also be replayed through the main app using `--file`.


## How It Works

```
Serial port / hex file
        │
        ▼
  pyfastnet FrameBuffer          ← byte sync, checksum, channel decoding
        │
        ▼
  Decoded frame queue            ← structured dicts: channel name + value
        │
        ▼
  Live data store                ← latest value, layout, and timestamp per channel
        │
        ▼
  Output handler                 ← nmea0183 or nmea2000, selected by --output
        │
        ▼
  UDP broadcast
```

A message is sent when a channel value changes, or if the previous broadcast for that channel was more than 5 seconds ago (so downstream apps don't lose data during quiet periods).

**Adding a new output format** — implement `OutputHandler` in `fastnet2ip/handlers/` and register it in `_HANDLERS` in `fastnet2ip/__main__.py`. No other changes needed.


## Acknowledgments

- [trlafleur](https://github.com/trlafleur) — collected significant background research
- [Oppedijk](https://www.oppedijk.com/bandg/fastnet.html) — protocol background
- [timmathews](https://github.com/timmathews/bg-fastnet-driver) — substantial C++ implementation
