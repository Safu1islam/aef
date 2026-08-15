"""Export of the permanent set (T-036, project.md 5.4).

project.md divides everything this system holds into two sets. The PERMANENT
set — rights evidence, provenance chains, published-post records, the approval
and audit log — must survive disk loss. The TRANSIENT set — masters, proxies,
renders, caches — is deleted by retention policy or is recomputable, and is
explicitly out of scope for backup. That division is why this is cheap: masters
are the only large thing here, and they are the thing deliberately not backed
up, so the artefact is megabytes.

What makes this task non-trivial is not moving bytes. It is three properties
that are easy to lose and expensive to lose silently:

* **No secret may ride along.** DR-008 keeps credentials out of the database
  precisely so they are absent from backup artefacts. An export that carried a
  credential off-site would defeat the control the entire credential design
  exists to provide, and it would do so invisibly — the artefact would look
  fine. Asserted with a canary in tests, never by reading the code.
* **Provenance must still verify after the round trip.** F-8 makes a sealed
  record self-contained and integrity-hashed. A backup of evidence whose
  integrity check fails on restore is a backup of bytes, not of evidence.
* **A new table must be classified, not defaulted.** The failure mode here is a
  table added to the schema years from now that silently falls outside the
  export and is discovered missing during a restore. ``classify_tables`` refuses
  to let that happen quietly.

Where the artefact GOES is deliberately not this module's business (T-038). What
to back up and where to put it are separate questions, and answering them
together is how the second answer quietly constrains the first.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..errors import ProMediaError, ValidationError
from .db import canonical_json, iso, transaction

ARTEFACT_VERSION = 1

# The permanent set of project.md 5.4, mapped to tables. Order matters on
# restore: a table is listed after everything it references, so a straight
# replay satisfies the foreign keys T-002 turned on.
PERMANENT_TABLES: tuple[str, ...] = (
    "schema_version",
    # Identity of the things evidence is ABOUT. The asset row is not the media —
    # the media is transient and its bytes are not here. Without it, a published
    # post and its rights evidence would reference an id with nothing behind it.
    "accounts",
    "assets",
    # Rights evidence (5.4, first bullet).
    "rights_declarations",
    "evidence",
    "rights_verdicts",
    # Provenance chains (second bullet).
    "provenance_records",
    # Published-post records (third bullet).
    "posts",
    "publications",
    # Approval and audit log (fourth bullet).
    "approvals",
    "audit_log",
    # Media projects (T-042). Not named in 5.4's four bullets, which predate the
    # production platform, but they belong here on the same reasoning: an EDL is
    # the operator's actual creative work, it is irreplaceable if lost, and it is
    # kilobytes. The media it references is transient by policy and is NOT here —
    # so a restored project comes back as an edit whose sources may need
    # re-ingesting, exactly like a restored asset.
    # Ordered after `assets` because the edits reference asset ids, and before
    # nothing, since nothing references a project.
    "projects",
    "project_edl_versions",
    # C-31 spend ledger (T-048). Classified PERMANENT, not transient — it is
    # the operator's actual money history against the $100/month ceiling,
    # not a cache recomputable from anything else on this machine. Nothing
    # produced it references this table (no foreign key points at it, and
    # it references nothing), so its position in this tuple is arbitrary;
    # placed last because it was added last. It is tiny: this ledger will
    # hold, at the project's own $5 per-operation cap, at most a few hundred
    # rows a year even at sustained maximum spend, nowhere near the scale
    # that made masters transient (project.md 5.4's A-4 reasoning, applied
    # to a second small permanent table rather than reopened from scratch).
    "spend_ledger",
    # Brand kits (T-068, DR-021). PERMANENT for the same reason accounts and
    # projects are: small, operator-authored configuration with no other
    # record of itself anywhere in the system — losing it would mean the
    # operator re-typing a name, two colours and a font, not a cache miss.
    # DR-021's own point is that a RENDER never depends on this table (the
    # logo is baked into the EDL as an ImageOverlay at apply time), but that
    # is an argument for why the row is safely DELETABLE after use, not an
    # argument for excluding it from backup while it still exists. Ordered
    # after `assets` (its logo_asset_id foreign key target) and last because
    # it was added last, same convention as spend_ledger above.
    "brand_kits",
)

# Excluded, each for a stated reason. Being listed here is what makes the
# exclusion a decision; a table in neither tuple is an unclassified table and
# classify_tables() refuses it.
TRANSIENT_TABLES: dict[str, str] = {
    "storage_ledger": (
        "Facts about THIS machine's disk, recomputed from what is actually "
        "stored. Restoring a ledger from another point in time would assert a "
        "committed footprint that does not match the files present, and DR-006 "
        "makes the ledger the sole source of truth for the F-7 ceiling — so a "
        "stale one is worse than an absent one."
    ),
    "ingest_queue": (
        "Work waiting on local storage that was refused at admission. It is "
        "about a disk state that no longer exists after a restore."
    ),
    "entity_locks": (
        "Session state (C-19). Restoring locks would wedge every listed entity "
        "against agent sessions that no longer exist, and the TTL that normally "
        "reclaims them would be measured from a timestamp in the past."
    ),
    "renders": (
        "Records of output FILES on this machine, and those files are transient "
        "by policy — not in the artefact, and reproducible from the EDL version "
        "plus its sources, which ARE backed up. Restoring these rows would "
        "assert a set of renders whose files do not exist, which is the phantom "
        "problem T-029 closed for assets, reintroduced one table over. The edit "
        "that produced any of them survives in project_edl_versions."
    ),
}


def classify_tables(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Every real table, split into permanent and transient. Refuses surprises.

    AC-1. The point is the failure: a table added to the schema later is in
    neither tuple, so this raises instead of quietly leaving it out of every
    future backup. Discovering that during a restore is the expensive version of
    the same discovery.
    """
    actual = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    known = set(PERMANENT_TABLES) | set(TRANSIENT_TABLES)
    unclassified = sorted(actual - known)
    if unclassified:
        raise ProMediaError(
            f"table(s) {unclassified} are in the schema but classified neither "
            "permanent nor transient; classify them in promedia.core.backup "
            "before taking a backup that would silently omit them",
            unclassified=unclassified,
        )
    missing = sorted(set(PERMANENT_TABLES) - actual)
    if missing:
        raise ProMediaError(
            f"permanent table(s) {missing} are declared but absent from this "
            "database; the schema and the backup definition disagree",
            missing=missing,
        )
    return {
        "permanent": [t for t in PERMANENT_TABLES if t in actual],
        "transient": sorted(t for t in TRANSIENT_TABLES if t in actual),
    }


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    # Ordered by rowid so two exports of an unchanged database are byte-identical,
    # which is what lets a caller detect "nothing changed" without a diff.
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")  # noqa: S608 - fixed set
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def build(conn: sqlite3.Connection, *, at: str | None = None) -> dict[str, Any]:
    """The artefact, as a dictionary. Self-contained and self-describing.

    The integrity hash covers the payload only, computed over canonical JSON —
    the same construction provenance sealing uses (F-8), so the two agree about
    what "unchanged" means.
    """
    classified = classify_tables(conn)
    payload = {table: _rows(conn, table) for table in classified["permanent"]}
    schema_version = None
    if payload.get("schema_version"):
        schema_version = payload["schema_version"][-1].get("version")

    return {
        "artefact_version": ARTEFACT_VERSION,
        "schema_version": schema_version,
        "created_at": at or iso(),
        "tables": list(payload),
        "excluded_tables": {t: TRANSIENT_TABLES[t] for t in classified["transient"]},
        "row_counts": {table: len(rows) for table, rows in payload.items()},
        "payload": payload,
        "integrity_hash": integrity_hash(payload),
        # Said plainly inside the artefact, because someone restoring one in
        # five years will not have read this module. F-7/5.4: masters are
        # transient by policy and are NOT here.
        "note": (
            "Permanent set only (project.md 5.4). Media masters, proxies, renders "
            "and caches are transient by policy and are NOT contained in this "
            "artefact; restoring it recovers the rights, provenance, publication "
            "and audit record, not the media."
        ),
    }


def integrity_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def verify(artefact: dict[str, Any]) -> dict[str, Any]:
    """Check an artefact against its own hash. AC-4.

    Takes the artefact rather than a path so it can be verified wherever it has
    got to — after transport, after storage, on a machine that has no ProMedia
    database at all.
    """
    if not isinstance(artefact, dict) or "payload" not in artefact:
        raise ValidationError("not a ProMedia backup artefact", got=type(artefact).__name__)
    expected = artefact.get("integrity_hash")
    actual = integrity_hash(artefact["payload"])
    return {
        "ok": expected == actual,
        "integrity_verified": expected == actual,
        "artefact_version": artefact.get("artefact_version"),
        "schema_version": artefact.get("schema_version"),
        "created_at": artefact.get("created_at"),
        "row_counts": artefact.get("row_counts", {}),
        "expected_hash": expected,
        "actual_hash": actual,
    }


def dumps(artefact: dict[str, Any]) -> str:
    return json.dumps(artefact, indent=2, sort_keys=True, default=str)


# --- restore (T-037) ---------------------------------------------------------
#
# The half that makes the export a backup rather than a file. Written as a
# separate reader deliberately: a format only its own writer can parse is the
# classic way a backup regime turns out not to be one.


# Excluded from the emptiness check, and NOT for symmetry with the export.
#
#   schema_version — every database has one; requiring it absent would mean
#     nothing could ever be restored into a database that exists.
#   audit_log — every ATTEMPT to restore is audited, including one that is
#     refused. Counting it as "non-empty" made the operation un-retryable: a
#     restore that failed integrity would write a denial entry, and the next
#     attempt would be refused for a non-empty database caused entirely by the
#     first. Found by test_an_agent_cannot_restore, which asserted no rows were
#     written after a refusal and was right to.
_EMPTINESS_EXEMPT = {"schema_version", "audit_log"}


def _is_empty(conn: sqlite3.Connection) -> bool:
    """No substantive permanent rows. See _EMPTINESS_EXEMPT for what is ignored."""
    for table in PERMANENT_TABLES:
        if table in _EMPTINESS_EXEMPT:
            continue
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():  # noqa: S608 - fixed set
            return False
    return True


def restore(
    conn: sqlite3.Connection, artefact: dict[str, Any], *, build_schema_version: int
) -> dict[str, Any]:
    """Rebuild the permanent set from an artefact, into an empty database.

    Three refusals, each protecting something specific:

    * **A tampered artefact** is refused before a single row is written. Half a
      restore is worse than none, because it looks like a whole one.
    * **An artefact from a newer schema** is refused rather than imported
      partially. An older build cannot know what a later column means, and
      guessing would corrupt records that are, by definition, irreplaceable.
    * **A non-empty database** is refused. A merge would have to answer "which
      version of this row wins", and there is no answer that is right in
      general — the audit log would silently gain or lose entries depending on
      it. Refusing sends the operator to an empty database, where the outcome
      is knowable.

    Media is NOT restored, because it was never in the artefact. Assets that
    were 'stored' come back as 'absent': the record is here, the bytes are not,
    and re-ingesting them is permitted (unlike 'deleted', which retention made
    final). That distinction is the whole reason this needed a schema change.
    """
    verified = verify(artefact)
    if not verified["integrity_verified"]:
        raise ProMediaError(
            "backup artefact failed integrity verification; refusing to restore "
            "from it rather than write a partial or altered permanent record",
            expected_hash=verified["expected_hash"],
            actual_hash=verified["actual_hash"],
        )

    artefact_version = artefact.get("artefact_version")
    if artefact_version != ARTEFACT_VERSION:
        raise ProMediaError(
            f"artefact format version {artefact_version} is not version "
            f"{ARTEFACT_VERSION}, which this build writes and reads",
            artefact_version=artefact_version,
            supported=ARTEFACT_VERSION,
        )

    source_schema = artefact.get("schema_version")
    if source_schema is not None and int(source_schema) > build_schema_version:
        raise ProMediaError(
            f"artefact was written from schema version {source_schema}, newer "
            f"than this build understands ({build_schema_version}); upgrade "
            "ProMedia before restoring rather than importing it partially",
            artefact_schema_version=int(source_schema),
            build_schema_version=build_schema_version,
        )

    if not _is_empty(conn):
        raise ProMediaError(
            "refusing to restore into a database that already holds permanent "
            "records; restore into an empty database, because merging two "
            "histories has no generally correct answer and would silently "
            "change the audit log",
            remedy="move the existing database aside, then restore",
        )

    payload = artefact["payload"]
    restored: dict[str, int] = {}
    absent_assets = 0

    # foreign_keys stays ON: PERMANENT_TABLES is ordered so referenced rows land
    # before the rows referencing them, and if that order is ever wrong this
    # should fail loudly rather than build a database with dangling references.
    with transaction(conn):
        for table in PERMANENT_TABLES:
            rows = payload.get(table) or []
            if table == "schema_version":
                # Not copied. The database's own version is a fact about THIS
                # database and its migrations, not about the artefact.
                continue
            for row in rows:
                row = dict(row)
                if table == "audit_log":
                    # Drop the surrogate id and let SQLite re-sequence. The
                    # local log already holds entries for the restore attempt
                    # itself, and copying explicit AUTOINCREMENT ids on top of
                    # them collides on the primary key. Nothing is lost: the id
                    # carries no meaning, and order is preserved by `at` and by
                    # insertion sequence.
                    row.pop("id", None)
                if table == "assets" and row.get("state") == "stored":
                    # The bytes are not in the artefact and never were.
                    row["state"] = "absent"
                    row["object_path"] = None
                    absent_assets += 1
                columns = ", ".join(row)
                placeholders = ", ".join("?" for _ in row)
                conn.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",  # noqa: S608
                    tuple(row.values()),
                )
            restored[table] = len(rows)

    return {
        "ok": True,
        "restored": restored,
        "assets_marked_absent": absent_assets,
        "created_at": artefact.get("created_at"),
        "integrity_verified": True,
        "media_restored": False,
        "note": (
            f"{absent_assets} asset(s) restored as 'absent': the rights, provenance "
            "and publication record is back, the media is not. Re-ingesting the "
            "original file returns an absent asset to 'stored'. Assets that "
            "retention had deleted stay 'deleted' and are NOT re-ingestable."
        ),
    }
