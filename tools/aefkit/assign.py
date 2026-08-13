"""Agent assignment — automatic from classification, manual by command.

Two paths, one record. Whichever path assigned an agent, the result is written
into .ai/state/plan.yaml and carries WHY it was assigned, so a later agent can
tell a decision from a guess.

Manual assignment is surgical: it rewrites the single `agent:` line for one node
rather than re-serialising the file. A plan carries the planner's comments and
ordering, and a round-trip through a YAML dumper would silently destroy both.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

from . import yamlio
from .paths import framework_file

__all__ = ["Catalogue", "Suggestion", "load_catalogue", "load_requirements",
           "suggest", "set_agent", "AssignError"]


class AssignError(Exception):
    pass


@dataclass
class Suggestion:
    agent: str | None
    basis: str          # change_class | capability | owner_role | title_keyword | none
    reason: str
    confidence: str     # strong | moderate | weak | none
    # Capabilities the change class demanded that the chosen agent does not
    # declare. Populated whatever the basis was, because the gap is worth seeing
    # even when an explicit routing rule made the choice.
    missing_capabilities: list[str] = field(default_factory=list)


class Catalogue:
    def __init__(self, data: dict[str, Any]):
        self.agents: dict[str, dict[str, Any]] = data.get("agents") or {}
        rules = data.get("assignment") or {}
        self.by_change_class: dict[str, str] = rules.get("by_change_class") or {}
        self.by_owner_role: dict[str, str] = rules.get("by_owner_role") or {}
        self.by_title_keyword: dict[str, list[str]] = rules.get("by_title_keyword") or {}
        self.fallback: str | None = rules.get("fallback")
        self.constraints: dict[str, Any] = data.get("constraints") or {}

    def role_of(self, agent: str | None) -> str | None:
        if not agent:
            return None
        return (self.agents.get(agent) or {}).get("role")

    def known(self, agent: str) -> bool:
        return agent in self.agents

    def capabilities_of(self, agent: str | None) -> set[str]:
        """What this agent declares it can do. Absent means declares nothing,
        which is different from declares everything — an agent with no
        capabilities never wins a capability match."""
        if not agent:
            return set()
        declared = (self.agents.get(agent) or {}).get("capabilities") or []
        if not isinstance(declared, list):
            return set()
        return {str(c).strip().lower() for c in declared if str(c).strip()}

    def ids(self) -> list[str]:
        return sorted(self.agents)


def load_catalogue(project_root: str = ".", *, force_bundled: bool = False) -> Catalogue:
    """Framework defaults, with the project's `agents:` override deep-merged over
    them. Same precedence rule as framework.yaml / overrides.yaml."""
    base_path = framework_file(project_root, "config", "agents.yaml")
    if not os.path.exists(base_path):
        raise AssignError(f"agent catalogue not found at {base_path}")
    data = yamlio.load(base_path, force_bundled=force_bundled) or {}

    override_path = os.path.join(project_root, ".ai", "config", "overrides.yaml")
    if os.path.exists(override_path):
        overrides = yamlio.load(override_path, force_bundled=force_bundled) or {}
        # `agents:` adds or replaces catalogue entries; `assignment:` adjusts the
        # rules. Two keys rather than one nested blob, so a project adding an
        # agent does not have to restate the rule table to do it.
        for key in ("agents", "assignment", "constraints"):
            section = overrides.get(key)
            if isinstance(section, dict):
                data = _deep_merge(data, {key: section})
    return Catalogue(data)


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_requirements(project_root: str = ".", *, force_bundled: bool = False) -> dict[str, list[str]]:
    """change_class -> the capabilities that class demands, from routing.yaml.

    Same override path as everything else: a project may tighten or extend the
    requirement list in .ai/config/overrides.yaml under `classes:`.
    """
    out: dict[str, list[str]] = {}
    paths = [
        framework_file(project_root, "config", "routing.yaml"),
        os.path.join(project_root, ".ai", "config", "overrides.yaml"),
    ]
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            data = yamlio.load(path, force_bundled=force_bundled) or {}
        except Exception:  # noqa: BLE001 - a broken config must not stop assignment
            continue
        classes = data.get("classes")
        if not isinstance(classes, dict):
            continue
        for name, entry in classes.items():
            if isinstance(entry, dict) and isinstance(entry.get("requires_capabilities"), list):
                out[str(name)] = [str(c).strip().lower()
                                  for c in entry["requires_capabilities"] if str(c).strip()]
    return out


def _score(required: list[str], catalogue: Catalogue) -> list[tuple[float, str, list[str]]]:
    """Every agent scored against a requirement list, best first.

    Score is the fraction of required capabilities the agent declares. Ties break
    on agent id so the result is deterministic — an assignment that changed
    between runs on equal evidence would be impossible to review.
    """
    wanted = set(required)
    scored: list[tuple[float, str, list[str]]] = []
    for agent in catalogue.ids():
        declared = catalogue.capabilities_of(agent)
        if not declared:
            continue
        hit = wanted & declared
        if not hit:
            continue
        scored.append((len(hit) / len(wanted), agent, sorted(wanted - declared)))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return scored


def suggest(task: dict[str, Any] | None, title: str, catalogue: Catalogue,
            requirements: dict[str, list[str]] | None = None) -> Suggestion:
    """Pick the implementing agent for one unit of work.

    Evidence order is deliberate and is the same order routing.yaml already
    trusts: an explicit classification beats a declared role, and both beat a
    word in a title. The weakest basis is labelled weak in the reason so nobody
    reads a keyword match as a decision.
    """
    task = task or {}

    requirements = requirements or {}
    change_class = task.get("change_class")
    required = requirements.get(str(change_class)) if change_class else None

    if change_class and change_class in catalogue.by_change_class:
        agent = catalogue.by_change_class[change_class]
        reason = f"change_class '{change_class}' routes to {agent}"
        missing: list[str] = []
        if required:
            missing = sorted(set(required) - catalogue.capabilities_of(agent))
            if missing:
                # The routing table stays authoritative — a project mapped this
                # class to this agent on purpose. But an agent that does not
                # declare what the class demands is worth saying out loud, since
                # the usual cause is a capability list nobody updated.
                reason += (
                    f", but {agent} does not declare: {', '.join(missing)}. "
                    "Routing wins; the gap is reported, not silently corrected."
                )
            else:
                reason += f" and covers all {len(required)} required capabilities"
        return Suggestion(agent, "change_class", reason, "strong", missing)

    # Capability match. Reached when a class declares what it needs but no
    # explicit agent is mapped to it — the case a heterogeneous fleet creates
    # every time a project adds an agent without rewriting the rule table.
    if required:
        scored = _score(required, catalogue)
        if scored:
            best, agent, missing = scored[0]
            confidence = "strong" if not missing else "moderate"
            covered = len(required) - len(missing)
            reason = (
                f"capability match: {agent} covers {covered}/{len(required)} of what "
                f"'{change_class}' requires ({int(round(best * 100))}%)"
            )
            if missing:
                reason += f"; missing {', '.join(missing)}"
            return Suggestion(agent, "capability", reason, confidence, missing)

    owner_role = task.get("owner_role")
    if owner_role and owner_role in catalogue.by_owner_role:
        agent = catalogue.by_owner_role[owner_role]
        return Suggestion(
            agent, "owner_role",
            f"owner_role '{owner_role}' routes to {agent}",
            "moderate",
        )

    haystack = f"{title} {task.get('title') or ''} {task.get('objective') or ''}".lower()
    for agent, keywords in catalogue.by_title_keyword.items():
        for keyword in keywords:
            if re.search(rf"\b{re.escape(str(keyword).lower())}\b", haystack):
                return Suggestion(
                    agent, "title_keyword",
                    f"WEAK: matched the word '{keyword}' in the title — no change_class "
                    "or owner_role was set. Classify the task to assign it properly.",
                    "weak",
                )

    if catalogue.fallback:
        return Suggestion(catalogue.fallback, "fallback",
                          f"no rule matched; catalogue fallback is {catalogue.fallback}", "weak")
    return Suggestion(None, "none",
                      "no change_class, owner_role or keyword matched — left unassigned "
                      "deliberately rather than guessed", "none")


# ---------------------------------------------------------------------------
# Writing back
# ---------------------------------------------------------------------------

_ID_LINE = re.compile(r"^(?P<indent>\s*)(?:-\s+)?id:\s*(?P<quote>['\"]?)(?P<id>[^'\"\s#]+)(?P=quote)\s*(?:#.*)?$")


def set_agent(plan_path: str, node_id: str, agent: str | None, *,
              locked: bool = True, catalogue: Catalogue | None = None) -> str:
    """Set (or clear) one node's agent, in place, preserving everything else.

    `locked=True` marks the assignment as MANUAL, which stops a later
    `assign --auto` pass from overwriting a human's decision. That is the whole
    point of the flag: automatic assignment must never quietly undo an explicit
    one.
    """
    if agent and catalogue is not None and not catalogue.known(agent):
        known = ", ".join(sorted(catalogue.agents)) or "(catalogue empty)"
        raise AssignError(
            f"unknown agent '{agent}'. Known agents: {known}\n"
            "Add it to .ai/config/overrides.yaml under `agents:` before assigning it."
        )

    with open(plan_path, "r", encoding="utf-8") as handle:
        lines = handle.read().splitlines(keepends=True)

    start = _find_node(lines, node_id, plan_path)
    indent, body_start, body_end = _node_body(lines, start)

    existing_agent = _find_key(lines, body_start, body_end, indent, "agent")
    existing_locked = _find_key(lines, body_start, body_end, indent, "agent_locked")

    replacement: list[str] = []
    if agent is not None:
        replacement.append(f"{indent}agent: {agent}\n")
        if locked:
            replacement.append(f"{indent}agent_locked: true\n")

    # Remove the old pair, high index first so earlier indices stay valid.
    for position in sorted([position for position in (existing_agent, existing_locked) if position is not None],
                           reverse=True):
        del lines[position]
        if position < body_start:
            body_start -= 1

    insert_at = existing_agent if existing_agent is not None else body_start
    insert_at = min(insert_at, len(lines))
    lines[insert_at:insert_at] = replacement

    with open(plan_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(lines))

    if agent is None:
        return f"cleared the agent on {node_id}"
    return f"assigned {node_id} -> {agent}" + (" (manual, locked)" if locked else " (auto)")


def _find_node(lines: list[str], node_id: str, plan_path: str) -> int:
    matches = [
        index for index, line in enumerate(lines)
        if (match := _ID_LINE.match(line.rstrip("\n"))) and match.group("id") == node_id
    ]
    if not matches:
        raise AssignError(f"no node with id '{node_id}' in {plan_path}")
    if len(matches) > 1:
        raise AssignError(
            f"id '{node_id}' appears {len(matches)} times in {plan_path}; "
            "fix the duplicate before assigning"
        )
    return matches[0]


def _node_body(lines: list[str], id_index: int) -> tuple[str, int, int]:
    """Return (indent of the node's keys, first body line, one-past-last body line).

    The node's keys sit at the indentation of its `id:` key. For a `- id: X`
    entry that is two columns right of the dash.
    """
    raw = lines[id_index].rstrip("\n")
    stripped = raw.lstrip(" ")
    lead = len(raw) - len(stripped)
    indent = " " * (lead + 2) if stripped.startswith("- ") else " " * lead

    end = id_index + 1
    while end < len(lines):
        line = lines[end].rstrip("\n")
        if line.strip() == "" or line.lstrip().startswith("#"):
            end += 1
            continue
        current = len(line) - len(line.lstrip(" "))
        if current < len(indent) or (current == len(indent) and line.lstrip().startswith("- ")):
            break
        end += 1
    return indent, id_index + 1, end


def _find_key(lines: list[str], start: int, end: int, indent: str, key: str) -> int | None:
    """Index of `key:` at exactly this node's own level. Nested occurrences at a
    deeper indent belong to a child and must not be touched."""
    pattern = re.compile(rf"^{re.escape(indent)}{re.escape(key)}:")
    for index in range(start, min(end, len(lines))):
        if pattern.match(lines[index].rstrip("\n")):
            return index
    return None
