"""Rate-constant construction from free energies (differentiable, JAX).

Everything here is a pure function of a free-energy vector (in J/mol) and T, so
autodiff flows straight through to rate constants and, ultimately, to the TOF.
That is what makes the degree of rate control and the reaction orders *exact*
rather than finite-difference estimates.
"""
from __future__ import annotations

import jax
import jax.numpy as jnp

from . import constants as C

jax.config.update("jax_enable_x64", True)


def eyring_k(dG_act_J, T, kappa=1.0, molecularity=1, c0=1.0):
    """Eyring/TST rate constant.

        k = kappa * (kB T / h) * exp(-dG_act / RT) / c0**(molecularity-1)

    dG_act_J : activation free energy, J/mol (may be a jnp array)
    c0       : standard-state concentration (mol/L) the barrier is referenced to;
               the c0**(m-1) factor gives k the right concentration units for a
               step consuming `molecularity` reactant molecules.
    """
    pref = kappa * C.KB_OVER_H * T
    k = pref * jnp.exp(-dG_act_J / (C.R_J * T))
    return k / c0 ** (molecularity - 1)


def _reduced_u(nu_imag_cm, T):
    """u = h|nu|/(kB T), the reduced imaginary frequency (dimensionless)."""
    return C.H_J * C.wavenumber_to_hz(nu_imag_cm) / (C.KB_J * T)


def wigner_kappa(nu_imag_cm, T):
    """Wigner tunneling correction from |imaginary frequency| (cm^-1).

        kappa = 1 + (1/24) u^2 ,   u = h|nu|/(kB T)

    The leading small-curvature term; safe for any u but underestimates deep
    tunneling. It is the second-order expansion of the Bell correction below.
    """
    u = _reduced_u(nu_imag_cm, T)
    return 1.0 + (u ** 2) / 24.0


def bell_kappa(nu_imag_cm, T):
    """Bell (1959) parabolic-barrier tunneling correction.

        kappa = (u/2) / sin(u/2) ,   u = h|nu|/(kB T)

    Exact for a parabolic barrier top and the right object for H/D contrast in
    primary KIE (H and D differ mainly in |nu|). VALID ONLY FOR u < 2*pi; it
    diverges at u -> 2*pi (barrier too thin / T too low for the parabolic model).
    We clip just below 2*pi so it never returns inf, but the caller must check
    `bell_valid` -- past the pole the number is meaningless and Eckart is
    required (see SKILL.md 'Tunneling'). Wigner is the safe bounded fallback.
    """
    u = _reduced_u(nu_imag_cm, T)
    u = jnp.clip(u, 0.0, 2.0 * jnp.pi - 1e-3)
    half = u / 2.0
    return jnp.where(half < 1e-8, 1.0, half / jnp.sin(half))


def bell_valid(nu_imag_cm, T, margin=0.9):
    """True when the Bell parabolic model is trustworthy (u < margin*2*pi)."""
    return bool(_reduced_u(nu_imag_cm, T) < margin * 2.0 * jnp.pi)


def step_barriers(G_species, G_ts, Rmat, Pmat, barrierless):
    """Forward & reverse activation free energies for every reaction (J/mol).

    G_species, G_ts : J/mol. `barrierless` is a static boolean mask (NOT derived
    from NaN inside the traced function -- doing that gives 0*NaN gradients).
    G_ts entries for barrierless steps are ignored; the forward barrier is
    max(0, dG_rxn) and the reverse max(0, -dG_rxn), so the step still obeys
    thermodynamics.
    """
    # reactant / product free-energy sums per reaction
    G_react = G_species @ Rmat        # (nr,)
    G_prod = G_species @ Pmat
    dG_rxn = G_prod - G_react

    dG_f_ts = G_ts - G_react
    dG_r_ts = G_ts - G_prod

    dG_f_bl = jnp.maximum(dG_rxn, 0.0)
    dG_r_bl = jnp.maximum(-dG_rxn, 0.0)

    dG_f = jnp.where(barrierless, dG_f_bl, dG_f_ts)
    dG_r = jnp.where(barrierless, dG_r_bl, dG_r_ts)
    # a TS that sits below a well (numerical noise) -> clamp barrier to 0
    dG_f = jnp.maximum(dG_f, 0.0)
    dG_r = jnp.maximum(dG_r, 0.0)
    return dG_f, dG_r, dG_rxn
