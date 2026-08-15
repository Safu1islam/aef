# Real-world projects

This directory holds full projects that are actually being built under AEF —
not fixtures. `docs/example/` is a synthetic project maintained only to
generate this repository's own README screenshots; nothing in it is real
work. What's here is the opposite: real state, real task history, real
decisions, taken from an active build and included so the framework can be
judged against something with actual mileage on it, not just its own pitch.

## [ProMedia](ProMedia/)

A single-operator social media production and publishing system — media
ingest, a rights-clearing gate, scheduling, and publishing — built end to end
under AEF starting 2026-08-08. It is the operator's own active project, not a
demo written to showcase the framework; AEF is the process it happens to be
built with.

Its `.ai/state/` carries the same kind of history the framework asks every
project to keep: a project constitution, decision records, a task plan, and
session/lock state written continuously by the agents that worked on it. Its
own [README](ProMedia/README.md) and [`.ai/project.md`](ProMedia/.ai/project.md)
are the authoritative description of what it is; nothing here restates that.

`docs/screenshots/` under it has a few snapshots — the app's own UI and the
AEF dashboard reading this project's actual plan — taken 2026-08-16.
