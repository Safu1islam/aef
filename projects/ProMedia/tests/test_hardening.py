"""T-023, T-024, T-025 — follow-up hardening from the independent review.

Each test is written from the reviewer's reproduction, so each fails without
its fix.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promedia.core import db
from promedia.core.principal import operator
from promedia.core.registry import Context, invoke, load_operations
from promedia.web.app import COOKIE_NAME, create_app
from tests.conftest import declaration_original, make_config

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    store_path = tmp_path / "creds.json"
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(store_path))
    # The origin guard now derives its baseline from config rather than from the
    # request's own Host header (N8), so the test app must be configured with the
    # host TestClient actually uses.
    cfg = make_config(
        tmp_path,
        **{"publishing.allow_simulation": True, "web.host": "testserver", "web.port": 80},
    )

    from promedia.core.credentials import CredentialStore

    store = CredentialStore(store_path)
    store.ensure_operator_token()
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=operator("op"))
    yield cfg, ctx, store
    conn.close()


def operator_client(cfg, store, **kwargs):
    client = TestClient(create_app(cfg, store=store), **kwargs)
    client.cookies.set(COOKIE_NAME, store.operator_token())
    return client


def _ready_post(ctx, media_file):
    """An approvable post: account connected, asset attested, provenance sealed."""
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "s"})
    asset_id = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )["asset_id"]
    invoke(ctx, "attest-declaration", {"asset_id": asset_id})
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    return invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"},
    )["post_id"]


# --- T-023: reconnect must preserve the account id ----------------------------


def test_reconnect_preserves_account_id(env):
    """I2: rotation minted a new id and deleted the old row."""
    cfg, ctx, store = env
    first = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "old"})
    second = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "new"})

    assert second["account_id"] == first["account_id"]
    assert second["reconnected"] is True
    assert first["reconnected"] is False

    rows = ctx.conn.execute("SELECT id FROM accounts").fetchall()
    assert len(rows) == 1

    from promedia.core.credentials import CredentialStore

    assert CredentialStore(store.path).get("x:me") == "new"


def test_reconnect_with_posts_referencing_the_account(env, media_file):
    """The case that previously crashed on ON DELETE RESTRICT."""
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "old"})
    asset_id = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )["asset_id"]
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})

    rotated = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "rotated"})
    assert rotated["account_id"] == account["account_id"]

    posts = invoke(ctx, "list-posts", {})["posts"]
    assert posts[0]["account_id"] == account["account_id"]


def test_different_handles_are_different_accounts(env):
    cfg, ctx, store = env
    a = invoke(ctx, "connect-account", {"platform": "x", "handle": "one", "secret": "s"})
    b = invoke(ctx, "connect-account", {"platform": "x", "handle": "two", "secret": "s"})
    assert a["account_id"] != b["account_id"]


# --- T-024: secrets never in argv or query strings ----------------------------


def test_sensitive_param_offers_only_out_of_band_input():
    """I4: `--secret VALUE` put a credential in argv and shell history.

    `--secret` is still registered, but only so it can be refused with a useful
    message (N12) — it never supplies a value. The value comes from stdin or a
    file.
    """
    from promedia.cli import _build_parser

    ops = load_operations()
    parser = _build_parser(ops)
    subparsers = next(
        a for a in parser._actions if isinstance(getattr(a, "choices", None), dict)
    )
    connect = subparsers.choices["connect-account"]
    flags = {opt for action in connect._actions for opt in action.option_strings}

    assert "--secret-stdin" in flags
    assert "--secret-file" in flags

    inline = next(a for a in connect._actions if "--secret" in a.option_strings)
    assert inline.dest == "secret__inline", "the inline flag must not feed the parameter"
    import argparse as _argparse

    assert inline.help == _argparse.SUPPRESS, "the inline flag must not be advertised in help"

    # And the parameter itself is marked sensitive in the contract.
    assert next(p for p in ops["connect-account"].params if p.name == "secret").sensitive


def test_secret_supplied_via_stdin(tmp_path):
    env_vars = dict(os.environ)
    env_vars["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    env_vars["PROMEDIA_CREDENTIAL_STORE"] = str(tmp_path / "creds.json")
    env_vars["PYTHONPATH"] = str(REPO)

    sys.path.insert(0, str(REPO))
    from promedia.core.credentials import CredentialStore

    token = CredentialStore(tmp_path / "creds.json").ensure_operator_token()
    env_vars["PROMEDIA_OPERATOR_TOKEN"] = token

    proc = subprocess.run(
        [sys.executable, "-m", "promedia", "connect-account",
         "--platform", "x", "--handle", "me", "--secret-stdin", "--json"],
        input="from-stdin-secret\n", capture_output=True, text=True,
        cwd=str(REPO), env=env_vars, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "connected"
    assert "from-stdin-secret" not in proc.stdout
    assert CredentialStore(tmp_path / "creds.json").get("x:me") == "from-stdin-secret"


def test_secret_supplied_via_file(tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("from-a-file\n", encoding="utf-8")

    env_vars = dict(os.environ)
    env_vars["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    env_vars["PROMEDIA_CREDENTIAL_STORE"] = str(tmp_path / "creds.json")
    env_vars["PYTHONPATH"] = str(REPO)

    sys.path.insert(0, str(REPO))
    from promedia.core.credentials import CredentialStore

    env_vars["PROMEDIA_OPERATOR_TOKEN"] = CredentialStore(tmp_path / "creds.json").ensure_operator_token()

    proc = subprocess.run(
        [sys.executable, "-m", "promedia", "connect-account",
         "--platform", "x", "--handle", "me", "--secret-file", str(secret_file), "--json"],
        capture_output=True, text=True, cwd=str(REPO), env=env_vars, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert CredentialStore(tmp_path / "creds.json").get("x:me") == "from-a-file"
    assert secret_file.read_text(encoding="utf-8") == "from-a-file\n", "the file must not be modified"


def test_sensitive_param_refused_in_query_string(env):
    """I4: GET ...?secret=... previously returned 200 and stored the credential."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    response = client.post(
        "/api/op/connect-account?secret=QUERYSTRINGSECRET",
        data={"platform": "x", "handle": "me"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["parameter"] == "secret"

    accounts = invoke(ctx, "list-accounts", {})
    assert accounts["count"] == 0, "a refused call must not create an account"


def test_sensitive_flag_is_visible_in_the_contract(env):
    """An agent must be able to discover which parameters it may not inline."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    ops = {o["name"]: o for o in client.get("/api/ops").json()["operations"]}
    secret = next(p for p in ops["connect-account"]["params"] if p["name"] == "secret")
    assert secret["sensitive"] is True


# --- T-025: no state change over GET, no cross-origin -------------------------


@pytest.mark.parametrize("operation", ["publish-post", "approve-post", "connect-account"])
def test_state_changing_operations_refused_over_get(env, operation):
    """I5: GET /api/op/publish-post worked. Prefetchers fetch GETs."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    response = client.get(f"/api/op/{operation}?post_id=post_x")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"


def test_read_operations_still_work_over_get(env):
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    assert client.get("/api/op/status").status_code == 200
    assert client.get("/api/op/storage-status").status_code == 200


def test_cross_origin_post_refused(env, media_file):
    """I5: only SameSite=strict stood between a foreign page and a publish."""
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "s"})
    asset_id = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )["asset_id"]
    invoke(ctx, "attest-declaration", {"asset_id": asset_id})
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post_id = invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"},
    )["post_id"]

    client = operator_client(cfg, store, follow_redirects=False)
    response = client.post(
        f"/posts/{post_id}/approve",
        data={"decision": "approved"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert "cross-origin" in response.text

    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued"


def test_same_origin_post_allowed(env, media_file):
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "s"})
    asset_id = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )["asset_id"]
    invoke(ctx, "attest-declaration", {"asset_id": asset_id})
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post_id = invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"},
    )["post_id"]

    client = operator_client(cfg, store, follow_redirects=False)
    response = client.post(
        f"/posts/{post_id}/approve",
        data={"decision": "approved"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 303
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "approved"


# --- I3: the auth parameter must not reach the operation ----------------------


def test_operator_token_via_header_not_query_string(env):
    """N9: the token grants publish authority over every account.

    T-024 banned platform credentials from query strings for exactly the reason
    that applies here with more force. ?token= survives only as the one-time
    bootstrap on "/", which exchanges it for a cookie.
    """
    cfg, ctx, store = env
    from promedia.web.app import AUTH_HEADER

    client = TestClient(create_app(cfg, store=store))

    # Refused in the URL of a real operation...
    refused = client.get(f"/api/op/status?token={store.operator_token()}")
    assert refused.status_code == 400
    assert refused.json()["detail"]["use_header"] == AUTH_HEADER

    # ...accepted as a header.
    ok = client.get("/api/op/status", headers={AUTH_HEADER: store.operator_token()})
    assert ok.status_code == 200
    assert ok.json()["principal"]["kind"] == "operator"

    # ...and still works as the one-time bootstrap on "/".
    boot = TestClient(create_app(cfg, store=store), follow_redirects=False)
    assert boot.get(f"/?token={store.operator_token()}").status_code == 303


def test_token_in_query_cannot_publish(env, media_file):
    """N9, end to end: the reviewer published this way."""
    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    client = TestClient(create_app(cfg, store=store))
    response = client.post(f"/api/op/publish-post?token={store.operator_token()}",
                           data={"post_id": post_id})
    assert response.status_code == 400
    assert invoke(ctx, "publications", {})["count"] == 0


# --- N8: the origin baseline must not come from the request -------------------


def test_host_header_rebind_cannot_forge_same_origin(env, media_file):
    """N8: the check derived 'this app' from the client-supplied Host header.

    An attacker controlling both Host and Origin matched its own forgery, and a
    state change went through. This is the case the first round of tests missed.
    """
    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    client = operator_client(cfg, store, follow_redirects=False)

    response = client.post(
        f"/posts/{post_id}/approve",
        data={"decision": "approved"},
        headers={"Host": "evil.com", "Origin": "http://evil.com"},
    )
    assert response.status_code == 403
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued"


@pytest.mark.parametrize(
    "origin",
    [
        "http://testserver.evil.com",   # prefix extension
        "http://testserver:9999",       # wrong port
        "https://testserver",           # scheme swap changes the default port
        "null",
        "http://evil.example",
        "http://sub.testserver",
    ],
)
def test_origin_vectors_refused(env, media_file, origin):
    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    client = operator_client(cfg, store, follow_redirects=False)
    response = client.post(
        f"/posts/{post_id}/approve", data={"decision": "approved"},
        headers={"Origin": origin},
    )
    assert response.status_code == 403
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued"


# --- N10 / N11: account state ------------------------------------------------


def test_bare_reconnect_does_not_downgrade_a_working_account(env):
    """N10: preserving the id (T-023) made a pre-existing line destructive."""
    cfg, ctx, store = env
    invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "s"})
    again = invoke(ctx, "connect-account", {"platform": "x", "handle": "me"})
    assert again["status"] == "connected", "a bare reconnect must not mark a live account broken"

    row = ctx.conn.execute("SELECT status FROM accounts").fetchone()
    assert row["status"] == "connected"


def test_publish_refuses_an_account_that_is_not_connected(env, media_file):
    """N11: status existed but nothing consulted it."""
    from promedia.errors import ValidationError

    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    invoke(ctx, "approve-post", {"post_id": post_id})

    ctx.conn.execute("UPDATE accounts SET status = 'error'")
    with pytest.raises(ValidationError, match="not connected"):
        invoke(ctx, "publish-post", {"post_id": post_id})
    assert invoke(ctx, "publications", {})["count"] == 0


def test_account_status_visible_before_approval(env, media_file):
    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    ctx.conn.execute("UPDATE accounts SET status = 'error'")
    assert invoke(ctx, "post", {"post_id": post_id})["account"]["status"] == "error"


def test_handle_is_case_normalised(env):
    """N13: 'x/Case' and 'x/case' became two accounts with two credential refs."""
    cfg, ctx, store = env
    a = invoke(ctx, "connect-account", {"platform": "x", "handle": "MyHandle", "secret": "s"})
    b = invoke(ctx, "connect-account", {"platform": "X", "handle": "myhandle", "secret": "s"})
    assert a["account_id"] == b["account_id"]
    assert ctx.conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"] == 1


# --- N12: the inline-flag refusal must be deliberate, not accidental ----------


def test_inline_secret_flag_gives_actionable_error(tmp_path):
    """N12: previously blocked only by argparse abbreviation ambiguity."""
    env_vars = dict(os.environ)
    env_vars["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    env_vars["PROMEDIA_CREDENTIAL_STORE"] = str(tmp_path / "creds.json")
    env_vars["PYTHONPATH"] = str(REPO)

    proc = subprocess.run(
        [sys.executable, "-m", "promedia", "connect-account",
         "--platform", "x", "--handle", "me", "--secret", "hunter2", "--json"],
        capture_output=True, text=True, cwd=str(REPO), env=env_vars, timeout=120,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "--secret-stdin" in combined, "the error must say what to do instead"
    assert "hunter2" not in combined, "the rejected value must not be echoed"


# --- N14: assert the end-state guarantee, not only the entry points ----------


# --- T-031: the adapter's operation mapping must be authoritative -------------
#
# create_app captured `operations = load_operations()` and consulted it for the
# T-025 GET guard and the T-024 sensitive-parameter guard only, then called
# invoke(), which re-reads the global registry itself. So the adapter's mapping
# was not the authority on what exists: an operation absent from it STILL RAN,
# with both guards skipped. The obvious way to take a capability off the web
# surface disabled its protections instead — a trapdoor that fails in the least
# safe direction.
#
# These tests hide one operation from the adapter and assert the request is
# refused rather than executed. Each fails without the fix, and fails loudly:
# before it, the publish test below publishes a post over GET.


def _hide_from_the_web_adapter(monkeypatch, name: str) -> None:
    """Make one operation absent from the adapter's view of the registry.

    Patched at the adapter's own import site, so the operation remains in the
    registry ``invoke()`` reads. That asymmetry is the point: if the adapter is
    not the authority on what exists, the call reaches invoke() anyway.
    """
    import promedia.web.app as web_app

    full = dict(load_operations())
    monkeypatch.setattr(
        web_app,
        "load_operations",
        lambda: {k: v for k, v in full.items() if k != name},
    )


def test_hidden_operation_is_refused_not_executed(env, monkeypatch):
    """AC-1: absent from the adapter's mapping must mean refused, not unguarded."""
    cfg, ctx, store = env
    _hide_from_the_web_adapter(monkeypatch, "connect-account")
    client = operator_client(cfg, store)

    response = client.post(
        "/api/op/connect-account",
        data={"platform": "x", "handle": "me", "secret": "TRAPDOOR-SECRET"},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"] == "NOT_FOUND"
    assert invoke(ctx, "list-accounts", {})["count"] == 0, (
        "an operation the adapter does not expose must not have executed"
    )


def test_hidden_state_changing_operation_cannot_be_published_over_get(env, media_file, monkeypatch):
    """AC-1: hiding an operation must not take the T-025 GET guard with it.

    publish-post is irreversible and mutating, so a GET of it is refused (405).
    That refusal was reached only when the operation was present in the
    adapter's mapping — hide it and the guard was skipped, so the same GET
    published the post.
    """
    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    invoke(ctx, "approve-post", {"post_id": post_id})

    _hide_from_the_web_adapter(monkeypatch, "publish-post")
    client = operator_client(cfg, store)

    response = client.get(f"/api/op/publish-post?post_id={post_id}")

    assert response.status_code in (404, 405), response.text
    assert response.json()["error"] in ("NOT_FOUND", "METHOD_NOT_ALLOWED")
    assert invoke(ctx, "publications", {})["count"] == 0, (
        "a hidden publish-post must not be publishable over GET"
    )
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "approved"


def test_hidden_operation_cannot_take_a_secret_from_the_query_string(env, monkeypatch):
    """AC-1: nor may hiding an operation take the T-024 guard with it."""
    cfg, ctx, store = env
    _hide_from_the_web_adapter(monkeypatch, "connect-account")
    client = operator_client(cfg, store)

    response = client.post(
        "/api/op/connect-account?secret=HIDDEN-QUERYSTRING-SECRET",
        data={"platform": "x", "handle": "me"},
    )

    assert response.status_code in (400, 404), response.text
    assert invoke(ctx, "list-accounts", {})["count"] == 0

    from promedia.core.credentials import CredentialStore

    assert not CredentialStore(store.path).has("x:me"), (
        "a credential must not be stored from a query string by an unguarded path"
    )


def test_hidden_operation_is_refused_on_the_html_routes_too(env, media_file, monkeypatch):
    """AC-1: the same authority applies to the operator's own publish button.

    The HTML routes call run() with a fixed name, so they went straight to
    invoke() as well. Resolution lives in run(), so they are refused here too
    rather than each route remembering to check.
    """
    cfg, ctx, store = env
    post_id = _ready_post(ctx, media_file)
    invoke(ctx, "approve-post", {"post_id": post_id})

    _hide_from_the_web_adapter(monkeypatch, "publish-post")
    client = operator_client(cfg, store, follow_redirects=False)

    response = client.post(f"/posts/{post_id}/publish")

    assert response.status_code != 303, "a hidden operation must not have run"
    assert "NOT_FOUND" in response.text
    assert invoke(ctx, "publications", {})["count"] == 0


def test_web_adapter_and_invoke_cannot_disagree_about_what_exists(env):
    """AC-2: one mapping decides both what is listed and what may run.

    The other direction matters as much (F-1, S4): this must not become a way to
    hide a capability from the web surface. Every registered operation resolves
    here, and tests/test_parity.py invokes all of them on both surfaces.
    """
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    registry = load_operations()

    listed = {op["name"] for op in client.get("/api/ops").json()["operations"]}
    assert listed == set(registry), "the listing and the registry must not diverge"

    unknown = client.post("/api/op/no-such-operation", data={})
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "NOT_FOUND"

    # GET rather than POST: a mutating operation answers the T-025 guard with
    # 405, which is itself proof the adapter resolved it, and nothing changes
    # state. A read operation either runs or fails validation.
    for name in sorted(registry):
        response = client.get(f"/api/op/{name}")
        assert "unknown operation" not in response.text, (
            f"'{name}' is registered but not resolvable at /api/op/{name}"
        )


def test_secret_never_reaches_database_or_audit_log(env):
    """N14: the earlier tests checked the doors, not the room."""
    cfg, ctx, store = env
    canary = "SECRET-CANARY-7f3a9"
    invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": canary})

    dump = "".join(str(row) for row in ctx.conn.iterdump())
    assert canary not in dump

    assert canary not in json.dumps(invoke(ctx, "audit", {"limit": 100}))
    assert canary not in json.dumps(invoke(ctx, "list-accounts", {}))
    assert canary not in json.dumps(invoke(ctx, "status", {}))

    from promedia.core.credentials import CredentialStore

    assert CredentialStore(store.path).get("x:me") == canary
