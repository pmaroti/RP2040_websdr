/*
 * USB network glue: TinyUSB NCM (or RNDIS/ECM) + lwIP + DHCP/DNS servers.
 *
 * Adapted from TinyUSB's net_lwip_webserver example and the usb_network_stack
 * library of luigifcruz/pico-stuff (both MIT licensed, originally written by
 * Peter Lawrence, influenced by lrndis).
 */

#include <string.h>

#include "bsp/board_api.h"
#include "tusb.h"

#include "pico/stdlib.h"
#include "dhserver.h"
#include "dnserver.h"
#include "lwip/init.h"
#include "lwip/timeouts.h"
#include "netif/ethernet.h"

#include "usb_network.h"

static struct netif netif_data;

// Shared between tud_network_recv_cb() and service_traffic().
static struct pbuf *received_frame;

// Used by TinyUSB's net driver and usb_descriptors.c. First byte 0x02 marks a
// locally administered address. lwIP gets the same MAC with the LSB flipped.
uint8_t tud_network_mac_address[6] = {0x02, 0x02, 0x84, 0x6A, 0x96, 0x00};

static const ip_addr_t ipaddr  = IP4(USB_NET_DEVICE_IP);
static const ip_addr_t netmask = IP4(255, 255, 255, 0);
static const ip_addr_t gateway = IP4(0, 0, 0, 0);

// Addresses offered to the host. Must live in RAM, the server stores MACs here.
static dhcp_entry_t entries[] = {
    { {0}, IP4(USB_NET_HOST_IP), 24 * 60 * 60 },
    { {0}, IP4(192, 168, 7, 3),  24 * 60 * 60 },
    { {0}, IP4(192, 168, 7, 4),  24 * 60 * 60 },
};

static const dhcp_config_t dhcp_config = {
    .router = IP4(0, 0, 0, 0),         // no router, so the host keeps its normal default route
    .port = 67,
    .dns = IP4(USB_NET_DEVICE_IP),
    "usb",
    TU_ARRAY_SIZE(entries),
    entries
};

static err_t linkoutput_fn(struct netif *netif, struct pbuf *p) {
    (void)netif;

    for (;;) {
        // Nothing we can do until the host has configured the device.
        if (!tud_ready()) return ERR_USE;

        if (tud_network_can_xmit(p->tot_len)) {
            tud_network_xmit(p, 0);
            return ERR_OK;
        }

        // Let TinyUSB finish sending the previous frame.
        tud_task();
    }
}

static err_t output_fn(struct netif *netif, struct pbuf *p, const ip4_addr_t *addr) {
    return etharp_output(netif, p, addr);
}

static err_t netif_init_cb(struct netif *netif) {
    LWIP_ASSERT("netif != NULL", (netif != NULL));
    netif->mtu = CFG_TUD_NET_MTU;
    netif->flags = NETIF_FLAG_BROADCAST | NETIF_FLAG_ETHARP | NETIF_FLAG_LINK_UP | NETIF_FLAG_UP;
    netif->state = NULL;
    netif->name[0] = 'E';
    netif->name[1] = 'X';
    netif->linkoutput = linkoutput_fn;
    netif->output = output_fn;
    return ERR_OK;
}

static void init_lwip(void) {
    struct netif *netif = &netif_data;

    lwip_init();

    netif->hwaddr_len = sizeof(tud_network_mac_address);
    memcpy(netif->hwaddr, tud_network_mac_address, sizeof(tud_network_mac_address));
    netif->hwaddr[5] ^= 0x01;

    netif = netif_add(netif, &ipaddr, &netmask, &gateway, NULL, netif_init_cb, ip_input);
    netif_set_default(netif);
}

static bool dns_query_proc(const char *name, ip4_addr_t *addr) {
    if (strcmp(name, "pico.usb") == 0) {
        *addr = ipaddr;
        return true;
    }
    return false;
}

bool tud_network_recv_cb(const uint8_t *src, uint16_t size) {
    // Still holding the previous frame, ask TinyUSB to retry later.
    if (received_frame) return false;

    if (size) {
        struct pbuf *p = pbuf_alloc(PBUF_RAW, size, PBUF_POOL);
        if (p) {
            pbuf_take(p, src, size);
            received_frame = p;
        }
    }

    return true;
}

uint16_t tud_network_xmit_cb(uint8_t *dst, void *ref, uint16_t arg) {
    struct pbuf *p = (struct pbuf *)ref;
    (void)arg;
    return pbuf_copy_partial(p, dst, p->tot_len, 0);
}

void tud_network_init_cb(void) {
    // Drop any leftover frame if the host re-initializes the interface.
    if (received_frame) {
        pbuf_free(received_frame);
        received_frame = NULL;
    }
}

static void service_traffic(void) {
    if (received_frame) {
        // ethernet_input() takes ownership on success.
        if (ethernet_input(received_frame, &netif_data) != ERR_OK) {
            pbuf_free(received_frame);
        }
        received_frame = NULL;
        tud_network_recv_renew();
    }

    sys_check_timeouts();
}

void network_init(void) {
    board_init();

    tusb_rhport_init_t dev_init = {
        .role = TUSB_ROLE_DEVICE,
        .speed = TUSB_SPEED_AUTO
    };
    tusb_init(BOARD_TUD_RHPORT, &dev_init);

    if (board_init_after_tusb) {
        board_init_after_tusb();
    }

    init_lwip();
    while (!netif_is_up(&netif_data));
    while (dhserv_init(&dhcp_config) != ERR_OK);
    while (dnserv_init(IP_ADDR_ANY, 53, dns_query_proc) != ERR_OK);
}

void network_step(void) {
    tud_task();
    service_traffic();
}

// lwIP NO_SYS hooks.
sys_prot_t sys_arch_protect(void) {
    return 0;
}

void sys_arch_unprotect(sys_prot_t pval) {
    (void)pval;
}

uint32_t sys_now(void) {
    return to_ms_since_boot(get_absolute_time());
}
