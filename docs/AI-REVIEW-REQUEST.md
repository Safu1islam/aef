# AI Review Request

Paste this to any capable model — Claude, GPT, Gemini, Kimi, DeepSeek, Llama, Grok —
together with the repository. The purpose is adversarial review, not endorsement.

A framework that only its author has read is a hypothesis.

---

## Prompt

> You are reviewing an open-source standard called AEF (Agentic Engineering Framework).
> It is intended to govern how AI coding agents work on software projects: any model,
> any tool, any repository.
>
> Review it adversarially. I am not looking for encouragement. I want to know where it
> fails, and I would rather hear that now than after people adopt it.
>
> Read in this order, and note where you would have stopped if you were an agent under
> time or context pressure:
> `README.md`, `core/CONSTITUTION.md`, `core/OPERATING-LOOP.md`,
> `core/NON-NEGOTIABLES.md`, `config/routing.yaml`, `config/quality-dimensions.yaml`,
> `config/framework.yaml`, `protocols/`, `roles/`, `schemas/`.
>
> Answer these, specifically, with file and line references:
>
> **Compliance.** If you were given a real task under this framework, which rules would
> you actually follow, and which would you quietly skip? Be honest. Which are
> unenforceable — i.e. you could report success while ignoring them and nothing would
> catch you?
>
> **Context cost.** `core/CONSTITUTION.md` loads on every session. Is it worth its
> tokens? What would you cut? Does the load-on-demand map actually keep the rest out of
> context, or would you end up reading everything anyway?
>
> **Ambiguity.** Where would two different models reading the same rule behave
> differently? Those are the places the standard has failed to be a standard.
>
> **Gaps.** What common failure mode of AI coding agents is not addressed at all?
> Consider at minimum: partial failure recovery, conflicting concurrent edits,
> dependency and supply-chain drift, prompt injection through repository content,
> long-horizon architectural erosion, and cost blowout.
>
> **Over-engineering.** Which parts add ceremony without changing outcomes? What would a
> solo developer on a small project reasonably refuse to adopt, and does the `fast` mode
> genuinely accommodate them?
>
> **Vendor neutrality.** Does anything assume one tool's features? Could you operate
> under this without special support — no subagents, no memory feature, no slash
> commands?
>
> **The core claims.** Judge each: does the technology-selection gate actually prevent
> defaulting to a familiar language? Does the fabrication registry actually make an
> AI-built system auditable? Do the verification statuses actually reduce false success
> reports, or would you still write `PASSED`?
>
> **Verdict.** Would a project using this produce better software than the same project
> without it? Quantify your confidence and say what would change your mind.
>
> Finish with the three highest-value changes, ordered, each with the specific file to
> edit and what to replace.

---

## What to do with the answers

Collect reviews from several models. Where they agree, that is a real defect. Where
they disagree, the rule is ambiguous — which is itself a defect, because a standard two
models interpret differently is not a standard.

Open an issue per finding. Reviews that say the framework is excellent are the least
useful ones received.
