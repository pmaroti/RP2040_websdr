/*
 * USB network glue: TinyUSB NCM (or RNDIS/ECM) + lwIP + DHCP/DNS servers.
 *
 * Adapted from TinyUSB's net_lwip_webserver example and the usb_network_stack
 * library of luigifcruz/pico-stuff (both MIT licensed, originally written by
 * Peter Lawrence, influenced by lrndis).
 *
 * ---------------------------------------------------------------------------
 * How the pieces fit
 * ---------------------------------------------------------------------------
 *
 *   Host computer                         Pico
 *   ------------------                    --------------------------------
 *   OS network stack                      lwIP (TCP/IP stack)
 *        |                                      |  netif (this file)
 *   USB NCM/RNDIS driver  <== USB cable ==>  TinyUSB "net" device class
 *
 * TinyUSB makes the Pico look like a USB Ethernet adapter. Whole Ethernet
 * frames travel over USB in both directions. This file is the bridge between
 * TinyUSB's frame callbacks and lwIP's "netif" (network interface) API:
 *
 *   - Host -> Pico: TinyUSB calls tud_network_recv_cb() with a frame. We copy
 *     it into an lwIP pbuf and later feed it to lwIP in service_traffic().
 *   - Pico -> host: lwIP calls linkoutput_fn() with a frame. We give it to
 *     TinyUSB, which calls tud_network_xmit_cb() to copy it into the USB
 *     transmit buffer.
 *
 * On top of that, two tiny servers from TinyUSB's lib/networking folder make
 * the link work with no setup on the host:
 *
 *   - a DHCP server, which gives the host 192.168.7.2 (or .3, .4)
 *   - a DNS server, which answers "pico.usb" with the Pico's address
 */

#include <string.h>

#include "bsp/board_api.h"      // board_init(): clocks, UART stdio, LED
#include "tusb.h"               // TinyUSB core and net device class

#include "pico/stdlib.h"
#include "dhserver.h"           // DHCP server (TinyUSB lib/networking)
#include "dnserver.h"           // DNS server (TinyUSB lib/networking)
#include "lwip/init.h"
#include "lwip/timeouts.h"      // sys_check_timeouts()
#include "netif/ethernet.h"     // ethernet_input(), etharp_output()

#include "usb_network.h"

// lwIP's state for our single network interface.
static struct netif netif_data;

// Shared between tud_network_recv_cb() and service_traffic().
//
// TinyUSB delivers received frames from inside tud_task(). We park the frame
// here and process it on the next service_traffic() call. Only one frame is
// held at a time; while it is occupied, TinyUSB is told to hold off (see
// tud_network_recv_cb), so no frame is ever silently dropped here.
static struct pbuf *received_frame;

// Used by TinyUSB's net driver and usb_descriptors.c. First byte 0x02 marks a
// locally administered address. lwIP gets the same MAC with the LSB flipped.
//
// There are two Ethernet endpoints on this virtual cable: the host's adapter
// (which uses this MAC, reported to it in the USB descriptors) and lwIP on the
// Pico. They need different MACs, hence the flipped bit in init_lwip().
uint8_t tud_network_mac_address[6] = {0x02, 0x02, 0x84, 0x6A, 0x96, 0x00};

// The Pico's own address on the USB link. The /24 netmask means the whole
// 192.168.7.x range is reachable directly, without a router.
static const ip_addr_t ipaddr  = IP4(USB_NET_DEVICE_IP);
static const ip_addr_t netmask = IP4(255, 255, 255, 0);
static const ip_addr_t gateway = IP4(0, 0, 0, 0);

// Addresses offered to the host. Must live in RAM, the server stores MACs here.
// Each entry: { MAC of the client it was given to (filled in by the server),
// address, lease time in seconds }. 24 h leases; three entries so a couple of
// hosts (or a replug with a new MAC) can still get an address.
static dhcp_entry_t entries[] = {
    { {0}, IP4(USB_NET_HOST_IP), 24 * 60 * 60 },
    { {0}, IP4(192, 168, 7, 3),  24 * 60 * 60 },
    { {0}, IP4(192, 168, 7, 4),  24 * 60 * 60 },
};

// DHCP server settings. Positional fields after .dns are the domain suffix
// ("usb", so the host can resolve "pico.usb"), the number of entries and the
// entries themselves.
static const dhcp_config_t dhcp_config = {
    .router = IP4(0, 0, 0, 0),         // no router, so the host keeps its normal default route
    .port = 67,                        // standard DHCP server port
    .dns = IP4(USB_NET_DEVICE_IP),     // the Pico itself answers DNS queries
    "usb",
    TU_ARRAY_SIZE(entries),
    entries
};

/*
 * lwIP -> USB: send one Ethernet frame to the host.
 *
 * lwIP calls this whenever it has a complete frame (with Ethernet header) to
 * transmit. If the USB transmit buffer is still busy with the previous frame,
 * we keep running the USB stack until it is free. That briefly blocks the
 * caller, but USB is far faster than our ~0.4 Mbit/s data rate, so the wait is
 * short.
 */
static err_t linkoutput_fn(struct netif *netif, struct pbuf *p) {
    (void)netif;

    for (;;) {
        // Nothing we can do until the host has configured the device.
        // ERR_USE tells lwIP the frame was not sent; for UDP that means the
        // datagram is simply dropped.
        if (!tud_ready()) return ERR_USE;

        if (tud_network_can_xmit(p->tot_len)) {
            // TinyUSB will call tud_network_xmit_cb() to copy the frame.
            tud_network_xmit(p, 0);
            return ERR_OK;
        }

        // Let TinyUSB finish sending the previous frame.
        tud_task();
    }
}

/*
 * lwIP's IPv4 output hook: turn an IP packet into an Ethernet frame. etharp
 * looks up (or asks for, via ARP) the MAC address that belongs to `addr`,
 * adds the Ethernet header and then calls linkoutput_fn().
 */
static err_t output_fn(struct netif *netif, struct pbuf *p, const ip4_addr_t *addr) {
    return etharp_output(netif, p, addr);
}

/*
 * Called by netif_add() to fill in the interface description.
 */
static err_t netif_init_cb(struct netif *netif) {
    LWIP_ASSERT("netif != NULL", (netif != NULL));
    netif->mtu = CFG_TUD_NET_MTU;   // largest IP packet per frame (1500 bytes)

    // BROADCAST: the link can broadcast (needed for DHCP and ARP)
    // ETHARP:    use ARP to map IP addresses to MAC addresses
    // LINK_UP/UP: the virtual cable is always "plugged in" from lwIP's view
    netif->flags = NETIF_FLAG_BROADCAST | NETIF_FLAG_ETHARP | NETIF_FLAG_LINK_UP | NETIF_FLAG_UP;
    netif->state = NULL;

    // Two-letter interface name, only used in lwIP debug output.
    netif->name[0] = 'E';
    netif->name[1] = 'X';

    netif->linkoutput = linkoutput_fn;  // raw Ethernet frames out
    netif->output = output_fn;          // IPv4 packets out (goes through ARP)
    return ERR_OK;
}

// Initialise lwIP and register our one network interface.
static void init_lwip(void) {
    struct netif *netif = &netif_data;

    lwip_init();

    // lwIP's MAC: same as the host side's, with the lowest bit of the last
    // byte flipped so the two ends of the link don't share an address.
    netif->hwaddr_len = sizeof(tud_network_mac_address);
    memcpy(netif->hwaddr, tud_network_mac_address, sizeof(tud_network_mac_address));
    netif->hwaddr[5] ^= 0x01;

    // ip_input is lwIP's entry point for received IP packets.
    netif = netif_add(netif, &ipaddr, &netmask, &gateway, NULL, netif_init_cb, ip_input);
    netif_set_default(netif);   // route all outgoing traffic through it
}

// DNS server callback: return true and fill in `addr` for names we know.
// Only "pico.usb" resolves; everything else gets "not found".
static bool dns_query_proc(const char *name, ip4_addr_t *addr) {
    if (strcmp(name, "pico.usb") == 0) {
        *addr = ipaddr;
        return true;
    }
    return false;
}

/*
 * USB -> lwIP, part 1. TinyUSB calls this (from inside tud_task()) when a
 * frame has arrived from the host. `src` is only valid during the call, so
 * the frame is copied into a pbuf from lwIP's pool.
 *
 * Return value: true = frame consumed, false = "can't take it now", in which
 * case TinyUSB keeps the frame and offers it again later.
 */
bool tud_network_recv_cb(const uint8_t *src, uint16_t size) {
    // Still holding the previous frame, ask TinyUSB to retry later.
    if (received_frame) return false;

    if (size) {
        struct pbuf *p = pbuf_alloc(PBUF_RAW, size, PBUF_POOL);
        if (p) {
            // pbuf_take copies across a chain of pool buffers if one is too small.
            pbuf_take(p, src, size);
            received_frame = p;
        }
        // If allocation failed the frame is dropped; higher layers (TCP, DHCP
        // retries) recover from that.
    }

    return true;
}

/*
 * lwIP -> USB, part 2. TinyUSB calls this after tud_network_xmit() to copy
 * the frame (`ref` is the pbuf we passed in) into its USB transmit buffer.
 * A pbuf can be a chain of several buffers; pbuf_copy_partial flattens it.
 * Returns the number of bytes written.
 */
uint16_t tud_network_xmit_cb(uint8_t *dst, void *ref, uint16_t arg) {
    struct pbuf *p = (struct pbuf *)ref;
    (void)arg;
    return pbuf_copy_partial(p, dst, p->tot_len, 0);
}

// TinyUSB calls this when the host (re)configures the network interface,
// for example after the cable is replugged.
void tud_network_init_cb(void) {
    // Drop any leftover frame if the host re-initializes the interface.
    if (received_frame) {
        pbuf_free(received_frame);
        received_frame = NULL;
    }
}

/*
 * USB -> lwIP, part 2: hand a parked frame to lwIP and run lwIP's timers.
 */
static void service_traffic(void) {
    if (received_frame) {
        // ethernet_input() takes ownership on success.
        // It looks at the Ethernet type and passes ARP or IP packets on. On
        // failure the pbuf is still ours and must be freed here.
        if (ethernet_input(received_frame, &netif_data) != ERR_OK) {
            pbuf_free(received_frame);
        }
        received_frame = NULL;

        // Tell TinyUSB we are ready for the next frame.
        tud_network_recv_renew();
    }

    // Run any lwIP timers that are due (ARP cache ageing, TCP timers...).
    sys_check_timeouts();
}

void network_init(void) {
    // Board support from TinyUSB/pico-sdk: clocks, UART stdio on GP0/GP1.
    board_init();

    // Start TinyUSB as a USB device on root hub port 0, at whatever speed the
    // hardware supports (Full Speed, 12 Mbit/s, on the RP2040).
    tusb_rhport_init_t dev_init = {
        .role = TUSB_ROLE_DEVICE,
        .speed = TUSB_SPEED_AUTO
    };
    tusb_init(BOARD_TUD_RHPORT, &dev_init);

    // Optional board hook (a weak symbol, so it may not exist; then the
    // address is NULL and we skip it).
    if (board_init_after_tusb) {
        board_init_after_tusb();
    }

    init_lwip();

    // These loops are only a safety net; with this setup each call succeeds
    // the first time.
    while (!netif_is_up(&netif_data));
    while (dhserv_init(&dhcp_config) != ERR_OK);
    while (dnserv_init(IP_ADDR_ANY, 53, dns_query_proc) != ERR_OK);
}

void network_step(void) {
    tud_task();         // run the USB device stack (may call the callbacks above)
    service_traffic();  // give received frames to lwIP, run lwIP timers
}

/*
 * lwIP NO_SYS hooks.
 *
 * With NO_SYS=1 (no operating system) lwIP still needs a few functions from
 * the "system architecture" layer:
 *   - sys_arch_protect/unprotect: a critical section. Everything lwIP-related
 *     runs from the main loop on one core, never from an interrupt, so no
 *     locking is needed and these do nothing.
 *   - sys_now: a millisecond clock for lwIP's timers.
 */
sys_prot_t sys_arch_protect(void) {
    return 0;
}

void sys_arch_unprotect(sys_prot_t pval) {
    (void)pval;
}

uint32_t sys_now(void) {
    return to_ms_since_boot(get_absolute_time());
}
