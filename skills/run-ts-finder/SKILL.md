---
name: run-ts-finder
description: Locate and rigorously verify a transition state from two endpoints. Use when asked to find/optimise/locate a transition state or saddle point, build a GSM string, get near a TS with g-xTB, refine a TS in Gaussian or ORCA, verify an imaginary frequency/mode, run IRC, or confirm a TS connects the right reactant/product. Drives g-xTB+GSM → Gaussian/ORCA → freq/IRC/endpoint verification, and records workarounds it discovers.
---

# run-ts-finder

Finds a transition state from **two endpoints** (or approximations) and **proves it
is the right one**. Pipeline: get near the saddle cheaply with **g-xTB + GSM**
(Growing String Method), refine to a true saddle in **Gaussian or ORCA** at the
project level of theory, then run the **verification cascade** — NImag=1, the
imaginary mode *looks like the reaction*, IRC both directions, and endpoint opts
that land on the intended reactant/product. When a run gets stuck it escalates
(GSM-coupled-to-QM) and **records the fix** so the skill improves.

Everything is driven by one self-contained script (Python 3.8+ stdlib;
matplotlib optional for plots):
**`~/.claude/skills/run-ts-finder/driver.py`** (call it `$D` below).
It is the harness — a markdown file can't run GSM. Each subcommand prints
`PASS`/`FAIL`/`WARN` so you can branch on results.

```bash
D=~/.claude/skills/run-ts-finder/driver.py
python3 $D -h          # list subcommands
python3 $D selftest    # parser sanity check — runs anywhere, no chemistry software needed
```

## Site setup (do this once per machine)

All machine specifics live in **`config.json`** next to the driver (copy
`config.example.json` and edit; `$RUN_TS_FINDER_CONFIG` can point elsewhere).
Precedence: CLI flags > environment (`$GXTB`, `$GSM_INFRA`) > config.json > defaults.

What the config names:

- **`gxtb`** — an xtb binary with **g-xTB v2.0.0+ (analytic gradients)**.
  Get it from https://github.com/grimme-lab/g-xtb/releases (the
  `xtb-*-gxtb-*-linux-x86_64.tar.xz` asset; verify the `.sha256`).
- **`gsm_infra`** — a directory with the **molecularGSM** `gsm.orca` binary and
  `tm2orca.py` (https://github.com/ZimmermanGroup/molecularGSM). A default
  `inpfileq_template` is bundled with the skill; a site copy in `gsm_infra`
  overrides it.
- **`extra_lib_paths`** — lib dirs `gsm.orca` needs (e.g. MKL, if it links `libmkl_rt`).
- **`gaussian_route` / `orca_route`** — the project level of theory.
- **`submit.gaussian` / `submit.orca`** — your site's queue-submit command
  templates (`{input} {mem} {cpus} {queue}` placeholders). Empty = the driver
  writes inputs and prints them; you submit your usual way.

Then check everything and confirm the g-xTB build:

```bash
python3 $D doctor       # PASS/FAIL per prerequisite — fix FAILs before running
python3 $D setup-gxtb   # PASS ANALYTIC gradients confirmed (behavioural test)
```

`setup-gxtb` matters: **do not trust `--version`** — g-xTB releases keep the base
xtb version label. The driver proves analytic gradients behaviourally (single
SCF, analytic component breakdown; see Gotchas).

## Run (agent path)

Full flow, each step verified. `$D = ~/.claude/skills/run-ts-finder/driver.py`.

**1 — GSM string from two endpoint XYZ files** (identical atom order required;
strictly-linear inputs are auto-perturbed). Writes `scratch/tsq0000.xyz` and a
`string_profile.png`.

```bash
python3 $D gsm reactant.xyz product.xyz --charge 0 --mult 1 --nnodes 9 --workdir ./gsm_run
# PASS exact-TS wrote tsq.xyz → ./gsm_run/scratch/tsq0000.xyz   (+ string_profile.png)
```

For a quick sanity check use HCN→HNC (`--charge 0 --nnodes 7`); it converges in
seconds and is the fastest way to confirm the whole chain works on a new machine.

**2 — Refine to a real saddle** at the project level of theory. Pick a backend;
`--submit` queues it via your configured template (omit to just generate the
input and print the submit line).

```bash
python3 $D refine ./gsm_run/scratch/tsq0000.xyz --backend gaussian --charge 0 --mult 1 --submit
python3 $D refine ./gsm_run/scratch/tsq0000.xyz --backend orca     --charge 0 --mult 1 --submit
# → writes <run>_tsq.inp (gaussian) / <run>_tsq_orca.inp (orca), prints the queue id if submitted
```

**3 — Verify the saddle** (the point of the skill). Works on Gaussian logs AND
ORCA outputs:

```bash
python3 $D verify ts.log [--reactant reactant.xyz --product product.xyz]
# PASS NImag = 1  (ν = -1016.8 cm⁻¹)
#   largest imaginary-mode displacements + bonds changing most along the mode (|Δ|)
#   WARN if |ν| < 100 cm⁻¹ (floppy/spurious, not a reaction coordinate)
```

It reports the dominant atomic motion and which bonds change along the imaginary
mode so you can confirm it *looks like the reaction*. With `--reactant/--product`
(matching atom order) it also prints `|cos(mode, product−reactant)|` — expect >0.5
for the right saddle.

**4 — IRC both directions, then endpoint opts:**

```bash
python3 $D irc ts.log            # → ts_irc.inp  (Gaussian IRC=(CalcFC,MaxPoints=30,StepSize=10,Both),
                                 #    or ORCA `! ... IRC`, reusing ts.hess if present)
# submit, wait, then:
python3 $D endpoints ts_irc.log  # → *_irc_fwd.inp / *_irc_rev.inp endpoint Opt+Freq inputs
                                 #   (ORCA: picks up the <base>_IRC_F.xyz/_IRC_B.xyz files)
```

Optimise both endpoints and confirm `NImag=0` (`verify` again) and that each
matches the intended reactant/product (use `status`).

**5 — Progress / energetics:**

```bash
python3 $D status ./gsm_run      # per-file table: backend, termination, NImag, E, G (+ GSM barrier)
```

**Self-improvement.** When you find a fix that works, record it — it appends to
the **Workarounds** section at the bottom of this file AND to `LEARNINGS.md`:

```bash
python3 $D record "GSM exact-TS stalled with SCF failures on the cationic endpoint" \
                  "Pre-relaxed the product fragment with xtb --gxtb --opt before building the string"
```

**Stuck?** Escalate to a QM-level string (queue-only; scaffolded for PBS or SLURM):

```bash
python3 $D escalate ./gsm_run --scheduler slurm   # writes gsm.job; edit ograd to call
                                                  # your QM backend per node, then sbatch/qsub
```

## Backends

| | Gaussian | ORCA |
|---|---|---|
| TS route (default) | `Opt=(TS,CalcFC,NoEigen,MaxCycles=200) Freq` | `! ... OptTS NumFreq` + `%geom Calc_Hess true end` |
| submit | `submit.gaussian` template from config.json | `submit.orca` template from config.json |
| `verify` | parses logs: NImag, imaginary mode, overlap | parses outputs: NImag, imaginary mode, overlap |
| `irc` / `endpoints` | IRC=(…,Both) input; endpoint geoms from the log | `! ... IRC` input (reuses `.hess`); endpoints from `_IRC_F/_IRC_B.xyz` |

Charge/mult/route/queue are CLI flags with config.json defaults (built-in
fallback: charge 0, mult 1, B3LYP-D3BJ/def2-SVP). Set your project level of
theory in config.json; override per run with `--route`.

## Gotchas (battle scars)

- **g-xTB MUST be an analytic-gradient build (v2.0.0+).** GSM evaluates gradients
  thousands of times and the exact-TS step is gradient-driven; v1.x numerical
  gradients (≈6N+1 SCFs each) are ~100× slower and noisy, and the exact-TS
  optimiser converges to garbage. `setup-gxtb` asserts this behaviourally.
- **g-xTB releases keep the base xtb version label** (e.g. v2.0.1 still reports
  "xtb version 6.7.1"), so `--version` cannot confirm an update — the tell is
  the commit hash/date and, decisively, the *behaviour*: analytic component
  breakdown + sub-second `--gxtb --grad` on a small molecule.
- **NImag=1 is necessary, not sufficient.** A tiny imaginary (|ν| < ~100 cm⁻¹) is a
  floppy/spurious mode, not a reaction coordinate — `verify` WARNs on it. Always
  look at *what moves* in the imaginary mode, not just the count.
- **GSM races on `scratch/gradient`** if `ograd` shares a work dir across parallel
  node calls. The driver-generated `ograd` uses a per-id `wrk_<id>/` dir; the GSM
  report flags `mv: cannot stat 'scratch/gradient'` if a stale ograd reintroduces it.
- **`gRMS: 0.0000` in gsm.out is a logging artefact** of the molecularGSM build,
  not a real zero gradient. The driver instead checks that per-node string energies
  *vary between iterations* to confirm gradients are flowing.
- **Strictly-linear endpoints (HCN, CO₂) crash GSM** with `bad spacings`/NaNs. The
  driver auto-perturbs atom 2 by 0.05 Å; for hand builds, bend each endpoint slightly.
- **Submit from a SHARED filesystem, never `/tmp`.** On most clusters `/tmp` is
  node-local: a job submitted from `/tmp/...` is *accepted* (you get a job ID) but
  dies on the compute node with `No such file or directory` for the `.inp`. Run
  from under `$HOME` or the project dir — the driver WARNs if it sees this.
- **Site submit wrappers often live only in login-shell PATHs.** The driver runs
  submit templates via `bash -lc` from the input's directory and passes the
  basename — some wrappers double the path prefix if handed an absolute path.
- **`gen`/`pseudo=read` routes need hand-finishing.** The driver writes the
  coordinates but cannot know your basis/ECP blocks; it WARNs so you append them
  before submitting.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `doctor` FAILs on g-xTB / GSM infra | Fill in `config.json` (see Site setup); re-run `doctor` until clean. |
| `setup-gxtb` says could not confirm analytic gradients | Install g-xTB v2.0.1+ from the grimme-lab releases; point `gxtb`/`$GXTB` at it. |
| `error while loading shared libraries: libmkl_rt.so.2` | Add the MKL lib dir to `extra_lib_paths` in config.json. |
| GSM: `Could not find tag for Default Info` | `inpfileq` missing the `QCHEM Scratch Info` block; use the bundled/site template (the driver copies it). |
| `verify`: "could not parse the normal-mode block" | Gaussian: add `Freq` and rerun. ORCA: ensure the output has `VIBRATIONAL FREQUENCIES` + `NORMAL MODES` sections. |
| No `tsq0000.xyz` after GSM | Read `gsm.out` for SCF failure / bad Hessian / race; rebuild endpoints cleaner or `escalate`. |
| Job accepted but dies with `No such file or directory` | You submitted from `/tmp` or another node-local dir; move to a shared filesystem. |

## Run (human path)

There is none worth using headless — GSM and the QM jobs are batch/queue tools.
The driver *is* the interface; do not try to "open" anything.

## Workarounds (self-recorded)

_Appended by `driver.py record`. Newest first._
