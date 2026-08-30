# chem-skills

Computational-chemistry [Claude Code](https://claude.com/claude-code) skills.
Each skill is a self-contained directory under [`skills/`](skills/) with a
`SKILL.md` (agent-facing instructions) and whatever driver/harness it needs.

Skills that need to drive a **desktop application** also ship an MCP connector
under [`mcp/`](mcp/) — see [Connectors](#connectors).

## Skills

| Skill | What it does |
|---|---|
| [`run-ts-finder`](skills/run-ts-finder/) | Locate **and rigorously verify** a transition state from two endpoints: g-xTB + GSM (growing string) → Gaussian **or** ORCA refinement at the project level of theory → verification cascade (NImag=1, imaginary mode ↔ reaction-vector overlap, IRC, endpoint opts). Self-improving: records workarounds it discovers. |
| [`run-mkm`](skills/run-mkm/) | **Differentiable micro-kinetic analysis** of a computed reaction network. DFT free energies → rate constants → steady-state TOF, **degree of rate control** (Campbell X\_RC), reaction orders, apparent Ea, **energetic span** (Kozuch–Shaik) cross-check, **kinetic isotope effect** (intrinsic → network-apparent), and DFT-uncertainty propagation. JAX + SciPy: steady state by BDF+Newton, exact autodiff sensitivities via the implicit function theorem. Pure Python — no cluster needed. |
| [`chemdraw-figures`](skills/chemdraw-figures/) | **Publication-quality chemistry figures** in ACS Document 1996 house style. Structures and whole schemes — catalytic cycles, mechanisms — are authored as **CDXML** (ChemDraw's own format) and rendered by driving ChemDraw itself, so the output is genuine ChemDraw artwork. SMILES → RDKit 2D coords → CDXML; export to vector PDF/EPS sized to a journal column. Needs the [`chemdraw`](mcp/chemdraw/) connector. macOS only. |

## Connectors

Most skills here drive a *queue* — write an input, submit, parse the output. A
skill that has to drive a **GUI application** needs something else: a live
channel to the running app. That is what [`mcp/`](mcp/) holds — MCP servers,
each registered with the Claude client rather than symlinked.

| Connector | What it does |
|---|---|
| [`chemdraw`](mcp/chemdraw/) | Drives **ChemDraw** on macOS via its AppleScript dictionary: draw from SMILES, read formula/MW/exact mass/elemental analysis, Clean Up Structure, and export to PDF, EPS, TIFF, PNG, Molfile, CML. 10 tools. Used by `chemdraw-figures` to render. |

**Why a connector and not just a driver.** ChemDraw has no batch mode and no
command-line interface. The only programmatic route in is its AppleScript
dictionary, which requires the app to be running and frontmost — a persistent
session, not a one-shot subprocess. An MCP server models that correctly: it
holds the conversation with the app across many calls, and it is the same server
whether Claude Code or Claude Desktop is asking.

**Registration differs by client**, which is the one genuinely fiddly part:

- **Claude Code** takes a stdio server directly —
  `claude mcp add --scope user chemdraw -- …`
- **Claude Desktop** installs local servers as **extensions**. Run
  `mcp/chemdraw/build.sh` to produce a `.mcpb` bundle and install it through
  Settings → Extensions. On the version tested, `claude_desktop_config.json`
  edits did not bring the server up, and `extensions-installations.json` is
  integrity-hashed, so hand-editing it is discarded on next launch — check your
  own version rather than assuming.
- **Claude Science** cannot host this connector at all: it declares no
  `com.apple.security.automation.apple-events` entitlement and sandboxes MCP
  servers, so it cannot send Apple events to a desktop app.

Full detail in [`mcp/chemdraw/README.md`](mcp/chemdraw/README.md).

## Install

Symlink every skill into `~/.claude/skills/` so Claude Code auto-discovers them:

```bash
git clone https://github.com/joshazerty/chem-skills.git
cd chem-skills
./install.sh
```

`install.sh` creates one symlink per skill (`~/.claude/skills/<name>` → this repo),
so the repo stays the single source of truth — edit here, the live skill updates.
Re-run it after pulling new skills. It also reports whether each connector in
`mcp/` is registered, but does not register one for you — that is a per-client
step, described above.

## Per-site configuration

The skills are machine-agnostic. `run-mkm` needs no configuration at all — it is
pure Python (`pip install numpy scipy jax jaxlib matplotlib`, then
`driver.py selftest`). The QM-driver skills read all site specifics (binary
locations, queue-submit commands, level of theory) from a `config.json` next to
the driver. To set up a new machine:

```bash
cd skills/run-ts-finder
cp config.example.json config.json    # edit paths/routes/submit templates for your site
python3 driver.py doctor              # PASS/FAIL per prerequisite
python3 driver.py selftest            # parser tests — needs no chemistry software
```

`config.json` is gitignored, so site setups never leak into the repo.
Environment variables (`$GXTB`, `$GSM_INFRA`, `$RUN_TS_FINDER_CONFIG`) and CLI
flags override the config; see each skill's `SKILL.md` Site setup section.

`chemdraw-figures` needs no `config.json` either, but it does need ChemDraw, a
registered connector, and — the part that catches people — **macOS Automation
permission** for whichever app hosts Claude. Its `doctor` checks all of that:

```bash
cd skills/chemdraw-figures
python3 driver.py doctor      # ChemDraw, uv, RDKit, Apple events, connector
python3 driver.py selftest    # 20 CDXML tests — needs no ChemDraw at all
```

A missing Automation grant surfaces as AppleEvent error `-1743`, which looks
like a broken connector but is a System Settings → Privacy & Security toggle.

## Adding a skill

Drop a new `skills/<name>/` directory with a `SKILL.md` (frontmatter `name:` =
`<name>`), commit, and re-run `./install.sh`. Conventions the existing skills
follow (worth keeping):

- **A driver, not just prose** — `SKILL.md` tells the agent *what* to do;
  a `driver.py` does it and prints `PASS`/`FAIL`/`WARN` for branching.
- **Config over hardcoding** — site specifics live in a gitignored
  `config.json` documented by a committed `config.example.json`.
- **`doctor` + `selftest`** — a setup check for prerequisites and offline
  parser tests, so the skill is testable before any real calculation runs.
  `.github/workflows/selftest.yml` runs every skill's `selftest` on each push.
- **Self-improvement** — a `record` subcommand that appends discovered
  workarounds to `SKILL.md` and `LEARNINGS.md`.

## Adding a connector

Drop a new `mcp/<name>/` directory containing the MCP server, a `README.md` with
the per-client registration steps, and — for anything that must reach Claude
Desktop — a `manifest.json` plus a `build.sh` that packages a `.mcpb`. Built
bundles are gitignored; the repo stays source-only.

Two conventions worth keeping, both learned the hard way:

- **One canonical copy of shared code.** `acs_style.py` lives with the skill and
  is copied into the bundle by `build.sh`, so the connector and the skill can
  never drift apart on house style.
- **Absolute paths in `mcp_config`, resolved at build time.** Clients spawn MCP
  servers without your shell's `PATH`, so a bare `uv` will not resolve — but the
  right absolute path differs per machine (Homebrew on Apple silicon vs Intel,
  or uv's own `~/.local/bin`). `build.sh` resolves it and rewrites the manifest;
  the committed manifest keeps a bare `uv` so no one machine's layout is baked
  into the repo. `doctor` cross-checks an installed manifest against this
  machine. Test the server the way the client launches it — with a minimal
  environment — not from your own shell.
- **Escape everything that reaches the host application.** An AppleScript
  string literal ends at the first unescaped quote, and the rest of the value is
  executed — `do shell script` included. Tool arguments are model-controlled, so
  every path, name and command goes through one escaping helper.

Undocumented behaviour of the host application belongs in the *skill's*
`LEARNINGS.md`, dated and concrete, next to the code it explains.
