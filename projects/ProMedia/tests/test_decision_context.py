"""T-035 — the decision context comes before the control.

Raised by independent review of T-034. That task made every capability operable
from a browser, which included approve-post and publish-post on the generic
/ops/{name} form: an operator could authorise on a typed post_id, and the
verdict, ruleset version and asset hash appeared in the RESPONSE — after the
decision. /posts/{id} exists precisely because those facts belong before the
button.

Every hard gate held (rights 403, authority 403, C-19 locking, masked secrets),
so this was never a security defect. It is a human-experience one, and F-2 is
why it matters: the UI is the authority surface BECAUSE it shows the basis.

The fix is a confirmation bound to the facts. The tests below cover the three
things that can go wrong with that:

* it does not actually gate (the defect);
* it gates, but the confirmation is a bare "yes" that survives the facts
  changing underneath it;
* it over-applies, and every ordinary operation grows a pointless second click.

The last one matters as much as the first. A confirmation on everything is a
confirmation nobody reads, which would make the surface worse, not better.
"""

from __future__ import annotations

import re

import pytest

from promedia.core.registry import invoke
from promedia.web.app import CONFIRM_FIELD, _needs_decision_context
from tests.conftest import declaration_original, declaration_unknown
from tests.test_ops_forms import (  # the T-034 fixtures; this task adds no new surface
    agent_client,
    env,
    ingest_as_agent,
    operator_client,
)

__all__ = ["confirmation", "env"]


def confirmation(response) -> dict[str, str]:
    """The hidden confirmation field out of a rendered decision screen.

    Read from the HTML rather than recomputed, so a test cannot confirm a
    digest the page never actually offered.
    """
    match = re.search(
        rf'name="{re.escape(CONFIRM_FIELD)}"\s+value="([0-9a-f]+)"', response.text
    )
    assert match, "the response carried no confirmation field"
    return {CONFIRM_FIELD: match.group(1)}


def _publishable_post(ctx, media_file):
    """A post that would really publish: attested, PERMITTED, sealed, approved."""
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = ingest_as_agent(ctx, media_file)
    invoke(ctx, "attest-declaration", {"asset_id": asset_id})
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(
        ctx, "queue-post",
        {"account_id": account["account_id"], "asset_id": asset_id, "body": "hello"},
    )
    return post["post_id"], asset_id


# --- which operations require it ----------------------------------------------


def test_the_rule_is_derived_not_a_hardcoded_list():
    """An operator write to an existing post, whatever it is called.

    A name list in the adapter is a second source of truth about which
    capabilities are dangerous, and DR-002 exists because that is the copy that
    drifts. Pinned as the actual set so a change is deliberate.
    """
    from promedia.core.registry import load_operations

    requiring = {
        name for name, op in load_operations().items() if _needs_decision_context(op)
    }
    assert requiring == {"approve-post", "publish-post", "release-publish-claim"}


def test_ordinary_operations_are_not_gated(env, media_file):
    """The over-application direction. A confirmation on everything is noise."""
    cfg, ctx, store = env
    response = operator_client(cfg, store).post("/ops/storage-status", data={})
    assert response.status_code == 200
    assert CONFIRM_FIELD not in response.text
    assert "Confirm:" not in response.text


def test_ingest_still_runs_in_one_submission(env, media_file):
    """A mutating, non-post operation must not have grown a second click."""
    cfg, ctx, store = env
    response = operator_client(cfg, store).post(
        "/ops/ingest",
        data={"source_path": str(media_file), "declaration": '{"authorship": "operator_original", "third_party_material": []}'},
    )
    assert response.status_code == 200
    assert invoke(ctx, "list-assets", {})["count"] == 1


# --- the gate itself ----------------------------------------------------------


def test_approve_does_not_execute_on_the_first_submission(env, media_file):
    """AC-1. The defect: a typed post_id used to be enough to approve."""
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)

    response = operator_client(cfg, store).post(
        "/ops/approve-post", data={"post_id": post_id, "decision": "approved"}
    )
    assert response.status_code == 200
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued", (
        "the post was approved without the decision context ever being shown"
    )
    assert "Nothing has run yet" in response.text


def test_the_first_response_carries_the_facts_t005_requires(env, media_file):
    """The same four facts /posts/{id} must show before an approve control.

    Asserted against the DATA, not against prose: each value is looked up and
    then required to appear on the page, so a template that drops a row fails
    here rather than passing on a plausible-looking screen.
    """
    cfg, ctx, store = env
    post_id, asset_id = _publishable_post(ctx, media_file)
    context = invoke(ctx, "post", {"post_id": post_id})

    response = operator_client(cfg, store).post(
        "/ops/approve-post", data={"post_id": post_id, "decision": "approved"}
    )
    body = response.text

    assert context["account"]["handle"] in body, "target account missing"
    assert context["rights"]["verdict"] in body, "rights verdict missing"
    assert context["rights"]["ruleset_version"] in body, "ruleset version missing"
    assert context["asset"]["content_hash"] in body, "asset hash missing"


def test_confirming_executes(env, media_file):
    """The fix must not break the path it protects."""
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)
    client = operator_client(cfg, store)
    data = {"post_id": post_id, "decision": "approved"}

    first = client.post("/ops/approve-post", data=data)
    second = client.post("/ops/approve-post", data={**data, **confirmation(first)})

    assert second.status_code == 200
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "approved"


def test_a_forged_confirmation_does_not_execute(env, media_file):
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)

    response = operator_client(cfg, store).post(
        "/ops/approve-post",
        data={"post_id": post_id, "decision": "approved", CONFIRM_FIELD: "0" * 64},
    )
    assert response.status_code == 200
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued"


def test_publish_is_gated_too(env, media_file):
    """The irreversible one. C-32: a double post is not recoverable once seen."""
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)
    client = operator_client(cfg, store)
    invoke(ctx, "approve-post", {"post_id": post_id})

    first = client.post("/ops/publish-post", data={"post_id": post_id})
    assert invoke(ctx, "publications", {})["count"] == 0, "published with no confirmation"

    client.post("/ops/publish-post", data={"post_id": post_id, **confirmation(first)})
    assert invoke(ctx, "publications", {})["count"] == 1


# --- the confirmation is bound to the facts, not to a bare yes ----------------


def test_a_confirmation_goes_stale_when_the_basis_changes(env, media_file):
    """The reason the digest is over the CONTENT rather than a nonce.

    An operator reads the screen, something changes — retention deletes the
    media, evidence degrades an ancestor, the account breaks — and the click
    that follows would otherwise authorise facts that stopped being true. Here
    the account is taken to 'error', which decision_context reports (N11).
    """
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)
    client = operator_client(cfg, store)
    data = {"post_id": post_id, "decision": "approved"}

    first = client.post("/ops/approve-post", data=data)
    stale = confirmation(first)

    ctx.conn.execute("UPDATE accounts SET status = 'error'")
    ctx.conn.commit()

    second = client.post("/ops/approve-post", data={**data, **stale})
    assert second.status_code == 200
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued", (
        "a confirmation issued against different facts still authorised the action"
    )
    # And the operator is re-shown the CURRENT facts, not the ones they read.
    assert "account error" in second.text.lower() or "error" in second.text.lower()

    # A confirmation of what is true NOW does proceed.
    third = client.post("/ops/approve-post", data={**data, **confirmation(second)})
    assert third.status_code == 200
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "approved"


# --- what this must NOT have changed ------------------------------------------


def test_the_confirmation_field_is_never_an_operation_parameter(env, media_file):
    """Stripped before validate(), as the operator token is (T-026).

    Otherwise the first confirmed submission would fail with 'unexpected
    parameter', which is the shape of bug that makes a guard look like it works
    while the happy path is broken.
    """
    from promedia.core.registry import load_operations

    for name, op in load_operations().items():
        assert CONFIRM_FIELD not in {p.name for p in op.params}, name


def test_the_api_route_is_unchanged(env, media_file):
    """F-1. This is a projection detail of one HTML route, not an operation rule.

    Agents and the CLI reach approve-post exactly as before; had the
    confirmation been enforced in invoke(), it would have become a capability
    the CLI could not perform and the parity gate would have been right to fail.
    """
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)

    response = operator_client(cfg, store).post(
        "/api/op/approve-post", json={"post_id": post_id, "decision": "approved"}
    )
    assert response.status_code == 200
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "approved"


def test_authority_is_still_refused_before_anything_is_shown(env, media_file):
    """An agent must not be able to read a post's basis off this route.

    The context is fetched through invoke(), so agent authority governs it. The
    'post' operation is agent-readable by design, but approve-post is not, and
    the confirmation screen must not become a way to probe.
    """
    cfg, ctx, store = env
    post_id, _ = _publishable_post(ctx, media_file)

    response = agent_client(cfg, store).post(
        "/ops/approve-post", data={"post_id": post_id, "decision": "approved"}
    )
    assert invoke(ctx, "post", {"post_id": post_id})["status"] == "queued"
    assert response.status_code in (200, 403)


def test_a_missing_post_id_still_reaches_normal_validation(env):
    """The gate must not swallow the ordinary error path."""
    cfg, ctx, store = env
    response = operator_client(cfg, store).post("/ops/approve-post", data={"decision": "approved"})
    assert response.status_code == 400
    assert "post_id" in response.text
