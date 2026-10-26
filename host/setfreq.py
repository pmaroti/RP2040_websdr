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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("freq", nargs="?", default="?", help="Hz, with optional k/M suffix, or 'off'")
    ap.add_argument("--device", default="192.168.7.1", help="Pico IP address (default %(default)s)")
    ap.add_argument("--port", type=int, default=7779, help="TCP port (default %(default)s)")
    args = ap.parse_args()

    with socket.create_connection((args.device, args.port), timeout=3) as s:
        f = s.makefile("rw", newline="\r\n")
        f.readline()  # greeting
        f.write(args.freq + "\n")
        f.flush()
        answer = f.readline().strip()

    print(answer)
    sys.exit(0 if not answer.startswith("ERR") else 1)


if __name__ == "__main__":
    main()
