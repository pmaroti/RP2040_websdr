/*
 * lwipopts.h - compile-time configuration of the lwIP TCP/IP stack.
 *
 * lwIP reads this file (by name) when it is compiled. Anything not set here
 * falls back to the defaults in lwip/opt.h. The settings below keep lwIP
 * small and match how it is used in this firmware: one USB Ethernet
 * interface, IPv4 only, UDP for the sample stream, TCP for the frequency
 * server, all driven from the main loop.
 */

#ifndef _LWIPOPTS_H_
#define _LWIPOPTS_H_

// Bare-metal lwIP (no RTOS), polled from the main loop.
// NO_SYS=1 removes threads, mailboxes and semaphores; only the "raw"
// callback APIs are available, and the application must call
// sys_check_timeouts() regularly (done in usb_network.c).
#define NO_SYS                          1

// Memory: lwIP has its own heap (MEM_SIZE bytes, used for PBUF_RAM buffers
// such as each outgoing sample datagram and queued TCP data) and pools of
// fixed-size objects. 16 KB of heap holds over a dozen 1 KB datagrams.
#define MEM_ALIGNMENT                   4           // 32-bit CPU: keep allocations word aligned
#define MEM_SIZE                        (16 * 1024) // lwIP heap size in bytes
#define PBUF_POOL_SIZE                  8           // pool buffers for received frames
#define MEMP_NUM_PBUF                   16          // pbuf headers that point at ROM/external data

// Which APIs and protocols to build.
#define LWIP_RAW                        0   // raw IP sockets: not needed
#define LWIP_NETCONN                    0   // netconn API needs an OS
#define LWIP_SOCKET                     0   // BSD sockets API needs an OS
#define LWIP_DHCP                       0   // the Pico is a DHCP *server* (dhserver.c), not a client
#define LWIP_ICMP                       1   // answer ping
#define LWIP_UDP                        1   // sample stream, DHCP and DNS servers
#define LWIP_TCP                        1   // frequency control server
#define LWIP_IPV4                       1
#define LWIP_IPV6                       0
#define ETH_PAD_SIZE                    0   // no padding before the Ethernet header

// Let the DHCP server see broadcast requests on port 67.
// A host asking for an address doesn't have one yet, so its request is sent
// to the broadcast address rather than to 192.168.7.1. lwIP normally drops
// such packets; this lets packets for port 67 through.
#define LWIP_IP_ACCEPT_UDP_PORT(p)      ((p) == PP_NTOHS(67))

// TCP sizing. MSS = largest TCP payload in one 1500-byte frame. The send
// buffer and window of two segments are plenty for short text commands.
#define TCP_MSS                         (1500 /*mtu*/ - 20 /*iphdr*/ - 20 /*tcphhr*/)
#define TCP_SND_BUF                     (2 * TCP_MSS)
#define TCP_WND                         (2 * TCP_MSS)

#define ETHARP_SUPPORT_STATIC_ENTRIES   1   // allow fixed ARP entries (unused, harmless)
#define LWIP_SINGLE_NETIF               1   // only one network interface: smaller and faster
#define LWIP_BROADCAST_PING             1   // reply to pings sent to the broadcast address

#define LWIP_STATS                      0   // no statistics counters, saves RAM
#define LWIP_NETIF_LINK_CALLBACK        0   // no link up/down callbacks needed
#define LWIP_NETIF_STATUS_CALLBACK      0   // no interface status callbacks needed

#endif /* _LWIPOPTS_H_ */
