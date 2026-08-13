# Protocol 04 — Planning

A plan that lives in a chat message is not a plan. It is a suggestion the next
agent will reinterpret. Plans are written to `.ai/state/` as files.

Two artefacts, one per question:

| File | Answers | Schema |
|---|---|---|
| `.ai/state/plan.yaml` | What is the shape of this project, and who owns each part? | `schemas/plan.schema.yaml` |
| `.ai/state/tasks.yaml` | What is the state of each unit of work, and what proves it? | `schemas/task.schema.yaml` |

**Plan before execute.** For a new project, the whole plan is written before the
first line of code. Not a phase of it. Not the interesting part of it. All of it.

---

## 1. Plan the whole project, A to Z

This is the rule most often broken, and it is broken by good intentions:
an agent sees a large project, plans the first slice properly, and leaves the
rest as "to be planned later". The result is a project that discovers its own
shape by colliding with it — the twelfth task reveals a constraint that
invalidates the third, and nobody knew because nobody looked that far ahead.

**The plan is not shortened because the work is long.** If the project contains
100 meaningful tasks, the plan contains 100 tasks. Length is not a defect. An
agent that finds itself writing "and then the remaining features" has stopped
planning and started hoping.

### Coverage checklist

The plan is not complete until every line here is either represented by nodes or
listed in `meta.completeness.known_omissions` with a reason:

- [ ] **Built** — every feature, screen, endpoint, job and model
- [ ] **Configured** — environments, secrets handling, feature flags, limits
- [ ] **Integrated** — every external service, and what happens when it is down
- [ ] **Migrated** — schema changes, backfills, and their reversals
- [ ] **Ordered** — dependencies between tasks, and what may run in parallel
- [ ] **Specified** — the technical steps each task actually requires
- [ ] **Tested** — unit, integration, and the end-to-end paths that matter
- [ ] **Validated** — how anyone knows the thing works, not just runs
- [ ] **Operated** — deployment, rollback, backup, monitoring, on-call reality
- [ ] **Secured** — authentication, authorisation, secrets, audit
- [ ] **Documented** — what a maintainer needs that the code does not say
- [ ] **Finished** — what "done" means for the project, not just for a task

Then state it explicitly:

```yaml
meta:
  completeness:
    declared_complete: true
    basis: >
      Why you believe this covers the project.
    known_omissions:
      - >
        In scope, deliberately unplanned, and why.
```

An honest `known_omissions` entry is worth more than a confident empty list.
A gap you named is a gap the next agent can close; a gap you did not notice is
one they will discover at the worst moment.

### What "meaningful" means

A node earns its place if it can be **completed** and **verified** independently.
"Set up the project" is not a task. "Configuration loads from file and falls back
to documented defaults, proven by a test that fails when the fallback is removed"
is a task.

Sizing rule, unchanged: a task that cannot be finished within one session's
context budget is too large. Split it. Resumability beats ambition.

---

## 2. Build the tree

Decompose top-down, into the shape the project actually has:

```
Project
├── Authentication
│   ├── Login
│   │   ├── UI
│   │   ├── API
│   │   └── Validation
│   └── Registration
│       └── ...
└── Testing
    ├── Unit tests
    └── Final validation
```

- Depth is whatever the project needs. Four or five levels is common; uniform
  depth is not a goal. Do not invent a `feature` layer containing one task.
- **Group by what the work is about, not by who does it.** Agents are assigned
  in step 4 and reassigned freely; a tree organised by agent has to be rebuilt
  the first time that changes.
- Every leaf that is real work links a task in `tasks.yaml` via `task:`.
- A grouping node gets no status. Its status is derived. See the schema.

Run the gate as soon as the tree exists:

```
python aef/tools/aef.py validate
```

It fails if a task is missing from the tree, claimed by two nodes, or links to
nothing — all three of which silently corrupt the completion percentage.

---

## 3. Write the tasks

Unchanged from earlier versions of this protocol, and still the substance:

1. Classify each task against `config/routing.yaml`. Apply escalators. The
   resulting mode and mandatory dimensions are not negotiable by you.
2. Establish interface contracts **before** dependent tasks are created. Two
   agents building against an unwritten contract produce two incompatible halves.
3. Assign owned paths per task. Overlapping ownership means the decomposition is
   wrong — fix the decomposition, not the paths.
4. Write acceptance criteria as observable behaviour, before implementation.
5. Attach verification commands per task.

### Acceptance criteria

Good: "Submitting the form with an email already in use shows an inline message
on the email field, preserves all other entered values, and creates no record."

Bad: "Registration works." / "The page loads." / "It runs without errors."

If a criterion cannot be observed from outside the code, rewrite it.

---

## 4. Assign agents

Every leaf gets an agent, or is visibly unassigned. See
`protocols/09-agent-assignment.md`. In short:

```
python aef/tools/aef.py assign --auto --dry-run    # see the reasoning
python aef/tools/aef.py assign --auto              # apply it
```

Read the reasons. An assignment whose basis is `WEAK: matched the word ...` means
the task has no `change_class` — fix the classification rather than the
assignment.

---

## 5. Hand over

Planning is done when all of these hold:

- [ ] `aef/tools/aef.py validate` exits 0
- [ ] `meta.completeness` is filled in, including omissions
- [ ] Every task has acceptance criteria written before implementation
- [ ] Every leaf has an agent, or is deliberately and visibly unassigned
- [ ] Interface contracts for shared surfaces are recorded as decisions
- [ ] `aef/tools/aef.py progress` shows a sensible starting state

Then the plan is the instruction. Protocol 05 executes against it and updates
`tasks.yaml`; the tree, the percentages and the dashboard follow automatically
because they are derived from exactly that.

---

## Replanning

Plans are wrong in predictable ways, and revising one is normal work, not
failure. Add nodes as the project reveals itself. Bump `meta.plan_version` when
the structure changes materially, and say what changed.

What you may not do is **delete a node to improve the percentage**. The number
exists to describe reality. Work that turned out to be unnecessary is marked
`abandoned` in `tasks.yaml`, with a reason — it stays visible, and the dashboard
counts it under Failed, which is the honest place for it.
