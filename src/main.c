/*
 * pico-adc-udp: sample one ADC input at 25 kHz and stream it over UDP.
 *
 * The ADC free-runs from its own 48 MHz clock, so sample timing is set by the
 * crystal, not by software. Two DMA channels chained in ping-pong drain the
 * ADC FIFO into a ring of buffers. The main loop wraps each finished buffer in
 * a small header and sends it as one UDP datagram over the USB network link
 * (same approach as PiccoloSDR from luigifcruz/pico-stuff).
 *
 * A TCP server on the same link lets a client set the frequency of a PIO
 * generated square wave on an output pin.
 */

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/adc.h"
#include "hardware/dma.h"
#include "hardware/irq.h"
#include "lwip/udp.h"
#include "tusb.h"

#include "freq_server.h"
#include "squarewave.h"
#include "usb_network.h"

#ifndef SAMPLE_RATE_HZ
#define SAMPLE_RATE_HZ      25000
#endif

#ifndef ADC_CHANNEL
#define ADC_CHANNEL         0       // ADC0 = GPIO26
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

#define RING_BLOCKS         8       // must be a power of two, >= 4

#define STREAM_PORT         7778    // host port the samples are sent to
#define CONTROL_PORT        7777    // any datagram here redirects the stream to its sender
#define FREQ_PORT           7779    // TCP: set the square wave frequency

#define PACKET_MAGIC        0x31434441u  // "ADC1" in little-endian byte order
#define FLAG_OVERRUN        0x0001u      // device dropped blocks before this one

// Every datagram starts with this header, followed by `count` uint16 samples.
// All fields are little-endian. Samples are raw 12-bit ADC codes (0..4095).
typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint32_t seq;           // block number, increments by 1 per datagram
    uint32_t sample_rate;   // Hz
    uint16_t count;         // samples in this datagram
    uint16_t flags;
} packet_header_t;

static_assert((RING_BLOCKS & (RING_BLOCKS - 1)) == 0, "RING_BLOCKS must be a power of two");
static_assert(sizeof(packet_header_t) + SAMPLES_PER_PACKET * 2 <= 1472, "packet exceeds one Ethernet frame");

static uint16_t ring[RING_BLOCKS][SAMPLES_PER_PACKET] __attribute__((aligned(4)));

static uint dma_chan_a, dma_chan_b;
static volatile uint32_t blocks_done;   // written only by the DMA IRQ

static struct udp_pcb *stream_pcb;
static ip_addr_t dest_addr = IP4(USB_NET_HOST_IP);
static u16_t dest_port = STREAM_PORT;

static void dma_irq_handler(void) {
    // Blocks complete strictly alternating a, b, a, b... Re-arm the channel
    // that just finished for the block after the one now being written.
    for (;;) {
        uint chan = (blocks_done & 1) ? dma_chan_b : dma_chan_a;
        if (!(dma_hw->ints0 & (1u << chan))) break;

        dma_hw->ints0 = 1u << chan;
        dma_channel_set_write_addr(chan, ring[(blocks_done + 2) % RING_BLOCKS], false);
        blocks_done++;
    }
}

static void init_adc_dma(void) {
    adc_gpio_init(26 + ADC_CHANNEL);
    adc_init();
    adc_select_input(ADC_CHANNEL);
    adc_fifo_setup(
        true,   // write conversions to the FIFO
        true,   // raise DREQ for DMA
        1,      // DREQ as soon as one sample is ready
        false,  // no error bit in the samples
        false   // keep all 12 bits
    );

    // One conversion every (1 + div) cycles of the 48 MHz ADC clock.
    adc_set_clkdiv((float)(48000000 / SAMPLE_RATE_HZ) - 1.0f);

    dma_chan_a = dma_claim_unused_channel(true);
    dma_chan_b = dma_claim_unused_channel(true);

    dma_channel_config cfg_a = dma_channel_get_default_config(dma_chan_a);
    dma_channel_config cfg_b = dma_channel_get_default_config(dma_chan_b);

    channel_config_set_transfer_data_size(&cfg_a, DMA_SIZE_16);
    channel_config_set_transfer_data_size(&cfg_b, DMA_SIZE_16);
    channel_config_set_read_increment(&cfg_a, false);
    channel_config_set_read_increment(&cfg_b, false);
    channel_config_set_write_increment(&cfg_a, true);
    channel_config_set_write_increment(&cfg_b, true);
    channel_config_set_dreq(&cfg_a, DREQ_ADC);
    channel_config_set_dreq(&cfg_b, DREQ_ADC);
    channel_config_set_chain_to(&cfg_a, dma_chan_b);
    channel_config_set_chain_to(&cfg_b, dma_chan_a);

    dma_channel_configure(dma_chan_a, &cfg_a, ring[0], &adc_hw->fifo, SAMPLES_PER_PACKET, false);
    dma_channel_configure(dma_chan_b, &cfg_b, ring[1], &adc_hw->fifo, SAMPLES_PER_PACKET, false);

    dma_hw->ints0 = (1u << dma_chan_a) | (1u << dma_chan_b);
    dma_channel_set_irq0_enabled(dma_chan_a, true);
    dma_channel_set_irq0_enabled(dma_chan_b, true);
    irq_set_exclusive_handler(DMA_IRQ_0, dma_irq_handler);
    irq_set_enabled(DMA_IRQ_0, true);
}

static void start_sampling(void) {
    blocks_done = 0;
    adc_fifo_drain();
    dma_channel_start(dma_chan_a);
    adc_run(true);
}

static void control_recv(void *arg, struct udp_pcb *pcb, struct pbuf *p,
                         const ip_addr_t *addr, u16_t port) {
    (void)arg;
    (void)pcb;
    ip_addr_copy(dest_addr, *addr);
    dest_port = port;
    printf("streaming to %s:%u\n", ipaddr_ntoa(addr), port);
    pbuf_free(p);
}

static void init_udp(void) {
    stream_pcb = udp_new();
    udp_bind(stream_pcb, IP_ADDR_ANY, CONTROL_PORT);
    udp_recv(stream_pcb, control_recv, NULL);
}

static void send_block(uint32_t seq, uint16_t flags) {
    const size_t payload = sizeof(packet_header_t) + sizeof(ring[0]);

    struct pbuf *p = pbuf_alloc(PBUF_TRANSPORT, payload, PBUF_RAM);
    if (p == NULL) return;

    packet_header_t hdr = {
        .magic = PACKET_MAGIC,
        .seq = seq,
        .sample_rate = SAMPLE_RATE_HZ,
        .count = SAMPLES_PER_PACKET,
        .flags = flags,
    };
    memcpy(p->payload, &hdr, sizeof(hdr));
    memcpy((uint8_t *)p->payload + sizeof(hdr), ring[seq % RING_BLOCKS], sizeof(ring[0]));

    udp_sendto(stream_pcb, p, &dest_addr, dest_port);
    pbuf_free(p);
}

static void update_led(void) {
#ifdef PICO_DEFAULT_LED_PIN
    // Blink while the host has the USB network up, solid on otherwise.
    bool on = !tud_ready() || ((to_ms_since_boot(get_absolute_time()) / 250) & 1);
    gpio_put(PICO_DEFAULT_LED_PIN, on);
#endif
}

int main(void) {
    network_init();

#ifdef PICO_DEFAULT_LED_PIN
    gpio_init(PICO_DEFAULT_LED_PIN);
    gpio_set_dir(PICO_DEFAULT_LED_PIN, GPIO_OUT);
#endif

    printf("pico-adc-udp: ADC%d at %d Hz, %d samples/packet\n",
           ADC_CHANNEL, SAMPLE_RATE_HZ, SAMPLES_PER_PACKET);

    squarewave_init(SQUAREWAVE_PIN);
    squarewave_set(SQUAREWAVE_DEFAULT_HZ);
    printf("square wave on GP%d, TCP port %d\n", SQUAREWAVE_PIN, FREQ_PORT);

    init_udp();
    freq_server_init(FREQ_PORT);
    init_adc_dma();
    start_sampling();

    uint32_t blocks_sent = 0;
    uint32_t overruns = 0;
    uint16_t pending_flags = 0;
    absolute_time_t next_report = make_timeout_time_ms(1000);

    while (true) {
        uint32_t done = blocks_done;

        // If we fell too far behind, the DMA is about to overwrite unsent
        // blocks. Skip ahead and tell the host via the overrun flag.
        if (done - blocks_sent > RING_BLOCKS - 2) {
            overruns += done - blocks_sent - (RING_BLOCKS - 2);
            blocks_sent = done - (RING_BLOCKS - 2);
            pending_flags |= FLAG_OVERRUN;
        }

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
