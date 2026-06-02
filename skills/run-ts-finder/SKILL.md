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
(GSM-coupled-to-QM / QST2) and **records the fix** so the skill improves.

Everything is driven by one script:
**`~/.claude/skills/run-ts-finder/driver.py`** (call it `$D` below).
It is the harness — a markdown file can't run GSM. Each subcommand prints
`PASS`/`FAIL`/`WARN` so you can branch.

```bash
D=~/.claude/skills/run-ts-finder/driver.py
python3 $D -h          # list subcommands
```

## Prerequisites (verified present on this machine)

- **gautools** installed editable (`python3 -c "import gautools"` works); provides
  `gau2xyz/ts2irc/irc2opt/xyz2inp/gau-status/gau-energy` and the parsers the driver reuses.
- **g-xTB v2.0.1** with **analytic gradients** at `/home/joshua/bins/gxtb/xtb-6.7.1/bin/xtb`
  (see Setup — this is non-negotiable; see Gotchas for why).
- **GSM infra** at `/home/joshua/dodh/ts/_gsm_infra/` (`gsm.orca`, `tm2orca.py`, templates).
- **MKL** at `/opt/intel/oneapi/intelpython/python3.12/lib` (gsm.orca links `libmkl_rt.so.2`).
- **QM backends + queue**: Gaussian via `subgau16`, ORCA via `/home/janko/Scripts/suborc6`,
  live Torque (`qsub`/`qstat`), default queue `m0311`.

## Setup — g-xTB v2.0.1 (analytic gradients)

The local build must be the analytic-gradient one. Install (idempotent; keeps the
tarball for provenance):

```bash
cd /home/joshua/bins/gxtb
curl -fL -o gxtb-v2.0.1.tar.xz \
  https://github.com/grimme-lab/g-xtb/releases/download/v2.0.1/xtb-6.7.1-gxtb-140526-linux-x86_64.tar.xz
curl -fsL -o gxtb-v2.0.1.tar.xz.sha256 \
  https://github.com/grimme-lab/g-xtb/releases/download/v2.0.1/xtb-6.7.1-gxtb-140526-linux-x86_64.tar.xz.sha256
sha256sum -c gxtb-v2.0.1.tar.xz.sha256 2>/dev/null || \
  diff <(sha256sum gxtb-v2.0.1.tar.xz | cut -d' ' -f1) <(cut -d' ' -f1 gxtb-v2.0.1.tar.xz.sha256)
tar -xf gxtb-v2.0.1.tar.xz     # extracts in place to ./xtb-6.7.1/ (existing refs keep working)
```

Then **confirm analytic gradients** (do not trust `--version`; v2.0.1 still says "6.7.1"):

```bash
python3 ~/.claude/skills/run-ts-finder/driver.py setup-gxtb
# PASS ANALYTIC gradients confirmed (analytic component breakdown, single SCF)
```

## Run (agent path)

Full flow, each step verified in-container. `$D = ~/.claude/skills/run-ts-finder/driver.py`.

**1 — GSM string from two endpoint XYZ files** (identical atom order required; linear
inputs are auto-perturbed). Writes `scratch/tsq0000.xyz` and a `string_profile.png`.

```bash
python3 $D gsm reactant.xyz product.xyz --charge -1 --mult 1 --nnodes 9 --workdir ./gsm_run
# PASS exact-TS wrote tsq.xyz → ./gsm_run/scratch/tsq0000.xyz   (+ string_profile.png)
```

For a quick sanity check the demo uses HCN→HNC (`--charge 0 --nnodes 7`); it
converges in seconds and is the fastest way to confirm the whole chain works.

**2 — Refine to a real saddle** at the project level of theory. Pick a backend;
`--submit` actually queues it (omit to just generate the input and print the submit line).

```bash
# Gaussian (project default route is baked in; override with --route)
python3 $D refine ./gsm_run/scratch/tsq0000.xyz --backend gaussian --charge -1 --mult 1 --submit
# ORCA (uses suborc6)
python3 $D refine ./gsm_run/scratch/tsq0000.xyz --backend orca --charge -1 --mult 1 --submit
# → prints the queue id, e.g. "subgau16 job id: 717736.diracgw"
#   (verified end-to-end: a 3-atom test TS ran to Normal termination, NImag=1,
#    imaginary-mode↔reaction-vector overlap |cos|=0.99. Submit from $HOME, not /tmp.)
```

**3 — Verify the saddle** (the point of the skill). Run on the freq log:

```bash
python3 $D verify ts.log [--reactant reactant.xyz --product product.xyz]
# PASS NImag = 1  (ν = -1016.8 cm⁻¹)
#   largest imaginary-mode displacements + bonds changing most along the mode (|Δ|)
#   WARN if |ν| < 100 cm⁻¹ (floppy/spurious, not a reaction coordinate)
```

It reports the dominant atomic motion and which bonds change along the imaginary
mode so you can confirm it *looks like the reaction*. With `--reactant/--product`
(matching atom order) it also prints `|cos(mode, product−reactant)|`.

**4 — IRC both directions, then endpoint opts** (thin gautools wrappers):

```bash
python3 $D irc ts.log            # → ts_irc.inp  (IRC=(CalcFC,MaxPoints=30,StepSize=10,Both))
# submit, wait, then:
python3 $D endpoints ts_irc.log  # → *_irc_rev.inp and *_irc_fwd.inp (Opt=(CalcFC,MaxCycles=200) Freq)
```

Optimise both endpoints and confirm `NImag=0` and that each matches the intended
reactant/product (use `status`).

**5 — Progress / energetics:**

```bash
python3 $D status ./gsm_run      # gau-status table + gau-energy ΔG (wraps gautools)
```

**Self-improvement.** When you find a fix that works, record it — it appends to the
**Workarounds** section at the bottom of this file AND to `LEARNINGS.md`:

```bash
python3 $D record "GSM exact-TS stalled with SCF failures on the cationic 6m endpoint" \
                  "Pre-relaxed the product fragment with xtb --gxtb --opt before building the string"
```

**Stuck?** Escalate to a QM-level string (queue-only; scaffolded):

```bash
python3 $D escalate ./gsm_run    # writes gsm.job; edit ograd to call subgau16/suborc6, then qsub
```

## Backends

| | Gaussian | ORCA |
|---|---|---|
| submit | `subgau16 --memory 32 --cpus 8 --queue m0311 --input x.inp` | `/home/janko/Scripts/suborc6 --input x.inp --memory 32 --cpus 8 --queue m0311` |
| TS route | `Opt=(TS,CalcFC,NoEigen,MaxCycles=200) Freq` | `! ... OptTS NumFreq` + `%geom Calc_Hess true end` |
| ran end-to-end | **yes** — g16 TS+Freq → Normal termination, NImag=1 | **yes** — ORCA 6.0.1 OptTS+Freq → TERMINATED NORMALLY, NImag=1 (−1267 cm⁻¹) |
| `driver.py verify` | **parses g16 logs** (NImag, mode, overlap) | not parsed — read NImag from the ORCA `.out` (`grep '\*\*\*imaginary mode'`) |

Charge/mult/route/solvent are CLI flags (defaults: charge −1, mult 1, B3LYP-D3BJ/
def2SVP+SDD(Re)/PCM(1-Pentanol) — the DODH project level). Override per project.

## Gotchas (battle scars)

- **g-xTB MUST be the analytic-gradient build (v2.0.0+).** The local build was
  compiled 2026-04-21 — one day *before* v2.0.0 (the first analytic-gradient
  release). GSM evaluates gradients thousands of times and the exact-TS step is
  gradient-driven; v1.x numerical gradients (≈6N+1 SCFs each) are ~100× slower and
  noisy, and the exact-TS optimiser converges to garbage. `setup-gxtb` asserts this.
- **v2.0.1 still reports "xtb version 6.7.1".** `--version` cannot confirm the
  update — the tell is the commit hash/date (`26dd68d`, 2026-05-14) and, decisively,
  the *behaviour*: analytic component breakdown + sub-second `--gxtb --grad`.
- **The v2.0.1 tarball extracts to `xtb-6.7.1/`** — the same dir name as the old
  install, so it upgrades **in place**. Convenient (all existing `ograd`/CLAUDE.md
  references keep resolving), but there is then no separate old-build fallback.
- **NImag=1 is necessary, not sufficient.** A tiny imaginary (|ν| < ~100 cm⁻¹) is a
  floppy/spurious mode, not a reaction coordinate — `verify` WARNs on it. Always
  look at *what moves* in the imaginary mode, not just the count.
- **GSM races on `scratch/gradient`** if `ograd` shares a work dir across parallel
  node calls. The driver-generated `ograd` uses a per-id `wrk_<id>/` dir; `verify`/
  GSM-report flags `mv: cannot stat 'scratch/gradient'` if a stale ograd reintroduces it.
- **`gRMS: 0.0000` in gsm.out is a logging artefact** of this GSM build, not a real
  zero gradient. The driver instead checks that per-node string energies *vary
  between iterations* to confirm gradients are flowing.
- **Strictly-linear endpoints (HCN, CO₂) crash GSM** with `bad spacings`/NaNs. The
  driver auto-perturbs atom 2 by 0.05 Å; for hand builds, bend each endpoint slightly.
- **`subgau16` path-doubling:** passing an absolute `--input` path doubles the cwd
  prefix and Gaussian fails instantly. The driver `cd`s into the input dir and
  passes the basename via a login shell (`subgau16` is only on PATH in login shells).
- **Submit from a SHARED filesystem, never `/tmp`.** `/tmp` is node-local: a job
  submitted from `/tmp/...` is *accepted* (you get a job ID) but dies on the compute
  node with `No such file or directory` for the `.inp`. Run from under `$HOME` or the
  project dir. (Discovered in-container — see Workarounds.)

## Troubleshooting

| Symptom | Fix |
|---|---|
| `setup-gxtb` says could not confirm analytic gradients | Re-run Setup; ensure `GXTB` points at the v2.0.1 binary. |
| `error while loading shared libraries: libmkl_rt.so.2` | Driver sets `LD_LIBRARY_PATH`; if calling gsm.orca by hand, export `/opt/intel/oneapi/intelpython/python3.12/lib`. |
| GSM: `Could not find tag for Default Info` | `inpfileq` missing the `QCHEM Scratch Info` block; use the template the driver copies. |
| `verify`: "could not parse the normal-mode block" | The log lacks a standard Freq block — add `Freq` (or `Freq=HPModes`) and rerun. |
| No `tsq0000.xyz` after GSM | Read `gsm.out` for SCF failure / bad Hessian / race; rebuild endpoints cleaner or `escalate`. |

## Run (human path)

There is none worth using headless — GSM and the QM jobs are batch/queue tools.
The driver *is* the interface; do not try to "open" anything.

## Workarounds (self-recorded)

_Appended by `driver.py record`. Newest first._
### 2026-06-02 — Needed to confirm the ORCA backend actually executes for this user (suborc6 only proves the submit script parses)
**Fix:** Verified: ORCA 6.0.1 via suborc6 from a $HOME dir ran OptTS+NumFreq to 'ORCA TERMINATED NORMALLY' with NImag=1; driver verify is Gaussian-only, so read ORCA NImag from the .out

### 2026-06-02 — subgau16/suborc6 jobs were accepted (job IDs returned) but died on the compute node with 'No such file or directory' for the .inp
**Fix:** Submit from a SHARED filesystem (under $HOME or the project dir), not /tmp — /tmp is node-local and invisible to compute nodes

### 2026-06-02 — GSM exact-TS stalled: product fragment >300 kcal/mol uphill on g-xTB, climb walked past the saddle
**Fix:** Pre-relaxed the product fragment with 'xtb prod.xyz --gxtb --opt' before concatenating endpoints; barrier sane, tsq converged

