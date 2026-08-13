"""Writes to sessions.yaml and recommendations.yaml.

Reading and deriving live in `team.py`. This module is the only thing that
mutates either file, so every rule about what a valid transition is has exactly
one home.

Mutation works on the RAW dictionaries rather than on the `Session` and
`Recommendation` dataclasses. That is deliberate: a dataclass knows only the
fields this version of AEF declares, so round-tripping through one would
silently drop anything a newer version — or a project extension — had written.
Reading raw, changing the key that changed, and writing raw back preserves it.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from . import team as team_mod, writer, yamlio

__all__ = [
    "TeamStoreError",
    "start_session",
    "heartbeat",
    "end_session",
    "claim_main_engineer",
    "add_recommendation",
    "resolve_recommendation",
    "next_recommendation_id",
]

SESSIONS_HEADER = """.ai/state/sessions.yaml — who is here right now.
Schema: aef/schemas/session.schema.yaml

MACHINE-MANAGED. Written by `aef.py session ...`; heartbeats rewrite it often.
Do not hand-edit: comments and ordering are not preserved across a write.

`stale` is never stored here. It is derived from heartbeat age on read, because
a crashed process cannot record its own death."""

RECOMMENDATIONS_HEADER = """.ai/state/recommendations.yaml — proposals nobody has authorised yet.
Schema: aef/schemas/recommendation.schema.yaml

MACHINE-MANAGED. Written by `aef.py recommend ...`.

A rejected recommendation is kept, with its reason. That is the point of the
file: it stops the same idea being re-proposed every few sessions."""


class TeamStoreError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _state_path(root: str, name: str) -> str:
    return os.path.join(root, ".ai", "state", name)


def _read(path: str, key: str, *, force_bundled: bool = False) -> dict[str, Any]:
    if not os.path.exists(path):
        return {key: []}
    data = yamlio.load(path, force_bundled=force_bundled) or {}
    entries = data.get(key)
    if not isinstance(entries, list):
        entries = []
    data[key] = [e for e in entries if isinstance(e, dict)]
    return data


def _require_state_dir(root: str) -> None:
    state = os.path.join(root, ".ai", "state")
    if not os.path.isdir(state):
        raise TeamStoreError(
            f"no {state} — this project has no AEF state layer. "
            "Run aef/install/BOOTSTRAP.md first."
        )


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

def _find(entries: list[dict[str, Any]], session_id: str) -> dict[str, Any] | None:
    for entry in entries:
        if str(entry.get("id")) == session_id:
            return entry
    return None


def start_session(root: str, session_id: str, agent: str, *, role: str | None = None,
                  vendor: str | None = None, model: str | None = None,
                  task: str | None = None, activity: str | None = None,
                  main_engineer: bool = False,
                  known_agents: set[str] | None = None,
                  force_bundled: bool = False) -> dict[str, Any]:
    _require_state_dir(root)
    if known_agents is not None and agent not in known_agents:
        known = ", ".join(sorted(known_agents)) or "(catalogue empty)"
        raise TeamStoreError(
            f"unknown agent '{agent}'. Known agents: {known}\n"
            "Add it under `agents:` in .ai/config/overrides.yaml before running as it."
        )

    path = _state_path(root, "sessions.yaml")
    data = _read(path, "sessions", force_bundled=force_bundled)
    if _find(data["sessions"], session_id) is not None:
        raise TeamStoreError(
            f"session '{session_id}' already exists. Ids identify one process and "
            "are never reused; use a new one, or `session end` the old one first."
        )

    if main_engineer:
        _refuse_second_main_engineer(root, data["sessions"], force_bundled)

    entry: dict[str, Any] = {
        "id": session_id,
        "agent": agent,
        "role": role,
        "vendor": vendor,
        "model": model,
        "main_engineer": main_engineer,
        "task": task,
        "activity": activity,
        "status": "working" if task else "idle",
        "started_at": _now(),
        "heartbeat_at": _now(),
    }
    data["sessions"].append(entry)
    writer.dump(path, data, SESSIONS_HEADER)
    return entry


def heartbeat(root: str, session_id: str, *, activity: str | None = None,
              status: str | None = None, task: str | None = None,
              blocked_reason: str | None = None,
              force_bundled: bool = False) -> dict[str, Any]:
    """Refresh liveness, and optionally what this session is doing.

    Always updates `heartbeat_at`. That is the entire contract — an agent that
    calls this is saying "I am still here", and everything else is optional.
    """
    path = _state_path(root, "sessions.yaml")
    data = _read(path, "sessions", force_bundled=force_bundled)
    entry = _find(data["sessions"], session_id)
    if entry is None:
        raise TeamStoreError(
            f"no session '{session_id}'. Start one with `aef.py session start` — a "
            "heartbeat cannot create a session, or a typo would silently mint one."
        )
    if entry.get("status") == "ended":
        raise TeamStoreError(
            f"session '{session_id}' has ended. Ended is terminal; start a new session."
        )

    if status is not None:
        if status not in team_mod.SESSION_STATUSES:
            raise TeamStoreError(
                f"unknown status '{status}'. One of: {', '.join(team_mod.SESSION_STATUSES)}. "
                "'stale' is not settable — it is derived from heartbeat age."
            )
        entry["status"] = status
    if task is not None:
        entry["task"] = task or None
    if activity is not None:
        entry["activity"] = activity
    if blocked_reason is not None:
        entry["blocked_reason"] = blocked_reason

    if entry.get("status") == "blocked" and not entry.get("blocked_reason"):
        raise TeamStoreError(
            "a blocked session must record why. Pass --reason: nobody can unblock "
            "what nobody can see."
        )

    entry["heartbeat_at"] = _now()
    writer.dump(path, data, SESSIONS_HEADER)
    return entry


def end_session(root: str, session_id: str, *, outcome: str = "completed",
                changed: str | None = None, evidence: list[str] | None = None,
                remaining: str | None = None, risks: str | None = None,
                next_step: str | None = None, references: list[str] | None = None,
                force_bundled: bool = False) -> dict[str, Any]:
    """End a session and record its handoff.

    The handoff is what the next agent reads instead of this one's transcript.
    It is compact by contract — references, not output.
    """
    valid = ("completed", "paused", "failed", "superseded")
    if outcome not in valid:
        raise TeamStoreError(f"unknown outcome '{outcome}'. One of: {', '.join(valid)}")

    path = _state_path(root, "sessions.yaml")
    data = _read(path, "sessions", force_bundled=force_bundled)
    entry = _find(data["sessions"], session_id)
    if entry is None:
        raise TeamStoreError(f"no session '{session_id}'")

    entry["status"] = "ended"
    entry["main_engineer"] = False   # the post is released, never inherited
    entry["heartbeat_at"] = _now()
    handoff: dict[str, Any] = {"ended_at": _now(), "outcome": outcome}
    for key, value in (
        ("changed", changed), ("remaining", remaining),
        ("risks", risks), ("next", next_step),
    ):
        if value:
            handoff[key] = value
    if evidence:
        handoff["evidence"] = list(evidence)
    if references:
        handoff["references"] = list(references)
    entry["handoff"] = handoff

    writer.dump(path, data, SESSIONS_HEADER)
    return entry


def claim_main_engineer(root: str, session_id: str, *,
                        force_bundled: bool = False) -> dict[str, Any]:
    path = _state_path(root, "sessions.yaml")
    data = _read(path, "sessions", force_bundled=force_bundled)
    entry = _find(data["sessions"], session_id)
    if entry is None:
        raise TeamStoreError(f"no session '{session_id}'")
    if entry.get("status") == "ended":
        raise TeamStoreError(f"session '{session_id}' has ended and cannot hold the post")

    _refuse_second_main_engineer(root, data["sessions"], force_bundled, exclude=session_id)
    entry["main_engineer"] = True
    entry["heartbeat_at"] = _now()
    writer.dump(path, data, SESSIONS_HEADER)
    return entry


def _refuse_second_main_engineer(root: str, entries: list[dict[str, Any]],
                                 force_bundled: bool, exclude: str | None = None) -> None:
    """One holder at a time.

    A STALE holder is not an obstacle — that is the handover path. If the
    previous Main Engineer's process died, a new session claims the post and the
    project keeps its coordinator. A LIVE holder is refused, because two
    coordinators assigning work independently is the failure this post exists to
    prevent.
    """
    live = team_mod.Team(
        [team_mod.Team._session(e) for e in entries],   # noqa: SLF001 - same package
        [],
        stale_minutes=team_mod._stale_minutes(root, force_bundled),  # noqa: SLF001
    )
    holder = live.main_engineer()
    if holder is not None and holder.id != exclude:
        raise TeamStoreError(
            f"session '{holder.id}' already holds main_engineer and is live "
            f"(last heartbeat {holder.heartbeat_age_minutes:g} min ago).\n"
            "The post is single-holder. Wait for it to end, or let its heartbeat go "
            "stale — a stale holder can be replaced, a live one cannot."
        )


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------

_REC_ID = re.compile(r"^R-(\d+)$")


def next_recommendation_id(root: str, *, force_bundled: bool = False) -> str:
    path = _state_path(root, "recommendations.yaml")
    data = _read(path, "recommendations", force_bundled=force_bundled)
    highest = 0
    for entry in data["recommendations"]:
        match = _REC_ID.match(str(entry.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"R-{highest + 1:03d}"


def add_recommendation(root: str, title: str, *, recommendation: str,
                       reason: str, raised_by: str | None = None,
                       raised_by_agent: str | None = None,
                       during_task: str | None = None,
                       expected_benefit: str | None = None,
                       risk: str | None = None,
                       affected: list[str] | None = None,
                       severity: str = "important",
                       force_bundled: bool = False) -> dict[str, Any]:
    _require_state_dir(root)
    if severity not in team_mod.REC_SEVERITIES:
        raise TeamStoreError(
            f"unknown severity '{severity}'. One of: {', '.join(team_mod.REC_SEVERITIES)}"
        )
    if not str(reason).strip():
        raise TeamStoreError(
            "a recommendation needs a reason. An unexplained proposal cannot be "
            "judged later, and will be re-proposed by the next agent."
        )

    path = _state_path(root, "recommendations.yaml")
    data = _read(path, "recommendations", force_bundled=force_bundled)
    entry: dict[str, Any] = {
        "id": next_recommendation_id(root, force_bundled=force_bundled),
        "title": title,
        "raised_by": raised_by,
        "raised_by_agent": raised_by_agent,
        "during_task": during_task,
        "raised_at": _now(),
        "recommendation": recommendation,
        "reason": reason,
        "expected_benefit": expected_benefit,
        "risk": risk,
        "affected_components": list(affected or []),
        "severity": severity,
        "status": "pending",
    }
    data["recommendations"].append(entry)
    writer.dump(path, data, RECOMMENDATIONS_HEADER)
    return entry


def resolve_recommendation(root: str, rec_id: str, status: str, *,
                           reason: str | None = None, decided_by: str | None = None,
                           became_task: str | None = None,
                           became_decision: str | None = None,
                           merged_into: str | None = None,
                           force_bundled: bool = False) -> dict[str, Any]:
    if status not in ("accepted", "rejected", "deferred", "merged"):
        raise TeamStoreError(
            f"unknown resolution '{status}'. One of: accepted, rejected, deferred, merged"
        )

    path = _state_path(root, "recommendations.yaml")
    data = _read(path, "recommendations", force_bundled=force_bundled)
    entry = None
    for candidate in data["recommendations"]:
        if str(candidate.get("id")) == rec_id:
            entry = candidate
            break
    if entry is None:
        raise TeamStoreError(f"no recommendation '{rec_id}'")

    # The two rules that make this file organisational memory rather than a
    # to-do list. Both are enforced here rather than reported later, because a
    # rejection without a reason is unrecoverable once the session that made it
    # is gone.
    if status == "rejected" and not (reason or "").strip():
        raise TeamStoreError(
            "rejecting a recommendation requires a reason (--reason).\n"
            "Without one, the next agent proposes the same thing and nobody can "
            "tell them it was already considered."
        )
    if status == "accepted" and not (became_task or became_decision):
        raise TeamStoreError(
            "accepting a recommendation requires --task or --decision.\n"
            "Acceptance that produces neither work nor a recorded decision is "
            "agreement, and agreement does nothing."
        )
    if status == "merged" and not merged_into:
        raise TeamStoreError("merging requires --into <recommendation id>")

    entry["status"] = status
    resolution: dict[str, Any] = {"decided_by": decided_by, "decided_at": _now()}
    for key, value in (
        ("reason", reason), ("became_task", became_task),
        ("became_decision", became_decision), ("merged_into", merged_into),
    ):
        if value:
            resolution[key] = value
    entry["resolution"] = resolution
    writer.dump(path, data, RECOMMENDATIONS_HEADER)
    return entry
