* =============================================================================
* BF961 mixer + LM358 4th-order Chebyshev low-pass for the RP2040 webSDR
* =============================================================================
*
* This is the third version of the mixer. The BF961 part (LO drive, RF
* input, self bias, drain load) is IDENTICAL to ../../mixer/ and
* ../../mixer_sallenkey/ and keeps the same part numbers. Only the filter
* between the drain and the ADC pin is different:
*
*   ../../mixer/            1-pole RC,                 -3 dB at 5.7 kHz
*   ../../mixer_sallenkey/  2-pole Butterworth (U1A),  -3 dB at 5.7 kHz
*   this version            4-pole Chebyshev, 0.5 dB ripple, BOTH halves of
*                           the LM358 (U1A + U1B), flat to 8 kHz, -3 dB at 8.8 kHz
*
* Why: the RP2040 ADC samples at 25 kS/s. A signal at f (between 12.5 and
* 25 kHz) shows up at 25 kHz - f inside the IF band ("alias"). If the IF is
* used up to 8 kHz, the aliases that land in it come from 17 ... 25 kHz.
* A 4th-order filter falls 24 dB per octave; the Chebyshev type trades a
* small 0.5 dB ripple in the passband for an even steeper knee. Result:
* a WIDER usable IF (8 kHz instead of ~5 kHz) AND much less aliasing
* (-33 dB at 17 kHz instead of -20 dB for the Sallen-Key version).
* The LM358 has two op-amps in one package; the Sallen-Key version left the
* second one unused, here it builds the second filter stage for free.
*
* This file holds only the circuit. It is pulled in with ".include" by the
* top-level decks (mixer_sk.cir, and the decks that run.py writes).
* The including deck must define these parameters BEFORE the .include:
*   flo   LO frequency in Hz       (the PIO square wave on GP8)
*   frf   RF test tone in Hz       (the "antenna" signal)
*   arf   RF test tone amplitude   (V peak, at the generator)
*
* Node names (used in .ic, save, plot and in the schematic):
*   vdd     +9 V supply (mixer and LM358)   v33    Pico 3V3(OUT), 3.3 V
*   lo_pin  GP8 output                      g2     gate 2 of Q1
*   rf_src  ideal RF generator              rf     antenna terminal (after 50 ohm)
*   g1      gate 1 of Q1                    src    source of Q1
*   drain   drain of Q1
*   s1_in   IF after C6, biased at 1.65 V (input of stage 1)
*   s1_mid  junction R9 / R10 / C7          s1_p   + input of U1A (pin 3)
*   s1_out  output of stage 1 (U1A pin 1)
*   s2_mid  junction R12 / R13 / C10        s2_p   + input of U1B (pin 5)
*   s2_out  output of stage 2 (U1B pin 7)   adc    the GP26 / ADC0 pin itself
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

* The BF961 subcircuit is shared with the simple version (same folder level),
* and the LM358 model lives in this version's models/ folder. Both paths are
* relative to THIS file (mixer_lpf4/sim/).
.include ../../mixer/models/bf961.lib
.include ../models/lm358.lib

* ---------------- supplies ----------------
* Ideal sources: no ripple and no internal resistance.
V9   vdd  0 DC 9           ; +9 V supply for the mixer AND the LM358
V33  v33  0 DC 3.3         ; Pico 3V3(OUT) pin, feeds the ADC bias and clamp

* ---------------- LO: RP2040 GP8 PIO square wave ----------------
* GP8 toggles between 0 V and 3.3 V. PULSE(V1 V2 delay rise fall width period):
* low 0 V, high 3.3 V, 2 ns edges, 50 % duty cycle.
VLO  lo_pin 0 PULSE(0 3.3 0 2n 2n {0.5/flo-2n} {1/flo})
* R1: series resistor GP8 -> gate 2, damps ringing on the fast edges.
R1   lo_pin g2 100

* ---------------- RF input (no input filter in this version) ----------------
VRF  rf_src 0 SIN(0 {arf} {frf})
RSRC rf_src rf 50           ; 50 ohm source resistance (antenna / generator)
C1   rf   g1 10n            ; RF coupling cap: passes RF, blocks DC
R2   g1   0  100k           ; gate-1 DC path to ground (gate 1 at 0 V DC)

* ---------------- mixer (same as the simple version) ----------------
* Q1 = BF961, pin order: drain gate2 gate1 source.
XQ1  drain g2 g1 src BF961
* R3 = source self-bias resistor (about 0.5 V across it: VG1S = -0.5 V).
* C2 / C3 short it for IF and RF so it only acts at DC.
R3   src 0 220
C2   src 0 47u              ; IF bypass (electrolytic, + to src)
C3   src 0 100n             ; RF bypass (ceramic)
* R4: drain load. The mixed signal is a current; R4 turns it into a voltage.
R4   vdd drain 1k
C4   vdd 0 100u             ; 9 V rail decoupling (needed on the board)

* ---------------- LO roofing capacitor ----------------
* The drain carries the IF (kHz) AND big LO / RF products (MHz). The LM358
* (0.7 MHz gain-bandwidth, 0.3 V/us) cannot filter MHz signals itself: there
* it has no loop gain left, so a Sallen-Key stage leaks them through, and
* they can be rectified into a DC shift. C5 across the drain removes them
* first:  fc = 1 / (2*pi * R4 * C5) = 1 / (2*pi * 1k * 2.2n) = 72 kHz
* (the Sallen-Key version used 4.7 nF / 34 kHz; with the higher 8 kHz
* corner here, 2.2 nF keeps the roofing pole further away from the
* passband). The 7.1 MHz LO is still about 40 dB down before the op-amps,
* and the two filter stages take it down by another ~100 dB.
C5   drain 0 2.2n

* ---------------- DC block + bias to 1.65 V ----------------
* C6 blocks the ~6.6 V drain DC. R6/R7 (equal, from 3.3 V) bias the filter
* input at 1.65 V = middle of the ADC range. Both op-amps are unity-gain
* followers for DC, so stage 1, stage 2 and the ADC pin all sit at 1.65 V.
* High-pass corner: 1/(2*pi * 50k * 1u) = 3.2 Hz.
C6   drain s1_in 1u
R6   v33  s1_in 100k
R7   s1_in 0    100k

* ---------------- 4th-order Chebyshev low-pass, two Sallen-Key stages ---------
* A 4th-order filter is built as two 2nd-order stages in a row. Each stage
* is the same unity-gain Sallen-Key circuit as in the Sallen-Key version:
*
*   in --Ra--+--Rb--+---- (+) op-amp ---+---- out
*            |      |      (-) <--------+
*            Ca     Cb                  |
*            |      |                   |
*           out    GND        (Ca goes back to the stage output)
*
*   f0 = 1 / (2*pi * sqrt(Ra*Rb*Ca*Cb))
*   Q  = sqrt(Ra*Rb*Ca*Cb) / (Cb * (Ra + Rb))
* Q can only reach sqrt(Ca/Cb)/2 (with Ra = Rb), so a high-Q stage needs a
* large capacitor ratio Ca/Cb.
*
* A 4th-order Chebyshev with 0.5 dB ripple and an 8 kHz passband edge is
* the product of two such stages (textbook pole table, e.g. Williams &
* Taylor, "Electronic Filter Design Handbook", or TI SLOA049):
*   stage 1:  f0 = 4.78 kHz, Q = 0.71   (gentle: shapes the low passband)
*   stage 2:  f0 = 8.25 kHz, Q = 2.94   (peaky: lifts the response near
*                                         8 kHz, then gives the steep knee)
* Together (ngspice, with the real part values and the LM358 model):
* within about 0.8 dB from 100 Hz to 8 kHz, -3 dB at 8.8 kHz, then 24 dB
* per octave: -20 dB at 12.5 kHz, -33 dB at 17 kHz, -48 dB at 25 kHz.
* The gentle stage comes first: it also absorbs the 1 kohm drain resistance
* R4 (which is in series with R9), and it keeps the big drain signals away
* from the peaky stage.
* The part values below were first computed with the formulas and rounded
* to E24 / E12 values, then fine-tuned in ngspice (with R4, C5, the bias
* divider and the LM358 model in place) to match the ideal Chebyshev
* response within 0.1 dB rms.
*
* Stage 1 (U1A): Ra = R9 + R4 = 6.8k + 1k, Rb = R10 = 4.3k, Ca = C7 = 8.2n,
*                Cb = C8 = 3.9n  ->  f0 = 4.9 kHz, Q = 0.69
R9   s1_in  s1_mid 6.8k
R10  s1_mid s1_p   4.3k
C7   s1_mid s1_out 8.2n
C8   s1_p   0      3.9n
* U1A = first half of the LM358 (pins 3 = IN+, 2 = IN-, 1 = OUT, 8 = V+, 4 = GND)
XU1A s1_p s1_out vdd 0 s1_out LM358
* R14: class-A pull-down for the U1A output (see R11 below)
R14  s1_out 0 10k
*
* Stage 2 (U1B): Ra = R12 = 6.8k, Rb = R13 = 2.7k, Ca = C10 = 27n,
*                Cb = C11 = 680p  ->  f0 = 8.7 kHz, Q = 2.84 on paper; the
*                LM358's limited bandwidth and the C5 roofing pole shift
*                the real response a little, which the ngspice tuning
*                took into account
* (Ca/Cb = 40, so Q could reach 3.15 with equal resistors; the 6.8k/2.7k
* split brings it down to the wanted value.)
R12  s1_out s2_mid 6.8k
R13  s2_mid s2_p   2.7k
C10  s2_mid s2_out 27n
C11  s2_p   0      680p
* U1B = second half of the LM358 (pins 5 = IN+, 6 = IN-, 7 = OUT)
XU1B s2_p s2_out vdd 0 s2_out LM358
* R11: pull-down at the final output. The LM358's output stage is class B:
* with no load it causes small "crossover" distortion. A steady 165 uA
* (1.65 V / 10k) keeps the pull-up transistor always on. (The model does
* not show crossover distortion; R11 and R14 are for the real board.)
R11  s2_out 0 10k
C9   vdd 0 100n             ; LM358 supply decoupling, right at pin 8
*
* Use 1 % metal-film resistors and C0G/NP0 or film capacitors, at least for
* stage 2: its high Q makes it sensitive to the C10/C11 ratio (see the
* tolerance check in run.py and the README).

* ---------------- ADC protection (same as the simple version) ----------------
* The LM358 output can swing up to 7.5 V when it is overloaded or while
* powering up, which would damage the RP2040 (max 3.3 V + 0.5 V). R8 limits
* the current and the BAT54S Schottky pair clamps the pin:
*   D1 conducts when V(adc) > 3.3 V + ~0.3 V and dumps the excess into 3V3,
*   D2 conducts when V(adc) < 0 V - ~0.3 V and pulls the pin up from GND.
* Worst case: (7.5 V - 3.6 V) / 1k = 3.9 mA into the 3V3 rail, which the
* Pico's own consumption (>20 mA) absorbs easily.
R8   s2_out adc 1k
D1   adc v33 BAT54          ; anode = adc, cathode = 3V3
D2   0   adc BAT54          ; anode = GND, cathode = adc
CADC adc 0 10p              ; RP2040 ADC input + pin + trace capacitance
* Approximate Schottky model for the BAT54.
.model BAT54 D (IS=2e-7 N=1.04 RS=2.5 BV=30 CJO=10p TT=5n)
