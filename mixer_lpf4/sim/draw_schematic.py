#!/usr/bin/env python3
"""Draw the BF961 mixer + LM358 4th-order Chebyshev schematic, with its
RP2040 connections, to mixer_lpf4/schematic.png.

Usage:  python3 mixer_lpf4/sim/draw_schematic.py   (needs: pip3 install schemdraw)
Part values and node names match sim/mixer_lpf4_core.inc.

The left half (Pico, Q1 and its bias parts) is drawn exactly like the simple
version in ../../mixer/sim/draw_schematic.py; the right half (from the drain
to GP26) is the two-stage filter. Both stages are drawn by one function,
sallen_key(), because they are the same circuit with different values.

How schemdraw works, in short:
  - A Drawing is a canvas. "d += element" places an element on it.
  - Two-terminal parts (Resistor, Capacitor, Line, Schottky ...) are placed
    with a direction and length, e.g. .right(L) or .down(L), starting either
    where the previous element ended or at an explicit point given with .at().
  - Every placed element has anchors (named points such as .start, .end,
    .center, or pin names), used to connect the next part to it.
  - Coordinates are in drawing units; 1 unit is about 0.5 inch at 100 % scale.
"""
import os
import schemdraw
from PIL import Image                       # Pillow, used to flatten the PNG background
import schemdraw.elements as elm
from schemdraw.segments import Segment, SegmentCircle

HERE = os.path.dirname(os.path.abspath(__file__))       # mixer_lpf4/sim/
OUT = os.path.join(HERE, "..", "schematic.png")         # mixer_lpf4/schematic.png
L = 2.5   # length of a two-terminal part
GREY = "#52514e"


class DualGateNMOS(elm.Element):
    """N-channel depletion dual-gate MOSFET (BF961). Anchors: drain, gate1, gate2, source.

    schemdraw has no dual-gate MOSFET symbol, so this class draws one from
    line segments. All coordinates below are in "symbol units" around the
    centre of the channel (0, 0) and are multiplied by the scale s, so the
    symbol can be made bigger or smaller with one number.

        drain (0.7, 1.6)
              |
          +---+          <- drain contact at y = 0.85
     G2 --|   |
          |<--+          <- substrate arrow at y = 0, joined to the source
     G1 --|   |
          +---+          <- source contact at y = -0.85
              |
        source (0.7, -1.6)
    """

    def __init__(self, s=1.5, **kwargs):
        super().__init__(**kwargs)
        def P(*pts):
            # scale a list of (x, y) points by s
            return [(x * s, y * s) for x, y in pts]
        # channel: solid line = depletion mode
        # (an enhancement MOSFET would be drawn with a broken line)
        self.segments.append(Segment(P((0, -1.0), (0, 1.0)), lw=2.5))
        # gate 2 (upper) and gate 1 (lower)
        # each gate = a vertical plate at x = -0.25 (insulated from the
        # channel by the gap) plus a horizontal lead out to x = -1.0
        self.segments.append(Segment(P((-0.25, 0.15), (-0.25, 0.9))))
        self.segments.append(Segment(P((-0.25, 0.55), (-1.0, 0.55))))
        self.segments.append(Segment(P((-0.25, -0.9), (-0.25, -0.15))))
        self.segments.append(Segment(P((-0.25, -0.55), (-1.0, -0.55))))
        # drain and source
        # each: a short stub off the channel, then a lead up (drain) or down (source)
        self.segments.append(Segment(P((0, 0.85), (0.7, 0.85), (0.7, 1.6))))
        self.segments.append(Segment(P((0, -0.85), (0.7, -0.85), (0.7, -1.6))))
        # substrate arrow into the channel (N-channel), tied to source
        # (the arrow points INTO the channel for N-channel, out of it for P-channel)
        self.segments.append(Segment(P((0.7, 0.0), (0.05, 0.0)), arrow="->",
                                     arrowwidth=0.25, arrowlength=0.3))
        self.segments.append(Segment(P((0.7, 0.0), (0.7, -0.85))))
        # the circle around a discrete transistor (the package outline)
        self.segments.append(SegmentCircle((0.2 * s, 0.0), 1.15 * s, lw=1.2))
        # named connection points, used as q.drain, q.gate1 ... after placing
        self.anchors["center"] = (0, 0)
        self.anchors["drain"] = (0.7 * s, 1.6 * s)
        self.anchors["source"] = (0.7 * s, -1.6 * s)
        self.anchors["gate2"] = (-1.0 * s, 0.55 * s)
        self.anchors["gate1"] = (-1.0 * s, -0.55 * s)
        # where the small "G2" / "G1" text goes, just above each gate lead
        self.anchors["G2lbl"] = (-0.9 * s, 0.75 * s)
        self.anchors["G1lbl"] = (-0.9 * s, -0.35 * s)


def wire(d, *pts):
    """Draw straight wires through a list of points: wire(d, a, b, c) = a-b and b-c."""
    for a, b in zip(pts, pts[1:]):
        d += elm.Line().at(a).to(b)


def sallen_key(d, start, ra, rb, ca, cb, unit, pins, names):
    """Draw one unity-gain Sallen-Key stage starting at point start.

    ra, rb, ca, cb: (reference, value) label pairs
    unit:  op-amp label, e.g. "U1A  ½ LM358"
    pins:  LM358 pin numbers (in+, in-, out)
    names: netlist node names (mid, p, out) for the small grey labels
    Returns the output dot (to continue drawing from).

    Layout:   start --Ra--+--Rb--+--(+)op-amp--+-- out
                          |      |    (-)<-----+
                          Ca     Cb            |
                          (up,   |             |
                          to out) GND
    """
    d += elm.Resistor().right(L).at(start).label(f"{ra[0]}\n{ra[1]}", loc="bottom")
    d += (mn := elm.Dot())
    d += elm.Resistor().right(L).label(f"{rb[0]}\n{rb[1]}", loc="bottom")
    d += (pn := elm.Dot())
    d += elm.Capacitor().down(L).at(pn.center).label(f"{cb[0]}\n{cb[1]}", loc="bottom")
    d += elm.Ground()
    # op-amp with its + input level with the Rb/Cb node.
    # schemdraw's Opamp: in1 = "-" (top), in2 = "+" (bottom), out on the right
    op = elm.Opamp(leads=True).anchor("in2").at((pn.center[0] + 1.0, pn.center[1])).right()
    d += op
    d += elm.Line().at(pn.center).to(op.in2)
    d += elm.Label().at((op.vs[0] + 0.3, op.vs[1] - 0.9)).label(unit, loc="right", fontsize=10)
    d += elm.Label().at((op.in2[0] + 0.05, op.in2[1] - 0.05)).label(pins[0], loc="right", fontsize=8, color=GREY, valign="top")
    d += elm.Label().at((op.in1[0] + 0.05, op.in1[1] + 0.05)).label(pins[1], loc="right", fontsize=8, color=GREY, valign="bottom")
    d += elm.Label().at((op.out[0] - 0.05, op.out[1] + 0.05)).label(pins[2], loc="left", fontsize=8, color=GREY, valign="bottom")
    # supply: both halves share pin 8 (V+) and pin 4 (GND); drawn on each
    d += elm.Line().up(0.9).at(op.vd)
    d += elm.Vdd().label("+9 V")
    d += elm.Label().at((op.vd[0] - 0.2, op.vd[1] + 0.45)).label("8", loc="left", fontsize=8, color=GREY)
    d += elm.Line().down(0.9).at(op.vs)
    d += elm.Ground()
    d += elm.Label().at((op.vs[0] - 0.2, op.vs[1] - 0.45)).label("4", loc="left", fontsize=8, color=GREY)
    # output node, follower feedback (out -> -) over the top
    d += elm.Line().right(0.8).at(op.out)
    d += (on := elm.Dot())
    fb_y = op.in1[1] + 1.0
    wire(d, op.in1, (op.in1[0] - 0.5, op.in1[1]), (op.in1[0] - 0.5, fb_y))
    wire(d, (op.in1[0] - 0.5, fb_y), (on.center[0], fb_y), on.center)
    d += elm.Dot().at((on.center[0], fb_y))
    # Ca from the Ra/Rb node up and over to the output
    cy = fb_y + 2.2
    d += elm.Line().at(mn.center).toy(cy)
    d += elm.Capacitor().right(L).at((mn.center[0], cy)).label(f"{ca[0]}\n{ca[1]}")
    wire(d, (mn.center[0] + L, cy), (on.center[0], cy), (on.center[0], fb_y))
    for node, xy in zip(names, (mn.center, pn.center, on.center)):
        d += elm.Label().at((xy[0] + 0.2, xy[1] + 0.35)).label(node, fontsize=8, color=GREY, halign="left")
    return on


with schemdraw.Drawing(show=False) as d:
    d.config(fontsize=11, unit=L, bgcolor="white", margin=0.5)

    # ---------------- Raspberry Pi Pico / RP2040 ----------------
    # A box with only the four pins this circuit uses. "pos" is the position
    # along the side (0 = bottom, 1 = top); "pin" is the Pico board pin number.
    H = 12.0
    pico = elm.Ic(size=(3.4, H), pins=[
        elm.IcPin(name="GP8", side="right", pos=0.8, pin="11"),        # LO output (PIO)
        elm.IcPin(name="GP26/ADC0", side="right", pos=0.06, pin="31"), # ADC input
        elm.IcPin(name="3V3(OUT)", side="left", pos=0.6, pin="36"),    # 3.3 V for bias/clamp
        elm.IcPin(name="GND", side="left", pos=0.4, pin="38"),
    ], edgepadW=0.6, leadlen=0.8).label("Raspberry Pi\nPico\n(RP2040)", loc="center", fontsize=13)
    d += pico.at((0, 0))
    # 3V3 and GND as net symbols: every "3V3" symbol is the same net, every
    # ground symbol is GND
    d += elm.Line().left(0.8).at(pico.absanchors["3V3(OUT)"])
    d += elm.Vdd().label("3V3")
    d += elm.Line().left(0.8).at(pico.absanchors["GND"])
    d += elm.Ground()
    gp8 = pico.absanchors["GP8"]
    gp26 = pico.absanchors["GP26/ADC0"]

    # ---------------- Q1 BF961, gate 2 level with GP8 ----------------
    QX = 15.0
    q = DualGateNMOS().theta(0)
    d += q.at((QX, gp8[1] - 0.55 * 1.5))
    d += elm.Label().at((q.center[0] + 2.2, q.center[1] + 0.2)).label("Q1\nBF961", loc="right")
    d += elm.Label().at(q.G2lbl).label("G2", fontsize=9, loc="right")
    d += elm.Label().at(q.G1lbl).label("G1", fontsize=9, loc="right")

    # LO: GP8 -> R1 -> gate 2
    d += elm.Line().at(gp8).tox(q.gate2[0] - L - 0.5)
    d += elm.Resistor().right(L).label("R1\n100 Ω")
    d += elm.Line().tox(q.gate2[0])
    d += elm.Label().at((gp8[0] + 0.3, gp8[1] + 0.5)).label(
        "LO: PIO square wave, 0/3.3 V", loc="right", fontsize=10)

    # RF in -> C1 -> gate 1, R2 to ground
    g1y = q.gate1[1]
    d += elm.Dot(open=True).at((q.gate1[0] - 2 * L - 1.0, g1y)).label("RF in", loc="left")
    d += elm.Capacitor().right(L).label("C1\n10 nF", loc="bottom")
    d += (g1n := elm.Dot())
    d += elm.Line().tox(q.gate1[0])
    d += elm.Resistor().down(L).at(g1n.center).label("R2\n100 kΩ", loc="bottom")
    d += elm.Ground()

    # source: R3 || C2 || C3 to ground
    d += elm.Line().down(0.8).at(q.source)
    d += (sn := elm.Dot())
    d += elm.Resistor().down(L).label("R3\n220 Ω", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(2.2).at(sn.center)
    d += (sn2 := elm.Dot())
    d += elm.Capacitor(polar=True).down(L).label("C2\n47 µF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(2.2).at(sn2.center)
    d += elm.Capacitor().down(L).label("C3\n100 nF", loc="bottom")
    d += elm.Ground()

    # drain: R4 to +9 V, C4 decoupling on the rail
    d += elm.Line().up(0.8).at(q.drain)
    d += (dn := elm.Dot())                     # the drain node, the mixer output
    d += elm.Resistor().up(L).label("R4\n1 kΩ", loc="bottom")
    d += (top := elm.Dot())
    d += elm.Vdd().label("+9 V")
    c4x = top.center[0] - 3.0
    wire(d, top.center, (c4x, top.center[1]))
    d += elm.Dot().at((c4x, top.center[1]))
    d += elm.Capacitor(polar=True).down(L).at((c4x, top.center[1])).label("C4\n100 µF", loc="bottom")
    d += elm.Ground()

    # ================= new part: from the drain to GP26 =================
    # C5: LO roofing capacitor straight from the drain to ground
    d += elm.Line().right(3.2).at(dn.center)   # clear of the Q1 label
    d += (c5n := elm.Dot())
    d += elm.Capacitor().down(L).at(c5n.center).label("C5\n2.2 nF", loc="bottom")
    d += elm.Ground()
    # C6 DC block, then the 1.65 V bias divider R6 / R7 at sk_in
    d += elm.Line().right(0.6).at(c5n.center)
    d += elm.Capacitor().right(L).label("C6\n1 µF")
    d += (bn := elm.Dot())                     # node sk_in
    d += elm.Resistor().up(L).at(bn.center).label("R6\n100 kΩ", loc="bottom")
    d += elm.Vdd().label("3V3")
    d += elm.Resistor().down(L).at(bn.center).label("R7\n100 kΩ", loc="bottom")
    d += elm.Ground()

    # stage 1 (U1A): f0 4.9 kHz, Q 0.69
    d += elm.Line().right(0.6).at(bn.center)
    s1 = sallen_key(d, (bn.center[0] + 0.6, bn.center[1]), ("R9", "6.8 kΩ"), ("R10", "4.3 kΩ"),
                    ("C7", "8.2 nF"), ("C8", "3.9 nF"), "U1A\n½ LM358", ("3", "2", "1"),
                    ("s1_mid", "s1_p", "s1_out"))
    # R14 pull-down at the stage-1 output
    d += elm.Resistor().down(L).at(s1.center).label("R14\n10 kΩ", loc="bottom")
    d += elm.Ground()
    # stage 2 (U1B): f0 8.7 kHz, Q 2.84 (the peaky one)
    d += elm.Line().right(0.6).at(s1.center)
    on = sallen_key(d, (s1.center[0] + 0.6, s1.center[1]), ("R12", "6.8 kΩ"), ("R13", "2.7 kΩ"),
                    ("C10", "27 nF"), ("C11", "680 pF"), "U1B\n½ LM358", ("5", "6", "7"),
                    ("s2_mid", "s2_p", "s2_out"))
    # R11 pull-down at the final output (keeps the LM358 output stage in class A)
    d += elm.Resistor().down(L).at(on.center).label("R11\n10 kΩ", loc="bottom")
    d += elm.Ground()

    # series resistor + BAT54S clamp
    d += elm.Line().right(0.6).at(on.center)
    d += elm.Resistor().right(L).label("R8\n1 kΩ")
    d += (an := elm.Dot())                     # the ADC pin node "adc"
    d += elm.Schottky().up(L).at(an.center).label("D1", loc="bottom")
    d += elm.Vdd().label("3V3")
    d += elm.Schottky().up(L).at((an.center[0], an.center[1] - L)).label("D2", loc="bottom")
    d += elm.Ground().at((an.center[0], an.center[1] - L))
    d += elm.Label().at((an.center[0] + 0.5, an.center[1] - L - 0.3)).label(
        "D1+D2 = BAT54S", loc="right", fontsize=10)

    # IF back to GP26: out to the right, down below everything, left to the pin
    ex = an.center[0] + 4.0
    wire(d, an.center, (ex, an.center[1]), (ex, gp26[1]), gp26)
    d += elm.Label().at((gp26[0] + 0.3, gp26[1] + 0.45)).label(
        "IF to ADC: 1.65 V ± signal, always within 0…3.3 V", loc="right", fontsize=10)

    # node names used in the netlist (small grey labels)
    for node, xy in (("drain", dn.center), ("s1_in", bn.center), ("adc", an.center),
                     ("src", sn.center)):
        d += elm.Label().at((xy[0] + 0.15, xy[1] + 0.35)).label(
            node, loc="right", fontsize=8, color=GREY)

    d += elm.Label().at((gp26[0] + 0.3, gp26[1] - 0.9)).label(
        "All grounds common with Pico GND.  +9 V feeds the mixer and the LM358 (U1A and U1B are the two "
        "halves of one LM358; C9 100 nF from pin 8 to GND, close to the chip).",
        loc="right", fontsize=9, color=GREY)
    d += elm.Label().at((gp26[0] + 0.3, gp26[1] - 1.6)).label(
        "4th-order Chebyshev low-pass (0.5 dB design ripple): flat to 8 kHz, -3 dB at 8.8 kHz, -20 dB at 12.5 kHz, "
        "-33 dB at 17 kHz.  Values match sim/mixer_lpf4_core.inc.",
        loc="right", fontsize=9, color=GREY)

    d.save(OUT, dpi=160)

# schemdraw saves PNGs with a transparent background; flatten onto white so
# the black lines stay visible in dark-mode viewers
img = Image.open(OUT).convert("RGBA")
bg = Image.new("RGBA", img.size, "white")
Image.alpha_composite(bg, img).convert("RGB").save(OUT)
print("wrote", os.path.abspath(OUT))
