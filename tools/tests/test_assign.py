"""Assignment: the rule precedence, and the surgical write-back.

The write-back is the risky half. A plan carries the planner's comments and
ordering, and assignment must not cost either — so the tests assert the file is
unchanged apart from the one key, not merely that the value parses back.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from aefkit import assign as assign_mod
from aefkit.paths import framework_file
from aefkit.model import Plan

# Resolve BOTH layouts. AEF is normally vendored at <project>/aef, but it is
# also a repository in its own right, and its suite must pass in both — it did
# not, which was found by running it from a fresh clone before publishing.
_HERE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK = os.path.abspath(os.path.join(_HERE, "..", ".."))
_PARENT = os.path.abspath(os.path.join(FRAMEWORK, ".."))
# Vendored iff the parent actually contains aef/config; otherwise the framework
# root IS the project root, which is what a standalone checkout looks like.
ROOT = _PARENT if os.path.isdir(os.path.join(_PARENT, "aef", "config")) else FRAMEWORK

PLAN = """# a comment that must survive
meta:
  project: T
tree:
  id: PRJ
  type: project
  title: T
  children:

    # section comment
    - id: S1
      type: section
      title: Frontend
      children:
        - id: N1
          title: Dashboard UI
          task: T-1
        - id: N2
          title: Project tree
          task: T-2
          agent: frontend-agent
          agent_locked: true
"""

TASKS = """tasks:
  - id: T-1
    status: ready
    change_class: ui_change
  - id: T-2
    status: ready
    change_class: api_change
"""


class Suggestions(unittest.TestCase):
    def setUp(self):
        self.catalogue = assign_mod.load_catalogue(ROOT)

    def test_change_class_is_the_strongest_basis(self):
        suggestion = assign_mod.suggest(
            {"change_class": "auth_or_permissions", "owner_role": "implementer"}, "anything",
            self.catalogue)
        self.assertEqual(suggestion.agent, "security-agent")
        self.assertEqual(suggestion.basis, "change_class")
        self.assertEqual(suggestion.confidence, "strong")

    def test_owner_role_is_used_when_there_is_no_change_class(self):
        suggestion = assign_mod.suggest({"owner_role": "verifier"}, "measure cold start", self.catalogue)
        self.assertEqual(suggestion.agent, "test-agent")
        self.assertEqual(suggestion.basis, "owner_role")

    def test_a_title_keyword_is_the_last_resort_and_says_it_is_weak(self):
        suggestion = assign_mod.suggest({}, "Rework the login page layout", self.catalogue)
        self.assertEqual(suggestion.agent, "frontend-agent")
        self.assertEqual(suggestion.confidence, "weak")
        self.assertIn("WEAK", suggestion.reason)

    def test_nothing_matched_leaves_it_unassigned_rather_than_guessing(self):
        suggestion = assign_mod.suggest({}, "zzzz", self.catalogue)
        self.assertIsNone(suggestion.agent)
        self.assertEqual(suggestion.confidence, "none")

    def test_every_routing_change_class_has_an_assignment_rule(self):
        """A change class routing.yaml knows about but agents.yaml does not would
        silently fall through to a weak keyword match."""
        from aefkit import yamlio
        routing = yamlio.load(framework_file(ROOT, "config", "routing.yaml"))
        missing = sorted(set(routing["classes"]) - set(self.catalogue.by_change_class))
        self.assertEqual(missing, [], f"change classes with no agent rule: {missing}")

    def test_every_rule_points_at_an_agent_that_exists(self):
        for source in (self.catalogue.by_change_class, self.catalogue.by_owner_role):
            for key, agent in source.items():
                self.assertTrue(self.catalogue.known(agent), f"{key} -> unknown agent {agent}")
        for agent in self.catalogue.by_title_keyword:
            self.assertTrue(self.catalogue.known(agent), f"unknown agent {agent}")


class WriteBack(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        state = os.path.join(self.directory, ".ai", "state")
        os.makedirs(state)
        self.plan_path = os.path.join(state, "plan.yaml")
        with open(self.plan_path, "w", encoding="utf-8") as handle:
            handle.write(PLAN)
        with open(os.path.join(state, "tasks.yaml"), "w", encoding="utf-8") as handle:
            handle.write(TASKS)
        self.catalogue = assign_mod.load_catalogue(ROOT)

    def read(self) -> str:
        with open(self.plan_path, encoding="utf-8") as handle:
            return handle.read()

    def test_assigning_adds_the_key_and_changes_nothing_else(self):
        from collections import Counter
        before = self.read()
        assign_mod.set_agent(self.plan_path, "N1", "frontend-agent", catalogue=self.catalogue)
        after = self.read()
        self.assertIn("# a comment that must survive", after)
        self.assertIn("# section comment", after)
        # Multiset difference, not membership: the fixture already carries an
        # `agent: frontend-agent` line on the sibling node, so a set-based check
        # would see no change and pass whatever happened.
        delta = Counter(after.splitlines()) - Counter(before.splitlines())
        self.assertEqual(sorted(line.strip() for line in delta.elements()),
                         ["agent: frontend-agent", "agent_locked: true"])
        self.assertEqual(Counter(before.splitlines()) - Counter(after.splitlines()), Counter(),
                         "nothing may be removed")
        plan = Plan.load(self.directory)
        by_id = {node.id: node for node in plan.root.leaves()}
        self.assertEqual(by_id["N1"].agent, "frontend-agent")
        self.assertEqual(by_id["N1"].agent_source, "manual")

    def test_reassigning_replaces_rather_than_duplicating(self):
        assign_mod.set_agent(self.plan_path, "N1", "frontend-agent", catalogue=self.catalogue)
        assign_mod.set_agent(self.plan_path, "N1", "backend-agent", catalogue=self.catalogue)
        plan = Plan.load(self.directory)
        by_id = {node.id: node for node in plan.root.leaves()}
        self.assertEqual(by_id["N1"].agent, "backend-agent")
        self.assertEqual(by_id["N2"].agent, "frontend-agent", "the sibling is untouched")
        # One agent line per node, so N1 did not accumulate both assignments.
        self.assertEqual(self.read().count("agent: backend-agent"), 1)
        self.assertEqual(self.read().count("agent: frontend-agent"), 1)

    def test_clearing_removes_both_keys(self):
        assign_mod.set_agent(self.plan_path, "N2", None, catalogue=self.catalogue)
        plan = Plan.load(self.directory)
        by_id = {node.id: node for node in plan.root.leaves()}
        self.assertIsNone(by_id["N2"].agent)
        self.assertNotIn("agent_locked", self.read())

    def test_assigning_one_node_does_not_touch_its_sibling(self):
        assign_mod.set_agent(self.plan_path, "N1", "backend-agent", catalogue=self.catalogue)
        plan = Plan.load(self.directory)
        by_id = {node.id: node for node in plan.root.leaves()}
        self.assertEqual(by_id["N2"].agent, "frontend-agent")
        self.assertEqual(by_id["N2"].agent_source, "manual")

    def test_an_unknown_agent_is_refused_with_the_known_list(self):
        with self.assertRaises(assign_mod.AssignError) as caught:
            assign_mod.set_agent(self.plan_path, "N1", "wizard", catalogue=self.catalogue)
        self.assertIn("backend-agent", str(caught.exception))
        self.assertNotIn("wizard", self.read())

    def test_an_unknown_node_is_refused(self):
        with self.assertRaises(assign_mod.AssignError):
            assign_mod.set_agent(self.plan_path, "N-nope", "backend-agent", catalogue=self.catalogue)

    def test_assigning_a_group_node_works_and_children_inherit(self):
        assign_mod.set_agent(self.plan_path, "S1", "frontend-agent", catalogue=self.catalogue)
        plan = Plan.load(self.directory)
        by_id = {node.id: node for node in plan.root.leaves()}
        self.assertEqual(by_id["N1"].agent, "frontend-agent")
        self.assertEqual(by_id["N1"].agent_source, "inherited")


if __name__ == "__main__":
    unittest.main()
