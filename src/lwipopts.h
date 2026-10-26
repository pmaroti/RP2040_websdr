#ifndef _LWIPOPTS_H_
#define _LWIPOPTS_H_

// Bare-metal lwIP (no RTOS), polled from the main loop.
#define NO_SYS                          1
#define MEM_ALIGNMENT                   4
#define MEM_SIZE                        (16 * 1024)
#define PBUF_POOL_SIZE                  8
#define MEMP_NUM_PBUF                   16

#define LWIP_RAW                        0
#define LWIP_NETCONN                    0
#define LWIP_SOCKET                     0
#define LWIP_DHCP                       0
#define LWIP_ICMP                       1
#define LWIP_UDP                        1
#define LWIP_TCP                        1
#define LWIP_IPV4                       1
#define LWIP_IPV6                       0
#define ETH_PAD_SIZE                    0

// Let the DHCP server see broadcast requests on port 67.
#define LWIP_IP_ACCEPT_UDP_PORT(p)      ((p) == PP_NTOHS(67))

#define TCP_MSS                         (1500 /*mtu*/ - 20 /*iphdr*/ - 20 /*tcphhr*/)
#define TCP_SND_BUF                     (2 * TCP_MSS)
#define TCP_WND                         (2 * TCP_MSS)

#define ETHARP_SUPPORT_STATIC_ENTRIES   1
#define LWIP_SINGLE_NETIF               1
#define LWIP_BROADCAST_PING             1

#define LWIP_STATS                      0
#define LWIP_NETIF_LINK_CALLBACK        0
#define LWIP_NETIF_STATUS_CALLBACK      0

#endif /* _LWIPOPTS_H_ */
