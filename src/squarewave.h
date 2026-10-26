#ifndef _SQUAREWAVE_H_
#define _SQUAREWAVE_H_

// Load the PIO programs and claim a state machine. The output stays low
// until a frequency is set.
void squarewave_init(unsigned int pin);

// Set the output frequency in Hz, 0 stops the output (pin held low).
// Returns the frequency actually produced, or -1 if out of range.
double squarewave_set(double freq_hz);

// Frequency currently produced, 0 if stopped.
double squarewave_get(void);

double squarewave_min_hz(void);
double squarewave_max_hz(void);

#endif /* _SQUAREWAVE_H_ */
