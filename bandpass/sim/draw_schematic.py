#!/usr/bin/env python3
"""Draw the 40 m and 20 m bandpass filter schematics to bandpass/schematic.png.

Usage:  python3 bandpass/sim/draw_schematic.py      (needs: pip3 install schemdraw)
Part values match sim/bpf_40m.inc and sim/bpf_20m.inc.

Both filters have the same circuit, so one function draws a filter at a
given vertical position with a given set of values, and is called twice.
"""
import os
import schemdraw
import schemdraw.elements as elm
from PIL import Image                       # used to flatten the PNG onto white

HERE = os.path.dirname(os.path.abspath(__file__))       # bandpass/sim/
OUT = os.path.join(HERE, "..", "schematic.png")
L = 2.5          # default length of a two-terminal part
GREY = "#52514e"


def draw_filter(d, y, title, v):
    """Draw one two-resonator bandpass with its input at (0, y).

    v: dict of part values as display strings, keys ce, cc, cs, l, coil.
    Layout, left to right:
      RF in o--CE1--+--------CC--------+--CE2--o RF out
                    |     |            |     |
                   L1    CS1          L2    CS2
                    |     |            |     |
                   GND   GND          GND   GND
    """
    d += elm.Label().at((0, y + 2.2)).label(title, loc="right", fontsize=13)
    d += elm.Dot(open=True).at((0, y)).label("RF in\n50 Ω", loc="left")
    d += elm.Capacitor().right(L).label(f"CE1\n{v['ce']}")
    d += elm.Line().right(0.6)
    d += (n1 := elm.Dot())
    # tank 1: inductor and capacitor side by side, both down to ground
    d += elm.Inductor2(loops=4).down(L).at(n1.center).label(f"L1\n{v['l']}", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(1.8).at(n1.center)
    d += (n1b := elm.Dot())
    d += elm.Capacitor().down(L).at(n1b.center).label(f"CS1\n{v['cs']}", loc="bottom")
    d += elm.Ground()
    # coupling capacitor between the tanks
    d += elm.Capacitor().right(L).at(n1b.center).label(f"CC\n{v['cc']}")
    d += (n2 := elm.Dot())
    d += elm.Capacitor().down(L).at(n2.center).label(f"CS2\n{v['cs']}", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(1.8).at(n2.center)
    d += (n2b := elm.Dot())
    d += elm.Inductor2(loops=4).down(L).at(n2b.center).label(f"L2\n{v['l']}", loc="bottom")
    d += elm.Ground()
    d += elm.Line().right(0.6).at(n2b.center)
    d += elm.Capacitor().right(L).label(f"CE2\n{v['ce']}")
    d += elm.Dot(open=True).label("RF out\n50 Ω", loc="right")
    # coil winding note under the filter
    d += elm.Label().at((0, y - L - 1.4)).label(v["coil"], loc="right", fontsize=9, color=GREY)


with schemdraw.Drawing(show=False) as d:
    d.config(fontsize=11, unit=L, bgcolor="white", margin=0.6)
    draw_filter(d, 0, "40 m bandpass  (7.0 ... 7.2 MHz, -3 dB 6.77 ... 7.44 MHz, ~0.9 dB loss)",
                dict(ce="100 pF", cc="22 pF", cs="220 pF", l="1.5 µH",
                     coil="L1, L2: 17 turns on T50-2 (red) toroid, ~1.5 µH.  "
                          "Capacitors: NP0/C0G ceramic or silver mica."))
    draw_filter(d, -9.5, "20 m bandpass  (14.0 ... 14.35 MHz, -3 dB 13.62 ... 14.73 MHz, ~1.1 dB loss)",
                dict(ce="39 pF", cc="6.8 pF", cs="82 pF", l="1.0 µH",
                     coil="L1, L2: 16 turns on T50-6 (yellow) toroid, ~1.0 µH.  "
                          "Tune by squeezing / spreading the turns."))
    d += elm.Label().at((0, -9.5 - L - 2.6)).label(
        "Both filters expect 50 Ω on both sides: the antenna in, the preamp's 51 Ω input "
        "(or a 51 Ω resistor at the mixer's RF in) out.", loc="right", fontsize=9, color=GREY)
    d.save(OUT, dpi=160)

# schemdraw PNGs have a transparent background; flatten onto white so they
# stay readable in dark-mode viewers
img = Image.open(OUT).convert("RGBA")
bg = Image.new("RGBA", img.size, "white")
Image.alpha_composite(bg, img).convert("RGB").save(OUT)
print("wrote", os.path.abspath(OUT))
