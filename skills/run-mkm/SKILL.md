---
name: run-mkm
description: Differentiable micro-kinetic analysis of a computed reaction network. Use when asked to build/run a micro-kinetic model (MKM), turn DFT free energies (intermediates + transition states) into rate constants and a turnover frequency (TOF), find the rate-determining step / degree of rate control (Campbell X_RC), get apparent reaction orders or apparent activation energy, run an energetic-span (Kozuch-Shaik) analysis, compute a kinetic isotope effect (KIE) or the apparent (network-masked) KIE, do sensitivity analysis on a catalytic cycle, or propagate DFT barrier uncertainty to the TOF. Drives a JAX + SciPy engine: steady state by BDF+Newton, then exact autodiff sensitivities via the implicit function theorem.
---

# run-mkm

Turns a **computed reaction network** — the free energies of every intermediate
and transition state from a DFT mechanistic study — into a full micro-kinetic
picture: steady-state concentrations, the **turnover frequency (TOF)**, the
**rate-determining step**, reaction orders, apparent activation energy, kinetic
isotope effects, and how hard your DFT error bars hit the answer.

The engine is **differentiable end-to-end**. The forward problem (steady state)
is solved with a battle-tested stiff integrator (SciPy **BDF**) plus a Newton
polish; sensitivities are then **exact and cheap** via the **implicit function
theorem** with **JAX autodiff** supplying every Jacobian — not finite
differences. That single design choice is what makes the degree of rate control,
reaction orders, and apparent Ea *exact derivatives of the TOF* that satisfy
their known sum rules by construction.

Everything is driven by one script:
**`~/.claude/skills/run-mkm/driver.py`** (call it `$D` below). It is the harness
— a markdown file can't integrate a stiff ODE or autodiff a steady state. Each
subcommand prints `PASS`/`FAIL`/`WARN` so you can branch.

```bash
D=~/.claude/skills/run-mkm/driver.py
python3 $D -h            # list subcommands
python3 $D selftest      # end-to-end check on the bundled example (no files needed)
```

## What it computes

| Quantity | Command | How |
|---|---|---|
| Rate constants, thermodynamic consistency | `check` | Eyring TST from free energies; reverse from Δ*G*ᵣₓₙ so every cycle is consistent by construction |
| Steady-state concentrations, fluxes, TOF, reversibility | `steady` | BDF integration → Newton polish of the conservation-bordered residual |
| Transient concentration profiles | `simulate --plot` | BDF trajectory |
| **Degree of rate control** X\_RC (Campbell) + thermodynamic control X\_TRC | `drc` | `-RT · ∂ln(TOF)/∂G` by autodiff+IFT; Σ X\_RC ≈ 1 sum rule checked |
| Apparent **reaction orders** + apparent **activation energy** | `orders` | `∂ln(TOF)/∂ln[X]` and `RT²·∂ln(TOF)/∂T` by autodiff |
| Parametric sensitivity matrix ∂ln C_i/∂(−G_TS,j/RT) | `sensitivity` | one linear solve through the steady-state Jacobian |
| **Energetic span** (Kozuch–Shaik): TDTS, TDI, δ*E*, span-TOF | `span` | analytic cross-check vs the numerical TOF |
| **Kinetic isotope effect**: intrinsic → network-apparent | `kie` | re-solve heavy MKM; apparent KIE = intrinsic masked by X\_RC |
| **DFT uncertainty** → TOF spread | `uncertainty [--mc]` | analytic Var[ln TOF]=Σ X\_RC²σ²; optional Monte Carlo |
| Everything + figures + `report.json` | `report` | one shot |

## Prerequisites

Pure-Python numerics — **no cluster, no QM package needed** (unlike `run-ts-finder`).
Install once into the session's Python:

```bash
python3 -m pip install numpy scipy jax jaxlib matplotlib
python3 ~/.claude/skills/run-mkm/driver.py selftest   # PASS x7 confirms the stack
```

`jax` is the autodiff core (Jacobians, adjoint sensitivities); `scipy` provides
the robust BDF integrator for the forward steady-state solve; `matplotlib` draws
the figures (Agg backend, headless-safe). x64 is enabled automatically — kinetics
spans many orders of magnitude and float32 is not enough.

## Input — a computed reaction network (JSON)

Free energies for every species and every TS, relative to a common reference.
This is exactly what you already have at the end of a mechanism study.

```json
{
  "name": "my_cycle",
  "T": 298.15,
  "energy_unit": "kcal/mol",          // kcal/mol | kJ/mol | J/mol | eV | hartree
  "standard_state": 1.0,              // mol/L the barriers are referenced to
  "species": [
    {"name": "cat",    "G": 0.0,   "conc0": 1e-3, "catalyst": true},
    {"name": "A",      "G": 0.0,   "conc0": 1.0,  "fixed": true},
    {"name": "P",      "G": -22.0, "conc0": 0.01, "fixed": true},
    {"name": "cat_A",  "G": -4.0,  "conc0": 0.0,  "catalyst": true}
  ],
  "reactions": [
    {"id": "r1", "reactants": {"cat":1,"A":1}, "products": {"cat_A":1},
     "G_ts": 6.0, "nu_imag": 1100.0}
  ],
  "objective": {"type": "species", "name": "P"}
}
```

- **`fixed: true`** — reservoir held constant (the overall reactant/product).
  A genuine *turnover* steady state needs these; without them the network just
  runs to equilibrium.
- **`catalyst: true`** — catalyst-bearing; their `conc0` sum is the conserved
  total that normalises the TOF (turnovers per catalyst per second). Conservation
  is auto-detected from the stoichiometry (left null space), so multiple
  catalysts / site balances work too.
- **`G_ts`** — transition-state free energy. Omit or `null` for a **barrierless**
  step (forward barrier = max(0, Δ*G*ᵣₓₙ), still thermodynamically consistent).
- **`nu_imag`** — |imaginary frequency| cm⁻¹, optional, enables Wigner tunnelling.
- **`kappa`** — explicit transmission prefactor per step, optional.
- **`objective`** — what the TOF measures: `{"type":"species","name":"P"}`
  (net production per catalyst) or `{"type":"reaction","id":"r4"}` (a step's net rate).

Two ready examples live in `examples/`: `catalytic_cycle.json` (a full A+B→P
cycle with a rate-limiting C–H step) and `ab_isomerization.json` (tiny, for the
self-test). `_comment`/`_note` keys are ignored, so annotate freely.

## Run (agent path)

`$D = ~/.claude/skills/run-mkm/driver.py`. Work through it top to bottom.

**1 — Validate first. Always.** Catches dangling species, bad objectives, and
prints the rate constants + K_eq so you can sanity-check magnitudes.

```bash
python3 $D check my_cycle.json
# PASS network is well-posed and thermodynamically consistent.
```

**2 — Steady state + TOF.** For a single cycle every step's net rate should be
equal (that's the definition of cyclic steady state); the tool shows them so you
can see it, plus per-step reversibility (+1 = irreversible, 0 = quasi-equilibrated).

```bash
python3 $D steady my_cycle.json
# ... TOF (production of P) = 3.67e-05 per catalyst per s
# PASS converged steady state; step fluxes should be equal for a single cycle.
```

**3 — Degree of rate control** — the headline diagnostic. `X_RC,i ≈ 1` marks the
rate-determining TS; the sum over TSs is ≈ 1 for a single dominant path (checked).
`X_TRC` on the intermediates finds the resting state (large negative).

```bash
python3 $D drc my_cycle.json --plot
# r3  +1.0000  ####################   <- rate-determining step
# sum over transition states = +1.0000  (ok, ~1)
```

**4 — Orders + apparent Ea**, exactly what a rate-law or Arrhenius fit would give:

```bash
python3 $D orders my_cycle.json
# order in A = +0.00, order in B = +0.01 ...
# Apparent activation energy Ea_app = +24.1 kcal/mol
```

**5 — Energetic span cross-check** (Kozuch–Shaik). Independent of the numerical
MKM; if the two TOFs agree you have a single dominant cycle and can trust either.
If they diverge the tool WARNs — the network is branching/off-cycle and only the
numerical MKM is valid.

```bash
python3 $D span my_cycle.json --plot
# TDTS = r3   TDI = cat_AB   dE = 23.50 kcal/mol
# TOF (span) = 3.70e-05   TOF (numerical) = 3.67e-05   ratio 1.006
# PASS span model and numerical MKM agree -> single dominant cycle.
```

**6 — Kinetic isotope effect.** Give the *intrinsic* KIE of the labelled step(s);
the tool re-solves the heavy-isotopologue MKM and returns the **apparent** KIE the
experiment would measure — which is the intrinsic value **masked by the degree of
rate control**. Label the rate-determining step and the full KIE shows through;
label a fast step and it is masked toward 1.

```bash
python3 $D kie my_cycle.json --label r3=7.0
# apparent KIE (exact re-solve) = 7.00   DRC-weighted estimate = 7.00
python3 $D kie my_cycle.json --label r1=7.0
# apparent KIE = 1.00   (masked: r1 is not rate-controlling)
```

For the intrinsic KIE itself from vibrational frequencies (Bigeleisen–Mayer RPFR
+ tunnelling) or from an isotopologue barrier difference, use the library:
`from mkm import kie; kie.bigeleisen_kie(...)` / `kie.kie_from_ddg(ddg, T)`.

**7 — DFT uncertainty → TOF.** Because TOF is exponential in the barriers, a
1–2 kcal/mol functional error can swing it orders of magnitude. The analytic
part is instant (variance share = X\_RC²); `--mc` adds a Monte-Carlo band.

```bash
python3 $D uncertainty my_cycle.json --sigma 1.5 --mc --n 1000 --plot
# analytic sigma(ln TOF) = 2.53 -> TOF uncertain to a factor ~12.6 (1 sigma)
# variance share: r3 100%    (rate-controlling step = uncertainty-controlling step)
```

**8 — One-shot report** (all of the above + figures + `report.json`):

```bash
python3 $D report my_cycle.json --mc --n 400 --out my_cycle_report
```

## Methods (what's under the hood)

- **Rate constants** — Eyring/TST `k = κ·(k_B T/h)·exp(−ΔG‡/RT)`, with molecularity-
  aware standard-state factors so bimolecular steps get the right units. Reverse
  constants are built from the **same species free energies**, so K_f/K_r = e^(−ΔGᵣₓₙ/RT)
  exactly — every cycle is thermodynamically consistent and there is no independent
  k_f/k_r drift to worry about.
- **Steady state** — mass-action ODEs `dC/dt = S·r`, reservoirs held fixed,
  integrated with **BDF** (handles >10 orders of magnitude in rate constant),
  then Newton-polished to machine precision on a **conservation-bordered
  residual** (a projector onto the left null space of the stoichiometry keeps the
  catalyst-conservation direction non-singular).
- **Sensitivities** — at the steady state `g(C;θ)=0`, so `dC/dθ = −(∂g/∂C)⁻¹(∂g/∂θ)`
  with both Jacobians from autodiff; an **adjoint** solve gives `∂ln(TOF)/∂θ` for
  *all* parameters at once. Read off X\_RC = −RT·∂ln(TOF)/∂G_TS, X\_TRC = −RT·∂ln(TOF)/∂G,
  orders = ∂ln(TOF)/∂ln[X], Ea = RT²·∂ln(TOF)/∂T.
- **Energetic span** — Kozuch–Shaik on the auto-detected cycle, profile built at
  the *actual reservoir concentrations* (μ = G° + RT ln[C]) so δ*E* and the span
  TOF are directly comparable to the numerical result.
- **Tunnelling** — Wigner (default, bounded) or Bell (parabolic, more accurate but
  only valid for u = hc·ν*/kT < 2π; the code flags when it isn't). See Gotchas.

> An autodiff-through-the-integrator path (diffrax Kvaerno5 + optimistix Newton)
> was prototyped and validated during development, but BDF + IFT proved faster and
> far more robust on real >10-decade stiffness, so the shipped engine uses that and
> depends only on JAX + SciPy. See LEARNINGS.md.

## Gotchas (battle scars)

- **A turnover steady state needs reservoirs.** Without at least one `fixed`
  reactant/product the network just relaxes to equilibrium (flux → 0) and every
  "sensitivity" is meaningless noise at the floating-point floor. `check`/`steady`
  WARN if nothing is fixed. (This was the very first thing that bit the build.)
- **Micro-kinetic stiffness defeats naive autodiff-through-the-integrator.** Rate
  constants here span ~13 orders of magnitude; a one-shot implicit solver (Kvaerno)
  can stall with the Newton step collapsing to dt→0 and make *zero* progress. The
  engine therefore uses SciPy BDF for the forward solve and gets exact gradients
  from the **IFT at the converged state**, not by differentiating the time march.
  It's faster, more robust, *and* exact.
- **Barrierless steps have no X\_RC.** There is no TS free energy to perturb, so
  `drc` prints `n/a` for them (internally their TS energy is masked out of the
  autodiff path — carrying a NaN barrier through `jnp.where` silently poisons
  every gradient with `0*NaN`, so barrierless-ness is tracked as a static mask).
- **Bell tunnelling diverges for fast H-transfer at low T.** For |ν*| ≳ 1300 cm⁻¹
  at 298 K, u = hc·ν*/kT approaches 2π and the parabolic-barrier κ blows up
  (a 1350 cm⁻¹ mode gave κ_H/κ_D ≈ 1700 — nonsense). The KIE code **defaults to
  Wigner** (bounded) and reports `bell_valid: False` when Bell is out of range;
  deep-tunnelling KIE needs an **Eckart** model (documented, not yet shipped —
  see LEARNINGS.md).
- **The span model only applies to a single cycle.** `span` auto-detects the cycle
  and cross-checks its TOF against the numerical MKM; a large `ratio span/numeric`
  is the tell that the network branches or has off-cycle reservoirs — trust the
  numerical MKM, not the span, in that case.
- **The TDI is not simply the lowest intermediate.** It's the intermediate that,
  paired with the highest TS in the turnover *after* it, maximises the span — which
  requires looking one full turnover ahead on the periodically descending profile.
  Picking the global minimum gives the wrong resting state and a wrong δ*E*.
- **Monte-Carlo uncertainty warm-starts from the nominal steady state.** Re-
  integrating from scratch for every draw is ~5× slower and some large-σ draws are
  very stiff; Newton from the nominal root converges in a few steps. Still, MC is
  the *optional* refinement — the analytic X\_RC² variance share is the primary,
  instant answer.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `steady` residual is large / fluxes unequal | Barriers may make the system pathologically stiff; raise `ModelConfig.t_relax` or check for a disconnected species. Newton polish should still finish — a large residual usually means a typo'd stoichiometry. |
| DRC sum ≠ 1 | Multiple competing pathways or off-cycle reservoirs; that's physics, not a bug. Cross-check with `span` (it will also disagree). |
| `span` WARNs "does not apply" | No clean single catalyst cycle was traceable (branched mechanism). Use the numerical `drc`/`sensitivity` instead. |
| `kie` apparent ≪ intrinsic | Expected — the labelled step isn't rate-controlling. Check its `X_RC` in `drc`. |
| import errors | `pip install numpy scipy jax jaxlib diffrax optimistix matplotlib`; then `driver.py selftest`. |

## Self-improvement

When you find a fix or a modelling gotcha, append it to **`LEARNINGS.md`** (newest
first) so the skill compounds — the same way `run-ts-finder` records its workarounds.
Good candidates: an Eckart tunnelling implementation, global (Sobol) sensitivity,
coverage-dependent (BEP/lateral-interaction) rate constants, or micro-kinetic
models with gas-phase partial pressures instead of solution concentrations.
