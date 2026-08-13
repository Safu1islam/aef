# Migration

What an existing project has to do when the pin moves. The short answer, for
every version so far, is *less than you would expect* — AEF's state files are
owned by the project, and the framework does not rewrite them.

---

## 0.3.0 → 0.4.0

**Required work: none.** 0.4.0 is additive. Every file it introduces is optional
and absent by default.

| Existing file | Change |
|---|---|
| `.ai/state/plan.yaml` | none |
| `.ai/state/tasks.yaml` | none |
| `.ai/state/locks.yaml` | none |
| `.ai/state/fabrications.yaml` | none |
| `.ai/state/decisions/` | none |
| `.ai/config/overrides.yaml` | none |

A 0.3.0 project on the 0.4.0 tooling loads, validates, renders and reports
exactly as it did. `sessions.yaml` and `recommendations.yaml` do not exist until
a command creates them, and the tools say so rather than inventing empty
structures.

### What changes if you do nothing

- The `/team` view renders, and honestly reports that no session is registered.
- Liveness continues to come from `locks.yaml`, exactly as in 0.3.0.
- Assignment behaves identically: the routing table is still consulted first, and
  capability data only adds detail to the *reason* a choice was made.

### What you gain by opting in

Start announcing sessions and the dashboard stops guessing:

```
aef.py session start --id <id> --agent <catalogue-id> --task <T-id>
aef.py session heartbeat --id <id> --activity "what you are doing"
aef.py session end --id <id> --outcome completed --next "what is next"
```

Nominate a coordinator:

```
aef.py session claim-main-engineer --id <id>
```

Give agents somewhere to put findings they are not authorised to act on:

```
aef.py recommend add --title "..." --what "..." --reason "..."
```

### Two behaviour changes worth knowing

1. **A live session outranks a lock** when both name the same task. The session
   is heartbeat-fresh where the lock is TTL-fresh, so it is the better answer to
   "who is on this right now". If you register no sessions, nothing changes.

2. **Capability gaps are now reported.** If a routing class declares
   `requires_capabilities` and the mapped agent does not declare them, the
   assignment still happens — the routing table remains authoritative — but the
   reason says what is missing. The usual cause is a catalogue entry nobody
   updated, and it is worth seeing.

### Optional tuning

```yaml
# .ai/config/overrides.yaml
execution:
  heartbeat_stale_minutes: 15    # how long a heartbeat stays fresh

agents:
  ml-agent:
    role: implementer            # must be one of the seven
    capabilities: [training, evaluation, inference]
```

### Rollback

Pin back to 0.3.0. The two new files are ignored by the older tooling — they
are not read, not required, and not referenced by any 0.3.0 schema. Nothing has
to be deleted.

---

## 0.2.0 → 0.3.0

**Required work: none.** `locks.yaml` already existed; 0.3.0 began deriving
liveness from it.

Behaviour change: a task with a live lock displays as **In progress** even when
`tasks.yaml` still says `ready`, and the disagreement is reported as a
coordination notice. `aef.py validate` reports notices but **exits 0** for them,
so the protocol 04 hand-over gate is unaffected.

---

## 0.1.x → 0.2.0

**Required work: one planning pass.** `plan.yaml` did not exist in 0.1.x and the
tooling refuses to run without it rather than inventing a tree.

Building one from an existing flat `tasks.yaml` is an *arranging* job:

- Do not renumber tasks.
- Do not rescope or rewrite them.
- Every task appears as exactly one leaf — `aef.py validate` enforces it, because
  a task claimed by two nodes silently corrupts the completion percentage.
- Fill in `meta.completeness`, including `known_omissions`. An honest omission is
  worth more than a confident empty list.

---

## The general rule

Project state is **never migrated automatically by the framework**. Migration is
a task like any other: planned, claimed, verified, reviewed. A framework that
rewrote your state files on upgrade would be a framework that could corrupt them
on upgrade.
