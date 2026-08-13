#!/usr/bin/env python3
"""AEF command line — the plan, its progress, and who is assigned to what.

    python aef/tools/aef.py dashboard        open the tree + progress views
    python aef/tools/aef.py progress         one-screen text summary
    python aef/tools/aef.py tree             the plan as an ASCII tree
    python aef/tools/aef.py validate         exit 1 if the plan and tasks disagree
    python aef/tools/aef.py assign ...       assign an agent, automatically or by hand
    python aef/tools/aef.py doctor           what this tool can see and read

Stdlib only. No install step. Works from a project root that contains aef/.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aefkit import (  # noqa: E402
    AEF_TOOLS_VERSION,
    assign as assign_mod,
    model,
    render,
    server,
    team as team_mod,
    teamstore as store,
    yamlio,
)

_GLYPH = {
    "complete": "[x]",
    "in_progress": "[~]",
    "pending": "[ ]",
    "blocked": "[!]",
    "failed": "[X]",
    "waiting_dependency": "[.]",
}


def _load(args) -> model.Plan:
    try:
        return model.Plan.load(args.root, force_bundled=args.force_bundled)
    except model.PlanError as exc:
        sys.stderr.write(f"error: {exc}\n")
        raise SystemExit(2)


def _plan_path(args) -> str:
    return os.path.join(args.root, ".ai", "state", "plan.yaml")


# ---------------------------------------------------------------------------

def cmd_dashboard(args) -> int:
    server.serve(args.root, args.host, args.port)
    return 0


def cmd_tree(args) -> int:
    plan = _load(args)

    def walk(node: model.Node, prefix: str = "", last: bool = True, top: bool = True) -> None:
        if top:
            progress = plan.progress(node)
            print(f"{node.title}  —  {progress.percent}% ({progress.counts.get('complete', 0)}/{progress.leaf_count})")
        else:
            branch = "`-- " if last else "|-- "
            agent = f"  -> {node.agent}" if node.agent else ""
            if node.is_leaf:
                tid = f" ({node.task_id})" if node.task_id else ""
                print(f"{prefix}{branch}{_GLYPH[node.status]} {node.title}{tid}{agent}")
            else:
                progress = plan.progress(node)
                print(f"{prefix}{branch}{_GLYPH[node.status]} {node.title}  "
                      f"[{progress.percent}% {progress.counts.get('complete', 0)}/{progress.leaf_count}]{agent}")
        children = node.children
        for index, child in enumerate(children):
            is_last = index == len(children) - 1
            extension = "" if top else ("    " if last else "|   ")
            walk(child, prefix + extension, is_last, False)

    walk(plan.root)
    if plan.problems:
        print(f"\n{len(plan.problems)} plan problem(s) — run `validate`.", file=sys.stderr)
    return 0


def cmd_progress(args) -> int:
    plan = _load(args)
    progress = plan.progress()
    counts = progress.counts

    print(f"{plan.root.title}")
    print(f"Project Progress: {progress.percent}%")
    width = 34
    filled = int(round(width * progress.percent / 100.0))
    print(f"  [{'#' * filled}{'-' * (width - filled)}]")
    print()
    for status in model.STATUSES:
        value = counts.get(status, 0)
        if value:
            print(f"  {value:>4}  {model.STATUS_LABELS[status].lower()}")
    print(f"  {'-' * 4}")
    print(f"  {progress.leaf_count:>4}  tasks in the plan")

    def section(title: str, nodes: list[model.Node], reason: bool = False,
                holder: bool = False) -> None:
        print(f"\n{title}")
        if not nodes:
            print("  (none)")
            return
        for node in nodes:
            agent = f"  -> {node.agent}" if node.agent else "  -> unassigned"
            where = " / ".join(node.path()[1:-1])
            line = f"  {_GLYPH[node.status]} {node.title}"
            if node.task_id:
                line += f" ({node.task_id})"
            print(line + agent)
            if where:
                print(f"        in: {where}")
            # WHO, not just what. The assigned agent is a plan-time intention;
            # the lock owner is the session actually holding the files, and on a
            # project with concurrent agents they are not always the same.
            if holder and node.session is not None:
                age = node.session.heartbeat_age_minutes
                print(f"        held by: {node.session.agent} "
                      f"(session {node.session.id}"
                      f"{f', heartbeat {age:g} min ago' if age is not None else ''})")
                if node.session.activity:
                    print(f"        doing  : {node.session.activity}")
            elif holder and node.lock is not None:
                print(f"        held by: {node.lock.agent}"
                      f"{f' until {node.lock.expires_at}' if node.lock.expires_at else ''}")
            elif holder and node.task and node.task.get("claimed_by"):
                print(f"        claimed by: {node.task['claimed_by']}")
            if reason and node.task and node.task.get("blocked_reason"):
                print(f"        why: {' '.join(str(node.task['blocked_reason']).split())}")

    section("Being worked on now:", plan.current(), holder=True)
    section("Coming next:", plan.upcoming(8))
    section("Needs attention:", plan.attention(), reason=True)

    agents = plan.agents()
    if agents:
        print("\nBy agent:")
        for name, stats in agents.items():
            done = stats["counts"].get("complete", 0)
            print(f"  {name:<18} {done}/{stats['total']} complete")

    if plan.problems:
        print(f"\n{len(plan.problems)} plan problem(s) — run `validate`.", file=sys.stderr)
        return 1
    return 0


def cmd_validate(args) -> int:
    plan = _load(args)

    def notices() -> None:
        # Printed whether or not the structural check passed, and never fatal.
        # A coordination notice describes this moment — somebody is mid-task —
        # and gating the plan on it would mean a plan cannot be validated while
        # anyone is working on the project.
        if not plan.notices:
            return
        print(f"\n{len(plan.notices)} coordination notice(s) — not a plan failure:",
              file=sys.stderr)
        for notice in plan.notices:
            print(f"  - {notice}", file=sys.stderr)

    if not plan.problems:
        progress = plan.progress()
        print(f"plan OK — {progress.leaf_count} leaves, {len(plan.tasks)} tasks, all accounted for")
        notices()
        return 0
    print(f"{len(plan.problems)} problem(s):", file=sys.stderr)
    for problem in plan.problems:
        print(f"  - {problem}", file=sys.stderr)
    notices()
    return 1


def cmd_assign(args) -> int:
    plan = _load(args)
    catalogue = assign_mod.load_catalogue(args.root, force_bundled=args.force_bundled)
    path = _plan_path(args)

    if args.list:
        print("Agents in the catalogue:\n")
        for name, spec in catalogue.agents.items():
            does = " ".join(str(spec.get("does") or "").split())
            print(f"  {name:<16} role={spec.get('role', '?'):<26} tier={spec.get('tier', '?')}")
            if does:
                print(f"  {'':<16} {does[:96]}")
        return 0

    if args.auto:
        changed = 0
        skipped = 0
        for node in plan.root.leaves():
            if node.agent_source == "manual":
                skipped += 1
                continue
            if node.agent and not args.overwrite:
                continue
            suggestion = assign_mod.suggest(node.task, node.title, catalogue)
            if not suggestion.agent or suggestion.agent == node.agent:
                continue
            if args.dry_run:
                print(f"would assign {node.id:<10} -> {suggestion.agent:<16} ({suggestion.reason})")
            else:
                assign_mod.set_agent(path, node.id, suggestion.agent, locked=False, catalogue=catalogue)
                print(f"{node.id:<10} -> {suggestion.agent:<16} ({suggestion.reason})")
            changed += 1
        verb = "would change" if args.dry_run else "assigned"
        print(f"\n{verb} {changed} node(s); {skipped} left alone because they were assigned by hand")
        return 0

    if not args.node:
        sys.stderr.write("error: give --node NODE_ID with --agent NAME, or use --auto / --list\n")
        return 2

    try:
        if args.clear:
            print(assign_mod.set_agent(path, args.node, None, catalogue=catalogue))
        else:
            if not args.agent:
                sys.stderr.write("error: --agent is required unless --clear is given\n")
                return 2
            print(assign_mod.set_agent(path, args.node, args.agent, locked=True, catalogue=catalogue))
    except assign_mod.AssignError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    return 0


def cmd_doctor(args) -> int:
    root = os.path.abspath(args.root)
    print(f"aef tools    {AEF_TOOLS_VERSION}")
    print(f"python       {sys.version.split()[0]}")
    print(f"yaml reader  {yamlio.reader_name()}")
    print(f"project root {root}")
    version_path = os.path.join(root, "aef", "VERSION")
    if os.path.exists(version_path):
        with open(version_path, encoding="utf-8") as handle:
            pinned = handle.read().strip()
        print(f"aef/VERSION  {pinned}" + ("" if pinned == AEF_TOOLS_VERSION else "   <-- MISMATCH with tools"))
    # locks.yaml is optional — a project nobody edits concurrently has none —
    # so its absence is reported as such rather than as MISSING.
    for relative in (".ai/state/plan.yaml", ".ai/state/tasks.yaml", "aef/config/agents.yaml"):
        full = os.path.join(root, relative)
        print(f"{'found  ' if os.path.exists(full) else 'MISSING'}      {relative}")
    for relative, note in (
        (".ai/state/locks.yaml", "no concurrent work recorded"),
        (".ai/state/sessions.yaml", "no agent has announced itself"),
        (".ai/state/recommendations.yaml", "nothing proposed yet"),
    ):
        if os.path.exists(os.path.join(root, relative)):
            print(f"found        {relative}")
        else:
            print(f"absent       {relative}   (optional; {note})")

    # Prove the bundled reader on this project's own files, both ways when
    # PyYAML is present. Claiming the fallback works without running it would be
    # exactly the unverified claim the constitution forbids.
    if yamlio.USING_PYYAML:
        import yaml as pyyaml
        for relative in (".ai/state/plan.yaml", ".ai/state/tasks.yaml", ".ai/state/locks.yaml",
                         ".ai/state/sessions.yaml", ".ai/state/recommendations.yaml"):
            full = os.path.join(root, relative)
            if not os.path.exists(full):
                continue
            try:
                mine = yamlio.load(full, force_bundled=True)
                theirs = pyyaml.safe_load(open(full, encoding="utf-8"))
                verdict = "agree" if mine == theirs else "DISAGREE"
            except Exception as exc:  # noqa: BLE001 - report, do not crash doctor
                verdict = f"bundled reader failed: {type(exc).__name__}: {exc}"
            print(f"readers {verdict:<9} on {relative}")
    return 0


# ---------------------------------------------------------------------------
# team: sessions, recommendations, briefing  (0.4.0)
# ---------------------------------------------------------------------------

def _team(args):
    return team_mod.Team.load(args.root, force_bundled=args.force_bundled)


def _known_agents(args) -> set[str] | None:
    try:
        return set(assign_mod.load_catalogue(args.root, force_bundled=args.force_bundled).agents)
    except assign_mod.AssignError:
        return None


def _tasks(args) -> dict[str, dict]:
    try:
        return model.Plan.load(args.root, force_bundled=args.force_bundled).tasks
    except (model.PlanError, OSError, ValueError):
        return {}


def cmd_session(args) -> int:
    action = args.action
    try:
        if action == "start":
            entry = store.start_session(
                args.root, args.id, args.agent, role=args.role, vendor=args.vendor,
                model=args.model_name, task=args.task, activity=args.activity,
                main_engineer=args.main_engineer, known_agents=_known_agents(args),
                force_bundled=args.force_bundled,
            )
            print(f"session {entry['id']} started as {entry['agent']}"
                  + (f" on {entry['task']}" if entry.get("task") else " (idle)")
                  + (" — MAIN ENGINEER" if entry.get("main_engineer") else ""))
        elif action == "heartbeat":
            entry = store.heartbeat(
                args.root, args.id, activity=args.activity, status=args.status,
                task=args.task, blocked_reason=args.reason,
                force_bundled=args.force_bundled,
            )
            print(f"{entry['id']}: {entry.get('status')} @ {entry['heartbeat_at']}")
        elif action == "end":
            entry = store.end_session(
                args.root, args.id, outcome=args.outcome, changed=args.changed,
                evidence=args.evidence, remaining=args.remaining, risks=args.risks,
                next_step=args.next, references=args.reference,
                force_bundled=args.force_bundled,
            )
            print(f"session {entry['id']} ended ({entry['handoff']['outcome']}); handoff recorded")
        elif action == "claim-main-engineer":
            entry = store.claim_main_engineer(args.root, args.id,
                                              force_bundled=args.force_bundled)
            print(f"{entry['id']} now holds the main_engineer post")
        else:  # list
            return _print_team(_team(args), _tasks(args), _known_agents(args))
    except store.TeamStoreError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


def _print_team(team, tasks, known_agents) -> int:
    me = team.main_engineer()
    print(f"Main Engineer: {me.id + ' (' + (me.agent or '?') + ')' if me else 'VACANT'}")
    live = team.live()
    print(f"\nLive sessions ({len(live)}):")
    if not live:
        print("  (none)")
    for session in live:
        line = f"  [{session.display_status:8}] {session.id:28} {session.agent or '?'}"
        if session.task:
            line += f"  -> {session.task}"
        print(line)
        if session.activity:
            print(f"        {session.activity}")
        if session.blocked_reason:
            print(f"        blocked: {session.blocked_reason}")
    stale = team.stale()
    if stale:
        print(f"\nStale ({len(stale)}):")
        for session in stale:
            age = (f"{session.heartbeat_age_minutes:g} min ago"
                   if session.heartbeat_age_minutes is not None else "never")
            print(f"  [stale   ] {session.id:28} {session.agent or '?'}  last beat {age}")

    rec = team.open_recommendations()
    print(f"\nOpen recommendations ({len(rec)}):")
    if not rec:
        print("  (none)")
    for item in rec:
        print(f"  {item.id}  [{item.severity:9}] {item.title}")

    notices = team.notices(tasks, known_agents)
    if notices:
        print(f"\n{len(notices)} coordination notice(s):", file=sys.stderr)
        for notice in notices:
            print(f"  - {notice}", file=sys.stderr)
    return 0


def cmd_recommend(args) -> int:
    try:
        if args.action == "add":
            entry = store.add_recommendation(
                args.root, args.title, recommendation=args.what, reason=args.reason,
                raised_by=args.session, raised_by_agent=args.agent,
                during_task=args.task, expected_benefit=args.benefit, risk=args.risk,
                affected=args.affects, severity=args.severity,
                force_bundled=args.force_bundled,
            )
            print(f"{entry['id']} recorded ({entry['severity']}), status pending")
            print("Recording is not permission. It is assigned through the normal "
                  "workflow if accepted.")
        elif args.action == "list":
            team = _team(args)
            items = team.recommendations if args.all else team.open_recommendations()
            if not items:
                print("no recommendations" if args.all else "no open recommendations")
                return 0
            for item in items:
                print(f"{item.id}  [{item.status:8}] [{item.severity:9}] {item.title}")
                if item.recommendation:
                    print(f"      what: {item.recommendation}")
                if item.reason:
                    print(f"      why : {item.reason}")
                resolution = item.resolution or {}
                if resolution.get("reason"):
                    print(f"      {item.status}: {' '.join(str(resolution['reason']).split())}")
                for key, label in (("became_task", "task"), ("became_decision", "decision"),
                                   ("merged_into", "merged into")):
                    if resolution.get(key):
                        print(f"      -> {label} {resolution[key]}")
        else:
            # The command is a verb the operator types; the stored value is the
            # state it lands in. Keeping them textually different is deliberate —
            # `status: accept` would read as an instruction rather than a fact.
            status = {"accept": "accepted", "reject": "rejected",
                      "defer": "deferred", "merge": "merged"}[args.action]
            entry = store.resolve_recommendation(
                args.root, args.id, status, reason=args.reason,
                decided_by=args.by, became_task=args.task,
                became_decision=args.decision, merged_into=args.into,
                force_bundled=args.force_bundled,
            )
            print(f"{entry['id']} -> {entry['status']}")
    except store.TeamStoreError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_brief(args) -> int:
    """The smallest sufficient context for one agent or one task.

    This exists so a new session does not re-read the repository to reconstruct
    what the last one already wrote down. Levels 1-4 are printed; level 5
    (decisions, memory, evidence) is listed BY REFERENCE so it costs an id
    rather than a document.
    """
    try:
        plan = model.Plan.load(args.root, force_bundled=args.force_bundled)
    except (model.PlanError, OSError, ValueError) as exc:
        print(f"no readable plan: {exc}", file=sys.stderr)
        return 1
    team = _team(args)
    data = plan.as_dict()
    meta = data.get("meta") or {}

    print("=" * 62)
    print(f"PROJECT   {meta.get('project') or plan.root.title}")
    print(f"PLAN      {data['progress']['percent']}% complete, "
          f"{data['progress']['leaf_count']} tasks")
    me = team.main_engineer()
    print(f"COORD     Main Engineer: {me.id if me else 'VACANT'}")
    print("RULES     aef/core/CONSTITUTION.md — claim a lock before editing; never")
    print("          report a check PASSED you did not run; register fabrications.")
    print("=" * 62)

    leaves = list(plan.root.leaves())
    if args.task:
        leaves = [leaf for leaf in leaves if leaf.task_id == args.task]
        if not leaves:
            print(f"\nno plan leaf links task {args.task}", file=sys.stderr)
            return 1
    elif args.agent:
        leaves = [leaf for leaf in leaves if leaf.agent == args.agent]
        print(f"\nYOU ARE   {args.agent}")

    mine = [leaf for leaf in leaves if leaf.status in ("pending", "in_progress")]
    print(f"\nYOUR WORK ({len(mine)} open of {len(leaves)} assigned)")
    for leaf in mine:
        print(f"  [{leaf.status:18}] {leaf.task_id or leaf.id}  {leaf.title}")
        task = leaf.task or {}
        if task.get("owned_paths"):
            print(f"        owns  : {', '.join(str(p) for p in task['owned_paths'])}")
        if leaf.depends_on:
            print(f"        needs : {', '.join(leaf.depends_on)}")
        criteria = task.get("acceptance_criteria") or []
        if criteria:
            print(f"        criteria: {len(criteria)}  "
                  f"(aef.py brief --task {leaf.task_id} for the full contract)")
        if task.get("required_dimensions"):
            print(f"        must cover: {', '.join(str(d) for d in task['required_dimensions'])}")

    if args.task and leaves:
        task = leaves[0].task or {}
        print("\nACCEPTANCE CRITERIA")
        for criterion in task.get("acceptance_criteria") or []:
            if isinstance(criterion, dict):
                print(f"  [{str(criterion.get('result') or 'NOT_RUN'):9}] "
                      f"{criterion.get('id')}: {criterion.get('statement')}")
        commands = [v for v in (task.get("verification") or []) if isinstance(v, dict)]
        if commands:
            print("\nVERIFICATION COMMANDS")
            for command in commands:
                print(f"  {command.get('command')}")

    print("\nDO NOT TOUCH (owned by another live session)")
    hands_off = [s for s in team.live() if s.task and (not args.agent or s.agent != args.agent)]
    if not hands_off:
        print("  (nothing claimed by anyone else)")
    for session in hands_off:
        print(f"  {session.task:10} {session.agent or '?':16} ({session.id})")

    touching = []
    for leaf in mine:
        for path in (leaf.task or {}).get("owned_paths") or []:
            touching.extend(team.recommendations_touching(str(path)))
    if touching:
        seen = set()
        print("\nOPEN PROPOSALS ON YOUR COMPONENTS")
        for rec in touching:
            if rec.id in seen:
                continue
            seen.add(rec.id)
            print(f"  {rec.id} [{rec.severity}] {rec.title}")

    print("\nBY REFERENCE (fetch only if you need it)")
    print(f"  decisions      .ai/state/decisions/   ({_count_dir(args.root, 'decisions')} records)")
    print("  memory         .ai/memory/index.md")
    print("  fabrications   .ai/state/fabrications.yaml")
    print("  full plan      aef/tools/aef.py tree")
    return 0


def _count_dir(root: str, name: str) -> int:
    path = os.path.join(root, ".ai", "state", name)
    if not os.path.isdir(path):
        return 0
    return len([f for f in os.listdir(path) if f.endswith((".yaml", ".yml", ".md"))])


# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aef", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=".", help="project root containing aef/ and .ai/ (default: .)")
    parser.add_argument("--force-bundled", action="store_true",
                        help="ignore PyYAML and use the bundled reader (for testing the fallback)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dashboard = subparsers.add_parser("dashboard", help="serve the tree and progress views")
    dashboard.add_argument("--port", type=int, default=7423)
    dashboard.add_argument("--host", default="127.0.0.1",
                           help="default 127.0.0.1; a plan is internal, bind wider only on purpose")
    dashboard.set_defaults(func=cmd_dashboard)

    subparsers.add_parser("tree", help="print the plan as a tree").set_defaults(func=cmd_tree)
    subparsers.add_parser("progress", help="print the progress summary").set_defaults(func=cmd_progress)
    subparsers.add_parser("validate", help="check the plan against tasks.yaml").set_defaults(func=cmd_validate)
    subparsers.add_parser("doctor", help="report what the tool can see").set_defaults(func=cmd_doctor)

    assign_parser = subparsers.add_parser("assign", help="assign agents to plan nodes")
    assign_parser.add_argument("--node", help="plan node id, e.g. N-014")
    assign_parser.add_argument("--agent", help="agent id from the catalogue")
    assign_parser.add_argument("--clear", action="store_true", help="remove the assignment")
    assign_parser.add_argument("--auto", action="store_true", help="assign every unassigned leaf automatically")
    assign_parser.add_argument("--overwrite", action="store_true",
                               help="with --auto, also replace automatic assignments (never manual ones)")
    assign_parser.add_argument("--dry-run", action="store_true", help="with --auto, show changes without writing")
    assign_parser.add_argument("--list", action="store_true", help="list the agent catalogue")
    assign_parser.set_defaults(func=cmd_assign)

    # -- session: liveness and handoff (0.4.0) --------------------------------
    session = subparsers.add_parser("session", help="declare and refresh this agent's presence")
    session_sub = session.add_subparsers(dest="action", required=True)

    start = session_sub.add_parser("start", help="register this session")
    start.add_argument("--id", required=True, help="unique session id; identifies a PROCESS")
    start.add_argument("--agent", required=True, help="catalogue id from config/agents.yaml")
    start.add_argument("--role", help="one of the seven; defaults to the agent's role")
    start.add_argument("--vendor", help="descriptive only; nothing branches on it")
    start.add_argument("--model-name", dest="model_name", help="descriptive only")
    start.add_argument("--task", help="task id being worked")
    start.add_argument("--activity", help="one line, present tense")
    start.add_argument("--main-engineer", action="store_true",
                       help="claim the coordination post; refused if a live session holds it")

    beat = session_sub.add_parser("heartbeat", help="refresh liveness; optionally update activity")
    beat.add_argument("--id", required=True)
    beat.add_argument("--activity")
    beat.add_argument("--status", choices=list(team_mod.SESSION_STATUSES),
                      help="'stale' is not settable — it is derived from heartbeat age")
    beat.add_argument("--task")
    beat.add_argument("--reason", help="required when --status blocked")

    end = session_sub.add_parser("end", help="end the session and record its handoff")
    end.add_argument("--id", required=True)
    end.add_argument("--outcome", default="completed",
                     choices=["completed", "paused", "failed", "superseded"])
    end.add_argument("--changed")
    end.add_argument("--evidence", action="append", help="reference, not output; repeatable")
    end.add_argument("--remaining")
    end.add_argument("--risks")
    end.add_argument("--next")
    end.add_argument("--reference", action="append", help="task/decision ids; repeatable")

    claim = session_sub.add_parser("claim-main-engineer", help="take the coordination post")
    claim.add_argument("--id", required=True)

    session_sub.add_parser("list", help="who is here, and what is open")
    session.set_defaults(func=cmd_session)

    subparsers.add_parser("team", help="alias for `session list`").set_defaults(
        func=lambda a: cmd_session(_with(a, action="list")))

    # -- recommend: propose without acting (0.4.0) ----------------------------
    recommend = subparsers.add_parser(
        "recommend", help="record a proposal without expanding your task's scope")
    rec_sub = recommend.add_subparsers(dest="action", required=True)

    add = rec_sub.add_parser("add", help="record a finding you are NOT authorised to act on")
    add.add_argument("--title", required=True)
    add.add_argument("--what", required=True, help="the proposal, specific enough to act on")
    add.add_argument("--reason", required=True, help="the evidence, not the intuition")
    add.add_argument("--benefit")
    add.add_argument("--risk", help="what could go wrong if this IS done")
    add.add_argument("--affects", action="append", help="component or path; repeatable")
    add.add_argument("--severity", default="important", choices=list(team_mod.REC_SEVERITIES))
    add.add_argument("--session", help="the session recording it")
    add.add_argument("--agent", help="catalogue id, so the source outlives the session")
    add.add_argument("--task", help="what you were doing when you noticed")

    listing = rec_sub.add_parser("list", help="open proposals, or --all including resolved")
    listing.add_argument("--all", action="store_true")

    for verb, helptext in (("accept", "accept, and name the task or decision it becomes"),
                           ("reject", "reject, with the reason that stops it recurring"),
                           ("defer", "defer, with a revisit trigger"),
                           ("merge", "merge into another recommendation")):
        resolver = rec_sub.add_parser(verb, help=helptext)
        resolver.add_argument("--id", required=True)
        resolver.add_argument("--reason")
        resolver.add_argument("--by", help="deciding session, or 'operator'")
        resolver.add_argument("--task", help="the task it became")
        resolver.add_argument("--decision", help="the DR it became")
        resolver.add_argument("--into", help="target recommendation id, for merge")
    recommend.set_defaults(func=cmd_recommend)

    # -- brief: the smallest sufficient context (0.4.0) -----------------------
    brief = subparsers.add_parser(
        "brief", help="what a joining session needs, and nothing more")
    brief.add_argument("--agent", help="brief for this catalogue agent")
    brief.add_argument("--task", help="the full contract for one task")
    brief.set_defaults(func=cmd_brief)

    args = parser.parse_args(argv)
    return args.func(args)


def _with(args, **overrides):
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


if __name__ == "__main__":
    raise SystemExit(main())
