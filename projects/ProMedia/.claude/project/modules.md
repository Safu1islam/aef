# Modules and product domains — ProMedia

> Domains were derived by **enumerating the operation registry at runtime**
> (29 operations), then mapping each to its implementing module, its database
> tables, its tests, and its plan section. Not from names.
> **VERIFIED** 2026-08-13, commit `b120111`.

## How to reproduce this table

```bash
python -c "from promedia.core.registry import load_operations; ops=load_operations(); print(len(ops)); [print(n, o.authority, o.mutates, o.entity) for n,o in sorted(ops.items())]"
```

---

## D1 — Operation layer & surfaces

**Steward:** `surfaces-domain-agent`

| | |
|---|---|
| **Purpose** | Make every capability exist exactly once and be callable identically from the UI and from the repo (F-1, S4). |
| **Users** | Both — the operator through the browser, agents through the CLI. |
| **Code** | `promedia/core/registry.py` (357), `promedia/core/principal.py` (60), `promedia/cli.py` (216), `promedia/web/app.py` (645), `promedia/web/templates/*.html` (6) |
| **Operations** | `ops`, `status`, `init`, `locks`, `audit` |
| **Tables** | `entity_locks`, `audit_log`, `schema_version` |
| **Tests** | `test_registry.py`, `test_parity.py` (62 cases), `test_cli.py`, `test_web.py`, `test_ops_forms.py`, `test_surface_signals.py`, `test_hardening.py`, `test_locking.py` |
| **Plan section** | `S-FOUNDATION`, `S-SURFACES` |
| **Permissions** | This module *is* the permission system. `invoke()` decides; adapters supply a principal. |

**Known risks**
- The registry is the single chokepoint: almost every change touches it or
  something it dispatches to. **File-ownership conflicts concentrate here.**
- `connect-account` reconnect is an unlocked write (T-033, in flight).
- Real multi-process lock contention is `NOT_RUN`.

**Success measure:** `tests/test_parity.py` stays at ≥62 cases and passes;
adding a registry entry reachable from one surface fails the suite.

---

## D2 — Rights & provenance

**Steward:** `rights-domain-agent` · **the highest-consequence domain in the system**

| | |
|---|---|
| **Purpose** | Decide deterministically whether content may be published, and produce a record of that decision that outlives the media (F-3, F-5, F-8, C-20). |
| **Users** | Operator attests and clears; agents may only add evidence and run the check. |
| **Code** | `core/rights_engine.py` (263), `core/rights.py` (337), `core/provenance.py` (163), `core/rulesets/conservative-1.0.0.yaml`, `core/ops/rights.py` (148), `core/ops/provenance.py` (46) |
| **Operations** | `add-evidence`, `attest-declaration` *(operator)*, `determine-rights`, `rights`, `seal-provenance`, `provenance`, `verify-provenance`, `list-provenance` |
| **Tables** | `rights_declarations`, `evidence`, `rights_verdicts`, `provenance_records` |
| **Tests** | `test_rights.py` (262), `test_provenance.py` (91), `test_review_regressions.py` (548), `test_phantom_asset.py` (334) |
| **Plan section** | `S-RIGHTS` |

**Non-negotiables this domain owns**
- Default arm is `ESCALATE`. An asset matching no permitting rule is never `PERMITTED`.
- Model-authored evidence cannot by itself permit (F-5).
- Transformation never launders a BLOCKED asset (F-4).
- A shipped ruleset is **immutable**. Changing a rule means a new version; past
  verdicts do not change.
- A verdict is a fact about a *rights position*. It stays true after the media is
  deleted — but `approve-post`/`publish-post` then refuse with `MediaUnavailable`,
  which is deliberately neither a rights error nor a not-found error.

**Known risks** — `jurisdiction = neutral` while `project.md` §7 confirms UAE. The
conservative ruleset can only become *less* restrictive on learning the
jurisdiction, so this is safe but stale. Doctrine rules (fair use / fair dealing)
are **absent, not approximated**, and adding one needs legal input.

---

## D3 — Media & storage

**Steward:** `media-domain-agent`

| | |
|---|---|
| **Purpose** | Admit media only when the projected lifecycle footprint fits under a hard 100 GB ceiling, and store it content-addressed (F-7, C-13). |
| **Users** | Agents ingest; the operator sees usage. |
| **Code** | `core/ingest.py` (261), `core/storage.py` (191), `core/ops/assets.py` (94), `core/ops/storage.py` (25) |
| **Operations** | `ingest`, `asset`, `list-assets`, `storage-status`, `ingest-queue`, `reclaim-reservations` |
| **Tables** | `assets`, `storage_ledger`, `ingest_queue` |
| **Tests** | `test_ingest.py`, `test_storage.py`, `test_phantom_asset.py` |
| **Plan section** | `S-MEDIA` |

**Rules**
- Reserve **before** writing; project derivatives at the configured multiplier,
  never just the source size.
- Refused ingest is queued, never discarded.
- Re-ingesting a retention-deleted asset is **REFUSED**, not restored (T-029).
  Deduplication claims the media is already here; for a deleted asset that claim
  is false, and a false `ok=true` is worse than a refusal.
- ffprobe is absent: duration and codec are `null` with
  `probe_status: unavailable`. **Never guess a duration** (A-15).

**Known risk** — A-3 (derivative multiplier 0.5×) is the highest-leverage
assumption in the project and breaks the moment a third platform is added.

---

## D4 — Publishing

**Steward:** `publishing-domain-agent`

| | |
|---|---|
| **Purpose** | Queue, gate on human approval, publish once, and record what happened (F-2, C-22, C-32). |
| **Users** | Agents queue; **only** the operator approves and publishes. |
| **Code** | `core/posts.py` (536), `core/publishers/base.py`, `core/publishers/stub.py`, `core/ops/posts.py` (107) |
| **Operations** | `queue-post`, `post`, `list-posts`, `approve-post` *(operator)*, `publish-post` *(operator)*, `release-publish-claim` *(operator)*, `publications`, `platform-capabilities` |
| **Tables** | `posts`, `approvals`, `publications` |
| **Tests** | `test_posts.py`, `test_publishers.py`, `test_review_regressions.py` |
| **Plan section** | `S-PUBLISHING` |

**Rules**
- The claim is transactional and happens **before** the external call. Never
  reorder (B2).
- Publish is idempotent: the same post twice does not create a second publication.
- A post may not be walked backwards from `published` to `rejected` (N2).
- The `simulated` marker comes from the **publication record**, not from current
  configuration (I6) — turning simulation off must not retroactively make a fake
  publication look real.

**Known risk / active fabrication** — **F-001**: the stub publisher. *Nothing has
ever been published to any platform.* Live adapters are T-019, blocked on
credentials and on verifying pricing against live documentation. `blocks_release:
true`.

---

## D5 — Accounts & credentials

**Steward:** `accounts-domain-agent`

| | |
|---|---|
| **Purpose** | Hold platform credentials so that they never appear in the repository, the database, a log, an audit row, a response, argv, or a URL. |
| **Users** | Operator only — `connect-account` is operator authority. |
| **Code** | `core/credentials.py` (108), `core/ops/accounts.py` (147) |
| **Operations** | `connect-account` *(operator)*, `list-accounts` |
| **Tables** | `accounts` (credential **reference** only) |
| **Tests** | `test_credentials.py`, `test_accounts.py`, `test_hardening.py` |
| **Plan section** | `S-SECURITY` |

**Rules**
- The store writes **outside the repository tree**. Assert it, don't assume it.
- A credential may arrive only via stdin or a file path. argv and query strings
  are refused (T-024).
- Reconnect **preserves** `account_id` (T-023) — minting a new one orphans posts.

**Known risks** — **F-002**: plaintext at rest; DPAPI is T-022, `ready`.
Reconnect is an unlocked write (T-033, in flight).

---

## Non-product modules

| Path | What | Owner |
|---|---|---|
| `aef/` | The framework. **READ-ONLY.** | Nobody. Never edit. |
| `.ai/` | Project constitution, plan, tasks, decisions, locks, fabrications | Whichever agent holds the lock |
| `.claude/` | This agent layer | `chief-orchestrator` |
| `aef/tools/` | Plan validation, progress, dashboard, assignment | Read-only; invoke it, don't edit it |

---

## Domains that were asked about and do not exist

`Lab`, `Candidates`, `Research Overflow`, `Strategy` — **0 word-boundary hits in
`promedia/` and `tests/`**. See [discovery.md §2](discovery.md). No agent, no
memory directory, and no routing entry was created for any of them.
