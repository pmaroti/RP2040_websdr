#!/usr/bin/env python3
"""Draw the RF preamp schematic and the front-end block diagram.

Usage:  python3 rf_preamp/sim/draw_schematic.py      (needs: pip3 install schemdraw)
Writes: rf_preamp/schematic.png   the BF961 preamp with its gain potentiometer
        rf_preamp/chain.png       how antenna, bandpass filter, preamp, mixer
                                  and the Pico connect
Part values and node names match sim/preamp.inc.

schemdraw basics (see also mixer/sim/draw_schematic.py):
  "d += element" places a part; two-terminal parts take a direction and
  length (.up(L), .right(L) ...) and start at the end of the previous part
  or at an explicit point given with .at((x, y)). Placed parts expose named
  anchors (.start, .end, .center, pin names) to connect the next part to.
"""
import os
import schemdraw
import schemdraw.elements as elm
import schemdraw.flow as flow
from PIL import Image                       # used to flatten the PNG onto white
from schemdraw.segments import Segment, SegmentCircle

HERE = os.path.dirname(os.path.abspath(__file__))       # rf_preamp/sim/
OUT = os.path.join(HERE, "..", "schematic.png")
OUT_CHAIN = os.path.join(HERE, "..", "chain.png")
L = 2.5          # default length of a two-terminal part
GREY = "#52514e"  # colour for notes and node names


class DualGateNMOS(elm.Element):
    """N-channel depletion dual-gate MOSFET (BF961). Anchors: drain, gate1, gate2, source.

    Same symbol as in mixer/sim/draw_schematic.py (copied so that each folder
    stands on its own): solid channel line (depletion mode), two gate plates
    (gate 2 upper, gate 1 lower), substrate arrow into the channel (N-channel)
    tied to the source, and a circle for the package. Coordinates are in
    symbol units around the channel centre and scaled by s.
    """

    def __init__(self, s=1.5, **kwargs):
        super().__init__(**kwargs)
        def P(*pts):
            return [(x * s, y * s) for x, y in pts]
        self.segments.append(Segment(P((0, -1.0), (0, 1.0)), lw=2.5))          # channel
        self.segments.append(Segment(P((-0.25, 0.15), (-0.25, 0.9))))           # gate 2 plate
        self.segments.append(Segment(P((-0.25, 0.55), (-1.0, 0.55))))           # gate 2 lead
        self.segments.append(Segment(P((-0.25, -0.9), (-0.25, -0.15))))         # gate 1 plate
        self.segments.append(Segment(P((-0.25, -0.55), (-1.0, -0.55))))         # gate 1 lead
        self.segments.append(Segment(P((0, 0.85), (0.7, 0.85), (0.7, 1.6))))    # drain
        self.segments.append(Segment(P((0, -0.85), (0.7, -0.85), (0.7, -1.6)))) # source
        self.segments.append(Segment(P((0.7, 0.0), (0.05, 0.0)), arrow="->",
                                     arrowwidth=0.25, arrowlength=0.3))         # substrate arrow
        self.segments.append(Segment(P((0.7, 0.0), (0.7, -0.85))))              # ... tied to source
        self.segments.append(SegmentCircle((0.2 * s, 0.0), 1.15 * s, lw=1.2))   # package outline
        self.anchors["center"] = (0, 0)
        self.anchors["drain"] = (0.7 * s, 1.6 * s)
        self.anchors["source"] = (0.7 * s, -1.6 * s)
        self.anchors["gate2"] = (-1.0 * s, 0.55 * s)
        self.anchors["gate1"] = (-1.0 * s, -0.55 * s)
        self.anchors["G2lbl"] = (-0.9 * s, 0.75 * s)
        self.anchors["G1lbl"] = (-0.9 * s, -0.35 * s)


def wire(d, *pts):
    """Straight wires through a list of points."""
    for a, b in zip(pts, pts[1:]):
        d += elm.Line().at(a).to(b)


def node_label(d, xy, text):
    """Small grey netlist node name next to a junction."""
    d += elm.Label().at((xy[0] + 0.15, xy[1] + 0.35)).label(text, loc="right",
                                                            fontsize=8, color=GREY)


def flatten_white(path):
    """schemdraw PNGs have a transparent background; put them on white so
    they stay readable in dark-mode viewers."""
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, "white")
    Image.alpha_composite(bg, img).convert("RGB").save(path)


# =============================================================================
# 1) Preamp schematic
# =============================================================================
with schemdraw.Drawing(show=False) as d:
    d.config(fontsize=11, unit=L, bgcolor="white", margin=0.5)

    # ---- Q1 in the middle; everything else is placed relative to it ----
    q = DualGateNMOS().theta(0)          # theta(0): not rotated
    d += q.at((14, 8))
    d += elm.Label().at((q.center[0] + 2.2, q.center[1] + 0.2)).label("Q1\nBF961", loc="right")
    d += elm.Label().at(q.G2lbl).label("G2", fontsize=9, loc="right")
    d += elm.Label().at(q.G1lbl).label("G1", fontsize=9, loc="right")

    # ---- input: RF in -> RT to ground, C1 -> gate 1, bias divider R7/R8 ----
    g1y = q.gate1[1]
    xin = q.gate1[0] - 9.0
    d += elm.Dot(open=True).at((xin, g1y)).label("RF in\n50 Ω", loc="left")
    d += elm.Line().right(1.0)
    d += (inn := elm.Dot())
    d += elm.Resistor().down(L).at(inn.center).label("RT\n51 Ω", loc="bottom")
    d += elm.Ground()
    d += elm.Capacitor().right(L).at(inn.center).label("C1\n10 nF")
    d += elm.Line().right(3.0)
    d += (g1n := elm.Dot())
    d += elm.Line().tox(q.gate1[0])
    d += elm.Resistor().down(L).at(g1n.center).label("R8\n33 kΩ", loc="bottom")
    d += elm.Ground()
    # R7 from gate 1 up to +9 V would cross the gate-2 wiring, so it goes up
    # from a point slightly left of the gate-1 junction
    r7x = g1n.center[0] - 1.5
    d += elm.Dot().at((r7x, g1y))
    d += elm.Resistor().down(L).at((r7x, g1y + L)).reverse().label("R7\n100 kΩ", loc="bottom")
    d += elm.Vdd().at((r7x, g1y + L)).label("+9 V")
    node_label(d, g1n.center, "g1")
    node_label(d, inn.center, "in")

    # ---- gate 2: C5 to ground, R6 from the pot wiper; pot RV1 with R5 ----
    # gate-2 lead -> short wire left -> up 3 units -> left to the C5/R6 node
    gx = q.gate2[0] - 0.8
    by = q.gate2[1] + 3.0
    wire(d, q.gate2, (gx, q.gate2[1]), (gx, by))
    d += elm.Line().left(1.2).at((gx, by))
    d += (g2n := elm.Dot())
    d += elm.Capacitor().down(L).at(g2n.center).label("C5\n10 nF", loc="top")
    d += elm.Ground()
    node_label(d, g2n.center, "g2")
    d += elm.Resistor().left(L).at(g2n.center).label("R6\n10 kΩ")
    d += elm.Line().left(1.2)
    wip = d.here                         # the wire end = the pot wiper
    # the pot stands vertically; its wiper arrow (tap) points right, at wip
    pot = elm.Potentiometer().up(3.0).flip()
    d += pot.at((wip[0] - 0.72, wip[1] - 1.5)).label("RV1\n50 kΩ lin\nRF GAIN", loc="top")
    d += elm.Ground().at(pot.start)
    d += elm.Resistor().up(L).at(pot.end).label("R5\n15 kΩ", loc="bottom")
    d += elm.Vdd().label("+9 V")
    node_label(d, (wip[0] - 0.2, wip[1] - 0.9), "wip")

    # ---- source: R2 to ground, C2 bypass, R3 bleeder from +9 V ----
    d += elm.Line().down(0.8).at(q.source)
    d += (sn := elm.Dot())
    d += elm.Resistor().down(L).label("R2\n330 Ω", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(2.2).at(sn.center)
    d += (sn2 := elm.Dot())
    d += elm.Capacitor().down(L).label("C2\n100 nF", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(4.8).at(sn2.center)
    d += elm.Resistor().up(L).label("R3\n2.2 kΩ", loc="bottom")
    d += elm.Vdd().label("+9 V")
    node_label(d, sn.center, "s")

    # ---- drain: L1 || R4 to +9 V, C4 to the output, R9 to ground ----
    d += elm.Line().up(0.8).at(q.drain)
    d += (dn := elm.Dot())
    d += elm.Inductor2(loops=4).up(L).label("L1\n47 µH", loc="bottom")
    d += (top := elm.Dot())
    d += elm.Vdd().label("+9 V")
    d += elm.Line().right(2.2).at(dn.center)
    d += (dn2 := elm.Dot())
    d += elm.Resistor().up(L).label("R4\n1 kΩ", loc="bottom")
    d += elm.Line().to(top.center)
    node_label(d, dn.center, "d")
    d += elm.Capacitor().right(L).at(dn2.center).label("C4\n10 nF")
    d += (on := elm.Dot())
    d += elm.Resistor().down(L).at(on.center).label("R9\n100 kΩ", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(1.5).at(on.center)
    d += elm.Dot(open=True).label("RF out\nto mixer RF in", loc="right")
    node_label(d, on.center, "out")

    # ---- 9 V decoupling, drawn on its own ----
    c6 = (xin, top.center[1])
    d += elm.Vdd().at(c6).label("+9 V")
    d += elm.Capacitor().down(L).at(c6).label("C6\n100 nF", loc="bottom")
    d += elm.Ground()

    d += elm.Label().at((xin - 1.0, sn.center[1] - L - 1.6)).label(
        "Gain: about -60 dB (pot at 0) to +20 dB (pot at max), 2 ... 30 MHz.  "
        "Values match sim/preamp.inc.  With a 10k pot use R5 = 3.3k; with 100k use R5 = 33k.",
        loc="right", fontsize=9, color=GREY)
    d.save(OUT, dpi=160)
flatten_white(OUT)
print("wrote", os.path.abspath(OUT))

# =============================================================================
# 2) Block diagram of the whole front end
# =============================================================================
with schemdraw.Drawing(show=False) as d:
    d.config(fontsize=11, unit=1, bgcolor="white", margin=0.5)
    w, h = 3.6, 2.0
    d += (ant := elm.Antenna().at((0, 0)).label("antenna", loc="top"))
    d += flow.Arrow().right(2.6).at(ant.start)
    d += (bpf := flow.Box(w=w, h=h).label("Bandpass filter\n40 m or 20 m\n(optional)"))
    d += flow.Arrow().right(2.6).at(bpf.E)
    d += (pre := flow.Box(w=w, h=h).label("RF preamp\nBF961, 20 dB\n(optional)"))
    d += flow.Arrow().right(2.6).at(pre.E)
    d += (mix := flow.Box(w=w, h=h).label("Mixer\nBF961\n+ RC low-pass"))
    d += flow.Arrow().right(2.6).at(mix.E)
    d += (pico := flow.Box(w=w, h=h).label("Raspberry Pi\nPico (RP2040)"))
    # LO from GP8 back to the mixer's gate 2, IF from the mixer to GP26
    lo_y = mix.S[1] - 1.6
    d += elm.Line().at(pico.S).down(1.6)
    d += elm.Line().tox(mix.S[0])
    d += flow.Arrow().up(1.6)

    def txt(xy, text, size=10, color="black", ha="center", va="center"):
        """Free text placed by its alignment point (centred by default)."""
        d.add(elm.Label().at(xy).label(text, fontsize=size, color=color,
                                       halign=ha, valign=va))

    txt(((mix.S[0] + pico.S[0]) / 2, lo_y - 0.4), "LO: GP8 square wave → gate 2", va="top")
    txt(((mix.E[0] + pico.W[0]) / 2, mix.E[1] + 0.2), "IF → GP26", va="bottom")
    txt(((pre.E[0] + mix.W[0]) / 2, mix.E[1] + 0.2), "RF", va="bottom")
    txt((pre.N[0], pre.N[1] + 0.2), "gain pot RV1", va="bottom")
    # interface notes under the boxes
    txt((bpf.S[0], bpf.S[1] - 0.2), "50 Ω in / 50 Ω out", 9, GREY, va="top")
    txt((pre.S[0], pre.S[1] - 0.2), "51 Ω input termination", 9, GREY, va="top")
    txt((ant.start[0], lo_y - 1.2),
        "Without the preamp: put a 51 Ω resistor from the mixer's RF in to GND after the filter, "
        "so that the filter sees its 50 Ω load.\n"
        "Without the filter: the preamp (or the mixer) takes the antenna directly.",
        9, GREY, ha="left", va="top")
    d.save(OUT_CHAIN, dpi=160)
flatten_white(OUT_CHAIN)
print("wrote", os.path.abspath(OUT_CHAIN))
