"""T-050 — the media library. Get footage in from the browser, and see it.

Before this, ProMedia's browser surface had no way to add a file at all — the
operator had to drop to the CLI to ingest anything. AC-1 is the one that
matters most: this must be as hard to bypass in the browser as it already is
on the CLI, because ``ingest`` refusing without a rights declaration IS the
rights model (F-3), not a convenience check layered on top of it.
"""

from __future__ import annotations

import json

import pytest

from promedia.core.registry import Context, invoke
from promedia.core.principal import agent, operator
from tests.conftest import attest, declaration_original
from tests.test_ops_forms import agent_client, env, ingest_as_agent, operator_client

__all__ = ["env"]


def _upload(client, media_file, **fields):
    with media_file.open("rb") as fh:
        return client.post(
            "/media",
            data=fields,
            files={"file": (media_file.name, fh, "video/mp4")},
        )


# --- AC-1: refused without a declaration, exactly like the CLI ---------------


def test_uploading_without_authorship_is_refused_and_nothing_is_ingested(env, media_file):
    cfg, ctx, store = env
    response = _upload(agent_client(cfg, store), media_file)
    assert response.status_code == 400
    assert "VALIDATION" in response.text
    assert invoke(ctx, "list-assets", {})["count"] == 0


def test_uploading_with_an_empty_authorship_string_is_also_refused(env, media_file):
    """A client that sends the field but leaves it blank must be refused too —
    not just a client that omits it. Both are 'no declaration' (protocol 05)."""
    cfg, ctx, store = env
    response = _upload(agent_client(cfg, store), media_file, authorship="")
    assert response.status_code == 400
    assert invoke(ctx, "list-assets", {})["count"] == 0


def test_uploading_with_a_declaration_ingests_and_redirects_to_the_asset_page(env, media_file):
    """AC-1's positive path. A real upload, staged and ingested exactly as a
    CLI-supplied source_path would be — content-addressed, hashed, stored."""
    cfg, ctx, store = env
    response = _upload(
        agent_client(cfg, store, follow_redirects=False), media_file,
        authorship="operator_original", third_party_material="",
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/media/")

    listing = invoke(ctx, "list-assets", {})
    assert listing["count"] == 1
    assert listing["assets"][0]["original_filename"] == media_file.name
    assert listing["assets"][0]["byte_size"] == media_file.stat().st_size


def test_third_party_material_lines_become_a_list(env, media_file):
    cfg, ctx, store = env
    _upload(
        agent_client(cfg, store), media_file,
        authorship="third_party",
        third_party_material="background music\nstock b-roll\n\n  ",
    )
    asset_id = invoke(ctx, "list-assets", {})["assets"][0]["id"]
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["declaration"]["third_party_material"] == ["background music", "stock b-roll"]


def test_the_uploaded_file_on_disk_is_not_left_behind(env, media_file, tmp_path):
    """The staged temp copy is disposable once ingest has its own content-
    addressed copy — leaving it around would double storage for nothing."""
    import tempfile
    from pathlib import Path

    before = set(Path(tempfile.gettempdir()).glob("promedia-upload-*"))
    cfg, ctx, store = env
    _upload(agent_client(cfg, store), media_file, authorship="operator_original")
    after = set(Path(tempfile.gettempdir()).glob("promedia-upload-*"))
    assert after - before == set(), "a staging directory was left behind after upload"


def test_the_original_filename_survives_staging(env, media_file):
    """The staged copy lives under a directory, not a randomly-named temp
    file — the asset must be recorded under the name the operator uploaded,
    not a generated one (finding: an earlier version of this route used
    tempfile.mkstemp's own name, so the library showed 'promedia-upload-
    xxxx.mp4' instead of the real filename for every single upload)."""
    cfg, ctx, store = env
    _upload(agent_client(cfg, store), media_file, authorship="operator_original")
    listing = invoke(ctx, "list-assets", {})
    assert listing["assets"][0]["original_filename"] == media_file.name


def test_the_refusal_test_can_actually_fail(env, media_file, monkeypatch):
    """Proves AC-1's refusal is a real gate rather than a test that passes for
    the wrong reason: temporarily force every declaration to look valid
    regardless of what was submitted, confirm a no-declaration upload then
    WRONGLY succeeds, then restore and confirm the refusal is back. The fix
    under test (``ingest_layer._validate_declaration``) belongs to T-008, not
    this task, so it is sabotaged inline via monkeypatch rather than edited
    and reverted in source."""
    import promedia.core.ingest as ingest_layer

    cfg, ctx, store = env
    original = ingest_layer._validate_declaration
    monkeypatch.setattr(
        ingest_layer, "_validate_declaration",
        lambda decl: {"authorship": "operator_original", "third_party_material": []},
    )
    try:
        response = _upload(agent_client(cfg, store, follow_redirects=False), media_file)  # no declaration at all
        assert response.status_code == 303, (
            "sabotage did not take effect; this test's premise is untrustworthy"
        )
        assert invoke(ctx, "list-assets", {})["count"] == 1, (
            "with validation forced to pass, an undeclared upload was wrongly ingested"
        )
    finally:
        monkeypatch.setattr(ingest_layer, "_validate_declaration", original)
    # Restored: the real refusal is back, and a second (still undeclared) upload
    # of the same bytes is refused before it ever reaches deduplication.
    response = _upload(agent_client(cfg, store), media_file)
    assert response.status_code == 400
    assert invoke(ctx, "list-assets", {})["count"] == 1, "still just the one from the sabotage run"


# --- AC-3: no JavaScript -------------------------------------------------------


def test_media_pages_carry_no_script_tags(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    client = agent_client(cfg, store)
    for url in ("/media", f"/media/{asset_id}"):
        response = client.get(url)
        assert response.status_code == 200
        assert "<script" not in response.text


# --- AC-2: the asset page shows verdict, evidence, provenance, availability --


def test_asset_page_before_any_evaluation_shows_the_gaps_honestly(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    response = agent_client(cfg, store).get(f"/media/{asset_id}")
    assert response.status_code == 200
    assert "ESCALATE" in response.text  # NO_VERDICT_YET governs by default
    assert "not sealed" in response.text
    assert "No evidence recorded yet" in response.text
    assert "not yet attested" in response.text


def test_asset_page_shows_verdict_evidence_and_sealed_provenance(env, media_file):
    """AC-2, the full path: attest, determine, add evidence, seal — every one
    of those facts must be readable on the one page afterwards."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)  # operator attests + determines
    invoke(ctx, "add-evidence", {
        "asset_id": asset_id, "kind": "public_domain_verification",
        "body": "checked against the registry", "produced_by": "agent",
    })
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})

    response = agent_client(cfg, store).get(f"/media/{asset_id}")
    assert response.status_code == 200
    assert "PERMITTED" in response.text
    assert "public_domain_verification" in response.text
    assert "checked against the registry" in response.text
    assert "sealed" in response.text
    assert "yes" in response.text.lower()  # media available


def test_asset_page_reports_media_gone_after_deletion(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    ctx.conn.execute("UPDATE assets SET state = 'deleted' WHERE id = ?", (asset_id,))
    ctx.conn.commit()
    response = agent_client(cfg, store).get(f"/media/{asset_id}")
    assert response.status_code == 200
    assert "media is gone" in response.text


# --- the action routes ---------------------------------------------------------


def test_determine_rights_button_runs_a_real_determination(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)
    # attest() already runs determine-rights once; run it again through the
    # button's own route to prove the ROUTE works, not just the fixture.
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/media/{asset_id}/determine-rights"
    )
    assert response.status_code == 303
    assert invoke(ctx, "rights", {"asset_id": asset_id})["verdict"] == "PERMITTED"


def test_attest_is_refused_for_an_agent_session(env, media_file):
    """F-2: only the operator may attest. The route must not quietly no-op —
    it must refuse, and the declaration must stay un-attested."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    response = agent_client(cfg, store).post(f"/media/{asset_id}/attest")
    assert response.status_code == 403
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["declaration"]["declared_by_kind"] == "agent"


def test_attest_succeeds_for_the_operator(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    response = operator_client(cfg, store, follow_redirects=False).post(
        f"/media/{asset_id}/attest"
    )
    assert response.status_code == 303
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["declaration"]["declared_by_kind"] == "operator"


def test_evidence_is_attributed_to_the_calling_principal_never_the_form(env, media_file):
    """The form has no produced_by field at all — a caller cannot claim
    'operator' just by including one; the route derives it independently."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/media/{asset_id}/evidence",
        data={"kind": "note", "body": "seen on screen", "produced_by": "operator"},
    )
    assert response.status_code == 303
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["evidence"][0]["produced_by"] == "agent"


def test_seal_before_any_verdict_is_refused(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    response = agent_client(cfg, store).post(f"/media/{asset_id}/seal")
    assert response.status_code in (400, 404)
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["provenance"] is None


def test_seal_after_a_verdict_makes_the_asset_page_show_it(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/media/{asset_id}/seal"
    )
    assert response.status_code == 303
    page = agent_client(cfg, store).get(f"/media/{asset_id}")
    assert "provenance" in page.text.lower()
    detail = invoke(ctx, "asset", {"asset_id": asset_id})
    assert detail["provenance"] is not None


# --- the queue and filters ------------------------------------------------------


def test_a_queued_ingest_appears_on_the_library_page(env, media_file):
    from promedia.core import storage as storage_layer

    cfg, ctx, store = env
    storage_layer.enqueue_refused(
        ctx.conn, source_path="Z:/would-not-fit.mp4", projected=999_999_999,
        declaration=declaration_original(), shortfall_bytes=123_456,
    )
    ctx.conn.commit()
    response = agent_client(cfg, store).get("/media")
    assert response.status_code == 200
    assert "would-not-fit.mp4" in response.text
    assert "queued" in response.text.lower()


def test_search_filters_the_library_by_filename(env, media_file, tmp_path):
    cfg, ctx, store = env
    ingest_as_agent(ctx, media_file)  # "clip.mp4"
    other = tmp_path / "second-video.mp4"
    other.write_bytes(b"different bytes" * 50)
    ingest_as_agent(ctx, other)

    response = agent_client(cfg, store).get("/media", params={"q": "second-video"})
    assert "second-video.mp4" in response.text
    assert media_file.name not in response.text


def test_verdict_filter_none_shows_only_ungraded_assets(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)  # now PERMITTED

    other_media = media_file.parent / "ungraded.mp4"
    other_media.write_bytes(b"other bytes" * 50)
    ingest_as_agent(ctx, other_media)  # stays ungraded

    response = agent_client(cfg, store).get("/media", params={"verdict": "none"})
    assert "ungraded.mp4" in response.text
    assert media_file.name not in response.text
