"""Propagate DFT free-energy uncertainty to the TOF.

DFT barriers carry an irreducible error (~1-2 kcal/mol for a good hybrid
functional), and because the TOF depends exponentially on the barriers, that
error can swing the TOF by orders of magnitude. This module answers "given my
functional's error bar, how well is the TOF actually determined, and which
barrier dominates the uncertainty?"

Two complementary views:
  * Monte Carlo: sample every TS (and optionally every intermediate) free energy
    from N(mean, sigma), recompute the TOF for each draw, report the log-normal
    spread (median, 90% band, probability within a factor).
  * Analytic first-order: Var[ln TOF] ~= sum_i (d lnTOF/dG_i)^2 sigma_i^2, so each
    barrier's variance share is (X_RC,i)^2 -- the degree of rate control squared.
    The rate-controlling step is also the uncertainty-controlling step.

Each MC draw needs its own stiff steady-state solve, so the loop runs over the
robust BDF solver rather than a vmap'd closed form -- correct, and quick enough
for the sample sizes that matter (a few thousand).
"""
from __future__ import annotations

import numpy as np

from . import constants as C
from .model import Model
from .sensitivity import degree_of_rate_control, total_grad_lntof


def monte_carlo_tof(model: Model, sigma=1.5, n=2000, unit="kcal/mol",
                    include_species=False, seed=0):
    """Sample TS (and optionally species) free energies ~ N(mean, sigma) and
    recompute the TOF. Returns the sample and summary statistics."""
    rng = np.random.default_rng(seed)
    sigma_J = C.to_j_per_mol(sigma, unit)
    theta0 = model.theta0
    Gts0 = np.asarray(theta0["G_ts"])
    Gsp0 = np.asarray(theta0["G_species"])
    barrierless = np.asarray(model.barrierless)

    import jax.numpy as jnp
    # nominal steady state: warm-start every draw from it (each perturbation
    # moves the root only slightly, so Newton converges in a few iterations).
    _, y_nom = model.steady_state()
    tofs = np.empty(n)
    y_guess = y_nom
    for i in range(n):
        th = dict(theta0)
        dts = rng.normal(0.0, sigma_J, size=Gts0.shape)
        dts[barrierless] = 0.0
        th["G_ts"] = jnp.asarray(Gts0 + dts)
        if include_species:
            th["G_species"] = jnp.asarray(Gsp0 + rng.normal(0.0, sigma_J, size=Gsp0.shape))
        tof, y = model.tof_from(th, y_guess)
        tofs[i] = abs(tof)
        if np.all(np.isfinite(y)) and np.all(y >= 0):
            y_guess = y_nom  # re-anchor to nominal (robust for large draws)

    logt = np.log10(tofs[tofs > 0])
    med = float(np.median(tofs))
    p05, p95 = (float(np.percentile(tofs, 5)), float(np.percentile(tofs, 95)))
    base = abs(model.tof())
    within10 = float(np.mean((tofs > base / 10) & (tofs < base * 10)))
    return {
        "tofs": tofs,
        "base_tof": float(base),
        "median": med,
        "p05": p05, "p95": p95,
        "log10_std": float(np.std(logt)),
        "band_factor": float(np.sqrt(p95 / max(p05, 1e-300))),  # geometric half-width
        "frac_within_decade": within10,
        "sigma": sigma, "unit": unit, "n": n,
    }


def variance_decomposition(model: Model, sigma=1.5, unit="kcal/mol"):
    """First-order share of Var[ln TOF] contributed by each TS barrier.

    share_i = (X_RC,i)^2 / sum_k (X_RC,k)^2  (independent equal-sigma barriers).
    Also returns the analytic sigma(ln TOF) = sqrt(sum_i (dlnTOF/dG_i)^2 sigma^2)."""
    xrc, _ = degree_of_rate_control(model)          # X_RC,i = -RT dlnTOF/dG_ts,i
    sigma_J = C.to_j_per_mol(sigma, unit)
    RT = C.R_J * float(model.theta0["T"])
    # dlnTOF/dG_ts = -X_RC / RT  -> per-barrier variance (X_RC*sigma/RT)^2
    x = np.nan_to_num(xrc, nan=0.0)
    var_i = (x * sigma_J / RT) ** 2
    total_var = float(var_i.sum())
    shares = var_i / total_var if total_var > 0 else var_i
    labels = [rx.id for rx in model.net.reactions]
    return {
        "sigma_lnTOF": float(np.sqrt(total_var)),
        "factor_1sigma": float(np.exp(np.sqrt(total_var))),
        "shares": {l: float(s) for l, s in zip(labels, shares)},
        "sigma": sigma, "unit": unit,
    }
