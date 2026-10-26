#!/usr/bin/env python3
"""Receive the ADC stream from pico-adc-udp.

Prints live statistics and can save the samples to a WAV or raw file.
Uses only the Python standard library.

    python3 receive.py                 # stats only
    python3 receive.py -o capture.wav  # also record (Ctrl+C to stop)
    python3 receive.py -o capture.u16  # raw little-endian uint16 ADC codes
"""

import argparse
import signal
import socket
import struct
import sys
import time
import wave
from array import array

HEADER = struct.Struct("<IIIHH")
MAGIC = 0x31434441  # "ADC1"
FLAG_OVERRUN = 0x0001


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="192.168.7.1", help="Pico IP address (default %(default)s)")
    ap.add_argument("--port", type=int, default=7778, help="local UDP port to listen on (default %(default)s)")
    ap.add_argument("--control-port", type=int, default=7777, help="Pico control port (default %(default)s)")
    ap.add_argument("-o", "--output", help="save samples: .wav = 16-bit WAV, anything else = raw uint16")
    args = ap.parse_args()

    # Treat SIGTERM like Ctrl+C so the output file is always closed cleanly.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(1.0)

    # Tell the Pico where to stream. Not needed if this host got 192.168.7.2,
    # but harmless, and it makes other addresses and ports work.
    def subscribe():
        try:
            sock.sendto(b"hello", (args.device, args.control_port))
        except OSError:
            pass  # interface not up yet

    subscribe()

    out = None
    wav = None
    if args.output:
        if args.output.lower().endswith(".wav"):
            wav = wave.open(args.output, "wb")
            wav.setnchannels(1)
            wav.setsampwidth(2)
        else:
            out = open(args.output, "wb")

    expected_seq = None
    lost_packets = 0
    device_overruns = 0
    samples_total = 0
    samples_window = 0
    lo, hi = 4095, 0
    t_window = time.monotonic()
    rate = None

    print(f"listening on UDP {args.port}, Ctrl+C to stop", file=sys.stderr)
    try:
        while True:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                print("no data, re-sending subscribe...", file=sys.stderr)
                subscribe()
                continue

            if len(data) < HEADER.size:
                continue
            magic, seq, sample_rate, count, flags = HEADER.unpack_from(data)
            if magic != MAGIC or len(data) != HEADER.size + 2 * count:
                continue

            if rate is None:
                rate = sample_rate
                print(f"stream: {rate} Hz, {count} samples/packet", file=sys.stderr)
                if wav:
                    wav.setframerate(rate)

            if expected_seq is not None and seq != expected_seq:
                lost_packets += (seq - expected_seq) & 0xFFFFFFFF
            expected_seq = (seq + 1) & 0xFFFFFFFF
            if flags & FLAG_OVERRUN:
                device_overruns += 1

            samples = array("H")
            samples.frombytes(data[HEADER.size:])
            if sys.byteorder == "big":
                samples.byteswap()

            samples_total += count
            samples_window += count
            lo = min(lo, min(samples))
            hi = max(hi, max(samples))

            if out:
                out.write(data[HEADER.size:])
            if wav:
                # Center the 12-bit code on zero and scale to full 16-bit range.
                pcm = array("h", ((s - 2048) << 4 for s in samples))
                if sys.byteorder == "big":
                    pcm.byteswap()
                wav.writeframes(pcm.tobytes())

            now = time.monotonic()
            if now - t_window >= 1.0:
                measured = samples_window / (now - t_window)
                print(f"{measured:8.0f} S/s  min {lo:4d}  max {hi:4d}  "
                      f"({lo * 3.3 / 4095:.3f}-{hi * 3.3 / 4095:.3f} V)  "
                      f"lost packets {lost_packets}  device overruns {device_overruns}", flush=True)
                samples_window = 0
                lo, hi = 4095, 0
                t_window = now
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close()
        if wav:
            if rate is None:
                wav.setframerate(25000)
            wav.close()
        print(f"\n{samples_total} samples received", file=sys.stderr)


if __name__ == "__main__":
    main()
