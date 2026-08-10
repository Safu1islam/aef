# Changelog

All notable changes to AEF are recorded here.
Versions follow semantic versioning. Projects pin a version; upgrades are deliberate.

## [0.1.0] — 2026-08-07

Initial public release.

### Added
- `core/CONSTITUTION.md` — always-loaded operating contract, under 200 lines
- `core/OPERATING-LOOP.md` — ten-stage loop, each stage producing an artifact
- `core/NON-NEGOTIABLES.md` — prohibited actions and human-approval gates
- `protocols/` — intake, technology selection, discovery, planning, execution,
  verification, completion, project skill generation
- `roles/` — seven model-agnostic role contracts
- `config/routing.yaml` — change class to mode, roles, and mandatory quality dimensions
- `config/quality-dimensions.yaml` — 45 dimensions, each with required evidence
- `config/framework.yaml` — autonomy, context budget, model tiering, execution defaults
- `schemas/` — task graph, fabrication registry, file locks, domain memory
- `adapters/` — entry stubs for Claude Code, Codex/AGENTS, Cursor, Gemini
- `install/BOOTSTRAP.md` — installation and upgrade procedure

### Known gaps
- No automated validator yet (`verify` script planned for 0.2.0)
- Not yet battle-tested against a large existing repository
- Routing classes cover common web/service/AI work; specialised domains need extension
