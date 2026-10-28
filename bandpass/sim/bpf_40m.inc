* =============================================================================
* 40 m amateur band bandpass filter (7.000 ... 7.200 MHz), 50 ohm in / out
* =============================================================================
*
* Two-resonator ("2-pole") top-coupled bandpass, Butterworth response:
*
*   in --CE1--+---CC---+--CE2-- out
*             |        |
*           L1 || CS1  L2 || CS2     (each a parallel LC tank to ground)
*             |        |
*            GND      GND
*
* How it works
*   - L1/CS1 and L2/CS2 are two parallel resonant circuits tuned to 7.1 MHz.
*     At resonance a parallel tank has a very high impedance, so the signal
*     is not shorted to ground; away from resonance the inductor (below) or
*     the capacitor (above) shorts it to ground.
*   - CC couples the two tanks. Its value sets how strongly they interact,
*     and with that the shape (flat top, Butterworth) of the passband.
*   - CE1 and CE2 match the 50 ohm source and load to the high impedance of
*     the tanks (about 1.1 kohm here). Without them the 50 ohm would load
*     the tanks so heavily that they would hardly filter anything.
*   - Two resonators give a 12 dB/octave skirt on each side: the 20 m band
*     and the mixer's 3rd-LO-harmonic response at 21.3 MHz are well down.
*
* Design (2-pole Butterworth: q1 = q2 = 1.414, k12 = 0.707)
*   f0 = 7.1 MHz, 3 dB bandwidth 600 kHz -> loaded Q, QL = f0/BW = 11.8
*   L = 1.5 uH -> XL = 2*pi*f0*L = 66.9 ohm, total tank C = 335 pF
*   tank end resistance  Rend = q * QL * XL = 1120 ohm
*   end coupling cap     XCE = 50 * sqrt(Rend/50 - 1) = 231 ohm -> 97 pF
*   coupling cap         CC = Ctank * k12 / QL = 20 pF
*   shunt cap            CS = 335 - 93 (CE seen from the tank) - 20 = 222 pF
*   Rounded to E12 values: CE = 100 pF, CC = 22 pF, CS = 220 pF.
*   (formulas from the coupled-resonator method in Zverev, "Handbook of
*    Filter Synthesis", and Hayward et al., "Experimental Methods in RF
*    Design" (EMRFD), ch. 3)
*
* Inductors: 1.5 uH = 17 turns on a T50-2 (red) iron-powder toroid
*   (AL = 4.9 nH/turn^2; 17^2 * 4.9 nH = 1.42 uH, adjust by squeezing or
*   spreading the turns while watching the response).
*   Their loss is modelled as a series resistor R = XL / QU with QU = unloaded
*   Q of the coil (about 150 for a T50-2 at 7 MHz); 'qu' is a parameter.
*
* Every part value is a parameter (default = the nominal value), so run.py
* can vary each part separately for the tolerance check. Normal use:
*        X1 in out BPF40M            (default QU = 150)
*        X1 in out BPF40M qu=100     (lossier coils)
* =============================================================================
.subckt BPF40M in out qu=150 ce1=100p ce2=100p cc=22p
+ cs1=220p cs2=220p l1=1.5u l2=1.5u
* coil loss resistors, R = 2*pi*f0*L / QU (QU = unloaded Q of the coil)
.param f0=7.1meg rl1={2*3.14159265*f0*l1/qu} rl2={2*3.14159265*f0*l2/qu}
CE1 in  n1  {ce1}         ; input matching (50 ohm -> tank)
L1  n1  l1b {l1}          ; tank 1 inductor ...
RL1 l1b 0   {rl1}         ; ... and its loss resistance
CS1 n1  0   {cs1}         ; tank 1 capacitor
CC  n1  n2  {cc}          ; coupling between the two tanks
L2  n2  l2b {l2}          ; tank 2 inductor ...
RL2 l2b 0   {rl2}         ; ... and its loss resistance
CS2 n2  0   {cs2}         ; tank 2 capacitor
CE2 n2  out {ce2}         ; output matching (tank -> 50 ohm)
.ends BPF40M
