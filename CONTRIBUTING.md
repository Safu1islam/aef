# Contributing to AEF

AEF is a standard, not a library. That changes what a good contribution looks like.

## Principles this project is held to

1. **Configuration over prose.** If a rule can be data, it must be data. Prose is
   reinterpreted by every model that reads it; YAML is not.
2. **Evidence over assertion.** Any rule that cannot be checked will eventually be
   ignored. Prefer rules with observable outputs.
3. **Few roles, many dimensions.** Adding a role is expensive and needs strong
   justification. Adding a quality dimension is cheap and usually the right move.
4. **Context is a budget.** Anything added to `core/CONSTITUTION.md` is paid for on
   every single agent session, forever. The bar is very high.
5. **Model-agnostic.** No contribution may depend on one vendor's features. Tool
   specifics live in `adapters/`, and only as pointers.

## What is welcome

- New change classes and quality dimensions in `config/`
- Adapters for tools not yet covered
- Real-world reports: what an agent ignored, what it faked, where the process broke
- Validator tooling
- Translations of documentation

## What will be declined

- Adding content to the constitution that could live in a protocol
- Role proliferation, or specialist agents per technology
- Vendor-specific mechanics outside `adapters/`
- Rules with no evidence requirement
- Anything that increases always-loaded context without removing more than it adds

## Process

Open an issue describing the failure the change fixes before opening a pull request.
State which principle above it serves. Changes to `core/` require a stated context
cost and an argument for why the rule cannot live elsewhere.

## Reporting a framework violation

If an agent operating under AEF reported a false `PASSED`, shipped unregistered
fabrication, or claimed completion with unresolved blocking findings, that is the
most valuable bug report this project can receive. Include the model, the tool, the
prompt, and what the state files said at the time.
