# Live snapshot — 2026-08-16

Pixel screenshots weren't obtainable in the session that added this project
(the browser pane wouldn't render frames), so this is a text capture of the
same two views instead, taken from the running app and the AEF dashboard
pointed at this project's real `.ai/state/`.

## ProMedia operator dashboard — `http://127.0.0.1:8765/`

```
Dashboard · Projects · Media · Posts · Publications · Settings · Capabilities

Needs you
  Nothing waiting on you. An agent can queue a post with python -m promedia queue-post.

Recent renders
  PROJECT              VERSION  SIZE    RENDERED           STATUS
  prj_5032f9315c974735 v2       0.9 MB  2026-08-13 15:57   substituted

Projects
  1 total. Open the workspace →
  Launch teaser v4 · 2026-08-13
```

## AEF dashboard, `--root` pointed at ProMedia — `http://127.0.0.1:7423/`

```
ProMedia · AEF 0.5.2
67 tasks across the plan · 81% complete

Completed (54) · In progress (2) · Blocked (7) · Pending (4)

PROJECT TREE                              54 of 67 done
  ProMedia                                          81%  54/67
    Foundation                                      100%  3/3
    Surfaces                                        100%  5/5
    Rights and provenance                           100%  4/4
    Storage and retention                           100%  1/1
    Media production                                 93% 13/14
    Pro Media v2 — rich client                       53%  9/17
    Durability and recovery                          40%  2/5
    Publishing                                       80%  4/5
    Security hardening                              100%  8/8
    Scheduling                                      100%  1/1
    Operator experience                             100%  1/1
    Quality and follow-ups                          100%  3/3

Derived on read from plan.yaml + tasks.yaml + locks.yaml + sessions.yaml + recommendations.yaml
```

Reproduce locally from this checkout:

```bash
cd projects/ProMedia
python -c "from promedia.web.app import run_server; run_server()"   # :8765
python ../../tools/aef.py --root . dashboard --port 7423            # :7423
```
