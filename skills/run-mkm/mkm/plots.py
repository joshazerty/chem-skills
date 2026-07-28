"""Publication-style figures. Matplotlib only, no seaborn; Agg backend so it
runs headless on a cluster. Every function writes a PNG and returns its path."""
from __future__ import annotations

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({
    "figure.dpi": 130, "savefig.dpi": 130, "font.size": 11,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.25,
})


def concentration_profile(model, path, tmax=None, npts=400):
    """Transient concentrations vs time. Two panels: reservoirs (linear) and the
    catalyst speciation (log-log, where the interesting dynamics live)."""
    if tmax is None:
        tmax = model.cfg.t_relax
    ts = np.concatenate([[0.0], np.logspace(-12, np.log10(tmax), npts)])
    t, C = model.transient(ts)
    tt = np.clip(t, 1e-13, None)
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(9.6, 4.0))
    for i, s in enumerate(model.net.species):
        if s.fixed:
            ax0.plot(tt, C[i], "--", lw=1.8, label=s.name)
        else:
            floor = np.clip(C[i], 1e-16, None)
            ax1.plot(tt, floor, "-", lw=1.8, label=s.name)
    ax0.set_xscale("log"); ax0.set_xlabel("time / s"); ax0.set_ylabel("conc / M")
    ax0.set_title("reservoirs"); ax0.legend(fontsize=8, frameon=False)
    ax1.set_xscale("log"); ax1.set_yscale("log")
    ax1.set_xlabel("time / s"); ax1.set_ylabel("conc / M")
    ax1.set_title("catalyst speciation"); ax1.legend(fontsize=8, frameon=False)
    fig.suptitle(f"{model.net.name}: transient to steady state")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return path


def drc_bar(xrc_dict, path, title="Degree of rate control"):
    labels = list(xrc_dict.keys())
    vals = [xrc_dict[k] for k in labels]
    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    colors = ["#c1121f" if v >= 0 else "#003049" for v in vals]
    ax.bar(labels, vals, color=colors)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel(r"$X_{\mathrm{RC}}$"); ax.set_title(title)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return path


def energy_span_profile(prof, span, path):
    """Sawtooth free-energy profile of the cycle with TDTS/TDI marked."""
    from . import constants as C
    I = C.from_j_per_mol(prof["I"], "kcal/mol")
    T = C.from_j_per_mol(prof["T"], "kcal/mol")
    Il, Tl = prof["I_labels"], prof["T_labels"]
    N = len(I)
    xs, ys, labs = [], [], []
    for p in range(N):
        xs += [2 * p, 2 * p + 1]; ys += [I[p], T[p]]; labs += [Il[p], Tl[p]]
    xs.append(2 * N); ys.append(I[0] + C.from_j_per_mol(prof["dG_r"], "kcal/mol"))
    labs.append(Il[0] + "'")
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(xs, ys, "-o", color="#264653", lw=1.6, ms=4)
    for x, y, l in zip(xs, ys, labs):
        ax.annotate(l, (x, y), textcoords="offset points", xytext=(0, 6),
                    ha="center", fontsize=8)
    ax.set_ylabel("G / kcal mol$^{-1}$"); ax.set_xticks([])
    ax.set_title(f"Energetic span  dE = {span['dE_kcal']:.1f} kcal/mol  "
                 f"(TDTS {span['TDTS']}, TDI {span['TDI']})")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return path


def uncertainty_hist(mc, path):
    tofs = mc["tofs"]; tofs = tofs[tofs > 0]
    fig, ax = plt.subplots(figsize=(5.8, 3.8))
    ax.hist(np.log10(tofs), bins=40, color="#2a9d8f", alpha=0.85)
    ax.axvline(np.log10(mc["base_tof"]), color="#e63946", lw=2,
               label=f"nominal {mc['base_tof']:.2e}")
    ax.set_xlabel(r"$\log_{10}$ TOF / s$^{-1}$"); ax.set_ylabel("count")
    ax.set_title(f"TOF uncertainty (sigma={mc['sigma']} {mc['unit']}, "
                 f"n={mc['n']}); {100*mc['frac_within_decade']:.0f}% within a decade")
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return path


def sensitivity_heatmap(names, matrix, rxn_labels, path):
    fig, ax = plt.subplots(figsize=(1.2 + 0.5 * len(rxn_labels), 0.9 + 0.4 * len(names)))
    vmax = np.nanmax(np.abs(matrix)) or 1.0
    im = ax.imshow(matrix, cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(rxn_labels))); ax.set_xticklabels(rxn_labels, rotation=45, ha="right")
    ax.set_yticks(range(len(names))); ax.set_yticklabels(names)
    ax.set_title(r"$\partial \ln C_i\, /\, \partial(-G_{TS,j}/RT)$")
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout(); fig.savefig(path); plt.close(fig)
    return path
