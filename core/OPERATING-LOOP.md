# The Operating Loop

Ten stages. Each has an entry condition, an output artifact, and a checkpoint.
A stage is not "thinking about it" — it produces a file. If no file changed, the
stage did not happen.

---

## 0. Orient  (always, every session, ~2k tokens)

Read, in order, and stop as soon as you know what to do:

1. `.ai/project.md` — what this project is and its hard constraints
2. `.ai/state/tasks.yaml` — what is claimed, blocked, ready
3. `.ai/state/locks.yaml` — what files other agents own right now
4. `.ai/state/fabrications.yaml` — what is currently fake

Do **not** read source files yet. Do **not** re-run discovery if
`.ai/state/discovery.md` exists and its `repo_hash` still matches.

If `.ai/` does not exist: this is an uninstalled project. Run `install/BOOTSTRAP.md`.

**Output:** none. **Checkpoint:** none.

---

## 1. Understand

Establish the real objective, which is often not the literal request.
Distinguish the *goal* from the *proposed solution*. Users describe solutions;
you implement goals.

Load only: the routing entry for this change class, the domain memory index for
affected domains, and the specific files named by those indexes.

Read budget: `context.read_budget_files` in `aef/config/framework.yaml`. If you
exceed it, you are exploring instead of working — narrow the question.

**Output:** objective statement + affected domains, appended to the task record.

---

## 2. Decide

Only if the change introduces or replaces a technology, an architecture pattern,
a data store, an external dependency, or a hosting target.

Run `protocols/02-technology-selection.md`. Produce a decision record.
Skipping this stage because "the answer is obvious" is the single most expensive
mistake this framework exists to prevent.

**Output:** `.ai/state/decisions/DR-<n>.yaml`

---

## 3. Plan

Convert the objective into a task graph, not prose. Prose plans are reinterpreted
by the next agent; task graphs are executed by it.

Every task carries: id, objective, owning role, mode, dependencies, owned paths,
acceptance criteria (observable behaviours), required quality dimensions,
verification commands, status.

Acceptance criteria are written **before** implementation. "It runs" is never one.

**Output:** tasks appended to `.ai/state/tasks.yaml`
**Checkpoint:** yes.

---

## 4. Claim

Before touching a file, write a lock. Two agents must never own the same path.

If a required path is locked by another agent: do not wait, do not edit anyway.
Take a different ready task, or split yours so the contested path is a separate
dependent task.

If tightly coupled work would need three agents on one file, use one agent instead.
Parallelism that creates conflict is slower than sequence.

**Output:** entry in `.ai/state/locks.yaml`

---

## 5. Implement

Write the work. While writing:

- Register every fabrication the moment it is created (Constitution §6).
- Follow the project's recorded conventions over your own preferences.
- Keep modules small enough that a future agent can understand one without the rest.
- Do not silently swallow failures. Errors must be meaningful and recoverable.
- Do not expand scope. Note the improvement as a new task instead.

**Output:** code + registry entries.
**Checkpoint:** yes, after each meaningful unit.

---

## 6. Verify

Execute the verification commands from the task. Actually execute them.

Record each with a status from Constitution §7. `NOT_RUN` is an acceptable, honest
result. A fabricated `PASSED` is a framework violation and the most damaging thing
an agent in this system can do.

If a check does not exist and the quality dimensions require it, creating it is part
of the task, not a follow-up.

**Output:** verification block on the task record.

---

## 7. Review

Independent by definition — the reviewing role is never the implementing role,
even if the same underlying model performs both. Load a fresh reviewer context.

Reviewers are selected by `aef/config/routing.yaml` from the change class.
Human-experience review runs on anything a user can see, and runs as a user, not
as a reader of code.

Findings are classified `BLOCKING` / `IMPORTANT` / `OPTIONAL`.
All `BLOCKING` findings are resolved, or the task is not complete.

**Output:** findings on the task record; new tasks for `IMPORTANT`/`OPTIONAL`.

---

## 8. Record

Persist what the next agent would otherwise have to rediscover:

- durable facts -> domain memory (`.ai/memory/domains/<domain>/`)
- reasoning -> decision record
- surprises, dead ends, gotchas -> domain memory `known-risks`
- new reusable procedure -> generate a project skill (`protocols/08-skill-generation.md`)
- anything still fake -> fabrication registry

Do not store logs, raw output, secrets, or unverified guesses. Memory is an index,
not an archive. If `MEMORY.md` exceeds `memory.index_max_lines`, split it.

Commit with the attribution trailer (Constitution §11).

---

## 9. Report

Run `protocols/07-completion.md`. The completion contract is structured, not prose.
It states what was verified, what was not, what is still fake, what a human must do,
and what remains risky.

Never report "everything works." Report what you observed.

---

## Interruption

If the session ends mid-loop — usage limit, crash, human stop — the next agent
resumes from `.ai/state/checkpoints/`. Nothing is redone that was already recorded.

This is why state lives in files. A framework whose progress dies with a context
window is not a framework.
