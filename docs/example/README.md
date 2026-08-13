# The example project

A fictional project called **Meridian**, so you can see a populated dashboard
before you have a project of your own.

```bash
python tools/aef.py --root docs/example dashboard
```

Then open <http://127.0.0.1:7423>. Nothing is installed and nothing is written —
the dashboard is read-only and binds localhost.

Text, if you would rather not open a browser:

```bash
python tools/aef.py --root docs/example progress
python tools/aef.py --root docs/example tree
python tools/aef.py --root docs/example team
python tools/aef.py --root docs/example validate
```

---

## This is example data

Meridian is not real. No such product, team or repository exists, and no file
here describes anything that was actually built. It is labelled in every file
so it can never be mistaken for project state — which matters in a framework
whose Constitution §6 makes undisclosed fabrication the cardinal sin.

## What it is chosen to show

The demo is not a happy path. A dashboard is only trustworthy once you have
seen it deliver bad news, so this one does:

- **Every status in the vocabulary.** Complete, in progress, pending, blocked,
  failed, and waiting for dependency — 52% complete overall.
- **A failed task with its reason.** `T-022` was implemented and rejected at
  review for a double-credit ordering defect, and says so. It is not hidden,
  and it drags its section's rollup down to 25%, which is the honest number.
- **A blocked task escalated rather than faked.** `T-021` needs commercial
  credentials the sandbox cannot substitute. The reason names what a human must
  do.
- **A stale session.** `session-mer-03` has not heartbeated in over an hour, so
  it is reported as stale and its claims flagged as possibly abandoned — not
  shown as busy, which is what a lock TTL alone would have done.
- **A blocked agent**, with the reason a human needs in order to unblock it.
- **A rejected recommendation, kept.** `R-002` proposed a Redis cache; it was
  rejected on measured arithmetic, and the reasoning stays so the next agent
  does not re-propose it.
- **Five agents from four vendors** — Claude, Codex, Gemini, Kimi — coordinating
  through the same files, because nothing in the framework branches on vendor.

## Why the example widens its own staleness window

`.ai/config/overrides.yaml` sets `heartbeat_stale_minutes: 2880` — 48 hours,
against the framework default of 15 minutes.

That is not a recommendation, it is a property of shipping fixed timestamps.
Under the default, every session here reads as stale a quarter of an hour after
generation, so anyone cloning the repository a day later sees a dead team —
exactly the opposite of the demo's point. Caught by looking at the dashboard
fifteen minutes after writing it, not in review.

**Do not copy that value into a real project.** A 48-hour window means a crashed
agent holds its claims for two days before anyone is told. 15 minutes is the
right number for work that is actually happening.

The file doubles as a worked example of the override mechanism: it deep-merges
over `config/framework.yaml`, and a project never edits the framework to change
its own behaviour.

## Regenerating the timestamps

Session heartbeats are written relative to generation time so the demo shows a
live team rather than one that went stale in 2026. If the ages stop looking
sensible, regenerate them:

```bash
python docs/example/refresh.py
```
