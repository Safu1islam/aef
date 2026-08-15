# Repository discovery — ProMedia

> Produced by inspection on **2026-08-13** against commit `b120111`.
> Every line below is either **VERIFIED** (observed in a file or by running a
> command, with the evidence named) or **UNCERTAIN** (labelled inline).
> Nothing here is inferred from the name of a thing.

---

## 0. The two layers in this repository

This repo contains **two distinct systems**, and confusing them is the single
easiest mistake to make here.

| Layer | Path | What it is | Mutable? |
|---|---|---|---|
| **The application** | `promedia/`, `tests/`, `promedia.toml`, `pyproject.toml` | ProMedia — the product being built | Yes |
| **The framework** | `aef/` | Agentic Engineering Framework 0.2.0, vendored and version-pinned | **NO. Read-only. Never edit.** |
| **Project state** | `.ai/` | This project's constitution, plan, tasks, decisions, locks, fabrications | Yes, per schema |
| **This agent layer** | `.claude/` | Claude Code projection of the framework (new) | Yes |

`aef/` read-only is stated in `CLAUDE.md`, `AGENTS.md`, `CODEOWNERS`, and
`aef/core/CONSTITUTION.md` §2. **VERIFIED** — all four files read.

---

## 1. Application purpose

**VERIFIED** (`.ai/project.md` §1, `pyproject.toml` description):

> ProMedia is a single-operator system for producing, rights-clearing, scheduling
> and publishing social media content, in which every capability is callable
> identically by a human through a UI and by an AI agent through the repository,
> and in which no content reaches a platform without a passing rights
> determination and explicit human approval.

Two actor types only, permanently (`project.md` §2, F-9): **Operator** (one human)
and **AI agents** (≤4 concurrent). No multi-tenancy, ever.

The nine fixed decisions (`project.md` §10) are the shape of the whole system.
The three that constrain implementation hardest:

- **F-1 dual entry points** — every capability is *one* operation with *one*
  implementation, exposed through both a UI surface and a repo-callable surface.
  A capability on one surface is a **build failure** (success measure S4).
- **F-2 agent authority ceiling** — agents may never publish, spend money, or
  clear a rights flag.
- **F-5 / C-20** — an LLM opinion is **evidence, never a verdict**. Rights
  verdicts are deterministic functions over recorded evidence, stamped with a
  ruleset version.

## 2. Product areas and domain modules

**VERIFIED** by enumerating the operation registry at runtime
(`python -c "from promedia.core.registry import load_operations; ..."` → 29
operations) and cross-reading `promedia/core/ops/*.py` and `.ai/state/plan.yaml`.

Full detail in [modules.md](modules.md). Summary:

| Domain | Code | Operations |
|---|---|---|
| Operation layer & surfaces | `core/registry.py`, `core/principal.py`, `cli.py`, `web/app.py` | `ops`, `status`, `init`, `locks`, `audit` |
| Rights & provenance | `core/rights_engine.py`, `core/rights.py`, `core/provenance.py`, `core/rulesets/` | `add-evidence`, `attest-declaration`, `determine-rights`, `rights`, `seal-provenance`, `provenance`, `verify-provenance`, `list-provenance` |
| Media & storage | `core/ingest.py`, `core/storage.py` | `ingest`, `asset`, `list-assets`, `storage-status`, `ingest-queue`, `reclaim-reservations` |
| Publishing | `core/posts.py`, `core/publishers/` | `queue-post`, `post`, `list-posts`, `approve-post`, `publish-post`, `release-publish-claim`, `publications`, `platform-capabilities` |
| Accounts & credentials | `core/credentials.py`, `core/ops/accounts.py` | `connect-account`, `list-accounts` |

### Named domains that were asked about and DO NOT EXIST

The bootstrap brief named four candidate domains. All four were probed by
word-boundary search over `promedia/` and `tests/`:

| Asked about | Result |
|---|---|
| **Lab** | **0 hits** in application code. The apparent matches repo-wide are substrings (`available`, `label`, `Lab` inside longer words) and prose in framework docs. |
| **Candidates** | **0 hits** in application code. Matches are `candidates:` keys in `.ai/state/decisions/DR-*.yaml`, meaning *evaluated alternatives* in technology-selection records — a framework concept, not a product domain. |
| **Research Overflow** | **0 hits anywhere in the repository.** |
| **Strategy** | **0 hits** in application code. |

**No agent was created for any of them.** They are not present, not partially
implemented, and not renamed — they belong to a different application.

## 3. Frontend

**VERIFIED.** No SPA framework, no build step, no `package.json`, no
`node_modules`, no TypeScript, no React, no Tailwind.

- Server-rendered HTML: **Jinja2** templates in `promedia/web/templates/`
  (`base.html`, `index.html`, `ops.html`, `op.html`, `post.html`, `error.html`).
- **No JavaScript is required for any flow.** `promedia/web/app.py:9` states this
  is deliberate: the approval flow authorises irreversible, legally consequential
  actions, so it must work plainly and be keyboard-reachable.
- Styling is a single inline `<style>` block in `base.html` with CSS custom
  properties and a `prefers-color-scheme` dark variant.

**Consequence for agent design:** a "React/Next.js/Tailwind frontend agent" would
be fiction here. The frontend agent is a *Jinja2 + semantic HTML + no-JS
accessibility* agent.

## 4. Backend

**VERIFIED** (`pyproject.toml`, imports in `promedia/web/app.py`):

- Python **≥3.11** (environment: 3.11.9, `aef.py doctor`).
- **FastAPI** + **uvicorn** for the web surface.
- **Jinja2** for templates, **python-multipart** for form posts, **PyYAML** for
  rulesets.
- **argparse** (stdlib) for the CLI surface (`promedia/cli.py`).

Total production dependency count: **5**. `pyproject.toml` states the smallness
is deliberate — `supply_chain_security` counts additions against a solo
maintainer.

### The architectural centre: the operation registry

`promedia/core/registry.py` (357 lines) is the single most important file in the
application. **VERIFIED** by reading it and by the parity test suite:

- Every capability is registered once as an `Operation` (name, parameters,
  authority, `mutates`, `entity`).
- `invoke()` enforces **authority** (F-2) and **entity locking** (C-19) *inside
  the operation layer*, so both adapters get identical behaviour for free.
- Both `cli.py` and `web/app.py` are **generic projections** of the registry.
  Neither hand-writes a route or a command per capability.

## 5. API styles and contracts

**VERIFIED** by running the registry and reading `web/app.py` routes:

| Surface | Shape |
|---|---|
| CLI | `python -m promedia <operation> [--param ...] [--json]` — parser generated from the registry |
| HTTP JSON | `POST /api/op/{name}` (+ `GET /api/ops` listing) |
| HTTP HTML | `GET /`, `GET /ops`, `GET|POST /ops/{name}`, `GET /posts/{id}`, `POST /posts/{id}/{approve,publish,release-claim}` |

29 operations, **all reachable from both surfaces** — enforced, not asserted, by
`tests/test_parity.py` (62 cases: 29 ops × 2 probes + 4 structural).

Error → HTTP status is one table (`ERROR_STATUS` in `web/app.py:70`); the CLI
carries the same signals as exit codes. `tests/test_surface_signals.py` pins the
pair.

## 6. Database

**VERIFIED** (`promedia/core/db.py`, `promedia/core/schema.sql`):

- **SQLite**, file-based, single machine. No ORM — hand-written SQL.
- Pragmas enforced on every connection: `foreign_keys`, WAL journal mode,
  `busy_timeout`.
- `schema_version` tracked; migrations are code in `db.py`, not a migration tool.
- Includes an application-level **entity lock table** implementing C-19 with a
  visible owner.

Decision record: `DR-003`.

## 7. Authentication and authorization

**VERIFIED** (`core/principal.py`, `core/registry.py`, `web/app.py`,
`tests/test_hardening.py`):

There is **no user accounts system** and there never will be (F-9). Authorization
is a two-value **principal** model:

- `operator` — proven by a bearer token, presented once as `?token=` on `/`,
  immediately exchanged for a `promedia_operator` cookie, and thereafter carried
  as the `X-ProMedia-Token` header. `?token=` is refused on `/api/op/*`.
- `agent` — everything else.

Enforcement lives in `registry.invoke()`, never in a template or a route.
Hardening already landed (T-024/T-025/T-026/T-031): secrets may not travel
through argv or query strings; mutating operations are refused over `GET`;
cross-origin POST is refused server-side.

**Platform** credentials (X, LinkedIn) are a separate concern: `core/credentials.py`
stores them **outside the repository tree**, and only a redacted reference ever
appears in a response, log, or audit row.

## 8. Background workers and scheduled jobs

**VERIFIED: none exist yet.** `T-018` (publish tick + Windows Task Scheduler
registration, implementing `DR-009`) is `status: ready`, not started. There is no
daemon, no queue worker, no cron.

## 9. Third-party integrations

**VERIFIED:** none are live. `promedia/core/publishers/` defines the publisher
interface (`DR-010`) and ships **only a stub**, which is a registered fabrication
(**F-001** in `.ai/state/fabrications.yaml`) and is unreachable unless
`publishing.allow_simulation = true` (it is `false` in `promedia.toml`).

X and LinkedIn adapters are `T-019`, `status: blocked`, `NEEDS_HUMAN` — no
credentials, and API pricing/rate limits must be verified against live
documentation rather than model memory (`project.md` §12 O-3).

## 10. AI / LLM functionality

**VERIFIED: the application makes no model calls.** There is no LLM client, no
prompt, no embedding, no API key for a model provider.

This is by design, and it is the most important negative finding in this
document. `project.md` F-5 and C-20: an LLM opinion is **evidence, never a
verdict**. `tests/test_rights.py::test_llm_evidence_cannot_permit` proves
model-authored evidence with high confidence cannot by itself produce
`PERMITTED`.

The AI in this repository is the *agents working on it*, not a feature of it.

## 11. Cloud and hosting

**VERIFIED** (`project.md` §6, `DR-009`): **none.** Self-hosted, single Windows 11
machine, localhost only (`web.host = 127.0.0.1`). No cloud provider, and O-1 is
recorded as resolved to that choice.

## 12. Infrastructure and deployment configuration

**VERIFIED:** no Dockerfile, no compose file, no Terraform, no Kubernetes, no
`.github/workflows` for the application. See [deployment.md](deployment.md).

`aef/.github/ISSUE_TEMPLATE/` exists but belongs to the **framework's own**
upstream repository, not to ProMedia's delivery.

## 13. CI/CD pipelines

**VERIFIED: none.** No `.github/workflows/`, no `.gitlab-ci.yml`, no pre-commit
config. `CODEOWNERS` exists and explicitly says it is "a declaration of intent,
not a control" until the repo has a remote with branch protection.

There is also **no git remote** configured. **VERIFIED.**

## 14. Testing

**VERIFIED by execution** — `python -m pytest -p no:cacheprovider` on
2026-08-13: **322 passed, 1 warning in 75.37 s.**

Framework tooling has its own suite: `python aef/tools/run_tests.py` → **51 tests,
OK**, stdlib `unittest`, no pytest.

Detail in [testing.md](testing.md).

## 15. Monitoring, logging, analytics, error tracking

**VERIFIED:** no APM, no Sentry, no metrics exporter, no analytics.

What exists instead is an **audit log** (`core/audit.py`) — an immutable
who-did-what for every authority-gated operation, **including denials**. Audit
entries record exception *type* only, never `str(exc)`, because that would
persist credential-bearing text into a database that ships in backups (finding
I1/N1).

## 16. Security-sensitive areas

See [security.md](security.md). Ranked by consequence:

1. `core/registry.py` — authority + locking for all 29 operations.
2. `core/credentials.py` + `core/ops/accounts.py` — platform secrets.
3. `web/app.py` — the operator token, GET-mutation guard, origin check.
4. `core/rights*.py` + `core/rulesets/` — the F-3 hard gate; a wrong `PERMITTED`
   is copyright liability (C-29).
5. `core/posts.py` — the publish claim; a double publish is not recoverable (C-32).

## 17. Existing documentation and repository instructions

**VERIFIED, all read before writing anything:**

| File | Status |
|---|---|
| `CLAUDE.md` | **Preserved and extended**, not replaced. |
| `AGENTS.md` | Untouched — it is the Codex/generic entry point. |
| `CODEOWNERS` | Untouched. |
| `README.md` | Untouched. |
| `aef/**` | **Untouched. Read-only.** |
| `.ai/project.md` | Read, not modified (amendable only by re-running intake). |

## 18. Important user journeys

See [user-journeys.md](user-journeys.md). The spine is:

`connect account → ingest with rights declaration → attest declaration →
determine rights → seal provenance → queue post → operator approves →
publish → publication recorded`

Verified end-to-end by the orchestrating session during T-027 review (8-operation
slice) and by `tests/test_parity.py` / `tests/test_review_regressions.py`.

## 19. Existing architectural boundaries

**VERIFIED** by reading imports:

- `promedia/core/ops/*` — thin operation definitions. They register and delegate.
- `promedia/core/*` — the logic. Knows nothing about HTTP or argparse.
- `promedia/cli.py`, `promedia/web/app.py` — adapters. Supply a principal and
  render. **Never decide authority.**
- `promedia/config.py` — the only place a threshold literal may appear.
- `.ai/state/` — the coordination substrate. Not imported by application code.

## 20. Incomplete, broken, risky, or poorly tested areas

**VERIFIED** from `.ai/state/tasks.yaml`, `.ai/state/fabrications.yaml`, and
`aef.py progress` (81% complete, 25/31 tasks):

| Item | Status | Risk |
|---|---|---|
| **F-001 stub publisher** | Registered fabrication. Live adapters blocked (T-019). | Nothing has ever actually been published. |
| **T-022 DPAPI credential backend** | `ready`. v1 stores credentials **plaintext at rest** behind the right interface. | Real, disclosed, on the plan. |
| **T-033 account reconnect lock** | `ready`. `connect-account` is create-or-update, so a reconnect is an **unlocked write** to an existing account. | Low — operator authority, C-17 fixes humans at 1. |
| **T-018 scheduler** | `ready`. No scheduling exists. | S1/S6 unmet. |
| **T-030 hygiene** | `ready`. ffprobe timeout and `busy_timeout` still hardcoded; `config.load` assigns mutable module-level `DEFAULTS` on the no-file path. | Small but real. |
| **T-035 decision context** | `ready`. | Operator-facing. |
| **Real multi-process contention** | `NOT_RUN`, disclosed in T-027 review. All concurrency tested is two SQLite connections in one interpreter. | Genuine untested area. |
| **Independent human-experience review** | Open (OD-5). The HX pass has usually been done by the implementing agent, which is **not independent**. | Disclosed in `tasks.yaml review_shortfall_disclosed`. |
| **No git remote / no CI** | Every check is local and manual. | A regression reaches `master` unopposed. |
| **`Recording 2026-08-10 103755.mp4`** | 113 MB, untracked, at the repo root. | Not ignored by `.gitignore`; one `git add -A` commits it. **Left alone — deleting operator media is not an agent's call.** |

---

## What this means for the agent system

1. **Do not invent a domain.** Four were suggested; none exist. The five that do
   exist were found by enumerating the registry, not by reading names.
2. **Do not create a React agent.** There is no React. There is Jinja2 and a
   deliberate no-JS constraint.
3. **The registry is the chokepoint.** Almost every non-trivial change touches
   `core/registry.py` or something it dispatches to. File ownership matters more
   here than parallelism does.
4. **AEF already owns routing, roles, modes, and quality dimensions.** The
   `.claude/` layer **projects** them; it does not restate them. This project has
   already paid twice for a second source of truth (T-028's tautological parity
   gate, T-031's decorative operations dict). See `DR-014`.
