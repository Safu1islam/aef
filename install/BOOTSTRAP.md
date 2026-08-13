# Bootstrap

Run once per project. Creates the mutable project layer and wires up the tools.

## 1. Copy the framework into your project (read-only)

**Copy it in. That is the normal installation.** The framework travels *with*
the project, so a fresh clone of your repository already carries its governance
layer — no second fetch, and no way for an agent to start work without one.

```
git clone --depth 1 https://github.com/Safu1islam/aef aef
rm -rf aef/.git
```

Or take a release archive if you want a fixed version:

```
curl -L https://github.com/Safu1islam/aef/archive/refs/tags/v0.4.0.tar.gz | tar xz
mv aef-0.4.0 aef
```

Either way `aef/` becomes an ordinary directory inside your project, committed
to your repository like any other file.

A submodule also works, if you would rather upgrades be an explicit pin bump:

```
git submodule add https://github.com/Safu1islam/aef aef
```

The cost is real, so choose deliberately: every clone then needs
`git submodule update --init`, and a session that skips it finds an empty `aef/`
and runs with no constitution at all.

Whichever you choose:

- **Record what you installed.** `aef/VERSION` is the pin.
- **Never edit anything under `aef/`.** Configure through
  `.ai/config/overrides.yaml`, which deep-merges over the framework defaults.
- **Upgrade by replacing the directory** and reading `CHANGELOG.md` — never by
  editing in place. Anything below 0.2.0 has no plan tree and no tooling, so a
  project on it cannot run steps 5 or 6 of this file at all.

## 2. Create the project layer

```
.ai/
  project.md                 authoritative; written by Intake
  config/overrides.yaml      project-specific config, deep-merged over framework
  state/
    plan.yaml                the project plan as a tree; written by Planning
    tasks.yaml
    locks.yaml
    sessions.yaml            0.4.0. OPTIONAL, machine-written. Who is here now
    recommendations.yaml     0.4.0. OPTIONAL, machine-written. Proposals
    fabrications.yaml
    discovery.md             brownfield only
    decisions/
    checkpoints/
  memory/
    index.md
    domains/
  skills/
    index.md
```

## 3. Install adapters

Copy the stubs your tools use from `aef/adapters/` to the project root.
If an instruction file already exists, read it, preserve its project-specific content,
and append the AEF pointer. Never overwrite it.

## 4. Initialise

- **Greenfield:** run `aef/protocols/01-intake.md`, then `02-technology-selection.md`.
  Do not write code before both are complete.
- **Brownfield:** run `aef/protocols/03-discovery.md`. Then run intake in reduced form
  to capture constraints the code cannot tell you — latency budgets, volumes,
  compliance, who maintains it.

## 5. Plan the project, all of it

Run `aef/protocols/04-planning.md`. It produces `.ai/state/plan.yaml` and
`.ai/state/tasks.yaml`, covering the project end to end — not the first slice.
No code is written before this is done. Constitution §4a.

```
python aef/tools/aef.py validate     # plan and tasks must agree
python aef/tools/aef.py assign --auto --dry-run
python aef/tools/aef.py dashboard    # look at what you planned
```

Nothing here needs installing. Stdlib Python only; PyYAML is used if present and
is not required.

## 5a. Nominate a coordinator (0.4.0)

Only if more than one agent will ever work this project. A solo project can skip
this entirely — the files stay absent and nothing degrades.

```
python aef/tools/aef.py session start --id <session-id> --agent architect --main-engineer
python aef/tools/aef.py session list
```

One live session holds the post and coordinates; it is the orchestrator role with
continuity across sessions, not an eighth role. `protocols/10-main-engineer.md`.

Every joining agent then starts its own session and gets its bearings from state
rather than from a conversation:

```
python aef/tools/aef.py brief --agent <agent-id>
```

## 6. Verify installation

- [ ] `aef/VERSION` recorded and unmodified
- [ ] `.ai/project.md` exists with a quantified constraints table
- [ ] At least one decision record exists (greenfield)
- [ ] State files exist and parse — `python aef/tools/aef.py doctor`
- [ ] `.ai/state/plan.yaml` exists and `aef.py validate` exits 0
- [ ] Every plan leaf has an agent, or is deliberately unassigned
- [ ] Coordination notices from `validate` are empty, or each one is understood.
      They do not fail the gate; an unexplained one still means two state files
      disagree about who is working on what
- [ ] Adapter present for each tool in use
- [ ] `aef/` is git-ignored for edits or protected in review

## 7. Upgrading the framework

Bump the pin, read the changelog, run the verification checklist again. Project state
is never migrated by the framework automatically — migration is a task like any other.

Upgrading from 0.1.x: `plan.yaml` did not exist, so the tooling will refuse to run
until protocol 04 produces one. Building it from an existing flat `tasks.yaml` is
an arranging job, not a rewrite — do not renumber or rescope tasks while doing it.
