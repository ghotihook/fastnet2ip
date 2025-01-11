# fastnet2ip
Fastnet is the propriatory protocol used by B&G on some older instruments, tested on Hydra/H2000. It might work on other systems. I developed this for personal use and publishing for general interest only. 

This code listens for fastnet data on the bus, interprets the message and then broadcasts a UDP packet with the applicable NMEA sentence.

This is the companion app to the library [pyfastnet](https://github.com/ghotihook/pyfastnet)


## Installation

Fastnet uses two-wire differential transmission and I have had success using RS-485/CAN bus connections. The CAN Hat has the option to enable 120ohm teminations which I am using.

These are known to work
- [Waveshare RS485 CAN HAT](https://www.waveshare.com/wiki/RS485_CAN_HAT)
- [DTECH USB RS422/RS485 USB dongle](https://www.amazon.com.au/DTECH-Converter-Adapter-Supports-Windows/dp/B076WVFXN8)

Connections
- **Fastnet White**: RS485 Data +
- **Fastnet Green**: RS485 Data -
- **Baud Rate**: 28,800
- **Data Bits**: 8
- **Parity**: Odd
- **Stop Bits**: 2

I have been running on Rasperry Pi, a stock install is sufficient.

```pip3 install pyfastnet```

if using a Waveshare CAN HAT add this to /boot/firmware/config.txt

```dtoverlay=mcp2515-can0,oscillator=12000000,interrupt=25,spimaxfrequency=2000000```

## Running

**Virtual mode - txt file input**

```~/python_environment/bin/python3 fastnet2ip.py --file raw_fastnet_data.txt -u 2002 --log-level ERROR```

**Real mode - serial port input**

```~/python_environment/bin/python3 fastnet2ip.py --serial /dev/ttyUSB0 -u 2002 --log-level ERROR```


## Watchdog
If being run at startup, the fastnet2ip_wd.sh can be used as a robust way to keep it running executed from /etc/rc.local

## Approach
This is the approximate approach
- Three concurrent threads
	- Producer (collect data stream either from test file or serila port)
	- Consumer (use pyfastnet library to decode the stream, submit to internal status and trigger output NMEA message)
	- Output (stream NMEA messaged via UDP)

## Bench Testing
Record and playback of live data. I use this to capture live data from the boat so I can play it back offline for testing
- use record_fn.py to capture raw FN data into a .txt file, no processing is done, it captures the raw data.
- use playback_fn.py to send the raw data. 

Given the differential nature of the connection, loopback if not feasable. Seperate transmit and receive ports required

## Acknowledgments / References

- [trlafleur - Collector of significant background](https://github.com/trlafleur) 
- [Oppedijk - Background](https://www.oppedijk.com/bandg/fastnet.html)
- [timmathews - Significant implementation in Cpp](https://github.com/timmathews/bg-fastnet-driver)
- Significant help from chatGPT!