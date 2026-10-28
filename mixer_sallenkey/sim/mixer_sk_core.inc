* =============================================================================
* BF961 mixer + LM358 Sallen-Key low-pass for the RP2040 webSDR - circuit only
* =============================================================================
*
* This is the "Sallen-Key" version of the simple mixer in ../../mixer/. The
* BF961 part (LO drive, RF input, self bias, drain load) is IDENTICAL to the
* simple version and keeps the same part numbers. Only the part between the
* drain and the ADC pin is new:
*
*   simple version:   drain -> R5/C5 (1-pole RC, 5.3 kHz) -> C6 -> bias -> R8 -> GP26
*   this version:     drain -> C5 (LO roofing) -> C6 -> bias -> 2-pole
*                     Sallen-Key low-pass with half of an LM358 -> R8 -> GP26
*
* Why a Sallen-Key filter? The RP2040 ADC samples at 25 kS/s, so everything
* above 12.5 kHz folds back ("aliases") into the 0..12.5 kHz IF band. A
* 1-pole RC falls only 6 dB per octave; the 2-pole Sallen-Key falls 12 dB
* per octave and has a flat top (Butterworth), so it keeps the wanted IF
* and attenuates the aliasing band much more. The op-amp also gives a low
* impedance output to drive the ADC.
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
*   sk_in   IF after C6, biased at 1.65 V (input of the Sallen-Key filter)
*   sk_mid  junction of R9 / R10 / C7       sk_p   + input of the op-amp (R10 / C8)
*   sk_out  LM358 output (pin 1)            adc    the GP26 / ADC0 pin itself
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
* relative to THIS file (mixer_sallenkey/sim/).
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
* The drain carries the IF (kHz) AND big LO / RF products (MHz). An LM358 is
* a slow op-amp (0.7 MHz gain-bandwidth, 0.3 V/us slew rate): MHz signals at
* its input are not filtered properly by the op-amp (the Sallen-Key filter
* "leaks" at frequencies where the op-amp has no gain left) and can even be
* rectified into a DC shift. So the MHz content is removed BEFORE the op-amp
* with one capacitor across the drain load:
*   fc = 1 / (2*pi * R4 * C5) = 1 / (2*pi * 1k * 4.7n) = 34 kHz
* That is far above the IF (only -0.03 dB at 6 kHz) but takes the 7 MHz LO
* down by about 46 dB. This replaces R5 of the simple version: there is no
* series resistor any more, so the IF reaches the filter without the R5 loss.
C5   drain 0 4.7n

* ---------------- DC block + bias to 1.65 V ----------------
* C6 blocks the ~6.6 V drain DC. R6/R7 (equal, from 3.3 V) bias the filter
* input at 1.65 V = middle of the ADC range. The op-amp is a unity-gain
* follower for DC, so its output - and the ADC pin - also sits at 1.65 V.
* High-pass corner: 1/(2*pi * 50k * 1u) = 3.2 Hz.
C6   drain sk_in 1u
R6   v33  sk_in 100k
R7   sk_in 0    100k

* ---------------- Sallen-Key low-pass, unity gain, Butterworth ----------------
* Classic unity-gain Sallen-Key (the op-amp is a voltage follower):
*
*   sk_in --R9--+--R10--+---- (+) LM358 ----+---- sk_out
*               |       |      (-) <--------+
*               C7      C8                  |
*               |       |                   |
*            sk_out    GND        (C7 goes back to the output)
*
* For a unity-gain Sallen-Key with resistors Ra, Rb and capacitors
* C7 (to the output) and C8 (to ground):
*   f0 = 1 / (2*pi * sqrt(Ra*Rb*C7*C8))
*   Q  = sqrt(Ra*Rb*C7*C8) / (C8 * (Ra + Rb))
* Here Ra is R9 PLUS the source resistance that drives sk_in: the drain
* load R4 = 1k (through C6, a short at the IF). R6||R7 = 50k also loads the
* node a little. With Ra = 10k + 1k = 11k, Rb = 10k, C7 = 3.9n, C8 = 1.8n:
*   sqrt(11k * 10k * 3.9n * 1.8n) = 27.8 us
*   f0 = 1 / (2*pi * 27.8 us)     = 5.7 kHz
*   Q  = 27.8 us / (1.8n * 21k)   = 0.74  (Butterworth = 0.707: flattest top)
* The -3 dB point of a Butterworth is f0, so the IF is flat to ~5 kHz and
* the filter falls 12 dB per octave above that: about -13 dB at the ADC's
* 12.5 kHz Nyquist frequency and -25 dB at 25 kHz (RC version: -7 / -13 dB).
* Use 1 % metal-film resistors and C0G/NP0 or film capacitors (X7R ceramics
* change their value with voltage and would shift the filter).
R9   sk_in  sk_mid 10k
R10  sk_mid sk_p   10k
C7   sk_mid sk_out 3.9n
C8   sk_p   0      1.8n
* U1A = one half of the LM358 (pins 3 = IN+, 2 = IN-, 8 = V+, 4 = GND, 1 = OUT).
* IN- is wired straight to OUT: a voltage follower (gain exactly 1).
* The LM358 runs from 9 V: its inputs work from 0 V to 9 - 1.5 = 7.5 V and
* its output from about 0 V to 7.5 V, so 1.65 V sits comfortably inside.
XU1A sk_p sk_out vdd 0 sk_out LM358
* R11: pull-down at the op-amp output. The LM358's output stage is class B:
* with no load the output crosses between its pull-up and pull-down
* transistors, which causes small "crossover" distortion. A steady 165 uA
* through R11 (1.65 V / 10k) keeps the pull-up transistor always on.
* (The model does not show crossover distortion; the resistor is for the
* real board.)
R11  sk_out 0 10k
C9   vdd 0 100n             ; LM358 supply decoupling, right at pin 8
* The second half of the LM358 (U1B, pins 5/6/7) is not used. It is tied off
* as a follower with its input at GND (pin 5 to GND, pin 6 to pin 7), so it
* cannot oscillate. Not simulated (it does nothing), but shown here:
* XU1B 0 u1b_out vdd 0 u1b_out LM358

* ---------------- ADC protection (same as the simple version) ----------------
* The LM358 output can swing up to 7.5 V when it is overloaded or while
* powering up, which would damage the RP2040 (max 3.3 V + 0.5 V). R8 limits
* the current and the BAT54S Schottky pair clamps the pin:
*   D1 conducts when V(adc) > 3.3 V + ~0.3 V and dumps the excess into 3V3,
*   D2 conducts when V(adc) < 0 V - ~0.3 V and pulls the pin up from GND.
* Worst case: (7.5 V - 3.6 V) / 1k = 3.9 mA into the 3V3 rail, which the
* Pico's own consumption (>20 mA) absorbs easily.
R8   sk_out adc 1k
D1   adc v33 BAT54          ; anode = adc, cathode = 3V3
D2   0   adc BAT54          ; anode = GND, cathode = adc
CADC adc 0 10p              ; RP2040 ADC input + pin + trace capacitance
* Approximate Schottky model for the BAT54.
.model BAT54 D (IS=2e-7 N=1.04 RS=2.5 BV=30 CJO=10p TT=5n)
