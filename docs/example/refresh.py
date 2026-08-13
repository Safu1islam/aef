#!/usr/bin/env python3
"""Rewrite the example project's session timestamps relative to now.

The demo is meant to show a LIVE team: four sessions heartbeating within the
last few minutes and one that has gone stale. Absolute timestamps stop doing
that the day after they are written — every session reads as stale, and the
screenshot in the README shows a dead project.

Run this whenever the ages stop looking sensible:

    python docs/example/refresh.py

Stdlib only, like everything else in tools/. Rewrites only the two timestamp
fields per session and the timestamps in recommendations; nothing else in
either file is touched.
"""

from __future__ import annotations

import datetime
import pathlib
import re

HERE = pathlib.Path(__file__).parent
STATE = HERE / ".ai" / "state"

# minutes ago, per session id: (started_at, heartbeat_at)
SESSION_AGES = {
    "session-mer-01": (190, 2),
    "session-mer-02": (46, 1),
    "session-mer-04": (63, 3),
    "session-mer-07": (120, 4),
    # Deliberately stale: well past the 15-minute default, so the dashboard has
    # something honest to report. Do not "fix" this one.
    "session-mer-03": (400, 96),
}

# minutes ago, per recommendation id: (raised_at, decided_at or None)
REC_AGES = {
    "R-001": (240, None),
    "R-002": (180, 120),
    "R-003": (90, 80),
}


def ago(now: datetime.datetime, minutes: int) -> str:
    return (now - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def rewrite_sessions(now: datetime.datetime) -> int:
    path = STATE / "sessions.yaml"
    text = path.read_text(encoding="utf-8")
    changed = 0
    for session_id, (started, beat) in SESSION_AGES.items():
        # Anchor on the id so each session's own block is the one rewritten.
        block = re.search(
            rf'(- id: "{re.escape(session_id)}".*?)(?=\n  - id: |\Z)', text, re.S
        )
        if not block:
            print(f"  ! {session_id} not found — skipped")
            continue
        body = block.group(1)
        body = re.sub(r'started_at: "[^"]*"', f'started_at: "{ago(now, started)}"', body)
        body = re.sub(r'heartbeat_at: "[^"]*"', f'heartbeat_at: "{ago(now, beat)}"', body)
        text = text[: block.start(1)] + body + text[block.end(1) :]
        changed += 1
    path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def rewrite_recommendations(now: datetime.datetime) -> int:
    path = STATE / "recommendations.yaml"
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8")
    changed = 0
    for rec_id, (raised, decided) in REC_AGES.items():
        block = re.search(
            rf'(- id: "{re.escape(rec_id)}".*?)(?=\n  - id: |\Z)', text, re.S
        )
        if not block:
            continue
        body = block.group(1)
        body = re.sub(r'raised_at: "[^"]*"', f'raised_at: "{ago(now, raised)}"', body)
        if decided is not None:
            body = re.sub(r'decided_at: "[^"]*"', f'decided_at: "{ago(now, decided)}"', body)
        text = text[: block.start(1)] + body + text[block.end(1) :]
        changed += 1
    path.write_text(text, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    now = datetime.datetime.now(datetime.timezone.utc).astimezone()
    sessions = rewrite_sessions(now)
    recommendations = rewrite_recommendations(now)
    print(f"refreshed {sessions} sessions and {recommendations} recommendations")
    print("session-mer-03 is intentionally left stale — that is what it demonstrates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
