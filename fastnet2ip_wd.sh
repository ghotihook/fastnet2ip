#!/bin/bash

while true; do
	/home/alex060/python_environment/bin/python3 /home/alex060/fastnet2ip/fastnet2ip.py --serial /dev/ttySTM3 -udp-port 2002 --log-level ERROR
	#/home/alex060/python_environment/bin/python3 /home/alex060/fastnet2ip/fastnet2ip.py --serial /dev/ttyUSB0 -udp-port 2002 --log-level ERROR
	sleep 5
done
