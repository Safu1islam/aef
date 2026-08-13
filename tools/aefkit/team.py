"""Live team state: who is here now, and what they have proposed.

Two files, two facts, both new in 0.4.0:

    .ai/state/sessions.yaml         liveness  — which agent processes exist
    .ai/state/recommendations.yaml  proposals — work nobody has authorised yet

Neither duplicates anything. `plan.yaml` still owns structure, `tasks.yaml`
still owns status, `locks.yaml` still owns path ownership. This module owns the
question none of them could answer: *is anyone actually there right now?*

Why not reuse locks.yaml, which 0.3.0 pressed into this job: a lock answers "may
I write this path" and its TTL is work-sized — 90 minutes by default. A heartbeat
answers "am I still alive" and is minutes. Deriving liveness from a lock meant a
crashed agent looked busy for up to an hour and a half. Both signals are real and
they are not the same signal.

Everything reported here is derived on read. `stale` in particular is never
written: a process that crashes cannot record its own death, so a file that could
store its own staleness is the one file guaranteed not to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import yamlio
from .paths import framework_file

__all__ = [
    "Session",
    "Recommendation",
    "Team",
    "SESSION_STATUSES",
    "REC_STATUSES",
    "REC_SEVERITIES",
    "DEFAULT_STALE_MINUTES",
]

# Written statuses. `stale` is derived and deliberately absent.
SESSION_STATUSES = ("working", "idle", "blocked", "ended")

# Display vocabulary, which adds the two derived states.
SESSION_DISPLAY = ("working", "idle", "blocked", "stale", "ended")

SESSION_LABELS = {
    "working": "Working",
    "idle": "Idle",
    "blocked": "Blocked",
    "stale": "Stale",
    "ended": "Ended",
}

REC_STATUSES = ("pending", "accepted", "rejected", "deferred", "merged")
REC_SEVERITIES = ("critical", "important", "minor")

# How long a heartbeat stays fresh. Overridable per project in
# .ai/config/overrides.yaml under execution.heartbeat_stale_minutes.
DEFAULT_STALE_MINUTES = 15


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _one_line(value: Any) -> str | None:
    return " ".join(str(value).split()) if value else None


@dataclass
class Session:
    """One running agent process."""

    id: str
    agent: str | None = None
    role: str | None = None
    vendor: str | None = None
    model: str | None = None
    main_engineer: bool = False
    task: str | None = None
    activity: str | None = None
    status: str = "idle"
    blocked_reason: str | None = None
    started_at: str | None = None
    heartbeat_at: str | None = None
    handoff: dict[str, Any] | None = None

    # Derived by Team._resolve(); never read from the file.
    display_status: str = "idle"
    heartbeat_age_minutes: float | None = None

    @property
    def ended(self) -> bool:
        return self.status == "ended"

    @property
    def live(self) -> bool:
        """Present and answering. The only definition of 'an agent is here'."""
        return self.display_status in ("working", "idle", "blocked")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "role": self.role,
            "vendor": self.vendor,
            "model": self.model,
            "main_engineer": self.main_engineer,
            "task": self.task,
            "activity": self.activity,
            "status": self.display_status,
            "status_label": SESSION_LABELS.get(self.display_status, self.display_status),
            "written_status": self.status,
            "blocked_reason": self.blocked_reason,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "heartbeat_age_minutes": self.heartbeat_age_minutes,
            "live": self.live,
            "handoff": self.handoff,
        }


@dataclass
class Recommendation:
    """A proposal. Recording one confers no authority to act on it."""

    id: str
    title: str = ""
    raised_by: str | None = None
    raised_by_agent: str | None = None
    during_task: str | None = None
    raised_at: str | None = None
    recommendation: str | None = None
    reason: str | None = None
    expected_benefit: str | None = None
    risk: str | None = None
    affected_components: list[str] = field(default_factory=list)
    severity: str = "important"
    status: str = "pending"
    resolution: dict[str, Any] = field(default_factory=dict)

    @property
    def open(self) -> bool:
        return self.status in ("pending", "deferred")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "raised_by": self.raised_by,
            "raised_by_agent": self.raised_by_agent,
            "during_task": self.during_task,
            "raised_at": self.raised_at,
            "recommendation": _one_line(self.recommendation),
            "reason": _one_line(self.reason),
            "expected_benefit": _one_line(self.expected_benefit),
            "risk": _one_line(self.risk),
            "affected_components": self.affected_components,
            "severity": self.severity,
            "status": self.status,
            "open": self.open,
            "resolution": self.resolution,
        }


class Team:
    """Live sessions and standing recommendations, with their derived state."""

    def __init__(self, sessions: list[Session], recommendations: list[Recommendation],
                 stale_minutes: int = DEFAULT_STALE_MINUTES,
                 now: datetime | None = None):
        self.sessions = sessions
        self.recommendations = recommendations
        self.stale_minutes = stale_minutes
        self.now = now or datetime.now(timezone.utc)
        self._resolve()

    # -- loading ----------------------------------------------------------

    @classmethod
    def load(cls, project_root: str = ".", *, force_bundled: bool = False,
             now: datetime | None = None) -> "Team":
        """Both files are OPTIONAL. A project with neither is a 0.3.0 project and
        loads as an empty team rather than an error — that is the whole of the
        migration story for this feature."""
        state = os.path.join(project_root, ".ai", "state")

        sessions: list[Session] = []
        sessions_path = os.path.join(state, "sessions.yaml")
        if os.path.exists(sessions_path):
            raw = yamlio.load(sessions_path, force_bundled=force_bundled) or {}
            for entry in raw.get("sessions") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    sessions.append(cls._session(entry))

        recommendations: list[Recommendation] = []
        rec_path = os.path.join(state, "recommendations.yaml")
        if os.path.exists(rec_path):
            raw = yamlio.load(rec_path, force_bundled=force_bundled) or {}
            for entry in raw.get("recommendations") or []:
                if isinstance(entry, dict) and entry.get("id"):
                    recommendations.append(cls._recommendation(entry))

        return cls(sessions, recommendations,
                   stale_minutes=_stale_minutes(project_root, force_bundled), now=now)

    @staticmethod
    def _session(entry: dict[str, Any]) -> Session:
        status = str(entry.get("status") or "idle")
        return Session(
            id=str(entry["id"]),
            agent=_opt(entry.get("agent")),
            role=_opt(entry.get("role")),
            vendor=_opt(entry.get("vendor")),
            model=_opt(entry.get("model")),
            main_engineer=bool(entry.get("main_engineer")),
            task=_opt(entry.get("task")),
            activity=_one_line(entry.get("activity")),
            status=status if status in SESSION_STATUSES else "idle",
            blocked_reason=_one_line(entry.get("blocked_reason")),
            started_at=_opt(entry.get("started_at")),
            heartbeat_at=_opt(entry.get("heartbeat_at")),
            handoff=entry.get("handoff") if isinstance(entry.get("handoff"), dict) else None,
        )

    @staticmethod
    def _recommendation(entry: dict[str, Any]) -> Recommendation:
        severity = str(entry.get("severity") or "important")
        status = str(entry.get("status") or "pending")
        components = entry.get("affected_components") or []
        return Recommendation(
            id=str(entry["id"]),
            title=str(entry.get("title") or entry["id"]),
            raised_by=_opt(entry.get("raised_by")),
            raised_by_agent=_opt(entry.get("raised_by_agent")),
            during_task=_opt(entry.get("during_task")),
            raised_at=_opt(entry.get("raised_at")),
            recommendation=_opt(entry.get("recommendation")),
            reason=_opt(entry.get("reason")),
            expected_benefit=_opt(entry.get("expected_benefit")),
            risk=_opt(entry.get("risk")),
            affected_components=[str(c) for c in components] if isinstance(components, list) else [],
            severity=severity if severity in REC_SEVERITIES else "important",
            status=status if status in REC_STATUSES else "pending",
            resolution=entry.get("resolution") if isinstance(entry.get("resolution"), dict) else {},
        )

    # -- derivation -------------------------------------------------------

    def _resolve(self) -> None:
        cutoff = self.now - timedelta(minutes=self.stale_minutes)
        for session in self.sessions:
            if session.status == "ended":
                session.display_status = "ended"
                continue
            beat = _parse_time(session.heartbeat_at) or _parse_time(session.started_at)
            if beat is not None:
                session.heartbeat_age_minutes = round(
                    (self.now - beat).total_seconds() / 60.0, 1
                )
            # No timestamp at all is treated as stale rather than live. A session
            # that never said when it was here has not said it is here — and the
            # opposite default would let a malformed entry occupy an agent slot
            # and the Main Engineer post indefinitely.
            if beat is None or beat < cutoff:
                session.display_status = "stale"
            else:
                session.display_status = session.status

    # -- reporting --------------------------------------------------------

    def live(self) -> list[Session]:
        return [s for s in self.sessions if s.live]

    def working(self) -> list[Session]:
        return [s for s in self.sessions if s.display_status == "working"]

    def stale(self) -> list[Session]:
        return [s for s in self.sessions if s.display_status == "stale"]

    def main_engineer(self) -> Session | None:
        """The session holding the post, if one is live.

        A stale holder does NOT count. The post being visibly vacant is the
        correct report — it prompts a new session to claim it, whereas silently
        inheriting would mean nobody knows who is coordinating.
        """
        for session in self.sessions:
            if session.main_engineer and session.live:
                return session
        return None

    def open_recommendations(self) -> list[Recommendation]:
        order = {"critical": 0, "important": 1, "minor": 2}
        return sorted(
            [r for r in self.recommendations if r.open],
            key=lambda r: (order.get(r.severity, 3), r.id),
        )

    def recommendations_touching(self, path: str) -> list[Recommendation]:
        """Open proposals naming a component, so an agent about to change it can
        see that a proposal is already standing there."""
        needle = path.lower()
        out = []
        for rec in self.open_recommendations():
            for component in rec.affected_components:
                comp = str(component).lower()
                if comp in needle or needle in comp:
                    out.append(rec)
                    break
        return out

    def workload(self) -> dict[str, dict[str, Any]]:
        """Live sessions per agent id. Counts PROCESSES, not tasks."""
        out: dict[str, dict[str, Any]] = {}
        for session in self.sessions:
            if not session.live or not session.agent:
                continue
            bucket = out.setdefault(session.agent, {"live": 0, "tasks": [], "sessions": []})
            bucket["live"] += 1
            bucket["sessions"].append(session.id)
            if session.task:
                bucket["tasks"].append(session.task)
        return dict(sorted(out.items()))

    # -- coordination notices ---------------------------------------------

    def notices(self, tasks: dict[str, dict[str, Any]] | None = None,
                known_agents: set[str] | None = None) -> list[str]:
        """Disagreements worth a human's attention. Reported, never resolved.

        Non-fatal by design, for the same reason 0.3.0's lock notices are: these
        describe THIS MOMENT and resolve themselves when an agent heartbeats or
        ends. Failing a validation gate on one would mean a project cannot be
        validated while anyone is working on it.
        """
        notices: list[str] = []
        tasks = tasks or {}

        seen: dict[str, str] = {}
        for session in self.sessions:
            if session.id in seen:
                notices.append(
                    f"session id {session.id} appears twice; an id identifies one "
                    "process and duplicates make activity unattributable"
                )
            seen[session.id] = session.id

        if known_agents is not None:
            for session in self.sessions:
                if session.agent and session.agent not in known_agents:
                    notices.append(
                        f"session {session.id} runs as '{session.agent}', which is in "
                        "no agent catalogue — add it to overrides.yaml or fix the typo"
                    )

        holders = [s for s in self.sessions if s.main_engineer and not s.ended]
        live_holders = [s for s in holders if s.live]
        if len(live_holders) > 1:
            names = ", ".join(s.id for s in live_holders)
            notices.append(
                f"{len(live_holders)} sessions claim main_engineer ({names}); the post "
                "is single-holder and a split coordinator is worse than none"
            )
        elif holders and not live_holders:
            notices.append(
                f"the main_engineer post is held by {holders[0].id}, whose heartbeat is "
                "stale — the post is effectively VACANT and a live session should claim it"
            )

        for session in self.stale():
            detail = f" on {session.task}" if session.task else ""
            age = (f", last heartbeat {session.heartbeat_age_minutes:g} min ago"
                   if session.heartbeat_age_minutes is not None else ", no heartbeat recorded")
            notices.append(
                f"session {session.id} ({session.agent or 'unknown agent'}){detail} is "
                f"stale{age}. Its locks and claims may be abandoned"
            )

        for session in self.sessions:
            if session.ended or not session.task:
                continue
            task = tasks.get(session.task)
            if task is None:
                notices.append(
                    f"session {session.id} is working {session.task}, which is in no "
                    "task file — work with no acceptance criteria cannot be judged"
                )
            elif session.live and str(task.get("status") or "ready") == "complete":
                notices.append(
                    f"session {session.id} is still working {session.task}, which is "
                    "already 'complete'"
                )
            if session.status == "blocked" and not session.blocked_reason:
                notices.append(
                    f"session {session.id} is blocked with no reason recorded; nobody "
                    "can unblock what nobody can see"
                )

        for rec in self.recommendations:
            if rec.status == "rejected" and not (rec.resolution or {}).get("reason"):
                notices.append(
                    f"{rec.id} was rejected with no reason; the next agent will "
                    "propose it again"
                )
            if rec.status == "accepted" and not (
                (rec.resolution or {}).get("became_task")
                or (rec.resolution or {}).get("became_decision")
            ):
                notices.append(
                    f"{rec.id} was accepted but became neither a task nor a decision — "
                    "acceptance that produces no work is agreement, not acceptance"
                )
        return notices

    def as_dict(self) -> dict[str, Any]:
        me = self.main_engineer()
        return {
            "sessions": [s.as_dict() for s in self.sessions if not s.ended],
            "ended": [s.as_dict() for s in self.sessions if s.ended],
            "main_engineer": me.as_dict() if me else None,
            "counts": {
                "live": len(self.live()),
                "working": len(self.working()),
                "stale": len(self.stale()),
                "open_recommendations": len(self.open_recommendations()),
            },
            "workload": self.workload(),
            "recommendations": [r.as_dict() for r in self.open_recommendations()],
            "resolved_recommendations": [
                r.as_dict() for r in self.recommendations if not r.open
            ],
            "stale_minutes": self.stale_minutes,
        }


def _opt(value: Any) -> str | None:
    return None if value is None else str(value)


def _stale_minutes(project_root: str, force_bundled: bool) -> int:
    """framework.yaml default, with the project's override applied.

    Read defensively: a malformed value must not stop the dashboard rendering,
    so anything unreadable falls back to the constant rather than raising.
    """
    value: Any = None
    for relative in (
        framework_file(project_root, "config", "framework.yaml"),
        os.path.join(project_root, ".ai", "config", "overrides.yaml"),
    ):
        path = relative
        if not os.path.exists(path):
            continue
        try:
            data = yamlio.load(path, force_bundled=force_bundled) or {}
        except Exception:  # noqa: BLE001 - config problems are reported elsewhere
            continue
        execution = data.get("execution")
        if isinstance(execution, dict) and execution.get("heartbeat_stale_minutes") is not None:
            value = execution["heartbeat_stale_minutes"]
    try:
        minutes = int(value)
        return minutes if minutes > 0 else DEFAULT_STALE_MINUTES
    except (TypeError, ValueError):
        return DEFAULT_STALE_MINUTES
