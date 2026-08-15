# ProMedia

Single-operator social media production and publishing, with a hard rights gate.

Built under the [Agentic Engineering Framework](aef/README.md). The authoritative
description of what this project is and what constrains it lives in
[`.ai/project.md`](.ai/project.md) — read that before changing anything.

**Status: v1 vertical slice. Publishing is SIMULATED.** No real platform adapter
exists yet (fabrication `F-001`), and simulation is disabled by default. See
[`.ai/state/fabrications.yaml`](.ai/state/fabrications.yaml).

## The two rules that shape everything

**Every capability is one implementation on two surfaces.** A capability
reachable from the UI but not from the repo — or the reverse — is a build
failure, not a gap (`S4`). Both surfaces are projections of the operation
registry in `promedia/core/registry.py`, and `tests/test_parity.py` fails the
build if they diverge.

**Agents may not publish, spend, or clear a rights flag.** Agents draft, ingest,
analyse, rights-check and queue. The operator approves. This is enforced in the
operation layer, so it holds identically on both surfaces.

## Running it

```bash
python -m promedia ops --json
```

That lists every capability with its parameters and required authority — it is
the whole contract, and it is the intended starting point for an agent.

The operator UI:

```bash
python -c "from promedia.web.app import run_server; run_server()"
```

It prints a URL containing the operator token. **Opening that URL is what grants
operator authority to the browser session.** Without it the UI runs with agent
authority and refuses to approve or publish — localhost is not an authentication
boundary, because an agent can issue local HTTP requests as easily as a browser.

Three pages: `/` is the dashboard, `/ops` lists every capability, and
`/ops/<operation>` runs one. That last page is generated from the registry, not
written per operation — the form has one control per declared parameter, and
submitting it goes through the same path `/api/op/<operation>` takes, so
authority (F-2), entity locking (C-19) and the rights gate (F-3) are enforced
identically whichever you use. `/posts/<id>` remains the place to approve and
publish, because it is the only screen that shows the decision context first.

## The slice, end to end

```bash
python -m promedia connect-account --platform x --handle you --secret-stdin  # operator
python -m promedia ingest --source-path clip.mp4 \
    --declaration '{"authorship":"operator_original","third_party_material":[]}'
python -m promedia attest-declaration --asset-id as_...                      # operator
python -m promedia determine-rights   --asset-id as_...
python -m promedia seal-provenance    --asset-id as_...
python -m promedia queue-post --account-id acct_... --asset-id as_... --body "..."
# approve and publish in the UI
```

There is deliberately no `--secret` flag. A credential passed as an argument is
visible to every process on the machine and persists in shell history, so
sensitive parameters are declared `sensitive` in the registry and accepted only
via `--<name>-stdin` or `--<name>-file`. The same declaration makes the web
surface refuse them in a query string.

An agent can run the un-annotated steps. The three marked `operator` require the
token.

Exit codes say what to do next, which is the only thing an agent can act on
without parsing prose (`DR-012`):

- **0** it worked.
- **1** it failed — a business rule refused, or the thing was not there. Running
  the same call again will fail the same way.
- **2** the call itself was wrong: a bad or missing parameter, or a precondition
  that cannot be undone (the media is gone, the config is broken).
- **3** operator authority is required. Hand it to the human; no agent can
  resolve this one.
- **4** another writer holds this entity right now (`C-19`). Nothing is wrong
  with the request — take a different ready task and come back to it.

The web surface carries the same distinctions, mapped per error class rather
than per exit code: **409** for a locked entity, 403 where operator authority is
required, 404 for something absent, 400 for the rest. (The two are not derivable
from each other — a missing thing is exit 1 but HTTP 404, while a plain refusal
is exit 1 and HTTP 400.) `tests/test_parity.py` fails the build if the two
surfaces ever disagree.

## Things that are deliberate and look like bugs

- **An agent's own declaration never permits.** Ingesting as an agent and running
  `determine-rights` yields `ESCALATE / DECLARATION_NOT_OPERATOR_ATTESTED` until
  the operator runs `attest-declaration`. An agent asserting "this is the
  operator's own work" is a proposal, not a fact (`DR-011`).
- **Derivatives inherit the worst verdict in their ancestry, evaluated now.**
  Editing a blocked asset does not clear it, and a source that degrades later
  degrades everything derived from it. Transformation is a production function,
  not a copyright-clearing function (`F-4`).
- **Media duration is `null` with `probe_status: unavailable`.** ffmpeg is not
  installed. A guessed duration would be a fabrication.
- **There is no `clear-rights-flag` operation.** `F-3` admits no override path,
  so none is offered to anyone.

## Layout

| Path | What it is |
|---|---|
| `promedia/core/registry.py` | The operation registry. One implementation per capability |
| `promedia/core/rights_engine.py` | Pure, versioned, deterministic verdicts |
| `promedia/core/rulesets/` | The rules, as data. Jurisdiction-neutral and conservative |
| `promedia/core/storage.py` | Reservation ledger enforcing the 100 GB ceiling |
| `promedia/core/provenance.py` | Records that outlive the media they describe |
| `promedia/cli.py`, `promedia/web/` | Thin adapters. No business logic, ever. Both generate their surface from the registry |
| `aef/` | **Read-only.** Version-pinned framework. Never edit |
| `.ai/` | Project state: constitution, decisions, tasks, fabrications |

## Tests

```bash
python -m pytest
```

322 tests. `tests/test_review_regressions.py` and `tests/test_hardening.py` hold
the ones written from an independent reviewer's reproduced attacks — those are
the interesting ones, and each was confirmed to fail before its fix.
