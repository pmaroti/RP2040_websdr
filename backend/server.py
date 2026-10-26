#!/usr/bin/env python3
"""Live spectrum web service for pico-adc-udp.

Receives the Pico's UDP sample stream, computes the spectrum with numpy and
pushes it to any browser connected to the web page over a WebSocket.

    python3 server.py              # listen for the Pico, serve http://localhost:8080
    python3 server.py --demo       # no Pico needed, generates a test signal

It also sets the Pico's square wave frequency over the Pico's TCP control
port (7779): 7.1 MHz at startup by default, then whatever is entered on the
page. If the Pico restarts, the frequency is sent again.
"""

# ---------------------------------------------------------------------------
# Architecture
# ---------------------------------------------------------------------------
#
#   Pico --UDP 7778--> PicoProtocol --> SampleBuffer (ring of recent samples)
#                                             |
#                                spectrum_loop, 10x per second:
#                                take newest N samples -> Spectrum.compute()
#                                             |
#                               binary WebSocket frame (float32 dB values)
#                                             v
#   Browser  <----------- /ws (WebSocket) ----+---- / (static/index.html)
#      |
#      +-- {"squarewave": ...} --> SquareWave --TCP 7779--> Pico
#
# Everything runs in one asyncio event loop in a single thread: the UDP
# receiver, the periodic spectrum task, the square wave keeper, the web server
# and every WebSocket connection. Nothing blocks, so no locks are needed except
# one to serialise the square wave's TCP conversations.
#
# Libraries:
#   aiohttp  web server and WebSocket support for asyncio
#   numpy    fast array maths and the FFT
#
# Messages on the WebSocket:
#   server -> browser, text:   JSON status object ("type": "status"), about
#                              once a second and after every change; or
#                              {"type": "freq_error", ...} for bad input
#   server -> browser, binary: one spectrum. float32 little-endian values:
#                              [sample_rate, dB(bin 0), dB(bin 1), ... dB(bin N/2)]
#   browser -> server, text:   JSON commands, any of
#                              {"fft_size": 4096}, {"averaging": 0.5},
#                              {"squarewave": 7100000}

import argparse
import asyncio
import json
import logging
import math
import re
import struct
import time
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web

# Sample datagram header, same layout as packet_header_t in the firmware:
# "<" little-endian; I = uint32 magic, seq, sample rate; H = uint16 count, flags.
HEADER = struct.Struct("<IIIHH")
MAGIC = 0x31434441  # "ADC1"
FLAG_OVERRUN = 0x0001
FULL_SCALE = 2048.0  # 12-bit ADC, mid-scale = 0

# FFT sizes the page may choose from. Larger = finer frequency resolution
# (sample_rate / size Hz per bin) but slower to react: 4096 samples is 164 ms
# of signal at 25 kHz.
FFT_SIZES = (512, 1024, 2048, 4096, 8192, 16384)

# Folder with index.html, next to this script (works from any current directory).
STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger("spectrum")

# PIO square wave limits at the default 125 MHz system clock (see squarewave.c).
# Min: slow 64-clock program at the largest divider. Max: 2-clock program at divider 1.
SQW_MIN_HZ = 125e6 / (64 * 65535.996)
SQW_MAX_HZ = 125e6 / 2


def parse_freq(text):
    """Parse '7.1M', '7100 kHz', '7100000' etc. into Hz. 'off' or 0 means off.
    Returns a float, or raises ValueError with a message for the page."""
    # Normalise: lower case, no spaces. So "7.1 MHz" -> "7.1mhz".
    t = str(text).strip().lower().replace(" ", "")
    if t in ("off", "0"):
        return 0.0
    # number, optional m (mega) or k (kilo), optional "hz"
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)(m|k)?(hz)?", t)
    if not m:
        raise ValueError(f"can't read '{text}', try 7.1M, 7100k or 7100000")
    hz = float(m.group(1)) * {"m": 1e6, "k": 1e3, None: 1.0}[m.group(2)]
    # Check the range here too, so the page gets a clear message right away
    # instead of a round trip to the Pico.
    if hz != 0 and not SQW_MIN_HZ <= hz <= SQW_MAX_HZ:
        raise ValueError(f"out of range, {SQW_MIN_HZ:.0f} Hz to {SQW_MAX_HZ / 1e6:.1f} MHz")
    return hz


def simulated_pico_freq(hz):
    """What the firmware would produce for hz (same divider math as squarewave.c)."""
    # Used by --demo mode, which has no Pico to ask.
    if hz == 0:
        return 0.0
    cycles = 64 if hz < 10000 else 2
    # Divider in 1/256 steps, rounded, clamped to the hardware range.
    div_fixed = min(max(int(125e6 / (cycles * hz) * 256 + 0.5), 256), 0xFFFFFF)
    return 125e6 * 256 / (cycles * div_fixed)


class SquareWave:
    """Keeps the Pico's square wave at the requested frequency.

    Talks to the firmware's line-based TCP server. Every couple of seconds it
    asks the Pico for its current frequency and re-sends the requested one if
    they differ, which covers startup, replugging and Pico resets.
    """

    def __init__(self, device, port, initial_hz, demo, on_change):
        self.device = device          # Pico IP address
        self.port = port              # Pico TCP port (7779)
        self.demo = demo              # True: simulate the Pico, no network
        self.on_change = on_change    # async callback, run when status() changes
        self.requested = initial_hz   # Hz, 0 = off, None = leave the Pico alone
        self.actual = None            # what the Pico reported, None = unknown
        self._applied = None          # (requested, actual) of the last successful set
        self.state = "connecting"     # connecting | ok | error | offline
        self.message = ""             # error text for the page
        # Only one TCP conversation with the Pico at a time (the 2-second
        # check and a user's "Set" could otherwise overlap).
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()  # currently unused

    def status(self):
        """Snapshot sent to the page as the "squarewave" part of the status message."""
        return {"requested": self.requested, "actual": self.actual,
                "state": self.state, "message": self.message}

    async def _command(self, line):
        """Send one command, return the Pico's reply line."""
        if self.demo:
            # Pretend to be the Pico.
            await asyncio.sleep(0.05)
            if line == "?":
                return f"FREQ {self.actual or 0:.3f}"
            return f"OK {simulated_pico_freq(float(line)):.3f}"
        # One short-lived TCP connection per command: connect, read the
        # greeting line, send the command, read the one-line reply, close.
        # Every step has a 2 s timeout so an unplugged Pico can't hang us.
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.device, self.port), timeout=2)
        try:
            await asyncio.wait_for(reader.readline(), timeout=2)  # greeting
            writer.write(line.encode() + b"\n")
            await writer.drain()
            reply = await asyncio.wait_for(reader.readline(), timeout=2)
            return reply.decode(errors="replace").strip()
        finally:
            writer.close()

    async def _sync(self):
        """Ask the Pico what it is doing and correct it if needed.

        Steps: query ("?"), compare with what we last set, re-send if they
        differ. Updates state/message for the page, and calls on_change if
        anything visible changed.
        """
        async with self._lock:
            before = self.status()
            try:
                reply = await self._command("?")
                # Reply looks like "FREQ 7098491.571".
                current = float(reply.split()[1]) if reply.startswith("FREQ") else None
                self.actual = current
                want = self.requested
                # Re-send if we never set this value, or the Pico no longer runs
                # it (for example it was reset and came back with the output off).
                # We compare against the value the Pico itself reported when we
                # set it (not the requested one), because the hardware can only
                # approximate most frequencies.
                if want is not None and (current is None or self._applied is None or
                                         self._applied[0] != want or
                                         abs(current - self._applied[1]) > 0.5):
                    reply = await self._command(f"{want:.3f}")
                    if reply.startswith("OK"):
                        # Reply looks like "OK 7098491.571".
                        self.actual = float(reply.split()[1])
                        self._applied = (want, self.actual)
                        log.info("square wave set to %.3f Hz", self.actual)
                        self.state, self.message = "ok", ""
                    else:
                        self.state, self.message = "error", reply.removeprefix("ERR").strip()
                        log.warning("Pico refused frequency %s: %s", want, reply)
                        self.requested = None  # don't retry a value the Pico rejects
                else:
                    self.state, self.message = "ok", ""
            except (OSError, asyncio.TimeoutError, ValueError, IndexError):
                # Connection refused/timed out (Pico unplugged or still booting),
                # or a reply we couldn't parse. Try again on the next round.
                if self.state != "offline":
                    log.info("Pico frequency port %s:%d not reachable, will retry", self.device, self.port)
                self.state, self.message = "offline", "Pico not reachable"
                self.actual = None
            if self.status() != before:
                await self.on_change()

    async def set(self, hz):
        """Called when the user sets a new frequency on the page."""
        self.requested = hz
        self.state, self.message = "connecting", ""
        await self.on_change()   # show "talking to Pico..." immediately
        await self._sync()

    async def run(self):
        """Background task: keep checking every 2 seconds, forever."""
        while True:
            await self._sync()
            await asyncio.sleep(2)


class SampleBuffer:
    """Ring buffer of the most recent samples plus stream statistics."""

    # Samples are stored as float32 in the range -1.0 .. +1.0 (ADC mid-scale
    # 2048 = 0.0). 65536 samples = 2.6 s at 25 kHz, enough for the largest
    # FFT size. New samples overwrite the oldest ones.

    def __init__(self, capacity=1 << 16):
        self.data = np.zeros(capacity, dtype=np.float32)
        self.capacity = capacity
        self.write_pos = 0          # index where the next sample goes
        self.total = 0              # samples received since start
        self.sample_rate = None     # from the datagram headers
        self.expected_seq = None    # next sequence number we expect
        self.lost_packets = 0       # datagrams missing from the sequence
        self.overruns = 0           # datagrams the Pico flagged as "dropped data before this"
        self.last_packet = 0.0      # time.monotonic() of the last datagram, 0 = never
        self.packets = 0            # datagrams received

    def add(self, seq, sample_rate, flags, codes):
        """Store one datagram's samples and update the statistics."""
        if self.sample_rate != sample_rate:
            if self.sample_rate is not None:
                log.info("sample rate changed to %d Hz", sample_rate)
            self.sample_rate = sample_rate
        # Gap in sequence numbers = lost datagrams. Done modulo 2^32 so the
        # counter wrapping around is not mistaken for a gap.
        if self.expected_seq is not None and seq != self.expected_seq:
            gap = (seq - self.expected_seq) & 0xFFFFFFFF
            if gap < 0x80000000:
                self.lost_packets += gap
            # A large backwards jump means the Pico rebooted; just resync.
        self.expected_seq = (seq + 1) & 0xFFFFFFFF
        if flags & FLAG_OVERRUN:
            self.overruns += 1

        # Convert 0..4095 codes to -1.0..+1.0.
        x = (codes.astype(np.float32) - FULL_SCALE) / FULL_SCALE
        n = len(x)
        end = self.write_pos + n
        if end <= self.capacity:
            # Fits without wrapping.
            self.data[self.write_pos:end] = x
        else:
            # Split: first part to the end of the buffer, rest at the start.
            first = self.capacity - self.write_pos
            self.data[self.write_pos:] = x[:first]
            self.data[:n - first] = x[first:]
        self.write_pos = end % self.capacity
        self.total += n
        self.packets += 1
        self.last_packet = time.monotonic()

    def latest(self, n):
        """Return the newest n samples in time order, or None if not enough yet."""
        if self.total < n:
            return None
        start = (self.write_pos - n) % self.capacity
        if start + n <= self.capacity:
            # Contiguous. copy() so later writes can't change what the caller holds.
            return self.data[start:start + n].copy()
        # Wraps around the end of the buffer: join the two pieces.
        return np.concatenate((self.data[start:], self.data[:self.write_pos]))

    def streaming(self):
        """True if a datagram arrived within the last second."""
        return self.last_packet and time.monotonic() - self.last_packet < 1.0


class PicoProtocol(asyncio.DatagramProtocol):
    """asyncio UDP handler: parses sample datagrams into the SampleBuffer."""

    def __init__(self, buf):
        self.buf = buf

    def datagram_received(self, data, addr):
        # Silently ignore anything that isn't a well-formed sample datagram.
        if len(data) < HEADER.size:
            return
        magic, seq, rate, count, flags = HEADER.unpack_from(data)
        if magic != MAGIC or len(data) != HEADER.size + 2 * count:
            return
        # View the payload as little-endian uint16 without copying.
        codes = np.frombuffer(data, dtype="<u2", offset=HEADER.size, count=count)
        self.buf.add(seq, rate, flags, codes)


class Spectrum:
    """Windowed FFT with optional exponential averaging, in dBFS."""

    def __init__(self):
        self.fft_size = 4096
        self.averaging = 0.5  # 0 = none, closer to 1 = smoother
        self._avg = None      # running average of the power spectrum
        self._window = None   # cached window function

    def set_fft_size(self, n):
        # Only accept the sizes the page offers; restart averaging on change
        # since the old average has a different number of bins.
        if n in FFT_SIZES and n != self.fft_size:
            self.fft_size = n
            self._avg = None

    def compute(self, x):
        """Return the spectrum of x in dBFS, one value per bin from 0 Hz to
        sample_rate / 2 (len(x) // 2 + 1 values)."""
        n = len(x)
        # Hann window: tapers the block's ends to zero so the abrupt start and
        # end of the block don't smear energy across the whole spectrum
        # ("spectral leakage"). Rebuilt only when the size changes.
        if self._window is None or len(self._window) != n:
            self._window = np.hanning(n).astype(np.float32)
            self._avg = None
        w = self._window
        x = x - x.mean()  # drop the DC bias so it doesn't swamp bin 0
        # rfft: FFT of real input, returns only the non-negative frequencies.
        # |X|^2 = power per bin.
        power = np.abs(np.fft.rfft(x * w)) ** 2
        # Scale so a full-scale sine reads 0 dBFS.
        # A sine of amplitude A gives a peak of A * sum(w) / 2 in |X|, so
        # multiplying |X| by 2 / sum(w) recovers A (and power by its square).
        power *= (2.0 / w.sum()) ** 2
        # Exponential moving average: avg = a * avg + (1 - a) * new.
        # Smooths out noise; a = 0 shows each frame as is.
        if self._avg is None or self.averaging <= 0:
            self._avg = power
        else:
            a = self.averaging
            self._avg = a * self._avg + (1 - a) * power
        # To decibels. The tiny offset avoids log10(0) = -infinity.
        return 10 * np.log10(self._avg + 1e-12)


async def send_subscribe(transport, device, control_port, buf):
    """Ask the Pico to stream to us until data shows up, and again if it stops."""
    # Any datagram to the Pico's control port (7777) makes it send the stream
    # to the sender's address and port. Since we send from the socket we
    # receive on, the stream comes back to us.
    while True:
        if not buf.streaming():
            try:
                transport.sendto(b"hello", (device, control_port))
            except OSError:
                pass  # USB network interface not up yet
        await asyncio.sleep(2)


async def demo_source(buf, rate=25000, block=500):
    """Generate a test signal shaped like the Pico's stream."""
    # Two fixed tones (1 kHz loud, 3.333 kHz quieter), a weak tone sweeping
    # slowly between 2 and 10 kHz, and a little noise. Produced in real time,
    # one 500-sample block every 20 ms, exactly like the Pico.
    seq = 0
    t0 = 0                                  # sample index of the block's start
    rng = np.random.default_rng()
    next_time = time.monotonic()
    while True:
        t = (t0 + np.arange(block)) / rate  # time of each sample in seconds
        sweep = 6000 + 4000 * math.sin(2 * math.pi * 0.05 * time.monotonic())
        sig = (0.5 * np.sin(2 * np.pi * 1000 * t)
               + 0.1 * np.sin(2 * np.pi * 3333 * t)
               + 0.05 * np.sin(2 * np.pi * sweep * t)
               + 0.002 * rng.standard_normal(block))
        # Convert to 12-bit ADC codes, as the Pico would send them.
        codes = np.clip(2048 + 2047 * sig, 0, 4095).astype(np.uint16)
        buf.add(seq, rate, 0, codes)
        seq += 1
        t0 += block
        # Sleep until the next block is due (scheduling against an absolute
        # time keeps the average rate exact even if a sleep runs late).
        next_time += block / rate
        await asyncio.sleep(max(0, next_time - time.monotonic()))


class App:
    """The web application: owns the buffer, spectrum, square wave and clients."""

    def __init__(self, args):
        self.args = args
        self.buf = SampleBuffer()
        self.spectrum = Spectrum()
        self.clients = set()    # open WebSocket connections
        self.squarewave = SquareWave(args.device, args.freq_port, args.freq, args.demo,
                                     on_change=self.push_status)

    async def push_status(self):
        """Send the current status to every open page right away."""
        if self.clients:
            await self.broadcast(json.dumps(self.status()))

    def status(self):
        """Everything the page shows apart from the spectrum itself."""
        b = self.buf
        # Input level is taken from the last second of samples (or fewer at startup).
        level = b.latest(min(b.sample_rate or 25000, b.total)) if b.total else None
        return {
            "type": "status",
            "streaming": bool(b.streaming()),
            "demo": self.args.demo,
            "sample_rate": b.sample_rate,
            "packets": b.packets,
            "lost_packets": b.lost_packets,
            "overruns": b.overruns,
            # -1..+1 back to ADC code, then to volts (3.3 V reference).
            "min_v": float((level.min() * FULL_SCALE + FULL_SCALE) * 3.3 / 4095) if level is not None else None,
            "max_v": float((level.max() * FULL_SCALE + FULL_SCALE) * 3.3 / 4095) if level is not None else None,
            "fft_size": self.spectrum.fft_size,
            "averaging": self.spectrum.averaging,
            "fps": self.args.fps,
            "squarewave": self.squarewave.status(),
        }

    async def broadcast(self, msg):
        """Send a message (str = text frame, bytes = binary frame) to all pages."""
        dead = []
        for ws in self.clients:
            try:
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
                else:
                    await ws.send_str(msg)
            except (ConnectionResetError, RuntimeError):
                # Page closed mid-send; forget it.
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def spectrum_loop(self):
        """Background task: compute and send a spectrum --fps times per second,
        plus a status message about once a second."""
        period = 1.0 / self.args.fps
        last_status = 0.0
        while True:
            await asyncio.sleep(period)
            if not self.clients:
                continue            # nobody watching: skip the work
            now = time.monotonic()
            if now - last_status >= 1.0:
                last_status = now
                await self.broadcast(json.dumps(self.status()))
            if not self.buf.streaming():
                continue            # no fresh data: keep the last spectrum on screen
            x = self.buf.latest(self.spectrum.fft_size)
            if x is None:
                continue            # not enough samples collected yet
            db = self.spectrum.compute(x)
            # Binary frame: float32 sample rate, then float32 dB per bin (0..rate/2).
            # Binary float32 is ~4x smaller than JSON and needs no parsing in
            # the browser (it becomes a Float32Array directly).
            frame = np.concatenate(([self.buf.sample_rate], db)).astype("<f4").tobytes()
            await self.broadcast(frame)

    async def ws_handler(self, request):
        """One WebSocket connection (one open page), at /ws."""
        # heartbeat=20: ping the browser every 20 s so dead connections are noticed.
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        # First message also lists the FFT sizes for the page's drop-down.
        await ws.send_str(json.dumps({**self.status(), "fft_sizes": FFT_SIZES}))
        try:
            # Handle commands from the page until it disconnects.
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    cmd = json.loads(msg.data)
                except ValueError:
                    continue
                if "fft_size" in cmd:
                    self.spectrum.set_fft_size(int(cmd["fft_size"]))
                if "averaging" in cmd:
                    # Clamp: 0.98 is already very slow; 1.0 would freeze the display.
                    self.spectrum.averaging = min(max(float(cmd["averaging"]), 0.0), 0.98)
                if "squarewave" in cmd:
                    try:
                        hz = parse_freq(cmd["squarewave"])
                    except ValueError as e:
                        # Bad input: tell only this page.
                        await ws.send_str(json.dumps({"type": "freq_error", "message": str(e)}))
                        continue
                    # Run in the background so this connection keeps handling
                    # messages while we talk to the Pico. SquareWave.set()
                    # pushes the status to all pages itself.
                    asyncio.create_task(self.squarewave.set(hz))
                    continue
                # Let every open page see the new settings.
                await self.broadcast(json.dumps(self.status()))
        finally:
            self.clients.discard(ws)
        return ws

    async def index(self, request):
        """The web page itself."""
        return web.FileResponse(STATIC_DIR / "index.html")

    async def start(self, app):
        """aiohttp startup hook: start the background tasks and the UDP receiver."""
        loop = asyncio.get_running_loop()
        self.tasks = [asyncio.create_task(self.spectrum_loop()),
                      asyncio.create_task(self.squarewave.run())]
        if self.args.demo:
            self.tasks.append(asyncio.create_task(demo_source(self.buf)))
            log.info("demo mode: generating a test signal")
        else:
            # UDP socket on all local addresses; PicoProtocol handles each datagram.
            transport, _ = await loop.create_datagram_endpoint(
                lambda: PicoProtocol(self.buf), local_addr=("0.0.0.0", self.args.udp_port))
            self.transport = transport
            self.tasks.append(asyncio.create_task(
                send_subscribe(transport, self.args.device, self.args.control_port, self.buf)))
            log.info("listening for samples on UDP %d (Pico at %s)", self.args.udp_port, self.args.device)
        log.info("open http://localhost:%d in a browser", self.args.http_port)

    async def stop(self, app):
        """aiohttp shutdown hook (Ctrl+C): stop tasks, close open pages."""
        for t in self.tasks:
            t.cancel()
        for ws in list(self.clients):
            await ws.close()


def main():
    # Command line options. Network defaults match the firmware.
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http-port", type=int, default=8080, help="web page port (default %(default)s)")
    ap.add_argument("--host", default="0.0.0.0", help="address to serve on (default %(default)s, all interfaces)")
    ap.add_argument("--udp-port", type=int, default=7778, help="UDP port for samples (default %(default)s)")
    ap.add_argument("--device", default="192.168.7.1", help="Pico IP address (default %(default)s)")
    ap.add_argument("--control-port", type=int, default=7777, help="Pico control port (default %(default)s)")
    ap.add_argument("--freq-port", type=int, default=7779, help="Pico square wave TCP port (default %(default)s)")
    ap.add_argument("--freq", default="7.1M",
                    help="square wave frequency to set at startup, e.g. 7.1M, 10k, off, "
                         "or 'keep' to leave the Pico as it is (default %(default)s)")
    ap.add_argument("--fps", type=float, default=10, help="spectrum updates per second (default %(default)s)")
    ap.add_argument("--demo", action="store_true", help="generate a test signal instead of listening for the Pico")
    args = ap.parse_args()
    # Turn --freq into Hz (or None for "keep") now, so a typo stops the
    # program with a clear message instead of failing later.
    if args.freq.lower() == "keep":
        args.freq = None
    else:
        try:
            args.freq = parse_freq(args.freq)
        except ValueError as e:
            ap.error(f"--freq: {e}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    # Routes: "/" the page, "/ws" the WebSocket, "/static/..." any other files.
    state = App(args)
    app = web.Application()
    app.router.add_get("/", state.index)
    app.router.add_get("/ws", state.ws_handler)
    app.router.add_static("/static/", STATIC_DIR)
    app.on_startup.append(state.start)
    app.on_shutdown.append(state.stop)
    # Runs until Ctrl+C. print=None suppresses aiohttp's own startup banner.
    web.run_app(app, host=args.host, port=args.http_port, print=None)


if __name__ == "__main__":
    main()
