"""Where the framework's own files live, whichever way it was installed.

AEF is normally VENDORED into a host project:

    <project>/aef/config/agents.yaml
    <project>/.ai/state/plan.yaml

but it is also a repository in its own right, and in a standalone checkout the
same file is at the top:

    <checkout>/config/agents.yaml

Every tool that reads framework config has to work in both, or the framework's
own test suite fails in the framework's own repository — which is exactly what
happened, and was caught only by running the suite from a fresh clone before
publishing. Thirteen errors, all of them "agent catalogue not found", all
because the path was assumed rather than resolved.

Project STATE (`.ai/`) is not resolved this way and must not be. A standalone
framework checkout has no project state, and inventing a location for it would
let a tool silently write a host project's coordination files into the
framework's own directory.
"""

from __future__ import annotations

import os

__all__ = ["framework_file", "framework_root"]


def framework_root(project_root: str = ".") -> str:
    """The directory holding `config/`, `core/`, `protocols/` and `schemas/`.

    Vendored layout wins when both are present. That ordering matters: a host
    project that happens to have its own `config/` directory must not shadow the
    framework's, and `aef/` is unambiguous where a bare `config/` is not.
    """
    vendored = os.path.join(project_root, "aef")
    if os.path.isdir(os.path.join(vendored, "config")):
        return vendored
    return project_root


def framework_file(project_root: str, *parts: str) -> str:
    """Path to one framework file, e.g. framework_file(root, "config", "agents.yaml").

    Returns the vendored path when neither exists, so the error a caller raises
    names the location a project is expected to have rather than the fallback.
    """
    candidate = os.path.join(framework_root(project_root), *parts)
    if os.path.exists(candidate):
        return candidate
    return os.path.join(project_root, "aef", *parts)
