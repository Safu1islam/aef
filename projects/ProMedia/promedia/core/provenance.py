"""Provenance sealing (T-010, F-8).

A sealed provenance record must answer, years later and with the media long
deleted, the question "on what basis was this published". It therefore:

  * embeds the declaration, the full evidence set and the verdict, rather than
    referencing rows that retention will remove;
  * carries the content hash as identity and NO filesystem path;
  * has no foreign key to ``assets``, so deleting the asset cannot cascade it
    away;
  * carries an integrity hash so tampering is detectable.

The failure this design exists to prevent is a provenance record that is a set
of pointers into tables that no longer have rows.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..errors import IntegrityError, NotFound
from . import rights
from .db import canonical_json, iso, new_id
from .registry import Context

RECORD_SCHEMA_VERSION = 1


def _integrity_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def seal(ctx: Context, asset_id: str) -> dict[str, Any]:
    """Freeze the rights position of an asset into a self-contained record."""
    asset = ctx.conn.execute(
        "SELECT * FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    if asset is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)

    decl = ctx.conn.execute(
        "SELECT * FROM rights_declarations WHERE asset_id = ? ORDER BY declared_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    if decl is None:
        raise NotFound(f"asset {asset_id} has no rights declaration", asset_id=asset_id)

    # T-030 (N6). This was its own "ORDER BY decided_at DESC LIMIT 1", which
    # differs from rights.latest_verdict's "ORDER BY decided_at DESC, id DESC"
    # by a tiebreaker. Two verdicts sharing a decided_at — determine-rights
    # called twice in the same clock tick, which is ordinary on Windows, where
    # the timer granularity is coarse — and SQLite was free to return either
    # row. The seal could then freeze a DIFFERENT verdict than the one the
    # publish gate enforces, and the record's whole purpose (F-8) is to be the
    # durable account of what was decided.
    #
    # Fixed by deleting the second copy rather than syncing it: one authority on
    # "which verdict is current", for the same reason T-032 collapsed the four
    # duplicated status maps into one. A tiebreaker that must be repeated in
    # every caller is a tiebreaker that will eventually not be.
    verdict = rights.latest_verdict(ctx, asset_id)
    if verdict is None:
        raise NotFound(
            f"asset {asset_id} has no rights verdict; determine rights before sealing",
            asset_id=asset_id,
        )

    evidence = [
        dict(r)
        for r in ctx.conn.execute(
            "SELECT kind, body, confidence, produced_by, model_id, created_at"
            " FROM evidence WHERE asset_id = ? ORDER BY created_at, id",
            (asset_id,),
        )
    ]

    payload = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "content_hash": asset["content_hash"],
        "byte_size": asset["byte_size"],
        "original_filename": asset["original_filename"],
        "duration_seconds": asset["duration_seconds"],
        "probe_status": asset["probe_status"],
        "derived_from": asset["derived_from"],
        "declaration": {
            "authorship": decl["authorship"],
            "third_party_material": json.loads(decl["third_party_material"]),
            "source_url": decl["source_url"],
            "licence_grantor": decl["licence_grantor"],
            "licence_scope": decl["licence_scope"],
            "licence_evidence_ref": decl["licence_evidence_ref"],
            "public_domain_source": decl["public_domain_source"],
            "declared_by": decl["declared_by"],
            "declared_by_kind": decl["declared_by_kind"],
            "declared_at": decl["declared_at"],
        },
        "evidence": evidence,
        "verdict": {
            "verdict": verdict["verdict"],
            "matched_rule": verdict["matched_rule"],
            "reasons": json.loads(verdict["reasons"]),
            "ruleset": verdict["ruleset"],
            "ruleset_version": verdict["ruleset_version"],
            "jurisdiction": verdict["jurisdiction"],
            "evidence_digest": verdict["evidence_digest"],
            "decided_at": verdict["decided_at"],
            "decided_by": verdict["decided_by"],
        },
        "sealed_at": iso(),
    }

    record_id = new_id("prov")
    ctx.conn.execute(
        "INSERT INTO provenance_records (id, asset_id, content_hash, schema_version,"
        " payload, integrity_hash, sealed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record_id,
            asset_id,
            asset["content_hash"],
            RECORD_SCHEMA_VERSION,
            canonical_json(payload),
            _integrity_hash(payload),
            payload["sealed_at"],
        ),
    )
    return {
        "ok": True,
        "provenance_id": record_id,
        "content_hash": asset["content_hash"],
        "verdict": verdict["verdict"],
        "integrity_hash": _integrity_hash(payload),
    }


def read(conn: sqlite3.Connection, provenance_id: str) -> dict[str, Any]:
    """Read a sealed record and verify it. Works with the asset long gone."""
    row = conn.execute(
        "SELECT * FROM provenance_records WHERE id = ?", (provenance_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"no provenance record {provenance_id}", provenance_id=provenance_id)
    payload = json.loads(row["payload"])
    if _integrity_hash(payload) != row["integrity_hash"]:
        raise IntegrityError(
            f"provenance record {provenance_id} failed integrity verification",
            provenance_id=provenance_id,
        )
    return {
        "ok": True,
        "provenance_id": row["id"],
        "content_hash": row["content_hash"],
        "sealed_at": row["sealed_at"],
        "integrity_verified": True,
        "payload": payload,
    }


def verify(conn: sqlite3.Connection, provenance_id: str) -> dict[str, Any]:
    try:
        read(conn, provenance_id)
    except IntegrityError as exc:
        return {"ok": False, "provenance_id": provenance_id, "integrity_verified": False, "error": exc.message}
    return {"ok": True, "provenance_id": provenance_id, "integrity_verified": True}


def latest_for_asset(conn: sqlite3.Connection, asset_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM provenance_records WHERE asset_id = ? ORDER BY sealed_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return dict(row) if row else None
