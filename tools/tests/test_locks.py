"""Live work is derived from locks.yaml, not from tasks.yaml alone.

The defect these pin was observed in a real project, not imagined: three tasks
held by a running agent session, all still `status: ready` in tasks.yaml, and
the dashboard announcing "Nothing is claimed right now" while that session was
editing the files. `status: claimed` is written when an agent remembers; a lock
is claimed before the first edit because Constitution rule 3 requires it. The
earlier signal is the truthful one.

Each case here fails if the promotion, the guard rails around it, or the
notice-vs-problem split is removed.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from aefkit.model import Plan

PLAN_HEAD = "meta:\n  project: T\ntree:\n  id: PRJ\n  type: project\n  title: T\n  children:\n"

FUTURE = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
PAST = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

ONE_TASK_PLAN = "    - id: N1\n      title: a\n      task: T-1\n"


def write(directory: str, plan_body: str, tasks_body: str, locks_body: str | None = None) -> str:
    state = os.path.join(directory, ".ai", "state")
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "plan.yaml"), "w", encoding="utf-8") as handle:
        handle.write(PLAN_HEAD + plan_body)
    with open(os.path.join(state, "tasks.yaml"), "w", encoding="utf-8") as handle:
        handle.write(tasks_body)
    if locks_body is not None:
        with open(os.path.join(state, "locks.yaml"), "w", encoding="utf-8") as handle:
            handle.write(locks_body)
    return directory


class Base(unittest.TestCase):
    def build(self, plan_body: str, tasks_body: str, locks_body: str | None = None) -> Plan:
        directory = tempfile.mkdtemp()
        write(directory, plan_body, tasks_body, locks_body)
        return Plan.load(directory)

    def lock(self, task_id: str = "T-1", agent: str = "session-a",
             expires: str | None = None, extra: str = "") -> str:
        expiry = FUTURE if expires is None else expires
        entry = (
            f'locks:\n  - path: "src/thing.py"\n'
            f'    task_id: "{task_id}"\n'
            f'    agent: "{agent}"\n'
        )
        if expiry:
            entry += f'    expires_at: "{expiry}"\n'
        return entry + extra


class Promotion(Base):
    def test_a_live_lock_makes_a_ready_task_read_as_in_progress(self):
        """The whole point. Without this the dashboard reports in-flight work as
        not started, which is the observed defect."""
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n", self.lock())
        leaf = plan.root.children[0]
        self.assertEqual(leaf.status, "in_progress")
        self.assertEqual(leaf.status_source, "lock")
        self.assertEqual([node.id for node in plan.current()], ["N1"])

    def test_a_promoted_task_leaves_coming_next(self):
        """Being worked on and coming next are different answers. A task that
        appears in both is worse than one that appears in neither."""
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n", self.lock())
        self.assertEqual(plan.upcoming(), [])

    def test_the_group_rolls_up_as_in_progress(self):
        plan = self.build(
            "    - id: S\n      type: section\n      title: S\n      children:\n"
            "        - id: N1\n          title: a\n          task: T-1\n"
            "        - id: N2\n          title: b\n          task: T-2\n",
            "tasks:\n  - id: T-1\n    status: ready\n  - id: T-2\n    status: ready\n",
            self.lock(),
        )
        self.assertEqual(plan.root.status, "in_progress")

    def test_a_waiting_dependency_task_is_promoted_too(self):
        """An agent may legitimately start work whose dependency is not formally
        closed. The lock is evidence they did; the tree should say so."""
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n"
            "    - id: N2\n      title: b\n      task: T-2\n",
            "tasks:\n  - id: T-1\n    status: ready\n"
            "  - id: T-2\n    status: ready\n    depends_on: [T-1]\n",
            self.lock(task_id="T-2"),
        )
        statuses = {node.id: node.status for node in plan.root.walk()}
        self.assertEqual(statuses["N2"], "in_progress")

    def test_held_by_is_reported_for_the_progress_view(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n", self.lock())
        current = plan.as_dict()["current"]
        self.assertEqual(current[0]["held_by"], "session-a")
        self.assertEqual(current[0]["lock"]["agent"], "session-a")


class Limits(Base):
    """A lock is evidence of intent to edit, never a finding about the work."""

    def test_a_lock_does_not_resurrect_a_complete_task(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: complete\n", self.lock())
        self.assertEqual(plan.root.children[0].status, "complete")
        self.assertEqual(plan.progress().percent, 100)

    def test_a_lock_does_not_hide_a_failure(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: failed\n", self.lock())
        self.assertEqual(plan.root.children[0].status, "failed")

    def test_a_lock_does_not_hide_a_blocked_task(self):
        """Blocked means something outside the agent must move. Someone holding
        the files does not change that, and burying it under In progress would
        take a NEEDS_HUMAN item off the operator's screen."""
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: blocked\n", self.lock())
        self.assertEqual(plan.root.children[0].status, "blocked")
        self.assertEqual([node.id for node in plan.attention()], ["N1"])

    def test_an_expired_lock_is_not_live_work(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n",
                          self.lock(expires=PAST))
        self.assertEqual(plan.root.children[0].status, "pending")
        self.assertEqual(plan.current(), [])

    def test_history_entries_are_not_read_as_live_work(self):
        """Released locks describe the past. Reading them would show every task
        anyone ever touched as being worked on right now."""
        locks = (
            'locks: []\n'
            'history:\n  - path: "src/thing.py"\n    task_id: "T-1"\n'
            '    agent: "old-session"\n'
            f'    expires_at: "{FUTURE}"\n'
        )
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n", locks)
        self.assertEqual(plan.current(), [])
        self.assertEqual(plan.root.children[0].status, "pending")

    def test_no_locks_file_is_not_an_error(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n")
        self.assertEqual(plan.root.children[0].status, "pending")
        self.assertEqual(plan.notices, [])


class Notices(Base):
    """Disagreements are reported, never smoothed over — and never fatal."""

    def test_a_lock_over_a_ready_task_is_a_notice_not_a_problem(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n", self.lock())
        self.assertEqual(plan.problems, [],
                         "coordination drift must not fail the protocol 04 hand-over gate")
        self.assertEqual(len(plan.notices), 1)
        self.assertIn("T-1", plan.notices[0])
        self.assertIn("session-a", plan.notices[0])

    def test_a_claimed_task_with_a_matching_lock_is_silent(self):
        """The consistent case. A notice here would train the reader to ignore
        the panel."""
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: claimed\n", self.lock())
        self.assertEqual(plan.notices, [])
        self.assertEqual(plan.root.children[0].status, "in_progress")

    def test_a_lock_left_on_a_finished_task_is_reported_as_a_leak(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: complete\n", self.lock())
        self.assertEqual(len(plan.notices), 1)
        self.assertIn("leak", plan.notices[0])

    def test_a_lock_for_an_unknown_task_is_reported(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: ready\n",
                          self.lock(task_id="T-999"))
        self.assertTrue(any("T-999" in notice for notice in plan.notices))

    def test_two_live_locks_on_one_task_is_reported(self):
        """C-19 in the framework's own vocabulary: exactly one writer."""
        locks = self.lock(agent="session-a") + (
            f'  - path: "src/other.py"\n    task_id: "T-1"\n'
            f'    agent: "session-b"\n    expires_at: "{FUTURE}"\n'
        )
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: claimed\n", locks)
        self.assertTrue(any("locked twice" in notice for notice in plan.notices))

    def test_a_lock_with_no_expiry_is_live_and_reported(self):
        """Treating an unparseable or absent TTL as expired would silently
        unlock a file somebody is editing. Fail toward the safer answer, and
        say so."""
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: claimed\n",
                          self.lock(expires=""))
        self.assertEqual(plan.root.children[0].status, "in_progress")
        self.assertTrue(any("TTL" in notice for notice in plan.notices))

    def test_an_unreadable_expiry_is_live_and_reported(self):
        plan = self.build(ONE_TASK_PLAN, "tasks:\n  - id: T-1\n    status: claimed\n",
                          self.lock(expires="whenever"))
        self.assertEqual(plan.root.children[0].status, "in_progress")
        self.assertTrue(any("unreadable" in notice for notice in plan.notices))


if __name__ == "__main__":
    unittest.main()
