cmake_minimum_required(VERSION 3.13)

# Pull in the Pico SDK (must come before project()).
include(pico_sdk_import.cmake)

project(pico_adc_udp C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

set(SAMPLE_RATE_HZ 25000 CACHE STRING "ADC sample rate in Hz")
set(ADC_CHANNEL 0 CACHE STRING "ADC input: 0=GPIO26, 1=GPIO27, 2=GPIO28")
set(SAMPLES_PER_PACKET 500 CACHE STRING "Samples per UDP datagram (max 728)")
set(SQUAREWAVE_PIN 8 CACHE STRING "GPIO for the PIO square wave output")
set(SQUAREWAVE_DEFAULT_HZ 0 CACHE STRING "Square wave frequency at boot in Hz, 0 = off")
option(USB_NET_RNDIS "Use RNDIS/ECM instead of NCM (needed for Windows 10)" OFF)

set(TINYUSB_NET ${PICO_TINYUSB_PATH}/lib/networking)

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

target_include_directories(pico_adc_udp PRIVATE
    ${CMAKE_CURRENT_LIST_DIR}/src
    ${TINYUSB_NET}
)

target_compile_definitions(pico_adc_udp PRIVATE
    SAMPLE_RATE_HZ=${SAMPLE_RATE_HZ}
    ADC_CHANNEL=${ADC_CHANNEL}
    SAMPLES_PER_PACKET=${SAMPLES_PER_PACKET}
    SQUAREWAVE_PIN=${SQUAREWAVE_PIN}
    SQUAREWAVE_DEFAULT_HZ=${SQUAREWAVE_DEFAULT_HZ}
    USE_RNDIS=$<BOOL:${USB_NET_RNDIS}>
)

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

pico_generate_pio_header(pico_adc_udp ${CMAKE_CURRENT_LIST_DIR}/src/squarewave.pio)

# USB is used for networking, so debug printf goes to UART0 (GP0 TX, 115200 baud).
pico_enable_stdio_usb(pico_adc_udp 0)
pico_enable_stdio_uart(pico_adc_udp 1)

pico_add_extra_outputs(pico_adc_udp)
