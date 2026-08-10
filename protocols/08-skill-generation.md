# Protocol 08 — Project Skill Generation

Generic skills mostly do not fit a specific application, and loading them wastes
context. This framework does not ship a skill library. It grows one from the project.

## When to generate

- A procedure has been performed `skills.repetition_threshold` times
- Discovery revealed a project-specific workflow (build, deploy, seed, migrate, test)
- A domain has conventions an agent would otherwise rediscover

## What a generated skill contains

Name, trigger condition, preconditions, exact verified commands, expected output,
failure modes and recovery, and the files it touches. Every command in a generated
skill must have been executed successfully at least once — untested skills are
fabrications and are registered as such.

## Where

`.ai/skills/<skill-name>/SKILL.md`, plus an entry in `.ai/skills/index.md`.
Keep the index thin; agents read the index and load one skill.

## Maintenance

When the underlying code changes, the skill is updated in the same task, or it is
deleted. A stale skill is worse than no skill, because it is trusted.
Unused skills are pruned after `skills.prune_unused_after_days`.
