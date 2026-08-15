"""T-002 — schema, pragmas, entity locks (C-19)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from promedia.core import db
from promedia.errors import EntityLocked


def test_fresh_database_applies_schema(conn):
    """AC-1.

    The literal version is pinned rather than compared to db.SCHEMA_VERSION,
    which would be the tautology T-028 removed from the parity gate — a value
    checked against itself cannot fail. Bumping it here is meant to be a
    deliberate edit accompanying a deliberate migration. Was 1 until T-037
    widened assets.state to admit 'absent'.
    """
    assert db.schema_version(conn) == 2
    tables = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    for expected in (
        "assets", "rights_declarations", "evidence", "rights_verdicts",
        "provenance_records", "posts", "approvals", "publications",
        "storage_ledger", "ingest_queue", "entity_locks", "audit_log",
    ):
        assert expected in tables


def test_pragmas_active(conn):
    """AC-2: pragmas are per connection, so they must hold on this one."""
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_foreign_keys_enforced(conn):
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rights_declarations (id, asset_id, authorship, third_party_material,"
            " declared_by, declared_at) VALUES ('d1', 'nonexistent', 'unknown', '[]', 'x', 'now')"
        )


def test_second_agent_cannot_lock_owned_entity(conn):
    """AC-3: the error names the owner, so the blocked agent can act on it."""
    db.acquire_lock(conn, "asset", "as_1", task_id="T-1", agent="agent-a", model="m", ttl_minutes=90)
    with pytest.raises(EntityLocked) as excinfo:
        db.acquire_lock(conn, "asset", "as_1", task_id="T-2", agent="agent-b", model="m", ttl_minutes=90)
    assert excinfo.value.detail["owner"] == "agent-a"
    assert excinfo.value.detail["owner_task"] == "T-1"


def test_same_agent_may_reacquire(conn):
    db.acquire_lock(conn, "asset", "as_2", task_id="T-1", agent="agent-a", model="m", ttl_minutes=90)
    db.acquire_lock(conn, "asset", "as_2", task_id="T-1", agent="agent-a", model="m", ttl_minutes=90)


def test_expired_lock_reclaimable(conn):
    """AC-4: a crashed agent must not hold an entity for ever."""
    db.acquire_lock(conn, "asset", "as_3", task_id="T-1", agent="agent-a", model="m", ttl_minutes=90)
    past = db.iso(db.now() - timedelta(minutes=5))
    conn.execute(
        "UPDATE entity_locks SET expires_at = ? WHERE entity_type = 'asset' AND entity_id = 'as_3'",
        (past,),
    )
    db.acquire_lock(conn, "asset", "as_3", task_id="T-2", agent="agent-b", model="m", ttl_minutes=90)
    owner = conn.execute(
        "SELECT agent FROM entity_locks WHERE entity_id = 'as_3'"
    ).fetchone()["agent"]
    assert owner == "agent-b"


def test_release_lock_is_owner_scoped(conn):
    db.acquire_lock(conn, "asset", "as_4", task_id="T-1", agent="agent-a", model="m", ttl_minutes=90)
    assert db.release_lock(conn, "asset", "as_4", agent="agent-b") is False
    assert db.release_lock(conn, "asset", "as_4", agent="agent-a") is True


def test_canonical_json_is_order_independent():
    """Determinism (C-20) and integrity hashing (F-8) both depend on this."""
    assert db.canonical_json({"b": 1, "a": 2}) == db.canonical_json({"a": 2, "b": 1})
