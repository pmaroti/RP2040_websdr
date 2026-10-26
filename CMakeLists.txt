# CMake build script for the pico-adc-udp firmware.
#
# Typical use (see README.md, "Building"):
#   cmake -B build                  configure (once, or to change options)
#   cmake --build build -j          compile; output is build/pico_adc_udp.uf2
#
# Requires the Pico SDK 2.x, located through the PICO_SDK_PATH environment
# variable (or -DPICO_SDK_PATH=...), with its tinyusb and lwip submodules.

cmake_minimum_required(VERSION 3.13)

# Pull in the Pico SDK (must come before project()).
include(pico_sdk_import.cmake)

project(pico_adc_udp C CXX ASM)
set(CMAKE_C_STANDARD 11)

# Sets up the SDK's libraries and the cross-compiler for the chosen board
# (PICO_BOARD, default "pico"; use -DPICO_BOARD=pico_w for a Pico W).
pico_sdk_init()

# ---------------------------------------------------------------------------
# Build options. Change them with -D<NAME>=<value> on the cmake command line,
# e.g. cmake -B build -DSAMPLE_RATE_HZ=50000. CACHE means the value is
# remembered in the build folder until changed again. They are passed to the
# C code as preprocessor definitions further down.
# ---------------------------------------------------------------------------

set(SAMPLE_RATE_HZ 25000 CACHE STRING "ADC sample rate in Hz")
set(ADC_CHANNEL 0 CACHE STRING "ADC input: 0=GPIO26, 1=GPIO27, 2=GPIO28")
set(SAMPLES_PER_PACKET 500 CACHE STRING "Samples per UDP datagram (max 728)")
set(SQUAREWAVE_PIN 8 CACHE STRING "GPIO for the PIO square wave output")
set(SQUAREWAVE_DEFAULT_HZ 0 CACHE STRING "Square wave frequency at boot in Hz, 0 = off")
option(USB_NET_RNDIS "Use RNDIS/ECM instead of NCM (needed for Windows 10)" OFF)

# TinyUSB ships a small DHCP server, DNS server and RNDIS helper in its
# lib/networking folder. They are not part of any SDK library, so they are
# compiled directly into the firmware below.
set(TINYUSB_NET ${PICO_TINYUSB_PATH}/lib/networking)

# The firmware image and its source files.

add_executable(pico_adc_udp
    src/main.c
    src/usb_network.c
    src/usb_descriptors.c
    src/squarewave.c
    src/freq_server.c
    ${TINYUSB_NET}/dhserver.c
    ${TINYUSB_NET}/dnserver.c
    ${TINYUSB_NET}/rndis_reports.c
)

# Header search paths. src/ also provides tusb_config.h and lwipopts.h, which
# TinyUSB and lwIP include by name to read their configuration.
target_include_directories(pico_adc_udp PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
    ${TINYUSB_NET}
)

# Turn the build options into #defines for the C code.
# $<BOOL:...> converts ON/OFF into 1/0.
target_compile_definitions(pico_adc_udp PRIVATE
    SAMPLE_RATE_HZ=${SAMPLE_RATE_HZ}
    ADC_CHANNEL=${ADC_CHANNEL}
    SAMPLES_PER_PACKET=${SAMPLES_PER_PACKET}
    SQUAREWAVE_PIN=${SQUAREWAVE_PIN}
    SQUAREWAVE_DEFAULT_HZ=${SQUAREWAVE_DEFAULT_HZ}
    USE_RNDIS=$<BOOL:${USB_NET_RNDIS}>
)

# SDK libraries used. In the Pico SDK, "linking" a library also adds its
# source files and include paths to the build.
#   pico_stdlib       GPIO, timers, stdio
#   hardware_*        ADC, DMA, interrupts, PIO, clock queries
#   tinyusb_device    USB device stack; tinyusb_board: board_init() etc.
#   pico_lwip_*       lwIP core, IPv4, Ethernet/ARP, and the RP2040 port (arch/cc.h)
target_link_libraries(pico_adc_udp
    pico_stdlib
    hardware_adc
    hardware_dma
    hardware_irq
    hardware_pio
    hardware_clocks
    tinyusb_device
    tinyusb_board
    pico_lwip_core
    pico_lwip_core4
    pico_lwip_netif
    pico_lwip_arch
)

# Assemble squarewave.pio into squarewave.pio.h at build time (with pioasm).
pico_generate_pio_header(pico_adc_udp ${CMAKE_CURRENT_LIST_DIR}/src/squarewave.pio)

# USB is used for networking, so debug printf goes to UART0 (GP0 TX, 115200 baud).
pico_enable_stdio_usb(pico_adc_udp 0)
pico_enable_stdio_uart(pico_adc_udp 1)

# Besides the .elf, also produce .uf2 (drag-and-drop flashing), .bin, .hex
# and a .map file.
pico_add_extra_outputs(pico_adc_udp)
