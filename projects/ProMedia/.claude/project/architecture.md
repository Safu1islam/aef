# Architecture — ProMedia

> **VERIFIED** by reading the files named. Uncertainty is labelled inline.
> Commit `b120111`, 2026-08-13.

## The one-sentence shape

Two thin adapters project a single operation registry; the registry — not the
adapters — decides authority and locking; everything below it is plain Python
over SQLite and a content-addressed file store.

```
        ┌──────────────────┐        ┌──────────────────┐
        │  promedia/cli.py │        │ promedia/web/    │
        │  argparse        │        │ FastAPI + Jinja2 │
        │  (repo surface)  │        │ (operator UI)    │
        └────────┬─────────┘        └────────┬─────────┘
                 │  supplies a Principal only │
                 └──────────────┬─────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │ promedia/core/registry.py    │  ← THE CHOKEPOINT
                 │  · load_operations()          │
                 │  · invoke(): authority (F-2)  │
                 │            + entity lock C-19 │
                 │            + audit            │
                 └──────────────┬───────────────┘
                                ▼
     ┌───────────┬───────────┬──┴────────┬───────────┬────────────┐
     ▼           ▼           ▼           ▼           ▼            ▼
  ingest.py  storage.py  rights_*.py  provenance  posts.py   credentials.py
     │           │           │           │           │            │
     └───────────┴───────────┴─────┬─────┴───────────┘            │
                                   ▼                              ▼
                        promedia/core/db.py               store OUTSIDE
                        SQLite, WAL, FK on                the repo tree
                        14 tables
```

## Layer rules (VERIFIED by reading imports)

| Layer | Path | May know about | Must not know about |
|---|---|---|---|
| Adapter | `cli.py`, `web/app.py` | registry, config, principal | domain logic, SQL |
| Operation definition | `core/ops/*.py` | core modules, registry | HTTP, argparse |
| Domain logic | `core/*.py` | `db`, `config`, `errors` | HTTP, argparse, templates |
| Persistence | `core/db.py`, `core/schema.sql` | sqlite3 | everything above it |
| Configuration | `config.py` | tomllib, pathlib only | everything (import-cost budget, C-4) |

`core/ops/*.py` files are deliberately thin — 25 to 148 lines. They register and
delegate. Logic in an `ops/` file is a smell.

## The five decisions that produced this shape

Read the full records in `.ai/state/decisions/`.

| Record | Decision | Why it constrains you |
|---|---|---|
| **DR-001** | Python, stdlib-heavy, no daemon | Cold start is on the C-4 budget. Adding a top-level import costs milliseconds you may not have — see T-021. |
| **DR-002** | One operation registry, authority attached to the operation | You do not add a route or a CLI command. You add a registry entry and both surfaces gain it. |
| **DR-003** | SQLite, WAL, application-level entity locks | No ORM. Write SQL. Locks are rows, not OS constructs. |
| **DR-004** | Server-rendered UI, no JS required | No client framework. No build step. |
| **DR-007** | Versioned, jurisdiction-parameterised rulesets | A rights rule change is a **new ruleset version**, never an edit to a shipped one. |

## The three invariants that break the build if violated

### 1. F-1 / S4 — dual-surface parity

Every registry operation is reachable and behaves identically on both surfaces.
Enforced by `tests/test_parity.py`, which invokes each of the 29 operations over
HTTP *and* through the CLI parser and compares outcome class plus surface-native
signal (HTTP status vs exit code). 62 cases.

This gate was once a tautology and was rebuilt (T-028). Do not weaken it.

### 2. F-2 — the authority ceiling

`authority=operator` operations (`approve-post`, `publish-post`,
`connect-account`, `attest-declaration`, `release-publish-claim`) are refused for
an agent principal **inside `invoke()`**. A template that hides a button is not a
control; the server refusal is.

### 3. C-19 — one writer per entity

`invoke()` derives a lock target from `mutates` + `entity` + a declared
`<entity>_id` parameter, acquires with `owner=ctx.agent_id`, and releases in a
`finally`. Seven operations lock. Zero read-only operations lock. Pinned by
`tests/test_locking.py::test_the_locking_and_creating_sets_are_what_this_task_intended`.

**Known hole, on the plan:** `connect-account` is create-*or*-update and its
natural key is not an entity id, so a reconnect is an unlocked write (T-033).

## Data flow: the rights gate

This is the part of the system whose correctness has legal consequence (C-29).

```
ingest ──► asset row (content_hash = SHA-256, state)
              │
              ├── attest-declaration   [operator authority only]
              │      └─► rights_declarations   (WHO claims what)
              │
              ├── add-evidence
              │      └─► evidence               (licences, LLM analyses, URLs)
              │
              └── determine-rights
                     └─► rights_engine.evaluate(declaration, evidence, ruleset)
                            │  deterministic. same inputs → same verdict (C-20)
                            ▼
                        rights_verdicts   PERMITTED | BLOCKED | ESCALATE
                            │  default arm is ESCALATE, never PERMITTED
                            ▼
                        seal-provenance
                            └─► provenance_records
                                   self-contained, keyed on content_hash,
                                   survives deletion of the media (F-8)
```

Two rules that are easy to break and expensive to break:

- **Ancestry is evaluated at gate time, not inherited once.** `core/rights.py`
  `ancestry()` + `effective_verdict()`. A derivative of a BLOCKED asset is
  BLOCKED even if an intermediate was never graded, and even if the source
  degraded *after* the derivative was graded (finding B3, four regression tests).
- **An agent cannot author its own permission.** Authorship is derived from
  `ctx.principal`, never from a caller-supplied string (finding B1). Permitting
  rules require an **operator-attested** declaration.

## Data flow: publish

```
queue-post   [agent may]   ─► posts (status=queued)
approve-post [OPERATOR]    ─► approvals   … server re-checks effective_verdict
publish-post [OPERATOR]    ─► _claim_for_publish()  TRANSACTIONAL claim
                                    │  claim happens BEFORE the external call
                                    ▼
                              publisher.publish()
                                    ▼
                              publications (platform_post_id, permalink, simulated)
```

`_claim_for_publish()` exists because `UNIQUE(post_id)` deduplicates the
**record**, not the **post**: two concurrent calls both reached the platform and
one left no record. A double-click was sufficient (finding B2). Never reorder
this.

## Storage as a budget, not a resource

`core/storage.py` implements a **reservation ledger** (DR-006). The ceiling is
enforced against `committed + reserved + projected` *before any byte is written*,
where projected includes derivatives at the configured multiplier.

- `commit()` raises `LedgerDrift` if rowcount is 0 — a reservation reclaimed
  mid-ingest would otherwise let bytes land counting **zero** against the ceiling,
  undetectably (finding B4).
- Refused ingest is **queued**, not discarded, and becomes admissible as
  retention frees space.

## Configuration

`promedia/config.py` holds `DEFAULTS`; `promedia.toml` overrides at runtime.
Protocol 05 forbids a threshold, endpoint, path, limit, schedule or switch
literal anywhere else. `tests/test_config.py::test_no_hardcoded_thresholds`
enforces it.

**Known exceptions on the plan (T-030):** the ffprobe timeout and SQLite
`busy_timeout` are still literals.

## What this repository does NOT have

Stated so no agent designs against an imagined system:

no ORM · no migration framework · no message queue · no cache layer · no
background worker · no container · no CI · no cloud · no metrics · no
feature-flag service · no client-side framework · no build step · no git remote.
