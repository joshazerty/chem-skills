# chem-skills

Computational-chemistry [Claude Code](https://claude.com/claude-code) skills.
Each skill is a self-contained directory under [`skills/`](skills/) with a
`SKILL.md` (agent-facing instructions) and whatever driver/harness it needs.

## Skills

| Skill | What it does |
|---|---|
| [`run-ts-finder`](skills/run-ts-finder/) | Locate **and rigorously verify** a transition state from two endpoints: g-xTB + GSM (growing string) → Gaussian **or** ORCA refinement at the project level of theory → verification cascade (NImag=1, imaginary mode ↔ reaction-vector overlap, IRC, endpoint opts). Self-improving: records workarounds it discovers. |

## Install

Symlink every skill into `~/.claude/skills/` so Claude Code auto-discovers them:

```bash
git clone https://github.com/joshazerty/chem-skills.git
cd chem-skills
./install.sh
```

`install.sh` creates one symlink per skill (`~/.claude/skills/<name>` → this repo),
so the repo stays the single source of truth — edit here, the live skill updates.
Re-run it after pulling new skills.

## Per-site configuration

The skills are machine-agnostic: drivers are stdlib-only Python and read all
site specifics (binary locations, queue-submit commands, level of theory) from
a `config.json` next to the driver. To set up a new machine:

```bash
cd skills/run-ts-finder
cp config.example.json config.json    # edit paths/routes/submit templates for your site
python3 driver.py doctor              # PASS/FAIL per prerequisite
python3 driver.py selftest            # parser tests — needs no chemistry software
```

`config.json` is gitignored, so site setups never leak into the repo.
Environment variables (`$GXTB`, `$GSM_INFRA`, `$RUN_TS_FINDER_CONFIG`) and CLI
flags override the config; see each skill's `SKILL.md` Site setup section.

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
- **Self-improvement** — a `record` subcommand that appends discovered
  workarounds to `SKILL.md` and `LEARNINGS.md`.
