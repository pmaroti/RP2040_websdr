* =============================================================================
* 20 m amateur band bandpass filter (14.000 ... 14.350 MHz), 50 ohm in / out
* =============================================================================
*
* Same circuit as the 40 m filter (see bpf_40m.inc for how it works), with
* values for 14.175 MHz:
*
*   in --CE1--+---CC---+--CE2-- out
*             |        |
*           L1 || CS1  L2 || CS2
*             |        |
*            GND      GND
*
* Design (2-pole Butterworth: q1 = q2 = 1.414, k12 = 0.707)
*   f0 = 14.175 MHz, 3 dB bandwidth 1.0 MHz -> QL = f0/BW = 14.2
*   L = 1.0 uH -> XL = 89.1 ohm, total tank C = 126 pF
*   tank end resistance  Rend = q * QL * XL = 1785 ohm
*   end coupling cap     XCE = 50 * sqrt(Rend/50 - 1) = 294 ohm -> 38 pF
*   coupling cap         CC = Ctank * k12 / QL = 6.3 pF
*   shunt cap            CS = 126 - 37 (CE seen from the tank) - 6.3 = 83 pF
*   Rounded to E12 values: CE = 39 pF, CC = 6.8 pF, CS = 82 pF.
*   The bandwidth is a little wider than for 40 m (1 MHz for a 350 kHz band)
*   so that part tolerances do not push a band edge outside the passband.
*
* Inductors: 1.0 uH = 16 turns on a T50-6 (yellow) iron-powder toroid
*   (AL = 4.0 nH/turn^2; 16^2 * 4.0 nH = 1.02 uH). T50-6 has a better Q than
*   T50-2 above 10 MHz. Loss modelled as R = XL / QU, QU = 150 by default.
*
* Every part value is a parameter (default = the nominal value), so run.py
* can vary each part separately for the tolerance check. Normal use:
*        X1 in out BPF20M            (default QU = 150)
* =============================================================================
.subckt BPF20M in out qu=150 ce1=39p ce2=39p cc=6.8p
+ cs1=82p cs2=82p l1=1.0u l2=1.0u
* coil loss resistors, R = 2*pi*f0*L / QU (QU = unloaded Q of the coil)
.param f0=14.175meg rl1={2*3.14159265*f0*l1/qu} rl2={2*3.14159265*f0*l2/qu}
CE1 in  n1  {ce1}         ; input matching (50 ohm -> tank)
L1  n1  l1b {l1}          ; tank 1 inductor ...
RL1 l1b 0   {rl1}         ; ... and its loss resistance
CS1 n1  0   {cs1}         ; tank 1 capacitor
CC  n1  n2  {cc}          ; coupling between the two tanks
L2  n2  l2b {l2}          ; tank 2 inductor ...
RL2 l2b 0   {rl2}         ; ... and its loss resistance
CS2 n2  0   {cs2}         ; tank 2 capacitor
CE2 n2  out {ce2}         ; output matching (tank -> 50 ohm)
.ends BPF20M
