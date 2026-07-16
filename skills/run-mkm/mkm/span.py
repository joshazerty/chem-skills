"""Energetic span model (Kozuch & Shaik) as an analytical cross-check.

For a single catalytic cycle the steady-state TOF has a closed form in terms of
the free-energy *profile* of the cycle -- the intermediates I_j and transition
states T_i laid out on one common descending reference that already contains the
per-cycle driving force dG_r. Kozuch's exact expression:

    TOF = (kB T/h) (e^{-dG_r/RT} - 1) / sum_{i,j} exp[(T_i - I_j - d_ij)/RT]
    d_ij = dG_r if the TS T_i precedes the intermediate I_j around the cycle
           (j > i), else 0

The dominant term identifies the TOF-determining TS (TDTS) and intermediate
(TDI); their separation is the energetic span dE, and TOF ~= (kB T/h) e^{-dE/RT}.
The degrees of TOF control X_TOF sum to 1 over TSs and over intermediates and
are an independent cross-check on the autodiff degree of rate control.

Assumptions: one closed cycle through the catalyst-bearing species, auto-detected
from the reactions. If the numerical MKM TOF and the span TOF disagree, the
network is not a single cycle (branching / off-cycle reservoirs) and the span
model does not apply -- the driver warns on that.

The profile is built at the *actual reservoir concentrations* via chemical
potentials mu = G_std + RT ln[C], so dE and the span TOF are directly
comparable to the numerical result, not just to the standard-state one.
"""
from __future__ import annotations

import numpy as np

from . import constants as C
from .network import Network


def detect_cycle(net: Network):
    """Return (states, edges) for the single catalytic cycle.

    states : list of catalyst-species indices in cycle order
    edges  : list of dicts {reaction_index, from, to} following reactant->product
    Raises ValueError if a clean single cycle can't be traced.
    """
    cat_idx = [i for i, s in enumerate(net.species) if s.catalyst]
    if not cat_idx:
        raise ValueError("no catalyst-bearing species; span model needs a cycle")
    catset = set(cat_idx)
    # directed edges among catalyst species from each reaction's reactant->product
    succ = {}
    for j, rx in enumerate(net.reactions):
        rc = [net.index[n] for n in rx.reactants if net.index[n] in catset]
        pc = [net.index[n] for n in rx.products if net.index[n] in catset]
        if len(rc) == 1 and len(pc) == 1:
            succ.setdefault(rc[0], []).append((pc[0], j))
    if not succ:
        raise ValueError("no reactions connect two catalyst states")
    # walk the cycle from an arbitrary catalyst state
    start = cat_idx[0]
    states, edges = [start], []
    cur, guard = start, 0
    while guard <= len(cat_idx) + 1:
        guard += 1
        nxt = succ.get(cur)
        if not nxt:
            raise ValueError(f"cycle dead-ends at {net.species[cur].name}")
        to, j = nxt[0]
        edges.append({"rxn": j, "from": cur, "to": to})
        if to == start:
            break
        states.append(to)
        cur = to
    if len(edges) != len(states):
        raise ValueError("traced path is not a closed single cycle")
    return states, edges


def _mu(net: Network, i, conc, unit_G):
    """Chemical potential of species i (J/mol) at concentration `conc` (mol/L)."""
    G = C.to_j_per_mol(net.species[i].G, unit_G)
    RT = C.R_J * net.T
    c = max(conc, 1e-300)
    return G + RT * np.log(c / net.standard_state)


def build_profile(net: Network):
    """Return dict with cycle profile levels (J/mol): I (intermediates), T (TSs),
    dG_r (per-cycle driving force), and the ordering, plus species/reaction labels."""
    states, edges = detect_cycle(net)
    unit = net.energy_unit
    RT = C.R_J * net.T
    conc = np.array([s.conc0 for s in net.species])

    off = 0.0
    I_levels, T_levels = [], []
    T_labels, I_labels = [], []
    for p, e in enumerate(edges):
        sp = e["from"]
        I_levels.append(C.to_j_per_mol(net.species[sp].G, unit) + off)
        I_labels.append(net.species[sp].name)
        rx = net.reactions[e["rxn"]]
        # reservoir (non-catalyst) reactants / products of this edge
        res_r = [net.index[n] for n in rx.reactants if not net.species[net.index[n]].catalyst]
        res_p = [net.index[n] for n in rx.products if not net.species[net.index[n]].catalyst]
        mu_r = sum(_mu(net, i, conc[i], unit) * rx.reactants[net.species[i].name] for i in res_r)
        mu_p = sum(_mu(net, i, conc[i], unit) * rx.products[net.species[i].name] for i in res_p)
        if rx.G_ts is None:
            # barrierless: TS at the higher of the two adjacent levels
            Gts_eff = max(C.to_j_per_mol(net.species[e["from"]].G, unit),
                          C.to_j_per_mol(net.species[e["to"]].G, unit) + (mu_p - mu_r))
        else:
            Gts_eff = C.to_j_per_mol(rx.G_ts, unit)
        T_levels.append(Gts_eff + off - mu_r)
        T_labels.append(rx.id)
        off = off - mu_r + mu_p
    dG_r = off  # net descent per cycle (J/mol)
    return {
        "I": np.array(I_levels), "T": np.array(T_levels), "dG_r": dG_r,
        "I_labels": I_labels, "T_labels": T_labels, "RT": RT,
    }


def energetic_span(net: Network):
    """Kozuch-Shaik energetic-span analysis on the auto-detected cycle.

    The TDI is the intermediate that, paired with the highest TS in the turnover
    that follows it, maximises the span dE -- this requires looking one full
    turnover ahead on the periodically descending profile (each period drops by
    dG_r), not merely picking the lowest intermediate. Returns dE, TDTS, TDI, the
    span TOF = (kB T/h) e^{-dE/RT}, and softmax degree-of-TOF-control weights.
    """
    prof = build_profile(net)
    I0, T0, dG_r, RT = prof["I"], prof["T"], prof["dG_r"], prof["RT"]
    N = len(I0)
    pref = C.KB_OVER_H * net.T

    # --- span dE by scanning each intermediate's forward turnover window ---
    best = {"dE": -np.inf}
    for j in range(N):                      # TDI candidate = intermediate j
        for m in range(N):                  # m steps forward to a TS
            i = (j + m) % N
            period = 1 if (j + m) >= N else 0
            lvl = T0[i] + period * dG_r     # TS level (period 1 is dG_r lower)
            dE = lvl - I0[j]
            if dE > best["dE"]:
                best = {"dE": dE, "tdi": j, "tdts": i}
    j_tdi, i_tdts, dE = best["tdi"], best["tdts"], best["dE"]
    tof_span = pref * np.exp(-dE / RT)

    # --- degree of TOF control (softmax over the determining windows) ---
    # TS control, given the TDI window
    ts_levels = np.array([T0[(j_tdi + m) % N] + (dG_r if (j_tdi + m) >= N else 0.0)
                          for m in range(N)])
    ts_idx = [(j_tdi + m) % N for m in range(N)]
    w_ts = np.exp((ts_levels - ts_levels.max()) / RT); w_ts /= w_ts.sum()
    xtof_ts = {prof["T_labels"][ts_idx[m]]: float(w_ts[m]) for m in range(N)}
    # intermediate control, given the TDTS: look back over one turnover
    int_levels = np.array([I0[(i_tdts - m) % N] - (dG_r if (i_tdts - m) < 0 else 0.0)
                           for m in range(N)])
    int_idx = [(i_tdts - m) % N for m in range(N)]
    w_int = np.exp((-int_levels + int_levels.min()) / RT); w_int /= w_int.sum()
    xtof_int = {prof["I_labels"][int_idx[m]]: float(w_int[m]) for m in range(N)}

    unit = net.energy_unit
    return {
        "TDTS": prof["T_labels"][i_tdts],
        "TDI": prof["I_labels"][j_tdi],
        "dE_kcal": C.from_j_per_mol(dE, "kcal/mol"),
        "dE": C.from_j_per_mol(dE, unit),
        "dG_r_kcal": C.from_j_per_mol(dG_r, "kcal/mol"),
        "tof_span": float(tof_span),
        "xtof_ts": xtof_ts,
        "xtof_int": xtof_int,
        "unit": unit,
    }
