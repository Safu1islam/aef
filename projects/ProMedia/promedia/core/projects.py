"""Projects — the persistent home of an edit (T-042).

T-041 built a render engine and registered no capability, so it was reachable
from neither the agent nor the operator. This module is what turns it into
something either can drive, and the shape it imposes is the point:

An edit is stored as an APPEND-ONLY sequence of EDL versions. There is no update
path. Every change — whether the agent proposes it or the operator makes it —
writes a new version with its author recorded, so:

  * an earlier edit is always recoverable, which is what makes an agent's
    autonomous change safe to accept;
  * "what did the agent change" is answerable by comparing two rows, rather
    than by trusting a summary of itself;
  * a render names the version it came from, so an output can always be traced
    to the edit that produced it.

Authority (F-2): drafting, editing and RENDERING are agent-callable. Rendering
produces a file; it does not publish, spend money, or clear a rights flag. The
operator gate stays where it already is — approval and publication.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from ..errors import NotFound, ValidationError
from . import storage
from .db import iso, new_id, transaction
from .media import ffmpeg, render as render_engine
from .media.edl import EDL
from .registry import Context


def create(ctx: Context, *, title: str) -> dict[str, Any]:
    """A new project, with an empty EDL as version 1.

    Version 1 exists immediately rather than on first edit, so a project always
    has a readable current state and callers never handle "no version yet".
    """
    clean = title.strip()
    if not clean:
        raise ValidationError("a project needs a title", parameter="title")

    project_id = new_id("prj")
    moment = iso()
    with transaction(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO projects (id, title, status, created_by, created_at, updated_at)"
            " VALUES (?, ?, 'draft', ?, ?, ?)",
            (project_id, clean, ctx.principal.id, moment, moment),
        )
        _append_version(ctx, project_id, EDL(), version=1, note="created", at=moment)

    return {
        "ok": True,
        "project_id": project_id,
        "title": clean,
        "status": "draft",
        "edl_version": 1,
        "note": "empty edit; add clips with set-edl before rendering",
    }


def _append_version(
    ctx: Context, project_id: str, edl: EDL, *, version: int, note: str, at: str
) -> None:
    ctx.conn.execute(
        "INSERT INTO project_edl_versions (project_id, version, edl_json, note,"
        " authored_by, authored_kind, authored_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            project_id,
            version,
            json.dumps(edl.to_dict(), sort_keys=True),
            note,
            ctx.principal.id,
            # Recorded because "did a human shape this edit, or an agent" is the
            # question an operator asks before approving what came out of it —
            # the same reasoning that put authorship on rights declarations.
            ctx.principal.kind,
            at,
        ),
    )


def _project_row(ctx: Context, project_id: str) -> Any:
    row = ctx.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise NotFound(f"no project {project_id}", project_id=project_id)
    return row


def current_version(ctx: Context, project_id: str) -> tuple[int, EDL]:
    row = ctx.conn.execute(
        "SELECT version, edl_json FROM project_edl_versions WHERE project_id = ?"
        " ORDER BY version DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"project {project_id} has no EDL", project_id=project_id)
    return int(row["version"]), EDL.from_dict(json.loads(row["edl_json"]))


def set_edl(
    ctx: Context, *, project_id: str, edl: Any, note: str | None = None,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Replace the edit with a new version. Validates BEFORE storing.

    An invalid EDL is refused rather than stored, because a stored one is a
    version an operator may later restore, and restoring to something that
    cannot render is a trap with a delay on it.

    ``expected_version``, when given, must match the CURRENT version or the
    write is refused. C-19's entity lock is held only for the duration of
    this call, so it protects the write itself but not the read-edit-write
    window around it — and the editor room holds a project's EDL client-side
    for as long as the tab is open (T-055), which C-18's four concurrent
    agent sessions and T-056's agent-diff workspace both make a real window,
    not a hypothetical one. Without this check, whichever caller saves last
    silently wins and the other's edit is discarded with no signal to either
    side (raised as R-010 during T-055/T-058's independent review).
    """
    _project_row(ctx, project_id)
    document = EDL.from_dict(edl if isinstance(edl, dict) else json.loads(edl))
    document.validate()

    version, _ = current_version(ctx, project_id)
    if expected_version is not None and expected_version != version:
        raise ValidationError(
            f"project {project_id} is now at version {version}, not the version"
            f" {expected_version} this edit was based on — reload and reapply",
            project_id=project_id, expected_version=expected_version, current_version=version,
        )
    next_version = version + 1
    moment = iso()
    with transaction(ctx.conn):
        _append_version(
            ctx, project_id, document, version=next_version,
            note=(note or "edited").strip()[:200], at=moment,
        )
        ctx.conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (moment, project_id))

    return {
        "ok": True,
        "project_id": project_id,
        "edl_version": next_version,
        "previous_version": version,
        "authored_kind": ctx.principal.kind,
        **document.summary(),
    }


def _version_edl(ctx: Context, project_id: str, version: int) -> EDL:
    """The EDL stored at one specific version, or NotFound.

    Factored out of ``get()`` so ``diff_versions()`` (T-056) reads the exact
    same rows the same way — two lookups of "what a version actually
    contained" that quietly drifted would be worse than either being wrong
    the same way.
    """
    stored = ctx.conn.execute(
        "SELECT edl_json FROM project_edl_versions WHERE project_id = ? AND version = ?",
        (project_id, version),
    ).fetchone()
    if stored is None:
        raise NotFound(
            f"project {project_id} has no version {version}",
            project_id=project_id, version=version,
        )
    return EDL.from_dict(json.loads(stored["edl_json"]))


def get(ctx: Context, *, project_id: str, version: int | None = None) -> dict[str, Any]:
    row = _project_row(ctx, project_id)
    if version is None:
        number, document = current_version(ctx, project_id)
    else:
        number = int(version)
        document = _version_edl(ctx, project_id, number)

    return {
        "ok": True,
        "project_id": project_id,
        "title": row["title"],
        "status": row["status"],
        "edl_version": number,
        "edl": document.to_dict(),
        **document.summary(),
    }


def versions(ctx: Context, *, project_id: str) -> dict[str, Any]:
    """The edit history, without the documents — who changed what, and when."""
    _project_row(ctx, project_id)
    rows = ctx.conn.execute(
        "SELECT version, note, authored_by, authored_kind, authored_at"
        " FROM project_edl_versions WHERE project_id = ? ORDER BY version DESC",
        (project_id,),
    ).fetchall()
    return {
        "ok": True,
        "project_id": project_id,
        "count": len(rows),
        "versions": [dict(r) for r in rows],
    }


def diff_versions(
    ctx: Context, *, project_id: str, from_version: int, to_version: int
) -> dict[str, Any]:
    """A version-to-version diff in the terms an operator reviews an edit in
    (T-056): which clips were added, removed, reordered, trimmed or adjusted;
    which captions or audio tracks changed; whether the frame or the master
    audio setting changed. Computed from the two real documents — nothing
    here is a model's summary of itself.

    Read-only: no lock, no mutation, no new authority (F-2 already lets an
    agent read anything it can render). This is why it needs no new column
    or table — both versions this compares already exist, append-only, in
    ``project_edl_versions`` (T-042).

    Replaces the mockup's decorative "accept/reject individual diff hunks",
    which the append-only, atomic-version EDL model cannot honestly support
    (frontend brief section 6). 'Accept' needs no call at all — the newer
    version is already current the moment ``set-edl`` wrote it. 'Reject' is
    an ordinary ``set-edl`` call the caller makes with the OLDER version's
    own document (``project`` with that ``version``), pinned with
    ``expected_version`` (R-010) so it never clobbers a THIRD version that
    landed in between; this function does not need to know about that half,
    which is why it stays read-only.
    """
    _project_row(ctx, project_id)
    from_version = int(from_version)
    to_version = int(to_version)
    from_edl = _version_edl(ctx, project_id, from_version)
    to_edl = _version_edl(ctx, project_id, to_version)

    asset_ids = sorted({*from_edl.asset_ids(), *to_edl.asset_ids()})
    names: dict[str, str] = {}
    durations: dict[str, float | None] = {}
    if asset_ids:
        placeholders = ",".join("?" for _ in asset_ids)
        for row in ctx.conn.execute(
            f"SELECT id, original_filename, duration_seconds FROM assets WHERE id IN ({placeholders})",
            asset_ids,
        ):
            names[row["id"]] = row["original_filename"]
            durations[row["id"]] = row["duration_seconds"]

    changes: list[dict[str, Any]] = [
        *_diff_clips(from_edl.clips, to_edl.clips, names, durations),
        *_diff_text(from_edl.text, to_edl.text),
        *_diff_audio(from_edl.audio, to_edl.audio, names),
        *_diff_document_settings(from_edl, to_edl),
    ]

    return {
        "ok": True,
        "project_id": project_id,
        "from_version": from_version,
        "to_version": to_version,
        "changes": changes,
        "count": len(changes),
        "identical": len(changes) == 0,
    }


def _clip_name(clip: Any, names: dict[str, str]) -> str:
    return names.get(clip.asset_id, clip.asset_id)


def _clip_removed(clip: Any, position: int, names: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "clip_removed", "asset_id": clip.asset_id, "position": position,
        "detail": f"clip {position} ({_clip_name(clip, names)}) removed",
    }


def _clip_added(clip: Any, position: int, names: dict[str, str]) -> dict[str, Any]:
    return {
        "kind": "clip_added", "asset_id": clip.asset_id, "position": position,
        "detail": f"clip {position} ({_clip_name(clip, names)}) added",
    }


def _diff_one_clip(
    old: Any, new: Any, position: int, names: dict[str, str], durations: dict[str, float | None],
) -> list[dict[str, Any]]:
    """Field-level diff of two clips already matched as "the same clip" by
    ``_diff_clips`` (same asset, same alignment). Every difference the
    operator can actually change in the editor is checked explicitly, rather
    than reporting one opaque "clip changed" — the point of a human-terms
    diff is naming WHICH thing changed.
    """
    changes: list[dict[str, Any]] = []
    name = _clip_name(new, names)
    if (old.start, old.end) != (new.start, new.end):
        old_seconds = old.duration(durations.get(old.asset_id))
        new_seconds = new.duration(durations.get(new.asset_id))
        if old_seconds is not None and new_seconds is not None and abs(old_seconds - new_seconds) > 0.005:
            delta = new_seconds - old_seconds
            direction = "lengthened" if delta > 0 else "trimmed"
            changes.append({
                "kind": "clip_trimmed", "asset_id": new.asset_id, "position": position,
                "before_seconds": round(old_seconds, 3), "after_seconds": round(new_seconds, 3),
                "detail": (
                    f"clip {position} ({name}) {direction} {abs(delta):.1f}s"
                    f" {'longer' if delta > 0 else 'shorter'}"
                    f" ({old_seconds:.1f}s → {new_seconds:.1f}s)"
                ),
            })
        else:
            # Duration unknown on one side (e.g. no probed source duration —
            # A-15 residue) or unchanged despite different in/out points
            # (moved without resizing). Still a real, disclosed change, just
            # not one seconds can be put on.
            changes.append({
                "kind": "clip_range_changed", "asset_id": new.asset_id, "position": position,
                "detail": f"clip {position} ({name}) in/out point changed",
            })
    if old.speed != new.speed:
        changes.append({
            "kind": "clip_speed_changed", "asset_id": new.asset_id, "position": position,
            "before": old.speed, "after": new.speed,
            "detail": f"clip {position} ({name}) speed changed from {old.speed:g}x to {new.speed:g}x",
        })
    if old.effect != new.effect:
        changes.append({
            "kind": "clip_effect_changed", "asset_id": new.asset_id, "position": position,
            "before": old.effect, "after": new.effect,
            "detail": f"clip {position} ({name}) effect changed from '{old.effect}' to '{new.effect}'",
        })
    if old.transition_in != new.transition_in:
        changes.append({
            "kind": "clip_transition_changed", "asset_id": new.asset_id, "position": position,
            "before": old.transition_in, "after": new.transition_in,
            "detail": (
                f"clip {position} ({name}) transition changed from"
                f" '{old.transition_in}' to '{new.transition_in}'"
            ),
        })
    elif old.transition_duration != new.transition_duration:
        # Reviewer finding (T-056): reusing clip_transition_changed's message
        # here reported "changed from 'dissolve' to 'dissolve'" when only the
        # DURATION moved and transition_in stayed the same — before/after
        # named the wrong field. Split out so before/after always name the
        # value that actually changed.
        changes.append({
            "kind": "clip_transition_duration_changed", "asset_id": new.asset_id, "position": position,
            "before": old.transition_duration, "after": new.transition_duration,
            "detail": (
                f"clip {position} ({name}) '{new.transition_in}' transition duration changed"
                f" from {old.transition_duration:g}s to {new.transition_duration:g}s"
            ),
        })
    if old.volume != new.volume or old.mute != new.mute:
        if new.mute and not old.mute:
            audio_detail = "muted"
        elif old.mute and not new.mute:
            audio_detail = "unmuted"
        else:
            audio_detail = f"volume changed from {old.volume:g} to {new.volume:g}"
        changes.append({
            "kind": "clip_audio_changed", "asset_id": new.asset_id, "position": position,
            "detail": f"clip {position} ({name}) {audio_detail}",
        })
    return changes


def _diff_clips(
    before: list[Any], after: list[Any], names: dict[str, str], durations: dict[str, float | None],
) -> list[dict[str, Any]]:
    """Align two clip lists by source asset (there is no other stable
    identity — an EDL's clips are positional) and report what differs.

    ``difflib.SequenceMatcher`` finds the longest common subsequence of
    asset ids, which is exactly "which clips are the same clip, allowing for
    insertions, removals and reordering elsewhere in the timeline" — the
    same problem a text diff solves, over a different alphabet. That
    algorithm alone only ever reports "equal" (same position), "replace",
    "delete" or "insert", never "moved" — a clip that shifted position with
    NOTHING else about it changed comes out of ``get_opcodes()`` as one
    clip's delete plus a DIFFERENT opcode's insert of an identical clip.
    Left as-is that reads as "removed, then a different one added", which is
    an actively false story about footage that never left the timeline
    (independent review, T-056) — AC-1 names "reordered" explicitly as a
    category this diff must report, so the second pass below exists
    specifically to turn an exact remove/insert pair of the SAME clip back
    into one honest ``clip_reordered`` entry.
    """
    changes: list[dict[str, Any]] = []
    removed: list[tuple[int, Any]] = []  # (1-indexed position in `before`, clip)
    added: list[tuple[int, Any]] = []    # (1-indexed position in `after`, clip)

    matcher = difflib.SequenceMatcher(
        None, [c.asset_id for c in before], [c.asset_id for c in after], autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                changes.extend(
                    _diff_one_clip(before[i1 + offset], after[j1 + offset], j1 + offset + 1, names, durations)
                )
        elif tag == "replace":
            removed.extend((k + 1, before[k]) for k in range(i1, i2))
            added.extend((k + 1, after[k]) for k in range(j1, j2))
        elif tag == "delete":
            removed.extend((k + 1, before[k]) for k in range(i1, i2))
        elif tag == "insert":
            added.extend((k + 1, after[k]) for k in range(j1, j2))

    # A move is only claimed when the clip's OWN fields are byte-for-byte
    # identical (to_dict() equal) — not merely the same asset_id. A clip that
    # both moved AND was trimmed/re-effected is not honestly described as a
    # pure reorder, so it is deliberately left to fall through to the
    # remove/add framing below rather than asserted as a move that also
    # silently changed something.
    still_removed: list[tuple[int, Any]] = []
    for from_pos, clip in removed:
        match = next(
            (i for i, (_, candidate) in enumerate(added)
             if candidate.asset_id == clip.asset_id and candidate.to_dict() == clip.to_dict()),
            None,
        )
        if match is None:
            still_removed.append((from_pos, clip))
            continue
        to_pos, _ = added.pop(match)
        changes.append({
            "kind": "clip_reordered", "asset_id": clip.asset_id,
            "from_position": from_pos, "to_position": to_pos,
            "detail": (
                f"clip ({_clip_name(clip, names)}) moved from position {from_pos}"
                f" to position {to_pos}"
            ),
        })

    changes.extend(_clip_removed(clip, pos, names) for pos, clip in still_removed)
    changes.extend(_clip_added(clip, pos, names) for pos, clip in added)
    return changes


def _truncate(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _diff_text(before: list[Any], after: list[Any]) -> list[dict[str, Any]]:
    """Captions have no id either — matched by their own text, which is a
    reasonable proxy (two captions with identical wording behaving as "the
    same caption" is the honest limit of this method, not a hidden guess)."""
    changes: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(
        None, [t.text for t in before], [t.text for t in after], autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                old, new = before[i1 + offset], after[j1 + offset]
                if (old.start, old.end, old.position, old.size, old.color, old.box) != (
                    new.start, new.end, new.position, new.size, new.color, new.box,
                ):
                    changes.append({
                        "kind": "caption_adjusted", "text": new.text,
                        "detail": f'caption "{_truncate(new.text)}" timing or style changed',
                    })
        elif tag in ("replace", "delete"):
            changes.extend(
                {"kind": "caption_removed", "text": before[k].text,
                 "detail": f'caption "{_truncate(before[k].text)}" removed'}
                for k in range(i1, i2)
            )
            if tag == "replace":
                changes.extend(
                    {"kind": "caption_added", "text": after[k].text,
                     "detail": f'caption "{_truncate(after[k].text)}" added'}
                    for k in range(j1, j2)
                )
        elif tag == "insert":
            changes.extend(
                {"kind": "caption_added", "text": after[k].text,
                 "detail": f'caption "{_truncate(after[k].text)}" added'}
                for k in range(j1, j2)
            )
    return changes


def _diff_audio(before: list[Any], after: list[Any], names: dict[str, str]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(
        None, [a.asset_id for a in before], [a.asset_id for a in after], autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                old, new = before[i1 + offset], after[j1 + offset]
                if old.to_dict() != new.to_dict():
                    changes.append({
                        "kind": "audio_track_changed", "asset_id": new.asset_id,
                        "detail": f"audio track ({names.get(new.asset_id, new.asset_id)}) settings changed",
                    })
        elif tag in ("replace", "delete"):
            changes.extend(
                {"kind": "audio_track_removed", "asset_id": before[k].asset_id,
                 "detail": f"audio track ({names.get(before[k].asset_id, before[k].asset_id)}) removed"}
                for k in range(i1, i2)
            )
            if tag == "replace":
                changes.extend(
                    {"kind": "audio_track_added", "asset_id": after[k].asset_id,
                     "detail": f"audio track ({names.get(after[k].asset_id, after[k].asset_id)}) added"}
                    for k in range(j1, j2)
                )
        elif tag == "insert":
            changes.extend(
                {"kind": "audio_track_added", "asset_id": after[k].asset_id,
                 "detail": f"audio track ({names.get(after[k].asset_id, after[k].asset_id)}) added"}
                for k in range(j1, j2)
            )
    return changes


def _diff_document_settings(before: EDL, after: EDL) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if before.aspect != after.aspect:
        changes.append({
            "kind": "aspect_changed", "before": before.aspect, "after": after.aspect,
            "detail": f"output frame changed from '{before.aspect}' to '{after.aspect}'",
        })
    if before.normalise_audio != after.normalise_audio:
        changes.append({
            "kind": "audio_normalisation_changed",
            "detail": f"audio normalisation {'enabled' if after.normalise_audio else 'disabled'}",
        })
    if before.subtitle_asset_id != after.subtitle_asset_id:
        if after.subtitle_asset_id and not before.subtitle_asset_id:
            detail = "subtitles added"
        elif before.subtitle_asset_id and not after.subtitle_asset_id:
            detail = "subtitles removed"
        else:
            detail = "subtitle source changed"
        changes.append({"kind": "subtitles_changed", "detail": detail})
    return changes


def list_projects(ctx: Context) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT p.*, (SELECT MAX(version) FROM project_edl_versions v"
        " WHERE v.project_id = p.id) AS edl_version"
        " FROM projects p ORDER BY p.updated_at DESC"
    ).fetchall()
    return {"ok": True, "count": len(rows), "projects": [dict(r) for r in rows]}


def _substitutions(document: EDL) -> list[dict[str, str]]:
    """What this render will NOT do as asked.

    Fabrication F-003. The EDL VALIDATES transitions against its vocabulary, so
    accepting one is an active promise that it is supported — and four of the
    seven render as hard cuts while the render reports success. That is the
    shape Constitution section 6 exists for: a silent substitution that succeeds
    prompts nobody to look, whereas a failure at least invites a question.

    Derived from render.TRANSITION_REALITY rather than listed here. The first
    version of this function WAS a hand-written list, it named only 'dissolve',
    and an independent audit found the other four by executing the compiler
    instead of reading either list. One source of truth, next to the filters.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for clip in document.clips:
        actual = render_engine.transition_substitution(clip.transition_in)
        if actual is None or clip.transition_in in seen:
            continue
        seen.add(clip.transition_in)
        found.append({
            "requested": clip.transition_in,
            "rendered": actual,
            "why": (
                "the concat graph carries no absolute timeline offsets, which"
                " xfade and wipe transitions both require"
            ),
            "fabrication": "F-003",
            "replacement_task": "T-045",
        })
    return found


def _require_assets_exist(ctx: Context, asset_ids: list[str]) -> None:
    """Every id an EDL references must be real before anything else is asked
    about it (T-044).

    A missing id is a caller mistake — the wrong id, not an unresolved rights
    question and not an availability question — and deserves the more
    specific answer NotFound rather than surfacing as either of those
    instead. Checked first, ahead of both ``_check_rights`` and
    ``_resolve_sources``, which is what keeps their own errors meaningful:
    a rights verdict or a media state for an asset that does not exist would
    not mean anything.
    """
    for asset_id in asset_ids:
        if ctx.conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
            raise NotFound(f"no asset {asset_id} referenced by this edit", asset_id=asset_id)


def _check_rights(ctx: Context, asset_ids: list[str]) -> dict[str, Any]:
    """AC-1 (T-044): refuse before any work starts if any source this edit
    references is not PERMITTED, naming the offending asset.

    Checked before media availability (``_resolve_sources``) — the same
    ordering ``posts.approve()`` already uses for the identical pair of
    gates: a legal refusal outranks a logistical one, so an operator who sees
    RightsBlocked is never left wondering whether the real problem was
    actually the footage being missing.

    F-4 is why this exists at all: 'transforming material never makes
    unusable material usable', and an editor is exactly that transformation
    machine — this is the gap a render used to sail straight through.
    Returns the winning verdict so the caller can reuse it (AC-2) rather than
    recomputing the same thing twice.
    """
    from . import rights as rights_layer
    from ..errors import RightsBlocked

    verdict = rights_layer.worst_verdict_of(ctx, asset_ids)
    if verdict["verdict"] != "PERMITTED":
        raise RightsBlocked(
            f"cannot render: asset {verdict['evaluated_source']} has rights"
            f" verdict {verdict['verdict']}, not PERMITTED (F-4: editing is a"
            " production function, not a copyright-clearing one)",
            asset_id=verdict["evaluated_source"],
            verdict=verdict["verdict"],
            matched_rule=verdict.get("matched_rule"),
            governing_asset=verdict.get("source_asset"),
            reason=verdict.get("reason"),
        )
    return verdict


def _register_render_asset(
    ctx: Context, *, render_id: str, project_title: str, document: EDL,
    output_path: Path, result: dict[str, Any], verdict: dict[str, Any], at: str,
) -> str:
    """AC-4 (T-044): make a successful render's output a derivative the
    EXISTING rights machinery governs, not a file nobody can post.

    Reuses the render's own id as the asset id: one id, findable in either
    table, with no join table or new column needed to connect them.
    ``derived_from`` names the ONE source whose verdict actually governs this
    output — the same asset ``_check_rights`` would have named had this
    render been refused — which is enough for ``rights.ancestry()`` /
    ``effective_verdict()`` to keep watching it after the fact: if that
    source is ever re-graded, this render's effective verdict moves with it
    (F-4). The other sources are not silently dropped — each is recorded as
    evidence on this asset for audit — even though only the governing one
    sits on the live single-parent chain. That is a disclosed limitation of
    reusing ``assets.derived_from`` rather than building a new multi-parent
    provenance table for this task alone; see T-044's completion record.

    Deliberately NOT routed through ``ingest.ingest_file()``: the bytes are
    already on disk (copying them again would be pointless), and the F-7
    reservation for them was already taken and committed by ``render()``
    itself under this exact id (T-043) — reserving again here would
    double-count the same bytes against the ledger.

    Returns the id of the asset that now represents this render — almost
    always ``render_id`` itself; see the content-hash de-duplication branch
    below for the one case it differs.
    """
    from .ingest import hash_file

    content_hash = hash_file(output_path)
    duplicate = ctx.conn.execute(
        "SELECT id FROM assets WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    if duplicate is not None:
        # Byte-identical to something already registered — two renders of an
        # identical edit, most likely. Mirrors ingest_file's own dedup
        # branch: the existing row already carries a verdict and a place in
        # the ancestry graph, so a second row for the same content would only
        # duplicate that, not add anything true.
        return str(duplicate["id"])

    source_ids = document.asset_ids()
    ctx.conn.execute(
        "INSERT INTO assets (id, content_hash, byte_size, original_filename, mime_type,"
        " duration_seconds, probe_status, derived_from, state, ingested_at, object_path)"
        " VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, 'stored', ?, ?)",
        (
            render_id,
            content_hash,
            result.get("byte_size", 0),
            f"{project_title} (render).mp4"[:255],
            "video/mp4",
            result.get("duration_seconds"),
            verdict["evaluated_source"],
            at,
            str(output_path),
        ),
    )
    ctx.conn.execute(
        "INSERT INTO rights_verdicts (id, asset_id, verdict, matched_rule, reasons, ruleset,"
        " ruleset_version, jurisdiction, evidence_digest, decided_at, decided_by)"
        " VALUES (?, ?, ?, 'RENDER_INHERITS_SOURCES', ?, ?, ?, ?, ?, ?, ?)",
        (
            new_id("vd"),
            render_id,
            verdict["verdict"],
            json.dumps([
                f"composite render of {len(source_ids)} source asset(s); most"
                f" restrictive verdict is {verdict['verdict']}, governed by"
                f" {verdict.get('source_asset')} (F-4: a render can never be"
                " cleaner than its worst source)"
            ]),
            verdict.get("ruleset") or "n/a",
            verdict.get("ruleset_version") or "n/a",
            verdict.get("jurisdiction") or "n/a",
            verdict.get("evidence_digest") or verdict.get("id") or "n/a",
            at,
            ctx.principal.id,
        ),
    )
    from . import rights as rights_layer

    for source_id in source_ids:
        source_verdict = rights_layer.effective_verdict(ctx, source_id)
        ctx.conn.execute(
            "INSERT INTO evidence (id, asset_id, kind, body, confidence, produced_by,"
            " model_id, created_at) VALUES (?, ?, 'render_source', ?, NULL, 'system', NULL, ?)",
            (
                new_id("ev"),
                render_id,
                json.dumps({
                    "source_asset_id": source_id,
                    "verdict": source_verdict["verdict"],
                    "governing_asset": source_verdict.get("source_asset"),
                }),
                at,
            ),
        )
    return render_id


def _resolve_sources(ctx: Context, document: EDL) -> dict[str, Path]:
    """Asset id to file path, refusing anything not usable.

    NOTE, and it is the one that matters: this checks that media EXISTS. It does
    NOT check the rights verdict — that is ``_check_rights``, called earlier in
    ``render()`` (T-044), so a rights refusal is never masked by a coincidental
    MediaUnavailable raised from here on the same call.
    """
    from . import rights as rights_layer

    sources: dict[str, Path] = {}
    for asset_id in document.asset_ids():
        row = ctx.conn.execute(
            "SELECT id, state, object_path FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"no asset {asset_id} referenced by this edit", asset_id=asset_id)
        state = rights_layer.media_state(ctx, asset_id)
        if state != "stored" or not row["object_path"]:
            from ..errors import MediaUnavailable

            raise MediaUnavailable(
                f"asset {asset_id} has no media on this machine (state '{state}'),"
                " so it cannot be rendered",
                asset_id=asset_id, asset_state=state,
            )
        path = Path(row["object_path"])
        if not path.is_file():
            from ..errors import MediaUnavailable

            raise MediaUnavailable(
                f"asset {asset_id} is recorded as stored but its file is missing",
                asset_id=asset_id, object_path=str(path),
            )
        sources[asset_id] = path
    return sources


def _projected_render_seconds(ctx: Context, document: EDL) -> tuple[float, bool]:
    """Total output duration BEFORE rendering, and whether every clip's
    contribution is a real number rather than a guess (T-043).

    The output's duration is the sum of its clips' durations — audio tracks
    lay UNDER the video (``amix ... duration=first``) rather than extending
    it, so only ``document.clips`` counts here, matching how
    ``render.compile_render`` actually determines length.

    A clip with an explicit ``end`` needs no lookup: its duration is fixed by
    the EDL itself. A clip that runs to the end of its source needs that
    source's probed duration, which is ``None`` when ffprobe was absent or
    failed at ingest (A-15 residue) — in that case a configured, generous
    per-clip default stands in, and the second return value goes False so the
    caller can say so rather than presenting a guess as a measurement.
    """
    fallback = float(ctx.config.get("media", "unknown_clip_duration_seconds"))
    cache: dict[str, float | None] = {}
    total = 0.0
    all_known = True
    for clip in document.clips:
        if clip.asset_id not in cache:
            row = ctx.conn.execute(
                "SELECT duration_seconds FROM assets WHERE id = ?", (clip.asset_id,)
            ).fetchone()
            cache[clip.asset_id] = row["duration_seconds"] if row else None
        seconds = clip.duration(cache[clip.asset_id])
        if seconds is None:
            seconds = fallback / (clip.speed or 1.0)
            all_known = False
        total += seconds
    return total, all_known


def render(ctx: Context, *, project_id: str, quality: str | None = None) -> dict[str, Any]:
    """Render the current edit. Agent-callable: it produces a file, not a post.

    T-043: quota is RESERVED against a projected output size before ffmpeg
    starts (AC-1), so a render that would breach the F-7 ceiling is refused
    before any encoding time is spent, not after. The reservation is released
    if compilation or encoding fails or times out (AC-2), and committed —
    reconciled to the real output size — only once a playable file exists.

    T-044: every source this edit references is checked, in order, before any
    of that spends a cycle — a nonexistent id (NotFound), then a rights
    verdict that is not PERMITTED (RightsBlocked, AC-1), then missing media
    (MediaUnavailable, AC-3). A successful render is then itself registered as
    a derivative asset carrying the inherited verdict (AC-2, AC-4), so it is
    governed by the same rights machinery as any other asset the moment it
    exists — not a file nobody can post.
    """
    row = _project_row(ctx, project_id)
    version, document = current_version(ctx, project_id)
    if not document.clips:
        raise ValidationError(
            "this project's edit has no clips, so there is nothing to render",
            project_id=project_id, edl_version=version,
        )

    source_ids = document.asset_ids()
    _require_assets_exist(ctx, source_ids)
    verdict = _check_rights(ctx, source_ids)

    chosen = quality or str(ctx.config.get("media", "default_quality"))
    sources = _resolve_sources(ctx, document)

    render_id = new_id("rnd")
    output_dir = ctx.config.data_dir / "renders" / project_id
    output_path = output_dir / f"{render_id}.mp4"
    configured_font = str(ctx.config.get("media", "font_path")).strip()

    # Reserve BEFORE ffmpeg starts (AC-1). The reservation id IS the render
    # id: storage_ledger.id needs no new column to be found again at delete
    # time (see delete_render), and it is a 'derivative' with no asset_id,
    # which the schema already allows for exactly this case.
    projected_seconds, duration_known = _projected_render_seconds(ctx, document)
    projected = storage.projected_render_bytes(
        ctx.config, duration_seconds=projected_seconds, quality=chosen
    )
    reservation_id = storage.reserve_projected(
        ctx.conn, ctx.config, projected=projected, kind="derivative",
        reservation_id=render_id,
    )

    try:
        plan = render_engine.compile_render(
            document, sources, output_path,
            quality=chosen,
            font=Path(configured_font) if configured_font else None,
            workspace=output_dir / f".{render_id}-text",
        )
        result = render_engine.execute(
            plan, timeout_seconds=float(ctx.config.get("media", "render_timeout_seconds"))
        )
    except Exception:
        # AC-2: a failed OR timed-out render leaks no quota. ffmpeg.run()
        # raises RenderFailed for both a non-zero exit and a timeout, and
        # anything else raised by compile_render (e.g. an unknown quality)
        # is covered the same way — nothing here reserved quota it is
        # entitled to keep unless a file actually came out the other end.
        storage.release(ctx.conn, reservation_id)
        raise

    # Ledger truth before bookkeeping: the bytes are already on disk the
    # moment execute() returns, so the reservation is committed — reconciled
    # to the ACTUAL size — before the renders row is written. If the process
    # died between these two statements, the worse outcome is a render file
    # the catalogue does not list yet; the better-protected outcome is that
    # the ledger never has a stale 'reserved' row waiting to expire while
    # real bytes sit uncounted on disk.
    storage.commit(
        ctx.conn, reservation_id, asset_id=None, actual_bytes=result.get("byte_size", 0)
    )

    substitutions = _substitutions(document)
    moment = iso()
    with transaction(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO renders (id, project_id, edl_version, output_path, quality,"
            " width, height, duration_seconds, byte_size, substitutions, rendered_by, rendered_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                render_id, project_id, version, str(output_path), chosen,
                result.get("width"), result.get("height"), result.get("duration_seconds"),
                result.get("byte_size", 0),
                json.dumps(substitutions) if substitutions else None,
                ctx.principal.id, moment,
            ),
        )
        derivative_asset_id = _register_render_asset(
            ctx, render_id=render_id, project_title=row["title"], document=document,
            output_path=output_path, result=result, verdict=verdict, at=moment,
        )

    return {
        "ok": True,
        "render_id": render_id,
        "project_id": project_id,
        "title": row["title"],
        "edl_version": version,
        **result,
        # Never omitted, even when empty: a caller that has to ask whether a key
        # exists will eventually stop asking.
        "substitutions": substitutions,
        "rights": {
            # T-044 AC-2: what this output inherits from its sources. Always
            # PERMITTED here — AC-1 already refused anything else above — but
            # reported anyway so a caller never has to ask, and so the value
            # this render was registered under is visible without a second
            # lookup.
            "verdict": verdict["verdict"],
            "governing_asset": verdict.get("source_asset"),
            "asset_id": derivative_asset_id,
            "note": (
                f"this render is also registered as asset {derivative_asset_id}"
                " (AC-4): it can be queued as a post like any other asset, and"
                " posts.py's existing approve/publish rights gate governs it"
                " exactly as it governs ingested media"
            ),
        },
        "storage": {
            "reservation_id": reservation_id,
            # What was reserved BEFORE encoding, and what actually landed on
            # disk. Different in the ordinary case; the gap is the estimate
            # error, absorbed by the safety margin and then reconciled away.
            "projected_bytes": projected,
            "committed_bytes": result.get("byte_size", 0),
            "projected_duration_seconds": round(projected_seconds, 3),
            # False when any clip's length came from a configured guess
            # rather than a probed source duration (A-15 residue) — an
            # honest flag on the estimate, not a hidden one.
            "duration_measured": duration_known,
        },
    }


def delete_render(
    ctx: Context, *, render_id: str, project_id: str | None = None
) -> dict[str, Any]:
    """Delete a rendered file and return its bytes to the storage ledger (AC-3).

    The ledger is freed regardless of whether the file itself is still on
    disk to delete — a render whose bytes are gone but still counted against
    the ceiling is the same defect this task exists to close, just facing the
    other direction.

    ``project_id`` is optional and backward compatible (R-006): the registered
    ``delete-render`` operation always supplies it, because that is the value
    C-19's lock is keyed on (the same convention every other project mutation
    uses — see ``ops/projects.py``), and passing it here lets this layer
    confirm the render actually belongs to the locked project rather than
    trusting the caller's project_id and render_id to agree. Direct callers
    (tests, scripts) that omit it get the old unchecked behaviour.
    """
    row = ctx.conn.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
    if row is None:
        raise NotFound(f"no render {render_id}", render_id=render_id)
    if project_id is not None and row["project_id"] != project_id:
        raise ValidationError(
            f"render {render_id} belongs to project {row['project_id']}, not {project_id}",
            render_id=render_id, project_id=project_id, actual_project_id=row["project_id"],
        )

    path = Path(row["output_path"])
    file_deleted = False
    if path.is_file():
        path.unlink()
        file_deleted = True

    ledger_state = storage.free(ctx.conn, render_id)
    with transaction(ctx.conn):
        ctx.conn.execute("DELETE FROM renders WHERE id = ?", (render_id,))
        # T-044 AC-4: a render registered as an asset (id == render_id — see
        # _register_render_asset) has its bytes deleted right above. Leaving
        # that row's state as 'stored' would let media_state()/media_available()
        # say the bytes are here when they are not — the exact phantom-asset
        # hazard T-029 closed for ingested masters, reopened here if this is
        # skipped. A render catalogued before this task shipped (or one whose
        # output collided on content_hash with something already registered,
        # see _register_render_asset's dedup branch) has no such row; the
        # UPDATE then matches nothing and does nothing, which is correct.
        ctx.conn.execute(
            "UPDATE assets SET state = 'deleted', object_path = NULL"
            " WHERE id = ? AND state = 'stored'",
            (render_id,),
        )

    return {
        "ok": True,
        "render_id": render_id,
        "project_id": row["project_id"],
        "file_deleted": file_deleted,
        "bytes_freed": int(row["byte_size"]) if ledger_state == "freed" else 0,
        "ledger_state": ledger_state,
    }


def renders(ctx: Context, *, project_id: str | None = None) -> dict[str, Any]:
    if project_id:
        _project_row(ctx, project_id)
        rows = ctx.conn.execute(
            "SELECT * FROM renders WHERE project_id = ? ORDER BY rendered_at DESC",
            (project_id,),
        ).fetchall()
    else:
        rows = ctx.conn.execute("SELECT * FROM renders ORDER BY rendered_at DESC").fetchall()

    out = []
    for row in rows:
        item = dict(row)
        item["substitutions"] = json.loads(item["substitutions"]) if item["substitutions"] else []
        item["output_exists"] = Path(item["output_path"]).is_file()
        out.append(item)
    return {"ok": True, "count": len(out), "renders": out}


def capabilities(ctx: Context) -> dict[str, Any]:
    """What this installation can actually do, right now.

    Exists because "why did that fail" is otherwise answered by trying it. An
    absent toolchain is reported as absent rather than as a media error.
    """
    from .media.edl import ASPECT_PRESETS, CLIP_EFFECTS, TRANSITIONS
    from .media.render import QUALITY_PRESETS

    available = ffmpeg.available()
    return {
        "ok": True,
        "ffmpeg_available": available,
        "ffmpeg_path": ffmpeg.tool_path("ffmpeg"),
        "font_available": ffmpeg.default_font() is not None,
        "aspects": sorted(ASPECT_PRESETS),
        "effects": list(CLIP_EFFECTS),
        "transitions": list(TRANSITIONS),
        "qualities": sorted(QUALITY_PRESETS),
        # Derived, so this cannot claim a transition works after the compiler
        # stops implementing it — or, as happened, keep claiming only one is
        # broken when five are.
        "known_substitutions": [
            {"requested": name, "rendered": actual, "fabrication": "F-003"}
            for name, actual in sorted(render_engine.TRANSITION_REALITY.items())
            if actual is not None
        ],
        "not_available": [
            {"capability": "video generation", "needs": "a hosted video generation API"},
            {"capability": "image generation", "needs": "a hosted image API; 1 GB VRAM rules out local diffusion"},
            {"capability": "voiceover", "needs": "a hosted TTS API, or local Piper"},
            {"capability": "semantic video analysis", "needs": "a hosted multimodal model"},
        ],
        "note": (
            "ffmpeg is installed and rendering works"
            if available
            else "ffmpeg is NOT installed; no media operation can run"
        ),
    }
