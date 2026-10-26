#!/usr/bin/env python3
"""Run every ngspice simulation of the BF961 mixer and draw the plots.

Usage (from anywhere):   python3 mixer/sim/run.py
Needs: ngspice on PATH, numpy, matplotlib.
Raw ngspice output goes to sim/out/, plots to plots/, numbers to plots/results.txt.
"""
import os
import subprocess
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
PLOTS = os.path.join(HERE, "..", "plots")
os.makedirs(OUT, exist_ok=True)
os.makedirs(PLOTS, exist_ok=True)

FLO = 7.100e6          # LO on GP8
ADC_FS = 25e3          # RP2040 ADC sample rate in the firmware
ARF = 10e-3            # RF test tone, V peak at the antenna

# chart styling (reference palette, light mode)
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
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
    print(line)
    results.append(line)


def ngspice(name, deck):
    """Write deck to sim/<name>.cir (next to mixer_core.inc) and run it in batch mode."""
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


def load(fname, ncols):
    """wrdata writes (x, y) pairs per vector; return x and the y columns."""
    d = np.loadtxt(os.path.join(OUT, fname))
    return d[:, 0], [d[:, 2 * i + 1] for i in range(ncols)]


def tone(t, y, f):
    """Amplitude of the f component over a whole number of periods (lock-in)."""
    tu = np.linspace(t[0], t[-1], 200000)
    yu = np.interp(tu, t, y)
    n = int((tu[-1] - tu[0]) * f)
    tu, yu = tu[tu <= tu[0] + n / f], yu[tu <= tu[0] + n / f]
    return 2 * abs(np.mean(yu * np.exp(-2j * np.pi * f * tu)))


# ------------------------------------------------------------------ model check
deck = """* BF961 model check
.include ../models/bf961.lib
VD d 0 15
VG2 g2 0 4
VG1 g1 0 0
XQ d g2 g1 0 BF961
.control
dc vg1 -2 0.6 0.01
let id=-i(vd)
let gm=deriv(id)
meas dc idss find id at=0
meas dc vg1off when id=20u
meas dc gm10 find gm when id=10m
wrdata out/bf961_id_vg1.txt id gm
alter vg1 0
dc vg2 -3 6 0.01
let id2=-i(vd)
meas dc vg2off when id2=20u
wrdata out/bf961_id_vg2.txt id2
dc vd 0 20 0.05 vg1 -0.8 0.6 0.2
let id3=-i(vd)
wrdata out/bf961_id_vds.txt id3
.endc
.end
"""
m = ngspice("model", deck)
log("== BF961 model vs datasheet (VDS=15 V, VG2S=4 V) ==")
log(f"IDSS (VG1S=0)        {m['idss']*1e3:6.2f} mA   datasheet 4..20 mA")
log(f"y21s at ID=10 mA     {m['gm10']*1e3:6.2f} mS   datasheet 15 typ, 12 min")
log(f"-VG1S(off) @20 uA    {-m['vg1off']:6.2f} V    datasheet <= 3.5 V")
log(f"-VG2S(off) @20 uA    {-m['vg2off']:6.2f} V    datasheet <= 3.5 V")

fig, ax = plt.subplots(1, 2, figsize=(10, 3.8))
d = np.loadtxt(os.path.join(OUT, "bf961_id_vds.txt"))
# dc sweep with a second source: the curves are concatenated, split on VDS reset
splits = np.where(np.diff(d[:, 0]) < 0)[0] + 1
for k, seg in enumerate(np.split(d, splits)):
    vg1 = -0.8 + 0.2 * k
    ax[0].plot(seg[:, 0], seg[:, 1] * 1e3, color=C1, lw=1.4)
    ax[0].annotate(f"{vg1:+.1f} V", (20, seg[-1, 1] * 1e3), xytext=(3, 0),
                   textcoords="offset points", va="center", fontsize=8, color=INK2)
ax[0].set(xlabel="VDS (V)", ylabel="ID (mA)", xlim=(0, 23),
          title="ID vs VDS, VG2S = 4 V, VG1S steps (cf. datasheet Fig. 2)")
x, (iv, gm) = load("bf961_id_vg1.txt", 2)
ax[1].plot(x, gm * 1e3, color=C1, label="y21s (mS)")
ax[1].plot(x, iv * 1e3, color=C2, label="ID (mA)")
ax[1].set(xlabel="VG1S (V)", title="VDS = 15 V, VG2S = 4 V (cf. Fig. 4)")
ax[1].legend()
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "bf961_model.png"), dpi=130)
plt.close(fig)

# ----------------------------------------------------- shared deck pieces
def mixer_deck(name, flo, fif, arf, tstop, vs0, vd0, extra_save="", tstep="4n", ic=True):
    ic_line = (f".ic v(src)={vs0} v(drain)={vd0} v(filt)={vd0} v(adc_b)=1.65 v(adc)=1.65"
               if ic else "")
    return f"""* {name}
.param flo={flo} frf={flo + fif} arf={arf}
.include mixer_core.inc
{ic_line}
.control
save v(adc) v(drain) v(src) v(filt) {extra_save}
tran {tstep} {tstop} {'uic' if not ic else ''}
meas tran vd_avg avg v(drain) from={tstop*0.6} to={tstop}
meas tran vs_avg avg v(src) from={tstop*0.6} to={tstop}
meas tran adc_avg avg v(adc) from={tstop*0.6} to={tstop}
meas tran adc_max max v(adc)
meas tran adc_min min v(adc)
wrdata out/{name}.txt v(adc) v(drain) v(filt)
.endc
.end
"""


# ------------------------------------------- step 1: settle the operating point
# The 220 ohm x 47 uF source bypass needs ~50 ms to settle. Average voltages
# depend on LO duty cycle, not its frequency, so settle with a 100 kHz LO
# (cheap), then start every 7.1 MHz run from that point via .ic.
op = ngspice("op", mixer_deck("op", 100e3, 0, 0, 60e-3, 0.5, 6.5, tstep="100n"))
VS0, VD0 = op["vs_avg"], op["vd_avg"]
log("\n== Operating point, LO running (50 % duty) ==")
log(f"V(source) avg        {VS0:6.3f} V  -> I avg = {VS0/220*1e3:.2f} mA")
log(f"V(drain)  avg        {VD0:6.3f} V")

# ------------------------------------------- step 2: the reference mixer run
FIF = 3e3
r = ngspice("main", mixer_deck("main", FLO, FIF, ARF, 10.5e-3, VS0, VD0))
t, (adc, drn, filt) = load("main.txt", 3)
sel = t > 0.5e-3
a_if = tone(t[sel], adc[sel], FIF)
gain_db = 20 * np.log10(a_if / ARF)
log(f"\n== Mixer, LO {FLO/1e6:.3f} MHz, RF {(FLO+FIF)/1e6:.6f} MHz @ {ARF*1e3:.0f} mV pk ==")
log(f"IF {FIF/1e3:.0f} kHz at ADC pin    {a_if*1e3:6.2f} mV pk")
log(f"conversion gain RF->ADC {gain_db:6.1f} dB")
log(f"ADC pin DC / min / max {r['adc_avg']:.3f} / {r['adc_min']:.3f} / {r['adc_max']:.3f} V")
ripple = adc[sel] - np.convolve(adc[sel], np.ones(51) / 51, "same")
log(f"LO ripple at ADC pin   {np.std(ripple[100:-100])*2.83*1e3:6.2f} mV pp (approx)")

fig, ax = plt.subplots(3, 1, figsize=(10, 7.5))
tz = (t > 1.0e-3) & (t < 1.0e-3 + 2.2 / FLO)
ax[0].plot((t[tz] - 1e-3) * 1e9, drn[tz], color=C1)
ax[0].set(ylabel="V(drain) (V)", xlabel="time (ns)",
          title="Drain: chopped at the LO rate (two LO periods)")
sw = (t > 0.5e-3) & (t < 3e-3)
ax[1].plot(t[sw] * 1e3, drn[sw], color="#c9c8c2", lw=0.6, label="drain (LO-rate square wave, envelope)")
ax[1].plot(t[sw] * 1e3, filt[sw], color=C1, label="after RC low-pass (filt)")
ax[1].set(ylabel="V", xlabel="time (ms)", title="RC low-pass removes the LO, leaves the 3 kHz IF")
ax[1].legend(loc="upper right", fontsize=8)
ax[2].plot(t[sw] * 1e3, adc[sw], color=C1)
ax[2].axhline(3.3, color=INK2, lw=1, ls="--")
ax[2].axhline(0, color=INK2, lw=1, ls="--")
ax[2].set(ylabel="V(adc) (V)", xlabel="time (ms)", ylim=(-0.2, 3.5),
          title=f"ADC pin (GP26): 1.65 V mid-scale + {a_if*1e3:.0f} mV pk IF, inside 0..3.3 V")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "mixer_waveforms.png"), dpi=130)
plt.close(fig)

# spectrum the RP2040 actually sees: sample V(adc) at 25 kS/s
fig, ax = plt.subplots(1, 2, figsize=(10, 3.8))
tu = np.arange(0.5e-3, t[-1], 1e-8)
yu = np.interp(tu, t, adc)
w = np.hanning(len(yu))
sp = np.abs(np.fft.rfft((yu - yu.mean()) * w)) * 2 / w.sum()
f = np.fft.rfftfreq(len(yu), 1e-8)
ax[0].plot(f / 1e6, 20 * np.log10(sp + 1e-9), color=C1, lw=0.8)
ax[0].set(xscale="linear", xlim=(0, 22), ylim=(-140, 0), xlabel="frequency (MHz)",
          ylabel="dBV", title="V(adc), 0..22 MHz (LO and its harmonics)")
ts = np.arange(0.5e-3, t[-1], 1 / ADC_FS)
ys = np.interp(ts, t, adc)
w = np.hanning(len(ys))
sp = np.abs(np.fft.rfft((ys - ys.mean()) * w)) * 2 / w.sum()
f = np.fft.rfftfreq(len(ys), 1 / ADC_FS)
ax[1].plot(f / 1e3, 20 * np.log10(sp + 1e-9), color=C1, marker="o", ms=3)
ax[1].set(xlabel="frequency (kHz)", ylabel="dBV", ylim=(-100, 0),
          title="Same signal sampled at 25 kS/s (what the ADC gets)")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "mixer_spectrum.png"), dpi=130)
plt.close(fig)

# ------------------------------------------- step 3: IF response vs IF frequency
log("\n== Conversion gain vs IF frequency (RF->ADC pin) ==")
fifs = [0.3e3, 1e3, 2e3, 3e3, 5e3, 7e3, 10e3, 12.5e3, 15e3, 20e3]
gains = []
for fi in fifs:
    tstop = 0.4e-3 + max(1.2e-3, 3 / fi)
    ngspice("ifsweep", mixer_deck("ifsweep", FLO, fi, ARF, tstop, VS0, VD0))
    t, (adc, _, _) = load("ifsweep.txt", 3)
    s = t > 0.4e-3
    g = 20 * np.log10(tone(t[s], adc[s], fi) / ARF)
    gains.append(g)
    log(f"IF {fi/1e3:5.1f} kHz   {g:6.1f} dB")
# analytic single-pole for comparison
R5, C5 = 10e3, 3.3e-9
Rth = R5 + 1e3            # drain load 1k in series with R5 ...
Rload = 50e3              # ... loaded by the 100k/100k bias divider
Req = 1 / (1 / Rth + 1 / Rload)
fc = 1 / (2 * np.pi * Req * C5)
log(f"RC corner (R4+R5 || R6||R7, C5): fc = {fc/1e3:.1f} kHz")
ff = np.logspace(2, np.log10(30e3), 200)
pole = gains[1] - 10 * np.log10(1 + (ff / fc) ** 2) + 10 * np.log10(1 + (1e3 / fc) ** 2)
fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(ff / 1e3, pole, color=GRID, lw=2.5, label=f"single pole, fc = {fc/1e3:.1f} kHz")
ax.plot(np.array(fifs) / 1e3, gains, color=C1, marker="o", ms=6, label="ngspice transient")
ax.axvline(ADC_FS / 2e3, color=INK2, lw=1, ls="--")
ax.annotate("Nyquist 12.5 kHz", (ADC_FS / 2e3, max(gains)), xytext=(4, -2),
            textcoords="offset points", fontsize=8, color=INK2, va="top")
ax.set(xscale="log", xlabel="IF frequency (kHz)", ylabel="RF -> ADC gain (dB)",
       title=f"Conversion gain vs IF, LO {FLO/1e6:.1f} MHz")
ax.legend(loc="lower left")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "if_response.png"), dpi=130)
plt.close(fig)

# ------------------------------------------- step 4: large RF input, ADC range
log("\n== Large RF input: ADC pin stays inside 0..3.3 V ==")
amps = [1e-3, 10e-3, 30e-3, 100e-3, 300e-3, 1.0]
outs, mins, maxs = [], [], []
for a in amps:
    r = ngspice("big", mixer_deck("big", FLO, FIF, a, 1.6e-3, VS0, VD0))
    t, (adc, _, _) = load("big.txt", 3)
    s = t > 0.5e-3
    outs.append(tone(t[s], adc[s], FIF))
    mins.append(adc[s].min()); maxs.append(adc[s].max())
    log(f"RF {a*1e3:7.1f} mV pk -> IF {outs[-1]*1e3:7.1f} mV pk, "
        f"ADC min {mins[-1]:.3f} V max {maxs[-1]:.3f} V")
fig, ax = plt.subplots(figsize=(7, 4))
ax.plot(np.array(amps) * 1e3, np.array(outs) * 1e3, color=C1, marker="o", label="IF amplitude at ADC")
ax.plot(np.array(amps) * 1e3, np.array(amps) * 1e3 * 10 ** (gain_db / 20), color=GRID, lw=2.5,
        label="linear (small-signal gain)", zorder=0)
ax.axhline(1650, color=INK2, lw=1, ls="--")
ax.annotate("full ADC swing (1.65 V pk)", (1, 1650), xytext=(0, 4),
            textcoords="offset points", fontsize=8, color=INK2)
ax.set(xscale="log", yscale="log", xlabel="RF input (mV pk)", ylabel="IF at ADC (mV pk)",
       title="Compression with large RF input")
ax.legend(loc="lower right")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "compression.png"), dpi=130)
plt.close(fig)

# ------------------------------------------- step 5: power-up transient
# 9 V and 3.3 V ramp from 0, all capacitors discharged, LO pin idle low (DC 0).
deck = """* power-up
.param flo=1meg frf=1meg arf=0
.include mixer_core.inc
.control
save v(adc) v(adc_b) v(drain) v(filt) v(vdd) i(v33)
tran 20u 400m uic
meas tran adc_max max v(adc)
meas tran adcb_max max v(adc_b)
meas tran adc_min min v(adc)
meas tran adc_end find v(adc) at=400m
wrdata out/powerup.txt v(adc) v(adc_b) v(drain) v(vdd)
.endc
.end
"""
# V9/V33 are DC in the core netlist; give them a PWL for this one run
core = open(os.path.join(HERE, "mixer_core.inc")).read()
core_pu = (core.replace("V9   vdd  0 DC 9", "V9   vdd  0 PWL(0 0 1m 9)")
               .replace("V33  v33  0 DC 3.3", "V33  v33  0 PWL(0 0 1m 3.3)")
               .replace("PULSE(0 3.3 0 2n 2n {0.5/flo-2n} {1/flo})", "DC 0"))
with open(os.path.join(HERE, "_core_pu.inc"), "w") as fh:
    fh.write(core_pu)
deck = deck.replace("mixer_core.inc", "_core_pu.inc")
try:
    p = ngspice("powerup", deck)
finally:
    os.remove(os.path.join(HERE, "_core_pu.inc"))
log("\n== Power-up (both rails 0 -> on in 1 ms, caps discharged) ==")
log(f"ADC pin max {p['adc_max']:.3f} V (clamp node before R8: {p['adcb_max']:.3f} V), "
    f"min {p['adc_min']:.3f} V, at 400 ms {p['adc_end']:.3f} V")
t, (adc, adcb, drn, vdd) = load("powerup.txt", 4)
fig, ax = plt.subplots(figsize=(8, 4))
ax.plot(t * 1e3, drn, color=GRID, lw=2, label="drain")
ax.plot(t * 1e3, adcb, color=C2, label="adc_b (before R8)")
ax.plot(t * 1e3, adc, color=C1, label="ADC pin GP26")
ax.axhline(3.3, color=INK2, lw=1, ls="--")
ax.set(xlabel="time (ms)", ylabel="V", title="Power-up: BAT54S clamp holds GP26 near 3.3 V while C6 charges")
ax.legend(loc="right")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "powerup.png"), dpi=130)
plt.close(fig)

with open(os.path.join(PLOTS, "results.txt"), "w") as fh:
    fh.write("\n".join(results) + "\n")
print("\nplots and results.txt written to", os.path.abspath(PLOTS))
