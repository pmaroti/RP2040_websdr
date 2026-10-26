/*
 * squarewave.h - PIO square wave generator.
 *
 * Generates a 50% duty cycle square wave on one GPIO pin using a PIO state
 * machine, so the output keeps running with no CPU involvement. See
 * squarewave.pio for the PIO programs and squarewave.c for how a frequency
 * is turned into a PIO clock divider.
 *
 * Frequencies are doubles in Hz. The hardware can only produce frequencies of
 * the form clk_sys / (cycles * n/256), so the value actually produced is
 * usually slightly different from the one requested; the functions return
 * the real one.
 */

#ifndef _SQUAREWAVE_H_
#define _SQUAREWAVE_H_

// Load the PIO programs and claim a state machine. The output stays low
// until a frequency is set.
// Call once at startup, before any other squarewave_* function.
void squarewave_init(unsigned int pin);

// Set the output frequency in Hz, 0 stops the output (pin held low).
// Returns the frequency actually produced, or -1 if out of range.
// An out-of-range request leaves the current output unchanged.
double squarewave_set(double freq_hz);

// Frequency currently produced, 0 if stopped.
double squarewave_get(void);

// Lowest and highest frequency squarewave_set() accepts, for the current
// system clock (about 30 Hz and 62.5 MHz at the default 125 MHz).
double squarewave_min_hz(void);
double squarewave_max_hz(void);

#endif /* _SQUAREWAVE_H_ */
