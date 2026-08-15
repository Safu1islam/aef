"""T-032 — one error class, one signal, on every route of both surfaces.

Why this is its own file rather than an addition to an existing one:

  * ``tests/test_locking.py`` owns C-19 *mechanics* — that the lock is taken,
    held with a visible owner, and released. What signal contention reports to
    a caller is a different property, and it is not specific to locking.
  * ``tests/test_parity.py`` owns *enumeration* — every registered operation
    answering on both surfaces. Its 62 probes deliberately use sentinel ids
    against a store with no locks outstanding, so they cannot produce
    ENTITY_LOCKED at all; adding the pair to its ``SURFACE_SIGNALS`` table
    proves nothing on its own. The proof has to come from a real contended
    entity, which is here.
  * The subject of this file is the contract itself: which HTTP status and
    which exit code each error class carries, and that the answer is the same
    whichever route raised it. That is a third thing, and naming it is what
    makes a reader look here when a status changes.

The defect being closed: ENTITY_LOCKED had no surface signal of its own. The
web adapter had no entry for it in any of its four separate status maps, so it
fell through to 400, and the CLI returned the base ``exit_code = 1``. Both
surfaces agreed, so F-1 held and the parity gate passed — but protocol 05 tells
a blocked agent to take a different ready task, and an agent cannot act on that
while contention is indistinguishable from a business-rule refusal.

Both failure directions are covered. A fix that answered 409 (or 404) for
everything would satisfy a naive check, so VALIDATION/400, FORBIDDEN/403 and a
plain success are asserted through the same unified map.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promedia import cli
from promedia.config import load as load_config
from promedia.core import db
from promedia.core.credentials import CredentialStore
from promedia.core.principal import operator
from promedia.core.registry import Context, invoke
from promedia.errors import (
    ApprovalRequired,
    EntityLocked,
    Forbidden,
    ProMediaError,
    ValidationError,
)
from promedia.web.app import COOKIE_NAME, ERROR_STATUS, create_app
from tests.conftest import declaration_original, declaration_uncleared

REPO = Path(__file__).resolve().parents[1]

# Held by a third session that is mid-operation. Contention, not absence.
CONTENDED_ASSET = "as_held_by_another_session"
CONTENDED_POST = "post_held_by_another_session"
OTHER_AGENT = "agent-gamma"


@pytest.fixture
def surface(tmp_path, monkeypatch):
    """One data directory, reachable by both adapters, with an operator token.

    The environment variables are what let the CLI half of a test see the same
    database the web half does: the CLI loads configuration through its own real
    code path, so an agent's invocation is not special-cased here.
    """
    monkeypatch.setenv("PROMEDIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    monkeypatch.setenv("PROMEDIA_CONFIG", str(REPO / "promedia.toml"))
    monkeypatch.delenv("PROMEDIA_OPERATOR_TOKEN", raising=False)

    cfg = load_config()
    store = CredentialStore()
    token = store.ensure_operator_token()

    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)

    # Two entities a third agent is holding. Written the way that session's own
    # invoke() would have written them — there is no other way to have a lock
    # outstanding while a separate caller makes its attempt.
    for entity_type, entity_id in (("asset", CONTENDED_ASSET), ("post", CONTENDED_POST)):
        db.acquire_lock(
            conn,
            entity_type,
            entity_id,
            task_id="determine-rights" if entity_type == "asset" else "approve-post",
            agent=OTHER_AGENT,
            model="claude-opus-5",
            ttl_minutes=int(cfg.get("locks", "ttl_minutes")),
        )

    ctx = Context(config=cfg, conn=conn, principal=operator("seed"))
    yield Surfaces(cfg=cfg, store=store, token=token, ctx=ctx)
    conn.close()


class Surfaces:
    """The two adapters plus the seeding context, over one configuration."""

    def __init__(self, cfg, store, token, ctx) -> None:
        self.cfg = cfg
        self.store = store
        self.token = token
        self.ctx = ctx

    def agent_client(self, **kwargs) -> TestClient:
        """No token presented — agent authority, as an unauthenticated caller."""
        return TestClient(create_app(self.cfg, store=self.store), **kwargs)

    def operator_client(self, **kwargs) -> TestClient:
        client = TestClient(create_app(self.cfg, store=self.store), **kwargs)
        client.cookies.set(COOKIE_NAME, self.token)
        return client

    def seed_post(self, media_file, declaration=None) -> str:
        """A real, approvable post: connected account, sealed, PERMITTED."""
        account = invoke(
            self.ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"}
        )
        asset = invoke(
            self.ctx,
            "ingest",
            {
                "source_path": str(media_file),
                "declaration": declaration or declaration_original(),
            },
        )
        invoke(self.ctx, "determine-rights", {"asset_id": asset["asset_id"]})
        invoke(self.ctx, "seal-provenance", {"asset_id": asset["asset_id"]})
        return invoke(
            self.ctx,
            "queue-post",
            {
                "account_id": account["account_id"],
                "asset_id": asset["asset_id"],
                "body": "hello world",
            },
        )["post_id"]


def _cli(name: str, params: dict[str, str]) -> tuple[dict, int]:
    """Invoke through the CLI adapter: argv in, JSON and an exit code out."""
    argv = [name, "--json"]
    for key, value in params.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(argv)
    return json.loads(stdout.getvalue()), int(exit_code)


# --- (a) contention has a signal of its own, on both surfaces at once ---------


def test_contended_entity_is_409_on_the_web_and_exit_4_on_the_cli(surface):
    """AC-1. Both halves in ONE test, so the pair cannot drift apart.

    Split across two tests, one could be updated and the other left behind and
    the suite would still be green while the surfaces disagreed. Asserted
    together, a change to either number fails here.
    """
    params = {"asset_id": CONTENDED_ASSET}

    response = surface.agent_client().post("/api/op/determine-rights", data=params)
    payload, exit_code = _cli("determine-rights", params)

    # Same refusal, same owner named, on both surfaces.
    assert response.json()["error"] == "ENTITY_LOCKED" == payload["error"]
    assert response.json()["detail"]["owner"] == payload["detail"]["owner"] == OTHER_AGENT

    # ...and now with a signal an agent can branch on without parsing prose.
    assert response.status_code == 409, "contention must be 409 Conflict, not a generic 400"
    assert exit_code == 4, "contention must be exit 4, not the base failure code 1"


def test_contended_entity_is_409_on_the_html_route_too(surface):
    """The HTML route consults the same map, not a copy of part of it.

    ``/posts/{id}/approve`` is where the four inline maps used to live. The lock
    is taken in the operation layer before the handler runs, so this is reached
    even though the post itself does not exist — which is the point: ownership
    is settled before anything is allowed to touch the entity.
    """
    response = surface.operator_client().post(
        f"/posts/{CONTENDED_POST}/approve", data={"decision": "approved"}
    )
    assert response.status_code == 409
    assert "ENTITY_LOCKED" in response.text
    assert OTHER_AGENT in response.text  # C-19: the owner stays visible


def test_contended_entity_is_409_on_the_generic_form_route(surface, media_file):
    """/ops/{name} is the fifth site the old maps were duplicated across.

    Two submissions since T-035: approve-post shows its decision context first
    and executes nothing, so the first POST cannot report contention — nothing
    has been attempted yet. The 409 belongs on the request that actually tries
    the write, and that is what is asserted here. The adapter deliberately does
    not pre-check the lock table to surface it sooner; that would be C-19 logic
    living in a surface, which is what DR-002 keeps out of the adapters.

    Uses a post that REALLY EXISTS and is then locked, rather than the module's
    phantom CONTENDED_POST. The phantom is fine for routes that go straight to
    invoke() — the lock is taken before the handler, so ENTITY_LOCKED precedes
    NOT_FOUND — but this route reads the post first, and for an id with no row
    404 is the correct answer and the more specific one. Seeding a real post
    keeps the test about contention instead of about a fixture artefact.
    """
    from tests.test_decision_context import confirmation

    ctx = surface.ctx
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    post_id = invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset["asset_id"], "body": "hi"},
    )["post_id"]
    db.acquire_lock(
        ctx.conn, "post", post_id,
        task_id="approve-post", agent=OTHER_AGENT, model="claude-opus-5",
        ttl_minutes=int(surface.cfg.get("locks", "ttl_minutes")),
    )

    client = surface.operator_client()
    data = {"post_id": post_id, "decision": "approved"}

    shown = client.post("/ops/approve-post", data=data)
    assert shown.status_code == 200, "the decision context should render, not execute"

    response = client.post("/ops/approve-post", data={**data, **confirmation(shown)})
    assert response.status_code == 409
    assert "ENTITY_LOCKED" in response.text
    assert OTHER_AGENT in response.text  # C-19: the owner stays visible


# --- (b) NOT_FOUND on the HTML routes is 404, not 400 ------------------------


def test_unknown_post_id_is_404_on_the_html_approve_route(surface):
    """The second defect. These routes special-cased only the 403 classes.

    An unknown id fell through to 400 here while /api/op/{name} and /ops/{name}
    already answered 404 for the identical refusal — the same error class
    reporting differently depending on which route happened to raise it.
    """
    response = surface.operator_client().post(
        "/posts/post_no_such_thing/approve", data={"decision": "approved"}
    )
    assert response.status_code == 404, "an unknown post is an absent resource, not a bad request"
    assert "NOT_FOUND" in response.text


def test_unknown_post_id_is_404_on_the_html_publish_route(surface):
    response = surface.operator_client().post("/posts/post_no_such_thing/publish")
    assert response.status_code == 404
    assert "NOT_FOUND" in response.text


# --- (c) the opposite failure direction --------------------------------------
#
# A fix that returned 409 for everything, or 404 for everything, would satisfy
# every assertion above. These are what stop that.


def test_validation_is_still_400_through_the_unified_map(surface):
    """An ordinary bad parameter must not have been swept into 409 or 404."""
    response = surface.operator_client().post(
        f"/posts/{'post_anything'}/approve", data={"decision": "maybe"}
    )
    assert response.status_code == 400
    assert "VALIDATION" in response.text


def test_forbidden_is_still_403_through_the_unified_map(surface, media_file):
    """F-2 on the same route, with no operator token presented."""
    post_id = surface.seed_post(media_file)
    response = surface.agent_client().post(
        f"/posts/{post_id}/approve", data={"decision": "approved"}
    )
    assert response.status_code == 403
    assert "FORBIDDEN" in response.text


def test_rights_blocked_is_403_on_the_json_api(surface, media_file):
    """CONTRACT CHANGE, deliberate: this was 400 on /api/op and 403 on /posts.

    api_op's map had no RIGHTS_BLOCKED entry while the HTML approve route did,
    so the hard rights gate (F-3) reported a different class of answer depending
    on which surface an agent used. Unifying the map fixes it in the direction
    the HTML route already had right. Recorded rather than discovered.
    """
    post_id = surface.seed_post(media_file, declaration=declaration_uncleared())
    response = surface.operator_client().post(
        "/api/op/approve-post", data={"post_id": post_id, "decision": "approved"}
    )
    assert response.json()["error"] == "RIGHTS_BLOCKED"
    assert response.status_code == 403


def test_a_present_unlocked_entity_still_succeeds(surface, media_file):
    """The case a status-mapping change is most likely to break silently."""
    post_id = surface.seed_post(media_file)
    client = surface.operator_client(follow_redirects=False)

    response = client.post(f"/posts/{post_id}/approve", data={"decision": "approved"})
    assert response.status_code == 303, "a real, unlocked, permitted post must still approve"

    detail = invoke(surface.ctx, "post", {"post_id": post_id})
    assert detail["status"] == "approved"

    # And the read route still renders it, rather than reporting some status.
    assert surface.operator_client().get(f"/posts/{post_id}").status_code == 200


def test_a_present_post_is_still_404_only_when_it_is_absent(surface, media_file):
    """post_detail now uses the map; NOT_FOUND must still be the 404 it was."""
    client = surface.operator_client()
    assert client.get("/posts/post_no_such_thing").status_code == 404
    assert client.get(f"/posts/{surface.seed_post(media_file)}").status_code == 200


# --- the contract itself ------------------------------------------------------


def test_exit_code_table_is_the_one_dr_012_records():
    """DR-012's table, asserted rather than only written down.

    0 is success (no exception), 130 is KeyboardInterrupt, and every other value
    belongs to exactly one class of refusal. 4 had to be a new number: sharing 1
    is the defect, and sharing 3 would tell an agent to hand contention to the
    operator, who can do nothing about it.
    """
    assert ProMediaError.exit_code == 1  # failed; retrying identically will not help
    assert ValidationError.exit_code == 2  # usage / bad input
    assert Forbidden.exit_code == 3  # F-2: hand it to the operator
    assert ApprovalRequired.exit_code == 3  # same instruction to the agent
    assert EntityLocked.exit_code == 4  # C-19: take a different ready task

    assert EntityLocked.exit_code not in {
        ProMediaError.exit_code,
        ValidationError.exit_code,
        Forbidden.exit_code,
    }, "contention that shares a code with another class is not a distinct signal"


def test_the_web_map_and_the_exit_codes_agree_on_entity_locked():
    """The two halves of the AC-1 pair, pinned at their source.

    tests/test_parity.py's SURFACE_SIGNALS pins the same pair for the gate; this
    asserts the values the adapters actually read, so a change to one of them
    fails here by name rather than only inside a parametrised probe.
    """
    assert ERROR_STATUS["ENTITY_LOCKED"] == 409
    assert EntityLocked.exit_code == 4


def test_the_map_covers_every_class_the_contract_names():
    """Unmapped is allowed and defaults to 400 — but not for these."""
    assert ERROR_STATUS == {
        "FORBIDDEN": 403,
        "APPROVAL_REQUIRED": 403,
        "RIGHTS_BLOCKED": 403,
        "NOT_FOUND": 404,
        "VALIDATION": 400,
        "ENTITY_LOCKED": 409,
    }
