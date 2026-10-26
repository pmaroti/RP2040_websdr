#!/usr/bin/env python3
"""Set the Pico's square wave frequency over TCP.

    python3 setfreq.py 1000       # 1 kHz
    python3 setfreq.py 6.25M      # 6.25 MHz
    python3 setfreq.py off        # stop the output
    python3 setfreq.py            # just print the current frequency

You can also type commands by hand: nc 192.168.7.1 7779
"""

import argparse
import socket
import sys

# How it works: the firmware runs a line-based text server on TCP port 7779
# (src/freq_server.c). On connect it sends one greeting line. Each command
# line we send gets exactly one reply line: "OK <Hz>", "FREQ <Hz>" or
# "ERR <reason>". This script connects, skips the greeting, sends one command,
# prints the reply and disconnects. The frequency text is passed through
# unchanged, so the Pico does the parsing ("1000", "10k", "6.25M", "off", "?").


def main():
    # The frequency argument is optional; without it we send "?" (a query).
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("freq", nargs="?", default="?", help="Hz, with optional k/M suffix, or 'off'")
    ap.add_argument("--device", default="192.168.7.1", help="Pico IP address (default %(default)s)")
    ap.add_argument("--port", type=int, default=7779, help="TCP port (default %(default)s)")
    args = ap.parse_args()

    # Connect (3 s timeout for connecting and for each read). makefile() wraps
    # the socket in a text file object so we can use readline()/write().
    # newline="\r\n": lines we write end in CR LF, and lines we read are split
    # at the CR LF the Pico sends.
    with socket.create_connection((args.device, args.port), timeout=3) as s:
        f = s.makefile("rw", newline="\r\n")
        f.readline()  # greeting
        f.write(args.freq + "\n")
        f.flush()
        answer = f.readline().strip()

    # Print the Pico's reply; exit status 1 on an error reply, for use in scripts.
    print(answer)
    sys.exit(0 if not answer.startswith("ERR") else 1)


if __name__ == "__main__":
    main()
