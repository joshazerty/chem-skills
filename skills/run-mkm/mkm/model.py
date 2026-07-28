"""Differentiable micro-kinetic model: free energies -> steady state -> TOF.

Design
------
The forward problem (steady state, transient, TOF) is solved with a battle-
tested stiff integrator (SciPy BDF) plus a Newton polish. Micro-kinetic
networks routinely span >10 orders of magnitude in rate constant, which defeats
naive one-shot autodiff-through-the-integrator; BDF eats it for breakfast.

Sensitivities are then exact and cheap via the **implicit function theorem** at
the converged steady state, using JAX autodiff for the Jacobians (not finite
differences). At steady state the dynamic-species residual g(C; theta) = 0, so

    dC/dtheta = -(dg/dC)^{-1} (dg/dtheta)

and any observable's gradient follows by the chain rule. This is the standard
parametric-sensitivity route (CHEMKIN/Cantera style) with autodiff supplying
every partial derivative exactly. Catalyst conservation (which makes the bare
steady-state Jacobian singular) is handled by a projector onto the conservation
subspace, so the bordered Jacobian dg/dC is nonsingular for any number of
independent conserved moieties.

Everything the sensitivity layer needs is a pure JAX function of a parameter
dict theta:

    theta = {"G_species","G_ts","logC_fix","T","kappa"}   (energies in J/mol)
"""
from __future__ import annotations

from dataclasses import dataclass

import jax
import jax.numpy as jnp
import numpy as np
from scipy.integrate import solve_ivp

from . import constants as C
from . import rates as R
from .network import Network

jax.config.update("jax_enable_x64", True)


@dataclass
class ModelConfig:
    t_relax: float = 1.0e10     # integrate this long to reach steady state, s
    rtol: float = 1e-9
    atol: float = 1e-18
    newton_iters: int = 60
    newton_tol: float = 1e-13   # ||g|| target after polish (relative to scale)


class Model:
    """Steady-state model + observables for one Network."""

    def __init__(self, net: Network, cfg: ModelConfig | None = None):
        self.net = net
        self.cfg = cfg or ModelConfig()

        self.Rmat = jnp.asarray(net.Rmat)
        self.Pmat = jnp.asarray(net.Pmat)
        self.S = jnp.asarray(net.S)
        self.Rmat_np = net.Rmat
        self.Pmat_np = net.Pmat
        self.S_np = net.S
        self.mol_f, self.mol_r = net.molecularity()
        self.mol_f = jnp.asarray(self.mol_f)
        self.mol_r_j = jnp.asarray(self.mol_r)
        self.c0 = net.standard_state

        fixed = net.fixed_mask()
        self.fixed = fixed
        self.idx_fix = np.where(fixed)[0]
        self.idx_dyn = np.where(~fixed)[0]
        self.n_dyn = len(self.idx_dyn)
        conc0 = np.array([s.conc0 for s in net.species], dtype=float)
        self.y0 = conc0[~fixed]
        self.C0_dyn = jnp.asarray(self.y0)
        self.cat_total = net.catalyst_total() or 1.0

        # conservation projector on the dynamic block (left null space of S_dyn)
        S_dyn = net.S[self.idx_dyn, :]           # n_dyn x nr
        if self.n_dyn > 0 and S_dyn.size:
            U, s, _ = np.linalg.svd(S_dyn)
            tol = max(S_dyn.shape) * (s[0] if s.size else 0.0) * 1e-12
            rank = int((s > tol).sum())
            Ln = U[:, rank:]                     # n_dyn x c orthonormal conserv. vecs
        else:
            Ln = np.zeros((self.n_dyn, 0))
        self.Ln = jnp.asarray(Ln)
        self.P = jnp.asarray(Ln @ Ln.T)          # projector onto conservation subspace
        self.n_cons = Ln.shape[1]

        self._setup_objective()
        self.theta0 = self.default_theta()

    # ------------------------------------------------------------------
    def default_theta(self):
        net = self.net
        Gs = C.to_j_per_mol(net.G_species(), net.energy_unit)
        Gts = C.to_j_per_mol(net.G_ts_vector(), net.energy_unit)
        self.barrierless = jnp.asarray(np.isnan(Gts))
        Gts = np.where(np.isnan(Gts), 0.0, Gts)
        Cfix = np.array([s.conc0 for s in net.species])[net.fixed_mask()]
        Cfix = np.where(Cfix <= 0, 1e-30, Cfix)
        return {
            "G_species": jnp.asarray(Gs),
            "G_ts": jnp.asarray(Gts),
            "logC_fix": jnp.asarray(np.log(Cfix)),
            "T": jnp.asarray(float(net.T)),
            "kappa": jnp.asarray(net.kappa_vector()),
        }

    def _setup_objective(self):
        obj = self.net.objective or {}
        self._obj_kind = "species"
        if obj.get("type") == "reaction":
            j = next(i for i, r in enumerate(self.net.reactions) if r.id == obj["id"])
            sel = np.zeros(self.net.nr); sel[j] = 1.0
            self._obj_kind = "reaction"
            self._obj_sel = jnp.asarray(sel)
            self.objective_label = f"net rate of {obj['id']}"
        else:
            if obj.get("type") == "species":
                name = obj["name"]
            else:
                fixed_named = [s.name for s in self.net.species if s.fixed]
                name = fixed_named[-1] if fixed_named else self.net.species[-1].name
            v = np.zeros(self.net.ns); v[self.net.index[name]] = 1.0
            self._obj_prodvec = jnp.asarray(v)
            self.objective_label = f"production of {name}"

    # ------------------------------------------------------------------
    # differentiable primitives (pure JAX, functions of theta)
    # ------------------------------------------------------------------
    def rate_constants(self, theta):
        dG_f, dG_r, _ = R.step_barriers(
            theta["G_species"], theta["G_ts"], self.Rmat, self.Pmat, self.barrierless
        )
        kf = R.eyring_k(dG_f, theta["T"], theta["kappa"], self.mol_f, self.c0)
        kr = R.eyring_k(dG_r, theta["T"], theta["kappa"], self.mol_r_j, self.c0)
        return kf, kr

    def _assemble(self, y_dyn, theta):
        full = jnp.zeros(self.net.ns)
        full = full.at[jnp.asarray(self.idx_dyn)].set(y_dyn)
        full = full.at[jnp.asarray(self.idx_fix)].set(jnp.exp(theta["logC_fix"]))
        return full

    def net_rate_vec(self, full, kf, kr):
        cr = jnp.prod(jnp.power(full[:, None], self.Rmat), axis=0)
        cp = jnp.prod(jnp.power(full[:, None], self.Pmat), axis=0)
        return kf * cr - kr * cp

    def residual(self, y_dyn, theta):
        """Bordered steady-state residual (pure JAX). Zero <=> steady state that
        also respects every conservation law."""
        kf, kr = self.rate_constants(theta)
        full = self._assemble(y_dyn, theta)
        r = self.net_rate_vec(full, kf, kr)
        f = self.S[jnp.asarray(self.idx_dyn), :] @ r          # n_dyn
        # replace the (redundant) conserved directions with the conservation law
        keep = f - self.P @ f                                  # (I-P) f
        cons = self.P @ (y_dyn - self.C0_dyn)                 # P (C - C0)
        return keep + cons

    def observable(self, y_dyn, theta):
        """TOF per catalyst (signed net turnover)."""
        kf, kr = self.rate_constants(theta)
        full = self._assemble(y_dyn, theta)
        r = self.net_rate_vec(full, kf, kr)
        if self._obj_kind == "reaction":
            flux = jnp.sum(self._obj_sel * r)
        else:
            flux = jnp.sum(self._obj_prodvec * (self.S @ r))
        return flux / self.cat_total

    def ln_observable(self, y_dyn, theta):
        return jnp.log(jnp.abs(self.observable(y_dyn, theta)) + 1e-300)

    # ------------------------------------------------------------------
    # forward solve (NumPy: robust on extreme stiffness)
    # ------------------------------------------------------------------
    def _kfr_np(self, theta):
        kf, kr = self.rate_constants(theta)
        return np.asarray(kf), np.asarray(kr)

    def _rhs_np(self, y, kf, kr, Cfix):
        full = np.zeros(self.net.ns)
        full[self.idx_dyn] = y
        full[self.idx_fix] = Cfix
        cr = np.prod(np.power(full[:, None], self.Rmat_np), axis=0)
        cp = np.prod(np.power(full[:, None], self.Pmat_np), axis=0)
        r = kf * cr - kr * cp
        return (self.S_np @ r)[self.idx_dyn]

    def steady_state(self, theta=None, polish=True, y_guess=None):
        """Return (full_conc, y_dyn) at steady state.

        If `y_guess` is given (e.g. the nominal steady state during Monte Carlo),
        try a warm-started Newton solve first -- a small parameter change moves
        the root only slightly, so this is far cheaper than re-integrating. Fall
        back to BDF from scratch if the warm start does not converge."""
        theta = theta if theta is not None else self.theta0
        Cfix = np.exp(np.asarray(theta["logC_fix"]))
        import jax.numpy as jnp

        if y_guess is not None:
            y = self._newton_polish(np.asarray(y_guess), theta)
            g = float(jnp.linalg.norm(self.residual(jnp.asarray(y), theta)))
            if g < 1e-7 * (np.linalg.norm(y) + 1.0) and np.all(np.isfinite(y)):
                full = np.zeros(self.net.ns)
                full[self.idx_dyn] = y; full[self.idx_fix] = Cfix
                return full, y
            # else fall through to a fresh integration

        kf, kr = self._kfr_np(theta)
        sol = solve_ivp(lambda t, y: self._rhs_np(y, kf, kr, Cfix),
                        [0.0, self.cfg.t_relax], self.y0, method="BDF",
                        rtol=self.cfg.rtol, atol=self.cfg.atol)
        y = np.maximum(sol.y[:, -1], 0.0)
        if polish:
            y = self._newton_polish(y, theta)
        full = np.zeros(self.net.ns)
        full[self.idx_dyn] = y
        full[self.idx_fix] = Cfix
        return full, y

    def tof_from(self, theta, y_guess):
        """TOF using a warm-start steady-state solve (for Monte Carlo)."""
        import jax.numpy as jnp
        _, y = self.steady_state(theta, y_guess=y_guess)
        return float(self.observable(jnp.asarray(y), theta)), y

    def _residual_jac(self):
        """Cached, jitted d(residual)/dy so repeated solves (e.g. Monte Carlo)
        reuse one compilation instead of recompiling every call."""
        if not hasattr(self, "_resjac"):
            self._resjac = jax.jit(jax.jacobian(self.residual, argnums=0))
        return self._resjac

    def _residual_jit(self):
        if not hasattr(self, "_resfn"):
            self._resfn = jax.jit(self.residual)
        return self._resfn

    def _newton_polish(self, y, theta):
        """Refine the integrated state to the exact root of the bordered residual
        using JAX Jacobians (damped Newton)."""
        jac = self._residual_jac()
        res = self._residual_jit()
        y = jnp.asarray(y)
        for _ in range(self.cfg.newton_iters):
            g = res(y, theta)
            gn = float(jnp.linalg.norm(g))
            scale = float(jnp.linalg.norm(y)) + 1e-30
            if gn < self.cfg.newton_tol * (scale + 1.0):
                break
            J = jac(y, theta)
            try:
                dy = jnp.linalg.solve(J, -g)
            except Exception:
                dy = jnp.linalg.lstsq(J, -g, rcond=None)[0]
            # damping + positivity (concentrations >= 0)
            step = 1.0
            for _ in range(30):
                yt = y + step * dy
                if jnp.all(yt >= -1e-14) and jnp.all(jnp.isfinite(yt)):
                    gt = res(jnp.maximum(yt, 0.0), theta)
                    if float(jnp.linalg.norm(gt)) < gn:
                        break
                step *= 0.5
            y = jnp.maximum(y + step * dy, 0.0)
        return np.asarray(y)

    def transient(self, ts, theta=None):
        """Concentration trajectory at times `ts` (1-D array). Returns (ts, C[ns,nt])."""
        theta = theta if theta is not None else self.theta0
        kf, kr = self._kfr_np(theta)
        Cfix = np.exp(np.asarray(theta["logC_fix"]))
        ts = np.asarray(ts, dtype=float)
        sol = solve_ivp(lambda t, y: self._rhs_np(y, kf, kr, Cfix),
                        [0.0, ts[-1]], self.y0, method="BDF",
                        rtol=self.cfg.rtol, atol=self.cfg.atol, t_eval=ts)
        C = np.zeros((self.net.ns, len(sol.t)))
        C[self.idx_dyn, :] = np.maximum(sol.y, 0.0)
        C[self.idx_fix, :] = Cfix[:, None]
        return sol.t, C

    # ------------------------------------------------------------------
    # concrete observables at steady state
    # ------------------------------------------------------------------
    def tof(self, theta=None):
        theta = theta if theta is not None else self.theta0
        _, y = self.steady_state(theta)
        return float(self.observable(jnp.asarray(y), theta))

    def net_rates(self, theta=None):
        theta = theta if theta is not None else self.theta0
        full, _ = self.steady_state(theta)
        kf, kr = self.rate_constants(theta)
        return np.asarray(self.net_rate_vec(jnp.asarray(full), kf, kr))

    def reversibilities(self, theta=None):
        """z_j = (r_f - r_r)/(r_f + r_r) per step: +1 fully forward, 0 at equilibrium."""
        theta = theta if theta is not None else self.theta0
        full, _ = self.steady_state(theta)
        kf, kr = self.rate_constants(theta)
        full = jnp.asarray(full)
        cr = jnp.prod(jnp.power(full[:, None], self.Rmat), axis=0)
        cp = jnp.prod(jnp.power(full[:, None], self.Pmat), axis=0)
        rf, rr = kf * cr, kr * cp
        return np.asarray((rf - rr) / (rf + rr + 1e-300))
