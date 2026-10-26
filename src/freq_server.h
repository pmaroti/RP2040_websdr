/*
 * freq_server.h - TCP server for setting the square wave frequency.
 *
 * A client connects (for example `nc 192.168.7.1 7779`) and sends one text
 * command per line; see freq_server.c for the protocol.
 */

#ifndef _FREQ_SERVER_H_
#define _FREQ_SERVER_H_

// Start the TCP frequency control server on the given port.
// Requires lwIP to be running (call after network_init()) and the square
// wave generator to be initialised (squarewave_init()). Connections are then
// handled entirely from lwIP callbacks during network_step().
void freq_server_init(unsigned short port);

#endif /* _FREQ_SERVER_H_ */
