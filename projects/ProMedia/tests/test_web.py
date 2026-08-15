"""T-005 — the operator UI as the authority surface.

Tested as a user would use it: fetch the page, read what it says, submit the
form. Not by reading the handler.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from promedia.core import db
from promedia.core.principal import operator
from promedia.core.registry import Context, invoke
from tests.conftest import declaration_original, declaration_uncleared, make_config


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A UI with operator authority available, and simulation on."""
    store_path = tmp_path / "creds.json"
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(store_path))
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})

    from promedia.core.credentials import CredentialStore

    store = CredentialStore(store_path)
    store.ensure_operator_token()

    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=operator("seed"))
    yield cfg, ctx, store
    conn.close()


def _seed_post(ctx, media_file, declaration=None):
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset = invoke(
        ctx, "ingest",
        {"source_path": str(media_file), "declaration": declaration or declaration_original()},
    )
    invoke(ctx, "determine-rights", {"asset_id": asset["asset_id"]})
    invoke(ctx, "seal-provenance", {"asset_id": asset["asset_id"]})
    return invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset["asset_id"], "body": "hello world"},
    )["post_id"]


def test_dashboard_renders(env, media_file):
    cfg, ctx, store = env
    _seed_post(ctx, media_file)
    client = operator_client(cfg, store)
    response = client.get("/")
    assert response.status_code == 200
    assert "ProMedia" in response.text
    assert "Simulation is enabled" in response.text


def create_app_for(cfg, store):
    from promedia.web.app import create_app

    return create_app(cfg, store=store)


def operator_client(cfg, store, **kwargs):
    """A client authenticated the way the operator's browser is: by token."""
    from promedia.web.app import COOKIE_NAME

    client = TestClient(create_app_for(cfg, store), **kwargs)
    client.cookies.set(COOKIE_NAME, store.operator_token())
    return client


def test_approval_page_shows_decision_context(env, media_file):
    """AC-1: the operator must see the basis before any control is reachable."""
    cfg, ctx, store = env
    post_id = _seed_post(ctx, media_file)
    client = operator_client(cfg, store)
    html = client.get(f"/posts/{post_id}").text

    assert "PERMITTED" in html
    assert "conservative v1.0.0" in html
    assert "jurisdiction neutral" in html
    assert "sealed" in html
    assert "hello world" in html
    # The account being posted to must be visible, not implied.
    assert "@" in html or "me" in html


def test_unauthenticated_local_request_has_no_operator_authority(env, media_file):
    """REGRESSION — found by hand-verification, classified BLOCKING.

    The UI previously granted operator authority to anything that could reach
    the port, on the reasoning that localhost is a boundary. It is not: an agent
    can issue local HTTP requests exactly as easily as the operator's browser,
    which made F-2 unenforced on the web surface while the CLI enforced it.

    An unauthenticated client must be refused on AUTHORITY (FORBIDDEN), not
    incidentally by some later gate.
    """
    cfg, ctx, store = env
    post_id = _seed_post(ctx, media_file)  # PERMITTED, so no rights gate can mask the result

    anonymous = TestClient(create_app_for(cfg, store))
    response = anonymous.post(f"/posts/{post_id}/approve", data={"decision": "approved"})
    assert response.status_code == 403
    assert "FORBIDDEN" in response.text

    api = anonymous.post("/api/op/approve-post", data={"post_id": post_id})
    assert api.status_code == 403
    assert api.json()["error"] == "FORBIDDEN"

    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued"

    entries = invoke(ctx, "audit", {"limit": 20})["entries"]
    denials = [e for e in entries if e["outcome"] == "denied"]
    assert denials and denials[0]["principal"] == "agent", (
        "an unauthenticated web request must be audited as an agent, not an operator"
    )


def test_token_query_param_sets_cookie_and_is_dropped(env):
    cfg, ctx, store = env
    from promedia.web.app import COOKIE_NAME

    client = TestClient(create_app_for(cfg, store), follow_redirects=False)
    response = client.get(f"/?token={store.operator_token()}")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert COOKIE_NAME in response.cookies


def test_wrong_token_is_not_operator(env, media_file):
    cfg, ctx, store = env
    from promedia.web.app import COOKIE_NAME

    post_id = _seed_post(ctx, media_file)
    client = TestClient(create_app_for(cfg, store))
    client.cookies.set(COOKIE_NAME, "not-the-real-token")
    response = client.post(f"/posts/{post_id}/approve", data={"decision": "approved"})
    assert response.status_code == 403
    assert "FORBIDDEN" in response.text


def test_server_refuses_approval_of_blocked_asset(env, media_file):
    """AC-2: refusal is server-side. Hiding the control is not a control."""
    cfg, ctx, store = env
    post_id = _seed_post(ctx, media_file, declaration=declaration_uncleared())
    client = operator_client(cfg, store)

    page = client.get(f"/posts/{post_id}").text
    assert "BLOCKED" in page
    assert "disabled" in page  # the control is also disabled, but that is not the control

    response = client.post(f"/posts/{post_id}/approve", data={"decision": "approved"})
    assert response.status_code == 403
    assert "RIGHTS_BLOCKED" in response.text

    detail = invoke(ctx, "post", {"post_id": post_id})
    assert detail["status"] == "queued", "a refused approval must not change state"


def test_full_approval_and_publish_flow(env, media_file):
    cfg, ctx, store = env
    post_id = _seed_post(ctx, media_file)
    client = operator_client(cfg, store, follow_redirects=False)

    approve = client.post(f"/posts/{post_id}/approve", data={"decision": "approved"})
    assert approve.status_code == 303

    publish = client.post(f"/posts/{post_id}/publish")
    assert publish.status_code == 303

    publications = invoke(ctx, "publications", {})["publications"]
    assert len(publications) == 1
    assert publications[0]["simulated"] is True


def test_ui_without_operator_token_cannot_approve(tmp_path, monkeypatch, media_file):
    """Fail closed: no token in the store means the UI has agent authority only."""
    store_path = tmp_path / "empty-creds.json"
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(store_path))
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})

    from promedia.core.credentials import CredentialStore

    store = CredentialStore(store_path)  # deliberately no operator token
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=operator("seed"))
    post_id = _seed_post(ctx, media_file)

    client = TestClient(create_app_for(cfg, store))
    response = client.post(f"/posts/{post_id}/approve", data={"decision": "approved"})
    assert response.status_code == 403
    assert "FORBIDDEN" in response.text
    conn.close()


def test_capabilities_page_lists_both_surfaces(env):
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    html = client.get("/ops").text
    assert "publish-post" in html
    assert "operator" in html
    assert "python -m promedia" in html


def test_error_page_for_missing_post(env):
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    response = client.get("/posts/post_missing")
    assert response.status_code == 404
    assert "NOT_FOUND" in response.text
