"""T-037 — restoring the permanent set, and the schema change it needed.

A backup that has never been restored is a hypothesis. These tests are the
thing that turns it into a fact, and they are written as a separate READER from
the writer in T-036 on purpose: a format only its own writer can parse is the
classic way a backup regime turns out not to be one.

The centre of this task is not the copying. It is a state that did not exist
before. Media is transient and deliberately absent from the artefact, so a
restored asset row cannot honestly say 'stored' — nothing is on disk. It also
must not say 'deleted', because retention deletion is FINAL: T-029 refuses
re-ingest of a deleted asset, so reusing that value would turn a successful
recovery into a permanent loss of capability. Hence 'absent', and hence a
migration, since SQLite cannot ALTER a CHECK constraint.
"""

from __future__ import annotations

import json

import pytest

from promedia.core import backup, db
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke
from promedia.errors import Forbidden, MediaUnavailable, ProMediaError
from tests.conftest import attest, declaration_original, make_config

CANARY = "canary-credential-must-never-be-backed-up-9x7"


def _ctx(tmp_path, name):
    cfg = make_config(tmp_path / name, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    return cfg, Context(config=cfg, conn=conn, principal=operator("op"))


@pytest.fixture
def source(tmp_path, monkeypatch):
    """A populated system, and the artefact taken from it."""
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg, ctx = _ctx(tmp_path, "source")
    yield cfg, ctx
    ctx.conn.close()


@pytest.fixture
def target(tmp_path, monkeypatch):
    """An empty system to restore into, with its own data dir."""
    cfg, ctx = _ctx(tmp_path, "target")
    yield cfg, ctx
    ctx.conn.close()


def _full_history(ctx, media_file):
    account = invoke(
        ctx, "connect-account", {"platform": "x", "handle": "me", "secret": CANARY}
    )["account_id"]
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    asset = invoke(
        as_agent, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )["asset_id"]
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


def _export(ctx, tmp_path, name="backup.json"):
    path = tmp_path / name
    invoke(ctx, "export-permanent-set", {"destination": str(path)})
    return path


# --- AC-1: the round trip -----------------------------------------------------


def test_every_permanent_row_survives_the_round_trip(source, target, media_file, tmp_path):
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)
    before = backup.build(ctx.conn)["row_counts"]

    result = invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    assert result["ok"] is True
    after = backup.build(tctx.conn)["row_counts"]
    for table, count in before.items():
        if table in ("schema_version", "audit_log"):
            continue  # see the audit_log test below
        assert after[table] == count, f"{table}: {after[table]} rows, expected {count}"

    assert tctx.conn.execute(
        "SELECT 1 FROM publications WHERE post_id = ?", (ids["post"],)
    ).fetchone(), "the publication record did not survive"


def test_provenance_integrity_verifies_after_the_round_trip(source, target, media_file, tmp_path):
    """F-8, end to end. The point of backing up evidence is that it is still
    evidence afterwards — a record whose hash no longer checks is just rows."""
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)

    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    verified = invoke(tctx, "verify-provenance", {"provenance_id": ids["provenance"]})
    assert verified["integrity_verified"] is True


def test_the_rights_position_is_intact_after_restore(source, target, media_file, tmp_path):
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)
    before = invoke(ctx, "rights", {"asset_id": ids["asset"]})["verdict"]

    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    assert invoke(tctx, "rights", {"asset_id": ids["asset"]})["verdict"] == before


def test_the_audit_log_survives(source, target, media_file, tmp_path):
    """It is a permanent record in its own right (5.4), and the one an operator
    would most want after losing a disk."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    source_entries = len(invoke(ctx, "audit", {"limit": 500})["entries"])

    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    restored = invoke(tctx, "audit", {"limit": 500})["entries"]
    # The restore itself is audited, so the count grows by exactly that.
    assert len(restored) >= source_entries
    assert any(e["operation"] == "publish-post" for e in restored)


# --- AC-3: media is NOT resurrected -------------------------------------------


def test_a_restored_asset_is_absent_not_stored(source, target, media_file, tmp_path):
    """The phantom-asset defect, which is what the schema change exists to stop.

    Availability is decided by assets.state, not by checking the disk. A
    restored row claiming 'stored' would walk straight through the media gates
    with nothing behind it — exactly what T-029 closed for retention deletion.
    """
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)

    result = invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    row = tctx.conn.execute(
        "SELECT state, object_path FROM assets WHERE id = ?", (ids["asset"],)
    ).fetchone()
    assert row["state"] == "absent"
    assert row["object_path"] is None, "an object path on this machine would be a lie"
    assert result["assets_marked_absent"] == 1
    assert result["media_restored"] is False


def test_publishing_a_restored_asset_is_refused(source, target, media_file, tmp_path):
    """The gate that matters. The record says PERMITTED and there is no media."""
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)
    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    new_post = invoke(
        tctx, "queue-post",
        {"account_id": ids["account"], "asset_id": ids["asset"], "body": "again"},
    )["post_id"]
    with pytest.raises(MediaUnavailable):
        invoke(tctx, "approve-post", {"post_id": new_post})


def test_a_retention_deleted_asset_stays_deleted(source, target, media_file, tmp_path):
    """'absent' must not launder 'deleted'.

    Retention deletion is final (project.md section 10). If a restore turned it
    into 'absent', re-ingest would become permitted and the backup would be a
    side door around the retention policy — an authority escalation dressed as
    recovery.
    """
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)
    ctx.conn.execute(
        "UPDATE assets SET state = 'deleted', object_path = NULL WHERE id = ?",
        (ids["asset"],),
    )

    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    row = tctx.conn.execute(
        "SELECT state FROM assets WHERE id = ?", (ids["asset"],)
    ).fetchone()
    assert row["state"] == "deleted", "retention deletion was laundered into 'absent'"


def test_entity_locks_are_not_reintroduced(source, target, media_file, tmp_path):
    """Restoring a lock would wedge an entity against a session that no longer
    exists, for the remainder of a TTL measured from the past."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    db.acquire_lock(
        ctx.conn, "asset", "as_whatever", task_id="t", agent="ghost",
        model="m", ttl_minutes=90,
    )

    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    assert db.list_locks(tctx.conn) == []


# --- the recovery path that makes 'absent' worth having -----------------------


def test_re_ingesting_the_original_file_restores_an_absent_asset(
    source, target, media_file, tmp_path
):
    """AC-3's other half, and the reason 'absent' is not 'deleted'.

    Refusing here would turn a successful recovery into a permanent loss of
    capability, which is the opposite of what a backup is for.
    """
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)
    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    as_agent = Context(config=tctx.config, conn=tctx.conn, principal=agent("ag"))
    result = invoke(
        as_agent, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )

    assert result["asset_id"] == ids["asset"], "a new id would strand the provenance"
    assert result["asset_state"] == "stored"
    assert result.get("restored") is True
    row = tctx.conn.execute(
        "SELECT state, object_path FROM assets WHERE id = ?", (ids["asset"],)
    ).fetchone()
    assert row["state"] == "stored" and row["object_path"]


def test_a_recovered_asset_is_publishable_again(source, target, media_file, tmp_path):
    """The end of the recovery story: the verdict still governs, and the media
    is back, so the gates open again."""
    cfg, ctx = source
    tcfg, tctx = target
    ids = _full_history(ctx, media_file)
    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})
    as_agent = Context(config=tctx.config, conn=tctx.conn, principal=agent("ag"))
    invoke(
        as_agent, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )

    post = invoke(
        tctx, "queue-post",
        {"account_id": ids["account"], "asset_id": ids["asset"], "body": "recovered"},
    )["post_id"]
    invoke(tctx, "approve-post", {"post_id": post})
    assert invoke(tctx, "publish-post", {"post_id": post})["ok"] is True


def test_re_ingesting_a_deleted_asset_is_still_refused(source, media_file):
    """T-029's guarantee, re-asserted now that a neighbouring state exists."""
    cfg, ctx = source
    ids = _full_history(ctx, media_file)
    ctx.conn.execute(
        "UPDATE assets SET state = 'deleted', object_path = NULL WHERE id = ?",
        (ids["asset"],),
    )
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    with pytest.raises(MediaUnavailable):
        invoke(
            as_agent, "ingest",
            {"source_path": str(media_file), "declaration": declaration_original()},
        )


# --- AC-2 / AC-4: the refusals ------------------------------------------------


def test_restoring_into_a_non_empty_database_is_refused(source, target, media_file, tmp_path):
    """A merge would have to decide which version of a row wins, and there is no
    generally right answer — the audit log would silently gain or lose entries."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    path = _export(ctx, tmp_path)
    invoke(tctx, "restore-permanent-set", {"source": str(path)})

    with pytest.raises(ProMediaError) as excinfo:
        invoke(tctx, "restore-permanent-set", {"source": str(path)})
    assert "empty" in str(excinfo.value).lower()


def test_a_tampered_artefact_is_refused_before_anything_is_written(
    source, target, media_file, tmp_path
):
    """Half a restore is worse than none, because it looks like a whole one."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    path = _export(ctx, tmp_path)
    artefact = json.loads(path.read_text(encoding="utf-8"))
    artefact["payload"]["audit_log"] = []
    path.write_text(json.dumps(artefact), encoding="utf-8")

    with pytest.raises(ProMediaError):
        invoke(tctx, "restore-permanent-set", {"source": str(path)})
    assert backup._is_empty(tctx.conn), "substantive rows were written despite the refusal"


def test_an_artefact_from_a_newer_schema_is_refused(source, target, media_file, tmp_path):
    """An older build cannot know what a later column means, and the records at
    stake are by definition irreplaceable."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    path = _export(ctx, tmp_path)
    artefact = json.loads(path.read_text(encoding="utf-8"))
    artefact["schema_version"] = db.SCHEMA_VERSION + 5
    artefact["integrity_hash"] = backup.integrity_hash(artefact["payload"])
    path.write_text(json.dumps(artefact), encoding="utf-8")

    with pytest.raises(ProMediaError) as excinfo:
        invoke(tctx, "restore-permanent-set", {"source": str(path)})
    assert "newer" in str(excinfo.value).lower()
    assert backup._is_empty(tctx.conn)


def test_an_agent_cannot_restore(source, target, media_file, tmp_path):
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    path = _export(ctx, tmp_path)
    as_agent = Context(config=tctx.config, conn=tctx.conn, principal=agent("ag"))

    with pytest.raises(Forbidden):
        invoke(as_agent, "restore-permanent-set", {"source": str(path)})
    assert backup._is_empty(tctx.conn)


def test_a_restore_can_be_retried_after_a_failed_attempt(source, target, media_file, tmp_path):
    """The trap this task nearly shipped.

    Every restore ATTEMPT is audited, refusals included. While the emptiness
    check counted audit_log, a first attempt that failed integrity wrote a
    denial entry — and every subsequent attempt was then refused for a
    non-empty database caused entirely by the first. The operator's recovery
    path would have closed behind them at the worst possible moment.
    """
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    good = _export(ctx, tmp_path, "good.json")

    bad = tmp_path / "bad.json"
    artefact = json.loads(good.read_text(encoding="utf-8"))
    artefact["payload"]["audit_log"] = []
    bad.write_text(json.dumps(artefact), encoding="utf-8")

    with pytest.raises(ProMediaError):
        invoke(tctx, "restore-permanent-set", {"source": str(bad)})

    # The retry must still work.
    result = invoke(tctx, "restore-permanent-set", {"source": str(good)})
    assert result["ok"] is True
    assert result["restored"]["publications"] == 1


def test_restored_audit_entries_do_not_collide_with_local_ones(
    source, target, media_file, tmp_path
):
    """audit_log ids are AUTOINCREMENT surrogates. The target already has its
    own entries from the restore attempt, so copying explicit ids would collide
    on the primary key. They are re-sequenced instead; nothing meaningful is in
    the id."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    source_ops = {e["operation"] for e in invoke(ctx, "audit", {"limit": 500})["entries"]}

    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    restored = invoke(tctx, "audit", {"limit": 500})["entries"]
    assert source_ops <= {e["operation"] for e in restored}
    ids = [e["id"] for e in restored if "id" in e]
    assert len(ids) == len(set(ids)), "duplicate audit ids after restore"


def test_no_credential_survives_into_the_restored_database(
    source, target, media_file, tmp_path
):
    """The canary, followed all the way through the round trip rather than only
    into the artefact."""
    cfg, ctx = source
    tcfg, tctx = target
    _full_history(ctx, media_file)
    invoke(tctx, "restore-permanent-set", {"source": str(_export(ctx, tmp_path))})

    dump = "".join(str(row) for row in tctx.conn.iterdump())
    assert CANARY not in dump
