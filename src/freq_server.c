/*
 * Line-based TCP server for setting the square wave frequency.
 *
 * Works with nc or telnet. One command per line:
 *   1000        set 1000 Hz (k and M suffixes work: 10k, 1.5M)
 *   FREQ 1000   same as above
 *   ?           report the current frequency (also FREQ?)
 *   OFF         stop the output (same as 0)
 * Replies are one line: "OK <actual Hz>", "FREQ <actual Hz>" or "ERR <reason>".
 */

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "lwip/tcp.h"

#include "freq_server.h"
#include "squarewave.h"

#define MAX_CLIENTS     4
#define LINE_MAX        64

typedef struct {
    struct tcp_pcb *pcb;
    char line[LINE_MAX];
    size_t len;
    bool overflow;
} client_t;

static client_t clients[MAX_CLIENTS];

static void reply(client_t *c, const char *fmt, ...) __attribute__((format(printf, 2, 3)));

static void reply(client_t *c, const char *fmt, ...) {
    char buf[96];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 3) n = sizeof(buf) - 3;
    buf[n++] = '\r';
    buf[n++] = '\n';

    if (tcp_sndbuf(c->pcb) >= n) {
        tcp_write(c->pcb, buf, n, TCP_WRITE_FLAG_COPY);
        tcp_output(c->pcb);
    }
}

// Parse "1000", "1.5k", "2M" into Hz. Returns false on junk.
static bool parse_freq(const char *s, double *out) {
    char *end;
    double v = strtod(s, &end);
    if (end == s) return false;
    while (isspace((unsigned char)*end)) end++;
    if (*end == 'k' || *end == 'K') { v *= 1e3; end++; }
    else if (*end == 'M') { v *= 1e6; end++; }
    while (isspace((unsigned char)*end)) end++;
    if (strncasecmp(end, "hz", 2) == 0) end += 2;
    while (isspace((unsigned char)*end)) end++;
    if (*end != '\0' || v < 0) return false;
    *out = v;
    return true;
}

static void handle_line(client_t *c, char *line) {
    // Trim surrounding whitespace.
    while (isspace((unsigned char)*line)) line++;
    char *e = line + strlen(line);
    while (e > line && isspace((unsigned char)e[-1])) *--e = '\0';
    if (*line == '\0') return;

    if (strcasecmp(line, "?") == 0 || strcasecmp(line, "FREQ?") == 0) {
        reply(c, "FREQ %.3f", squarewave_get());
        return;
    }
    if (strcasecmp(line, "OFF") == 0) {
        squarewave_set(0);
        printf("square wave off\n");
        reply(c, "OK 0");
        return;
    }
    if (strncasecmp(line, "FREQ", 4) == 0 && isspace((unsigned char)line[4])) {
        line += 5;
    }

    double hz;
    if (!parse_freq(line, &hz)) {
        reply(c, "ERR unknown command, use <Hz>[k|M], OFF or ?");
        return;
    }

    double actual = squarewave_set(hz);
    if (actual < 0) {
        reply(c, "ERR out of range %.3f-%.0f Hz", squarewave_min_hz(), squarewave_max_hz());
        return;
    }
    printf("square wave %.3f Hz\n", actual);
    reply(c, "OK %.3f", actual);
}

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

static err_t on_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err) {
    client_t *c = arg;
    (void)err;

    if (p == NULL) {
        // Remote side closed the connection.
        return client_close(c);
    }

    for (struct pbuf *q = p; q != NULL; q = q->next) {
        const char *data = q->payload;
        for (u16_t i = 0; i < q->len; i++) {
            char ch = data[i];
            if (ch == '\n' || ch == '\r') {
                if (c->overflow) {
                    reply(c, "ERR line too long");
                } else {
                    c->line[c->len] = '\0';
                    handle_line(c, c->line);
                }
                c->len = 0;
                c->overflow = false;
            } else if (c->len < LINE_MAX - 1) {
                c->line[c->len++] = ch;
            } else {
                c->overflow = true;
            }
        }
    }

    tcp_recved(pcb, p->tot_len);
    pbuf_free(p);
    return ERR_OK;
}

static void on_err(void *arg, err_t err) {
    client_t *c = arg;
    (void)err;
    // The pcb is already freed by lwIP when this is called.
    if (c) c->pcb = NULL;
}

static err_t on_accept(void *arg, struct tcp_pcb *pcb, err_t err) {
    (void)arg;
    if (err != ERR_OK || pcb == NULL) return ERR_VAL;

    client_t *c = NULL;
    for (int i = 0; i < MAX_CLIENTS; i++) {
        if (clients[i].pcb == NULL) {
            c = &clients[i];
            break;
        }
    }
    if (c == NULL) {
        tcp_abort(pcb);
        return ERR_ABRT;
    }

    c->pcb = pcb;
    c->len = 0;
    c->overflow = false;
    tcp_arg(pcb, c);
    tcp_recv(pcb, on_recv);
    tcp_err(pcb, on_err);

    reply(c, "pico square wave: <Hz>[k|M] to set, OFF to stop, ? to query. Now %.3f Hz",
          squarewave_get());
    return ERR_OK;
}

void freq_server_init(unsigned short port) {
    struct tcp_pcb *pcb = tcp_new();
    tcp_bind(pcb, IP_ADDR_ANY, port);
    pcb = tcp_listen_with_backlog(pcb, MAX_CLIENTS);
    tcp_accept(pcb, on_accept);
}
