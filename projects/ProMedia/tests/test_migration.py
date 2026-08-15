"""T-037 — the schema 1 -> 2 migration.

`schema.sql` is CREATE TABLE IF NOT EXISTS throughout, which builds a fresh
database correctly and is completely INERT against an existing one. So a
changed column definition reaches new installs and silently misses every
existing one — including the operator's, which is the only database in this
project that holds anything irreplaceable.

These tests run against a database built the way the operator's actually was:
at version 1, with real rows in it.
"""

from __future__ import annotations

import sqlite3

import pytest

from promedia.core import db
from promedia.errors import ProMediaError

# The v1 assets table, exactly as it stood before this task: no 'absent'.
V1_ASSETS = """
CREATE TABLE assets (
    id                TEXT PRIMARY KEY,
    content_hash      TEXT NOT NULL UNIQUE,
    byte_size         INTEGER NOT NULL CHECK (byte_size >= 0),
    original_filename TEXT NOT NULL,
    mime_type         TEXT,
    duration_seconds  REAL,
    probe_status      TEXT NOT NULL CHECK (probe_status IN ('ok', 'unavailable', 'failed')),
    derived_from      TEXT REFERENCES assets (id) ON DELETE SET NULL,
    state             TEXT NOT NULL CHECK (state IN ('stored', 'deleted')),
    ingested_at       TEXT NOT NULL,
    object_path       TEXT
);
"""


@pytest.fixture
def v1_db(tmp_path):
    """A version-1 database with rows, as an existing install would be."""
    path = tmp_path / "v1.db"
    conn = db.connect(path)
    # Build the full current schema, then swap assets back to its v1 shape so
    # the fixture differs from HEAD only in the thing being migrated.
    conn.executescript(db._SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("DROP TABLE assets")
    conn.executescript(V1_ASSETS)
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (1, ?)", (db.iso(),))
    conn.execute(
        "INSERT INTO assets (id, content_hash, byte_size, original_filename,"
        " probe_status, state, ingested_at, object_path)"
        " VALUES ('as_kept', 'hash_kept', 10, 'a.mp4', 'ok', 'stored', ?, '/objects/a')",
        (db.iso(),),
    )
    conn.execute(
        "INSERT INTO assets (id, content_hash, byte_size, original_filename,"
        " probe_status, state, ingested_at, object_path)"
        " VALUES ('as_gone', 'hash_gone', 20, 'b.mp4', 'ok', 'deleted', ?, NULL)",
        (db.iso(),),
    )
    yield conn, path
    conn.close()


def test_the_fixture_really_is_version_one(v1_db):
    """Otherwise every assertion below proves nothing."""
    conn, _ = v1_db
    assert db.schema_version(conn) == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO assets (id, content_hash, byte_size, original_filename,"
            " probe_status, state, ingested_at) VALUES ('x', 'h', 1, 'c.mp4',"
            " 'ok', 'absent', '2026-01-01')"
        )


def test_migration_admits_the_new_state(v1_db):
    conn, _ = v1_db
    db.migrate(conn)

    assert db.schema_version(conn) == 2
    conn.execute(
        "INSERT INTO assets (id, content_hash, byte_size, original_filename,"
        " probe_status, state, ingested_at) VALUES ('x', 'h', 1, 'c.mp4',"
        " 'ok', 'absent', '2026-01-01')"
    )
    assert conn.execute("SELECT state FROM assets WHERE id = 'x'").fetchone()[0] == "absent"


def test_no_row_changes_meaning(v1_db):
    """The migration widens what is permitted. It must not rewrite anything."""
    conn, _ = v1_db
    before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM assets")}

    db.migrate(conn)

    after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM assets")}
    assert after == before


def test_the_old_constraint_is_actually_gone_not_just_widened_in_name(v1_db):
    """A rebuild that copied the OLD table definition would still reject
    'absent' while reporting version 2 — the failure that looks like success."""
    conn, _ = v1_db
    db.migrate(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'assets'"
    ).fetchone()[0]
    assert "'absent'" in sql


def test_migration_is_idempotent(v1_db):
    conn, _ = v1_db
    assert db.migrate(conn) == [2]
    assert db.migrate(conn) == []
    assert db.schema_version(conn) == 2


def test_apply_schema_migrates_an_existing_database(v1_db):
    """The path an operator actually takes: they never run a migration command,
    they just start the application."""
    conn, _ = v1_db
    db.apply_schema(conn)
    assert db.schema_version(conn) == 2


def test_foreign_keys_are_on_again_afterwards(v1_db):
    """They are switched off for the table rebuild. Leaving them off would
    disable the referential integrity T-002 turned on, for the rest of the
    connection's life."""
    conn, _ = v1_db
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.migrate(conn)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_referencing_rows_survive_the_rebuild(v1_db):
    """assets is dropped and recreated, and posts/rights rows reference it.
    Losing them to a cascade would be a data-loss migration."""
    conn, _ = v1_db
    conn.execute(
        "INSERT INTO rights_declarations (id, asset_id, authorship,"
        " third_party_material, declared_by, declared_by_kind, declared_at)"
        " VALUES ('rd_1', 'as_kept', 'operator_original', '[]', 'op', 'operator', ?)",
        (db.iso(),),
    )

    db.migrate(conn)

    assert conn.execute(
        "SELECT 1 FROM rights_declarations WHERE asset_id = 'as_kept'"
    ).fetchone(), "a referencing row was lost in the rebuild"


def test_a_newer_database_is_refused_rather_than_downgraded(v1_db):
    """An older build must not write to a database it does not understand."""
    conn, _ = v1_db
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (db.SCHEMA_VERSION + 3, db.iso()),
    )
    with pytest.raises(ProMediaError) as excinfo:
        db.migrate(conn)
    assert "newer" in str(excinfo.value).lower()
