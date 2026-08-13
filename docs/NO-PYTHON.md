# Using AEF without Python

**The tooling is optional. The standard is not.**

AEF is markdown and YAML. `tools/` is a convenience layer that reads and writes
those files faster than you would by hand — it is not the framework, and nothing
in the framework requires it to run.

| | What it is | Needs Python? |
|---|---|---|
| `core/` | The constitution, the loop, the non-negotiables | **No** |
| `protocols/` | Intake, planning, execution, verification, completion | **No** |
| `roles/` | Seven role contracts | **No** |
| `config/` | Routing, quality dimensions, agent catalogue | **No** |
| `schemas/` | The shape of every state file | **No** |
| `adapters/` | Entry stubs for Claude Code, Codex, Cursor, Gemini | **No** |
| `.ai/state/` | Your project's plan, tasks, locks, sessions | **No** |
| `tools/` | Dashboard, progress, validate, assign, session, brief | Yes — and it is optional |

That is 38 files of standard against 19 files of convenience.

---

## Why this matters

AEF is meant to be dropped into a project written in anything — Rust, Go,
TypeScript, Swift, Elixir, a data pipeline, an infrastructure repo. A framework
that forced a Python install on all of them would be imposing its own stack on
projects that never asked for it, which is precisely what
`protocols/02-technology-selection.md` tells agents not to do.

It would also contradict Constitution §8: technology is chosen against recorded
constraints, never by habit. A governance layer is not exempt from its own rule.

---

## What the tools do, and how to do it by hand

Every state file is plain YAML. Any agent can read and write it, and so can you.

| Command | What it does | By hand |
|---|---|---|
| `aef.py validate` | Checks every task appears as exactly one plan leaf | Read `plan.yaml` beside `tasks.yaml`. The invariant is: one leaf per task, one task per leaf |
| `aef.py progress` | Percentage complete | Count leaves with `status: complete` over total leaves |
| `aef.py tree` | The plan as a tree | `plan.yaml` **is** the tree. Open it |
| `aef.py dashboard` | The same, in a browser | Not reproducible by hand, and not needed — it renders state you can already read |
| `aef.py assign` | Sets `agent:` on a plan node | Edit the `agent:` line. Add `agent_locked: true` if a human chose it |
| `aef.py session start/heartbeat/end` | Records who is live | Append an entry to `sessions.yaml` per `schemas/session.schema.yaml` |
| `aef.py recommend add` | Records a proposal | Append to `recommendations.yaml` per its schema |
| `aef.py brief` | The smallest sufficient context | Read `project.md`, then your task in `tasks.yaml`, then what its `owned_paths` name |

The schemas in `schemas/` are the contract. They are written to be read by a
human or an agent, with the reasoning inline, precisely so that hand-editing is
a first-class path rather than a fallback.

---

## Using the concept alone

Some projects will take the ideas and none of the files. That is a legitimate
use, and the ideas travel on their own:

- **The repository is the memory.** Nothing that matters lives only in a chat.
- **Plan the whole project before executing any of it.** The plan is not
  shortened because the work is long.
- **One fact, one home.** Anything derivable is derived on read, never stored
  twice. Two places to answer "is this done?" is two answers.
- **Never claim what you did not verify.** `NOT_RUN` is an honest result; a
  fabricated `PASSED` is the most damaging thing an agent can produce.
- **Register every fabrication when you create it.** A demo running on invented
  data is undisclosed debt, not progress.
- **The implementer is never the sole reviewer of its own work.**
- **Risk sets ceremony.** A typo does not need a threat model; an auth change
  does.

If you adopt only that list, you have most of the value.

---

## Which Python, if you do use the tools

Whatever the badge in `README.md` says, measured by CI across a version matrix
rather than asserted. There is no `pip install` step, no lockfile, no
`requirements.txt`, and no third-party import — a CI job fails the build if one
ever appears.

PyYAML is used when present and is **not required**. A bundled reader covers the
subset AEF's own files use, and CI proves the two agree on every config and
schema file rather than assuming it.
