"""Regression tests for the BLOCKING findings of the independent review (2026-08-08).

Each test is written from the reviewer's reproduced attack, not from the fix.
The suite passed 105 tests over four laundering paths because every test
exercised the honest caller; these exercise the adversarial one.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from promedia.core import storage
from promedia.core.db import iso, now
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke
from promedia.errors import Forbidden, LedgerDrift, RightsBlocked
from tests.conftest import attest, declaration_original, declaration_unknown, make_config


def _ingest(ctx, path, declaration, derived_from=None):
    params = {"source_path": str(path), "declaration": declaration}
    if derived_from:
        params["derived_from"] = derived_from
    return invoke(ctx, "ingest", params)["asset_id"]


def _file(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content)
    return p


# --- B1: forged evidence authorship ------------------------------------------


def test_agent_cannot_forge_operator_authored_evidence(agent_ctx, tmp_path):
    """B1: produced_by guarded F-5 but was a caller-supplied string.

    Attack: declare a bogus public-domain source, then self-attest to it as the
    operator. Before the fix this produced PERMITTED / PUBLIC_DOMAIN_VERIFIED,
    which is an agent clearing a rights flag — forbidden by F-2 outright.
    """
    src = _file(tmp_path, "claimed-pd.mp4", b"someone else's footage")
    asset_id = _ingest(
        agent_ctx,
        src,
        {
            "authorship": "third_party",
            "third_party_material": [],
            "public_domain_source": "totally public domain, trust me",
        },
    )
    assert invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})["verdict"] == "ESCALATE"

    with pytest.raises(Forbidden) as excinfo:
        invoke(
            agent_ctx,
            "add-evidence",
            {
                "asset_id": asset_id,
                "kind": "public_domain_verification",
                "body": "I checked. It is fine.",
                "produced_by": "operator",
            },
        )
    assert excinfo.value.detail["attempted"] == "operator"

    # The verdict must be unmoved.
    assert invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})["verdict"] == "ESCALATE"


def test_agent_cannot_forge_system_authored_evidence(agent_ctx, media_file):
    asset_id = _ingest(agent_ctx, media_file, declaration_original())
    with pytest.raises(Forbidden):
        invoke(
            agent_ctx,
            "add-evidence",
            {"asset_id": asset_id, "kind": "x", "body": "y", "produced_by": "system"},
        )


def test_operator_may_still_attest(operator_ctx, tmp_path):
    """The fix must not break the legitimate path it protects."""
    src = _file(tmp_path, "real-pd.mp4", b"genuinely public domain")
    asset_id = _ingest(
        operator_ctx,
        src,
        {
            "authorship": "third_party",
            "third_party_material": [],
            "public_domain_source": "US Government, 1955",
        },
    )
    invoke(
        operator_ctx,
        "add-evidence",
        {
            "asset_id": asset_id,
            "kind": "public_domain_verification",
            "body": "checked the registry entry",
            "produced_by": "operator",
        },
    )
    assert invoke(operator_ctx, "determine-rights", {"asset_id": asset_id})["verdict"] == "PERMITTED"


def test_model_evidence_must_name_the_model(agent_ctx, media_file):
    from promedia.errors import ValidationError

    asset_id = _ingest(agent_ctx, media_file, declaration_original())
    with pytest.raises(ValidationError):
        invoke(
            agent_ctx,
            "add-evidence",
            {"asset_id": asset_id, "kind": "k", "body": "b", "produced_by": "model"},
        )


# --- B3: laundering through the derivation chain ------------------------------


def test_ungraded_intermediate_cannot_break_the_chain(agent_ctx, tmp_path):
    """B3 variant A: grandchild of a BLOCKED asset came out PERMITTED."""
    a = _ingest(agent_ctx, _file(tmp_path, "a.mp4", b"source material"), declaration_unknown())
    assert invoke(agent_ctx, "determine-rights", {"asset_id": a})["verdict"] == "BLOCKED"

    b = _ingest(agent_ctx, _file(tmp_path, "b.mp4", b"intermediate"), declaration_original(), derived_from=a)
    # deliberately never graded
    c = _ingest(agent_ctx, _file(tmp_path, "c.mp4", b"grandchild"), declaration_original(), derived_from=b)

    verdict = invoke(agent_ctx, "determine-rights", {"asset_id": c})
    assert verdict["verdict"] == "BLOCKED", "a BLOCKED ancestor must reach the grandchild"


def test_grading_order_does_not_change_the_verdict(agent_ctx, tmp_path):
    """B3 variant B: order-dependence is exactly what C-20 forbids."""
    s = _ingest(agent_ctx, _file(tmp_path, "s.mp4", b"source"), declaration_unknown())
    d = _ingest(agent_ctx, _file(tmp_path, "d.mp4", b"derived"), declaration_original(), derived_from=s)

    # Grade the DERIVATIVE first, while the source has no verdict at all.
    first = invoke(agent_ctx, "determine-rights", {"asset_id": d})
    assert first["verdict"] != "PERMITTED", "an ungraded ancestor must not confer usability"

    invoke(agent_ctx, "determine-rights", {"asset_id": s})
    from promedia.core import rights as rights_layer

    assert rights_layer.effective_verdict(agent_ctx, d)["verdict"] == "BLOCKED"


def test_source_degrading_later_blocks_the_derivative(agent_ctx, tmp_path, media_file):
    """B3 variant C: the reviewer published this end to end."""
    from promedia.core import rights as rights_layer

    s = _ingest(agent_ctx, _file(tmp_path, "clean-source.mp4", b"my own work"), declaration_original())
    attest(agent_ctx, s)
    d = _ingest(agent_ctx, _file(tmp_path, "cut.mp4", b"my own work, trimmed"), declaration_original(), derived_from=s)
    attest(agent_ctx, d)
    assert rights_layer.effective_verdict(agent_ctx, d)["verdict"] == "PERMITTED"

    # New evidence degrades the SOURCE after the derivative was graded.
    invoke(
        agent_ctx,
        "add-evidence",
        {
            "asset_id": s,
            "kind": "third_party_material_suspected",
            "body": "music bed detected",
            "produced_by": "model",
            "confidence": 0.9,
            "model_id": "some-llm",
        },
    )
    invoke(agent_ctx, "determine-rights", {"asset_id": s})

    effective = rights_layer.effective_verdict(agent_ctx, d)
    assert effective["verdict"] == "ESCALATE", "a degraded ancestor must govern its descendants"
    assert effective["source_asset"] == s


def test_publish_gate_uses_the_live_chain(tmp_path, media_file):
    """The end-to-end version of B3 variant C: it must not reach publication."""
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    from promedia.core import db

    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    op_ctx = Context(config=cfg, conn=conn, principal=operator("op"))
    ag_ctx = Context(config=cfg, conn=conn, principal=agent("ag"))

    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    s = _ingest(ag_ctx, _file(tmp_path, "src.mp4", b"original"), declaration_original())
    invoke(op_ctx, "attest-declaration", {"asset_id": s})
    invoke(ag_ctx, "determine-rights", {"asset_id": s})
    d = _ingest(ag_ctx, _file(tmp_path, "der.mp4", b"derivative"), declaration_original(), derived_from=s)
    invoke(op_ctx, "attest-declaration", {"asset_id": d})
    invoke(ag_ctx, "determine-rights", {"asset_id": d})
    invoke(ag_ctx, "seal-provenance", {"asset_id": d})

    post = invoke(ag_ctx, "queue-post", {"account_id": account["account_id"], "asset_id": d, "body": "hi"})
    invoke(op_ctx, "approve-post", {"post_id": post["post_id"]})

    # Source degrades AFTER approval.
    invoke(ag_ctx, "add-evidence", {
        "asset_id": s, "kind": "third_party_material_suspected", "body": "music",
        "produced_by": "model", "confidence": 0.95, "model_id": "some-llm",
    })
    invoke(ag_ctx, "determine-rights", {"asset_id": s})

    with pytest.raises(RightsBlocked):
        invoke(op_ctx, "publish-post", {"post_id": post["post_id"]})
    assert invoke(op_ctx, "publications", {})["count"] == 0
    conn.close()


# --- B2: double publish -------------------------------------------------------


def test_concurrent_publish_calls_the_platform_once(tmp_path, media_file):
    """B2: two threads both reached the platform; only one left a record.

    The claim now happens before the external call, so exactly one caller can
    proceed. A double-click on the publish button was enough to trigger this.
    """
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    from promedia.core import db, posts, publishers
    from promedia.core.publishers.stub import StubPublisher

    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    op_ctx = Context(config=cfg, conn=conn, principal=operator("op"))

    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = _ingest(op_ctx, media_file, declaration_original())
    invoke(op_ctx, "determine-rights", {"asset_id": asset_id})
    invoke(op_ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(op_ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})
    post_id = post["post_id"]
    invoke(op_ctx, "approve-post", {"post_id": post_id})
    conn.close()

    external_calls: list[str] = []
    original = StubPublisher.publish

    def counting_publish(self, **kwargs):
        external_calls.append(self.platform)
        return original(self, **kwargs)

    StubPublisher.publish = counting_publish
    errors: list[Exception] = []

    # T-030 (the fourth item). The barrier sat before invoke(), which is wider
    # than the race it exists to force: authority, validation, C-19 locking and
    # the whole read-gate prologue run inside the window, and SQLite then
    # serialises the two writers anyway. One thread could finish the entire
    # publish — including flipping the post to 'published' — before the other
    # read the post's status at all. What deduplicated them in that case was the
    # status gate, NOT the claim under test.
    #
    # Moved to the entry of the publish handler, which is the tightest point
    # that is still INDEPENDENT of the thing being tested. That independence is
    # the whole design constraint, and this task nearly got it wrong: the
    # obvious "tightest" placement is inside _claim_for_publish itself, and a
    # barrier living inside the function under test vanishes along with it.
    # Verified by sabotage rather than reasoned about — with the barrier inside
    # the claim, deleting the claim made this test PASS, because nothing waited
    # and the threads simply ran one after the other.
    #
    # ops/posts.py calls posts_layer.publish by attribute, so patching the
    # module attribute is what the operation actually reaches.
    barrier = threading.Barrier(2)
    real_publish = posts.publish

    def publish_in_lockstep(ctx, *args, **kwargs):
        # Both threads enter the read gates and the claim together, so the
        # critical section is genuinely contended on every run rather than by
        # luck of scheduling. Signature is pass-through: the operation calls
        # this by keyword, and a wrapper that quietly fails to match would make
        # the test report zero platform calls and look like a different bug.
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:  # pragma: no cover - safety valve
            pass
        return real_publish(ctx, *args, **kwargs)

    posts.publish = publish_in_lockstep

    def worker():
        c = db.connect(cfg.db_path)
        ctx = Context(config=cfg, conn=c, principal=operator("op"))
        try:
            invoke(ctx, "publish-post", {"post_id": post_id})
        except Exception as exc:  # noqa: BLE001 - recorded, asserted below
            errors.append(exc)
        finally:
            c.close()

    try:
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        StubPublisher.publish = original
        posts.publish = real_publish

    assert len(external_calls) == 1, (
        f"the platform was called {len(external_calls)} times; a double post is "
        "not recoverable once seen (C-32)"
    )

    conn = db.connect(cfg.db_path)
    count = conn.execute("SELECT COUNT(*) AS n FROM publications").fetchone()["n"]
    status = conn.execute("SELECT status FROM posts WHERE id = ?", (post_id,)).fetchone()["status"]
    conn.close()
    assert count == 1
    assert status == "published"
    assert not any(isinstance(e, sqlite3.IntegrityError) for e in errors)


# --- B4: silent ledger drift --------------------------------------------------


def test_commit_of_reclaimed_reservation_fails_loudly(tmp_path, conn):
    """B4: bytes on disk counting zero against the ceiling, undetectably."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 100000})
    reservation = storage.reserve(conn, cfg, master_bytes=1000)

    # Simulate the machine sleeping past the TTL, then another ingest reclaiming.
    conn.execute(
        "UPDATE storage_ledger SET expires_at = ? WHERE id = ?",
        (iso(now().replace(year=2000)), reservation),
    )
    assert storage.reclaim_expired(conn) == 1

    with pytest.raises(LedgerDrift) as excinfo:
        storage.commit(conn, reservation, asset_id="as_whatever")
    assert excinfo.value.detail["state"] == "released"


def test_release_cannot_erase_committed_storage(tmp_path, conn):
    """B4: release() used `state != 'released'` and would un-count real bytes."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 100000})
    reservation = storage.reserve(conn, cfg, master_bytes=1000)
    storage.commit(conn, reservation, asset_id="as_1")
    committed = storage.usage(conn)["total_bytes"]
    assert committed == 1500

    storage.release(conn, reservation)  # must be a no-op against a committed row
    assert storage.usage(conn)["total_bytes"] == committed


# --- I6: a simulated publication must never render as a real one --------------


def test_simulated_publication_stays_marked_after_simulation_is_disabled(tmp_path, media_file):
    """I6: the marker came from current config, not from the record.

    Publish under simulation, then turn simulation off — as the operator will
    the moment real credentials arrive. The past publication must still announce
    itself as never published.
    """
    from promedia.core import db

    cfg_sim = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg_sim.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg_sim, conn=conn, principal=operator("op"))

    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = _ingest(ctx, media_file, declaration_original())
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})
    invoke(ctx, "approve-post", {"post_id": post["post_id"]})
    invoke(ctx, "publish-post", {"post_id": post["post_id"]})
    conn.close()

    # Same data, simulation now OFF.
    cfg_real = make_config(tmp_path, **{"publishing.allow_simulation": False})
    conn = db.connect(cfg_real.db_path)
    ctx = Context(config=cfg_real, conn=conn, principal=operator("op"))

    detail = invoke(ctx, "post", {"post_id": post["post_id"]})
    assert detail["simulation_enabled"] is False
    assert detail["was_simulated"] is True
    assert "NEVER PUBLISHED" in detail["warning"]

    listed = invoke(ctx, "list-posts", {})["posts"]
    assert listed[0]["simulated"] is True

    from promedia.web.app import COOKIE_NAME, create_app
    from fastapi.testclient import TestClient
    from promedia.core.credentials import CredentialStore

    store = CredentialStore(tmp_path / "creds-i6.json")
    store.ensure_operator_token()
    client = TestClient(create_app(cfg_real, store=store))
    client.cookies.set(COOKIE_NAME, store.operator_token())
    html = client.get(f"/posts/{post['post_id']}").text
    assert "NEVER PUBLISHED" in html
    conn.close()


# --- N1..N5: second-round review findings -------------------------------------


def test_exception_text_never_reaches_the_audit_log(operator_ctx, monkeypatch):
    """N1: audit_log lives in the DB, which is the backup artefact.

    A real publisher adapter will one day raise an error containing a request
    URL. That must not be persisted.
    """
    from promedia.core import ingest as ingest_layer
    from promedia.errors import ProMediaError

    secret = "super-secret-token-value-9x7"

    def boom(*args, **kwargs):
        raise RuntimeError(f"connection failed for token={secret}")

    monkeypatch.setattr(ingest_layer, "ingest_file", boom)
    with pytest.raises(ProMediaError):
        invoke(operator_ctx, "ingest", {"source_path": "x", "declaration": declaration_original()})

    entries = invoke(operator_ctx, "audit", {"limit": 10})["entries"]
    failed = [e for e in entries if e["outcome"] == "failed"]
    assert failed, "the attempt must still be audited"
    assert secret not in json.dumps(entries)
    assert failed[0]["detail"] == "unexpected RuntimeError"

    dump = "".join(str(row) for row in operator_ctx.conn.iterdump())
    assert secret not in dump


def test_published_post_cannot_be_walked_backwards(tmp_path, media_file):
    """N2: a post live on a platform could be re-labelled 'rejected'."""
    from promedia.core import db
    from promedia.errors import ValidationError

    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=operator("op"))

    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = _ingest(ctx, media_file, declaration_original())
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})
    invoke(ctx, "approve-post", {"post_id": post["post_id"]})
    invoke(ctx, "publish-post", {"post_id": post["post_id"]})

    for decision in ("approved", "rejected"):
        with pytest.raises(ValidationError):
            invoke(ctx, "approve-post", {"post_id": post["post_id"], "decision": decision})

    assert invoke(ctx, "post", {"post_id": post["post_id"]})["status"] == "published"
    conn.close()


def test_stranded_publish_claim_is_releasable_on_both_surfaces(tmp_path, media_file):
    """N3: recovery previously existed only as a side effect of the N2 bug."""
    from promedia.core import db
    from promedia.errors import ValidationError

    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=operator("op"))

    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = _ingest(ctx, media_file, declaration_original())
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})
    post_id = post["post_id"]
    invoke(ctx, "approve-post", {"post_id": post_id})

    # Simulate a crash between claim and INSERT.
    conn.execute("UPDATE posts SET status = 'publishing' WHERE id = ?", (post_id,))
    with pytest.raises(ValidationError, match="already being published"):
        invoke(ctx, "publish-post", {"post_id": post_id})

    released = invoke(ctx, "release-publish-claim", {"post_id": post_id})
    assert released["status"] == "approved"
    result = invoke(ctx, "publish-post", {"post_id": post_id})
    assert result["ok"] is True
    conn.close()


def test_release_claim_refuses_when_actually_published(tmp_path, media_file):
    from promedia.core import db
    from promedia.errors import ValidationError

    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=operator("op"))
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = _ingest(ctx, media_file, declaration_original())
    invoke(ctx, "determine-rights", {"asset_id": asset_id})
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})
    invoke(ctx, "approve-post", {"post_id": post["post_id"]})
    invoke(ctx, "publish-post", {"post_id": post["post_id"]})
    with pytest.raises(ValidationError):
        invoke(ctx, "release-publish-claim", {"post_id": post["post_id"]})
    conn.close()


def test_reporting_operations_agree_with_the_gate(agent_ctx, tmp_path):
    """N4: `rights` and `list-assets` reported PERMITTED where the gate refused."""
    from promedia.core import rights as rights_layer

    s = _ingest(agent_ctx, _file(tmp_path, "s2.mp4", b"src"), declaration_original())
    attest(agent_ctx, s)
    d = _ingest(agent_ctx, _file(tmp_path, "d2.mp4", b"der"), declaration_original(), derived_from=s)
    attest(agent_ctx, d)
    assert invoke(agent_ctx, "rights", {"asset_id": d})["verdict"] == "PERMITTED"

    invoke(agent_ctx, "add-evidence", {
        "asset_id": s, "kind": "third_party_material_suspected", "body": "music",
        "produced_by": "model", "confidence": 0.9, "model_id": "some-llm",
    })
    invoke(agent_ctx, "determine-rights", {"asset_id": s})

    gate = rights_layer.effective_verdict(agent_ctx, d)["verdict"]
    reported = invoke(agent_ctx, "rights", {"asset_id": d})
    assert reported["verdict"] == gate == "ESCALATE"
    assert reported["stored_verdict"] == "PERMITTED"
    assert reported["differs_from_stored"] is True

    listed = {a["id"]: a for a in invoke(agent_ctx, "list-assets", {})["assets"]}
    assert listed[d]["latest_verdict"] == "ESCALATE"


def test_approval_screen_discloses_who_declared(tmp_path, media_file):
    """The operator must see that an agent authored the rights claim."""
    from promedia.core import db

    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    op_ctx = Context(config=cfg, conn=conn, principal=operator("op"))
    ag_ctx = Context(config=cfg, conn=conn, principal=agent("evil-agent"))

    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = _ingest(ag_ctx, media_file, declaration_original())
    invoke(ag_ctx, "determine-rights", {"asset_id": asset_id})
    post = invoke(ag_ctx, "queue-post", {"account_id": account["account_id"], "asset_id": asset_id, "body": "x"})

    detail = invoke(ag_ctx, "post", {"post_id": post["post_id"]})
    assert detail["declaration"]["declared_by"] == "evil-agent"
    assert detail["declaration"]["attested_by_operator"] is False
    assert detail["approvable"] is False
    conn.close()


# --- I1: unexpected failures must still be audited ----------------------------


def test_unexpected_exception_is_audited_and_wrapped(operator_ctx, media_file, monkeypatch):
    """I1: a non-ProMediaError left no audit row and surfaced as a raw traceback."""
    from promedia.core import ingest as ingest_layer
    from promedia.errors import ProMediaError

    def boom(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(ingest_layer, "ingest_file", boom)

    with pytest.raises(ProMediaError) as excinfo:
        invoke(operator_ctx, "ingest", {"source_path": str(media_file),
                                        "declaration": declaration_original()})
    assert excinfo.value.detail["exception_type"] == "IntegrityError"

    entries = invoke(operator_ctx, "audit", {"limit": 10})["entries"]
    assert any(e["operation"] == "ingest" and e["outcome"] == "failed" for e in entries)
