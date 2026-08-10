# Adapters

Each AI tool reads a different entry file. The adapters are thin pointers so that
every tool lands on the same framework. Never duplicate framework content into an
adapter — duplication is how versions drift.

Copy the relevant stub to the project root at install time:

| Tool | File |
|---|---|
| Claude Code | `CLAUDE.md` |
| Codex / OpenAI agents | `AGENTS.md` |
| Cursor | `.cursorrules` |
| Gemini CLI | `GEMINI.md` |
| GitHub Copilot | `.github/copilot-instructions.md` |
| Kimi and others | `AGENTS.md` (de facto default) |

All stubs have identical content apart from tool-specific extras.
