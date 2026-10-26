* BF961 dual-gate MOSFET mixer, circuit only (included by the top-level decks).
* Needs these .params from the including deck: flo (LO Hz), frf (RF Hz), arf (RF V peak)
* Node names match the schematic in README.md.

.include ../models/bf961.lib

* ---------------- supplies ----------------
V9   vdd  0 DC 9
V33  v33  0 DC 3.3         ; Pico 3V3(OUT) pin

* ---------------- LO: RP2040 GP8 PIO square wave ----------------
* 3.3 V CMOS, 2 ns edges
VLO  lo_pin 0 PULSE(0 3.3 0 2n 2n {0.5/flo-2n} {1/flo})
R1   lo_pin g2 100          ; series damping into gate 2

* ---------------- RF input (no input filter in this version) ----------------
VRF  rf_src 0 SIN(0 {arf} {frf})
RSRC rf_src rf 50           ; 50 ohm antenna / generator
C1   rf   g1 10n            ; RF coupling
R2   g1   0  100k           ; gate 1 DC return (VG1S = -VS, self bias)

* ---------------- mixer ----------------
XQ1  drain g2 g1 src BF961
R3   src 0 220              ; source self-bias
C2   src 0 47u              ; source bypass (keeps IF gain flat down to ~100 Hz)
C3   src 0 100n
R4   vdd drain 1k           ; drain load
C4   vdd 0 100u             ; 9 V rail decoupling (ideal source here anyway)

* ---------------- single-pole RC low-pass ----------------
R5   drain filt 10k
C5   filt  0 3.3n

* ---------------- level shift to ADC mid-scale + protection ----------------
C6   filt adc_b 1u          ; DC block: 9 V side never reaches the ADC
R6   v33  adc_b 100k        ; bias divider -> 1.65 V
R7   adc_b 0    100k
R8   adc_b adc 1k           ; ADC series / current limit
D1   adc v33 BAT54          ; clamp to 3V3 (BAT54S, upper diode)
D2   0   adc BAT54          ; clamp to GND (BAT54S, lower diode)
CADC adc 0 10p              ; RP2040 ADC input + pin capacitance
.model BAT54 D (IS=2e-7 N=1.04 RS=2.5 BV=30 CJO=10p TT=5n)
