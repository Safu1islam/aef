# Protocol 09 — Agent Assignment

Who implements each unit of work, and how that is decided and recorded.

Read with `config/agents.yaml` (the catalogue and the rules) and
`schemas/plan.schema.yaml` (where assignments are stored).

---

## What an agent is here

| | Definition | Where it lives | How many |
|---|---|---|---|
| **Role** | A contract: what this kind of work owes | `roles/*.md` | Seven. Stable. |
| **Agent** | An assignable unit of capacity, occupying one role | `config/agents.yaml` | As many as the project needs |

Adding an agent does **not** add a role. Three hundred specialist agents produce
three hundred overlapping descriptions and a routing system that cannot choose;
`roles/README.md` explains why the role set stays small. An agent is a *name for
a lane of work*, bound to a role that already defines the standard.

**Assignment answers one question: who implements this.** Who reviews it is
answered by `routing.yaml`, per change class. The two are never the same agent —
Constitution §7, and `framework.yaml` sets `self_review_counts_as_review: false`.

---

## Automatic assignment

`aef/tools/aef.py assign --auto` walks every unassigned leaf and applies the
first rule that matches, strongest evidence first:

| Order | Basis | Confidence | Why it ranks here |
|---|---|---|---|
| 1 | `change_class` | strong | Already drives mode, reviewers and dimensions in `routing.yaml`. Reusing it means one classification governs everything, instead of two that can disagree. |
| 2 | `owner_role` | moderate | The task has been classified by a human or an earlier protocol stage. Respect it. |
| 3 | title keyword | **weak** | A word in a title is not a classification. Reported as `WEAK:` in the reason. |
| 4 | nothing matched | none | Left **unassigned**. |

Rule 4 is deliberate. An invented assignment is worse than a visible gap,
because a gap prompts a question and a wrong assignment looks like a decision.

Always read the reasoning before applying it:

```
python aef/tools/aef.py assign --auto --dry-run
```

A `WEAK:` line is a defect in the *plan*, not in the assignment. The task has no
`change_class`, which means it also has no mode and no mandatory quality
dimensions. Fix the classification; the assignment follows.

---

## Manual assignment

```
python aef/tools/aef.py assign --node N-014 --agent frontend-agent
python aef/tools/aef.py assign --node S-BACKEND --agent backend-agent   # whole subtree
python aef/tools/aef.py assign --node N-014 --clear
python aef/tools/aef.py assign --list                                    # the catalogue
```

Manual assignment sets `agent_locked: true`, and `--auto` never overwrites a
locked node. That flag is the entire difference between a human decision and a
machine default, and it is why the dashboard marks manual assignments distinctly.

Assigning a **group** sets the default for its whole subtree. An explicit
assignment on a child always wins. This is how the common case stays short:
assign `Frontend` once rather than every leaf under it.

The write is surgical — one key, in place. Your comments, ordering and
formatting survive, because a plan is a document people read.

---

## Choosing well

Automatic assignment is a good default, not a judgement. Override it when:

- **The specialism is wrong for the actual work.** `change_class: api_change`
  routes to `backend-agent`, but an endpoint that exists only to feed one screen
  may belong with whoever is building the screen.
- **A task is the seam between two areas.** Assign the side that owns the
  contract, and record the contract as a decision before either starts.
- **Continuity beats specialism.** An agent holding the context of the last three
  tasks in a subsystem is often the right choice over a nominal specialist.
- **The work is dangerous.** `security-agent` is frontier-tier deliberately: a
  plausible-looking wrong answer costs most in that class.

Do not assign to balance workload. The dashboard shows per-agent load, and
`advisory_wip_limit` flags saturation, but a task belongs with whoever should do
it. Load-balancing across agents that do not exist yet is theatre.

---

## What must remain true

- [ ] No agent reviews its own implementation
- [ ] Every leaf is assigned, or visibly unassigned — never silently defaulted
- [ ] Every assignment can be traced to a basis, and weak bases say so
- [ ] `agent_locked` is set on every human decision, so automation cannot undo it
- [ ] Agents referenced in `plan.yaml` exist in the catalogue — `assign` refuses
      unknown names rather than accepting a typo that reads as a real agent

---

## Extending the catalogue

Projects add agents in `.ai/config/overrides.yaml`, never by editing
`aef/config/agents.yaml`:

```yaml
agents:
  ml-agent:
    role: implementer          # must be one of the seven
    tier: frontier
    specialism: [training, evaluation, inference]
    does: Model work, and the evaluation that proves it.

assignment:
  by_change_class:
    ai_feature: ml-agent       # replaces the framework default
```

`agents:`, `assignment:` and `constraints:` are deep-merged over the framework
file, so adding one agent does not require restating the rule table.

Every agent must name one of the seven roles. If a proposed agent does not fit
any of them, that is a signal about the *role set*, and it goes through a
framework change — not a local invention that quietly creates an eighth role.
