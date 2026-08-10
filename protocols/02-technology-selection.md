# Protocol 02 — Technology Selection

## Why this exists

An agent asked to build a trading system will reach for Python, because Python is
what it generates most fluently — not because Python fits the latency budget. The
user discovers the mismatch after the system exists, and pays for a rewrite.

Fluency is not a selection criterion. This protocol makes the criterion explicit.

## Hard rules

1. **Constraints first.** You may not name a technology before the quantified
   constraints from `.ai/project.md` are on the page.
2. **Disqualify before you choose.** List what the constraints rule OUT and why.
   This step is what fluency bias skips.
3. **Minimum three candidates**, each with a real disadvantage stated. A candidate
   with no downside means you have not understood it.
4. **Name the bias.** State explicitly which option you would have chosen by habit,
   and whether the constraints support it.
5. **Operator fit.** The best technology the maintainer cannot run is the wrong one —
   but say so out loud rather than silently downgrading.
6. **Reversibility.** Prefer decisions that are cheap to undo. Where a decision is
   expensive to undo, mark it and raise the evidence bar.
7. **Boring by default, exotic by justification.** Novelty must earn its place.
8. **Build vs buy** is a candidate, always.

## Evaluation matrix

Score each candidate against the constraints that actually bind. Ignore generic
"popularity" and "developer experience" unless they were named as constraints.

| Criterion | Weight source |
|---|---|
| Meets latency / throughput budget | project.md |
| Handles data volume and growth | project.md |
| Concurrency and consistency model fit | project.md |
| Determinism / ordering guarantees | project.md |
| Failure semantics and recovery | project.md |
| Operational burden vs maintainer capability | project.md |
| Total cost at expected volume | project.md |
| Ecosystem maturity for the specific need | judgement |
| Migration cost if wrong | judgement |

## Applies to

Language and runtime; framework; datastore class and product; architecture style;
concurrency model; messaging and queueing; hosting and deployment model; auth
provider; observability stack; AI model tier and provider; build vs buy.

## Output: `.ai/state/decisions/DR-<n>.yaml`

```yaml
id: DR-001
date: <iso>
decision_area: primary_language
binding_constraints:
  - "order-to-wire latency budget 250us p99"
  - "deterministic ordering required"
ruled_out:
  - option: Python
    reason: "GC pauses and interpreter overhead exceed the p99 budget"
  - option: Node.js
    reason: "single-threaded event loop; no deterministic ordering guarantee"
candidates:
  - option: C++
    advantages: [...]
    disadvantages: [...]
  - option: Rust
    advantages: [...]
    disadvantages: [...]
decision: C++
rationale: "..."
fluency_bias_check: "Habit would have chosen Python. Constraints disqualify it."
reversibility: expensive
revisit_if: "latency budget relaxes above 5ms"
status: accepted
```

A decision record is permanent. Superseding it creates a new record that references
the old one; it never edits the old one.
