# Protocol 10 — The Main Engineer

Who holds the project together when the agents doing the work keep changing.

Read with `roles/orchestrator.md` (the contract), `schemas/session.schema.yaml`
(where the post is recorded) and `protocols/09-agent-assignment.md` (how work is
handed out).

---

## 1. It is a post, not a person, and not a role

The Main Engineer is the **orchestrator role, held by one live session at a
time**. There is no eighth role and no new contract — `roles/README.md` argues
why the role set stays at seven, and 0.4.0 does not reopen it.

What 0.4.0 adds is **continuity**. Before it, the orchestrator was a hat a
session put on, so "the Main Engineer" died with the chat wearing it and the
next session had to reconstruct the coordination picture from scratch.

```
role         orchestrator            unchanged
the post     main_engineer: true     on exactly one live session
where        .ai/state/sessions.yaml
```

The rule that makes it work: **the post carries no memory.** Everything the Main
Engineer knows is in the plan, the tasks, the decisions and the
recommendations. If coordination depends on something a session remembers, that
is a defect to be fixed by writing the thing down, not by keeping the session
alive.

---

## 2. Taking the post

```
python aef/tools/aef.py session start --id <session-id> --agent architect --main-engineer
python aef/tools/aef.py session claim-main-engineer --id <session-id>
```

One holder at a time, enforced:

| Situation | What happens |
|---|---|
| Nobody holds it | The claim succeeds |
| A **live** session holds it | The claim is **refused**, naming the holder |
| The holder's heartbeat is **stale** | The post reads **VACANT** and a new session may claim it |
| The holder ended | The post was released on `session end` |

A stale holder is replaceable on purpose. That is the handover path: a
coordinator's process dies and the project keeps a coordinator, without anyone
deciding to take over in a chat nobody else can read.

The post is never *silently inherited*. A vacancy is reported as a coordination
notice, because a project running with no coordinator should be visible rather
than assumed.

---

## 3. What the Main Engineer owes

Every item below has an artifact. A responsibility with no file is a
responsibility nobody can audit.

| Owes | Artifact |
|---|---|
| A complete plan before execution | `.ai/state/plan.yaml`, protocol 04 |
| Work broken into verifiable units | `.ai/state/tasks.yaml` |
| Every leaf assigned or visibly unassigned | `plan.yaml`, protocol 09 |
| No two implementers in one file | `.ai/state/locks.yaml` |
| Dependencies honoured before work starts | `depends_on`, derived readiness |
| Blockers visible with a reason | `blocked_reason` |
| Findings captured rather than acted on | `.ai/state/recommendations.yaml` |
| Reasoning recorded, alternatives included | `.ai/state/decisions/` |
| Required review actually happened | routing.yaml + the task's review block |
| Nothing claimed without evidence | the task's verification block |

**The Main Engineer does not implement most tasks.** Coordinating and building at
once is how a coordinator loses the thread — and Constitution §7 forbids being
the sole reviewer of your own implementation, which a self-assigning coordinator
walks into immediately.

---

## 4. The coordination round

Run this on taking the post, and whenever returning to it:

```
python aef/tools/aef.py session list      # who is here, what is stale, what is proposed
python aef/tools/aef.py progress          # what is done, in flight, next, blocked
python aef/tools/aef.py validate          # does the plan still match the task graph
```

Then, in order:

1. **Reclaim what is abandoned.** A stale session's locks and claims are
   reported. Release them so the paths are usable.
2. **Unblock.** Every `blocked` task and session has a reason. Anything blocked
   on a human is escalated as a decision, not left sitting.
3. **Triage recommendations.** Nothing stays `pending` indefinitely. Accept it
   into a task, reject it *with a reason*, or defer it *with a trigger*.
4. **Assign only what is ready.** A task whose dependencies are unmet is not
   ready no matter who is free. `waiting_dependency` exists to say so.
5. **Check the ceremony matches the risk.** `routing.yaml` sets the mode; a
   `critical` change with one reviewer is a shortfall, and a shortfall is
   disclosed rather than quietly accepted.
6. **Heartbeat.** A Main Engineer whose own session goes stale has vacated the
   post without saying so.

---

## 5. Assigning concurrent work

Parallelism is only safe where ownership is disjoint. Before assigning a batch:

- [ ] No two tasks in the batch declare overlapping `owned_paths`
- [ ] Every task in the batch has its dependencies met
- [ ] Tightly coupled work is **one** task, not three — parallelism that creates
      conflict is slower than sequence
- [ ] Each task's contract exists *before* its dependents start
      (protocol 04 §3.2)

`framework.yaml execution.max_parallel_agents` caps the fleet. The cap is about
review capacity as much as machine capacity: work produced faster than it can be
independently reviewed is not progress.

---

## 6. Handover

A session that ends records what the next one needs:

```
python aef/tools/aef.py session end --id <id> --outcome paused \
  --changed "..." --remaining "..." --risks "..." --next "..." --reference T-042
```

The handoff is **compact by contract** — references, not transcripts. The task
record already holds the evidence; repeating it here creates a second copy that
will drift from the first.

A joining session gets its bearings with:

```
python aef/tools/aef.py brief --agent <agent-id>
```

which answers *who am I, what is mine, what must I not touch, what is already
proposed on my components* without reading the repository.

---

## 7. What must remain true

- [ ] Exactly one live session holds the post, or the vacancy is visible
- [ ] No coordination fact exists only in a session's context
- [ ] Every open recommendation is triaged, not accumulated
- [ ] No agent is assigned work whose dependencies are unmet
- [ ] The Main Engineer is not the sole reviewer of anything it implemented
- [ ] Stale sessions are reclaimed rather than waited on
