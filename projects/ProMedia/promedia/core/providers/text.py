"""Text-generation capability (T-048).

READ THIS BEFORE CALLING ``run-capability`` WITH capability=text.

Operator instruction, 2026-08-13, recorded in T-048's task note: **the agent
IS the language model** for agent-driven text generation. An agent drafting
a caption, a post body, or an evidence note already has a language model in
the loop — itself — and asking this seam to call a *second* one to do the
same job would spend real money reproducing work the agent can do for free.

This capability exists ONLY for UI-triggered generation with no agent in the
loop — an operator clicking "draft this for me" in the web surface with no
agent session running. That UI feature does not exist yet either; when it
is built, it is the one caller with a legitimate reason to reach this class.

Structural enforcement, not just documentation: ``run-capability`` is
registered ``authority="operator"`` (see ``promedia/core/ops/providers.py``),
so an agent principal cannot invoke ANY capability through it, this one
included — F-2 already forbids it before this class's own logic runs.
"""

from __future__ import annotations

from .base import BaseCapability


class TextCapability(BaseCapability):
    kind = "text"
    provider_name = "an LLM completion API — for example OpenAI's chat completions endpoint"
    package = "openai"
    credential_env = "OPENAI_API_KEY"
    pricing_reference = "the provider's own current API pricing page, checked at time of use"
    what_it_would_satisfy = (
        "UI-triggered content drafting with NO agent in the loop, only. An "
        "agent must never call this to draft its own text — see this "
        "module's docstring. The exact package name, credential convention "
        "and pricing above are NOT verified against live documentation by "
        "this task (project.md O-3) and must be confirmed before use."
    )
