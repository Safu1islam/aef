# aef/tools

The framework's only executable layer. Everything else in `aef/` is documents.

```
python aef/tools/aef.py dashboard     # project tree + progress, on localhost
python aef/tools/aef.py progress      # the same summary as text
python aef/tools/aef.py tree          # the plan as an ASCII tree
python aef/tools/aef.py validate      # exit 1 if plan and tasks disagree
python aef/tools/aef.py assign --auto # assign agents from classification
python aef/tools/aef.py doctor        # what the tool can see and read
python aef/tools/run_tests.py         # the tooling's own tests
```

Run from the project root — the one containing `aef/` and `.ai/`.

## Contract

**Stdlib only, no install step.** AEF is copied into projects whose language and
dependency set it does not know. A tool that needs `pip install` is a tool that
does not run. This is a hard constraint, not a preference: nothing here may grow
a third-party import.

**PyYAML is used if present, and not required.** `aefkit/yamlio.py` falls back to
a bundled reader covering the subset AEF state files use. The fallback is not
assumed to work — `tests/test_yamlio.py` parses the project's real state files
with both readers and asserts the results are equal, and `aef doctor` runs the
same comparison against your files. Anything the bundled reader does not
understand raises with the file and line rather than guessing.

**The dashboard is read-only and binds 127.0.0.1.** It serves GET, mutates
nothing, and re-reads the files on every request. A plan names internal work and
blockers; exposing it is a decision, so `--host` exists but the default is
loopback. Assignment is a CLI command precisely so no link can change a plan.

## Layout

| File | Does |
|---|---|
| `aef.py` | Command line. The only entry point. |
| `aefkit/yamlio.py` | YAML reading, PyYAML or bundled. |
| `aefkit/model.py` | The plan tree, derived status, progress arithmetic, validation. |
| `aefkit/assign.py` | Agent rules, and the surgical write-back into `plan.yaml`. |
| `aefkit/render.py` | The two HTML views. Self-contained: no CDN, no build. |
| `aefkit/server.py` | `http.server` wrapper. |
| `run_tests.py` | `unittest` runner. No pytest. |

## What this deliberately does not do

- **No writes to `tasks.yaml`.** Status is protocol 05's business, written by the
  agent doing the work with the evidence that justifies it. A tool that flipped a
  task to `complete` would be manufacturing exactly the unverified claim
  Constitution §7 forbids.
- **No stored rollups.** Group status and percentages are computed on read. See
  `schemas/plan.schema.yaml`.
- **No project-specific knowledge.** Nothing here knows what any particular
  project is about. The tools read schemas, not domains.
