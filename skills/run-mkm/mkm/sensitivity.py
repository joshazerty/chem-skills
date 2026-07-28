"""Exact sensitivity analysis by the implicit function theorem + autodiff.

At the steady state y*(theta) the bordered residual g(y*, theta) = 0. Autodiff
gives the two Jacobians, and one adjoint linear solve turns them into the exact
gradient of ln(TOF) with respect to *every* parameter at once:

    lambda solves   Jy^T lambda = d lnTOF/dy
    d lnTOF/dtheta = d lnTOF/dtheta|_explicit  -  lambda^T (dg/dtheta)

From that single object we read off, with the right unit factor:

  * degree of rate control      X_RC,i  = -RT d lnTOF/dG_ts,i        (Campbell)
  * degree of thermo. control   X_TRC,n = -RT d lnTOF/dG_species,n
  * apparent reaction order     n_X     =    d lnTOF/d ln[X]  (reservoir X)
  * apparent activation energy  Ea_app  =  R T^2 d lnTOF/dT

The DRC sum rule (sum over transition states ~ 1 for a single dominant path)
and the order/Ea identities are all consequences of the same gradient, so they
double as internal consistency checks.
"""
from __future__ import annotations

import jax
import jax.numpy as jnp
import numpy as np

from . import constants as C
from .model import Model


def total_grad_lntof(model: Model, theta=None, y=None):
    """Exact gradient of ln(TOF) w.r.t. every parameter (adjoint IFT).

    Returns a dict with the same keys as theta, each an array of derivatives
    d ln(TOF)/d(param) in *per-J/mol* (energies) or per-unit (T, logC).
    """
    theta = theta if theta is not None else model.theta0
    if y is None:
        _, y = model.steady_state(theta)
    y = jnp.asarray(y)

    Jy = jax.jacobian(model.residual, argnums=0)(y, theta)          # (n_dyn,n_dyn)
    dO_dy = jax.grad(model.ln_observable, argnums=0)(y, theta)      # (n_dyn,)
    # adjoint variable
    lam = jnp.linalg.solve(Jy.T, dO_dy)

    dO_dtheta = jax.grad(model.ln_observable, argnums=1)(y, theta)  # dict
    dg_dtheta = jax.jacobian(model.residual, argnums=1)(y, theta)   # dict, leaves (n_dyn,*)

    out = {}
    for k in dO_dtheta:
        gth = dg_dtheta[k]                     # (n_dyn, *leaf_shape)
        # contract the adjoint over the residual axis
        corr = jnp.tensordot(lam, gth, axes=([0], [0]))
        out[k] = dO_dtheta[k] - corr
    return out, np.asarray(y)


def degree_of_rate_control(model: Model, theta=None):
    """Campbell degree of rate control per reaction (via its TS free energy).

    X_RC,i = -RT d lnTOF/dG_ts,i. Barrierless steps have no TS to perturb and are
    returned as NaN. Also returns the sum over real TSs (should be ~1 when a
    single pathway dominates).
    """
    theta = theta if theta is not None else model.theta0
    grads, _ = total_grad_lntof(model, theta)
    RT = C.R_J * float(theta["T"])
    xrc = -RT * np.asarray(grads["G_ts"])
    barrierless = np.asarray(model.barrierless)
    xrc = np.where(barrierless, np.nan, xrc)
    total = float(np.nansum(xrc))
    return xrc, total


def thermodynamic_rate_control(model: Model, theta=None):
    """Degree of *thermodynamic* rate control per species: X_TRC,n = -RT dlnTOF/dG_n.

    Perturbing an intermediate's free energy (its stability) changes the TOF;
    resting-state intermediates carry the large negative values."""
    theta = theta if theta is not None else model.theta0
    grads, _ = total_grad_lntof(model, theta)
    RT = C.R_J * float(theta["T"])
    return -RT * np.asarray(grads["G_species"])


def reaction_orders(model: Model, theta=None):
    """Apparent reaction order in every reservoir species: n_X = dlnTOF/dln[X].

    Returns a dict {species_name: order} for fixed (reservoir) species only."""
    theta = theta if theta is not None else model.theta0
    grads, _ = total_grad_lntof(model, theta)
    d_logc = np.asarray(grads["logC_fix"])       # aligned to fixed species order
    fixed_names = [model.net.species[i].name for i in model.idx_fix]
    return {name: float(v) for name, v in zip(fixed_names, d_logc)}


def apparent_activation_energy(model: Model, theta=None, unit="kcal/mol"):
    """Apparent activation energy Ea_app = R T^2 d lnTOF/dT, in `unit`.

    This is exactly what an Arrhenius fit of the network TOF would return."""
    theta = theta if theta is not None else model.theta0
    grads, _ = total_grad_lntof(model, theta)
    T = float(theta["T"])
    Ea_J = C.R_J * T * T * float(grads["T"])
    return C.from_j_per_mol(Ea_J, unit)


def parametric_sensitivity(model: Model, theta=None):
    """Full d ln C_i / d G_ts,j matrix (species x reaction) at steady state.

    Uses the IFT once per species observable would be wasteful; instead we solve
    dy/dG_ts = -Jy^{-1} (dg/dG_ts) directly and map to all dynamic species."""
    theta = theta if theta is not None else model.theta0
    _, y = model.steady_state(theta)
    y = jnp.asarray(y)
    Jy = jax.jacobian(model.residual, argnums=0)(y, theta)
    dg_dGts = jax.jacobian(model.residual, argnums=1)(y, theta)["G_ts"]  # (n_dyn,nr)
    dy_dGts = -jnp.linalg.solve(Jy, dg_dGts)                              # (n_dyn,nr)
    RT = C.R_J * float(theta["T"])
    # convert to dlnC/d(-G/RT) = -RT * (1/C) dC/dG
    y_safe = jnp.where(y > 0, y, 1.0)
    dlnC = -RT * (dy_dGts / y_safe[:, None])
    names = [model.net.species[i].name for i in model.idx_dyn]
    return names, np.asarray(dlnC)
