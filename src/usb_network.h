#ifndef _USB_NETWORK_H_
#define _USB_NETWORK_H_

#include "lwip/ip_addr.h"

// The Pico shows up on the host as a USB network adapter. It takes
// 192.168.7.1 and hands out 192.168.7.2..4 to the host over DHCP.
#define USB_NET_DEVICE_IP   192, 168, 7, 1
#define USB_NET_HOST_IP     192, 168, 7, 2

// Expands a comma-separated address like the ones above into an ip_addr_t initializer.
#define IP4(...)            IPADDR4_INIT_BYTES(__VA_ARGS__)

// Bring up TinyUSB, lwIP and the DHCP/DNS servers.
void network_init(void);

// Service USB and lwIP. Call this often from the main loop.
void network_step(void);

#endif /* _USB_NETWORK_H_ */
