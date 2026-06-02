# fastnet2ip

Fastnet is the proprietary serial protocol used by B&G on older instruments (tested on Hydra/H2000). This application reads raw Fastnet data from a serial port, decodes it using [pyfastnet](https://github.com/ghotihook/pyfastnet), and broadcasts it over UDP in your choice of output format:

| Output | Flag | Default port | Use with |
|---|---|---|---|
| NMEA 0183 | `--output nmea0183` | 2002 | Chart plotters, OpenCPN, most navigation software |
| NMEA 2000 | `--output nmea2000` | 2000 | Actisense, Yacht Devices, Signal K server (via UDP) |

`--output nmea0183` is the default.


## Hardware

Fastnet uses two-wire differential transmission. RS-485 adapters work well; the CAN Hat option includes 120 ohm termination which is recommended.

**Tested hardware**
- Raspberry Pi 4/5/Zero 2W
- Mac
- [M5Stack Core MP135](https://shop.m5stack.com/products/m5stack-coremp135-w-stm32mp135d) — has RS422/485 built in; a bit more fiddly to set up but good for a permanent install
- [DTECH USB RS422/RS485 dongle](https://www.amazon.com.au/DTECH-Converter-Adapter-Supports-Windows/dp/B076WVFXN8) — works out of the box
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

```bash
python3 -m venv --system-site-packages ~/python_environment
source ~/python_environment/bin/activate
cd ~
git clone https://github.com/ghotihook/fastnet2ip
cd fastnet2ip
pip3 install -r requirements.txt
```

## Upgrading

```bash
source ~/python_environment/bin/activate
cd ~/fastnet2ip
git pull origin main
pip3 install -r requirements.txt --upgrade
```


## Running

**Live data from serial port**

```bash
python3 -m fastnet2ip --serial /dev/ttyUSB0 --output nmea0183 --live-data
python3 -m fastnet2ip --serial /dev/ttyUSB0 --output nmea2000 --live-data
```

**From a recorded hex file (testing)**

```bash
python3 -m fastnet2ip --file tests/data/example1_fastnet_data.txt --output nmea0183 --live-data
```

**Console output — `--live-data` flag**

![Example console output](images/console_output.jpg "Fastnet live data console")


## Systemd Service

A `fastnet2ip.service` file is provided with both output modes — uncomment the `ExecStart` line for the one you want.

> **Note:** Update `BindsTo=`, `After=`, `WorkingDirectory=`, and the active `ExecStart=` to match your serial port and installation path.

```bash
sudo cp fastnet2ip.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable fastnet2ip
sudo systemctl start fastnet2ip
```


## Command-line arguments

**Shared**

| Argument | Default | Description |
|---|---|---|
| `--output FORMAT` | `nmea0183` | `nmea0183` or `nmea2000` |
| `--serial PORT` | — | Serial port (e.g. `/dev/ttyUSB0`) |
| `--file PATH` | — | Path to a recorded hex file |
| `--log-level LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--live-data` | off | Print live channel table to console once per second |
| `--ignore-gps` | off | Suppress GPS channels — see below |

**NMEA 0183** (`--output nmea0183`)

| Argument | Default | Description |
|---|---|---|
| `--udp-port N` | `2002` | UDP broadcast port |
| `--host ADDR` | `255.255.255.255` | UDP broadcast address |

**NMEA 2000** (`--output nmea2000`)

| Argument | Default | Description |
|---|---|---|
| `--udp-port N` | `2000` | UDP port |
| `--host ADDR` | `255.255.255.255` | UDP destination host |
| `--n2k-src N` | `201` | N2K source address 0–253 (accepts hex: `0xC9`) |
| `--n2k-pri N` | `4` | Message priority 0 (highest) – 7 (lowest) |
| `--n2k-format FMT` | `ydwg` | Wire format: `ydwg` or `pcdin` (see below) |


## GPS and `--ignore-gps`

Most B&G systems receive GPS input from an external source (chartplotter, dedicated GPS receiver) and pass it through onto the Fastnet bus alongside the instrument data. If you are also connecting that same GPS source directly to your network, re-broadcasting the GPS data from this bridge creates a **feedback loop** — the chartplotter sees the same position arriving twice, which can cause jumps, conflicts, or incorrect averaging depending on the software.

`--ignore-gps` works with both `--output nmea0183` and `--output nmea2000` and suppresses the following Fastnet channels:

| Fastnet channel | NMEA 0183 | NMEA 2000 |
|---|---|---|
| LatLon | GLL | PGN 129025 |
| Speed Over Ground | VTG | PGN 129026 |
| Course Over Ground (True) | VTG | PGN 129026 |
| Course Over Ground (Mag) | VTG | PGN 129026 |

If the bridge is the **only** GPS source on your network, omit this flag.


## NMEA 0183 Output

| Sentence | Content |
|---|---|
| VHW | Boatspeed; heading in True or Magnetic field per instrument configuration |
| DBT | Depth below transducer |
| RSA | Rudder angle |
| HDM | Magnetic heading (emitted when instrument is configured for magnetic reference) |
| HDT | True heading (emitted when instrument is configured for true reference) |
| MWD | True wind direction and speed; direction True or Magnetic per instrument configuration |
| MWV | True wind angle/speed (ref T) |
| MWV | Apparent wind angle/speed (ref R) |
| MDA | Air temp, sea temp, barometric pressure |
| VTG | COG and SOG |
| VPW | Velocity made good |
| VDR | Tidal set and drift; set direction True or Magnetic per instrument configuration |
| GLL | Latitude/Longitude |

> **Note on True vs Magnetic:** The Fastnet data stream carries no magnetic variation or deviation. Whether a channel is labelled True or Magnetic reflects the reference configured in the B&G instrument — not a computed conversion. VHW, HDM/HDT, MWD, and VDR will each output whichever reference the instrument is set to.

XDR transducers:

| XDR name | Content |
|---|---|
| `BATTV` | Battery voltage |
| `ROLL` | Heel angle (degrees) |
| `PITCH` | Fore/aft trim (degrees) |
| `RAW_WIND_A` | Apparent wind angle raw sensor value |
| `RAW_WIND_S` | Apparent wind speed raw sensor value |
| `RAW_BSP` | Boatspeed raw sensor value |


## NMEA 2000 Output

| PGN | Name |
|---|---|
| 127245 | Rudder |
| 127250 | Vessel Heading |
| 127251 | Rate of Turn |
| 127257 | Attitude (heel + trim) |
| 127508 | Battery Status |
| 128000 | Leeway |
| 128259 | Boat Speed |
| 128267 | Water Depth |
| 128275 | Distance Log |
| 129025 | Position |
| 129026 | COG & SOG |
| 129283 | Cross Track Error |
| 129291 | Set & Drift |
| 130306 | Wind Data (apparent, true boat-ref, true ground-ref) |
| 130312 | Temperature (sea + air) |
| 130314 | Pressure |
| 65280 | Proprietary: raw wind speed, wind angle, boatspeed |
| 65281 | Proprietary: raw heading |

**Wire formats** (`--n2k-format`):
- `ydwg` — Yacht Devices RAW UDP: `HH:MM:SS.mmm R XXXXXXXX DD DD DD...`
- `pcdin` — PCDIN sentences for Signal K server UDP input: `$PCDIN,PPPPPP,TTTTTTTT,SS,DDDD...*CC`


## Bench Testing Tools

**Record raw Fastnet data to file**

```bash
python3 tools/record_fn.py --port /dev/ttyUSB0 --output my_capture.txt
```

**Play back a recording to a serial port**

```bash
python3 tools/playback_fn.py --port /dev/ttyUSB1 --input my_capture.txt
```

Recordings can also be replayed through the main app with `--file`.


## How It Works

```
Serial port / hex file
        │
        ▼
  pyfastnet FrameBuffer     ← byte sync, checksum, channel decoding
        │
        ▼
  Live data store           ← latest value per channel
        │
        ▼
  Output handler            ← nmea0183 or nmea2000, selected by --output
        │
        ▼
  UDP broadcast
```

A message is sent when a value changes, or after 5 seconds if unchanged (so downstream apps don't lose data during quiet periods).

To add a new output format, implement `OutputHandler` in `fastnet2ip/handlers/` and add it to `_HANDLERS` in `fastnet2ip/__main__.py`.


## What this app does vs. pyfastnet

[pyfastnet](https://github.com/ghotihook/pyfastnet) handles the protocol layer: frame sync, checksums, and decoding raw bytes into named instrument channels. This app handles everything above that: reading the serial port, maintaining a live data store, mapping channels to output sentences/frames, rate limiting, and UDP broadcast.


## Acknowledgments

- [trlafleur](https://github.com/trlafleur) — collected significant background research
- [Oppedijk](https://www.oppedijk.com/bandg/fastnet.html) — protocol background
- [timmathews](https://github.com/timmathews/bg-fastnet-driver) — substantial C++ implementation
