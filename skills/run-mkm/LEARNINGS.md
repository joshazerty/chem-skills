# run-mkm — learnings

Newest first. Append a dated entry when you discover a fix, a modelling gotcha,
or a validated extension. Keep it concrete.

### 2026-07-16 — Autodiff-through-the-stiff-integrator stalls; use IFT instead
Micro-kinetic rate constants span ~13 orders of magnitude. A one-shot implicit
solve (diffrax Kvaerno5 + Newton) on the full catalytic cycle made **zero**
progress (dt collapsed, 2M steps still at the initial condition). SciPy BDF
solved the same system in ~1900 f-evals. **Fix:** forward solve with BDF +
Newton polish; get exact sensitivities from the **implicit function theorem** at
the converged steady state (autodiff Jacobians, adjoint solve). Faster, robust,
and still exact — DRC matches finite differences and Σ X_RC = 1 to 5 digits.

### 2026-07-16 — A turnover steady state needs fixed reservoirs
Without at least one `fixed` reactant/product the network relaxes to equilibrium
(flux → 0) and the "steady state" is the dead final state; gradients are noise at
the 1e-30 floor. Always hold the overall reactant(s)/product(s) constant.

### 2026-07-16 — `jnp.where` with NaN barriers poisons all gradients
Marking barrierless steps with `G_ts = NaN` and selecting via
`jnp.where(isnan, ...)` gives `0*NaN = NaN` cotangents that contaminate *every*
parameter gradient. **Fix:** carry barrierless-ness as a static boolean mask and
keep NaN out of the traced computation entirely.

### 2026-07-16 — Energetic-span TDI is not the lowest intermediate
The Kozuch TDI is the intermediate that, paired with the highest TS in the
turnover *after* it, maximises the span — a periodic (two-turnover) pairing.
Naively taking the global-minimum intermediate picked the wrong resting state
(cat_P instead of cat_AB) and gave δE = 60 kcal/mol instead of 23.5. The
doubled-profile forward-window scan reproduces the numerical TOF to 0.6%.

### 2026-07-16 — Bell tunnelling is only valid for u < 2π
`κ_Bell = (u/2)/sin(u/2)`, u = hc·ν*/kT, diverges at u → 2π. A 1350 cm⁻¹
imaginary mode at 298 K has u ≈ 6.5 > 2π and gave κ_H/κ_D ≈ 1700 (nonsense).
KIE defaults to the bounded Wigner correction and reports `bell_valid`. Deep
tunnelling needs an **Eckart** model — not yet implemented (the closed form is
easy to get subtly wrong; validate any implementation against a known κ before
shipping).

### 2026-07-16 — Monte-Carlo uncertainty: warm-start from the nominal root
Re-integrating from scratch per draw is ~5× slower and some large-σ draws are
very stiff. Warm-starting Newton from the nominal steady state cuts it to
~0.03 s/solve. The analytic Var[ln TOF] = Σ X_RC²σ² is the instant primary
answer; MC is the optional band.
