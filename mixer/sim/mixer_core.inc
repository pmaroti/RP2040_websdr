* =============================================================================
* BF961 dual-gate MOSFET mixer for the RP2040 webSDR - circuit only
* =============================================================================
*
* This file holds only the circuit. It is pulled in with ".include" by the
* top-level decks (mixer.cir, and the decks that run.py writes), which set
* the frequencies and decide which analysis to run.
*
* The including deck must define these parameters BEFORE the .include:
*   flo   LO frequency in Hz       (the PIO square wave on GP8)
*   frf   RF test tone in Hz       (the "antenna" signal)
*   arf   RF test tone amplitude   (V peak, at the generator)
*
* Signal path, in one line:
*   RF -> C1 -> gate 1 of Q1;  LO (GP8) -> R1 -> gate 2 of Q1;
*   Q1 drain -> R5/C5 low-pass -> C6 DC block -> 1.65 V bias -> R8 -> GP26 (ADC)
*
* Node names (used in .ic, save, plot and in the schematic):
*   vdd     +9 V mixer supply          v33    Pico 3V3(OUT), 3.3 V
*   lo_pin  GP8 output                 g2     gate 2 of Q1
*   rf_src  ideal RF generator         rf     antenna terminal (after 50 ohm)
*   g1      gate 1 of Q1               src    source of Q1
*   drain   drain of Q1                filt   output of the RC low-pass
*   adc_b   biased IF (before R8)      adc    the GP26 / ADC0 pin itself
*
* SPICE syntax reminder:
*   Rname node1 node2 value      resistor
*   Cname node1 node2 value      capacitor (first node = + for electrolytics)
*   Dname anode cathode model    diode
*   Vname node+ node- source     voltage source
*   Xname nodes... subckt        instance of a subcircuit
*   Suffixes: f=1e-15 p=1e-12 n=1e-9 u=1e-6 m=1e-3 k=1e3 meg=1e6
*   Text after ';' is a comment.
* =============================================================================

* The BF961 subcircuit. The path is relative to THIS file (sim/), so the
* decks work no matter which folder ngspice is started from.
.include ../models/bf961.lib

* ---------------- supplies ----------------
* Ideal sources: no ripple and no internal resistance.
V9   vdd  0 DC 9           ; +9 V supply for the mixer
V33  v33  0 DC 3.3         ; Pico 3V3(OUT) pin, feeds the ADC bias and clamp

* ---------------- LO: RP2040 GP8 PIO square wave ----------------
* The firmware's PIO program toggles GP8 between 0 V and 3.3 V (3.3 V CMOS).
* PULSE(V1 V2 delay rise fall width period):
*   low 0 V, high 3.3 V, no delay, 2 ns rise and fall times,
*   high time = half a period minus one edge, so the duty cycle is 50 %.
VLO  lo_pin 0 PULSE(0 3.3 0 2n 2n {0.5/flo-2n} {1/flo})
* R1: series resistor between GP8 and gate 2. With the gate and wiring
* capacitance it damps ringing on the fast edges and limits the current
* that charges the gate. Gate 2 is DC coupled: it really sees 0 V / 3.3 V.
R1   lo_pin g2 100

* ---------------- RF input (no input filter in this version) ----------------
* SIN(offset amplitude frequency): an ideal RF test tone.
VRF  rf_src 0 SIN(0 {arf} {frf})
RSRC rf_src rf 50           ; 50 ohm source resistance (antenna / generator)
C1   rf   g1 10n            ; RF coupling cap: passes RF, blocks DC
* R2 gives gate 1 a DC path to ground, so gate 1 sits at 0 V DC. The source
* sits a little above ground (current through R3), therefore
* VG1S = 0 - V(src) is negative: this is "self bias".
R2   g1   0  100k

* ---------------- mixer ----------------
* Q1 = BF961, pin order: drain gate2 gate1 source.
XQ1  drain g2 g1 src BF961
* R3: source resistor for the self bias. The drain current makes a voltage
* across R3 (about 0.5 V here); that voltage is the negative gate-1 bias.
* If a part draws more current, VG1S becomes more negative and pulls the
* current back down, so R3 also stabilises the bias against part spread.
R3   src 0 220
* C2 and C3 short R3 for signals, so R3 only acts at DC:
*   C2 (47 uF electrolytic, + to src) for the IF band down to ~100 Hz.
*   Without it, R3 would give negative feedback at the IF and cut the gain.
C2   src 0 47u
*   C3 (100 nF ceramic) for the RF / LO frequencies, where the big
*   electrolytic is inductive and no longer a good short.
C3   src 0 100n
* R4: drain load resistor. The mixed signal is a current; R4 turns it into a
* voltage at the drain. With ~2.35 mA average current the drain sits at
* about 6.6 V, leaving enough VDS for the BF961 to stay in saturation.
R4   vdd drain 1k
C4   vdd 0 100u             ; 9 V rail decoupling (does nothing in simulation,
*                              because V9 is ideal, but is needed on the board)

* ---------------- single-pole RC low-pass ----------------
* The drain carries the wanted IF (kHz) plus the LO and RF (MHz) and their
* sums and harmonics. R5/C5 is a first-order low-pass that keeps the IF and
* attenuates the rest. The corner frequency, including R4 in series with R5
* and the load of the R6/R7 divider, is
*   fc = 1 / (2*pi * ((R4+R5) || (R6||R7)) * C5)
*      = 1 / (2*pi * (11k || 50k) * 3.3n) = about 5.3 kHz
* chosen below the ADC's 12.5 kHz Nyquist frequency (25 kS/s sample rate).
R5   drain filt 10k
C5   filt  0 3.3n

* ---------------- level shift to ADC mid-scale + protection ----------------
* C6 blocks the ~6.6 V DC of the drain, so only the IF signal passes. This
* also means the 9 V side can never put DC on the ADC pin.
C6   filt adc_b 1u
* R6/R7: equal divider from 3.3 V, sets the DC level at the ADC to
* 3.3 V / 2 = 1.65 V, the middle of the ADC range, so the IF can swing
* +/-1.65 V without clipping. C6 with R6||R7 (50k) is a high-pass at
* 1/(2*pi*50k*1u) = 3.2 Hz: low enough to pass all useful IF frequencies.
R6   v33  adc_b 100k
R7   adc_b 0    100k
* R8: series resistor into the ADC pin. If the clamp diodes conduct (power-up
* spike, overload), R8 limits the current that can flow.
R8   adc_b adc 1k
* D1/D2 = BAT54S (two Schottky diodes in one SOT-23 package) clamp the pin:
*   D1 conducts when V(adc) > 3.3 V + ~0.3 V and dumps the excess into 3V3,
*   D2 conducts when V(adc) < 0 V - ~0.3 V and pulls the pin up from GND.
* Schottky diodes switch on at a lower voltage than the RP2040's own ESD
* diodes, so they take the current instead of the chip.
D1   adc v33 BAT54          ; anode = adc, cathode = 3V3
D2   0   adc BAT54          ; anode = GND, cathode = adc
CADC adc 0 10p              ; RP2040 ADC input + pin + trace capacitance
* Approximate Schottky model for the BAT54 (saturation current, ideality,
* series resistance, 30 V breakdown, 10 pF junction capacitance, 5 ns
* transit time). Good enough for clamp behaviour.
.model BAT54 D (IS=2e-7 N=1.04 RS=2.5 BV=30 CJO=10p TT=5n)
