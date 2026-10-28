* =============================================================================
* BF961 RF preamplifier with manual gain control (optional front-end stage)
* =============================================================================
*
* A broadband (about 2 ... 30 MHz) common-source amplifier. The RF goes to
* gate 1; gate 2 is a DC control voltage from a potentiometer that sets the
* gain from about -60 dB (off) to about +20 dB. That is the classic use of a
* dual-gate MOSFET: gate 2 changes the transconductance, the same way an AGC
* voltage would.
*
* Signal path:
*   in (50 ohm) -> RT termination -> C1 -> gate 1
*   drain -> L1 || R4 load (to +9 V) -> C4 -> out (to the mixer's RF in);
*   R9 ties out to ground for DC
*
* Gain control path:
*   +9 V -> R5 -> potentiometer RV1 -> GND;  wiper -> R6 -> gate 2,
*   C5 grounds gate 2 for RF (gate 2 must be an RF ground for the device to
*   work as a cascode, otherwise the drain signal leaks back through it).
*
* Why the source sits at a fixed ~1.2 ... 2.6 V
*   A BF961 needs gate 2 about 1 V BELOW the source to switch off. There is
*   no negative supply, so instead the source is lifted: the bleeder R3 from
*   +9 V and R2 to ground hold the source at 1.17 V even with no drain
*   current. With the pot at 0 V, VG2S = -1.2 V and the amplifier is off.
*   Gate 1 is biased to +2.2 V by R7/R8, a little below the source voltage
*   at full current (2.6 V), which gives the usual slightly negative VG1S.
*
* Node names:
*   in    RF input (50 ohm)           g1    gate 1
*   g2    gate 2                      s     source
*   d     drain                       out   RF output (DC-free)
*   wip   potentiometer wiper         vdd   +9 V (passed in as a pin)
*
* Parameters:
*   pos   potentiometer position, 0 = fully counter-clockwise (wiper at
*         GND, minimum gain) ... 1 = fully clockwise (maximum gain)
*   rpot  potentiometer value (10k, 50k or 100k; 50k is the default)
*   rtop  resistor from +9 V to the top of the pot, sets the highest gate 2
*         voltage to 9 V * rpot / (rpot + rtop) = 6.9 V with 50k / 15k
*         (use 3.3k for a 10k pot, 33k for a 100k pot)
*
* Use:  X1 in out vdd PREAMP pos=0.8
* Needs the BF961 model:  .include <path>/mixer/models/bf961.lib
* =============================================================================
.subckt PREAMP in out vdd pos=1 rpot=50k rtop=15k
* --- input: 50 ohm termination, DC block, gate-1 bias ---
RT  in  0   51            ; terminates the antenna / bandpass filter in 50 ohm
C1  in  g1  10n           ; DC block: keeps the 2.2 V gate bias off the input
R7  vdd g1  100k          ; gate 1 bias divider: 9 V * 33k / 133k = 2.2 V
R8  g1  0   33k
* --- the transistor ---
XQ1 d g2 g1 s BF961       ; pins: drain gate2 gate1 source
* --- source: fixed-ish voltage for the gate-2 cut-off, RF bypassed ---
R2  s   0   330           ; source resistor to ground
R3  vdd s   2.2k          ; bleeder from +9 V: holds the source up at zero current
C2  s   0   100n          ; RF bypass: the source is at RF ground
* --- drain load: choke for DC, resistor sets the RF gain ---
L1  vdd d   47u           ; RF choke: full 9 V on the drain, no DC drop
R4  vdd d   1k            ; RF load: gain is about gm * 1k (~20 dB); also
*                           damps the choke so the response stays flat
C4  d   out 10n           ; output DC block (the drain sits at +9 V)
C6  vdd 0   100n          ; 9 V decoupling right at the preamp (no effect in
*                           simulation, where the 9 V source is ideal)
R9  out 0   100k          ; gives the output a DC path to ground. The mixer's own
*                           input also starts with a capacitor (its C1), so
*                           without R9 the wire between the two caps would
*                           float and pick up any DC charge.
* --- gain control: pot divider into gate 2 ---
* RV1 is modelled as two resistors: top part rpot*(1-pos), bottom rpot*pos.
* The +1 ohm stops either part from becoming exactly zero at the ends.
R5   vdd ptop {rtop}
RV1a ptop wip {rpot*(1-pos)+1}
RV1b wip  0   {rpot*pos+1}
R6  wip g2  10k           ; isolates the pot wiring from the RF at gate 2
C5  g2  0   10n           ; RF ground for gate 2 (with R6: 1.6 kHz low-pass)
.ends PREAMP
