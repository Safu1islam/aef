"""Asset ingest (T-008).

Order of operations matters and is not arbitrary:

  1. reserve quota   — before any byte is written (F-7)
  2. hash the source — identity is content, not path (F-8)
  3. write the object
  4. record asset + declaration
  5. commit the reservation

A failure at any step releases the reservation. Getting this order wrong is
how a system ends up over its ceiling with no way to discover it.

Ingest without a rights declaration is refused. Rights metadata is not an
optional enrichment: an asset with no declaration cannot be evaluated, and an
asset that cannot be evaluated must never become publishable.

Re-ingesting content whose asset was deleted by retention is refused too, and
loudly (T-029). Deduplication is a claim that the media is already here; for a
deleted asset that claim is false, and a false ok=True is worse than a refusal.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..errors import CeilingExceeded, MediaUnavailable, NotFound, ValidationError
from . import storage
from .db import iso, new_id, transaction
from .registry import Context

_HASH_CHUNK = 1024 * 1024


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_path_for(root: Path, content_hash: str) -> Path:
    """Sharded so one directory never holds every object."""
    return root / content_hash[0:2] / content_hash[2:4] / content_hash


def probe_media(path: Path, *, timeout_seconds: float | None = None) -> dict[str, Any]:
    """Best-effort technical metadata.

    ffprobe is absent on this machine (project.md A-15). When it is missing the
    duration is recorded as null with probe_status 'unavailable' — never as a
    guessed number. An invented duration would be a fabrication, and it would
    be one that later arithmetic silently trusts.

    T-030 (O2): the timeout was a literal 30. It is the ceiling on how long an
    ingest can block on an external binary, which is a limit an operator has a
    real reason to change on slow media, so it belongs in configuration like
    every other limit (protocol 05). Resolved from ``ingest.probe_timeout_seconds``
    by the caller; the parameter keeps this function callable without a Config.
    """
    if timeout_seconds is None:
        from ..config import DEFAULTS

        timeout_seconds = DEFAULTS["ingest"]["probe_timeout_seconds"]
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return {"probe_status": "unavailable", "duration_seconds": None}
    except subprocess.TimeoutExpired:
        return {"probe_status": "failed", "duration_seconds": None}
    if proc.returncode != 0:
        return {"probe_status": "failed", "duration_seconds": None}
    try:
        data = json.loads(proc.stdout or b"{}")
        duration = data.get("format", {}).get("duration")
        return {
            "probe_status": "ok",
            "duration_seconds": float(duration) if duration is not None else None,
        }
    except (ValueError, TypeError):
        return {"probe_status": "failed", "duration_seconds": None}


def _validate_declaration(declaration: dict[str, Any] | None) -> dict[str, Any]:
    if not declaration:
        raise ValidationError(
            "a rights declaration is required at ingest; an asset that cannot be"
            " evaluated must never become publishable",
            parameter="declaration",
            required_fields=["authorship"],
        )
    authorship = declaration.get("authorship")
    valid = {"operator_original", "third_party", "unknown"}
    if authorship not in valid:
        raise ValidationError(
            f"declaration.authorship must be one of {sorted(valid)}",
            parameter="declaration.authorship",
            got=authorship,
        )
    return declaration


def ingest_file(
    ctx: Context,
    *,
    source_path: str,
    declaration: dict[str, Any] | None,
    derived_from: str | None = None,
) -> dict[str, Any]:
    src = Path(source_path).expanduser()
    if not src.is_file():
        raise NotFound(f"no file at {src}", path=str(src))
    decl = _validate_declaration(declaration)

    master_bytes = src.stat().st_size
    projected = storage.projected_bytes(ctx.config, master_bytes)

    # 1. Reserve before writing. A refusal is queued, not discarded (F-7).
    try:
        reservation_id = storage.reserve(
            ctx.conn, ctx.config, master_bytes=master_bytes, kind="master"
        )
    except CeilingExceeded as exc:
        queue_id = storage.enqueue_refused(
            ctx.conn,
            source_path=str(src),
            projected=projected,
            declaration=decl,
            shortfall_bytes=int(exc.detail.get("shortfall_bytes", 0)),
        )
        exc.detail["queued_as"] = queue_id
        exc.detail["queued"] = True
        raise

    try:
        # 2. Identity is the content.
        content_hash = hash_file(src)

        existing = ctx.conn.execute(
            "SELECT id, state FROM assets WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if existing is not None and existing["state"] == "deleted":
            # Finding I9 (T-029). The duplicate branch used to match on
            # content_hash alone and return ok=True, duplicate=True — for a row
            # whose state is 'deleted', whose object_path is NULL, with nothing
            # on disk and zero bytes accounted. The reassuring note was the
            # defect: a caller that trusts ok=True believes the media is back.
            #
            # REFUSE rather than restore, for three reasons:
            #
            #  1. project.md section 10 makes retention deletion FINAL and says
            #     so on arithmetic, not on preference: "Deletion is final and
            #     forecloses repurposing." Section 4 then lists publishing a
            #     deleted asset to a new platform as permanently OUT OF SCOPE,
            #     "foreclosed by the storage ceiling, not an oversight". A quiet
            #     restore inside ingest reopens exactly that, by a side door.
            #  2. The verdict and the sealed provenance survive deletion by
            #     design (F-8). So a restored asset is immediately publishable
            #     again — no attestation, no re-determination. Ingest is AGENT
            #     authority (F-2). Restoring here would let an agent resurrect
            #     an asset that policy deleted and hand it back publish-ready,
            #     which is an authority escalation dressed as deduplication.
            #  3. Restore is a different capability from ingest, with different
            #     authority and its own decisions to make (does the old verdict
            #     still govern? does the grace period restart?). Inventing it
            #     inside this branch would be scope creep on a bug fix.
            #
            # The reservation taken above is released by the except handler
            # below, so a refusal leaks no quota.
            raise MediaUnavailable(
                "these bytes were ingested before and the media was deleted by"
                " retention; re-ingest does not restore it",
                asset_id=existing["id"],
                content_hash=content_hash,
                asset_state="deleted",
                why=(
                    "retention deletion is final and forecloses repurposing"
                    " (project.md section 10); the rights and provenance records"
                    " for this content remain readable (F-8)"
                ),
                remedy=(
                    "read the sealed provenance for this content hash; there is"
                    " deliberately no operation that restores deleted media"
                ),
            )
        if existing is not None and existing["state"] == "absent":
            # T-037. The asset's RECORD came back from a backup but its media
            # did not, because masters are transient and are deliberately not in
            # the artefact. Supplying the original bytes is the recovery path,
            # and it is the reason 'absent' had to be a different state from
            # 'deleted' rather than a reuse of it:
            #
            #   deleted -> retention destroyed this on purpose. Final. Refused
            #              above, and publishing it to a new platform is out of
            #              scope by policy.
            #   absent  -> nothing was destroyed on purpose; a disk was lost and
            #              the record outlived the bytes. Refusing here would
            #              turn a successful recovery into a permanent loss of
            #              capability, which is the opposite of what a backup is
            #              for.
            #
            # The same asset id is kept. The rights position, the sealed
            # provenance and the publication history all reference it, and
            # minting a new id would strand them — C-20's continuity applies to
            # the asset's identity, not just to its verdict.
            target = object_path_for(ctx.config.object_root, content_hash)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            storage.commit(
                ctx.conn, reservation_id, asset_id=existing["id"], actual_bytes=master_bytes
            )
            with transaction(ctx.conn):
                ctx.conn.execute(
                    "UPDATE assets SET state = 'stored', object_path = ?"
                    " WHERE id = ? AND state = 'absent'",
                    (str(target), existing["id"]),
                )
            return {
                "ok": True,
                "asset_id": existing["id"],
                "content_hash": content_hash,
                "asset_state": "stored",
                "duplicate": False,
                "restored": True,
                "note": (
                    "media restored for an asset whose record was recovered from"
                    " a backup; its rights verdict and sealed provenance are"
                    " unchanged and still govern"
                ),
            }

        if existing is not None:
            storage.release(ctx.conn, reservation_id)
            return {
                "ok": True,
                "asset_id": existing["id"],
                "content_hash": content_hash,
                "asset_state": existing["state"],
                "duplicate": True,
                "note": "identical bytes already ingested; storage not double-counted",
            }

        # 3. Write the object.
        target = object_path_for(ctx.config.object_root, content_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

        probe = probe_media(
            target,
            timeout_seconds=float(ctx.config.get("ingest", "probe_timeout_seconds")),
        )
        asset_id = new_id("as")

        # 4. Record asset and declaration atomically.
        with transaction(ctx.conn):
            ctx.conn.execute(
                "INSERT INTO assets (id, content_hash, byte_size, original_filename, mime_type,"
                " duration_seconds, probe_status, derived_from, state, ingested_at, object_path)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'stored', ?, ?)",
                (
                    asset_id,
                    content_hash,
                    master_bytes,
                    src.name,
                    None,
                    probe["duration_seconds"],
                    probe["probe_status"],
                    derived_from,
                    iso(),
                    str(target),
                ),
            )
            ctx.conn.execute(
                "INSERT INTO rights_declarations (id, asset_id, authorship, third_party_material,"
                " source_url, licence_grantor, licence_scope, licence_evidence_ref,"
                " public_domain_source, declared_by, declared_by_kind, declared_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("dec"),
                    asset_id,
                    decl["authorship"],
                    json.dumps(list(decl.get("third_party_material") or [])),
                    decl.get("source_url"),
                    decl.get("licence_grantor"),
                    decl.get("licence_scope"),
                    decl.get("licence_evidence_ref"),
                    decl.get("public_domain_source"),
                    ctx.principal.id,
                    # Derived from the principal, never from a parameter.
                    ctx.principal.kind,
                    iso(),
                ),
            )
        # 5. Commit the reservation.
        storage.commit(ctx.conn, reservation_id, asset_id=asset_id)
    except Exception:
        storage.release(ctx.conn, reservation_id)
        raise

    return {
        "ok": True,
        "asset_id": asset_id,
        "content_hash": content_hash,
        "byte_size": master_bytes,
        "projected_bytes": projected,
        "probe_status": probe["probe_status"],
        "duration_seconds": probe["duration_seconds"],
        "duplicate": False,
    }
