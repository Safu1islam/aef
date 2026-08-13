# AEF — Agentic Engineering Framework

**An open, tool-neutral operating standard for AI coding agents.**

Drop it into any repository and every agent — Claude Code, Codex, Cursor, Gemini, Kimi,
whatever ships next — works to the same process, writes to the same state, and reports
to the same standard.

MIT licensed. Version 0.1.0.

---

## The problem

AI can write code. It cannot yet run a project.

Every new chat rediscovers the repository. Every tool invents its own architecture and
its own definition of "done". Mock data gets buried and never removed. An agent asked
for a latency-critical system reaches for whatever language it writes most fluently,
and you find out after the system exists. Tests are reported as passing when nothing
ran. A usage limit hits mid-task and a day of progress dies with the context window.

None of this is fixed by telling an agent to try harder. Each needs a mechanism, and an
artifact a human can check.

## What AEF does

| Failure | Mechanism |
|---|---|
| Every agent invents its own process | Constitution plus routing configuration as data |
| New chat rediscovers everything | Repository-resident state, decisions, domain memory |
| Wrong stack chosen by fluency | Selection gate requiring quantified constraints first |
| "It runs but nothing works" | Observable acceptance criteria before code; independent verification |
| Fake data indistinguishable from real | Fabrication registry, enforced at completion |
| Demo-grade where serious was needed | Mandatory quality dimensions per change class |
| Token burn, lost usage limits | Tiered loading, model tiering, checkpointed resumable state |
| Long sessions degrade | Session caps; continuity in files, not context |
| No accountability | Role, task, and model attribution on every commit |

## How it works

**Three layers.** The framework (`aef/`) is read-only and version-pinned. The project
constitution (`.ai/project.md`) holds domain facts. Project state (`.ai/state/`) is
written continuously by any agent. Projects cannot mutate the standard, so the standard
does not fork on contact with reality.

**Seven roles, not three hundred agents.** Orchestrator, analyst, architect,
implementer, reviewer, verifier, human-experience reviewer, domain steward. Any model
occupies any role by loading the constitution plus one role file. Expertise lives in
45 quality dimensions that load only when a change requires them.

**Configuration, not prose.** `routing.yaml` maps change classes to modes, roles, and
mandatory quality dimensions. Prose gets reinterpreted by every model that reads it.
YAML does not.

**Honest status, structurally.** Six verification statuses. `PASSED` requires that the
agent executed the check and observed success. `NOT_RUN` is acceptable and honest. There
is nowhere to be vague.

## Quickstart

Copy the framework into your project — it travels with the repository, so a
fresh clone already has its governance layer.

```bash
git clone --depth 1 https://github.com/Safu1islam/aef aef && rm -rf aef/.git
cp aef/adapters/CLAUDE.md .        # or AGENTS.md, .cursorrules, GEMINI.md
mkdir -p .ai/state/decisions .ai/memory/domains .ai/config
```

`aef/` is read-only from then on: configure through `.ai/config/overrides.yaml`,
and upgrade by replacing the directory. Full options and the project layout are
in [`install/BOOTSTRAP.md`](install/BOOTSTRAP.md).

Then, in your agent tool:

> Read aef/core/CONSTITUTION.md, then run aef/protocols/01-intake.md.

Full instructions: [`docs/QUICKSTART.md`](docs/QUICKSTART.md).

## Layout

```
core/        constitution, operating loop, non-negotiables
protocols/   intake, technology selection, discovery, planning,
             execution, verification, completion, skill generation
roles/       seven model-agnostic role contracts
config/      framework.yaml, routing.yaml, quality-dimensions.yaml
schemas/     task graph, fabrication registry, locks, domain memory
adapters/    entry stubs per tool
install/     bootstrap and upgrade
docs/        why, concepts, quickstart, AI review request
```

## Documentation

- [Why AEF exists](docs/WHY.md) — the nine failures, in detail
- [Concepts](docs/CONCEPTS.md) — layers, roles, dimensions, modes, registry
- [Quickstart](docs/QUICKSTART.md) — installation and first run
- [AI review request](docs/AI-REVIEW-REQUEST.md) — adversarial review prompt for any model

## Status and honest gaps

v0.4.0. What it has earned since 0.1.0: a plan tree with derived progress, a
dashboard, agent assignment, live multi-agent coordination, and 109 tests that
are proven able to fail by sabotage rather than merely asserted.

What it has **not** earned:

- **Partial enforcement only.** `aef.py validate` checks the plan against the
  task graph, and the coordination checks report drift between state files.
  Nothing mechanically prevents an agent writing `PASSED` for a check it never
  ran. The standard raises the cost of dishonesty; it does not make it impossible.
- **Not battle-tested against a large legacy repository.** It has been run
  against one real project, continuously, by a small number of agents.
- **No genuinely heterogeneous fleet.** The coordination layer is designed to be
  vendor-neutral and nothing in it branches on vendor or model, but four agents
  from four vendors coordinating through these files has not been run.
- **Routing classes cover common web, service, and AI work.** Specialised
  domains — embedded, games, data engineering, scientific computing — need
  contributed classes.

## Contributing

Real-world failure reports are worth more than feature requests. If an agent operating
under AEF reported a false `PASSED`, shipped unregistered fabrication, or claimed
completion with blocking findings open, that is the single most valuable issue you can
file. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Design principles

Configuration over prose. Data over documents. Evidence over assertion. Few roles, many
dimensions. State in files, not context. Honest status always.

## Licence

MIT — see [`LICENSE`](LICENSE).
