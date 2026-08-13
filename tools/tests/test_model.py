"""Rollup, percentage and validation.

The rollup is where a dashboard lies most easily: a section containing a failed
task must not read as healthy, and a half-done section must not read as
untouched. Each of those is pinned here by a case that fails if the precedence
is reordered.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from aefkit.model import Plan, PlanError

PLAN_HEAD = "meta:\n  project: T\ntree:\n  id: PRJ\n  type: project\n  title: T\n  children:\n"


def write(directory: str, plan_body: str, tasks_body: str) -> str:
    state = os.path.join(directory, ".ai", "state")
    os.makedirs(state, exist_ok=True)
    with open(os.path.join(state, "plan.yaml"), "w", encoding="utf-8") as handle:
        handle.write(PLAN_HEAD + plan_body)
    with open(os.path.join(state, "tasks.yaml"), "w", encoding="utf-8") as handle:
        handle.write(tasks_body)
    return directory


class Base(unittest.TestCase):
    def build(self, plan_body: str, tasks_body: str) -> Plan:
        self.directory = tempfile.mkdtemp()
        self.addCleanup(lambda: None)
        write(self.directory, plan_body, tasks_body)
        return Plan.load(self.directory)


class Rollup(Base):
    def test_all_children_complete_makes_the_group_complete(self):
        plan = self.build(
            "    - id: S\n      type: section\n      title: S\n      children:\n"
            "        - id: N1\n          title: a\n          task: T-1\n"
            "        - id: N2\n          title: b\n          task: T-2\n",
            "tasks:\n  - id: T-1\n    status: complete\n  - id: T-2\n    status: complete\n",
        )
        self.assertEqual(plan.root.status, "complete")
        self.assertEqual(plan.progress().percent, 100)

    def test_a_partly_done_group_is_in_progress_not_pending(self):
        plan = self.build(
            "    - id: S\n      type: section\n      title: S\n      children:\n"
            "        - id: N1\n          title: a\n          task: T-1\n"
            "        - id: N2\n          title: b\n          task: T-2\n",
            "tasks:\n  - id: T-1\n    status: complete\n  - id: T-2\n    status: ready\n",
        )
        self.assertEqual(plan.root.status, "in_progress")
        self.assertEqual(plan.progress().percent, 50)

    def test_a_failed_child_outranks_everything(self):
        plan = self.build(
            "    - id: S\n      type: section\n      title: S\n      children:\n"
            "        - id: N1\n          title: a\n          task: T-1\n"
            "        - id: N2\n          title: b\n          task: T-2\n"
            "        - id: N3\n          title: c\n          task: T-3\n",
            "tasks:\n  - id: T-1\n    status: complete\n"
            "  - id: T-2\n    status: claimed\n  - id: T-3\n    status: failed\n",
        )
        self.assertEqual(plan.root.status, "failed",
                         "a section containing a failed task must not read as in progress")

    def test_blocked_shows_when_nothing_is_moving(self):
        plan = self.build(
            "    - id: S\n      type: section\n      title: S\n      children:\n"
            "        - id: N1\n          title: a\n          task: T-1\n",
            "tasks:\n  - id: T-1\n    status: blocked\n",
        )
        self.assertEqual(plan.root.status, "blocked")

    def test_abandoned_is_reported_as_failed_not_hidden(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n",
            "tasks:\n  - id: T-1\n    status: abandoned\n",
        )
        self.assertEqual(plan.root.status, "failed")


class Dependencies(Base):
    def test_a_ready_task_with_an_unmet_dependency_is_waiting_not_pending(self):
        plan = self.build(
            "    - id: N1\n      title: first\n      task: T-1\n"
            "    - id: N2\n      title: second\n      task: T-2\n",
            "tasks:\n  - id: T-1\n    status: ready\n"
            "  - id: T-2\n    status: ready\n    depends_on: [T-1]\n",
        )
        statuses = {node.id: node.status for node in plan.root.leaves()}
        self.assertEqual(statuses["N1"], "pending")
        self.assertEqual(statuses["N2"], "waiting_dependency")

    def test_the_dependency_clears_when_the_blocker_completes(self):
        plan = self.build(
            "    - id: N1\n      title: first\n      task: T-1\n"
            "    - id: N2\n      title: second\n      task: T-2\n",
            "tasks:\n  - id: T-1\n    status: complete\n"
            "  - id: T-2\n    status: ready\n    depends_on: [T-1]\n",
        )
        statuses = {node.id: node.status for node in plan.root.leaves()}
        self.assertEqual(statuses["N2"], "pending")

    def test_upcoming_excludes_tasks_that_are_waiting(self):
        plan = self.build(
            "    - id: N1\n      title: first\n      task: T-1\n"
            "    - id: N2\n      title: second\n      task: T-2\n",
            "tasks:\n  - id: T-1\n    status: ready\n"
            "  - id: T-2\n    status: ready\n    depends_on: [T-1]\n",
        )
        self.assertEqual([node.id for node in plan.upcoming()], ["N1"])


class Progress(Base):
    def test_percentage_counts_leaves_not_grouping_nodes(self):
        """Two plans describing the same four tasks must report the same
        percentage whether or not the tasks are nested in sections."""
        flat = self.build(
            "".join(f"    - id: N{i}\n      title: t{i}\n      task: T-{i}\n" for i in range(1, 5)),
            "tasks:\n" + "".join(
                f"  - id: T-{i}\n    status: {'complete' if i < 3 else 'ready'}\n" for i in range(1, 5)),
        )
        nested = self.build(
            "    - id: S1\n      type: section\n      title: S1\n      children:\n"
            "        - id: N1\n          title: t1\n          task: T-1\n"
            "        - id: N2\n          title: t2\n          task: T-2\n"
            "    - id: S2\n      type: section\n      title: S2\n      children:\n"
            "        - id: N3\n          title: t3\n          task: T-3\n"
            "        - id: N4\n          title: t4\n          task: T-4\n",
            "tasks:\n" + "".join(
                f"  - id: T-{i}\n    status: {'complete' if i < 3 else 'ready'}\n" for i in range(1, 5)),
        )
        self.assertEqual(flat.progress().percent, 50)
        self.assertEqual(nested.progress().percent, 50)

    def test_weight_shifts_the_percentage(self):
        plan = self.build(
            "    - id: N1\n      title: big\n      task: T-1\n      weight: 3\n"
            "    - id: N2\n      title: small\n      task: T-2\n",
            "tasks:\n  - id: T-1\n    status: complete\n  - id: T-2\n    status: ready\n",
        )
        self.assertEqual(plan.progress().percent, 75)


class Validation(Base):
    def test_a_task_missing_from_the_plan_is_a_problem(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n",
            "tasks:\n  - id: T-1\n    status: complete\n  - id: T-2\n    status: ready\n",
        )
        self.assertTrue(any("T-2" in problem for problem in plan.problems),
                        "an unplanned task silently shrinks the denominator")

    def test_a_task_claimed_twice_is_a_problem(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n"
            "    - id: N2\n      title: b\n      task: T-1\n",
            "tasks:\n  - id: T-1\n    status: complete\n",
        )
        self.assertTrue(any("claimed by 2 nodes" in problem for problem in plan.problems))

    def test_a_link_to_a_nonexistent_task_is_a_problem(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-9\n",
            "tasks:\n  - id: T-1\n    status: complete\n",
        )
        self.assertTrue(any("T-9" in problem for problem in plan.problems))

    def test_duplicate_node_ids_are_a_problem(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n"
            "    - id: N1\n      title: b\n      task: T-2\n",
            "tasks:\n  - id: T-1\n    status: complete\n  - id: T-2\n    status: ready\n",
        )
        self.assertTrue(any("duplicate node id" in problem for problem in plan.problems))

    def test_a_dependency_cycle_is_reported(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      depends_on: [N2]\n"
            "    - id: N2\n      title: b\n      depends_on: [N1]\n",
            "tasks: []\n",
        )
        self.assertTrue(any("cycle" in problem for problem in plan.problems))

    def test_follow_up_tasks_are_counted_too(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n",
            "tasks:\n  - id: T-1\n    status: complete\n"
            "follow_up_tasks:\n  - id: T-50\n    status: ready\n",
        )
        self.assertIn("T-50", plan.tasks)
        self.assertTrue(any("T-50" in problem for problem in plan.problems))

    def test_a_missing_plan_says_what_to_do(self):
        directory = tempfile.mkdtemp()
        with self.assertRaises(PlanError) as caught:
            Plan.load(directory)
        self.assertIn("04-planning", str(caught.exception))


class Agents(Base):
    def test_an_agent_on_a_group_is_inherited_by_its_children(self):
        plan = self.build(
            "    - id: S\n      type: section\n      title: S\n      agent: backend-agent\n      children:\n"
            "        - id: N1\n          title: a\n          task: T-1\n"
            "        - id: N2\n          title: b\n          task: T-2\n          agent: qa-agent\n",
            "tasks:\n  - id: T-1\n    status: ready\n  - id: T-2\n    status: ready\n",
        )
        by_id = {node.id: node for node in plan.root.leaves()}
        self.assertEqual(by_id["N1"].agent, "backend-agent")
        self.assertEqual(by_id["N1"].agent_source, "inherited")
        self.assertEqual(by_id["N2"].agent, "qa-agent", "an explicit child assignment wins")

    def test_workload_is_counted_per_agent(self):
        plan = self.build(
            "    - id: N1\n      title: a\n      task: T-1\n      agent: backend-agent\n"
            "    - id: N2\n      title: b\n      task: T-2\n      agent: backend-agent\n"
            "    - id: N3\n      title: c\n      task: T-3\n",
            "tasks:\n  - id: T-1\n    status: complete\n"
            "  - id: T-2\n    status: ready\n  - id: T-3\n    status: ready\n",
        )
        agents = plan.agents()
        self.assertEqual(agents["backend-agent"]["total"], 2)
        self.assertEqual(agents["backend-agent"]["counts"]["complete"], 1)
        self.assertEqual(agents["unassigned"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
