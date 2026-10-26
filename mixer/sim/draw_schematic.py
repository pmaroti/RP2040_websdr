#!/usr/bin/env python3
"""Draw the BF961 mixer schematic, with its RP2040 connections, to mixer/schematic.png.

Usage:  python3 mixer/sim/draw_schematic.py      (needs: pip3 install schemdraw)
Part values and node names match sim/mixer_core.inc.
"""
import os
import schemdraw
from PIL import Image
import schemdraw.elements as elm
from schemdraw.segments import Segment, SegmentCircle

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "schematic.png")
L = 2.5   # length of a two-terminal part


class DualGateNMOS(elm.Element):
    """N-channel depletion dual-gate MOSFET (BF961). Anchors: drain, gate1, gate2, source."""

    def __init__(self, s=1.5, **kwargs):
        super().__init__(**kwargs)
        def P(*pts):
            return [(x * s, y * s) for x, y in pts]
        # channel: solid line = depletion mode
        self.segments.append(Segment(P((0, -1.0), (0, 1.0)), lw=2.5))
        # gate 2 (upper) and gate 1 (lower)
        self.segments.append(Segment(P((-0.25, 0.15), (-0.25, 0.9))))
        self.segments.append(Segment(P((-0.25, 0.55), (-1.0, 0.55))))
        self.segments.append(Segment(P((-0.25, -0.9), (-0.25, -0.15))))
        self.segments.append(Segment(P((-0.25, -0.55), (-1.0, -0.55))))
        # drain and source
        self.segments.append(Segment(P((0, 0.85), (0.7, 0.85), (0.7, 1.6))))
        self.segments.append(Segment(P((0, -0.85), (0.7, -0.85), (0.7, -1.6))))
        # substrate arrow into the channel (N-channel), tied to source
        self.segments.append(Segment(P((0.7, 0.0), (0.05, 0.0)), arrow="->",
                                     arrowwidth=0.25, arrowlength=0.3))
        self.segments.append(Segment(P((0.7, 0.0), (0.7, -0.85))))
        self.segments.append(SegmentCircle((0.2 * s, 0.0), 1.15 * s, lw=1.2))
        self.anchors["center"] = (0, 0)
        self.anchors["drain"] = (0.7 * s, 1.6 * s)
        self.anchors["source"] = (0.7 * s, -1.6 * s)
        self.anchors["gate2"] = (-1.0 * s, 0.55 * s)
        self.anchors["gate1"] = (-1.0 * s, -0.55 * s)
        self.anchors["G2lbl"] = (-0.9 * s, 0.75 * s)
        self.anchors["G1lbl"] = (-0.9 * s, -0.35 * s)


def wire(d, *pts):
    for a, b in zip(pts, pts[1:]):
        d += elm.Line().at(a).to(b)


with schemdraw.Drawing(show=False) as d:
    d.config(fontsize=11, unit=L, bgcolor="white", margin=0.5)  # opaque: readable in dark viewers

    # ---------------- Raspberry Pi Pico / RP2040 ----------------
    H = 12.0
    pico = elm.Ic(size=(3.4, H), pins=[
        elm.IcPin(name="GP8", side="right", pos=0.8, pin="11"),
        elm.IcPin(name="GP26/ADC0", side="right", pos=0.06, pin="31"),
        elm.IcPin(name="3V3(OUT)", side="left", pos=0.6, pin="36"),
        elm.IcPin(name="GND", side="left", pos=0.4, pin="38"),
    ], edgepadW=0.6, leadlen=0.8).label("Raspberry Pi\nPico\n(RP2040)", loc="center", fontsize=13)
    d += pico.at((0, 0))
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
    d += (dn := elm.Dot())
    d += elm.Resistor().up(L).label("R4\n1 kΩ", loc="bottom")
    d += (top := elm.Dot())
    d += elm.Vdd().label("+9 V")
    c4x = top.center[0] - 3.0
    wire(d, top.center, (c4x, top.center[1]))
    d += elm.Dot().at((c4x, top.center[1]))
    d += elm.Capacitor(polar=True).down(L).at((c4x, top.center[1])).label("C4\n100 µF", loc="bottom")
    d += elm.Ground()

    # single-pole RC low-pass R5 / C5
    d += elm.Line().right(0.8).at(dn.center)
    d += elm.Resistor().right(L).label("R5\n10 kΩ")
    d += (fn := elm.Dot())
    d += elm.Capacitor().down(L).at(fn.center).label("C5\n3.3 nF", loc="bottom")
    d += elm.Ground()
    # DC block + 1.65 V bias divider
    d += elm.Line().right(0.6).at(fn.center)
    d += elm.Capacitor().right(L).label("C6\n1 µF")
    d += (bn := elm.Dot())
    d += elm.Resistor().up(L).at(bn.center).label("R6\n100 kΩ", loc="bottom")
    d += elm.Vdd().label("3V3")
    d += elm.Resistor().down(L).at(bn.center).label("R7\n100 kΩ", loc="bottom")
    d += elm.Ground()
    # series resistor + BAT54S clamp
    d += elm.Line().right(0.6).at(bn.center)
    d += elm.Resistor().right(L).label("R8\n1 kΩ")
    d += (an := elm.Dot())
    d += elm.Schottky().up(L).at(an.center).label("D1", loc="bottom")
    d += elm.Vdd().label("3V3")
    d += elm.Schottky().up(L).at((an.center[0], an.center[1] - L)).label("D2", loc="bottom")
    d += elm.Ground().at((an.center[0], an.center[1] - L))
    d += elm.Label().at((an.center[0] + 0.5, an.center[1] - L - 0.3)).label(
        "D1+D2 = BAT54S", loc="right", fontsize=10)

    # IF back to the ADC input GP26
    ex = an.center[0] + 4.0
    wire(d, an.center, (ex, an.center[1]), (ex, gp26[1]), gp26)
    d += elm.Label().at((gp26[0] + 0.3, gp26[1] + 0.45)).label(
        "IF to ADC: 1.65 V ± signal, always within 0…3.3 V", loc="right", fontsize=10)

    # node names used in the netlist
    for node, xy in (("drain", dn.center), ("filt", fn.center), ("adc_b", bn.center),
                     ("adc", an.center), ("src", sn.center)):
        d += elm.Label().at((xy[0] + 0.15, xy[1] + 0.35)).label(
            node, loc="right", fontsize=8, color="#52514e")

    d += elm.Label().at((gp26[0] + 0.3, gp26[1] - 0.9)).label(
        "All grounds common with Pico GND.  9 V supply for the mixer only.  "
        "Values match sim/mixer_core.inc.", loc="right", fontsize=9, color="#52514e")

    d.save(OUT, dpi=160)

# schemdraw saves PNGs with a transparent background; flatten onto white so
# the black lines stay visible in dark-mode viewers
img = Image.open(OUT).convert("RGBA")
bg = Image.new("RGBA", img.size, "white")
Image.alpha_composite(bg, img).convert("RGB").save(OUT)
print("wrote", os.path.abspath(OUT))
