/*
 * PIO square wave output. The PIO state machine toggles the pin from a tight
 * loop and the clock divider sets the frequency (same technique as
 * pmaroti/am_generator). The divider has 8 fractional bits, so frequencies
 * that need a fractional divider have up to one PIO clock of period jitter.
 */

#include "pico/stdlib.h"
#include "hardware/clocks.h"
#include "hardware/pio.h"

#include "squarewave.h"
#include "squarewave.pio.h"

#define FAST_CYCLES     2       // PIO clocks per period, squarewave
#define SLOW_CYCLES     64      // PIO clocks per period, squarewave_slow
#define SLOW_BELOW_HZ   10000.0 // use the slow program below this frequency
#define DIV_MAX         65535.996

static PIO pio = pio0;
static uint sm;
static uint pin;
static uint offset_fast, offset_slow;
static int running_program = -1;    // -1 stopped, else cycles per period
static double current_hz;

void squarewave_init(unsigned int out_pin) {
    pin = out_pin;
    offset_fast = pio_add_program(pio, &squarewave_program);
    offset_slow = pio_add_program(pio, &squarewave_slow_program);
    sm = pio_claim_unused_sm(pio, true);

    gpio_init(pin);
    gpio_set_dir(pin, GPIO_OUT);
    gpio_put(pin, 0);
    gpio_set_drive_strength(pin, GPIO_DRIVE_STRENGTH_12MA);
    gpio_set_slew_rate(pin, GPIO_SLEW_RATE_FAST);
}

double squarewave_min_hz(void) {
    return clock_get_hz(clk_sys) / (SLOW_CYCLES * DIV_MAX);
}

double squarewave_max_hz(void) {
    return clock_get_hz(clk_sys) / (double)FAST_CYCLES;
}

static void start_program(int cycles, uint16_t div_int, uint8_t div_frac) {
    uint offset = (cycles == FAST_CYCLES) ? offset_fast : offset_slow;
    pio_sm_config c = (cycles == FAST_CYCLES)
        ? squarewave_program_get_default_config(offset)
        : squarewave_slow_program_get_default_config(offset);

    pio_sm_set_enabled(pio, sm, false);
    sm_config_set_set_pins(&c, pin, 1);
    sm_config_set_clkdiv_int_frac8(&c, div_int, div_frac);
    pio_gpio_init(pio, pin);
    pio_sm_init(pio, sm, offset, &c);
    pio_sm_set_enabled(pio, sm, true);
    running_program = cycles;
}

double squarewave_set(double freq_hz) {
    if (freq_hz == 0) {
        pio_sm_set_enabled(pio, sm, false);
        gpio_init(pin);
        gpio_set_dir(pin, GPIO_OUT);
        gpio_put(pin, 0);
        running_program = -1;
        current_hz = 0;
        return 0;
    }
    if (!(freq_hz >= squarewave_min_hz() && freq_hz <= squarewave_max_hz())) {
        return -1;
    }

    double clk = clock_get_hz(clk_sys);
    int cycles = (freq_hz < SLOW_BELOW_HZ) ? SLOW_CYCLES : FAST_CYCLES;

    // Divider in 16.8 fixed point, rounded to nearest.
    double div = clk / (cycles * freq_hz);
    uint32_t div_fixed = (uint32_t)(div * 256.0 + 0.5);
    if (div_fixed < 256) div_fixed = 256;
    if (div_fixed > 0xFFFFFF) div_fixed = 0xFFFFFF;
    uint16_t div_int = div_fixed >> 8;
    uint8_t div_frac = div_fixed & 0xFF;

    if (running_program == cycles) {
        // Same program already running: just retune, no glitch on the pin.
        pio_sm_set_clkdiv_int_frac8(pio, sm, div_int, div_frac);
    } else {
        start_program(cycles, div_int, div_frac);
    }

    current_hz = clk * 256.0 / ((double)cycles * div_fixed);
    return current_hz;
}

double squarewave_get(void) {
    return current_hz;
}
