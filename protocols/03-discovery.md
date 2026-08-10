# Protocol 03 — Discovery  (brownfield, runs once)

Establish what exists, cheaply, and write it down so no agent ever pays this cost again.

Skip entirely if `.ai/state/discovery.md` exists and its `repo_hash` matches HEAD.

## Order (cheapest signal first — stop when the picture is stable)

1. Manifests, lockfiles, config, CI files, container and infra definitions
2. Directory shape and entry points
3. Routes, schemas, migrations, public interfaces
4. Existing documentation and agent instruction files (read, never overwrite)
5. Tests — what exists, what is skipped, how they are run
6. Only then, source files, and only the ones the above pointed to

Never read the whole repository. Never read dependency directories.

## Record

`.ai/state/discovery.md` — purpose, stack, entry points, how to run, how to test,
how it deploys, verified commands, and a `repo_hash`.

`.ai/memory/domains/<domain>/` — one per verified product domain, using the memory
schema. A domain is created from evidence (routes, tables, services, journeys), never
from a name someone mentioned.

Label every uncertain finding `UNVERIFIED`. An unverified fact that later turns out
wrong is far more expensive than a gap.

## Also record what is broken

Discovery is not neutral. Note: incomplete features, disconnected UI, dead code,
missing tests, secrets in code, unhandled failures, and anything that appears to run
on fabricated data — the last of these goes straight into `fabrications.yaml`.
