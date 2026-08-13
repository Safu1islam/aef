"""AEF tooling — the framework's only executable layer.

Everything here is stdlib-only by contract. AEF is copied into projects whose
language and dependency set are unknown to it, so a tool that needs `pip install`
is a tool that does not run. See aef/tools/README.md.

Read-only rule: this package lives under aef/ and is therefore not editable by a
project. Projects configure it through .ai/config/overrides.yaml.
"""

__all__ = ["paths", "yamlio", "writer", "model", "assign", "team", "teamstore", "render", "server"]

# Kept in step with aef/VERSION. The dashboard displays it so a stale vendored
# copy is visible rather than silent.
AEF_TOOLS_VERSION = "0.4.1"
