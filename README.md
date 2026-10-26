# pico-adc-udp

Samples one analog input on a Raspberry Pi Pico (RP2040) at 25 kHz and streams
the samples as UDP datagrams over IP. It also generates a square wave on an
output pin with the PIO, at a frequency a client sets over TCP.

## Building

This section takes you from a fresh machine to a flashed Pico. If you only want
to try it, skip to step 5 and use the prebuilt `firmware/pico_adc_udp.uf2`.

### 1. Install the tools

You need CMake, the Arm GNU embedded toolchain (`arm-none-eabi-gcc` with
newlib), git, Python 3, and a host C/C++ compiler. The Pico SDK uses the host
compiler to build `picotool` on the first configure.

**macOS** (with [Homebrew](https://brew.sh)):

```sh
xcode-select --install                    # host compiler, git (skip if already installed)
brew install cmake
brew install --cask gcc-arm-embedded      # Arm's official toolchain, includes newlib
```

Use the `gcc-arm-embedded` cask, not the `arm-none-eabi-gcc` formula. The
formula ships without the C library and fails with errors like
`nosys.specs: No such file`. Open a new terminal afterwards and check:

```sh
arm-none-eabi-gcc --version
cmake --version
```

**Linux** (Debian/Ubuntu):

```sh
sudo apt install cmake gcc-arm-none-eabi libnewlib-arm-none-eabi \
                 libstdc++-arm-none-eabi-newlib build-essential git python3
```

### 2. Get the Pico SDK

The project needs Pico SDK 2.x with its `tinyusb` and `lwip` submodules. It was
built against 2.2.0. Clone it once, anywhere you like:

```sh
cd ~/stuff
git clone -b 2.2.0 https://github.com/raspberrypi/pico-sdk
git -C pico-sdk submodule update --init lib/tinyusb lib/lwip
```

Then tell CMake where it is. Add this line to `~/.zshrc` (macOS) or
`~/.bashrc` (Linux) so it sticks:

```sh
export PICO_SDK_PATH=~/stuff/pico-sdk
```

If you already have an SDK, for example from the VS Code Raspberry Pi Pico
extension, you can point `PICO_SDK_PATH` at that instead, as long as it is
version 2.x and has both submodules.

### 3. Configure

From the project folder:

```sh
cd ~/stuff/RP2040_websdr
cmake -B build                            # plain Pico (RP2040)
# cmake -B build -DPICO_BOARD=pico_w      # Pico W instead
```

The first configure downloads and builds `picotool`, which needs network
access and takes a minute or so. Later configures reuse it.

### 4. Build

```sh
cmake --build build -j
```

The firmware ends up in `build/pico_adc_udp.uf2` (`.elf`, `.bin` and `.hex`
are there too).

### 5. Flash

1. Hold the BOOTSEL button on the Pico and plug it into USB.
2. A drive called `RPI-RP2` appears (in Finder on macOS).
3. Copy the firmware onto it:

   ```sh
   cp build/pico_adc_udp.uf2 /Volumes/RPI-RP2/            # macOS
   # cp build/pico_adc_udp.uf2 /media/$USER/RPI-RP2/      # Linux
   ```

The Pico reboots by itself and shows up as a new network interface a few
seconds later. See [Host setup](#host-setup) below.

The USB port is used for networking, so there is no USB reset-to-BOOTSEL.
Use the button each time you reflash.

### Rebuilding and changing options

After editing the code, run `cmake --build build -j` again. To change a build
option, pass it to the configure step and rebuild:

```sh
cmake -B build -DSAMPLE_RATE_HZ=50000 -DSQUAREWAVE_PIN=15
cmake --build build -j
```

If something gets stuck (for example after switching boards or SDKs), delete
the `build` folder and configure again.

#### Build options

| Option | Default | Meaning |
|---|---|---|
| `SAMPLE_RATE_HZ` | 25000 | Sample rate in Hz (up to 500000). Rates that divide 48 MHz evenly have no jitter. |
| `ADC_CHANNEL` | 0 | 0 = GP26, 1 = GP27, 2 = GP28 |
| `SAMPLES_PER_PACKET` | 500 | Samples per datagram, at most 728 to fit one Ethernet frame |
| `SQUAREWAVE_PIN` | 8 | GPIO for the square wave output |
| `SQUAREWAVE_DEFAULT_HZ` | 0 | Square wave frequency at boot, 0 = off |
| `USB_NET_RNDIS` | OFF | Use RNDIS/ECM instead of NCM. Turn on for Windows 10. |


## Overview

Like [PiccoloSDR](https://github.com/luigifcruz/pico-stuff/tree/main/apps/piccolosdr),
the IP link runs over the Pico's own USB port: the Pico shows up on your
computer as a USB network adapter (TinyUSB NCM, or RNDIS/ECM) with lwIP running
on the chip. No Ethernet or Wi-Fi hardware is needed, and it works on a plain
Pico as well as a Pico W.

## How it works

- The ADC free-runs from its 48 MHz clock with a divider of 1919, giving exactly
  25,000 samples/s. Timing comes from the crystal, not from software.
- Two DMA channels chained in ping-pong drain the ADC FIFO into a ring of eight
  500-sample buffers. The CPU never touches individual samples.
- The main loop takes each finished buffer, adds a 16-byte header and sends it as
  one UDP datagram (50 datagrams/s, about 0.4 Mbit/s).
- If the main loop ever falls behind, it skips ahead and sets an overrun flag in
  the next datagram, so the host always knows when data was dropped.

- A TCP server on port 7779 accepts frequency commands and drives a PIO state
  machine that toggles GP8 (the technique from
  [am_generator](https://github.com/pmaroti/am_generator)).

## Wiring

| Signal        | Pico pin           |
|---------------|--------------------|
| Analog input  | GP26 / ADC0 (pin 31) |
| Analog ground | AGND (pin 33)      |
| ADC reference | ADC_VREF (pin 35), 3.3 V by default |
| Square wave output | GP8 (pin 11), 3.3 V logic |
| Host link     | Micro-USB port     |
| Debug UART (optional) | GP0 TX (pin 1), 115200 baud |

The input range is 0 to 3.3 V. For an AC signal, bias it to mid-rail (for
example two 10 kΩ resistors from 3V3 and AGND to GP26, with the signal coupled
in through a capacitor). Add an anti-aliasing low-pass filter below 12.5 kHz,
such as 1 kΩ in series with 10 nF to AGND (about 16 kHz corner) or steeper if
you need it, and never drive the pin above 3.3 V.

Use GP27 or GP28 instead by setting `ADC_CHANNEL` to 1 or 2.

## Host setup

When plugged in, the Pico appears as a new network interface. It takes
192.168.7.1 and runs a small DHCP server that gives your computer 192.168.7.2.
No router is advertised, so your normal internet connection is untouched.

- **Linux**: works out of the box (NCM or ECM). NetworkManager picks up the
  address automatically. Check with `ip addr`, then `ping 192.168.7.1`.
- **macOS**: works out of the box with NCM.
- **Windows 11**: works with NCM. **Windows 10**: build with `-DUSB_NET_RNDIS=ON`.

On a plain Pico the board LED is solid while waiting for the host and blinks
once the USB network is up. (The Pico W LED sits behind the Wi-Fi chip, so it
stays off there.)

## Receiving the data

`host/receive.py` needs only Python 3:

```sh
python3 host/receive.py                  # live stats
python3 host/receive.py -o capture.wav   # record a 16-bit WAV at 25 kHz
python3 host/receive.py -o capture.u16   # record raw ADC codes
```

Typical output:

```
stream: 25000 Hz, 500 samples/packet
   25000 S/s  min 1049  max 3046  (0.845-2.455 V)  lost packets 0  device overruns 0
```

On start the script sends one datagram to the Pico's control port, which points
the stream at the script's address and port. That makes it work even if your
computer was given 192.168.7.3, or if you listen on a different port.

## Live spectrum web page

The `backend/` folder holds a small Python service that receives the sample
stream and serves a web page showing the signal's spectrum, updated 10 times a
second. The page has a spectrum plot with peak marker and cursor readout, a
waterfall (spectrogram) below it, and controls for FFT size, averaging, dB
range, peak hold and pause. The header shows the sample rate, input voltage
range and any lost packets.

The page also sets the Pico's square wave output. Type a frequency in the
**Square wave** box (`7.1M`, `7100k`, `3.5 MHz` or plain Hz) and press Enter or
**Set**. The arrow keys step it by 1 kHz, or 100 kHz with Shift, and **Off**
stops the output. Next to the box the page shows the frequency the Pico
actually produces, which can differ slightly from what you typed (see
[Square wave output](#square-wave-output)). At the default 125 MHz clock,
7.1 MHz comes out as 7.098492 MHz.

The service sets 7.1 MHz when it starts. It checks the Pico every two
seconds and sends the frequency again if the Pico was reset or replugged.
Use `--freq 3.5M` for a different startup frequency, `--freq off`, or
`--freq keep` to leave the Pico's setting alone.

The service uses [aiohttp](https://docs.aiohttp.org) for the web server and
WebSocket, and numpy for the FFT. Set it up once from the project folder:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then, with the Pico plugged in:

```sh
cd backend
source .venv/bin/activate
python3 server.py
```

Open <http://localhost:8080> in a browser. Stop the service with Ctrl+C.

- `python3 server.py --demo` generates a test signal (two tones and a slow
  sweep), so you can try the page without a Pico. The square wave box works
  too, against a simulated Pico.
- The service and `host/receive.py` both listen on UDP port 7778, so run one
  at a time.
- On macOS, the first run may ask whether Python may accept incoming network
  connections. Allow it, or the samples from the Pico are blocked.
- Other machines on your network can open the page at `http://<your-mac>:8080`.
  Use `--host 127.0.0.1` to keep it local only.

Other options: `--http-port` (default 8080), `--fps` (spectrum updates per
second, default 10), `--freq` (see above), and `--udp-port`, `--device`,
`--control-port` and `--freq-port`, which default to the firmware's settings.

How it works: the service keeps the last 65,536 samples in a ring buffer. For
each update it takes the newest FFT-size samples, removes the DC offset,
applies a Hann window and computes the power spectrum, optionally averaged with
earlier frames. The result is scaled so a full-scale sine reads 0 dBFS, and is
sent to every open page as a binary WebSocket message. Like `receive.py`, it
keeps sending a datagram to the Pico's control port while no data arrives, so
the stream finds it even after the Pico is replugged.

## Square wave output

Connect to TCP port 7779 and send one command per line:

| Command | Effect | Reply |
|---|---|---|
| `1000`, `1.5k`, `6.25M`, `FREQ 1000` | set the frequency in Hz | `OK <actual Hz>` |
| `?` or `FREQ?` | query | `FREQ <actual Hz>` |
| `OFF` or `0` | stop, pin held low | `OK 0` |

Bad input gets `ERR <reason>`. Up to four clients can be connected at once.

```sh
python3 host/setfreq.py 1000      # 1 kHz
python3 host/setfreq.py 6.25M
python3 host/setfreq.py off
nc 192.168.7.1 7779               # or type commands by hand
```

The output starts off at boot. The range is about 30 Hz to 62.5 MHz at the
default 125 MHz system clock:

- Below 10 kHz a PIO program with 64 clocks per period is used, above it the
  2-clock program from am_generator. The state machine's clock divider sets
  the frequency.
- The divider has 8 fractional bits, so the reply gives the frequency actually
  produced. When 125 MHz divides evenly (e.g. 1 kHz, 10 kHz, 6.25 MHz) the
  output is exact and jitter-free. Otherwise individual edges jitter by up to one
  PIO clock period (8 ns times the divider) while the average frequency stays
  right.
- Retuning within the same range changes only the divider, so the pin keeps
  toggling without a glitch.
- The pin is a 3.3 V push-pull output with 12 mA drive and fast slew. Above
  about 25 MHz the edges are limited by the pad and your load, so expect a
  rounded waveform rather than a clean square.

## Packet format

Each UDP datagram is one header followed by `count` samples. Everything is
little-endian.

| Offset | Type | Field |
|---|---|---|
| 0  | uint32 | magic, `0x31434441` (bytes `"ADC1"`) |
| 4  | uint32 | sequence number, +1 per datagram |
| 8  | uint32 | sample rate in Hz |
| 12 | uint16 | sample count |
| 14 | uint16 | flags, bit 0 = device dropped data before this datagram |
| 16 | uint16[count] | raw 12-bit ADC codes, 0 to 4095 |

Voltage = code × 3.3 / 4095 (with the default 3.3 V reference).
A gap in sequence numbers means datagrams were lost, either on the host or by
the device (in which case the overrun flag is also set).

| Port | Direction | Purpose |
|---|---|---|
| 7778 | Pico → host | sample stream (default destination 192.168.7.2:7778) |
| 7777 | host → Pico | any datagram here redirects the stream to its sender |
| 7779 (TCP) | host → Pico | square wave frequency control |

## Files

```
CMakeLists.txt        build, options
src/main.c            ADC + DMA sampling and UDP streaming
src/squarewave.pio    PIO square wave programs
src/squarewave.c      frequency to PIO clock divider
src/freq_server.c     TCP frequency control server
src/usb_network.c     TinyUSB + lwIP glue, DHCP and DNS server
src/usb_descriptors.c USB descriptors for NCM / RNDIS / ECM
src/tusb_config.h     TinyUSB configuration
src/lwipopts.h        lwIP configuration
host/receive.py       host receiver for the sample stream
host/setfreq.py       host client for the square wave
backend/server.py     live spectrum web service
backend/static/       web page (index.html)
```

## Credits

The USB network layer is adapted from TinyUSB's `net_lwip_webserver` example
and the `usb_network_stack` library in luigifcruz/pico-stuff (MIT licensed).
The PIO square wave is based on pmaroti/am_generator.
