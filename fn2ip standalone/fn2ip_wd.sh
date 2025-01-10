#!/bin/bash

while true; do
	/home/alex060/python_environment/bin/python3 /home/alex060/FN2IP/fn2ip.py --serial-port /dev/ttyUSB0 --udp-port 2002
	sleep 5
done
