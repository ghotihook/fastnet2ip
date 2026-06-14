# fastnet2ip

Reads raw **B&G Fastnet** data from a serial port, decodes it with
[pyfastnet](https://github.com/ghotihook/pyfastnet), and **broadcasts it over UDP**
in your choice of format — NMEA 0183 or NMEA 2000 (over IP) — for Signal K,
OpenCPN, chart plotters, and other navigation software on your network.

| Output | Flag | Default port | Use with |
|---|---|---|---|
| NMEA 0183 | `--output nmea0183` *(default)* | 2002 | Chart plotters, OpenCPN, most navigation software |
| NMEA 2000 | `--output nmea2000` | 2000 | Actisense, Yacht Devices, Signal K server (via UDP) |

Runs on Raspberry Pi, macOS, or Linux. Requires **Python 3.10+**.

## Quick start

Install the CLI in its own isolated environment with
[pipx](https://pipx.pypa.io/):

```bash
pipx install fastnet2ip
```

Run it against your Fastnet serial port:

```bash
fastnet2ip --serial /dev/ttyUSB0 --output nmea0183 --live-data
fastnet2ip --serial /dev/ttyUSB0 --output nmea2000 --live-data
```

No instruments to hand? Replay one of the bundled captures from the source repo
(`tests/data/`):

```bash
fastnet2ip --file example1_fastnet_data.txt --output nmea0183 --live-data
```

`--live-data` prints a live channel table to the console once per second:

![Example console output](images/console_output.jpg "Fastnet live data console")

> The app can also be invoked as a module: `python3 -m fastnet2ip ...` — equivalent
> to the `fastnet2ip` command.

To upgrade later: `pipx upgrade fastnet2ip`.

<details>
<summary>Alternative installs (pip venv / from source)</summary>

```bash
# pip into a virtual environment
python3 -m venv ~/fastnet2ip-venv
source ~/fastnet2ip-venv/bin/activate
pip install fastnet2ip

# from source (development)
git clone https://github.com/ghotihook/fastnet2ip
cd fastnet2ip
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```
</details>

### The Fastnet toolkit

Three projects stack together — pick the one that matches where you want the data
to end up:

| Project | What it does | Use it when |
|---|---|---|
| [pyfastnet](https://github.com/ghotihook/pyfastnet) | **Decoder library.** Turns raw Fastnet bytes into named instrument channels. | You're writing your own Python and want the decoded data. |
| **fastnet2ip** *(this app)* | **Serial → network.** Broadcasts decoded data over UDP as NMEA 0183 or NMEA 2000 (over IP). | Feeding Signal K, OpenCPN, or a plotter over WiFi / Ethernet. |
| [fastnet2n2k](https://github.com/ghotihook/fastnet2n2k) | **Serial → physical NMEA 2000 bus.** Transmits PGNs onto a CAN backbone via SocketCAN. | Wiring into a real NMEA 2000 network / chartplotter. |

```
                          ┌─ fastnet2ip   → UDP (NMEA 0183 / NMEA 2000 over IP) → Signal K, OpenCPN, plotters
B&G Fastnet bus ─(serial)─→ pyfastnet ─┤
                          └─ fastnet2n2k → SocketCAN (NMEA 2000 PGNs)           → CAN backbone, chartplotter
```

This app builds on `pyfastnet`: pyfastnet handles the protocol layer (frame sync,
checksums, decoding raw bytes into named channels); fastnet2ip handles everything
above that — reading the serial port, a live data store, mapping channels to
output sentences/frames, rate limiting, and UDP broadcast. If you need the data on
a **physical** NMEA 2000 CAN backbone rather than over the network, use
[fastnet2n2k](https://github.com/ghotihook/fastnet2n2k) instead.

## Running as a systemd service

For an always-on bridge (e.g. a Raspberry Pi), run `fastnet2ip` under systemd so
it starts on boot and restarts on failure.

> **Don't use the pipx install for the service.** pipx installs into a *user's*
> `~/.local/bin`, which a root-run service can't rely on. Install into a dedicated
> system virtual environment instead.

**1. Install into a dedicated venv**

```bash
sudo python3 -m venv /opt/fastnet2ip
sudo /opt/fastnet2ip/bin/pip install fastnet2ip
```

This gives you `/opt/fastnet2ip/bin/fastnet2ip`, the path the unit below uses.

**2. Create the unit file**

Paste the following into `/etc/systemd/system/fastnet2ip.service`
(e.g. `sudo nano /etc/systemd/system/fastnet2ip.service`). The same template ships
as `fastnet2ip.service` in the source repository.

```ini
[Unit]
Description=fastnet2ip Service
####################################### CHANGE THIS IF PORT CHANGES
After=dev-ttyUSB0.device
BindsTo=dev-ttyUSB0.device

[Service]
Type=simple
User=root
WorkingDirectory=/opt/fastnet2ip
# Uncomment ONE ExecStart line for the output mode you want:
# NMEA 2000 output:
ExecStart=/opt/fastnet2ip/bin/fastnet2ip --output nmea2000 --serial /dev/ttyUSB0 --udp-port 2000 --n2k-format ydwg --n2k-src 201 --n2k-pri 4 --ignore-gps --log-level INFO
# NMEA 0183 output:
#ExecStart=/opt/fastnet2ip/bin/fastnet2ip --output nmea0183 --serial /dev/ttyUSB0 --udp-port 2002 --log-level INFO
Restart=always
RestartSec=10

# === RESOURCE LIMITS ===
OOMScoreAdjust=-700
OOMPolicy=continue
MemoryMax=128M
MemoryHigh=96M
TimeoutStopSec=30

# === LOGGING ===
StandardOutput=journal
StandardError=journal
SyslogIdentifier=fastnet2ip
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

**Before enabling, edit it to match your setup:**
- `After=` / `BindsTo=` — your serial device, in systemd's escaped form
  (`/dev/ttyUSB0` → `dev-ttyUSB0.device`)
- the active `ExecStart=` — pick **one** line (NMEA 0183 or NMEA 2000) and set the
  correct `--serial` port

**3. Enable and start it**

```bash
sudo systemctl daemon-reload
sudo systemctl enable fastnet2ip
sudo systemctl start fastnet2ip
sudo journalctl -u fastnet2ip -f      # follow the logs
```

To upgrade later:
`sudo /opt/fastnet2ip/bin/pip install --upgrade fastnet2ip && sudo systemctl restart fastnet2ip`.

## Command-line options

**Shared**

| Argument | Default | Description |
|---|---|---|
| `--output FORMAT` | `nmea0183` | `nmea0183` or `nmea2000` |
| `--serial PORT` | — | Serial port (e.g. `/dev/ttyUSB0`) |
| `--file PATH` | — | Path to a recorded hex file |
| `--log-level LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `--live-data` | off | Print live channel table to console once per second |
| `--ignore-gps` | off | Suppress GPS channels — see below |
| `--ignore-heading` | off | Suppress heading channels — see below |
| `--host ADDR` | `255.255.255.255` | UDP destination host |
| `--udp-port N` | `2002` / `2000` | UDP port (default depends on output mode) |

**NMEA 2000** (`--output nmea2000`)

| Argument | Default | Description |
|---|---|---|
| `--n2k-src N` | `201` | N2K source address 0–253 (accepts hex: `0xC9`) |
| `--n2k-pri N` | `4` | Message priority 0 (highest) – 7 (lowest) |
| `--n2k-format FMT` | `ydwg` | Wire format: `ydwg` or `pcdin` (see below) |

### Avoiding feedback loops: `--ignore-gps` and `--ignore-heading`

Most B&G systems receive GPS (and sometimes heading) from an external source and
pass it through onto the Fastnet bus alongside the instrument data. If you also
connect that same source directly to your network, re-broadcasting it from this
bridge creates a **feedback loop** — downstream software sees the same data
arriving twice, which can cause jumps, conflicts, or incorrect averaging.

Use `--ignore-gps` and/or `--ignore-heading` when that source is **already** on
your network. Both flags work with `--output nmea0183` and `--output nmea2000`.
If the bridge is the **only** source of that data on your network, omit them.

`--ignore-gps` suppresses:

| Fastnet channel | NMEA 0183 | NMEA 2000 |
|---|---|---|
| LatLon | GLL | PGN 129025 |
| Speed Over Ground | VTG | PGN 129026 |
| Course Over Ground (True) | VTG | PGN 129026 |
| Course Over Ground (Mag) | VTG | PGN 129026 |

`--ignore-heading` suppresses:

| Fastnet channel | NMEA 0183 | NMEA 2000 |
|---|---|---|
| Heading | HDM / HDT | PGN 127250 |
| Heading (Raw) | — | PGN 65281 |

## Hardware

Fastnet uses two-wire differential transmission. RS-485 adapters work well; the
CAN Hat option includes 120 Ω termination which is recommended.

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

## Output reference

### NMEA 0183

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

> **True vs Magnetic:** The Fastnet data stream carries no magnetic variation or
> deviation. Whether a channel is labelled True or Magnetic reflects the reference
> configured in the B&G instrument — not a computed conversion. VHW, HDM/HDT, MWD,
> and VDR each output whichever reference the instrument is set to.

XDR transducers:

| XDR name | Content |
|---|---|
| `BATTV` | Battery voltage |
| `ROLL` | Heel angle (degrees) |
| `PITCH` | Fore/aft trim (degrees) |
| `RAW_WIND_A` | Apparent wind angle raw sensor value |
| `RAW_WIND_S` | Apparent wind speed raw sensor value |
| `RAW_BSP` | Boatspeed raw sensor value |

### NMEA 2000

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
| 65280 | Proprietary: raw wind speed, wind angle |
| 65281 | Proprietary: raw heading |
| 65282 | Proprietary: raw boatspeed |

**Wire formats** (`--n2k-format`):
- `ydwg` — Yacht Devices RAW UDP: `HH:MM:SS.mmm R XXXXXXXX DD DD DD...`
- `pcdin` — PCDIN sentences for Signal K server UDP input: `$PCDIN,PPPPPP,TTTTTTTT,SS,DDDD...*CC`

## How it works

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

A message is sent when a value changes, or after 5 seconds if unchanged (so
downstream apps don't lose data during quiet periods).

To add a new output format, implement `OutputHandler` in `fastnet2ip/handlers/`
and add it to `_HANDLERS` in `fastnet2ip/__main__.py`.

## Bench testing tools

Two helper scripts for capturing and replaying Fastnet data live in the `tools/`
directory of the source repository (they are not installed by pip/pipx — clone the
repo to use them):

```bash
# record raw Fastnet data to file
python3 tools/record_fn.py --port /dev/ttyUSB0 --output my_capture.txt

# play a recording back to a serial port
python3 tools/playback_fn.py --port /dev/ttyUSB1 --input my_capture.txt
```

Recordings can also be replayed through the main app with
`fastnet2ip --file my_capture.txt`.

## Development

```bash
git clone https://github.com/ghotihook/fastnet2ip
cd fastnet2ip
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
pytest
```

Build distributable artifacts:

```bash
pip install build
python -m build        # writes sdist + wheel to dist/
```

### Releasing to PyPI

The version lives in one place — `__version__` in `fastnet2ip/__init__.py` — which
`pyproject.toml` reads automatically.

1. **Bump the version** in `fastnet2ip/__init__.py` per [semver](https://semver.org/)
   (patch = bug fix, minor = new backward-compatible flag, major = breaking change).
2. **Rebuild clean and validate** (stale `dist/` files would otherwise be uploaded):
   ```bash
   rm -rf dist build *.egg-info
   python -m build
   twine check dist/*
   ```
3. **Tag and upload:**
   ```bash
   git commit -am "Release X.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   twine upload dist/*          # needs a PyPI account + API token in ~/.pypirc
   ```
4. Users upgrade with `pipx upgrade fastnet2ip` (or `pip install --upgrade fastnet2ip`).

> **PyPI never lets you re-upload an existing version** — even a deleted or
> "yanked" one. If a release has a bug, bump to the next patch version and
> re-publish; you can never overwrite a number that's already live.

## Acknowledgments

- [trlafleur](https://github.com/trlafleur) — collected significant background research
- [Oppedijk](https://www.oppedijk.com/bandg/fastnet.html) — protocol background
- [timmathews](https://github.com/timmathews/bg-fastnet-driver) — substantial C++ implementation

## License

MIT — see [LICENSE](LICENSE).
