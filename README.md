# chem-skills

Computational-chemistry [Claude Code](https://claude.com/claude-code) skills.
Each skill is a self-contained directory under [`skills/`](skills/) with a
`SKILL.md` (agent-facing instructions) and whatever driver/harness it needs.

## Skills

| Skill | What it does |
|---|---|
| [`run-ts-finder`](skills/run-ts-finder/) | Locate **and rigorously verify** a transition state from two endpoints: g-xTB + GSM (growing string) → Gaussian **or** ORCA refinement at the project level of theory → verification cascade (NImag=1, imaginary mode ↔ reaction-vector overlap, IRC, endpoint opts). Self-improving: records workarounds it discovers. |
| [`run-mkm`](skills/run-mkm/) | **Differentiable micro-kinetic analysis** of a computed reaction network. DFT free energies → rate constants → steady-state TOF, **degree of rate control** (Campbell X\_RC), reaction orders, apparent Ea, **energetic span** (Kozuch–Shaik) cross-check, **kinetic isotope effect** (intrinsic → network-apparent), and DFT-uncertainty propagation. JAX + SciPy: steady state by BDF+Newton, exact autodiff sensitivities via the implicit function theorem. Pure Python — no cluster needed. |

## Install

Symlink every skill into `~/.claude/skills/` so Claude Code auto-discovers them:

```bash
git clone git@github.com:joshazerty/chem-skills.git
cd chem-skills
./install.sh
```

`install.sh` creates one symlink per skill (`~/.claude/skills/<name>` → this repo),
so the repo stays the single source of truth — edit here, the live skill updates.
Re-run it after pulling new skills.

## Environment notes

`run-mkm` is pure-Python and machine-independent (`pip install numpy scipy jax
jaxlib matplotlib`, then `driver.py selftest`). The QM-driver skills below are
tuned for **joshua's cluster** (Gaussian via `subgau16`, ORCA via
`/home/janko/Scripts/suborc6`, g-xTB at `/home/joshua/bins/gxtb/...`, GSM infra at
`/home/joshua/dodh/ts/_gsm_infra`, Torque queue `m0311`). On a different machine,
override the paths the drivers expose — `run-ts-finder/driver.py` reads `GXTB` and
`GSM_INFRA` from the environment, and charge/mult/route/queue are CLI flags. See
each skill's `SKILL.md` Prerequisites + Gotchas.

## Adding a skill

Drop a new `skills/<name>/` directory with a `SKILL.md` (frontmatter `name:` =
`<name>`), commit, and re-run `./install.sh`.
