"""T-034 — the generic operation forms.

The gap this covers: /ops rendered all 29 capabilities as a table of text, and
the only operable HTML in the app was /posts/{id}. An operator could read about
25 of their own capabilities in the browser and run none of them.

Tested as an operator would use it — fetch the page, fill the form, submit it —
and then, separately, tested as an attacker would: GET the dangerous ones, put
secrets in the URL, submit from another origin, submit without authority.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from promedia.core import db
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke, load_operations
from tests.conftest import declaration_original, make_config

OPERATIONS = sorted(load_operations())


@pytest.fixture
def env(tmp_path, monkeypatch):
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


def create_app_for(cfg, store):
    from promedia.web.app import create_app

    return create_app(cfg, store=store)


def operator_client(cfg, store, **kwargs):
    from promedia.web.app import COOKIE_NAME

    client = TestClient(create_app_for(cfg, store), **kwargs)
    client.cookies.set(COOKIE_NAME, store.operator_token())
    return client


def agent_client(cfg, store, **kwargs):
    """A browser that never opened the startup URL. Agent authority only."""
    return TestClient(create_app_for(cfg, store), **kwargs)


def ingest_as_agent(ctx, media_file):
    """Seed an asset whose declaration is an agent's PROPOSAL (DR-011).

    Ingesting through the operator context would record declared_by_kind
    'operator' and make attest-declaration a no-op, so a refused attestation
    would be indistinguishable from a successful one.
    """
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("test-agent"))
    return invoke(
        as_agent, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )["asset_id"]


def attesting_principal(ctx, asset_id):
    """Who authored the asset's latest rights declaration."""
    row = ctx.conn.execute(
        "SELECT declared_by_kind FROM rights_declarations WHERE asset_id = ?"
        " ORDER BY declared_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return row["declared_by_kind"]


# --- AC-1: every operation has a form, generated from the registry -----------


@pytest.mark.parametrize("name", OPERATIONS)
def test_every_operation_has_a_form_page(env, name):
    """AC-1. Not 'a route exists' — a form, with a control per declared parameter.

    Parametrised over the registry rather than a hand-written list, so a new
    capability fails here until it is reachable, the same way test_parity.py
    treats the two surfaces.
    """
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    response = client.get(f"/ops/{name}")

    assert response.status_code == 200, f"'{name}' has no form page"
    html = response.text
    assert f'action="/ops/{name}"' in html, f"'{name}' renders no form that submits anywhere"
    assert 'method="post"' in html, f"'{name}' would submit over GET"

    op = load_operations()[name]
    for p in op.params:
        assert f'name="{p.name}"' in html, (
            f"'{name}' declares parameter '{p.name}' but the form has no control for it"
        )


def test_capabilities_index_links_every_operation(env):
    cfg, ctx, store = env
    html = operator_client(cfg, store).get("/ops").text
    for name in OPERATIONS:
        assert f'href="/ops/{name}"' in html, f"'{name}' is listed but not reachable from the list"


def test_form_page_states_the_authority_the_registry_will_enforce(env):
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    assert "operator" in client.get("/ops/publish-post").text
    # And the notice is about THIS session, not a static label.
    warned = agent_client(cfg, store).get("/ops/publish-post").text
    assert "agent authority" in warned
    assert "agent authority" not in client.get("/ops/publish-post").text


def test_unknown_operation_is_not_found(env):
    cfg, ctx, store = env
    response = operator_client(cfg, store).get("/ops/no-such-operation")
    assert response.status_code == 404
    assert "NOT_FOUND" in response.text


# --- AC-2: submitting runs the real operation through the guarded path -------


def test_submitting_a_read_operation_shows_its_result(env):
    cfg, ctx, store = env
    response = operator_client(cfg, store).post("/ops/storage-status", data={})
    assert response.status_code == 200
    assert "Result" in response.text
    assert "ceiling_bytes" in response.text


def test_submitting_a_form_performs_the_real_side_effect(env, media_file):
    """AC-2. The whole point: an operator can now ingest from the browser.

    Asserted against the database through the CLI-shared operation layer, not
    against the page — a page can say anything.
    """
    cfg, ctx, store = env
    client = operator_client(cfg, store)

    response = client.post(
        "/ops/ingest",
        data={
            "source_path": str(media_file),
            "declaration": json.dumps(declaration_original()),
        },
    )
    assert response.status_code == 200, response.text

    assets = invoke(ctx, "list-assets", {})["assets"]
    assert len(assets) == 1
    assert assets[0]["original_filename"] == media_file.name
    assert assets[0]["id"] in response.text


def test_a_json_parameter_round_trips_from_a_textarea(env, media_file):
    """`declaration` is type json. A textarea delivers a string; the registry coerces."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    client.post(
        "/ops/ingest",
        data={"source_path": str(media_file), "declaration": json.dumps(declaration_original())},
    )
    asset_id = invoke(ctx, "list-assets", {})["assets"][0]["id"]
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["declaration"]["authorship"] == "operator_original"


def test_malformed_json_is_a_validation_error_not_a_traceback(env, media_file):
    cfg, ctx, store = env
    response = operator_client(cfg, store).post(
        "/ops/ingest", data={"source_path": str(media_file), "declaration": "{not json"}
    )
    assert response.status_code == 400
    assert "VALIDATION" in response.text
    assert "declaration" in response.text
    assert invoke(ctx, "list-assets", {})["assets"] == []


def test_missing_required_parameter_names_the_parameter(env):
    cfg, ctx, store = env
    response = operator_client(cfg, store).post("/ops/asset", data={"asset_id": ""})
    assert response.status_code == 400
    assert "asset_id" in response.text


def test_optional_parameter_left_blank_uses_its_default(env, media_file):
    """An empty text input submits "", which must mean 'omitted', not ''."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    response = client.post(
        "/ops/ingest",
        data={
            "source_path": str(media_file),
            "declaration": json.dumps(declaration_original()),
            "derived_from": "",  # optional; blank must not become a parent id
        },
    )
    assert response.status_code == 200, response.text
    assert len(invoke(ctx, "list-assets", {})["assets"]) == 1


def test_agent_authority_is_refused_by_the_operation_layer(env, media_file):
    """AC-2. F-2 is not re-decided here, so the form cannot weaken it."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    assert attesting_principal(ctx, asset_id) == "agent"  # the precondition is real

    response = agent_client(cfg, store).post(
        "/ops/attest-declaration", data={"asset_id": asset_id}
    )
    assert response.status_code == 403
    assert "FORBIDDEN" in response.text

    assert attesting_principal(ctx, asset_id) == "agent", (
        "the form route performed an operator action for an agent principal"
    )


def test_the_denial_is_audited_as_an_agent_attempt(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    agent_client(cfg, store).post("/ops/attest-declaration", data={"asset_id": asset_id})

    entries = invoke(ctx, "audit", {"limit": 20})["entries"]
    denials = [e for e in entries if e["outcome"] == "denied"]
    assert denials and denials[0]["principal"] == "agent"


def test_rights_gate_still_refuses_through_the_form(env, media_file):
    """F-3 has no override path, and adding a form must not have created one."""
    cfg, ctx, store = env
    from tests.conftest import declaration_unknown

    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_unknown()}
    )
    invoke(ctx, "determine-rights", {"asset_id": asset["asset_id"]})
    post = invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset["asset_id"], "body": "hi"},
    )

    client = operator_client(cfg, store)
    data = {"post_id": post["post_id"], "decision": "approved"}

    # T-035: approve-post now confirms first, so the single POST that used to
    # execute returns the decision screen having run nothing. The gate is
    # asserted on the CONFIRMED submission, which is the stronger claim — the
    # refusal survives the operator explicitly saying yes.
    from tests.test_decision_context import confirmation

    first = client.post("/ops/approve-post", data=data)
    assert first.status_code == 200
    assert invoke(ctx, "post", {"post_id": post["post_id"]})["status"] == "queued"

    response = client.post("/ops/approve-post", data={**data, **confirmation(first)})
    assert response.status_code == 403
    assert invoke(ctx, "post", {"post_id": post["post_id"]})["status"] == "queued"


def test_entity_lock_is_taken_through_the_form_route(env, media_file):
    """C-19 is enforced in invoke(), so the form inherits it. Proven, not assumed."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    db.acquire_lock(
        ctx.conn, "asset", asset_id,
        task_id="held", agent="agent-beta", model="m", ttl_minutes=30,
    )

    response = operator_client(cfg, store).post(
        "/ops/attest-declaration", data={"asset_id": asset_id}
    )
    assert "ENTITY_LOCKED" in response.text
    assert "agent-beta" in response.text


# --- AC-3: a GET executes nothing -------------------------------------------


def _seed_full_slice(ctx, media_file):
    """A real account, a PERMITTED asset with sealed provenance, and a queued post.

    Needed so the AC-3 probes below can carry VALID parameters. The first
    version of this test passed 'probe' for every id, which meant a GET that
    really did execute was stopped by validation rather than by the design —
    the test passed under a deliberate sabotage that made GET run the
    operation. Valid ids are what make the assertion mean anything.
    """
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset = invoke(
        ctx, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )
    invoke(ctx, "determine-rights", {"asset_id": asset["asset_id"]})
    sealed = invoke(ctx, "seal-provenance", {"asset_id": asset["asset_id"]})
    post = invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset["asset_id"], "body": "seeded"},
    )
    # A real project with a real edit, so render-project's probe is a call that
    # would genuinely render rather than one that fails validation first.
    project = invoke(ctx, "create-project", {"title": "seeded project"})
    invoke(ctx, "set-edl", {
        "project_id": project["project_id"],
        "edl": {"aspect": "landscape_720",
                "clips": [{"asset_id": asset["asset_id"], "start": 0, "end": 1}]},
    })
    # R-006. A real renders row, so delete-render's probe would genuinely find
    # and delete something rather than fail on NotFound before proving
    # anything — same reasoning as every other id in this dict. Written
    # directly (like tests/test_render_storage.py's legacy-render case)
    # rather than through a real ffmpeg encode, which delete-render does not
    # need in order to act.
    render_id = "rnd_probe"
    render_output = media_file.parent / "probe-render.mp4"
    render_output.write_bytes(b"a render for the GET-must-not-execute probe")
    ctx.conn.execute(
        "INSERT INTO renders (id, project_id, edl_version, output_path, quality,"
        " width, height, duration_seconds, byte_size, substitutions, rendered_by, rendered_at)"
        " VALUES (?, ?, 1, ?, 'fast', 1280, 720, 1.0, 123, NULL, 'probe', ?)",
        (render_id, project["project_id"], str(render_output), db.iso()),
    )
    # T-068 brand kits (DR-021). A real kit, so update/delete-brand-kit's own
    # GET probe would genuinely succeed rather than fail on NotFound first —
    # same reasoning as every other id in this dict.
    brand_kit_id = invoke(
        ctx, "create-brand-kit",
        {"name": "probe kit", "logo_asset_id": asset["asset_id"]},
    )["brand_kit_id"]
    return {
        "account_id": account["account_id"],
        "asset_id": asset["asset_id"],
        "post_id": post["post_id"],
        "provenance_id": sealed["provenance_id"],
        "project_id": project["project_id"],
        "render_id": render_id,
        "brand_kit_id": brand_kit_id,
        "source_path": str(media_file),
        # A REAL artefact, so restore-permanent-set's probe is a call that would
        # genuinely do something. A path to nothing would make the GET fail on
        # NotFound and the test would prove only that bad input does nothing —
        # which is the exact weakness sabotage 1 exposed in this file's first
        # version. (The restore would still refuse this non-empty database, but
        # the refusal happens after the artefact is read and verified, so a GET
        # that executes is visible in the audit log either way.)
        "source": str(_seeded_artefact(ctx, media_file)),
    }


def _seeded_artefact(ctx, media_file):
    """Write a genuine backup artefact next to the media file."""
    path = media_file.parent / "probe-artefact.json"
    invoke(ctx, "export-permanent-set", {"destination": str(path)})
    return path


def _valid_query_for(op, seeded):
    """Parameters that would make `op` SUCCEED, so a GET that executes is visible."""
    values = dict(seeded)
    values.update({
        "declaration": json.dumps(declaration_original()),
        "platform": "x",
        "handle": "a-second-handle",
        "body": "a GET must not create this",
        "kind": "third_party_material_suspected",
        "produced_by": "operator",
        "decision": "approved",
        "confidence": "0.9",
        "model_id": "m",
        "limit": "5",
        "status": "queued",
        "derived_from": seeded["asset_id"],
        "scheduled_at": "",
        # T-042 media production. Values that would genuinely SUCCEED, which is
        # the whole point of this probe: parameters that merely fail validation
        # would prove only that bad input does nothing.
        "title": "a GET must not create this project",
        "edl": json.dumps({
            "aspect": "landscape_720",
            "clips": [{"asset_id": seeded["asset_id"], "start": 0, "end": 1}],
        }),
        "quality": "fast",
        "version": "1",
        "note": "a GET must not record this edit",
        "expected_version": "2",
        # T-046 acquisition, T-048 providers/spend ledger. Values that would
        # genuinely succeed if this were ever actually invoked, matching
        # every other entry in this dict — even though the whole point of
        # the test is that a GET never reaches invoke() at all.
        "url": "https://example.com/video",
        "capability": "transcription",
        "provider": "test-provider",
        "amount_usd": "1.00",
        "approved": "true",
        "input_ref": seeded["asset_id"],
        # T-068 brand kits (DR-021). Values that would genuinely SUCCEED for
        # create/list/update/delete-brand-kit, same reasoning as every other
        # entry here — a GET that executes would otherwise be invisible
        # behind a parameter that merely fails validation.
        "name": "a GET must not create this brand kit",
        "logo_asset_id": seeded["asset_id"],
        "primary_color": "#112233",
        "secondary_color": "#445566",
        "font_family": "Inter",
    })
    query = {}
    for p in op.params:
        if p.sensitive:
            continue  # T-024 refuses these in a query string; covered separately
        assert p.name in values, (
            f"'{op.name}.{p.name}' has no valid probe value — add one, or this test"
            " silently stops proving anything for that operation"
        )
        query[p.name] = values[p.name]
    return query


def _observable_state(ctx):
    return {
        "assets": invoke(ctx, "list-assets", {})["assets"],
        "posts": invoke(ctx, "list-posts", {})["posts"],
        "accounts": invoke(ctx, "list-accounts", {})["accounts"],
        "publications": invoke(ctx, "publications", {})["publications"],
        "provenance": invoke(ctx, "list-provenance", {})["records"],
        "locks": db.list_locks(ctx.conn),
        "audit": invoke(ctx, "audit", {"limit": 100})["entries"],
    }


@pytest.mark.parametrize(
    "name", [n for n, op in sorted(load_operations().items()) if op.mutates]
)
def test_get_of_a_mutating_form_changes_nothing(env, name, media_file):
    """AC-3. T-025 refuses mutating operations over GET on /api/op/{name}.

    /ops/{name} answers GET by design — it has to, that is the form — so the
    property must be proven rather than inherited: rendering reaches the
    registry's metadata and never invoke(). Each mutating operation is fetched
    with parameters that WOULD SUCCEED in the query string, which is the shape a
    prefetcher, a link preview or a pasted URL would produce.
    """
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    seeded = _seed_full_slice(ctx, media_file)
    op = load_operations()[name]

    before = _observable_state(ctx)
    response = client.get(f"/ops/{name}", params=_valid_query_for(op, seeded))
    assert response.status_code == 200
    assert _observable_state(ctx) == before, f"GET /ops/{name} changed state"


def test_get_of_the_form_writes_no_audit_entry(env):
    """The sharper version of the above: invoke() audits every operator-authority
    attempt, so an empty audit log proves the render never reached it."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    for name in OPERATIONS:
        client.get(f"/ops/{name}")
    assert invoke(ctx, "audit", {"limit": 50})["entries"] == []


# --- AC-4 / AC-5: secrets must not travel in the URL -------------------------


def test_sensitive_parameter_renders_masked(env):
    """AC-4. connect-account.secret is the only sensitive parameter today; the
    assertion is driven off the registry so a second one is covered for free."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    for name, op in load_operations().items():
        sensitive = [p for p in op.params if p.sensitive]
        if not sensitive:
            continue
        html = client.get(f"/ops/{name}").text
        for p in sensitive:
            assert f'type="password" id="f-{p.name}"' in html, (
                f"'{name}.{p.name}' is sensitive but renders as a visible field"
            )


def test_sensitive_parameter_is_refused_in_the_query_string(env):
    """AC-4 / T-024. Both methods: the harm is that it reached the URL at all."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)

    got = client.get("/ops/connect-account", params={"secret": "canary-9f3a"})
    assert got.status_code == 400
    assert "sensitive" in got.text

    posted = client.post(
        "/ops/connect-account?secret=canary-9f3a",
        data={"platform": "x", "handle": "me"},
    )
    assert posted.status_code == 400
    assert invoke(ctx, "list-accounts", {})["accounts"] == []


def test_a_submitted_secret_is_not_echoed_back_into_the_form(env):
    """AC-4. A re-populated password field would put the secret in every
    subsequent response body — the request-side rule applied to the response."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    response = client.post(
        "/ops/connect-account",
        data={"platform": "x", "handle": "me", "secret": "canary-9f3a"},
    )
    assert response.status_code == 200, response.text
    assert "canary-9f3a" not in response.text

    # Nor on the error path, which re-renders the same form.
    failed = client.post(
        "/ops/connect-account", data={"platform": "", "handle": "me", "secret": "canary-9f3a"}
    )
    assert "canary-9f3a" not in failed.text


def test_the_secret_reaches_the_store_and_nothing_else(env):
    """The canary must be absent from the database and the audit log (N14's rule)."""
    cfg, ctx, store = env
    operator_client(cfg, store).post(
        "/ops/connect-account",
        data={"platform": "x", "handle": "me", "secret": "canary-9f3a"},
    )
    accounts = invoke(ctx, "list-accounts", {})["accounts"]
    assert len(accounts) == 1

    dump = "\n".join(ctx.conn.iterdump())
    assert "canary-9f3a" not in dump
    entries = json.dumps(invoke(ctx, "audit", {"limit": 50})["entries"])
    assert "canary-9f3a" not in entries


def test_operator_token_is_refused_in_the_query_string(env):
    """AC-5 / N9. The token grants publish authority over every account, so the
    rule that applies to a platform credential applies to it with more force."""
    cfg, ctx, store = env
    anonymous = TestClient(create_app_for(cfg, store))
    token = store.operator_token()

    got = anonymous.get(f"/ops/publish-post?token={token}")
    assert got.status_code == 400
    assert "X-ProMedia-Token" in got.text

    posted = anonymous.post(f"/ops/attest-declaration?token={token}", data={"asset_id": "as_x"})
    assert posted.status_code == 400
    assert invoke(ctx, "audit", {"limit": 10})["entries"] == []


def test_cross_origin_submission_is_refused(env, media_file):
    """T-025. guarded() is the same call the /posts routes make."""
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    asset_id = ingest_as_agent(ctx, media_file)

    response = client.post(
        "/ops/attest-declaration",
        data={"asset_id": asset_id},
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403
    assert attesting_principal(ctx, asset_id) == "agent"


# --- the surface must not have grown a capability ---------------------------


def test_the_form_routes_add_no_capability(env):
    """S4/F-1 in the other direction: this task adds routes, not operations.

    A form route that could run something the registry does not list would be a
    single-surface capability the parity gate cannot see — the T-027 shape of
    defect. The set reachable at /ops/{name} must be exactly the registry.
    """
    cfg, ctx, store = env
    client = operator_client(cfg, store)
    listed = {op["name"] for op in client.get("/api/ops").json()["operations"]}
    assert listed == set(OPERATIONS)
    for name in listed:
        assert client.get(f"/ops/{name}").status_code == 200
    assert client.get("/ops/publish_post").status_code == 404  # underscore, not a real name
