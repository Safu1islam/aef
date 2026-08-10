# Bootstrap

Run once per project. Creates the mutable project layer and wires up the tools.

## 1. Attach the framework (read-only, pinned)

```
git submodule add <framework-repo-url> aef
git -C aef checkout v0.1.0
```

Any copy works, provided `aef/VERSION` is recorded and `aef/` is never edited.

## 2. Create the project layer

```
.ai/
  project.md                 authoritative; written by Intake
  config/overrides.yaml      project-specific config, deep-merged over framework
  state/
    tasks.yaml
    locks.yaml
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

## 5. Verify installation

- [ ] `aef/VERSION` recorded and unmodified
- [ ] `.ai/project.md` exists with a quantified constraints table
- [ ] At least one decision record exists (greenfield)
- [ ] State files exist and parse
- [ ] Adapter present for each tool in use
- [ ] `aef/` is git-ignored for edits or protected in review

## 6. Upgrading the framework

Bump the pin, read the changelog, run the verification checklist again. Project state
is never migrated by the framework automatically — migration is a task like any other.
