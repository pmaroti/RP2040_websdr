/*
 * pico-adc-udp: sample one ADC input at 25 kHz and stream it over UDP.
 *
 * ---------------------------------------------------------------------------
 * Overview
 * ---------------------------------------------------------------------------
 *
 * This is the firmware's entry point. It ties together four parts:
 *
 *   1. ADC + DMA sampling (this file)
 *      The RP2040's ADC free-runs from its own 48 MHz clock, so the moment of
 *      every conversion is set by the crystal, not by software. Each result is
 *      pushed into the ADC's small hardware FIFO. Two DMA channels, chained in
 *      ping-pong fashion, copy the FIFO contents into a ring of RAM buffers
 *      ("blocks"). The CPU never touches individual samples; it only gets an
 *      interrupt each time a whole block of SAMPLES_PER_PACKET samples is full.
 *
 *   2. UDP streaming (this file)
 *      The main loop notices finished blocks, prepends a 16-byte header and
 *      sends each block as one UDP datagram to the host computer. This is the
 *      same approach as PiccoloSDR from luigifcruz/pico-stuff.
 *
 *   3. USB networking (usb_network.c)
 *      The Pico's USB port pretends to be an Ethernet adapter (TinyUSB NCM or
 *      RNDIS/ECM). lwIP, a small TCP/IP stack, runs on the Pico on top of it.
 *      The Pico is 192.168.7.1 and gives the host 192.168.7.2 over DHCP.
 *
 *   4. Square wave generator (squarewave.c, freq_server.c)
 *      A PIO state machine toggles an output pin; a TCP server on port 7779
 *      lets a client change its frequency at runtime.
 *
 * ---------------------------------------------------------------------------
 * Data flow
 * ---------------------------------------------------------------------------
 *
 *   ADC (48 MHz / 1920 = 25 kS/s)
 *     -> ADC FIFO (hardware, 4 entries deep)
 *       -> DMA channel A or B (alternating, chained)
 *         -> ring[0..RING_BLOCKS-1] (RAM, one block = one datagram)
 *           -> main loop: header + samples -> lwIP UDP -> TinyUSB -> host
 *
 * Everything runs on core 0. There is no RTOS; the main loop polls the USB
 * stack and lwIP ("bare metal", NO_SYS=1 in lwIP terms).
 */

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"        // GPIO, time functions, stdio (printf to UART)
#include "hardware/adc.h"       // ADC configuration and FIFO
#include "hardware/dma.h"       // DMA channels
#include "hardware/irq.h"       // interrupt controller (NVIC) helpers
#include "lwip/udp.h"           // lwIP raw UDP API
#include "tusb.h"               // TinyUSB, used here only for tud_ready()

#include "freq_server.h"        // TCP server that sets the square wave frequency
#include "squarewave.h"         // PIO square wave generator
#include "usb_network.h"        // USB network adapter + lwIP glue

/*
 * ---------------------------------------------------------------------------
 * Build-time configuration
 * ---------------------------------------------------------------------------
 *
 * Each of these can be overridden from CMake (see CMakeLists.txt), for example
 * `cmake -DSAMPLE_RATE_HZ=50000 ..`. The #ifndef guards provide the defaults
 * when the file is compiled without those definitions.
 */

#ifndef SAMPLE_RATE_HZ
#define SAMPLE_RATE_HZ      25000   // ADC conversions per second
#endif

#ifndef ADC_CHANNEL
#define ADC_CHANNEL         0       // ADC0 = GPIO26 (1 = GPIO27, 2 = GPIO28)
#endif

#ifndef SAMPLES_PER_PACKET
#define SAMPLES_PER_PACKET  500     // 20 ms of data per datagram at 25 kHz
#endif

#ifndef SQUAREWAVE_PIN
#define SQUAREWAVE_PIN      8       // PIO square wave output
#endif

#ifndef SQUAREWAVE_DEFAULT_HZ
#define SQUAREWAVE_DEFAULT_HZ 0     // 0 = output off until a client sets it
#endif

/*
 * Number of sample blocks in the RAM ring. At any moment one block is being
 * filled by DMA, one more is already armed as the next DMA target, and the
 * rest (RING_BLOCKS - 2) are finished blocks waiting to be sent. With 8 blocks
 * of 20 ms each, the main loop can stall for up to ~120 ms before data is
 * lost. Must be a power of two so that "% RING_BLOCKS" stays cheap and keeps
 * working correctly when the 32-bit block counter wraps around.
 */
#define RING_BLOCKS         8       // must be a power of two, >= 4

#define STREAM_PORT         7778    // host port the samples are sent to
#define CONTROL_PORT        7777    // any datagram here redirects the stream to its sender
#define FREQ_PORT           7779    // TCP: set the square wave frequency

/*
 * The magic number lets the receiver recognise our datagrams. Stored
 * little-endian, its bytes on the wire are 'A' 'D' 'C' '1'.
 */
#define PACKET_MAGIC        0x31434441u  // "ADC1" in little-endian byte order
#define FLAG_OVERRUN        0x0001u      // device dropped blocks before this one

// Every datagram starts with this header, followed by `count` uint16 samples.
// All fields are little-endian. Samples are raw 12-bit ADC codes (0..4095).
//
// `packed` stops the compiler from inserting padding, so the struct's memory
// layout is exactly the 16 bytes that go on the wire. The RP2040 (Cortex-M0+)
// is little-endian, so no byte swapping is needed.
typedef struct __attribute__((packed)) {
    uint32_t magic;         // PACKET_MAGIC
    uint32_t seq;           // block number, increments by 1 per datagram
    uint32_t sample_rate;   // Hz
    uint16_t count;         // samples in this datagram
    uint16_t flags;         // FLAG_OVERRUN if blocks were dropped just before this one
} packet_header_t;

// Compile-time sanity checks: a bad configuration fails the build instead of
// misbehaving at runtime. 1472 bytes = 1500-byte Ethernet MTU minus the
// 20-byte IPv4 header and 8-byte UDP header, so each datagram fits in one
// frame and is never fragmented.
static_assert((RING_BLOCKS & (RING_BLOCKS - 1)) == 0, "RING_BLOCKS must be a power of two");
static_assert(sizeof(packet_header_t) + SAMPLES_PER_PACKET * 2 <= 1472, "packet exceeds one Ethernet frame");

/*
 * ---------------------------------------------------------------------------
 * Global state
 * ---------------------------------------------------------------------------
 */

// The sample ring: RING_BLOCKS blocks of SAMPLES_PER_PACKET 16-bit samples.
// DMA writes into it, the main loop reads from it. Block number N always lives
// in ring[N % RING_BLOCKS]. Aligned to 4 bytes so DMA and memcpy are efficient.
static uint16_t ring[RING_BLOCKS][SAMPLES_PER_PACKET] __attribute__((aligned(4)));

// The two DMA channels. Channel A fills even-numbered blocks, B odd ones.
static uint dma_chan_a, dma_chan_b;

// Count of blocks the DMA has completely filled since sampling started.
// `volatile` because it is changed by the interrupt handler and read by the
// main loop; without it the compiler could cache the value in a register and
// the main loop would never see it change.
static volatile uint32_t blocks_done;   // written only by the DMA IRQ

// lwIP's handle for our UDP "socket" (a protocol control block).
static struct udp_pcb *stream_pcb;

// Where the samples are sent. Starts as 192.168.7.2:7778 (the address the
// Pico's DHCP server gives the first host); a datagram from the host to
// CONTROL_PORT replaces it with the sender's address and port.
static ip_addr_t dest_addr = IP4(USB_NET_HOST_IP);
static u16_t dest_port = STREAM_PORT;

/*
 * ---------------------------------------------------------------------------
 * DMA interrupt handler
 * ---------------------------------------------------------------------------
 *
 * Runs every time one DMA channel finishes a block (every 20 ms at default
 * settings). At that moment the other channel has already been started
 * automatically by the hardware "chain" feature, so sampling never pauses.
 *
 * Timeline (block numbers in brackets):
 *
 *   A fills [0] -> chain -> B fills [1] -> chain -> A fills [2] -> ...
 *
 * When A finishes [k], B is already filling [k+1]. The handler points A at
 * block [k+2] so that it is ready when B finishes and chains back to A.
 * A DMA channel's write address advances as it writes, so it must be reset
 * every time; the transfer count, by contrast, is reloaded automatically by
 * the hardware when the channel is re-triggered.
 */
static void dma_irq_handler(void) {
    // Blocks complete strictly alternating a, b, a, b... Re-arm the channel
    // that just finished for the block after the one now being written.
    //
    // The loop normally runs once. It handles the (unlikely) case where the
    // interrupt was delayed long enough for both channels to finish, and it
    // only accepts completions in the expected order, so the block counter
    // can never get out of step with the channel that actually finished.
    for (;;) {
        // Even block numbers belong to channel A, odd ones to channel B.
        uint chan = (blocks_done & 1) ? dma_chan_b : dma_chan_a;

        // INTS0 has one bit per DMA channel that has raised IRQ 0.
        if (!(dma_hw->ints0 & (1u << chan))) break;

        // Writing a 1 to the bit acknowledges (clears) that channel's interrupt.
        dma_hw->ints0 = 1u << chan;

        // Point the finished channel at the block two ahead. `false` = don't
        // start it now; the chain trigger from the other channel will.
        dma_channel_set_write_addr(chan, ring[(blocks_done + 2) % RING_BLOCKS], false);

        // Publish the finished block to the main loop.
        blocks_done++;
    }
}

/*
 * ---------------------------------------------------------------------------
 * ADC and DMA setup
 * ---------------------------------------------------------------------------
 */
static void init_adc_dma(void) {
    // Switch the GPIO pin to its analog function (digital input disabled, no
    // pull-ups). ADC channel 0..3 is on GPIO 26..29.
    adc_gpio_init(26 + ADC_CHANNEL);
    adc_init();
    adc_select_input(ADC_CHANNEL);

    // Route conversions through the FIFO so DMA can pick them up.
    adc_fifo_setup(
        true,   // write conversions to the FIFO
        true,   // raise DREQ for DMA
        1,      // DREQ as soon as one sample is ready
        false,  // no error bit in the samples
        false   // keep all 12 bits
    );

    // One conversion every (1 + div) cycles of the 48 MHz ADC clock.
    // For 25 kHz: 48,000,000 / 25,000 = 1920 cycles, so div = 1919.
    // The ADC's own conversion takes 96 cycles, so the fastest possible rate is
    // 500 kS/s (div = 95). Integer division here means rates that don't divide
    // 48 MHz evenly are rounded down to the nearest whole divider.
    adc_set_clkdiv((float)(48000000 / SAMPLE_RATE_HZ) - 1.0f);

    // Grab two free DMA channels (panics if none are left, which can't happen
    // here because nothing else uses DMA).
    dma_chan_a = dma_claim_unused_channel(true);
    dma_chan_b = dma_claim_unused_channel(true);

    // Start from the SDK's default channel configuration and adjust it.
    dma_channel_config cfg_a = dma_channel_get_default_config(dma_chan_a);
    dma_channel_config cfg_b = dma_channel_get_default_config(dma_chan_b);

    // Each transfer moves one 16-bit ADC result.
    channel_config_set_transfer_data_size(&cfg_a, DMA_SIZE_16);
    channel_config_set_transfer_data_size(&cfg_b, DMA_SIZE_16);

    // Always read from the same address (the ADC FIFO register)...
    channel_config_set_read_increment(&cfg_a, false);
    channel_config_set_read_increment(&cfg_b, false);

    // ...and write to consecutive addresses in the RAM block.
    channel_config_set_write_increment(&cfg_a, true);
    channel_config_set_write_increment(&cfg_b, true);

    // Pace the transfers by the ADC's data request signal: one transfer each
    // time the ADC has a sample, instead of running as fast as possible.
    channel_config_set_dreq(&cfg_a, DREQ_ADC);
    channel_config_set_dreq(&cfg_b, DREQ_ADC);

    // When A finishes it starts B, and vice versa. This hand-over happens in
    // hardware with no gap, so no sample is ever missed between blocks.
    channel_config_set_chain_to(&cfg_a, dma_chan_b);
    channel_config_set_chain_to(&cfg_b, dma_chan_a);

    // Apply the configuration: A writes block 0, B writes block 1, each moving
    // SAMPLES_PER_PACKET samples. `false` = don't start yet (see start_sampling).
    dma_channel_configure(dma_chan_a, &cfg_a, ring[0], &adc_hw->fifo, SAMPLES_PER_PACKET, false);
    dma_channel_configure(dma_chan_b, &cfg_b, ring[1], &adc_hw->fifo, SAMPLES_PER_PACKET, false);

    // Clear any stale interrupt flags, then let both channels raise DMA_IRQ_0
    // when they complete, and install our handler for that interrupt.
    dma_hw->ints0 = (1u << dma_chan_a) | (1u << dma_chan_b);
    dma_channel_set_irq0_enabled(dma_chan_a, true);
    dma_channel_set_irq0_enabled(dma_chan_b, true);
    irq_set_exclusive_handler(DMA_IRQ_0, dma_irq_handler);
    irq_set_enabled(DMA_IRQ_0, true);
}

// Start the ADC free-running and the DMA chain collecting its output.
static void start_sampling(void) {
    blocks_done = 0;
    adc_fifo_drain();               // throw away anything left in the FIFO
    dma_channel_start(dma_chan_a);  // A waits for the first DREQ...
    adc_run(true);                  // ...which comes once the ADC starts converting
}

/*
 * ---------------------------------------------------------------------------
 * UDP
 * ---------------------------------------------------------------------------
 */

/*
 * Called by lwIP when a datagram arrives on CONTROL_PORT. The content is
 * ignored; what matters is who sent it. From then on the samples go to that
 * address and port. This lets the host receive on any address or port
 * without reconfiguring the firmware (host/receive.py and backend/server.py
 * send such a "hello" when they start).
 */
static void control_recv(void *arg, struct udp_pcb *pcb, struct pbuf *p,
                         const ip_addr_t *addr, u16_t port) {
    (void)arg;
    (void)pcb;
    ip_addr_copy(dest_addr, *addr);
    dest_port = port;
    printf("streaming to %s:%u\n", ipaddr_ntoa(addr), port);
    pbuf_free(p);   // lwIP hands us ownership of the packet buffer; release it
}

// Create the UDP endpoint. It is bound to CONTROL_PORT (so it can receive the
// "hello" datagrams above) and the same endpoint is used to send samples, so
// the samples arrive at the host with source port 7777.
static void init_udp(void) {
    stream_pcb = udp_new();
    udp_bind(stream_pcb, IP_ADDR_ANY, CONTROL_PORT);
    udp_recv(stream_pcb, control_recv, NULL);
}

/*
 * Send block number `seq` as one datagram.
 *
 * lwIP sends data held in "pbufs" (packet buffers). PBUF_TRANSPORT reserves
 * room in front of the payload for the UDP, IP and Ethernet headers, so lwIP
 * can add them without copying. PBUF_RAM allocates the buffer from lwIP's
 * heap (MEM_SIZE in lwipopts.h).
 *
 * The samples are copied out of the ring rather than sent in place, so the DMA
 * can reuse the ring slot as soon as this function returns, even if lwIP is
 * still holding the pbuf (for example while waiting for an ARP reply).
 */
static void send_block(uint32_t seq, uint16_t flags) {
    const size_t payload = sizeof(packet_header_t) + sizeof(ring[0]);

    struct pbuf *p = pbuf_alloc(PBUF_TRANSPORT, payload, PBUF_RAM);
    if (p == NULL) return;  // out of memory: drop this block (host sees a seq gap)

    packet_header_t hdr = {
        .magic = PACKET_MAGIC,
        .seq = seq,
        .sample_rate = SAMPLE_RATE_HZ,
        .count = SAMPLES_PER_PACKET,
        .flags = flags,
    };
    memcpy(p->payload, &hdr, sizeof(hdr));
    memcpy((uint8_t *)p->payload + sizeof(hdr), ring[seq % RING_BLOCKS], sizeof(ring[0]));

    // Hand the datagram to lwIP. If the USB link isn't up, this fails quietly
    // and the block is simply lost, which is what we want while unplugged.
    udp_sendto(stream_pcb, p, &dest_addr, dest_port);

    // Drop our reference. lwIP keeps its own reference if it still needs the
    // buffer, and frees it when done.
    pbuf_free(p);
}

/*
 * ---------------------------------------------------------------------------
 * Status LED
 * ---------------------------------------------------------------------------
 *
 * Solid on: waiting for the host to configure the USB network device.
 * Blinking at 2 Hz: USB network is up (tud_ready() is true).
 * The Pico W's LED is wired to the Wi-Fi chip, not a GPIO, so its board file
 * doesn't define PICO_DEFAULT_LED_PIN and this code compiles to nothing there.
 */
static void update_led(void) {
#ifdef PICO_DEFAULT_LED_PIN
    // Blink while the host has the USB network up, solid on otherwise.
    // (time in ms / 250) & 1 alternates between 0 and 1 every 250 ms.
    bool on = !tud_ready() || ((to_ms_since_boot(get_absolute_time()) / 250) & 1);
    gpio_put(PICO_DEFAULT_LED_PIN, on);
#endif
}

/*
 * ---------------------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------------------
 */
int main(void) {
    // Bring up the board (including UART stdio on GP0), USB, lwIP and the
    // DHCP/DNS servers. Must come first: everything else uses the network.
    network_init();

#ifdef PICO_DEFAULT_LED_PIN
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
#endif

    // Debug messages go to UART0 (GP0, 115200 baud), since USB is taken.
    printf("pico-adc-udp: ADC%d at %d Hz, %d samples/packet\n",
           ADC_CHANNEL, SAMPLE_RATE_HZ, SAMPLES_PER_PACKET);

    // Square wave generator: load the PIO programs, then apply the boot-time
    // frequency (0 = leave the output low until a client sets one).
    squarewave_init(SQUAREWAVE_PIN);
    squarewave_set(SQUAREWAVE_DEFAULT_HZ);
    printf("square wave on GP%d, TCP port %d\n", SQUAREWAVE_PIN, FREQ_PORT);

    // Network services, then start sampling last so no block is produced
    // before there is somewhere to send it.
    init_udp();
    freq_server_init(FREQ_PORT);
    init_adc_dma();
    start_sampling();

    uint32_t blocks_sent = 0;       // next block number to send
    uint32_t overruns = 0;          // total blocks dropped because we fell behind
    uint16_t pending_flags = 0;     // flags to put on the next datagram
    absolute_time_t next_report = make_timeout_time_ms(1000);

    /*
     * The main loop. Each pass:
     *   1. sends every block the DMA has finished since the last pass,
     *   2. services USB and lwIP (network_step), which actually moves the
     *      datagrams out and handles incoming packets (DHCP, TCP commands...),
     *   3. updates the LED and, once a second, prints statistics.
     *
     * Block counters are 32-bit and wrap after 2^32 blocks (~2.7 years at 50
     * blocks/s). All comparisons use unsigned subtraction, which stays correct
     * across the wrap.
     */
    while (true) {
        // Take one snapshot of the counter, since the IRQ may change it at any time.
        uint32_t done = blocks_done;

        // If we fell too far behind, the DMA is about to overwrite unsent
        // blocks. Skip ahead and tell the host via the overrun flag.
        // Only RING_BLOCKS - 2 finished blocks are safe to read: the other two
        // slots are the one being written and the one armed to be written next.
        if (done - blocks_sent > RING_BLOCKS - 2) {
            overruns += done - blocks_sent - (RING_BLOCKS - 2);
            blocks_sent = done - (RING_BLOCKS - 2);
            pending_flags |= FLAG_OVERRUN;
        }

        // Send everything that's ready, oldest first.
        while (blocks_sent != done) {
            send_block(blocks_sent, pending_flags);
            pending_flags = 0;
            blocks_sent++;
        }

        network_step();
        update_led();

        if (time_reached(next_report)) {
            next_report = make_timeout_time_ms(1000);
            printf("blocks %lu, dropped %lu\n", (unsigned long)blocks_sent, (unsigned long)overruns);
        }
    }
}
