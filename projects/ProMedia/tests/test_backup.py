"""T-036 — exporting the permanent set (project.md 5.4).

The easy part of a backup is copying rows. These tests are about the three
things that are easy to get wrong and expensive to discover late:

* a credential riding along into an off-site artefact, defeating DR-008's whole
  reason for keeping secrets out of the database;
* provenance whose integrity no longer verifies after the round trip, which
  turns a backup of EVIDENCE into a backup of bytes (F-8);
* a table added to the schema years from now that silently falls outside every
  future backup, and is noticed during a restore.

The last one has no failing test today by construction — it is about a table
that does not exist yet — so it is tested by adding one.
"""

from __future__ import annotations

import json

import pytest

from promedia.core import backup, db
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke
from promedia.errors import Forbidden, NotFound, ProMediaError, ValidationError
from tests.conftest import attest, declaration_original, make_config

CANARY = "canary-credential-must-never-be-backed-up-9x7"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    yield cfg, Context(config=cfg, conn=conn, principal=operator("op"))
    conn.close()


def _full_history(ctx, media_file):
    """One complete slice, so the export has every permanent kind in it."""
    account = invoke(
        ctx, "connect-account", {"platform": "x", "handle": "me", "secret": CANARY}
    )["account_id"]
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    asset = invoke(
        as_agent, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )["asset_id"]
    # Real evidence, so the artefact contains every permanent kind rather than
    # only the ones a minimal happy path happens to create.
    invoke(
        ctx, "add-evidence",
        {"asset_id": asset, "kind": "ownership_confirmed",
         "body": "shot by the operator", "produced_by": "operator"},
    )
    attest(ctx, asset)
    prov = invoke(ctx, "seal-provenance", {"asset_id": asset})["provenance_id"]
    post = invoke(
        ctx, "queue-post", {"account_id": account, "asset_id": asset, "body": "hello"}
    )["post_id"]
    invoke(ctx, "approve-post", {"post_id": post})
    invoke(ctx, "publish-post", {"post_id": post})
    return {"account": account, "asset": asset, "provenance": prov, "post": post}


# --- AC-2: no secret rides along ----------------------------------------------


def test_no_credential_appears_anywhere_in_the_artefact(env, media_file):
    """The control DR-008 exists to provide, asserted with a canary.

    The whole point of keeping credentials out of the database is that they are
    absent from backup artefacts. An export that carried one would look
    completely normal, which is why this is asserted against the serialised
    bytes rather than by reasoning about which tables were selected.
    """
    cfg, ctx = env
    _full_history(ctx, media_file)

    text = backup.dumps(backup.build(ctx.conn))

    assert CANARY not in text, "a credential reached the backup artefact"
    assert "credential_ref" in text, "sanity: the account row IS in the artefact"


def test_the_operator_token_is_not_in_the_artefact(env, media_file):
    """It grants publish authority over every account, and it lives in the
    credential store rather than the database — so it must be absent for the
    same reason, and is the one most likely to be forgotten."""
    cfg, ctx = env
    from promedia.core.credentials import CredentialStore

    token = CredentialStore().ensure_operator_token()
    _full_history(ctx, media_file)

    assert token not in backup.dumps(backup.build(ctx.conn))


# --- AC-3: provenance survives the round trip ---------------------------------


def test_provenance_integrity_verifies_out_of_the_artefact(env, media_file):
    """F-8. A record whose hash no longer checks is not evidence.

    Recomputed from the artefact's own payload, with no reference to the
    database — which is the situation a restore is actually performed in.
    """
    cfg, ctx = env
    ids = _full_history(ctx, media_file)

    artefact = json.loads(backup.dumps(backup.build(ctx.conn)))
    records = artefact["payload"]["provenance_records"]
    assert len(records) == 1
    record = records[0]
    assert record["id"] == ids["provenance"]

    from promedia.core.provenance import _integrity_hash

    payload = json.loads(record["payload"])
    assert _integrity_hash(payload) == record["integrity_hash"], (
        "provenance integrity does not verify from the artefact alone"
    )


def test_the_evidence_chain_is_complete_in_the_artefact(env, media_file):
    """'Rights evidence' is three tables, and a partial chain is not evidence."""
    cfg, ctx = env
    _full_history(ctx, media_file)
    payload = backup.build(ctx.conn)["payload"]

    for table in ("rights_declarations", "evidence", "rights_verdicts"):
        assert payload[table], f"{table} is empty; the rights chain is incomplete"
    assert payload["approvals"], "the approval record is missing"
    assert payload["publications"], "the publication record is missing"
    assert payload["audit_log"], "the audit log is missing"


# --- AC-1 / AC-5: the table classification ------------------------------------


def test_every_schema_table_is_classified(env):
    """No table may be in neither set."""
    cfg, ctx = env
    classified = backup.classify_tables(ctx.conn)
    assert set(classified["permanent"]) == set(backup.PERMANENT_TABLES)
    assert set(classified["transient"]) == set(backup.TRANSIENT_TABLES)


def test_a_new_unclassified_table_is_refused(env):
    """AC-1's real subject: the table that does not exist yet.

    A future migration adds a table, nobody updates this module, and it falls
    out of every backup silently. Here it fails loudly instead — at backup time,
    which is years before the restore where it would otherwise be discovered.
    """
    cfg, ctx = env
    ctx.conn.execute("CREATE TABLE future_feature (id TEXT PRIMARY KEY)")

    with pytest.raises(ProMediaError) as excinfo:
        backup.classify_tables(ctx.conn)
    assert "future_feature" in str(excinfo.value)

    # And the export refuses too, rather than quietly omitting it.
    with pytest.raises(ProMediaError):
        backup.build(ctx.conn)


def test_locks_and_ledger_are_excluded_with_a_stated_reason(env, media_file):
    """AC-5. Restoring these would actively harm, so the exclusion is not a
    space saving and must not read as one."""
    cfg, ctx = env
    artefact = backup.build(ctx.conn)

    assert "entity_locks" not in artefact["payload"]
    assert "storage_ledger" not in artefact["payload"]
    assert "ingest_queue" not in artefact["payload"]
    for table in ("entity_locks", "storage_ledger", "ingest_queue"):
        assert len(artefact["excluded_tables"][table]) > 40, (
            f"{table} is excluded without a real reason recorded"
        )


def test_the_artefact_says_media_is_not_included(env, media_file):
    """The surprising fact, stated inside the artefact itself.

    Someone restoring this in five years will not have read the source. 'Where
    is my media' is the worst question to have to answer during a recovery.
    """
    cfg, ctx = env
    _full_history(ctx, media_file)
    note = backup.build(ctx.conn)["note"]
    assert "transient" in note.lower() and "not" in note.lower()
    assert invoke(ctx, "backup-scope", {})["media_included"] is False


# --- AC-4: integrity of the artefact ------------------------------------------


def test_a_tampered_artefact_is_detected(env, media_file):
    cfg, ctx = env
    _full_history(ctx, media_file)
    artefact = json.loads(backup.dumps(backup.build(ctx.conn)))

    assert backup.verify(artefact)["integrity_verified"] is True

    artefact["payload"]["audit_log"] = []  # the edit an attacker would want
    result = backup.verify(artefact)
    assert result["integrity_verified"] is False
    assert result["expected_hash"] != result["actual_hash"]


def test_verification_needs_nothing_but_the_artefact(env, media_file, tmp_path):
    """It will be verified off-site, where the database does not exist."""
    cfg, ctx = env
    _full_history(ctx, media_file)
    path = tmp_path / "out" / "backup.json"
    invoke(ctx, "export-permanent-set", {"destination": str(path)})

    artefact = json.loads(path.read_text(encoding="utf-8"))
    assert backup.verify(artefact)["integrity_verified"] is True


def test_two_exports_of_an_unchanged_database_are_identical(env, media_file):
    """Lets a caller detect 'nothing changed' without diffing row by row."""
    cfg, ctx = env
    _full_history(ctx, media_file)
    at = "2026-08-13T00:00:00+00:00"
    first = backup.build(ctx.conn, at=at)
    second = backup.build(ctx.conn, at=at)
    assert backup.dumps(first) == backup.dumps(second)


# --- the operations -----------------------------------------------------------


def test_export_writes_a_file_and_reports_what_it_wrote(env, media_file, tmp_path):
    cfg, ctx = env
    _full_history(ctx, media_file)
    path = tmp_path / "nested" / "dir" / "backup.json"

    result = invoke(ctx, "export-permanent-set", {"destination": str(path)})

    assert path.is_file() and result["written_to"] == str(path)
    assert result["bytes"] > 0
    assert result["row_counts"]["publications"] == 1
    assert result["integrity_hash"]


def test_an_agent_cannot_export_the_permanent_set(env, media_file):
    """Read-only, but operator authority — see test_registry for the reasoning."""
    cfg, ctx = env
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    with pytest.raises(Forbidden):
        invoke(as_agent, "export-permanent-set", {})


def test_an_agent_may_verify_a_backup(env, media_file, tmp_path):
    """Verifying must be cheap and frequent, so it is not gated."""
    cfg, ctx = env
    path = tmp_path / "backup.json"
    invoke(ctx, "export-permanent-set", {"destination": str(path)})

    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    assert invoke(as_agent, "verify-backup", {"source": str(path)})["integrity_verified"] is True


def test_verifying_a_missing_or_bogus_artefact_is_refused(env, tmp_path):
    cfg, ctx = env
    with pytest.raises(NotFound):
        invoke(ctx, "verify-backup", {"source": str(tmp_path / "nope.json")})

    junk = tmp_path / "junk.json"
    junk.write_text("this is not json", encoding="utf-8")
    with pytest.raises(ValidationError):
        invoke(ctx, "verify-backup", {"source": str(junk)})


def test_exporting_onto_a_directory_is_refused(env, tmp_path):
    cfg, ctx = env
    with pytest.raises(ValidationError):
        invoke(ctx, "export-permanent-set", {"destination": str(tmp_path)})


def test_export_changes_nothing_except_recording_that_it_happened(env, media_file):
    """A backup that changed the thing it backs up would be a poor backup.

    The one exception is the audit log, and it is not an exception worth
    removing: export-permanent-set is operator authority, so invoke() audits it
    like every other authority-gated call. An operation that writes the entire
    audit log to a file of the caller's choosing is exactly the kind that should
    leave a trace of having been used. This test originally asserted NOTHING
    changed and failed on that entry, which was the test being wrong rather than
    the code.
    """
    cfg, ctx = env
    _full_history(ctx, media_file)
    before = backup.build(ctx.conn)["row_counts"]

    invoke(ctx, "export-permanent-set", {})
    after = backup.build(ctx.conn)["row_counts"]

    assert {t: n for t, n in after.items() if t != "audit_log"} == {
        t: n for t, n in before.items() if t != "audit_log"
    }
    assert after["audit_log"] == before["audit_log"] + 1
    entries = invoke(ctx, "audit", {"limit": 5})["entries"]
    assert any(e["operation"] == "export-permanent-set" for e in entries)
    assert db.list_locks(ctx.conn) == []
