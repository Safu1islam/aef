"""Backup capabilities (T-036).

Registered like everything else, so the permanent set can be exported from
either surface (F-1) and, later, by the scheduler (T-039) through the same
repo-callable path the operator uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...errors import NotFound, ValidationError
from .. import backup
from ..registry import Context, Param, register


@register(
    "export-permanent-set",
    "Export rights evidence, provenance, publication and audit records to a verifiable artefact.",
    params=(
        Param(
            "destination",
            "str",
            required=False,
            help=(
                "File path to write the artefact to. Omit to return it inline,"
                " which is useful for inspection but not for backup."
            ),
        ),
    ),
    authority="operator",
    mutates=False,
    danger="Writes the rights, provenance and audit record to a file you choose the location of.",
)
def export_permanent_set(ctx: Context, destination: str | None = None) -> dict[str, Any]:
    """Operator authority despite being read-only.

    Every other read here is agent-callable, and this one is not: it collects
    the entire audit log and publication history into a single portable file
    whose location the caller chooses. That is a capability worth a human, and
    the authority check is the only thing standing between "an agent may read
    the audit log" and "an agent may write the whole of it anywhere it likes".
    """
    artefact = backup.build(ctx.conn)
    summary = {
        "ok": True,
        "artefact_version": artefact["artefact_version"],
        "schema_version": artefact["schema_version"],
        "created_at": artefact["created_at"],
        "row_counts": artefact["row_counts"],
        "excluded_tables": sorted(artefact["excluded_tables"]),
        "integrity_hash": artefact["integrity_hash"],
        "note": artefact["note"],
    }

    if destination is None:
        return {**summary, "written_to": None, "artefact": artefact}

    path = Path(destination).expanduser()
    if path.is_dir():
        raise ValidationError(
            f"destination '{destination}' is a directory; name the file to write",
            parameter="destination",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(backup.dumps(artefact), encoding="utf-8")
    return {**summary, "written_to": str(path), "bytes": path.stat().st_size}


@register(
    "verify-backup",
    "Check a backup artefact against its own integrity hash.",
    params=(Param("source", "str", help="Path to an artefact written by export-permanent-set."),),
)
def verify_backup(ctx: Context, source: str) -> dict[str, Any]:
    """Agent-readable: verifying a backup must be cheap and frequent.

    Deliberately does not consult the database. An artefact that can only be
    verified against the system that produced it is not verifiable off-site,
    which is the only place it will ever matter.
    """
    path = Path(source).expanduser()
    if not path.is_file():
        raise NotFound(f"no backup artefact at '{source}'", source=str(path))
    try:
        artefact = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValidationError(
            f"'{source}' is not readable as a backup artefact: {exc}", parameter="source"
        ) from exc
    return {**backup.verify(artefact), "source": str(path)}


@register(
    "restore-permanent-set",
    "Rebuild rights, provenance, publication and audit records from a backup artefact.",
    params=(Param("source", "str", help="Path to an artefact written by export-permanent-set."),),
    authority="operator",
    mutates=True,
    danger=(
        "Writes the entire permanent record into this database. Only possible on an"
        " empty one, and it does NOT restore media."
    ),
)
def restore_permanent_set(ctx: Context, source: str) -> dict[str, Any]:
    """Operator authority: this reconstitutes the whole rights and audit history.

    No entity lock is taken. The operation is only permitted against an empty
    database, so there is no existing entity for another agent to be holding,
    and a lock over 'every entity at once' is not a thing C-19 expresses.
    """
    from .. import db as db_layer

    path = Path(source).expanduser()
    if not path.is_file():
        raise NotFound(f"no backup artefact at '{source}'", source=str(path))
    try:
        artefact = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValidationError(
            f"'{source}' is not readable as a backup artefact: {exc}", parameter="source"
        ) from exc

    result = backup.restore(
        ctx.conn, artefact, build_schema_version=db_layer.SCHEMA_VERSION
    )
    return {**result, "source": str(path)}


@register(
    "backup-scope",
    "Report which tables are backed up, which are excluded, and why.",
)
def backup_scope(ctx: Context) -> dict[str, Any]:
    """What a restore will and will not bring back, before it is needed.

    The question this answers — "is my media in the backup?" — has a surprising
    answer (no, by policy), and the worst time to discover it is during a
    recovery.
    """
    classified = backup.classify_tables(ctx.conn)
    return {
        "ok": True,
        "permanent": classified["permanent"],
        "excluded": {t: backup.TRANSIENT_TABLES[t] for t in classified["transient"]},
        "media_included": False,
        "note": backup.build(ctx.conn)["note"],
    }
