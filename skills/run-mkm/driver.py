#!/usr/bin/env python3
"""run-mkm driver: differentiable micro-kinetic analysis of a computed network.

This script IS the harness -- a markdown file can't integrate a stiff ODE or
autodiff a steady state. Every subcommand prints PASS / FAIL / WARN so the agent
can branch on the result. See SKILL.md for the guided flow.

    python3 driver.py -h                 # subcommands
    python3 driver.py selftest           # fast end-to-end check (no files needed)
    python3 driver.py report NET.json    # run everything, write figures + JSON
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# make `import mkm` work no matter where the driver is invoked from (it is
# usually symlinked into ~/.claude/skills/, so add its real directory).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np


def _load(path):
    from mkm import Network, Model
    net = Network.load(path)
    return net, Model(net)


def _p(tag, msg):
    print(f"{tag} {msg}")


# --------------------------------------------------------------------------
def cmd_check(args):
    from mkm import Network, Model, validate
    net = Network.load(args.network)
    fails = 0
    for lvl, msg in validate(net):
        _p(lvl, msg); fails += lvl == "FAIL"
    m = Model(net)
    kf, kr = m.rate_constants(m.theta0)
    print(f"\n{net.name}: {net.ns} species, {net.nr} reactions, T={net.T} K, "
          f"{m.n_cons} conservation law(s)")
    print("  reaction    kf            kr            Keq")
    for j, rx in enumerate(net.reactions):
        keq = float(kf[j] / kr[j]) if float(kr[j]) else float("inf")
        print(f"  {rx.id:<8} {float(kf[j]):.3e}   {float(kr[j]):.3e}   {keq:.3e}")
    if fails:
        _p("FAIL", f"{fails} fatal problem(s); fix before analysing.")
        return 1
    _p("PASS", "network is well-posed and thermodynamically consistent.")
    return 0


def cmd_steady(args):
    net, m = _load(args.network)
    full, y = m.steady_state()
    import jax.numpy as jnp
    resid = float(jnp.linalg.norm(m.residual(jnp.asarray(y), m.theta0)))
    tof = m.tof()
    rates = m.net_rates()
    revs = m.reversibilities()
    print(f"{net.name}: steady state (residual ||g|| = {resid:.1e})")
    print("  species     conc / M")
    for s, c in zip(net.species, full):
        flag = "  (fixed)" if s.fixed else ""
        print(f"  {s.name:<10} {c:.4e}{flag}")
    print("\n  reaction    net rate / M s^-1   reversibility")
    for j, rx in enumerate(net.reactions):
        print(f"  {rx.id:<8}    {rates[j]:+.4e}       {revs[j]:+.3f}")
    print(f"\nTOF ({m.objective_label}) = {tof:.4e} per catalyst per s")
    if resid > 1e-6:
        _p("WARN", "residual is large; steady state may not be converged.")
    else:
        _p("PASS", "converged steady state; step fluxes should be equal for a "
                   "single cycle.")
    return 0


def cmd_simulate(args):
    net, m = _load(args.network)
    if args.plot:
        from mkm import plots
        out = args.out or f"{net.name}_transient.png"
        plots.concentration_profile(m, out, tmax=args.tmax)
        _p("PASS", f"wrote transient concentration profile -> {out}")
    else:
        ts = np.logspace(-10, np.log10(args.tmax or m.cfg.t_relax), 8)
        t, C = m.transient(ts)
        print("  time/s    " + "  ".join(f"{s.name:>9}" for s in net.species))
        for k in range(len(t)):
            print(f"  {t[k]:.2e}  " + "  ".join(f"{C[i,k]:9.3e}" for i in range(net.ns)))
        _p("PASS", "transient integrated.")
    return 0


def cmd_drc(args):
    net, m = _load(args.network)
    from mkm import sensitivity as sn
    xrc, total = sn.degree_of_rate_control(m)
    xtrc = sn.thermodynamic_rate_control(m)
    print("Degree of rate control  X_RC,i = -RT d ln(TOF)/dG_ts,i  (Campbell)")
    for rx, x in zip(net.reactions, xrc):
        bar = "" if np.isnan(x) else "#" * int(round(20 * min(abs(x), 1.0)))
        val = "  n/a (barrierless)" if np.isnan(x) else f"{x:+.4f}  {bar}"
        print(f"  {rx.id:<8} {val}")
    print(f"  sum over transition states = {total:+.4f}  "
          f"({'ok, ~1' if abs(total-1) < 0.05 else 'off from 1 -> multiple paths / off-cycle'})")
    print("\nDegree of thermodynamic rate control  X_TRC,n = -RT d ln(TOF)/dG_n")
    for s, x in zip(net.species, xtrc):
        if abs(x) > 1e-3:
            print(f"  {s.name:<10} {x:+.4f}")
    if args.plot:
        from mkm import plots
        out = args.out or f"{net.name}_drc.png"
        plots.drc_bar({rx.id: (0.0 if np.isnan(x) else float(x))
                       for rx, x in zip(net.reactions, xrc)}, out)
        _p("PASS", f"wrote DRC bar chart -> {out}")
    _p("PASS", "degree-of-rate-control analysis complete (exact via autodiff+IFT).")
    return 0


def cmd_orders(args):
    net, m = _load(args.network)
    from mkm import sensitivity as sn
    orders = sn.reaction_orders(m)
    Ea = sn.apparent_activation_energy(m)
    print("Apparent reaction orders  n_X = d ln(TOF)/d ln[X]  (reservoirs)")
    for k, v in orders.items():
        print(f"  order in {k:<8} = {v:+.4f}")
    print(f"\nApparent activation energy  Ea_app = R T^2 d ln(TOF)/dT "
          f"= {Ea:+.3f} kcal/mol")
    _p("PASS", "orders and apparent Ea are exact autodiff derivatives of the TOF.")
    return 0


def cmd_sensitivity(args):
    net, m = _load(args.network)
    from mkm import sensitivity as sn
    names, mat = sn.parametric_sensitivity(m)
    print("Parametric sensitivity  d ln C_i / d(-G_ts,j/RT)")
    header = "  species     " + "  ".join(f"{rx.id:>8}" for rx in net.reactions)
    print(header)
    for i, nm in enumerate(names):
        print(f"  {nm:<10}  " + "  ".join(f"{mat[i,j]:+8.3f}" for j in range(net.nr)))
    if args.plot:
        from mkm import plots
        out = args.out or f"{net.name}_sensitivity.png"
        plots.sensitivity_heatmap(names, mat, [rx.id for rx in net.reactions], out)
        _p("PASS", f"wrote sensitivity heatmap -> {out}")
    _p("PASS", "parametric sensitivity matrix computed.")
    return 0


def cmd_span(args):
    net, m = _load(args.network)
    from mkm import span as sp
    try:
        res = sp.energetic_span(net)
    except ValueError as e:
        _p("WARN", f"energetic span model does not apply: {e}")
        return 0
    tof_num = m.tof()
    print("Energetic span model (Kozuch-Shaik) vs numerical MKM")
    print(f"  TDTS = {res['TDTS']}   TDI = {res['TDI']}")
    print(f"  energetic span dE   = {res['dE_kcal']:+.2f} kcal/mol")
    print(f"  driving force dG_r  = {res['dG_r_kcal']:+.2f} kcal/mol")
    print(f"  TOF (span)          = {res['tof_span']:.4e} /s")
    print(f"  TOF (numerical MKM) = {tof_num:.4e} /s")
    ratio = res["tof_span"] / tof_num if tof_num else float("inf")
    print(f"  ratio span/numeric  = {ratio:.3f}")
    print("  X_TOF over TS:  " + ", ".join(f"{k} {v:.2f}" for k, v in res["xtof_ts"].items()))
    print("  X_TOF over int: " + ", ".join(f"{k} {v:.2f}" for k, v in res["xtof_int"].items()))
    if args.plot:
        from mkm import plots, span as sp2
        out = args.out or f"{net.name}_span.png"
        plots.energy_span_profile(sp2.build_profile(net), res, out)
        _p("PASS", f"wrote energy-span profile -> {out}")
    if 0.2 < ratio < 5:
        _p("PASS", "span model and numerical MKM agree -> single dominant cycle.")
    else:
        _p("WARN", "span and numerical TOF disagree -> branching / off-cycle "
                   "reservoirs; trust the numerical MKM, not the span.")
    return 0


def cmd_kie(args):
    net, m = _load(args.network)
    from mkm import kie
    step_kies = {}
    for tok in args.label:
        sid, val = tok.split("=")
        step_kies[sid] = float(val)
    res = kie.network_kie(m, step_kies)
    print("Kinetic isotope effect (network-apparent)")
    for sid, d in res["per_step"].items():
        print(f"  labelled {sid}: intrinsic KIE = {d['intrinsic']:.3f}, "
              f"X_RC = {d['X_RC']:+.3f}")
    print(f"  TOF(light) = {res['tof_light']:.4e}   TOF(heavy) = {res['tof_heavy']:.4e}")
    print(f"  apparent KIE (exact re-solve)   = {res['apparent_kie']:.3f}")
    print(f"  DRC-weighted estimate           = {res['drc_weighted_kie']:.3f}")
    _p("PASS", "apparent KIE = intrinsic masked by degree of rate control.")
    return 0


def cmd_uncertainty(args):
    net, m = _load(args.network)
    from mkm import uncertainty as un
    vd = un.variance_decomposition(m, sigma=args.sigma)
    print(f"DFT uncertainty propagation (sigma = {args.sigma} kcal/mol per barrier)")
    print(f"  analytic sigma(ln TOF) = {vd['sigma_lnTOF']:.3f}  "
          f"-> TOF uncertain to a factor ~{vd['factor_1sigma']:.2f} (1 sigma)")
    print("  variance share by barrier (= X_RC^2):")
    for k, s in sorted(vd["shares"].items(), key=lambda kv: -kv[1]):
        if s > 1e-3:
            print(f"    {k:<8} {100*s:5.1f}%")
    if args.mc:
        mc = un.monte_carlo_tof(m, sigma=args.sigma, n=args.n)
        print(f"\n  Monte Carlo (n={mc['n']}): median TOF = {mc['median']:.3e}, "
              f"90% band [{mc['p05']:.3e}, {mc['p95']:.3e}]")
        print(f"  {100*mc['frac_within_decade']:.0f}% of samples within a decade of nominal")
        if args.plot:
            from mkm import plots
            out = args.out or f"{net.name}_uncertainty.png"
            plots.uncertainty_hist(mc, out)
            _p("PASS", f"wrote TOF uncertainty histogram -> {out}")
    _p("PASS", "the rate-controlling step is the uncertainty-controlling step.")
    return 0


def cmd_report(args):
    net, m = _load(args.network)
    from mkm import sensitivity as sn, span as sp, uncertainty as un, plots
    outdir = args.out or f"{net.name}_report"
    os.makedirs(outdir, exist_ok=True)
    report = {"network": net.name, "T": net.T}

    full, y = m.steady_state()
    report["tof"] = m.tof()
    report["steady_state"] = {s.name: float(c) for s, c in zip(net.species, full)}
    report["reversibility"] = {rx.id: float(v) for rx, v in zip(net.reactions, m.reversibilities())}

    xrc, tot = sn.degree_of_rate_control(m)
    report["degree_of_rate_control"] = {rx.id: (None if np.isnan(x) else float(x))
                                        for rx, x in zip(net.reactions, xrc)}
    report["drc_sum"] = tot
    report["orders"] = sn.reaction_orders(m)
    report["apparent_Ea_kcal"] = sn.apparent_activation_energy(m)

    try:
        report["energetic_span"] = sp.energetic_span(net)
        plots.energy_span_profile(sp.build_profile(net), report["energetic_span"],
                                  os.path.join(outdir, "energy_span.png"))
    except ValueError as e:
        report["energetic_span"] = f"n/a: {e}"

    report["uncertainty"] = un.variance_decomposition(m, sigma=args.sigma)

    plots.concentration_profile(m, os.path.join(outdir, "transient.png"))
    plots.drc_bar({rx.id: (0.0 if np.isnan(x) else float(x))
                   for rx, x in zip(net.reactions, xrc)},
                  os.path.join(outdir, "drc.png"))
    names, mat = sn.parametric_sensitivity(m)
    plots.sensitivity_heatmap(names, mat, [rx.id for rx in net.reactions],
                              os.path.join(outdir, "sensitivity.png"))
    if args.mc:
        mc = un.monte_carlo_tof(m, sigma=args.sigma, n=args.n)
        report["uncertainty"]["mc_median"] = mc["median"]
        report["uncertainty"]["mc_90pct"] = [mc["p05"], mc["p95"]]
        plots.uncertainty_hist(mc, os.path.join(outdir, "uncertainty.png"))

    with open(os.path.join(outdir, "report.json"), "w") as fh:
        json.dump(report, fh, indent=2, default=float)
    _p("PASS", f"full report + figures written to {outdir}/")
    print(f"  TOF = {report['tof']:.4e} /s | Ea_app = {report['apparent_Ea_kcal']:.1f} kcal/mol "
          f"| DRC sum = {tot:.3f}")
    return 0


def cmd_selftest(args):
    """End-to-end check on the bundled example; no user files needed."""
    here = os.path.dirname(os.path.abspath(__file__))
    net_path = os.path.join(here, "examples", "catalytic_cycle.json")
    from mkm import Network, Model
    from mkm import sensitivity as sn, span as sp, kie
    net = Network.load(net_path); m = Model(net)
    import jax.numpy as jnp
    _, y = m.steady_state()
    resid = float(jnp.linalg.norm(m.residual(jnp.asarray(y), m.theta0)))
    tof = m.tof()
    xrc, tot = sn.degree_of_rate_control(m)
    span = sp.energetic_span(net)
    ratio = span["tof_span"] / tof
    nk = kie.network_kie(m, {"r3": 7.0})
    ok = True
    def chk(name, cond):
        nonlocal ok
        _p("PASS" if cond else "FAIL", name); ok = ok and cond
    chk(f"steady state converged (||g||={resid:.1e} < 1e-8)", resid < 1e-8)
    chk(f"TOF finite and positive ({tof:.3e})", tof > 0 and np.isfinite(tof))
    chk(f"DRC sum rule (sum={tot:.3f} ~ 1)", abs(tot - 1) < 0.02)
    chk(f"span TOF matches numerical (ratio={ratio:.3f})", 0.5 < ratio < 2.0)
    chk(f"span TDTS is r3 ({span['TDTS']})", span["TDTS"] == "r3")
    chk(f"apparent KIE on rate-limiting step ~ intrinsic "
        f"({nk['apparent_kie']:.2f} vs 7.0)", abs(nk["apparent_kie"] - 7.0) < 0.5)
    if ok:
        _p("PASS", "run-mkm self-test passed end-to-end.")
        return 0
    _p("FAIL", "self-test failed; environment or code regression.")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def net_arg(p):
        p.add_argument("network", help="path to the reaction-network JSON")

    p = sub.add_parser("check", help="validate + rate constants + consistency"); net_arg(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("steady", help="steady-state concentrations, fluxes, TOF"); net_arg(p)
    p.set_defaults(func=cmd_steady)

    p = sub.add_parser("simulate", help="transient integration (optionally plot)"); net_arg(p)
    p.add_argument("--tmax", type=float, default=None); p.add_argument("--plot", action="store_true")
    p.add_argument("--out", default=None); p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("drc", help="degree of rate control + thermodynamic control"); net_arg(p)
    p.add_argument("--plot", action="store_true"); p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_drc)

    p = sub.add_parser("orders", help="apparent reaction orders + apparent Ea"); net_arg(p)
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("sensitivity", help="parametric sensitivity matrix"); net_arg(p)
    p.add_argument("--plot", action="store_true"); p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_sensitivity)

    p = sub.add_parser("span", help="energetic span cross-check (Kozuch-Shaik)"); net_arg(p)
    p.add_argument("--plot", action="store_true"); p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_span)

    p = sub.add_parser("kie", help="network-apparent KIE for labelled step(s)"); net_arg(p)
    p.add_argument("--label", nargs="+", required=True,
                   help="step_id=intrinsic_KIE, e.g. r3=7.0")
    p.set_defaults(func=cmd_kie)

    p = sub.add_parser("uncertainty", help="propagate DFT barrier error to TOF"); net_arg(p)
    p.add_argument("--sigma", type=float, default=1.5); p.add_argument("--mc", action="store_true")
    p.add_argument("--n", type=int, default=1000); p.add_argument("--plot", action="store_true")
    p.add_argument("--out", default=None); p.set_defaults(func=cmd_uncertainty)

    p = sub.add_parser("report", help="run everything, write figures + report.json"); net_arg(p)
    p.add_argument("--sigma", type=float, default=1.5); p.add_argument("--mc", action="store_true")
    p.add_argument("--n", type=int, default=400); p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("selftest", help="end-to-end check on the bundled example")
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
