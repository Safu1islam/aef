# Protocol 01 — Intake  (greenfield only)

Runs once, before any code exists. Output is `.ai/project.md`, which becomes
authoritative for every agent, forever.

The purpose is to stop an agent inventing a project shape. A project built without
intake is a guess that everyone downstream inherits.

## Rules

- Ask in **batches**, not one question at a time.
- Never ask what you can infer. Infer, state the inference, let the user correct it.
- If the user cannot answer a non-functional question, do not skip it — propose a
  default with its consequence: "Assuming under 100 concurrent users; this permits X
  and rules out Y. Correct me if wrong."
- Vague answers get converted into numbers by you, then confirmed.

## Batch 1 — Purpose

1. What does this do, in one sentence, for whom?
2. Who are the distinct user types?
3. What must be true for this to be considered successful?
4. What is explicitly out of scope?
5. Is this internal, commercial, or open source?

## Batch 2 — Non-functional constraints (the batch that prevents rewrites)

These are the questions whose absence caused the failure this framework was built to
prevent. Every answer must end up as a number or a hard statement.

1. Latency: what is slow enough to be a failure? (user-facing ms; machine-facing µs)
2. Throughput: operations per second, peak and sustained.
3. Data volume: records now, records in two years, single-record size.
4. Concurrency: simultaneous users, simultaneous writers to the same entity.
5. Determinism: must identical input produce identical output? Must ordering hold?
6. Availability: acceptable downtime per month. Planned maintenance allowed?
7. Durability: what may never be lost? What may be lost on crash?
8. Consistency: is stale data acceptable, and for how long?
9. Real-time: is anything on a hard deadline, and what happens if it is missed?
10. Money or safety: can a defect cause financial loss or physical harm?

## Batch 3 — Environment

1. Where does this run — browser, server, mobile, desktop, embedded, exchange colo?
2. Deployment target and any provider constraint.
3. Existing systems it must integrate with.
4. Regulatory or compliance regimes that apply.
5. Data residency requirements.

## Batch 4 — Reality

1. Who maintains this after it is built, and what can they operate?
2. Budget ceiling for infrastructure and API spend.
3. Deadline, and what may be cut to meet it.
4. Existing assets — code, designs, data, accounts.
5. Anything already decided and non-negotiable.

## Output

Write `.ai/project.md` containing: purpose, users, success measures, out of scope,
**quantified constraints table**, environment, compliance, maintainer capability,
budget, fixed decisions, and stated purpose of the work (see NON-NEGOTIABLES,
"What is NOT a reason to refuse").

Then, and only then, run `protocols/02-technology-selection.md`.

`.ai/project.md` is amended only through this protocol. Agents read it; they do not
rewrite it in passing.
