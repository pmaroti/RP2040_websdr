#!/usr/bin/env python3
"""Simulate the 40 m and 20 m bandpass filters in ngspice and draw the plots.

Usage (from anywhere):   python3 bandpass/sim/run.py
Needs: ngspice on PATH, numpy, matplotlib.
Raw ngspice output goes to sim/out/, plots to plots/, numbers to plots/results.txt.

What it does:
  step 1  nominal response of both filters between 50 ohm source and load:
          S21 (transmission) and S11 (input reflection), 2 ... 50 MHz
  step 2  how much the coil quality factor QU matters (75 / 150 / 300)
  step 3  tolerance check: 100 filters with every L and C picked at random
          within +/-5 %, to see how far a real build can be off before tuning

How S21 and S11 are measured: the source is an ideal AC generator of 2 V in
series with 50 ohm, the load is 50 ohm. With a perfect match the load then
gets 1 V, so S21 in dB is simply the dB value of the output voltage.
S11 = (Zin - 50) / (Zin + 50), where Zin = V(in) / I(in) is the impedance
looking into the filter.
"""
import os
import subprocess
import numpy as np
import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")   # draw into files only, no window
import matplotlib.pyplot as plt

# ----------------------------------------------------------------- folders
HERE = os.path.dirname(os.path.abspath(__file__))   # bandpass/sim/
OUT = os.path.join(HERE, "out")                     # raw ngspice data
PLOTS = os.path.join(HERE, "..", "plots")           # plots + results.txt
os.makedirs(OUT, exist_ok=True)
os.makedirs(PLOTS, exist_ok=True)

# ----------------------------------------------------------------- the two filters
# name: (subcircuit, include file, centre frequency, band edges in Hz)
FILTERS = {
    "40m": ("BPF40M", "bpf_40m.inc", 7.1e6, (7.0e6, 7.2e6)),
    "20m": ("BPF20M", "bpf_20m.inc", 14.175e6, (14.0e6, 14.35e6)),
}

# ----------------------------------------------------------------- chart styling
# same palette and style as mixer/sim/run.py
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BAND = "#eceae4"        # shading for the amateur bands
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
    """Write the deck into sim/ and run ngspice in batch mode; raise on errors.

    The deck is written next to the .inc files so their relative paths work,
    and ngspice runs with sim/ as working folder so "wrdata out/..." lands in
    sim/out/.
    """
    path = os.path.join(HERE, f"_{name}.cir")
    with open(path, "w") as f:
        f.write(deck)
    r = subprocess.run(["ngspice", "-b", path], cwd=HERE, capture_output=True, text=True)
    os.remove(path)
    if r.returncode != 0 or "rror" in r.stdout:          # "Error" / "error"
        bad = [l for l in (r.stdout + r.stderr).splitlines() if "rror" in l]
        raise RuntimeError(f"ngspice {name} failed:\n" + "\n".join(bad[:20]))


def ac_run(name, instances, f1=2e6, f2=50e6, points=4000):
    """AC-sweep several filter instances at once, each in its own 50 ohm test set.

    instances: list of (include file, subcircuit name, extra parameters)
    Returns (frequency array, list of complex S21 arrays, list of complex S11 arrays).

    Putting all instances in one deck is much faster than one ngspice run per
    filter: ngspice solves them together in a single sweep.
    """
    incs = sorted({inc for inc, _, _ in instances})
    lines = [f"* {name}"] + [f".include {inc}" for inc in incs]
    vecs = []
    for k, (_, sub, params) in enumerate(instances):
        lines += [
            f"VS{k} src{k} 0 AC 2",            # 2 V generator ...
            f"RS{k} src{k} in{k} 50",          # ... with 50 ohm source resistance
            f"X{k} in{k} out{k} {sub} {params}",
            f"RL{k} out{k} 0 50",              # 50 ohm load
        ]
        # S21 = V(out) (because of the 2 V source, see the docstring);
        # Zin = V(in) / current into the filter; the current through VSk
        # flows from + to - inside the source, so the current into the
        # filter is -i(VSk).
        vecs.append(f"v(out{k})")
        vecs.append(f"v(in{k})/(-i(vs{k}))")
    lines += [
        ".control",
        # log sweep with many points so narrow features are resolved
        f"ac dec {points // int(np.log10(f2 / f1) + 1)} {f1} {f2}",
        # wrdata on complex vectors writes real and imaginary parts
        f"wrdata out/{name}.txt " + " ".join(vecs),
        ".endc", ".end", ""]
    ngspice(name, "\n".join(lines))
    d = np.loadtxt(os.path.join(OUT, f"{name}.txt"))
    # complex wrdata layout per vector: frequency, real, imaginary
    f = d[:, 0]
    s21, s11 = [], []
    for k in range(len(instances)):
        v = d[:, 6 * k + 1] + 1j * d[:, 6 * k + 2]
        z = d[:, 6 * k + 4] + 1j * d[:, 6 * k + 5]
        s21.append(v)
        s11.append((z - 50) / (z + 50))
    return f, s21, s11


def db(x):
    """Magnitude in dB (20 log10 |x|), safe for zero."""
    return 20 * np.log10(np.abs(x) + 1e-15)


def edges_3db(f, s21_db):
    """Lower and upper -3 dB frequencies around the peak of a bandpass response."""
    i = int(np.argmax(s21_db))
    lim = s21_db[i] - 3
    lo = np.where(s21_db[:i] < lim)[0]
    hi = np.where(s21_db[i:] < lim)[0]
    flo = f[lo[-1]] if len(lo) else np.nan
    fhi = f[i + hi[0]] if len(hi) else np.nan
    return flo, fhi


def at(f, y, f0):
    """Value of y at frequency f0 (linear interpolation)."""
    return float(np.interp(f0, f, y))


# ------------------------------------------------------- step 1: nominal response
inst = [(inc, sub, "") for sub, inc, _, _ in FILTERS.values()]
f, s21, s11 = ac_run("nominal", inst)
log("== Nominal response, 50 ohm source and load, coil QU = 150 ==")
for (band, (sub, inc, f0, (b1, b2))), h, r in zip(FILTERS.items(), s21, s11):
    g = db(h)
    flo, fhi = edges_3db(f, g)
    other = 14.175e6 if band == "40m" else 7.1e6          # the other band
    log(f"{band}: peak {g.max():5.2f} dB at {f[np.argmax(g)]/1e6:.3f} MHz, "
        f"-3 dB {flo/1e6:.3f} ... {fhi/1e6:.3f} MHz (BW {(fhi-flo)/1e3:.0f} kHz)")
    log(f"      band edges {b1/1e6:.3f} / {b2/1e6:.3f} MHz: {at(f, g, b1):5.2f} / {at(f, g, b2):5.2f} dB;"
        f"  S11 in band <= {max(at(f, db(r), x) for x in np.linspace(b1, b2, 9)):5.1f} dB")
    # A square-wave LO also mixes at 3 x f_LO: a signal there would land on
    # the same IF. The filter's job is to attenuate it.
    log(f"      at the other band ({other/1e6:.2f} MHz) {at(f, g, other):6.1f} dB,"
        f"  at 3 x f0 ({3*f0/1e6:.1f} MHz, LO 3rd harmonic) {at(f, g, 3*f0):6.1f} dB")

fig, ax = plt.subplots(1, 2, figsize=(11, 4))
cols = {"40m": C1, "20m": C2}
for (band, (_, _, f0, (b1, b2))), h, r in zip(FILTERS.items(), s21, s11):
    for a in ax:
        a.axvspan(b1 / 1e6, b2 / 1e6, color=BAND, zorder=0)
    ax[0].plot(f / 1e6, db(h), color=cols[band], label=f"{band} filter")
    ax[1].plot(f / 1e6, db(r), color=cols[band], label=f"{band} filter")
ax[0].set(xscale="log", xlim=(2, 50), ylim=(-70, 2), xlabel="frequency (MHz)",
          ylabel="S21 (dB)", title="Transmission, 50 ohm in and out (bands shaded)")
ax[1].set(xscale="log", xlim=(2, 50), ylim=(-40, 0), xlabel="frequency (MHz)",
          ylabel="S11 (dB)", title="Input match (lower is better)")
for a in ax:
    a.set_xticks([2, 3, 5, 7, 10, 14, 20, 30, 50])
    a.set_xticklabels(["2", "3", "5", "7", "10", "14", "20", "30", "50"])
    a.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())   # no extra labels
    a.legend(loc="lower left")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "bpf_response.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 2: coil Q
# The coils are the lossy parts. Lower QU = more insertion loss and a
# rounder passband. QU about 150 is typical for T50 toroids at HF.
log("\n== Effect of coil quality factor QU ==")
qus = [75, 150, 300]
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
for j, (band, (sub, inc, f0, (b1, b2))) in enumerate(FILTERS.items()):
    fz, hz, _ = ac_run(f"qu_{band}", [(inc, sub, f"qu={q}") for q in qus],
                       f1=f0 * 0.75, f2=f0 * 1.3, points=3000)
    ax[j].axvspan(b1 / 1e6, b2 / 1e6, color=BAND, zorder=0)
    for q, h, col in zip(qus, hz, (C2, C1, C3)):
        g = db(h)
        ax[j].plot(fz / 1e6, g, color=col, label=f"QU = {q}")
        log(f"{band} QU={q:3d}: loss at f0 {at(fz, g, f0):5.2f} dB, "
            f"worst in band {min(at(fz, g, b1), at(fz, g, b2)):5.2f} dB")
    ax[j].set(ylim=(-25, 1), xlabel="frequency (MHz)", ylabel="S21 (dB)",
              title=f"{band} passband vs coil Q (band shaded)")
    ax[j].legend(loc="lower center")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "bpf_coil_q.png"), dpi=130)
plt.close(fig)

# ------------------------------------------------------- step 3: tolerances
# 100 random builds per filter: every capacitor and inductor gets its own
# random error, uniformly within +/-5 % (typical NP0 capacitors and a toroid
# wound to the turns count without adjustment). The plot shows how far the
# passband can move before tuning; the README explains how to tune it.
log("\n== Tolerance check: 100 random builds, all L and C within +/-5 % ==")
rng = np.random.default_rng(1)          # fixed seed: same result every run
N = 100
fig, ax = plt.subplots(1, 2, figsize=(11, 4))
nominal = {"40m": dict(ce=100e-12, cc=22e-12, cs=220e-12, l=1.5e-6),
           "20m": dict(ce=39e-12, cc=6.8e-12, cs=82e-12, l=1.0e-6)}
for j, (band, (sub, inc, f0, (b1, b2))) in enumerate(FILTERS.items()):
    nv = nominal[band]
    inst = []
    for _ in range(N):
        e = rng.uniform(0.95, 1.05, 7)          # one random factor per part
        p = (f"ce1={nv['ce']*e[0]:.4g} ce2={nv['ce']*e[1]:.4g} cc={nv['cc']*e[2]:.4g} "
             f"cs1={nv['cs']*e[3]:.4g} cs2={nv['cs']*e[4]:.4g} "
             f"l1={nv['l']*e[5]:.4g} l2={nv['l']*e[6]:.4g}")
        inst.append((inc, sub, p))
    fz, hz, _ = ac_run(f"tol_{band}", inst, f1=f0 * 0.75, f2=f0 * 1.3, points=2000)
    G = np.array([db(h) for h in hz])
    # worst loss of each build at either band edge
    worst = np.minimum(np.array([at(fz, g, b1) for g in G]),
                       np.array([at(fz, g, b2) for g in G]))
    centres = np.array([fz[np.argmax(g)] for g in G])
    ax[j].axvspan(b1 / 1e6, b2 / 1e6, color=BAND, zorder=0)
    for g in G:
        ax[j].plot(fz / 1e6, g, color=C1, lw=0.5, alpha=0.25)
    # nominal response from step 1, interpolated onto this frequency grid
    ax[j].plot(fz / 1e6, np.interp(fz, f, db(s21[j])), color=INK, lw=1.6,
               label="nominal values")
    ax[j].set(ylim=(-25, 1), xlabel="frequency (MHz)", ylabel="S21 (dB)",
              title=f"{band}: 100 builds with +/-5 % parts (before tuning)")
    ax[j].legend(loc="lower center")
    log(f"{band}: peak frequency {centres.min()/1e6:.3f} ... {centres.max()/1e6:.3f} MHz; "
        f"worst band-edge loss {worst.min():5.1f} dB, "
        f"{np.mean(worst > -3)*100:.0f} % of builds lose < 3 dB at both band edges")
fig.tight_layout()
fig.savefig(os.path.join(PLOTS, "bpf_tolerance.png"), dpi=130)
plt.close(fig)

with open(os.path.join(PLOTS, "results.txt"), "w") as fh:
    fh.write("\n".join(results) + "\n")
print("\nplots and results.txt written to", os.path.abspath(PLOTS))
