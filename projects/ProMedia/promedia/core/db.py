"""SQLite access layer (DR-003).

All database access goes through here so the store stays replaceable. Pragmas
are applied per connection, not once at creation: SQLite scopes foreign_keys
and busy_timeout to the connection, so setting them at schema time would leave
later connections silently unprotected.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..errors import EntityLocked, ProMediaError

SCHEMA_VERSION = 2
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime | None = None) -> str:
    return (moment or now()).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def canonical_json(payload: Any) -> str:
    """Stable serialisation.

    Determinism (C-20) and integrity hashing (F-8) both depend on identical
    input producing identical bytes, so key order and separators are fixed.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def connect(db_path: Path, *, busy_timeout_ms: int | None = None) -> sqlite3.Connection:
    """Open a connection with the pragmas C-19 concurrency depends on.

    T-030 (O3): ``busy_timeout_ms`` defaulted to a literal 5000 here while
    ``database.busy_timeout_ms`` is what configuration says. With up to four
    concurrent sessions (C-18) this is the value that decides whether a
    contended write waits or fails, so a caller raising it in promedia.toml and
    seeing no effect is exactly the silent failure protocol 05 forbids.
    Resolved from configuration when not supplied; the parameter stays so tests
    can pin a short timeout without a config file.
    """
    if busy_timeout_ms is None:
        from ..config import DEFAULTS

        busy_timeout_ms = DEFAULTS["database"]["busy_timeout_ms"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    # WAL is a persistent database property, but setting it per connection is
    # harmless and guarantees it even on a database created elsewhere.
    # In-memory databases do not support WAL; ignore the failure there.
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:  # pragma: no cover
        pass
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create anything missing, then migrate anything outdated.

    ``schema.sql`` is CREATE TABLE IF NOT EXISTS throughout, so it builds a
    fresh database but is INERT against an existing one — including when a
    column definition in it has changed. That is why migrate() exists and why
    it runs on every open: an operator whose database predates a change never
    runs a migration command, they just start the application.
    """
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    if row is None or row["v"] is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, iso()),
        )
        return
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Bring an existing database up to SCHEMA_VERSION. Returns versions applied.

    Idempotent: re-running against a current database does nothing and returns
    an empty list.
    """
    applied: list[int] = []
    current = schema_version(conn)
    if current > SCHEMA_VERSION:
        raise ProMediaError(
            f"database is at schema version {current}, newer than this build "
            f"understands ({SCHEMA_VERSION}); upgrade ProMedia rather than "
            "letting an older build write to it",
            database_version=current,
            build_version=SCHEMA_VERSION,
        )
    if current < 2:
        _migrate_1_to_2(conn)
        applied.append(2)
    if applied:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, iso()),
        )
    return applied


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """assets.state gains 'absent' (T-037).

    SQLite cannot ALTER a CHECK constraint, so the table is rebuilt: create,
    copy, drop, rename — the standard 12-step recipe, reduced to what this case
    needs. Two details that are easy to get wrong and expensive to get wrong:

      * foreign_keys must be OFF for the duration. It is ON per connection
        (DR-003), and dropping a table that posts/rights rows reference would
        otherwise either fail or, with deferred enforcement, cascade. It is
        restored afterwards regardless of outcome.
      * PRAGMA legacy_alter_table is not used, and the rename happens INSIDE
        the transaction, so a crash mid-migration leaves the original table
        intact rather than a half-copied one.

    No row changes meaning: every existing asset keeps its exact state. This
    migration only widens what is permitted.
    """
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with transaction(conn):
            conn.execute("""
                CREATE TABLE assets_migrated (
                    id                TEXT PRIMARY KEY,
                    content_hash      TEXT NOT NULL UNIQUE,
                    byte_size         INTEGER NOT NULL CHECK (byte_size >= 0),
                    original_filename TEXT NOT NULL,
                    mime_type         TEXT,
                    duration_seconds  REAL,
                    probe_status      TEXT NOT NULL
                                      CHECK (probe_status IN ('ok', 'unavailable', 'failed')),
                    derived_from      TEXT REFERENCES assets (id) ON DELETE SET NULL,
                    state             TEXT NOT NULL
                                      CHECK (state IN ('stored', 'deleted', 'absent')),
                    ingested_at       TEXT NOT NULL,
                    object_path       TEXT
                )
            """)
            conn.execute("INSERT INTO assets_migrated SELECT * FROM assets")
            conn.execute("DROP TABLE assets")
            conn.execute("ALTER TABLE assets_migrated RENAME TO assets")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets (content_hash)"
            )
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction.

    isolation_level=None means autocommit, so transactions are stated rather
    than implied. IMMEDIATE takes the write lock up front, which is what makes
    the publish claim in posts.py safe against a concurrent tick (DR-009).
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- entity locks (C-19) ------------------------------------------------------


def acquire_lock(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    *,
    task_id: str,
    agent: str,
    model: str,
    ttl_minutes: int,
) -> None:
    """Take exclusive ownership of an entity.

    Raises EntityLocked naming the current owner. Expired locks are reclaimable
    — a crashed agent must not block an entity forever.
    """
    moment = now()
    with transaction(conn):
        row = conn.execute(
            "SELECT agent, task_id, expires_at FROM entity_locks WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        ).fetchone()
        if row is not None:
            expires = datetime.fromisoformat(row["expires_at"])
            if expires > moment and row["agent"] != agent:
                raise EntityLocked(
                    f"{entity_type} {entity_id} is locked by {row['agent']}",
                    entity_type=entity_type,
                    entity_id=entity_id,
                    owner=row["agent"],
                    owner_task=row["task_id"],
                    expires_at=row["expires_at"],
                )
            conn.execute(
                "DELETE FROM entity_locks WHERE entity_type = ? AND entity_id = ?",
                (entity_type, entity_id),
            )
        conn.execute(
            "INSERT INTO entity_locks (entity_type, entity_id, task_id, agent, model,"
            " acquired_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entity_type,
                entity_id,
                task_id,
                agent,
                model,
                iso(moment),
                iso(moment + timedelta(minutes=ttl_minutes)),
            ),
        )


def release_lock(conn: sqlite3.Connection, entity_type: str, entity_id: str, *, agent: str) -> bool:
    cur = conn.execute(
        "DELETE FROM entity_locks WHERE entity_type = ? AND entity_id = ? AND agent = ?",
        (entity_type, entity_id, agent),
    )
    return cur.rowcount > 0


def list_locks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM entity_locks ORDER BY acquired_at")]
