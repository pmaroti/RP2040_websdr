/*
 * Line-based TCP server for setting the square wave frequency.
 *
 * Works with nc or telnet. One command per line:
 *   1000        set 1000 Hz (k and M suffixes work: 10k, 1.5M)
 *   FREQ 1000   same as above
 *   ?           report the current frequency (also FREQ?)
 *   OFF         stop the output (same as 0)
 * Replies are one line: "OK <actual Hz>", "FREQ <actual Hz>" or "ERR <reason>".
 *
 * Example session:
 *
 *   $ nc 192.168.7.1 7779
 *   pico square wave: <Hz>[k|M] to set, OFF to stop, ? to query. Now 0.000 Hz
 *   7.1M
 *   OK 7098491.571
 *   ?
 *   FREQ 7098491.571
 *
 * ---------------------------------------------------------------------------
 * How it is built
 * ---------------------------------------------------------------------------
 *
 * This uses lwIP's "raw" (callback) TCP API, the only one available without
 * an operating system. Nothing here blocks: lwIP calls our functions from
 * inside network_step() when something happens:
 *
 *   on_accept  a client connected
 *   on_recv    data arrived (or the client closed the connection)
 *   on_err     the connection broke (reset, timeout)
 *
 * TCP is a byte stream, not a message stream: a command can arrive split
 * across several packets, or several commands in one packet. Each client
 * therefore has a small line buffer that collects bytes until a CR or LF,
 * then the complete line is handled.
 */

#include <ctype.h>      // isspace
#include <stdarg.h>     // va_list for reply()
#include <stdio.h>      // vsnprintf, printf
#include <stdlib.h>     // strtod
#include <string.h>
#include <strings.h>    // strcasecmp, strncasecmp (case-insensitive compare)

#include "lwip/tcp.h"

#include "freq_server.h"
#include "squarewave.h"

#define MAX_CLIENTS     4       // simultaneous connections; more are refused
#define LINE_MAX        64      // longest accepted command line, including the terminator

// Per-connection state.
typedef struct {
    struct tcp_pcb *pcb;        // lwIP connection handle, NULL = slot free
    char line[LINE_MAX];        // bytes of the line received so far
    size_t len;                 // number of bytes in `line`
    bool overflow;              // line got longer than LINE_MAX; reject it at the newline
} client_t;

// Fixed pool of connection slots (no dynamic memory needed).
static client_t clients[MAX_CLIENTS];

// The format attribute lets the compiler check reply()'s printf-style
// arguments, just as it does for printf itself.
static void reply(client_t *c, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

/*
 * Send one line of text (printf-style) followed by CR LF.
 *
 * tcp_write() only queues data in lwIP's send buffer; tcp_output() asks lwIP
 * to transmit it now instead of waiting. TCP_WRITE_FLAG_COPY makes lwIP copy
 * the bytes, since `buf` disappears when this function returns. If the send
 * buffer is full (a client that never reads), the reply is skipped rather than
 * blocking the whole firmware.
 */
static void reply(client_t *c, const char *fmt, ...) {
    char buf[96];
    va_list ap;
    va_start(ap, fmt);
    // Leave 2 bytes for CR LF.
    int n = vsnprintf(buf, sizeof(buf) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;

    // vsnprintf returns the length it *wanted*; if the text was truncated,
    // clamp to what actually fits in the buffer.
    if (n > (int)sizeof(buf) - 3) n = sizeof(buf) - 3;
    buf[n++] = '\r';
    buf[n++] = '\n';

    if (tcp_sndbuf(c->pcb) >= n) {
        tcp_write(c->pcb, buf, n, TCP_WRITE_FLAG_COPY);
        tcp_output(c->pcb);
    }
}

// Parse "1000", "1.5k", "2M" into Hz. Returns false on junk.
// Also accepts an optional "Hz" (any case) and spaces between the parts,
// e.g. "7.1 MHz". "k"/"K" = kilo, "M" = mega (lower-case "m" is rejected,
// since it would mean milli).
static bool parse_freq(const char *s, double *out) {
    char *end;
    double v = strtod(s, &end);         // number part; `end` points just after it
    if (end == s) return false;         // no number at all
    while (isspace((unsigned char)*end)) end++;
    if (*end == 'k' || *end == 'K') { v *= 1e3; end++; }
    else if (*end == 'M') { v *= 1e6; end++; }
    while (isspace((unsigned char)*end)) end++;
    if (strncasecmp(end, "hz", 2) == 0) end += 2;
    while (isspace((unsigned char)*end)) end++;
    if (*end != '\0' || v < 0) return false;   // trailing junk or negative
    *out = v;
    return true;
}

// Execute one complete command line and send the reply.
static void handle_line(client_t *c, char *line) {
    // Trim surrounding whitespace.
    while (isspace((unsigned char)*line)) line++;
    char *e = line + strlen(line);
    while (e > line && isspace((unsigned char)e[-1])) *--e = '\0';
    if (*line == '\0') return;  // empty line (e.g. the LF after a CR): ignore

    // Query.
    if (strcasecmp(line, "?") == 0 || strcasecmp(line, "FREQ?") == 0) {
        reply(c, "FREQ %.3f", squarewave_get());
        return;
    }

    // Stop.
    if (strcasecmp(line, "OFF") == 0) {
        squarewave_set(0);
        printf("square wave off\n");
        reply(c, "OK 0");
        return;
    }

    // Optional "FREQ " prefix before a number.
    if (strncasecmp(line, "FREQ", 4) == 0 && isspace((unsigned char)line[4])) {
        line += 5;
    }

    double hz;
    if (!parse_freq(line, &hz)) {
        reply(c, "ERR unknown command, use <Hz>[k|M], OFF or ?");
        return;
    }

    // squarewave_set returns the frequency really produced, or -1 if the
    // request is outside what the hardware can do (0 is accepted = off).
    double actual = squarewave_set(hz);
    if (actual < 0) {
        reply(c, "ERR out of range %.3f-%.0f Hz", squarewave_min_hz(), squarewave_max_hz());
        return;
    }
    printf("square wave %.3f Hz\n", actual);
    reply(c, "OK %.3f", actual);
}

/*
 * Close a connection and free its slot.
 *
 * The callbacks are cleared first so lwIP won't call us about this
 * connection again. tcp_close() can fail if lwIP is out of memory; then the
 * connection is aborted (reset) instead, and the caller must return ERR_ABRT
 * to lwIP to signal that the pcb no longer exists.
 */
static err_t client_close(client_t *c) {
    err_t result = ERR_OK;
    if (c->pcb) {
        tcp_arg(c->pcb, NULL);
        tcp_recv(c->pcb, NULL);
        tcp_err(c->pcb, NULL);
        if (tcp_close(c->pcb) != ERR_OK) {
            tcp_abort(c->pcb);
            result = ERR_ABRT;
        }
    }
    c->pcb = NULL;
    return result;
}

/*
 * lwIP callback: data arrived on a connection.
 *
 * `p` is a chain of pbufs holding the received bytes, or NULL when the client
 * has closed its side of the connection. We own `p` and must free it.
 */
static err_t on_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err) {
    client_t *c = arg;
    (void)err;

    if (p == NULL) {
        // Remote side closed the connection.
        return client_close(c);
    }

    // Walk every byte of every pbuf in the chain, building lines.
    for (struct pbuf *q = p; q != NULL; q = q->next) {
        const char *data = q->payload;
        for (u16_t i = 0; i < q->len; i++) {
            char ch = data[i];
            if (ch == '\n' || ch == '\r') {
                // End of line (CR, LF or CR LF all work; the empty line
                // between CR and LF is ignored by handle_line).
                if (c->overflow) {
                    reply(c, "ERR line too long");
                } else {
                    c->line[c->len] = '\0';
                    handle_line(c, c->line);
                }
                c->len = 0;
                c->overflow = false;
            } else if (c->len < LINE_MAX - 1) {
                c->line[c->len++] = ch;     // keep 1 byte free for the '\0'
            } else {
                c->overflow = true;         // drop the rest of this line
            }
        }
    }

    // Tell lwIP we've consumed the data, so it can advertise a larger receive
    // window to the client again (TCP flow control).
    tcp_recved(pcb, p->tot_len);
    pbuf_free(p);
    return ERR_OK;
}

/*
 * lwIP callback: the connection failed (reset by the client, timed out...).
 */
static void on_err(void *arg, err_t err) {
    client_t *c = arg;
    (void)err;
    // The pcb is already freed by lwIP when this is called.
    // So we must not touch it; just mark the slot free.
    if (c) c->pcb = NULL;
}

/*
 * lwIP callback: a new client connected to the listening port.
 */
static err_t on_accept(void *arg, struct tcp_pcb *pcb, err_t err) {
    (void)arg;
    if (err != ERR_OK || pcb == NULL) return ERR_VAL;

    // Find a free slot.
    client_t *c = NULL;
    for (int i = 0; i < MAX_CLIENTS; i++) {
        if (clients[i].pcb == NULL) {
            c = &clients[i];
            break;
        }
    }
    if (c == NULL) {
        // All slots busy: refuse. After tcp_abort() we must return ERR_ABRT.
        tcp_abort(pcb);
        return ERR_ABRT;
    }

    c->pcb = pcb;
    c->len = 0;
    c->overflow = false;

    // Register our callbacks; `c` is passed back to them as `arg`.
    tcp_arg(pcb, c);
    tcp_recv(pcb, on_recv);
    tcp_err(pcb, on_err);

    // Greeting with a short help text and the current frequency.
    reply(c, "pico square wave: <Hz>[k|M] to set, OFF to stop, ? to query. Now %.3f Hz",
          squarewave_get());
    return ERR_OK;
}

void freq_server_init(unsigned short port) {
    // Create a TCP pcb, bind it to the port on all addresses, and turn it into
    // a listening socket. tcp_listen returns a new, smaller "listen pcb" and
    // frees the original, hence the reassignment.
    struct tcp_pcb *pcb = tcp_new();
    tcp_bind(pcb, IP_ADDR_ANY, port);
    pcb = tcp_listen_with_backlog(pcb, MAX_CLIENTS);
    tcp_accept(pcb, on_accept);
}
