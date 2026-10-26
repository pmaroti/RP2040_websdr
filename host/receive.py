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

# ---------------------------------------------------------------------------
# How this script works
# ---------------------------------------------------------------------------
#
# The Pico sends one UDP datagram every 20 ms (at the default settings). Each
# datagram is a 16-byte header followed by raw ADC samples:
#
#   offset  type      field
#   0       uint32    magic, 0x31434441 ("ADC1")
#   4       uint32    sequence number, +1 per datagram
#   8       uint32    sample rate in Hz
#   12      uint16    sample count
#   14      uint16    flags (bit 0: the Pico dropped data before this datagram)
#   16      uint16[]  samples, 12-bit ADC codes 0..4095
#
# Everything is little-endian. The script listens on a UDP port, checks each
# datagram, counts lost datagrams by looking for gaps in the sequence numbers,
# prints statistics once a second, and optionally writes the samples to a file.
#
# Only the Python standard library is used, so it runs anywhere Python 3 does.

# Layout of the header: "<" little-endian, I = uint32, H = uint16.
HEADER = struct.Struct("<IIIHH")
MAGIC = 0x31434441  # "ADC1"
FLAG_OVERRUN = 0x0001  # header flag: the Pico fell behind and skipped data


def main():
    # Command line options. The defaults match the firmware.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="192.168.7.1", help="Pico IP address (default %(default)s)")
    ap.add_argument("--port", type=int, default=7778, help="local UDP port to listen on (default %(default)s)")
    ap.add_argument("--control-port", type=int, default=7777, help="Pico control port (default %(default)s)")
    ap.add_argument("-o", "--output", help="save samples: .wav = 16-bit WAV, anything else = raw uint16")
    args = ap.parse_args()

    # Treat SIGTERM like Ctrl+C so the output file is always closed cleanly.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    # UDP socket listening on all local addresses. A 1 MB receive buffer lets
    # the OS hold several seconds of data if this script is briefly busy
    # (e.g. writing to a slow disk). The 1 s timeout makes recvfrom() return
    # regularly so we can notice when no data is arriving.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(1.0)

    # Tell the Pico where to stream. Not needed if this host got 192.168.7.2,
    # but harmless, and it makes other addresses and ports work.
    # The Pico sends the stream to whoever last sent a datagram to its control
    # port (7777); the content of the datagram doesn't matter. Because we send
    # it from our listening socket, the Pico replies to this exact address and
    # port.
    def subscribe():
        try:
            sock.sendto(b"hello", (args.device, args.control_port))
        except OSError:
            pass  # interface not up yet

    subscribe()

    # Output file, if requested: a WAV file (playable/analysable in audio
    # tools) or a raw dump of the uint16 sample codes.
    out = None
    wav = None
    if args.output:
        if args.output.lower().endswith(".wav"):
            wav = wave.open(args.output, "wb")
            wav.setnchannels(1)     # mono
            wav.setsampwidth(2)     # 16-bit samples (rate is set once known)
        else:
            out = open(args.output, "wb")

    # Statistics.
    expected_seq = None     # sequence number we expect next (None = first datagram)
    lost_packets = 0
    device_overruns = 0
    samples_total = 0
    samples_window = 0      # samples since the last stats line
    lo, hi = 4095, 0        # smallest/largest ADC code since the last stats line
    t_window = time.monotonic()
    rate = None             # sample rate, learned from the first datagram

    print(f"listening on UDP {args.port}, Ctrl+C to stop", file=sys.stderr)
    try:
        while True:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                # Nothing for a second: the Pico may have been replugged and
                # forgotten us, so subscribe again.
                print("no data, re-sending subscribe...", file=sys.stderr)
                subscribe()
                continue

            # Ignore anything that isn't a well-formed sample datagram.
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

            # Lost datagrams show up as a jump in the sequence number. The
            # "& 0xFFFFFFFF" keeps the arithmetic correct when the 32-bit
            # counter wraps around.
            if expected_seq is not None and seq != expected_seq:
                lost_packets += (seq - expected_seq) & 0xFFFFFFFF
            expected_seq = (seq + 1) & 0xFFFFFFFF
            if flags & FLAG_OVERRUN:
                device_overruns += 1

            # Decode the samples into an array of unsigned 16-bit ints. The
            # data is little-endian; swap only on a big-endian computer.
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
                # 0..4095 -> -2048..2047 -> x16 -> -32768..32752 (signed 16-bit).
                pcm = array("h", ((s - 2048) << 4 for s in samples))
                if sys.byteorder == "big":
                    pcm.byteswap()
                wav.writeframes(pcm.tobytes())

            # Once a second, print a stats line. The sample rate is measured
            # from the data actually received, so it drops if datagrams are lost.
            # Voltages assume the default 3.3 V ADC reference.
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
        # Always close the output file properly (WAV needs its header updated).
        if out:
            out.close()
        if wav:
            if rate is None:
                wav.setframerate(25000)  # no data received: still write a valid file
            wav.close()
        print(f"\n{samples_total} samples received", file=sys.stderr)


if __name__ == "__main__":
    main()
