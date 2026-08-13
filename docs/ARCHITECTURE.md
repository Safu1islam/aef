# AEF Architecture

How the framework is put together, what owns which fact, and why the seams fall
where they do. Written at 0.4.0, when AEF stopped being a governance framework
for one agent at a time and became a coordination substrate for several.

Read `core/CONSTITUTION.md` first. This document explains the machinery the
constitution assumes.

---

## 1. The one rule everything else follows

**Every fact has exactly one authoritative home, and everything else is derived
on read.**

This is not a style preference. This project has paid for its violation three
times, each time in a different disguise:

| Incident | The duplicate | What it cost |
|---|---|---|
| `T-028` | A parity test compared a dict against the function that built it | A build gate that could not fail |
| `T-031` | A captured `operations` snapshot alongside the live registry | Removing a capability removed its *guards*, not the capability |
| `0.3.0` | Task status and file locks both answered "who is working on this" | The dashboard reported in-flight work as not started |

So: when a new capability needs a value, the first question is never "where do I
store it" but "can it be derived from something already authoritative". Storage
is the fallback, not the default.

---

## 2. Layers

| Layer | Location | Mutable | Owns |
|---|---|---|---|
| Framework | `aef/` | Never in a project | Process, roles, routing, schemas, tooling |
| Project constitution | `.ai/project.md` | Intake / amendment only | Domain facts, constraints, fixed decisions |
| Project state | `.ai/state/` | Continuously, by any agent | Everything that changes |
| Domain memory | `.ai/memory/` | On verified discovery | Durable knowledge per domain |

Framework wins on process; project wins on domain facts. A conflict is recorded
as a decision rather than resolved silently.

---

## 3. The state model

### 3.1 What each file owns

| File | Authoritative for | Added |
|---|---|---|
| `plan.yaml` | **Structure**: decomposition, weight, agent assignment | 0.2.0 |
| `tasks.yaml` | **Status and evidence**: what is done, what proves it | 0.1.0 |
| `locks.yaml` | **Path ownership**: who may write which files | 0.1.0 |
| `sessions.yaml` | **Liveness**: which agent processes exist right now | **0.4.0** |
| `recommendations.yaml` | **Proposals**: work nobody has authorised yet | **0.4.0** |
| `fabrications.yaml` | **Unreality**: what is mocked, stubbed or simulated | 0.1.0 |
| `decisions/DR-*.yaml` | **Reasoning**: why, including rejected alternatives | 0.1.0 |
| `checkpoints/` | **Resumption**: where an interrupted session stopped | 0.1.0 |

### 3.2 Derived, never stored

- Group status, per-node and project completion percentage
- Whether a task is ready, or waiting on an unmet dependency
- Whether an agent is working, idle, stale or absent
- Per-agent workload and saturation
- Whether the plan and the task graph agree

If you find yourself writing one of these into a file, the design is wrong.

### 3.3 The seam 0.4.0 corrects

0.3.0 derived "is anyone working on this?" from **lock TTL**. That was the right
direction and the wrong instrument. A lock answers *may I write this path*; its
TTL is 90 minutes because that is how long a reasonable unit of work takes. A
crashed agent therefore looks busy for up to 90 minutes.

0.4.0 splits the two:

```
locks.yaml     -> ownership.  "these paths are mine."      TTL: work-sized.
sessions.yaml  -> liveness.   "I am still here."           TTL: heartbeat-sized.
```

An agent is `working` only when it holds a session whose heartbeat is fresh
**and** a task claim. A fresh lock with a dead session is a **stale claim** — a
condition worth showing, because it is exactly the state that blocks the next
agent for no reason.

`tasks.yaml:claimed_by` is retained and remains the *historical* record of who
took a task. It is not the liveness signal, and where the two disagree the
dashboard reports it rather than picking a winner.

---

## 4. Roles, agents, sessions

Three concepts, routinely conflated, deliberately separate here:

| | What it is | Where | Cardinality |
|---|---|---|---|
| **Role** | A contract: what this kind of work owes | `roles/*.md` | **Seven. Stable.** |
| **Agent** | A named lane of capacity with capabilities | `config/agents.yaml` | As many as the project needs |
| **Session** | One running process, with an identity and a heartbeat | `state/sessions.yaml` | Ephemeral |

An agent occupies one role. A session runs as one agent. The role set does not
grow when agents are added — `roles/README.md` argues the case, and 0.4.0 does
not reopen it.

### 4.1 Why "Main Engineer" is not an eighth role

The mandate asks for a formal Main Engineer that owns overall coherence, holds
the master plan, assigns work and survives across sessions.

That is the **orchestrator** role's contract almost exactly. What was genuinely
missing was not a role but **continuity**: the orchestrator existed only as a hat
a session put on, so "the Main Engineer" died with the chat wearing it.

0.4.0 therefore makes Main Engineer a **durable project assignment** rather than
a new contract:

```
role       orchestrator     (unchanged, seven roles still)
assignment main_engineer: true on exactly one session in sessions.yaml
```

Consequences, which are the point:

- A new session can *become* Main Engineer by claiming the assignment.
- If the holding session goes stale, the assignment is visibly vacant, not
  silently inherited.
- At most one is enforced; a second claim is refused and reported.
- Nothing depends on a Main Engineer *remembering* anything, because the plan,
  tasks, recommendations and decisions are all files.

This satisfies the mandate's requirement while preserving the role invariant.
Recorded as a deliberate interpretation, not an oversight.

---

## 5. Capability-based assignment

0.2.0 matched `change_class` to an agent id. That is a *lane* match and it works,
but it cannot answer "which of the available agents can actually do this", which
is what a heterogeneous fleet needs.

0.4.0 adds capabilities on both sides:

```yaml
# config/agents.yaml — what an agent can do
security-agent:
  role: implementer
  capabilities: [security_analysis, threat_modeling, code_review, auth]

# config/routing.yaml — what a change class demands
auth_or_permissions:
  requires_capabilities: [auth, security_analysis, threat_modeling]
```

Matching is a scored set intersection, and the **basis is always reported**:
a strong match, a partial match with the missing capabilities named, or no match
at all. Ranking order is unchanged and deliberate:

1. explicit human assignment (`agent_locked`) — never overridden
2. `change_class` → agent
3. capability match
4. `owner_role`
5. title keyword (reported as `WEAK:`)
6. unassigned — **still the honest answer when nothing matches**

Vendor neutrality is structural: `vendor` and `model` are descriptive fields used
for reporting and for capability declaration. Nothing in the matcher branches on
them, and no vendor is named in the framework's defaults.

---

## 6. Recommendations

An agent that finds a problem outside its task has three bad options and one
good one. The bad ones: fix it silently (scope creep, invisible risk), ignore it
(knowledge lost), or stop and ask (blocks the work). The good one is to **record
it and continue**.

```
recommendation -> PENDING -> accepted -> becomes a task, and optionally a decision
                          -> rejected -> stays, with the reason
                          -> deferred -> stays, with a revisit trigger
                          -> merged   -> points at the surviving one
```

A rejected recommendation is **not deleted**. It is the cheapest possible defence
against the same idea being re-proposed every three sessions, and it is why the
schema requires a reason on rejection.

Relationship to decisions: a recommendation is a *proposal*; a decision record is
*reasoning that has been settled*. Accepting a recommendation that changes
architecture produces a `DR-*`; accepting one that is merely work produces a
task. Both, sometimes. The recommendation keeps the pointer either way.

Conflicts between agents use the same channel: two competing recommendations on
the same component, resolved by a decision that names both. No separate conflict
file — a disagreement is a decision that has not been made yet.

---

## 7. Context economy

The framework's own constraint (`framework.yaml: context`) is that agents should
load the smallest sufficient context. 0.4.0 makes that operational with a
generated brief rather than an instruction to be frugal.

```
aef.py brief --agent <id>      what am I, what is mine, what must I not touch
aef.py brief --task <id>       the task, its contract, its neighbours
```

Layering, cheapest first:

| Level | Content | Cost |
|---|---|---|
| 1 | Project identity, hard rules, my role | tiny, always |
| 2 | My assignment, my paths, my dependencies | small |
| 3 | What else is live, and what I must not touch | small |
| 4 | Task contract: criteria, dimensions, verification commands | medium |
| 5 | Decisions, memory, evidence — **by reference, fetched on demand** | on request |

The brief is *derived*, so it cannot drift from the state it describes. It exists
to replace the pattern where each new session re-reads the repository to
reconstruct what the last one already wrote down.

---

## 8. Progress

Unchanged in principle, extended in reach.

- The unit is the **leaf**, weighted. Grouping nodes are not work; counting them
  would inflate a deep tree over a flat one describing the same work.
- Percentage = complete weight / total weight, computed on read.
- Per-section and per-agent rollups use the same arithmetic on a subtree.

The rollup precedence is *worst-news-first with one exception*: `failed`
outranks everything so a section containing a failure never reads as healthy,
but any progress at all outranks `pending` so a half-finished section never
reads as untouched.

**Nothing on the dashboard is a number a human typed.** That is the whole of §34
of the mandate, and it was already true; 0.4.0 keeps it true for the new views.

---

## 9. Tooling constraints

- **Stdlib only.** AEF is copied into projects whose language and dependency set
  it cannot know. A tool needing `pip install` is a tool that does not run. PyYAML
  is used when present; a bundled reader is used when it is not, and `doctor`
  proves the two agree on the project's own files rather than asserting it.
- **The dashboard is read-only and binds localhost.** A plan names internal work,
  people and blockers. Every mutation is a CLI command, so no link can change
  state.
- **State is re-read per request.** No cache. The files are the truth.

---

## 10. Migration

Every 0.4.0 file is **optional**. A 0.3.0 project with no `sessions.yaml` and no
`recommendations.yaml` loads and renders exactly as it did before, and the tools
say so rather than inventing empty structures. There is no state migration step
and no format change to `plan.yaml`, `tasks.yaml` or `locks.yaml`.

See `docs/MIGRATION.md`.

---

## 11. Known architectural gaps

Named here rather than left for discovery.

- **AEF has no home for its own project state.** The framework requires every
  project to keep `.ai/`, but AEF developed inside a host repository borrows the
  host's. Framework work is currently tracked as pseudo-tasks (`AEF-0.3.0`) in
  the host's `locks.yaml`, which the host's own validator correctly flags as
  belonging to no task file. Recorded as recommendation `R-001`.
- **Liveness is heartbeat-based, so it is only as honest as the agent.** A
  process that dies without ending its session looks alive until its heartbeat
  goes stale. This is a bounded, visible wrong answer rather than an unbounded
  one, which is the improvement over lock-TTL liveness — but it is not detection.
- **Nothing mechanically prevents a fabricated `PASSED`.** The framework raises
  the cost of dishonesty and makes disclosure easy; it does not make lying
  impossible. `CODEOWNERS` says the same of itself.
