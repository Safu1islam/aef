"""Multi-agent coordination: liveness, the Main Engineer post, recommendations.

These are written against the scenarios the 0.4.0 mandate names — single agent,
several concurrent, conflict, handoff, session restart, Main Engineer
replacement, blocker, recommendation, rejected recommendation kept — because a
test that only exercises a getter proves nothing about whether the framework can
actually run a team.

Every case here fails if the behaviour it describes is removed. That is checked
by sabotage, not asserted; see DR-015.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from aefkit import teamstore as store
from aefkit.team import Team

NOW = datetime.now(timezone.utc)
FRESH = NOW.isoformat()
OLD = (NOW - timedelta(hours=2)).isoformat()

AGENTS = {"backend-agent", "frontend-agent", "architect", "security-agent", "test-agent"}


def project(tmp: str) -> str:
    os.makedirs(os.path.join(tmp, ".ai", "state"), exist_ok=True)
    return tmp


def write_sessions(root: str, body: str) -> None:
    with open(os.path.join(root, ".ai", "state", "sessions.yaml"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(body)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = project(tempfile.mkdtemp())

    def team(self) -> Team:
        return Team.load(self.root)

    def session(self, sid: str, agent: str = "backend-agent", **kw) -> dict:
        return store.start_session(self.root, sid, agent, known_agents=AGENTS, **kw)


class Liveness(Base):
    def test_a_fresh_session_is_live_and_working(self):
        self.session("s1", task="T-1", activity="doing the thing")
        team = self.team()
        self.assertEqual([s.id for s in team.working()], ["s1"])
        self.assertTrue(team.sessions[0].live)

    def test_a_session_with_an_old_heartbeat_is_stale_not_working(self):
        """The defect 0.4.0 exists to fix. A lock TTL is 90 minutes, so under
        0.3.0 this agent looked busy for an hour and a half after dying."""
        write_sessions(self.root,
                       'sessions:\n  - id: "s1"\n    agent: "backend-agent"\n'
                       f'    status: "working"\n    heartbeat_at: "{OLD}"\n')
        team = self.team()
        self.assertEqual([s.id for s in team.stale()], ["s1"])
        self.assertEqual(team.working(), [])
        self.assertFalse(team.sessions[0].live)

    def test_stale_is_never_read_from_the_file(self):
        """A crashed process cannot write its own obituary, so a stored `stale`
        would be the one value guaranteed to be absent when it mattered."""
        write_sessions(self.root,
                       'sessions:\n  - id: "s1"\n    agent: "backend-agent"\n'
                       f'    status: "stale"\n    heartbeat_at: "{FRESH}"\n')
        team = self.team()
        self.assertEqual(team.sessions[0].status, "idle",
                         "an unknown written status falls back to idle")
        self.assertTrue(team.sessions[0].live)

    def test_a_session_with_no_timestamps_is_stale_not_live(self):
        write_sessions(self.root, 'sessions:\n  - id: "s1"\n    agent: "backend-agent"\n'
                                  '    status: "working"\n')
        self.assertEqual([s.id for s in self.team().stale()], ["s1"])

    def test_several_agents_work_concurrently(self):
        self.session("s1", agent="backend-agent", task="T-1")
        self.session("s2", agent="frontend-agent", task="T-2")
        self.session("s3", agent="test-agent", task="T-3")
        team = self.team()
        self.assertEqual(len(team.working()), 3)
        self.assertEqual(sorted(team.workload()), ["backend-agent", "frontend-agent", "test-agent"])

    def test_no_state_files_is_an_empty_team_not_an_error(self):
        """The whole migration story: a 0.3.0 project loads unchanged."""
        team = self.team()
        self.assertEqual(team.sessions, [])
        self.assertEqual(team.notices(), [])
        self.assertIsNone(team.main_engineer())


class MainEngineerPost(Base):
    def test_the_post_is_single_holder(self):
        self.session("m1", agent="architect", main_engineer=True)
        self.session("w1", agent="backend-agent")
        with self.assertRaises(store.TeamStoreError) as caught:
            store.claim_main_engineer(self.root, "w1")
        self.assertIn("already holds main_engineer", str(caught.exception))

    def test_a_stale_holder_can_be_replaced(self):
        """Session continuity, which is the point of the post existing in state
        rather than in a chat: the coordinator's process dies and the project
        keeps a coordinator."""
        write_sessions(self.root,
                       'sessions:\n  - id: "m1"\n    agent: "architect"\n'
                       f'    main_engineer: true\n    status: "idle"\n    heartbeat_at: "{OLD}"\n'
                       '  - id: "m2"\n    agent: "architect"\n'
                       f'    status: "idle"\n    heartbeat_at: "{FRESH}"\n')
        self.assertIsNone(self.team().main_engineer(), "a stale holder does not hold the post")
        store.claim_main_engineer(self.root, "m2")
        self.assertEqual(self.team().main_engineer().id, "m2")

    def test_a_stale_holder_is_reported_as_vacant_not_silently_inherited(self):
        write_sessions(self.root,
                       'sessions:\n  - id: "m1"\n    agent: "architect"\n'
                       f'    main_engineer: true\n    status: "idle"\n    heartbeat_at: "{OLD}"\n')
        notices = self.team().notices()
        self.assertTrue(any("VACANT" in n for n in notices), notices)

    def test_two_live_holders_are_reported(self):
        write_sessions(self.root,
                       'sessions:\n  - id: "m1"\n    agent: "architect"\n'
                       f'    main_engineer: true\n    status: "idle"\n    heartbeat_at: "{FRESH}"\n'
                       '  - id: "m2"\n    agent: "architect"\n'
                       f'    main_engineer: true\n    status: "idle"\n    heartbeat_at: "{FRESH}"\n')
        self.assertTrue(any("single-holder" in n for n in self.team().notices()))

    def test_ending_a_session_releases_the_post(self):
        self.session("m1", agent="architect", main_engineer=True)
        store.end_session(self.root, "m1", outcome="completed")
        self.assertIsNone(self.team().main_engineer())
        self.session("m2", agent="architect")
        store.claim_main_engineer(self.root, "m2")
        self.assertEqual(self.team().main_engineer().id, "m2")


class Handoff(Base):
    def test_ending_records_a_handoff_the_next_session_can_read(self):
        self.session("s1", task="T-1")
        store.end_session(self.root, "s1", outcome="paused",
                          changed="storage.py reclaim path",
                          remaining="multi-process contention NOT_RUN",
                          next_step="T-2 can start", references=["DR-006"])
        ended = [s for s in self.team().sessions if s.ended]
        self.assertEqual(len(ended), 1)
        handoff = ended[0].handoff
        self.assertEqual(handoff["outcome"], "paused")
        self.assertIn("NOT_RUN", handoff["remaining"])
        self.assertEqual(handoff["references"], ["DR-006"])

    def test_an_ended_session_is_not_live(self):
        self.session("s1", task="T-1")
        store.end_session(self.root, "s1")
        self.assertEqual(self.team().live(), [])

    def test_ended_is_terminal(self):
        self.session("s1")
        store.end_session(self.root, "s1")
        with self.assertRaises(store.TeamStoreError):
            store.heartbeat(self.root, "s1", activity="back from the dead")


class Guards(Base):
    def test_an_unknown_agent_is_refused(self):
        with self.assertRaises(store.TeamStoreError) as caught:
            store.start_session(self.root, "s1", "backnd-agent", known_agents=AGENTS)
        self.assertIn("unknown agent", str(caught.exception))

    def test_a_duplicate_session_id_is_refused(self):
        self.session("s1")
        with self.assertRaises(store.TeamStoreError):
            self.session("s1")

    def test_a_heartbeat_cannot_create_a_session(self):
        """Otherwise a typo mints a phantom agent that occupies a slot."""
        with self.assertRaises(store.TeamStoreError):
            store.heartbeat(self.root, "typo", activity="x")

    def test_blocked_requires_a_reason(self):
        self.session("s1", task="T-1")
        with self.assertRaises(store.TeamStoreError) as caught:
            store.heartbeat(self.root, "s1", status="blocked")
        self.assertIn("blocked session must record why", str(caught.exception))
        store.heartbeat(self.root, "s1", status="blocked", blocked_reason="waiting on T-9")
        self.assertEqual(self.team().sessions[0].display_status, "blocked")

    def test_stale_cannot_be_set_by_hand(self):
        self.session("s1")
        with self.assertRaises(store.TeamStoreError) as caught:
            store.heartbeat(self.root, "s1", status="stale")
        self.assertIn("derived", str(caught.exception))


class Notices(Base):
    def test_a_session_working_an_unknown_task_is_reported(self):
        self.session("s1", task="T-999")
        self.assertTrue(any("T-999" in n for n in self.team().notices({})))

    def test_a_session_still_working_a_complete_task_is_reported(self):
        self.session("s1", task="T-1")
        notices = self.team().notices({"T-1": {"status": "complete"}})
        self.assertTrue(any("already 'complete'" in n for n in notices))

    def test_a_session_running_as_an_uncatalogued_agent_is_reported(self):
        write_sessions(self.root, 'sessions:\n  - id: "s1"\n    agent: "ghost-agent"\n'
                                  f'    status: "idle"\n    heartbeat_at: "{FRESH}"\n')
        notices = self.team().notices({}, known_agents=AGENTS)
        self.assertTrue(any("ghost-agent" in n for n in notices))

    def test_a_stale_session_is_reported_so_its_claims_can_be_reclaimed(self):
        write_sessions(self.root,
                       'sessions:\n  - id: "s1"\n    agent: "backend-agent"\n'
                       f'    task: "T-1"\n    status: "working"\n    heartbeat_at: "{OLD}"\n')
        self.assertTrue(any("stale" in n for n in self.team().notices()))


class Recommendations(Base):
    def add(self, **kw):
        params = dict(recommendation="do the thing", reason="because of measured evidence")
        params.update(kw)
        return store.add_recommendation(self.root, kw.pop("title", "a proposal"), **params)

    def test_a_recommendation_is_recorded_without_changing_anything(self):
        entry = self.add()
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(len(self.team().open_recommendations()), 1)

    def test_a_reason_is_required(self):
        with self.assertRaises(store.TeamStoreError):
            store.add_recommendation(self.root, "t", recommendation="x", reason="   ")

    def test_rejection_requires_a_reason(self):
        """The entire point of the file: a rejection with no reason gets
        re-proposed by the next agent, forever."""
        self.add()
        with self.assertRaises(store.TeamStoreError) as caught:
            store.resolve_recommendation(self.root, "R-001", "rejected")
        self.assertIn("reason", str(caught.exception))

    def test_a_rejected_recommendation_is_kept_with_its_reason(self):
        self.add()
        store.resolve_recommendation(self.root, "R-001", "rejected",
                                     reason="measured; the cache is not the bottleneck")
        team = self.team()
        self.assertEqual(team.open_recommendations(), [])
        kept = [r for r in team.recommendations if r.id == "R-001"]
        self.assertEqual(len(kept), 1, "a rejected recommendation is never deleted")
        self.assertIn("bottleneck", kept[0].resolution["reason"])

    def test_acceptance_must_produce_work_or_a_decision(self):
        self.add()
        with self.assertRaises(store.TeamStoreError) as caught:
            store.resolve_recommendation(self.root, "R-001", "accepted")
        self.assertIn("agreement", str(caught.exception))

    def test_acceptance_links_the_task_it_became(self):
        self.add()
        store.resolve_recommendation(self.root, "R-001", "accepted", became_task="T-51")
        rec = [r for r in self.team().recommendations if r.id == "R-001"][0]
        self.assertEqual(rec.status, "accepted")
        self.assertEqual(rec.resolution["became_task"], "T-51")

    def test_ids_do_not_collide(self):
        self.add()
        self.add()
        self.add()
        self.assertEqual([r.id for r in self.team().recommendations],
                         ["R-001", "R-002", "R-003"])

    def test_severity_orders_open_recommendations(self):
        self.add(severity="minor")
        self.add(severity="critical")
        self.add(severity="important")
        self.assertEqual([r.severity for r in self.team().open_recommendations()],
                         ["critical", "important", "minor"])

    def test_an_open_proposal_is_findable_by_the_component_it_touches(self):
        """So an agent about to change a file learns a proposal already stands
        on it, instead of colliding with it."""
        self.add(affected=["src/storage/ledger.py"])
        hits = self.team().recommendations_touching("src/storage/ledger.py")
        self.assertEqual([r.id for r in hits], ["R-001"])
        self.assertEqual(self.team().recommendations_touching("src/web/app.py"), [])


class SessionRestart(Base):
    """A completely new session continues from state alone — no chat history."""

    def test_a_new_session_sees_what_the_previous_one_left(self):
        self.session("first", task="T-1", activity="half done")
        store.end_session(self.root, "first", outcome="paused",
                          remaining="the second half", next_step="continue T-1")
        # A brand new Team load, as a fresh process would do.
        fresh = Team.load(self.root)
        ended = [s for s in fresh.sessions if s.ended][0]
        self.assertEqual(ended.handoff["next"], "continue T-1")
        self.assertEqual(fresh.live(), [], "the previous process is gone")


if __name__ == "__main__":
    unittest.main()
