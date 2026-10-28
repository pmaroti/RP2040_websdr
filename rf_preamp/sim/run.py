#!/usr/bin/env python3
"""Simulate the BF961 RF preamp, and the whole chain filter -> preamp -> mixer.

Usage (from anywhere):   python3 rf_preamp/sim/run.py
Needs: ngspice on PATH, numpy, matplotlib, and the sibling folders
       ../../mixer/ (BF961 model, mixer circuit) and ../../bandpass/ (filters).
Raw ngspice output goes to sim/out/, plots to plots/, numbers to plots/results.txt.

What it does:
  step 1  DC: gate-2 voltage, source voltage and drain current versus the
          potentiometer position
  step 2  AC: gain versus frequency for several pot positions, and the
          input match (S11) of the 50 ohm input
  step 3  gain versus pot position at 7.1 and 14.175 MHz
  step 4  large signal: output amplitude versus input amplitude at full
          gain (where the preamp starts to compress)
  step 5  the chain: antenna -> bandpass -> preamp -> mixer RF input,
          frequency response for both bands (AC), and one full transient run
          of antenna -> 40 m filter -> preamp -> mixer -> ADC pin, compared
          with the mixer alone

Gain convention: "gain" is V(out) / V(in), where V(in) is the voltage at
the preamp's 50 ohm input terminal (= what an antenna or filter delivers
into 50 ohm) and V(out) is the voltage at the mixer's RF input. The preamp
output is loaded like the mixer input loads it: 100 kohm || 6 pF.
"""
import os
import subprocess
import numpy as np
import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")   # draw into files only, no window
import matplotlib.pyplot as plt

# ----------------------------------------------------------------- folders
HERE = os.path.dirname(os.path.abspath(__file__))       # rf_preamp/sim/
OUT = os.path.join(HERE, "out")
PLOTS = os.path.join(HERE, "..", "plots")
os.makedirs(OUT, exist_ok=True)
os.makedirs(PLOTS, exist_ok=True)
# relative paths as seen from rf_preamp/sim/, where the decks are written
BF961 = "../../mixer/models/bf961.lib"
MIXER = "../../mixer/sim/mixer_core.inc"
BPF = {"40m": ("../../bandpass/sim/bpf_40m.inc", "BPF40M", 7.1e6),
       "20m": ("../../bandpass/sim/bpf_20m.inc", "BPF20M", 14.175e6)}

POT = 50e3      # potentiometer value used in the design (see README)
RTOP = 15e3     # resistor from +9 V to the top of the pot

# ----------------------------------------------------------------- chart styling
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BAND = "#eceae4"
plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
    "ytick.color": INK2, "text.color": INK, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
    "axes.spines.right": False, "lines.linewidth": 1.6, "font.size": 10,
    "legend.frameon": False,
})

results = []


def log(line):
    """Print a result line and remember it for results.txt."""
    print(line)
    results.append(line)


def ngspice(name, deck):
    """Write the deck into sim/, run ngspice in batch mode, return the .meas values.

    Same helper as in mixer/sim/run.py: ".meas" results are printed by
    ngspice as "name = value ...", and are collected into a dict.
    """
    path = os.path.join(HERE, f"_{name}.cir")
    with open(path, "w") as f:
        f.write(deck)
    r = subprocess.run(["ngspice", "-b", path], cwd=HERE, capture_output=True, text=True)
    os.remove(path)
    if r.returncode != 0 or "rror" in r.stdout:
        bad = [l for l in (r.stdout + r.stderr).splitlines() if "rror" in l]
        raise RuntimeError(f"ngspice {name} failed:\n" + "\n".join(bad[:20]))
    meas = {}
    for line in r.stdout.splitlines():
        parts = line.split("=")
        if len(parts) >= 2 and len(parts[0].split()) == 1:
            try:
                meas[parts[0].strip()] = float(parts[1].split()[0])
            except ValueError:
                pass
    return meas


def load(fname):
    """Load a wrdata file as a 2-D array."""
    return np.loadtxt(os.path.join(OUT, fname))


def tone(t, y, f):
    """Amplitude of the f component over a whole number of periods (lock-in).

    Resample on an even grid, multiply by exp(-j 2 pi f t), average over an
    integer number of periods: 2 * |mean| is the peak amplitude at f.
    """
    tu = np.linspace(t[0], t[-1], 200000)
    yu = np.interp(tu, t, y)
    n = int((tu[-1] - tu[0]) * f)
    tu, yu = tu[tu <= tu[0] + n / f], yu[tu <= tu[0] + n / f]
    return 2 * abs(np.mean(yu * np.exp(-2j * np.pi * f * tu)))


def db(x):
    """Magnitude in dB (20 log10 |x|), safe for zero."""
    return 20 * np.log10(np.abs(x) + 1e-15)


def mhz_axis(a, lo, hi):
    """Log frequency axis in MHz with plain tick labels (1, 2, 3, 5, 7, 10 ...)."""
    ticks = [t for t in (1, 2, 3, 5, 7, 10, 14, 20, 30, 50) if lo <= t <= hi]
    a.set_xlim(lo, hi)
    a.set_xticks(ticks)
    a.set_xticklabels([str(t) for t in ticks])
    a.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())


# The preamp test set used in steps 1 to 4:
#   2 V AC generator + 50 ohm source resistance -> preamp input
#   preamp output -> 100 kohm || 6 pF (what the mixer's RF input looks like)
def bench(pos, src="AC 2"):
    return f"""V9 vdd 0 9
VS src 0 {src}
RS src in 50
X1 in out vdd PREAMP pos={pos} rpot={POT} rtop={RTOP}
RL out 0 100k
CL out 0 6p
"""


HEAD = f"""* preamp
.include {BF961}
.include preamp.inc
"""

# ------------------------------------------------------- step 1: DC vs pot
# One operating-point run per pot position. Node voltages inside the
# subcircuit are reached as v(x1.<node>). Drain current = current into the
# source node from the transistor = V(s)/R2 - (9 V - V(s))/R3.
log("== DC operating point vs potentiometer position (50k pot, 15k top resistor) ==")
log("  pos   V(g2)   V(s)    VG2S    ID")
# One small operating-point run per pot position. "print" writes lines like
# "v(x1.g2) = 3.1e+00" which the ngspice() helper also picks up (a single
# word before "="), so the values come back in the same dict as .meas results.
pos_list = np.round(np.linspace(0, 1, 21), 3)
vg2, vs, idd = [], [], []
for p in pos_list:
    m = ngspice("dc", HEAD + bench(p) + """.control
op
let id = v(x1.s)/330 - (9 - v(x1.s))/2.2k
print v(x1.g2) v(x1.s) id
.endc
.end
""")
    vg2.append(m["v(x1.g2)"]); vs.append(m["v(x1.s)"]); idd.append(m["id"])
vg2, vs, idd = np.array(vg2), np.array(vs), np.array(idd)
for p, a, b, c in zip(pos_list, vg2, vs, idd):
    if int(round(p * 20)) % 2 == 0:                     # print every other row
        log(f"  {p:4.2f}  {a:5.2f} V  {b:5.2f} V  {a-b:+5.2f} V  {c*1e3:5.2f} mA")

fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(pos_list, vg2, color=C1, label="V(gate 2)")
ax.plot(pos_list, vs, color=C2, label="V(source)")
ax.plot(pos_list, idd * 1e3, color=C3, label="drain current (mA)")
ax.set(xlabel="potentiometer position (0 = min, 1 = max)", ylabel="V  /  mA",
       title="Preamp DC operating point vs gain pot")
ax.legend(loc="upper left")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "preamp_dc.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 2: AC response
# AC sweep 1 ... 60 MHz at several pot positions. Gain = V(out) / V(in).
# S11 from the input impedance Zin = V(in) / current into the input.
log("\n== Gain vs frequency (V(out)/V(in), out loaded by 100k || 6 pF) ==")
positions = [1.0, 0.45, 0.35, 0.25, 0.15]
curves = []
for p in positions:
    ngspice("ac", HEAD + bench(p) + """.control
ac dec 100 1meg 60meg
wrdata out/ac.txt v(out)/v(in) v(in)/(-i(vs))
.endc
.end
""")
    d = load("ac.txt")
    f = d[:, 0]
    g = d[:, 1] + 1j * d[:, 2]
    zin = d[:, 4] + 1j * d[:, 5]
    curves.append(g)
    if p == 1.0:
        s11 = (zin - 50) / (zin + 50)
        for fx in (3.6e6, 7.1e6, 14.175e6, 21.2e6, 28.5e6):
            log(f"pos 1.00: {fx/1e6:6.3f} MHz  gain {np.interp(fx, f, db(g)):5.1f} dB,"
                f"  input S11 {np.interp(fx, f, db(s11)):5.1f} dB")
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
cols = [C1, C2, C3, "#eda100", "#6250d6"]
for p, g, c in zip(positions, curves, cols):
    ax[0].plot(f / 1e6, db(g), color=c, label=f"pot at {p:.2f}")
for b1, b2 in ((7.0, 7.2), (14.0, 14.35)):
    ax[0].axvspan(b1, b2, color=BAND, zorder=0)
ax[0].set(xscale="log", ylim=(-40, 25), xlabel="frequency (MHz)", ylabel="gain (dB)",
          title="Preamp gain vs frequency (40 m and 20 m shaded)")
ax[0].legend(loc="lower center", ncol=2, fontsize=8)
ax[1].plot(f / 1e6, db(s11), color=C1)
for a in ax:
    a.set_xscale("log")
    mhz_axis(a, 1, 60)
ax[1].set(ylim=(-60, 0), xlabel="frequency (MHz)", ylabel="S11 (dB)",
          title="Input match at full gain (51 ohm termination)")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "preamp_ac.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 3: gain vs pot
log("\n== Gain vs potentiometer position ==")
pp = np.round(np.linspace(0, 1, 41), 3)
g7, g14 = [], []
for p in pp:
    m = ngspice("pot", HEAD + bench(p) + """.control
ac lin 201 7meg 14.5meg
let g = vdb(out) - vdb(in)
meas ac g7 find g at=7.1meg
meas ac g14 find g at=14.175meg
.endc
.end
""")
    g7.append(m["g7"]); g14.append(m["g14"])
for p, a, b in zip(pp, g7, g14):
    if int(round(p * 40)) % 4 == 0:
        log(f"  pos {p:4.2f}: {a:6.1f} dB at 7.1 MHz, {b:6.1f} dB at 14.175 MHz")
log(f"control range at 7.1 MHz: {max(g7) - min(g7):.0f} dB")
fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(pp, g7, color=C1, label="7.1 MHz")
ax.plot(pp, g14, color=C2, label="14.175 MHz")
ax.set(xlabel="potentiometer position (0 = min, 1 = max)", ylabel="gain (dB)",
       title="Gain set by the 50k potentiometer")
ax.legend(loc="lower right")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "preamp_gain_vs_pot.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 4: large signal
# Transient runs at 7.1 MHz with increasing input amplitude at full gain.
# The output amplitude at 7.1 MHz is measured with the lock-in helper; where
# it falls 1 dB below the straight small-signal line, the preamp is in
# 1 dB compression.
log("\n== Large signal at full gain, 7.1 MHz ==")
amps = np.array([1e-3, 3e-3, 10e-3, 30e-3, 100e-3, 200e-3, 300e-3, 500e-3])
outs = []
for a in amps:
    # source amplitude 2a, so the 50 ohm input sees about a
    ngspice("big", HEAD + bench(1.0, f"SIN(0 {2*a} 7.1meg)") + """.control
tran 2n 20u
wrdata out/big.txt v(in) v(out)
.endc
.end
""")
    d = load("big.txt")
    s = d[:, 0] > 10e-6                     # skip the first 10 us (coupling caps)
    vin = tone(d[s, 0], d[s, 1], 7.1e6)
    vout = tone(d[s, 0], d[s, 3], 7.1e6)
    outs.append((vin, vout))
    log(f"  in {vin*1e3:6.1f} mV pk -> out {vout*1e3:7.1f} mV pk  ({db(vout/vin):5.1f} dB)")
vin = np.array([o[0] for o in outs]); vout = np.array([o[1] for o in outs])
g_small = vout[0] / vin[0]
fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(vin * 1e3, vout * 1e3, color=C1, marker="o", label="simulated")
ax.plot(vin * 1e3, vin * g_small * 1e3, color=GRID, lw=2.5, zorder=0, label="linear")
ax.set(xscale="log", yscale="log", xlabel="input (mV pk)", ylabel="output (mV pk)",
       title="Preamp compression at full gain, 7.1 MHz")
ax.legend(loc="lower right")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "preamp_compression.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 5a: chain, AC
# Antenna (50 ohm generator) -> bandpass filter -> preamp -> mixer RF input
# (modelled as 100 kohm || 6 pF as above). Also shown: the filter alone,
# terminated with 51 ohm at the mixer input, for building without the preamp.
log("\n== Chain response: antenna -> bandpass -> (preamp) -> mixer RF input ==")
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
for j, (band, (inc, sub, f0)) in enumerate(BPF.items()):
    deck = f"""* chain ac {band}
.include {BF961}
.include preamp.inc
.include {inc}
V9 vdd 0 9
* chain with preamp at full gain
VS1 a1 0 AC 2
RS1 a1 b1 50
XF1 b1 c1 {sub}
XP1 c1 o1 vdd PREAMP pos=1 rpot={POT} rtop={RTOP}
RL1 o1 0 100k
CL1 o1 0 6p
* chain without preamp: filter, then 51 ohm termination at the mixer input
VS2 a2 0 AC 2
RS2 a2 b2 50
XF2 b2 o2 {sub}
RT2 o2 0 51
RL2 o2 0 100k
CL2 o2 0 6p
.control
ac dec 200 2meg 50meg
wrdata out/chain_{band}.txt v(o1) v(o2)
.endc
.end
"""
    ngspice(f"chain_{band}", deck)
    d = load(f"chain_{band}.txt")
    f = d[:, 0]
    # gain relative to the voltage a matched 50 ohm load would get (1 V)
    with_pre = db(d[:, 1] + 1j * d[:, 2])
    no_pre = db(d[:, 4] + 1j * d[:, 5])
    ax[j].plot(f / 1e6, with_pre, color=C1, label="filter + preamp (full gain)")
    ax[j].plot(f / 1e6, no_pre, color=C2, label="filter + 51 ohm, no preamp")
    b1, b2 = (7.0, 7.2) if band == "40m" else (14.0, 14.35)
    ax[j].axvspan(b1, b2, color=BAND, zorder=0)
    ax[j].set(xscale="log", ylim=(-60, 25), xlabel="frequency (MHz)",
              ylabel="gain to mixer RF input (dB)", title=f"{band} chain")
    mhz_axis(ax[j], 2, 50)
    ax[j].legend(loc="lower center", fontsize=8)
    log(f"{band}: at {f0/1e6:.3f} MHz with preamp {np.interp(f0, f, with_pre):5.1f} dB, "
        f"filter only {np.interp(f0, f, no_pre):5.1f} dB; with preamp at "
        f"{3*f0/1e6:.1f} MHz (3rd LO harmonic) {np.interp(3*f0, f, with_pre):6.1f} dB")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "chain_ac.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 5b: chain, transient
# The real thing, all in one deck: LO 7.1 MHz on the mixer's gate 2, an
# antenna signal of 1 mV peak EMF at 7.103 MHz behind 50 ohm, and the IF
# measured at the ADC pin. Four builds are compared:
#   A  mixer alone (antenna straight into the mixer's RF input)
#   B  40 m filter + 51 ohm termination -> mixer
#   C  40 m filter -> preamp at full gain -> mixer
#   D  40 m filter -> preamp with the pot at 0.30 -> mixer
# The mixer circuit is read from mixer_core.inc with its own RF generator
# (VRF, RSRC) taken out, so the chain can drive the mixer's "rf" node, and
# its .include of the BF961 model re-pointed to the path seen from here.
core = open(os.path.join(HERE, MIXER)).read()
core = "\n".join(l for l in core.splitlines()
                 if not l.startswith(("VRF ", "RSRC ", ".include")))
with open(os.path.join(HERE, "_mixer_chain.inc"), "w") as fh:
    fh.write(core + "\n")
EMF = 1e-3
FLO, FIF = 7.1e6, 3e3
builds = {
    "A  mixer alone": "RSRC ant rf 50",
    "B  filter + 51R -> mixer": "RS ant b 50\nXF b rf BPF40M\nRT rf 0 51",
    "C  filter -> preamp (full) -> mixer": f"RS ant b 50\nXF b c BPF40M\nXP c rf vdd PREAMP pos=1 rpot={POT} rtop={RTOP}",
    "D  filter -> preamp (pot 0.30) -> mixer": f"RS ant b 50\nXF b c BPF40M\nXP c rf vdd PREAMP pos=0.3 rpot={POT} rtop={RTOP}",
}
log(f"\n== Whole chain, transient: LO {FLO/1e6:.1f} MHz, antenna {EMF*1e3:.0f} mV pk EMF "
    f"(50 ohm) at {(FLO+FIF)/1e6:.3f} MHz, IF {FIF/1e3:.0f} kHz at the ADC pin ==")
ifs = {}
try:
    for k, (label, net) in enumerate(builds.items()):
        deck = f"""* chain transient
.param flo={FLO} frf={FLO+FIF} arf=0
.include {BF961}
.include preamp.inc
.include {BPF['40m'][0]}
.include _mixer_chain.inc
VANT ant 0 SIN(0 {EMF} {FLO+FIF})
{net}
.ic v(src)=0.517 v(drain)=6.65 v(filt)=6.65 v(adc_b)=1.65 v(adc)=1.65
.control
save v(adc)
tran 4n 2.4m
wrdata out/chain_tran.txt v(adc)
.endc
.end
"""
        ngspice("chain_tran", deck)
        d = load("chain_tran.txt")
        s = d[:, 0] > 0.4e-3                 # skip start-up
        ifs[label] = tone(d[s, 0], d[s, 1], FIF)
        log(f"{label:42s} IF {ifs[label]*1e3:7.2f} mV pk  "
            f"({db(ifs[label] / ifs['A  mixer alone']):+5.1f} dB vs mixer alone)")
finally:
    os.remove(os.path.join(HERE, "_mixer_chain.inc"))

with open(os.path.join(PLOTS, "results.txt"), "w") as fh:
    fh.write("\n".join(results) + "\n")
print("\nplots and results.txt written to", os.path.abspath(PLOTS))
