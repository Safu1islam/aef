# Changelog

All notable changes to AEF are recorded here.
Versions follow semantic versioning. Projects pin a version; upgrades are deliberate.

## [0.5.0] — 2026-08-13

A runnable example, so the dashboard has something to show on a machine that
has never run AEF.

### Added
- **`docs/example/`** — a fictional project, "Meridian", with a full plan tree,
  23 tasks, five agent sessions across four vendors, and three recommendations.
  `python tools/aef.py --root docs/example dashboard` and it is populated
- **`docs/example/refresh.py`** — rewrites the session heartbeats relative to
  now, so the demo shows a live team rather than one that went stale the day
  after it was written. Stdlib only, like everything else in `tools/`
- **README: "See it before you install it"** — real `progress`, `tree` and
  `team` output, so the framework can be evaluated without installing it

### Why the example is not a happy path
It carries a **failed** task with its review findings, a **blocked** payment
integration escalated rather than stubbed, a **stale** agent session whose
claims are flagged as possibly abandoned, a **blocked agent** with the reason a
human needs, and a **rejected recommendation kept with its reasoning**. All six
statuses appear. A dashboard is only worth trusting once you have seen it
deliver bad news, and a demo that shows 100% green teaches nothing.

Every file is labelled `EXAMPLE DATA` in its header. Constitution §6 makes
undisclosed fabrication the cardinal sin, and shipping demo state that could be
mistaken for a real project would be exactly that.

### Minor
- Version is 0.5.0 rather than 0.4.3: the example is new surface that the
  README, the docs and a script now depend on, not a fix to existing behaviour.

## [0.4.2] — 2026-08-13

CI, and a correction: the tooling is optional and 0.4.0 stopped saying so.

### Added
- **`.github/workflows/tests.yml`** — the suite on every push, across Python
  3.9–3.13 plus Windows and macOS, run **with and without PyYAML**. It exists
  because of a real defect: the 0.4.0 standalone-layout bug survived three
  releases undetected, since the suite had only ever run vendored inside a host
  project. CI clones this repository standalone, which is exactly the broken
  layout
- A **`stdlib only`** job that parses every import and fails the build if a
  third-party dependency ever appears. "No install step" is now enforced rather
  than promised
- A **reader-parity** job proving the bundled YAML reader and PyYAML agree on
  every config and schema file. AEF ships without PyYAML, so disagreement would
  mean project state meant different things to different agents
- **`docs/NO-PYTHON.md`** — what each tool does and how to do it by hand, plus
  the concepts alone for projects that adopt nothing else

### Fixed
- **0.4.0 made the constitution read as if Python were mandatory.** §4b listed
  `aef.py` commands as the obligation; the obligation is the *record*, and
  writing the state files by hand is equally valid. The planning gate is
  likewise stated as the invariant it checks, with the command as one way to
  check it
- The §4a planning gate had been orphaned under §4b when §4b was inserted
- `README.md` still said "Version 0.1.0", omitted `tools/` from the layout, and
  did not link ARCHITECTURE, MIGRATION or NO-PYTHON

### Note
The framework is 38 markdown and YAML files against 19 Python files. The
standard is the former. A governance layer that forced a Python install on a
Rust or TypeScript project would be imposing its own stack — which is precisely
what `protocols/02-technology-selection.md` tells agents not to do, and
Constitution §8 forbids by name.

## [0.4.1] — 2026-08-13

Install instructions, which were wrong for the primary audience.

### Fixed
- `install/BOOTSTRAP.md` and `docs/QUICKSTART.md` led with `git submodule add`
  against a `<owner>` placeholder, and told the reader to check out **v0.1.0**
  and **v0.3.0** respectively. Anyone following either got a framework with no
  plan tree and no tooling, from a URL that does not resolve
- Both now lead with **copying the framework into the project**, which is how it
  is meant to be installed: `aef/` travels with the repository, so a fresh clone
  already carries its governance layer and no session can start without one.
  The submodule route is kept, with its real cost stated — every clone needs
  `git submodule update --init`, and a session that skips it runs with no
  constitution at all
- `README.md` status section still described v0.1.0 and promised a validator
  "planned for 0.2.0" that shipped three releases ago. Rewritten to state what
  0.4.0 actually earned, and what it still has not

### Note
Cut as a patch release rather than by moving the v0.4.0 tag. A published tag
that changes underneath its consumers is the same class of problem as a state
file with two sources of truth.

## [0.4.0] — 2026-08-13

From a governance framework for one agent to a coordination substrate for
several. **Additive: every new file is optional and a 0.3.0 project runs
unchanged.** See `docs/MIGRATION.md`.

### Added

**Liveness — `.ai/state/sessions.yaml`** (`schemas/session.schema.yaml`)
- A session is one running agent process, with a heartbeat. `aef.py session
  start | heartbeat | end | claim-main-engineer | list`
- Separates two things 0.3.0 conflated. A **lock** answers "may I write this
  path" and is work-sized (90 min). A **heartbeat** answers "am I still running"
  and is minutes (`execution.heartbeat_stale_minutes`, default 15). Deriving
  liveness from lock TTL meant a crashed agent looked busy for an hour and a half
- `stale` is **derived, never stored** — a crashed process cannot record its own
  death, so a stored staleness flag is the one value guaranteed to be absent when
  it matters
- A live session now promotes its task to In progress ahead of a lock, and names
  the holder on the dashboard and in `aef.py progress`

**The Main Engineer post** (`protocols/10-main-engineer.md`)
- The orchestrator role held by exactly one live session, recorded in state.
  **Not an eighth role** — the role set stays at seven
- Single-holder, enforced. A live holder cannot be displaced; a **stale** one can,
  which is the handover path: the coordinator's process dies and the project keeps
  a coordinator
- A vacancy is reported, never silently inherited
- The post carries no memory. Everything it knows is in files

**Recommendations — `.ai/state/recommendations.yaml`** (`schemas/recommendation.schema.yaml`)
- `aef.py recommend add | list | accept | reject | defer | merge`
- The channel for work an agent finds but was not assigned. Recording is **not
  permission**: the finding survives, the scope does not widen
- **Rejection requires a reason**, enforced. A rejected recommendation is kept, so
  the next agent learns it was already considered instead of re-proposing it
- **Acceptance requires a task or a decision**, enforced. Acceptance that produces
  neither is agreement, and agreement does no work
- Disagreement between agents uses this same channel. There is deliberately no
  separate conflict register — a disagreement is a decision not yet made

**Capability-based assignment**
- `capabilities:` on every catalogue agent; `requires_capabilities:` on every
  routing class
- The routing table stays authoritative. Where the mapped agent does not declare
  what the class demands, the **gap is reported rather than silently corrected**
- Pure capability matching fills the case a heterogeneous fleet creates: a class
  that declares what it needs but has no agent mapped to it
- Vendor neutrality is structural. `vendor` and `model` are descriptive; nothing
  in the matcher branches on them and no vendor appears in the defaults

**Context economy — `aef.py brief`**
- `--agent` for a joining session, `--task` for one contract. Levels 1–4 printed;
  decisions, memory and evidence listed **by reference** so they cost an id
- Exists to replace the pattern where each new session re-reads the repository to
  reconstruct what the last one wrote down

**Dashboard — `/team`**
- Live sessions, stale sessions, the Main Engineer post, open and resolved
  recommendations, live workload. Every value derived; nothing typed

**`docs/ARCHITECTURE.md`** — what owns which fact, and why the seams fall there.

**`aefkit/writer.py`** — a deterministic YAML emitter for the two
machine-managed files. Round-trip through **both** readers is a correctness
requirement, not a nicety: AEF ships without PyYAML

### Fixed
- **The framework's own test suite failed in the framework's own repository.**
  `tools/` resolved framework config by assuming AEF is always vendored at
  `<project>/aef/`, so a standalone checkout produced 13 "agent catalogue not
  found" errors and one failure. Found by running the suite from a fresh clone
  before publishing, which is the only place it could have been found.
  `aefkit/paths.py` now resolves both layouts, and the suite passes vendored
  **and** standalone

### Changed
- `aef.py doctor` reports the new state files and proves the bundled reader
  agrees with PyYAML on them
- `aef.py progress` names the holding session and its activity under live work
- Constitution §4a extended, §4b added ("you are one of several"). Still under
  the 200-line cap

### Deliberately not done
- **No new role.** The Main Engineer is the orchestrator with continuity
- **No conflict file.** Competing proposals are two recommendations and one
  decision
- **No separate handoff store.** A handoff is what a session leaves behind, and
  splitting it out would create a second place to look for the same answer
- **No dashboard write path.** Still read-only, still localhost. Every mutation
  is a CLI command, so no link can change state
- **No agent auto-registration.** A session names an agent from the catalogue or
  is refused; a typo must not mint a phantom teammate

### Known gaps
- Liveness is only as honest as the agent. A process that dies without ending its
  session looks alive until its heartbeat goes stale — bounded and visible, but
  detection it is not
- AEF still has no home for its own project state; framework work is tracked as
  pseudo-tasks in the host project. Recorded as a recommendation rather than
  fixed, because the fix belongs with extracting AEF to its own repository
- The team view has no auto-refresh. State is re-read per request, so a reload is
  correct, but nothing pushes

## [0.3.0] — 2026-08-13

Make "being worked on now" true.

0.2.0 derived every status from `tasks.yaml`. That answers "is this done?"
correctly and "is anyone on this right now?" incorrectly, because `status:
claimed` is written when an agent remembers to write it, whereas a file lock is
claimed **before the first edit** — Constitution rule 3 makes it mandatory. The
dashboard was reading the later, weaker signal.

Observed in a live project rather than imagined: three tasks held by a running
agent session, all reading `ready` in `tasks.yaml`, the dashboard reporting
"Nothing is claimed right now", and all three listed under *Coming next* while
that session had their files open.

### Added
- `.ai/state/locks.yaml` is now a **third input** to the plan model. A live lock
  promotes a `pending` or `waiting_dependency` leaf to **In progress**, and the
  holder is shown on the tree, the progress view and `aef.py progress`
- `Lock` in `tools/aefkit/model.py`, with TTL evaluation. Only the active
  `locks:` key is read; `history:` is the past and is ignored
- **Coordination notices** — a second, non-fatal problem channel. A lock that
  disagrees with a task status, a lock left on a finished task, two locks on one
  task, a missing or unreadable TTL: each is reported, none is hidden, and none
  fails the plan
- `tools/tests/test_locks.py` — 18 tests. Proven able to fail by three
  sabotages (promotion disabled; guard rails removed; notices made fatal)

### Changed
- `aef.py validate` reports notices but **exits 0** for them. Gating the
  protocol 04 hand-over on a transient lock would mean a plan cannot be
  validated while anyone is working on the project
- `aef.py progress` prints `held by: <agent> until <expiry>` under live work
- `install/BOOTSTRAP.md` §1 pinned the example checkout at `v0.1.0`, two
  releases stale. A reader following it got a framework with no plan tooling at
  all

### Deliberately not done
- A lock never overrides `complete`, `failed` or `blocked`. Those are findings
  about the work and outrank a claim to be editing it; a lock over one of them
  is reported as a leak instead. Burying a `blocked` task under "In progress"
  would take a `NEEDS_HUMAN` item off the operator's screen
- An absent or unparseable TTL counts as **live**, not expired. Failing the
  other way would silently unlock a file somebody is editing
- Still no write path from the dashboard. Status changes go through protocol 05
  and the agent that did the work

## [0.2.0] — 2026-08-12

Plan before execute, and make the plan visible.

### Added
- `core/CONSTITUTION.md` §4a — **plan the whole project before executing any of
  it**. A new project is planned end to end before the first line of code, and
  the plan is not shortened because the work is long
- `schemas/plan.schema.yaml` — `.ai/state/plan.yaml`, the project plan as a tree
  (project → section → feature → task → subtask). Structure, weight and agent
  live here; status stays in `tasks.yaml`; every rollup is derived on read and
  stored nowhere
- `protocols/09-agent-assignment.md` — automatic assignment from classification,
  manual assignment by command, and the rule that automation never overwrites a
  human decision
- `config/agents.yaml` — agent catalogue and assignment rules, as data. Agents
  are assignable capacity bound to the existing seven roles; adding one does not
  add a role
- `tools/` — the framework's first executable layer. Stdlib only, no install:
  - `aef.py dashboard` — project tree and progress views on localhost, read-only
  - `aef.py progress` / `tree` — the same state as text, for agents
  - `aef.py validate` — fails if the plan and the task graph disagree
  - `aef.py assign` — automatic and manual agent assignment
  - `aef.py doctor` — reports what the tooling can see, and verifies the bundled
    YAML reader against PyYAML on your own files
  - `run_tests.py` — 51 tests, `unittest`, no pytest

### Changed
- `protocols/04-planning.md` — rewritten. Adds the A-to-Z requirement, a twelve
  point coverage checklist, a required `completeness` declaration including
  known omissions, tree construction, and the validation gate
- `schemas/task.schema.yaml` — adds `agent`, adds `blocked_reason`, and adds
  `failed` to the status enum. A task that was attempted and did not succeed is
  not the same as one nobody has started
- `install/BOOTSTRAP.md` — plan creation and the dashboard are part of setup

### Notes
- **No project migration is automatic.** `plan.yaml` does not exist until
  protocol 04 writes one, and the tooling says so rather than inventing a tree.
  Migrating an existing flat `tasks.yaml` is a task like any other (BOOTSTRAP §6)
- `abandoned` and `failed` both display under Failed. Removing a node to improve
  the percentage is explicitly forbidden in protocol 04

### Known gaps
- Still no automated validator for `tasks.yaml` itself; `aef.py validate` checks
  the plan/task seam, not every schema field
- The dashboard has no write path by design. Status changes go through protocol
  05 and the agent that did the work

## [0.1.0] — 2026-08-07

Initial public release.

### Added
- `core/CONSTITUTION.md` — always-loaded operating contract, under 200 lines
- `core/OPERATING-LOOP.md` — ten-stage loop, each stage producing an artifact
- `core/NON-NEGOTIABLES.md` — prohibited actions and human-approval gates
- `protocols/` — intake, technology selection, discovery, planning, execution,
  verification, completion, project skill generation
- `roles/` — seven model-agnostic role contracts
- `config/routing.yaml` — change class to mode, roles, and mandatory quality dimensions
- `config/quality-dimensions.yaml` — 45 dimensions, each with required evidence
- `config/framework.yaml` — autonomy, context budget, model tiering, execution defaults
- `schemas/` — task graph, fabrication registry, file locks, domain memory
- `adapters/` — entry stubs for Claude Code, Codex/AGENTS, Cursor, Gemini
- `install/BOOTSTRAP.md` — installation and upgrade procedure

### Known gaps
- No automated validator yet (`verify` script planned for 0.2.0)
- Not yet battle-tested against a large existing repository
- Routing classes cover common web/service/AI work; specialised domains need extension
