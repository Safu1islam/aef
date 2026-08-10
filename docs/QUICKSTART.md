# Quickstart

Ten minutes. Works with any agent tool.

## 1. Add the framework

```bash
cd your-project
git submodule add https://github.com/Safu1islam/aef aef
git -C aef checkout v0.1.0
```

No submodules? Copy the directory in and record the version. The only hard rule is
that `aef/` is never edited inside your project.

## 2. Create the project layer

```bash
mkdir -p .ai/state/decisions .ai/state/checkpoints .ai/memory/domains .ai/skills .ai/config
touch .ai/state/tasks.yaml .ai/state/locks.yaml .ai/state/fabrications.yaml
```

## 3. Install the adapter for your tool

```bash
cp aef/adapters/CLAUDE.md .        # Claude Code
cp aef/adapters/AGENTS.md .        # Codex, Kimi, most others
cp aef/adapters/.cursorrules .     # Cursor
cp aef/adapters/GEMINI.md .        # Gemini CLI
```

Already have one of these files? Keep it. Append the AEF pointer to the top.

## 4. Initialise

Open your agent tool in the project and say:

**New project**
> Read aef/core/CONSTITUTION.md, then run aef/protocols/01-intake.md.

**Existing project**
> Read aef/core/CONSTITUTION.md, then run aef/protocols/03-discovery.md.
> Then run reduced intake to capture constraints the code cannot tell you.

## 5. Work

> Read aef/core/CONSTITUTION.md. Objective: <what you want>. Act as orchestrator.

The agent classifies the change, selects roles, plans, implements, verifies, reviews,
and reports against the completion contract. You do not have to ask for tests,
documentation, or review — they are what "done" means here.

## What you should see

- A task graph in `.ai/state/tasks.yaml`, not a plan in the chat
- A decision record whenever a technology is chosen
- Entries in `.ai/state/fabrications.yaml` for anything faked
- A completion report that says what was *not* verified

If you see none of these, the agent skipped the framework. Point it back at the
constitution — and please open an issue, because that is the failure mode this
project most needs to know about.

## Tuning

Everything is configuration. Override in `.ai/config/overrides.yaml`:

```yaml
autonomy:
  default_level: staged        # stop at stage boundaries
execution:
  max_parallel_agents: 2
quality:
  block_completion_on_unresolved_fabrications: true
```

Add your own change classes and quality dimensions the same way. Nothing in the
framework is meant to be hardcoded for you.
