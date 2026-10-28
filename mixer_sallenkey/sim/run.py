#!/usr/bin/env python3
"""Run every ngspice simulation of the BF961 mixer with the LM358 Sallen-Key
low-pass, and draw the plots.

Usage (from anywhere):   python3 mixer_sallenkey/sim/run.py
Needs: ngspice on PATH, numpy, matplotlib, and the sibling folder ../mixer/
(for the shared BF961 model). Raw ngspice output goes to sim/out/, plots to
plots/, numbers to plots/results.txt.

What it does, step by step:
  model check  LM358 model alone: DC gain, gain-bandwidth, slew rate, swing
  step 1       AC response of the filter alone, Sallen-Key vs the simple RC
  step 2       find the average operating point with the LO running
  step 3       one full mixer run (LO 7.1 MHz, RF 7.103 MHz -> 3 kHz IF),
               waveforms and the spectrum the 25 kS/s ADC sees
  step 4       conversion gain versus IF frequency, including IFs above the
               12.5 kHz Nyquist frequency (those alias into the IF band)
  step 5       large RF input: compression, and the ADC pin stays in 0..3.3 V
  step 6       power-up from 0 V: the clamp protects the ADC pin
  step 7       the same circuit on a 5 V supply (with R3 = 470 ohm)

How it talks to ngspice: for every run it writes a small top-level deck
(parameters + ".include mixer_sk_core.inc" + a .control block), runs
"ngspice -b" on it, reads the ".meas" results from ngspice's printed output,
and reads the waveforms from the text files that "wrdata" writes into sim/out/.
"""
import os
import subprocess
import numpy as np
import matplotlib
matplotlib.use("Agg")   # draw into files only, no window (works without a display)
import matplotlib.pyplot as plt

# ----------------------------------------------------------------- folders
HERE = os.path.dirname(os.path.abspath(__file__))   # mixer_sallenkey/sim/
OUT = os.path.join(HERE, "out")                     # raw ngspice waveform files
PLOTS = os.path.join(HERE, "..", "plots")           # finished plots + results.txt
os.makedirs(OUT, exist_ok=True)
os.makedirs(PLOTS, exist_ok=True)

# ----------------------------------------------------------------- test conditions
FLO = 7.100e6          # LO frequency on GP8 (40 m band), Hz
ADC_FS = 25e3          # RP2040 ADC sample rate used by the firmware, samples/s
ARF = 10e-3            # RF test tone amplitude, V peak at the antenna
FIF = 3e3              # IF of the reference run, Hz

# ----------------------------------------------------------------- chart styling
# Same palette as the simple version: C1 blue = this version (Sallen-Key),
# C2 orange = the simple RC version for comparison, C3 aqua = third series.
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

# Every result line is printed and also collected here; at the end the list
# is written to plots/results.txt.
results = []


def log(line):
    """Print a result line and remember it for results.txt."""
    print(line)
    results.append(line)


def ngspice(name, deck):
    """Write deck to sim/_<name>.cir and run it in batch mode.

    The deck is written into sim/ so that the relative ".include" paths
    resolve. Returns a dict {measurement name: value} parsed from the
    ".meas" lines, which ngspice prints as "name = value ...".
    Raises RuntimeError if ngspice fails or prints an error.
    """
    path = os.path.join(HERE, f"_{name}.cir")
    with open(path, "w") as f:
        f.write(deck)
    # cwd=HERE so that "wrdata out/..." in the deck lands in sim/out/
    r = subprocess.run(["ngspice", "-b", path], cwd=HERE, capture_output=True, text=True)
    os.remove(path)
    if r.returncode != 0 or "rror" in r.stdout:          # "Error" or "error"
        bad = [l for l in (r.stdout + r.stderr).splitlines() if "rror" in l]
        raise RuntimeError(f"ngspice {name} failed:\n" + "\n".join(bad[:20]))
    # pick out lines like "vd_avg  =  6.65e+00 from= ..." : a single word
    # before the first "=", and a number right after it
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
    """wrdata writes (x, y) pairs per vector; return x and the y columns.

    For "wrdata file a b c" ngspice writes the columns x a x b x c, so the
    y values are in the odd columns 1, 3, 5, ...
    """
    d = np.loadtxt(os.path.join(OUT, fname))
    return d[:, 0], [d[:, 2 * i + 1] for i in range(ncols)]


def tone(t, y, f):
    """Amplitude of the f component over a whole number of periods (lock-in).

    ngspice uses a variable time step, so the samples are first resampled on
    an even time grid. The signal is then multiplied by exp(-j*2*pi*f*t) and
    averaged over an integer number of periods of f: everything not at f
    averages out, and 2*|mean| is the peak amplitude at f.
    """
    tu = np.linspace(t[0], t[-1], 200000)
    yu = np.interp(tu, t, y)
    n = int((tu[-1] - tu[0]) * f)                       # whole periods available
    keep = tu <= tu[0] + n / f
    return 2 * abs(np.mean(yu[keep] * np.exp(-2j * np.pi * f * tu[keep])))


def alias(f, fs=ADC_FS):
    """Frequency at which a tone at f shows up after sampling at fs."""
    f = f % fs
    return fs - f if f > fs / 2 else f


# ------------------------------------------------------------- model check
# LM358 model alone, on 9 V:
#   XA: follower closed through a 1 MH inductor and 1 F capacitor. At DC the
#       loop is closed (sets the operating point at 2 V), at AC the loop is
#       open, so v(outa) = open-loop gain. -> DC gain and unity-gain frequency
#   XB: follower driven by a 0.5 V -> 6.5 V step              -> slew rate
#   XC: follower driven by a 1 kHz sine from -2 V to 10 V     -> output swing
deck = """* LM358 model check
.include ../models/lm358.lib
V1 vcc 0 9
XA inp fb vcc 0 outa LM358
VIN inp 0 DC 2 AC 1
L1 outa fb 1e6
C1 fb 0 1
XB pin outb vcc 0 outb LM358
VP pin 0 PULSE(0.5 6.5 10u 10n 10n 40u 100u)
XC sin outc vcc 0 outc LM358
VS sin 0 SIN(4 6 1k)
.control
op
meas_icc = -i(v1)
print meas_icc
ac dec 20 1 10meg
meas ac a0 find vdb(outa) at=1
meas ac fu when vdb(outa)=0
tran 50n 2m
meas tran sr trig v(outb) val=2 rise=1 targ v(outb) val=5 rise=1
meas tran vmax max v(outc)
meas tran vmin min v(outc)
.endc
.end
"""
m = ngspice("lm358", deck)
log("== LM358 model vs datasheet (one amplifier, 9 V supply) ==")
log(f"DC open-loop gain    {m['a0']:6.1f} dB    datasheet 100 dB typ")
log(f"unity-gain frequency {m['fu']/1e6:6.2f} MHz   datasheet 0.7 MHz (TI)")
log(f"slew rate            {3/m['sr']/1e6:6.2f} V/us  datasheet 0.3 V/us")
log(f"output swing         {m['vmin']*1e3:4.0f} mV .. {m['vmax']:.2f} V   "
    f"datasheet ~5 mV .. Vcc-1.5 V")

# ------------------------------------------------- step 1: filter AC response
# The two filters on their own, driven like the BF961 drives them: a source
# behind the 1 kohm drain load R4 (the BF961's own output resistance is much
# higher and is left out). Both include the C6 / R6 / R7 bias network.
#   "sk_*" nodes: this version (C5 roofing + Sallen-Key + LM358 follower)
#   "rc_*" nodes: the simple version's R5 10k / C5 3.3n low-pass
# vdb(...) is relative to the 1 V AC source, i.e. the filter's own gain.
deck = """* filter AC response
.include ../models/lm358.lib
V33 v33 0 3.3
V9  vdd 0 9
VIN x 0 DC 6.6 AC 1
* --- this version ---
R4  x drain 1k
C5  drain 0 4.7n
C6  drain sk_in 1u
R6  v33 sk_in 100k
R7  sk_in 0 100k
R9  sk_in sk_mid 10k
R10 sk_mid sk_p 10k
C7  sk_mid sk_out 3.9n
C8  sk_p 0 1.8n
XU1A sk_p sk_out vdd 0 sk_out LM358
R11 sk_out 0 10k
* --- simple version ---
R4r x rdrain 1k
R5r rdrain rc_filt 10k
C5r rc_filt 0 3.3n
C6r rc_filt rc_b 1u
R6r v33 rc_b 100k
R7r rc_b 0 100k
.control
ac dec 50 10 20meg
meas ac sk_1k  find vdb(sk_out) at=1k
meas ac sk_f3  when vdb(sk_out)=-3.0
meas ac sk_12k find vdb(sk_out) at=12.5k
meas ac sk_25k find vdb(sk_out) at=25k
meas ac sk_lo  find vdb(sk_out) at=7.1meg
meas ac rc_1k  find vdb(rc_b) at=1k
meas ac rc_12k find vdb(rc_b) at=12.5k
meas ac rc_25k find vdb(rc_b) at=25k
meas ac rc_lo  find vdb(rc_b) at=7.1meg
let rc_f3_level = vdb(rc_b)[0] - 3
meas ac rc_f3 when vdb(rc_b)=rc_f3_level
wrdata out/filter_ac.txt vdb(sk_out) vdb(rc_b)
.endc
.end
"""
m = ngspice("filter_ac", deck)
log("\n== Filter response alone (drain -> filter output, incl. bias network) ==")
log(f"                       Sallen-Key   simple RC")
log(f"gain at 1 kHz          {m['sk_1k']:7.1f} dB   {m['rc_1k']:7.1f} dB")
log(f"-3 dB frequency        {m['sk_f3']/1e3:7.1f} kHz  {m['rc_f3']/1e3:7.1f} kHz (-3 dB from its DC gain)")
log(f"at 12.5 kHz (Nyquist)  {m['sk_12k']:7.1f} dB   {m['rc_12k']:7.1f} dB")
log(f"at 25 kHz (= fs)       {m['sk_25k']:7.1f} dB   {m['rc_25k']:7.1f} dB")
log(f"at 7.1 MHz (LO)        {m['sk_lo']:7.1f} dB   {m['rc_lo']:7.1f} dB")
f, (sk, rc) = load("filter_ac.txt", 2)
fig, ax = plt.subplots(figsize=(8, 4.2))
ax.plot(f, rc, color=C2, label="simple RC (mixer/)")
ax.plot(f, sk, color=C1, label="LM358 Sallen-Key (this version)")
ax.axvline(ADC_FS / 2, color=INK2, lw=1, ls="--")
ax.annotate("Nyquist 12.5 kHz:\nabove this, signals alias\ninto the IF band",
            (ADC_FS / 2, -45), xytext=(5, 0), textcoords="offset points",
            fontsize=8, color=INK2)
ax.set(xscale="log", xlim=(10, 20e6), ylim=(-110, 5), xlabel="frequency (Hz)",
       ylabel="gain (dB)", title="Filter alone, from the drain to the filter output")
ax.legend(loc="lower left")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "filter_response.png"), dpi=130)
plt.close(fig)


# ----------------------------------------------------- shared deck pieces
def mixer_deck(name, flo, fif, arf, tstop, vs0, vd0, tstep="4n", core="mixer_sk_core.inc"):
    """Build a top-level deck for one transient run of the mixer.

    name        used for the deck file and for out/<name>.txt
    flo         LO frequency, Hz
    fif         wanted IF, Hz; the RF tone is put at flo + fif
    arf         RF amplitude, V peak
    tstop       simulated time, s
    vs0, vd0    starting source and drain voltages for .ic (from step 2)
    tstep       maximum time step; 4 ns gives ~35 points per 7.1 MHz period
    core        circuit file (a modified copy for the 5 V run)

    The filter, the ADC pin and the LM358's internal integrator node
    (xu1a.int, which sets its output) start at their 1.65 V bias. The .meas lines
    average over the last 40 % of the run, after the start-up.
    """
    return f"""* {name}
.param flo={flo} frf={flo + fif} arf={arf}
.include {core}
.ic v(src)={vs0} v(drain)={vd0} v(sk_in)=1.65 v(sk_mid)=1.65 v(sk_p)=1.65
+ v(sk_out)=1.65 v(adc)=1.65 v(xu1a.int)=1.65
.control
save v(adc) v(drain) v(sk_in) v(sk_out) v(src)
tran {tstep} {tstop}
meas tran vd_avg avg v(drain) from={tstop*0.6} to={tstop}
meas tran vs_avg avg v(src) from={tstop*0.6} to={tstop}
meas tran adc_avg avg v(adc) from={tstop*0.6} to={tstop}
meas tran adc_max max v(adc)
meas tran adc_min min v(adc)
wrdata out/{name}.txt v(adc) v(drain) v(sk_in) v(sk_out)
.endc
.end
"""


# ------------------------------------------- step 2: settle the operating point
# The 220 ohm x 47 uF source bypass needs ~50 ms to settle. The averages
# depend on the LO duty cycle, not its frequency, so settle with a 100 kHz
# LO (cheap), then start every 7.1 MHz run from that point via .ic.
op = ngspice("op", mixer_deck("op", 100e3, 0, 0, 60e-3, 0.5, 6.5, tstep="100n"))
VS0, VD0 = op["vs_avg"], op["vd_avg"]
log("\n== Operating point, LO running (50 % duty) ==")
log(f"V(source) avg        {VS0:6.3f} V  -> I avg = {VS0/220*1e3:.2f} mA")
log(f"V(drain)  avg        {VD0:6.3f} V")

# ------------------------------------------- step 3: the reference mixer run
# LO 7.1 MHz, RF 7.103 MHz at 10 mV peak -> 3 kHz IF; 10.5 ms of simulated
# time so the 25 kS/s spectrum has enough samples (250) to resolve it.
r = ngspice("main", mixer_deck("main", FLO, FIF, ARF, 10.5e-3, VS0, VD0))
t, (adc, drn, skin, skout) = load("main.txt", 4)
sel = t > 0.5e-3                       # ignore the first 0.5 ms (start-up)
a_if = tone(t[sel], adc[sel], FIF)     # IF amplitude at the ADC pin, V peak
gain_db = 20 * np.log10(a_if / ARF)    # conversion gain, RF in -> ADC pin
log(f"\n== Mixer, LO {FLO/1e6:.3f} MHz, RF {(FLO+FIF)/1e6:.6f} MHz @ {ARF*1e3:.0f} mV pk ==")
log(f"IF {FIF/1e3:.0f} kHz at ADC pin    {a_if*1e3:6.2f} mV pk")
log(f"conversion gain RF->ADC {gain_db:6.1f} dB   (simple RC version: 5.1 dB)")
log(f"ADC pin DC / min / max {r['adc_avg']:.3f} / {r['adc_min']:.3f} / {r['adc_max']:.3f} V")
# LO ripple: subtract a 51-point moving average (keeps the slow IF) and take
# the RMS of what is left; x 2.83 converts RMS to peak-to-peak.
ripple = adc[sel] - np.convolve(adc[sel], np.ones(51) / 51, "same")
log(f"LO ripple at ADC pin   {np.std(ripple[100:-100])*2.83*1e3:6.3f} mV pp (approx; simple RC: 1.03 mV pp)")

# Waveform plot, three panels:
#   top     drain over two LO periods: the LO switching the BF961, already
#           smoothed by the C5 roofing capacitor
#   middle  filter input (sk_in) vs filter output (sk_out) over 2.5 ms
#   bottom  ADC pin on the full 0..3.3 V range
fig, ax = plt.subplots(3, 1, figsize=(10, 7.5))
tz = (t > 1.0e-3) & (t < 1.0e-3 + 2.2 / FLO)
ax[0].plot((t[tz] - 1e-3) * 1e9, drn[tz], color=C1)
ax[0].set(ylabel="V(drain) (V)", xlabel="time (ns)",
          title="Drain over two LO periods (C5 already removes most of the LO)")
sw = (t > 0.5e-3) & (t < 3e-3)
ax[1].plot(t[sw] * 1e3, skin[sw], color="#c9c8c2", lw=0.8, label="filter input sk_in")
ax[1].plot(t[sw] * 1e3, skout[sw], color=C1, label="LM358 output sk_out")
lo_, hi_ = skin[sw].min(), skin[sw].max()
ax[1].set(ylabel="V", xlabel="time (ms)", title="Sallen-Key filter: 3 kHz IF in and out",
          ylim=(lo_ - 0.1 * (hi_ - lo_), hi_ + 0.45 * (hi_ - lo_)))   # room for the legend
ax[1].legend(loc="upper center", ncol=2, fontsize=8)
ax[2].plot(t[sw] * 1e3, adc[sw], color=C1)
ax[2].axhline(3.3, color=INK2, lw=1, ls="--")
ax[2].axhline(0, color=INK2, lw=1, ls="--")
ax[2].set(ylabel="V(adc) (V)", xlabel="time (ms)", ylim=(-0.2, 3.5),
          title=f"ADC pin (GP26): 1.65 V mid-scale + {a_if*1e3:.0f} mV pk IF, inside 0..3.3 V")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "mixer_waveforms.png"), dpi=130)
plt.close(fig)

# Spectrum the RP2040 actually sees: V(adc) sampled at 25 kS/s. Hann window,
# scaled to peak amplitude, so the y axis is dBV (0 dBV = 1 V peak).
ts = np.arange(0.5e-3, t[-1], 1 / ADC_FS)
ys = np.interp(ts, t, adc)
w = np.hanning(len(ys))
sp = np.abs(np.fft.rfft((ys - ys.mean()) * w)) * 2 / w.sum()
fs_ax = np.fft.rfftfreq(len(ys), 1 / ADC_FS)
fig, ax = plt.subplots(figsize=(7, 3.8))
ax.plot(fs_ax / 1e3, 20 * np.log10(sp + 1e-9), color=C1, marker="o", ms=3)
ax.set(xlabel="frequency (kHz)", ylabel="dBV", ylim=(-110, 0),
       title="V(adc) sampled at 25 kS/s (what the ADC gets)")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "mixer_spectrum.png"), dpi=130)
plt.close(fig)

# ------------------------------------------- step 4: IF response and aliasing
# Repeat the mixer run with the RF tone at several offsets from the LO. Each
# run measures the IF amplitude at the ADC pin (before sampling). Offsets
# above 12.5 kHz are signals the receiver should NOT hear: after sampling at
# 25 kS/s they fold back to alias(f) inside the IF band. The lower the gain
# there, the better the filter protects the IF band.
log("\n== Conversion gain vs IF frequency (RF->ADC pin) ==")
fifs = [0.3e3, 1e3, 2e3, 3e3, 5e3, 7e3, 10e3, 12.5e3, 15e3, 20e3, 30e3]
gains = []
for fi in fifs:
    tstop = 0.4e-3 + max(1.2e-3, 3 / fi)   # 0.4 ms settling + >= 3 IF periods
    ngspice("ifsweep", mixer_deck("ifsweep", FLO, fi, ARF, tstop, VS0, VD0))
    t, (adc, *_) = load("ifsweep.txt", 4)
    s = t > 0.4e-3
    g = 20 * np.log10(tone(t[s], adc[s], fi) / ARF)
    gains.append(g)
    note = f"   -> aliases to {alias(fi)/1e3:.1f} kHz" if fi > ADC_FS / 2 else ""
    log(f"IF {fi/1e3:5.1f} kHz   {g:6.1f} dB{note}")
# Plot: transient points on top of the AC curves from step 1, each curve
# shifted to match the mixer's low-frequency conversion gain, so the shape
# of the two filters can be compared directly at the ADC pin.
f, (sk, rc) = load("filter_ac.txt", 2)
i1k = np.argmin(abs(f - 1e3))
rc_gain_1k = 5.9                       # simple RC version, transient, 1 kHz (mixer/plots/results.txt)
fig, ax = plt.subplots(figsize=(7.5, 4.2))
band = (f > 100) & (f < 40e3)
ax.plot(f[band] / 1e3, rc[band] - rc[i1k] + rc_gain_1k, color=C2,
        label="simple RC version (AC, scaled)")
ax.plot(f[band] / 1e3, sk[band] - sk[i1k] + gains[1], color=C1, lw=1, alpha=0.6,
        label="Sallen-Key (AC, scaled)")
ax.plot(np.array(fifs) / 1e3, gains, color=C1, marker="o", ms=6, lw=0,
        label="Sallen-Key, ngspice mixer transient")
ax.axvline(ADC_FS / 2e3, color=INK2, lw=1, ls="--")
ax.annotate("Nyquist 12.5 kHz", (ADC_FS / 2e3, max(gains) + 1), xytext=(4, 0),
            textcoords="offset points", fontsize=8, color=INK2, va="top")
ax.set(xscale="log", xlabel="IF frequency (kHz)", ylabel="RF -> ADC gain (dB)",
       title=f"Conversion gain vs IF, LO {FLO/1e6:.1f} MHz")
ax.legend(loc="lower left", fontsize=8)
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "if_response.png"), dpi=130)
plt.close(fig)

# ------------------------------------------- step 5: large RF input, ADC range
# RF amplitude from 1 mV to 1 V peak: where does the mixer compress, and
# does the ADC pin stay inside 0..3.3 V? (The LM358 itself could swing to
# 7.5 V; the BAT54S clamp is the safety net if it ever does.)
log("\n== Large RF input: ADC pin stays inside 0..3.3 V ==")
amps = [1e-3, 10e-3, 30e-3, 100e-3, 300e-3, 1.0]
outs = []
for a in amps:
    ngspice("big", mixer_deck("big", FLO, FIF, a, 1.6e-3, VS0, VD0))
    t, (adc, *_) = load("big.txt", 4)
    s = t > 0.5e-3
    outs.append(tone(t[s], adc[s], FIF))
    log(f"RF {a*1e3:7.1f} mV pk -> IF {outs[-1]*1e3:7.1f} mV pk, "
        f"ADC min {adc[s].min():.3f} V max {adc[s].max():.3f} V")
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

# ------------------------------------------- step 6: power-up transient
# 9 V and 3.3 V ramp from 0 in 1 ms, all capacitors empty, LO idle at 0 V.
# When the drain jumps up, the empty C6 passes the jump to the filter input;
# the LM358 follows it (up to its 7.5 V ceiling) until C6 has charged. The
# BAT54S clamp and R8 must keep GP26 below the RP2040's limit
# (IOVDD + 0.5 V = 3.8 V). "uic" = start from 0 V everywhere.
core = open(os.path.join(HERE, "mixer_sk_core.inc")).read()
core_pu = (core.replace("V9   vdd  0 DC 9", "V9   vdd  0 PWL(0 0 1m 9)")
               .replace("V33  v33  0 DC 3.3", "V33  v33  0 PWL(0 0 1m 3.3)")
               .replace("PULSE(0 3.3 0 2n 2n {0.5/flo-2n} {1/flo})", "DC 0"))
assert core_pu.count("PWL") == 2 and "DC 0\n" in core_pu    # all three swaps happened
deck = """* power-up
.param flo=1meg frf=1meg arf=0
.include _core_pu.inc
.control
save v(adc) v(sk_out) v(drain) v(vdd)
tran 20u 400m uic
meas tran adc_max max v(adc)
meas tran skout_max max v(sk_out)
meas tran adc_min min v(adc)
meas tran adc_end find v(adc) at=400m
wrdata out/powerup.txt v(adc) v(sk_out) v(drain)
.endc
.end
"""
with open(os.path.join(HERE, "_core_pu.inc"), "w") as fh:
    fh.write(core_pu)
try:
    p = ngspice("powerup", deck)
finally:
    os.remove(os.path.join(HERE, "_core_pu.inc"))
log("\n== Power-up (both rails 0 -> on in 1 ms, caps discharged) ==")
log(f"LM358 output max {p['skout_max']:.3f} V, ADC pin max {p['adc_max']:.3f} V, "
    f"min {p['adc_min']:.3f} V, at 400 ms {p['adc_end']:.3f} V")
t, (adc, skout, drn) = load("powerup.txt", 3)
fig, ax = plt.subplots(figsize=(8, 4))
ax.plot(t * 1e3, drn, color=GRID, lw=2, label="drain")
ax.plot(t * 1e3, skout, color=C2, label="LM358 output (before R8)")
ax.plot(t * 1e3, adc, color=C1, label="ADC pin GP26")
ax.axhline(3.3, color=INK2, lw=1, ls="--")
ax.set(xlabel="time (ms)", ylabel="V", title="Power-up: BAT54S clamp holds GP26 near 3.3 V while C6 charges")
ax.legend(loc="right")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "powerup.png"), dpi=130)
plt.close(fig)

# ------------------------------------------- step 7: 5 V supply
# Same circuit on 5 V: mixer AND LM358 from 5 V, and R3 raised to 470 ohm
# (as recommended for the simple version on 5 V) so the BF961 keeps enough
# drain-source voltage. The LM358 output then reaches only 5 - 1.5 = 3.5 V,
# still above the ADC's 3.3 V, and its inputs work up to 3.5 V, still above
# the 1.65 V bias.
core5 = (core.replace("V9   vdd  0 DC 9", "V9   vdd  0 DC 5")
             .replace("R3   src 0 220", "R3   src 0 470"))
assert "DC 5" in core5 and "src 0 470" in core5
with open(os.path.join(HERE, "_core5.inc"), "w") as fh:
    fh.write(core5)
try:
    op5 = ngspice("op5", mixer_deck("op5", 100e3, 0, 0, 120e-3, 0.4, 3.5, tstep="100n",
                                     core="_core5.inc"))
    ngspice("main5", mixer_deck("main5", FLO, FIF, ARF, 2.4e-3, op5["vs_avg"], op5["vd_avg"],
                                core="_core5.inc"))
finally:
    os.remove(os.path.join(HERE, "_core5.inc"))
t, (adc, *_) = load("main5.txt", 4)
s = t > 0.4e-3
g5 = 20 * np.log10(tone(t[s], adc[s], FIF) / ARF)
log("\n== 5 V supply (mixer + LM358 on 5 V, R3 = 470 ohm) ==")
log(f"V(source) {op5['vs_avg']:.3f} V (I = {op5['vs_avg']/470*1e3:.2f} mA), "
    f"V(drain) {op5['vd_avg']:.3f} V")
log(f"conversion gain RF->ADC at 3 kHz {g5:6.1f} dB   (9 V: {gain_db:.1f} dB)")

# ------------------------------------------- write all numbers to one file
with open(os.path.join(PLOTS, "results.txt"), "w") as fh:
    fh.write("\n".join(results) + "\n")
print("\nplots and results.txt written to", os.path.abspath(PLOTS))
