"""Kinetic isotope effects: intrinsic -> network-apparent.

Two things a computed network can tell you about a KIE:

1. The *intrinsic* KIE of the elementary step where the isotope sits. Provided
   three ways, cheapest first:
     * a number you already have (from experiment or a separate calc),
     * from the isotopologue barrier difference  KIE = exp(ddG_act/RT) * tunnel,
     * from full vibrational frequency sets (Bigeleisen-Mayer reduced isotopic
       partition function ratios) with a Bell/Wigner tunnelling ratio.

2. The *apparent* KIE the experiment would actually measure, which is the ratio
   of overall TOFs with light vs heavy rate constants. That is NOT the intrinsic
   KIE unless the labelled step is fully rate-controlling -- it is masked by the
   degree of rate control. We compute it exactly (re-solve the MKM with the heavy
   step slowed) and also report the DRC-weighted estimate

       KIE_app  ~=  prod_i (KIE_intrinsic,i) ** X_RC,i

   which makes the masking explicit and ties KIE straight back to the rate-
   control analysis.

Applying an intrinsic KIE: the labelled step's TS free energy is raised by
RT ln(KIE), which divides both k_f and k_r of that step by KIE (a pure kinetic
effect, no equilibrium isotope effect by default). For a rate-determining,
effectively irreversible step this is exactly the primary KIE; add an explicit
species-energy shift if an equilibrium isotope effect is also wanted.
"""
from __future__ import annotations

import numpy as np

from . import constants as C
from . import rates as R
from .model import Model
from .sensitivity import degree_of_rate_control


# --------------------------------------------------------------------------
# intrinsic KIE
# --------------------------------------------------------------------------
def kie_from_ddg(ddg_act, T, unit="kcal/mol", tunnel_ratio=1.0):
    """Intrinsic KIE from the isotopologue barrier difference.

    ddg_act = G_act(heavy) - G_act(light). A positive value (heavy barrier
    higher, i.e. heavy slower) gives a normal KIE > 1:  KIE = exp(ddg/RT)."""
    ddg_J = C.to_j_per_mol(ddg_act, unit)
    return float(np.exp(ddg_J / (C.R_J * T)) * tunnel_ratio)


def _rpfr(freqs_light, freqs_heavy, T):
    """Reduced isotopic partition function ratio (Bigeleisen-Mayer) for one
    stationary point, from matched real vibrational frequencies (cm^-1)."""
    fl = np.asarray(freqs_light, float)
    fh = np.asarray(freqs_heavy, float)
    fl = fl[fl > 0]; fh = fh[fh > 0]
    uL = C.H_J * C.wavenumber_to_hz(fl) / (C.KB_J * T)
    uH = C.H_J * C.wavenumber_to_hz(fh) / (C.KB_J * T)
    # product over modes of (uH/uL) * (sinh(uL/2)/sinh(uH/2))
    term = (uH / uL) * (np.sinh(uL / 2.0) / np.sinh(uH / 2.0))
    return float(np.prod(term))


def bigeleisen_kie(reactant_light, reactant_heavy, ts_light, ts_heavy,
                   nu_imag_light, nu_imag_heavy, T, tunnelling="wigner"):
    """Full semiclassical primary KIE from frequency sets (cm^-1).

    KIE = (RPFR_reactant / RPFR_TS) * (nu*_L / nu*_H) * (kappa_L / kappa_H)

    ts_light/ts_heavy are the *real* TS frequencies (exclude the imaginary mode);
    nu_imag_* are the magnitudes of the imaginary frequencies. tunnelling in
    {'none','wigner','bell'} sets the kappa_L/kappa_H ratio. Default 'wigner' is
    bounded; 'bell' is more accurate but diverges once u = hc*nu/kT approaches
    2*pi (common for H-transfer at 298 K), in which case `bell_valid` is False
    and Eckart tunnelling is required for a trustworthy number.
    """
    rpfr_R = _rpfr(reactant_light, reactant_heavy, T)
    rpfr_TS = _rpfr(ts_light, ts_heavy, T)
    reaction_coord = nu_imag_light / nu_imag_heavy
    bell_ok = R.bell_valid(nu_imag_light, T) and R.bell_valid(nu_imag_heavy, T)
    if tunnelling == "none":
        tratio = 1.0
    elif tunnelling == "bell":
        tratio = float(R.bell_kappa(nu_imag_light, T) / R.bell_kappa(nu_imag_heavy, T))
    else:
        tratio = float(R.wigner_kappa(nu_imag_light, T) / R.wigner_kappa(nu_imag_heavy, T))
    kie_semi = (rpfr_R / rpfr_TS) * reaction_coord
    return {
        "kie": float(kie_semi * tratio),
        "kie_no_tunnel": float(kie_semi),
        "rpfr_reactant": rpfr_R,
        "rpfr_ts": rpfr_TS,
        "reaction_coordinate_ratio": float(reaction_coord),
        "tunnelling_ratio": tratio,
        "tunnelling": tunnelling,
        "bell_valid": bool(bell_ok),
    }


# --------------------------------------------------------------------------
# network-apparent KIE
# --------------------------------------------------------------------------
def apply_kie(model: Model, step_kies: dict, theta=None):
    """Return a theta with the labelled steps slowed by their intrinsic KIE
    (TS free energy raised by RT ln KIE)."""
    theta = dict(theta if theta is not None else model.theta0)
    RT = C.R_J * float(theta["T"])
    Gts = np.asarray(theta["G_ts"]).copy()
    id_to_j = {rx.id: j for j, rx in enumerate(model.net.reactions)}
    for step_id, kie in step_kies.items():
        Gts[id_to_j[step_id]] += RT * np.log(kie)
    import jax.numpy as jnp
    theta["G_ts"] = jnp.asarray(Gts)
    return theta


def network_kie(model: Model, step_kies: dict, theta=None):
    """Apparent (measured) KIE = TOF_light / TOF_heavy, plus the DRC-weighted
    estimate that shows how much the intrinsic KIE is masked by rate control."""
    theta = theta if theta is not None else model.theta0
    tof_light = model.tof(theta)
    theta_heavy = apply_kie(model, step_kies, theta)
    tof_heavy = model.tof(theta_heavy)
    apparent = tof_light / tof_heavy

    xrc, _ = degree_of_rate_control(model, theta)
    id_to_j = {rx.id: j for j, rx in enumerate(model.net.reactions)}
    drc_weighted = 1.0
    per_step = {}
    for step_id, kie in step_kies.items():
        x = float(xrc[id_to_j[step_id]])
        per_step[step_id] = {"intrinsic": float(kie), "X_RC": x}
        drc_weighted *= kie ** x
    return {
        "apparent_kie": float(apparent),
        "drc_weighted_kie": float(drc_weighted),
        "tof_light": float(tof_light),
        "tof_heavy": float(tof_heavy),
        "per_step": per_step,
    }
