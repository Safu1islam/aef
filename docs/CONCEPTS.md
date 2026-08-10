# Concepts

## Three layers

| Layer | Path | Mutable |
|---|---|---|
| Framework | `aef/` | Never. Version-pinned. |
| Project constitution | `.ai/project.md` | Via intake or amendment only |
| Project state | `.ai/state/` | Continuously, per schema |

This separation is the reason the framework stays universal. Projects cannot mutate the
standard, so the standard does not fork on contact with reality.

## Roles, not agents

Seven roles: orchestrator, analyst, architect, implementer, reviewer, verifier,
human-experience-reviewer, domain-steward. Any model occupies any role by loading the
constitution plus one role file.

The alternative — a specialist agent per technology — produces hundreds of overlapping
delegation descriptions and a router that cannot choose between them. Expertise belongs
in quality dimensions and project-generated skills, which load only when required.

## Quality dimensions

Forty-five dimensions, each stating what must exist and what counts as evidence.
`routing.yaml` decides which are mandatory for a change class. This is how
"enterprise-grade" becomes checkable instead of aspirational.

## Modes

`fast` for localised reversible changes, `standard` for normal work, `critical` for
anything irreversible, security-relevant, or money-relevant. Escalators upgrade a mode
automatically and can never downgrade it.

## The fabrication registry

Every mock, stub, placeholder, hardcoded value, and simulated response, registered when
created, with a replacement condition and an owning task. This single file is what makes
an AI-built system auditable by a human who did not watch it being built.

## Verification statuses

`PASSED` `FAILED` `NOT_AVAILABLE` `NOT_RUN` `BLOCKED` `NEEDS_HUMAN`

`PASSED` requires execution and observation. `NOT_RUN` is an honest, acceptable answer.
A false `PASSED` is the most damaging act available to an agent here, because every
downstream decision inherits it.

## Locks

Agents claim exclusive write scope before editing. Overlapping ownership between
concurrent tasks means the plan was wrong, not that the lock should be broken.
