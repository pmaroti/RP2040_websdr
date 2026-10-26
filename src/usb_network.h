/*
 * usb_network.h - public interface of the USB network layer.
 *
 * The rest of the firmware only needs three things from usb_network.c:
 * the IP addresses in use, network_init() once at startup, and
 * network_step() called continuously from the main loop. Everything about
 * TinyUSB and lwIP's network interface stays hidden inside usb_network.c.
 */

#ifndef _USB_NETWORK_H_
#define _USB_NETWORK_H_

#include "lwip/ip_addr.h"   // ip_addr_t and IPADDR4_INIT_BYTES

// The Pico shows up on the host as a USB network adapter. It takes
// 192.168.7.1 and hands out 192.168.7.2..4 to the host over DHCP.
//
// The addresses are written as comma-separated byte lists (not strings) so
// they can be fed to lwIP's compile-time initializer macro through IP4().
#define USB_NET_DEVICE_IP   192, 168, 7, 1
#define USB_NET_HOST_IP     192, 168, 7, 2

// Expands a comma-separated address like the ones above into an ip_addr_t initializer.
//
// Why a variadic macro: IPADDR4_INIT_BYTES(a, b, c, d) needs four separate
// arguments, but USB_NET_HOST_IP is a single token until it is expanded.
// Passing it through __VA_ARGS__ expands it first, so
// IP4(USB_NET_HOST_IP) becomes IPADDR4_INIT_BYTES(192, 168, 7, 2).
#define IP4(...)            IPADDR4_INIT_BYTES(__VA_ARGS__)

// Bring up TinyUSB, lwIP and the DHCP/DNS servers.
// Also initialises the board (clocks, UART stdio). Call once, first thing in main().
void network_init(void);

// Service USB and lwIP. Call this often from the main loop.
// It moves received Ethernet frames from USB into lwIP and runs lwIP's
// timers (ARP, TCP retransmits, DHCP leases). If it isn't called, the
// network freezes.
void network_step(void);

#endif /* _USB_NETWORK_H_ */
