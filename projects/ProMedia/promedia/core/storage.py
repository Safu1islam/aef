"""Storage ledger and admission control (DR-006, F-7).

The ceiling is enforced against ``committed + reserved + projected``, decided
BEFORE any byte is written. Checking free disk space at write time — the usual
approach — cannot express a footprint for work not yet done, which is exactly
the failure mode a 20-file batch produces: the source fits, the derivatives do
not, and by then the bytes are already on disk.

The ledger, not the filesystem, is the source of truth for usage.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import timedelta
from typing import Any

from ..errors import CeilingExceeded, LedgerDrift
from ..config import Config
from .db import canonical_json, iso, new_id, now, transaction


def projected_bytes(config: Config, master_bytes: int) -> int:
    """Master plus the derivatives it will require.

    Multiplier is configuration (A-3), not a literal, because it is the
    assumption most likely to change — adding a platform moves it.
    """
    return int(master_bytes + master_bytes * config.derivative_multiplier)


def projected_render_bytes(config: Config, *, duration_seconds: float, quality: str) -> int:
    """Estimate an unrendered output's size (T-043).

    Unlike an ingest, there is no source file to read a size from — the
    output does not exist yet. This uses the standard proxy for an unencoded
    video's size: projected duration x an estimated bitrate for the chosen
    quality preset, plus a configured safety margin.

    The bitrate table (``media.estimated_bitrate_bytes_per_second``) is only
    PARTLY measured. 'balanced' and 'hardware' are anchored to a real
    encode (T-041 AC-2: 60s of 1280x720 footage rendered 10.4 MB on
    'balanced' and 5.4 MB on 'hardware'); 'fast' and 'quality' were never
    separately measured and are extrapolated from the CRF each preset uses.
    Neither resolution nor source content complexity is modelled — both move
    the real bitrate — so the margin exists to make this a safe UPPER BOUND
    for admission, not a precise prediction. ``storage.commit`` always
    reconciles the reservation against the real output size once ffmpeg is
    done, so a bad estimate here never survives as a wrong ledger figure —
    it only ever survives as a wrongly-refused or wrongly-admitted
    reservation for the seconds between reserve() and commit().
    """
    bitrate_table = config.get("media", "estimated_bitrate_bytes_per_second")
    try:
        bitrate = float(bitrate_table[quality])
    except (KeyError, TypeError):
        # An unrecognised quality string. Fall back to the highest known
        # bitrate rather than guessing low — compile_render raises its own
        # ValidationError for this case moments later, at which point the
        # caller releases whatever was reserved here.
        bitrate = float(max(bitrate_table.values())) if bitrate_table else 0.0
    margin = float(config.get("media", "render_size_safety_margin"))
    return math.ceil(max(0.0, duration_seconds) * bitrate * margin)


def usage(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT"
        " COALESCE(SUM(CASE WHEN state = 'committed' THEN bytes ELSE 0 END), 0) AS committed,"
        " COALESCE(SUM(CASE WHEN state = 'reserved' AND (expires_at IS NULL OR expires_at > ?)"
        "     THEN bytes ELSE 0 END), 0) AS reserved"
        " FROM storage_ledger",
        (iso(),),
    ).fetchone()
    committed = int(row["committed"])
    reserved = int(row["reserved"])
    return {"committed_bytes": committed, "reserved_bytes": reserved, "total_bytes": committed + reserved}


def status(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    u = usage(conn)
    total = u["total_bytes"]
    ceiling = config.ceiling_bytes
    return {
        **u,
        "ceiling_bytes": ceiling,
        "warn_bytes": config.warn_bytes,
        "refuse_bytes": config.refuse_bytes,
        "available_bytes": max(0, config.refuse_bytes - total),
        "fraction_used": round(total / ceiling, 4) if ceiling else 0.0,
        "state": (
            "refusing" if total >= config.refuse_bytes
            else "warning" if total >= config.warn_bytes
            else "ok"
        ),
    }


def reclaim_expired(conn: sqlite3.Connection) -> int:
    """Release reservations abandoned by a crashed ingest.

    Without this a failed ingest would consume quota permanently, and the
    ceiling would ratchet down until the system refused everything.
    """
    cur = conn.execute(
        "UPDATE storage_ledger SET state = 'released', released_at = ?"
        " WHERE state = 'reserved' AND expires_at IS NOT NULL AND expires_at <= ?",
        (iso(), iso()),
    )
    return cur.rowcount


def reserve(
    conn: sqlite3.Connection,
    config: Config,
    *,
    master_bytes: int,
    asset_id: str | None = None,
    kind: str = "master",
) -> str:
    """Claim quota before writing. Raises CeilingExceeded with the shortfall."""
    projected = projected_bytes(config, master_bytes)
    return reserve_projected(conn, config, projected=projected, kind=kind, asset_id=asset_id)


def reserve_projected(
    conn: sqlite3.Connection,
    config: Config,
    *,
    projected: int,
    kind: str,
    asset_id: str | None = None,
    reservation_id: str | None = None,
) -> str:
    """Claim quota for an ALREADY-COMPUTED projected byte count.

    Factored out of ``reserve`` (T-043) so there is exactly one place that
    compares a projection against the ceiling — ``reserve`` computes its
    projection as master-bytes-plus-derivative-multiplier (a source file
    exists to read a size from); a render (``projects.render``) computes its
    projection as duration-times-estimated-bitrate (no source file exists
    yet). Two different ways to ESTIMATE, one way to be ADMITTED — the
    ceiling check itself never forks.

    ``reservation_id`` lets the caller supply its own id instead of a random
    one. A render passes its own render_id here, so the ledger row it owns
    can be found again by id alone when the render is deleted (T-043 AC-3) —
    with no new column and no second table to keep in sync with ``renders``.
    """
    with transaction(conn):
        conn.execute(
            "UPDATE storage_ledger SET state = 'released', released_at = ?"
            " WHERE state = 'reserved' AND expires_at IS NOT NULL AND expires_at <= ?",
            (iso(), iso()),
        )
        u = usage(conn)
        if u["total_bytes"] + projected > config.refuse_bytes:
            shortfall = u["total_bytes"] + projected - config.refuse_bytes
            raise CeilingExceeded(
                "storage ceiling would be exceeded; this reservation is refused",
                projected_bytes=projected,
                shortfall_bytes=shortfall,
                **usage(conn),
                refuse_bytes=config.refuse_bytes,
                ceiling_bytes=config.ceiling_bytes,
            )
        new_reservation_id = reservation_id or new_id("res")
        ttl = int(config.get("storage", "reservation_ttl_seconds"))
        conn.execute(
            "INSERT INTO storage_ledger (id, asset_id, kind, bytes, state, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, 'reserved', ?, ?)",
            (new_reservation_id, asset_id, kind, projected, iso(), iso(now() + timedelta(seconds=ttl))),
        )
    return new_reservation_id


def commit(
    conn: sqlite3.Connection,
    reservation_id: str,
    *,
    asset_id: str | None = None,
    actual_bytes: int | None = None,
) -> None:
    """Convert a reservation into committed usage.

    BLOCKING finding B4 (independent review, 2026-08-08). This used to be a
    fire-and-forget UPDATE. If the reservation had already expired and been
    reclaimed by a concurrent ingest — which a sleeping Windows desktop makes
    ordinary rather than exotic — the UPDATE matched nothing, returned silently,
    and the bytes landed on disk counting ZERO against the ceiling. DR-006 makes
    the ledger the sole source of truth, so nothing could ever detect the
    shortfall; the drift would accumulate until the disk, not the ledger, ran
    out. Protocol 05: fail loudly and recoverably.

    ``asset_id`` is optional (T-043): a render commits with no asset_id at
    all — ``storage_ledger.kind = 'derivative'`` with a NULL asset_id is
    exactly what the schema already allows for a derivative with nothing to
    point at yet.

    Reconciliation (T-043): pass ``actual_bytes`` to overwrite the projected
    figure with the real, measured output size. This is what makes the
    ledger CONVERGE on truth rather than drift — an over-estimate returns its
    slack the moment the render finishes, and an under-estimate is corrected
    to the true figure rather than quietly staying wrong forever.
    """
    with transaction(conn):
        if actual_bytes is None:
            cur = conn.execute(
                "UPDATE storage_ledger SET state = 'committed', asset_id = ?, expires_at = NULL"
                " WHERE id = ? AND state = 'reserved'",
                (asset_id, reservation_id),
            )
        else:
            cur = conn.execute(
                "UPDATE storage_ledger SET state = 'committed', asset_id = ?, bytes = ?, expires_at = NULL"
                " WHERE id = ? AND state = 'reserved'",
                (asset_id, int(actual_bytes), reservation_id),
            )
        if cur.rowcount == 0:
            row = conn.execute(
                "SELECT state FROM storage_ledger WHERE id = ?", (reservation_id,)
            ).fetchone()
            raise LedgerDrift(
                "storage reservation could not be committed; it was expired or already"
                " resolved, so these bytes would not have counted against the ceiling",
                reservation_id=reservation_id,
                state=row["state"] if row else "missing",
                remedy="re-run ingest; run reclaim-reservations to tidy expired rows",
            )


def release(conn: sqlite3.Connection, reservation_id: str) -> None:
    """Release a RESERVED row only.

    Scoped to 'reserved' (finding B4): the previous `state != 'released'`
    predicate would happily flip an already-COMMITTED row to released, erasing
    accounted storage that is genuinely on disk.
    """
    conn.execute(
        "UPDATE storage_ledger SET state = 'released', released_at = ? WHERE id = ? AND state = 'reserved'",
        (iso(), reservation_id),
    )


def free(conn: sqlite3.Connection, reservation_id: str) -> str:
    """Return COMMITTED bytes to the pool because the file behind them is gone.

    T-043 AC-3: deleting a render must return its bytes to the ledger.
    ``release`` exists for abandoning a still-open reservation (scoped to
    'reserved' — finding B4); this is the mirror for a reservation that
    already succeeded and whose product has now been deleted, so it is
    scoped to 'committed' the same way, for the same reason: touching a row
    in any other state could erase accounting for bytes that this deletion
    does not own — including a reservation a concurrent render still has open.

    Returns a status string rather than raising, because a MISSING row is an
    expected case here, not drift: any render made before this task shipped
    was never reserved at all, and deleting one of those must still succeed
    (there is simply nothing to free). Only a row that exists and is stuck in
    an unexpected state (e.g. still 'reserved' — this render's own
    commit somehow never landed) is worth a caller's attention, and it is
    reported rather than silently coerced.

      'freed'            — a committed reservation was released.
      'already_released' — nothing to do; a previous delete already freed it.
      'missing'          — no reservation was ever recorded for this id.
      'reserved'         — the row exists but was never committed; the
                            caller decides whether that is alarming.
    """
    row = conn.execute(
        "SELECT state FROM storage_ledger WHERE id = ?", (reservation_id,)
    ).fetchone()
    if row is None:
        return "missing"
    if row["state"] == "released":
        return "already_released"
    if row["state"] != "committed":
        return str(row["state"])
    conn.execute(
        "UPDATE storage_ledger SET state = 'released', released_at = ? WHERE id = ? AND state = 'committed'",
        (iso(), reservation_id),
    )
    return "freed"


def enqueue_refused(
    conn: sqlite3.Connection,
    *,
    source_path: str,
    projected: int,
    declaration: dict[str, Any],
    shortfall_bytes: int,
) -> str:
    """F-7: refused ingest is queued, never discarded."""
    queue_id = new_id("q")
    conn.execute(
        "INSERT INTO ingest_queue (id, source_path, projected_bytes, declaration, queued_at,"
        " status, shortfall_bytes) VALUES (?, ?, ?, ?, ?, 'queued', ?)",
        (queue_id, source_path, projected, canonical_json(declaration), iso(), int(shortfall_bytes)),
    )
    return queue_id


def queued(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM ingest_queue WHERE status = 'queued' ORDER BY queued_at"
        )
    ]
