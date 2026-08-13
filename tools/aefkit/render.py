"""HTML for the two views. Self-contained by contract: no CDN, no build step.

Two pages, deliberately not one:

    /          the project tree — structure, status, who owns what
    /progress  how far along, what is moving, what is stuck

The tree is the primary object. The progress view exists because "how far along
are we" is a different question from "what is the shape of this project", and
answering both on one screen produces a page that answers neither quickly.

The tree uses native <details>/<summary>. Expansion, keyboard access and screen
reader semantics then cost nothing and work before any script runs.
"""

from __future__ import annotations

import html
import json
from typing import Any

__all__ = ["tree_page", "progress_page"]

_STATUS_ORDER = ("complete", "in_progress", "blocked", "waiting_dependency", "failed", "pending")

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfbfa;         --panel: #ffffff;      --line: #e7e5e1;
  --ink: #1b1a18;        --muted: #6f6b64;      --faint: #9a958c;
  --accent: #3b5bdb;     --accent-soft: #edf0fd;
  --complete: #2f9e59;   --in_progress: #3b5bdb; --pending: #a8a29a;
  --blocked: #c98a1b;    --failed: #d2483f;      --waiting_dependency: #7c5cd6;
  --radius: 9px;
  --shadow: 0 1px 2px rgba(20,18,15,.05), 0 4px 14px rgba(20,18,15,.04);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16161a;       --panel: #1d1e23;      --line: #2c2e35;
    --ink: #e9e8e4;      --muted: #9d9a94;      --faint: #6f6c67;
    --accent: #8ea2ff;   --accent-soft: #232741;
    --complete: #52c07d; --in_progress: #8ea2ff; --pending: #6b6862;
    --blocked: #e0a63c;  --failed: #f0736a;      --waiting_dependency: #a78bfa;
    --shadow: 0 1px 2px rgba(0,0,0,.3), 0 4px 14px rgba(0,0,0,.22);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
a { color: inherit; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 22px 72px; }

/* ---- header ---- */
header.top {
  position: sticky; top: 0; z-index: 20;
  background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: saturate(1.6) blur(9px);
  border-bottom: 1px solid var(--line);
}
.top-inner {
  max-width: 1080px; margin: 0 auto; padding: 13px 22px;
  display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
}
.brand { font-weight: 620; letter-spacing: -.01em; display: flex; align-items: baseline; gap: 9px; }
.brand .v { font-size: 11px; color: var(--faint); font-weight: 500; letter-spacing: .02em; }
.nav { margin-left: auto; display: flex; gap: 6px; }
.nav a {
  text-decoration: none; font-size: 13.5px; font-weight: 520; color: var(--muted);
  padding: 6.5px 13px; border-radius: 7px; border: 1px solid transparent; white-space: nowrap;
}
.nav a:hover { background: var(--panel); color: var(--ink); }
.nav a.on { background: var(--accent-soft); color: var(--accent); border-color: color-mix(in srgb, var(--accent) 22%, transparent); }

/* ---- headline ---- */
.headline { padding: 30px 0 20px; }
.headline h1 { margin: 0 0 5px; font-size: 25px; letter-spacing: -.02em; font-weight: 640; }
.headline p { margin: 0; color: var(--muted); font-size: 14px; }

.bigpct { display: flex; align-items: baseline; gap: 13px; margin: 4px 0 14px; }
.bigpct b { font-size: 46px; font-weight: 660; letter-spacing: -.035em; line-height: 1; }
.bigpct span { color: var(--muted); font-size: 14px; }

/* ---- meter ---- */
.meter { display: flex; height: 9px; border-radius: 99px; overflow: hidden; background: var(--line); }
.meter i { display: block; }
.meter i + i { border-left: 1px solid color-mix(in srgb, var(--bg) 55%, transparent); }

.legend { display: flex; flex-wrap: wrap; gap: 7px 20px; margin: 13px 0 0; padding: 0; list-style: none; font-size: 13px; }
.legend li { display: flex; align-items: center; gap: 7px; color: var(--muted); }
.legend b { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }

.dot { width: 8px; height: 8px; border-radius: 99px; flex: none; }
.dot.complete { background: var(--complete); }
.dot.in_progress { background: var(--in_progress); }
.dot.pending { background: var(--pending); }
.dot.blocked { background: var(--blocked); }
.dot.failed { background: var(--failed); }
.dot.waiting_dependency { background: var(--waiting_dependency); }

/* ---- panels ---- */
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
.panel + .panel { margin-top: 18px; }
.panel > h2 {
  margin: 0; padding: 14px 17px; font-size: 13px; font-weight: 620; letter-spacing: .03em;
  text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line);
  display: flex; align-items: center; gap: 9px;
}
.panel > h2 .count { margin-left: auto; font-weight: 560; color: var(--faint); letter-spacing: 0; text-transform: none; font-size: 12px; }

/* ---- toolbar ---- */
.toolbar { display: flex; gap: 9px; align-items: center; flex-wrap: wrap; margin: 20px 0 13px; }
.toolbar input[type=search] {
  flex: 1 1 210px; min-width: 160px; padding: 8px 12px; font: inherit; font-size: 13.5px;
  border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--ink);
}
.toolbar input[type=search]:focus { outline: 2px solid color-mix(in srgb, var(--accent) 45%, transparent); outline-offset: 1px; border-color: transparent; }
.btn {
  font: inherit; font-size: 13px; font-weight: 520; padding: 8px 13px; cursor: pointer;
  border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--muted);
}
.btn:hover { color: var(--ink); border-color: color-mix(in srgb, var(--ink) 22%, var(--line)); }
.btn.on { background: var(--accent-soft); color: var(--accent); border-color: color-mix(in srgb, var(--accent) 30%, transparent); }

/* ---- tree ---- */
.tree { padding: 7px 6px 11px; }
.tree details, .tree .leaf { margin: 0; }
.tree summary, .tree .leaf {
  display: flex; align-items: center; gap: 9px; padding: 6px 11px; border-radius: 7px;
  cursor: default; list-style: none; min-height: 32px; min-width: 0;
}
.tree summary { cursor: pointer; }
.tree summary::-webkit-details-marker { display: none; }
.tree summary:hover, .tree .leaf:hover { background: color-mix(in srgb, var(--accent) 5%, transparent); }
.tree summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }

.tw { /* twisty */
  width: 13px; flex: none; color: var(--muted); font-size: 13px; line-height: 1; text-align: center;
  transition: transform .12s ease; user-select: none;
}
details[open] > summary .tw { transform: rotate(90deg); }
.leaf .tw { visibility: hidden; }

/* The title is the only element allowed to shrink — everything else in the row
   is flex:none. Without this, a long title in a deep branch pushes the row, and
   with it the whole page, wider than the viewport. It WRAPS rather than
   ellipsing: a truncated task title is unreadable, and these are sentences. */
.name {
  font-weight: 520; letter-spacing: -.005em;
  min-width: 0; flex: 0 1 auto; overflow-wrap: anywhere;
}
.leaf .name { font-weight: 450; }
summary .name { font-weight: 590; }
.kids { margin-left: 15px; padding-left: 11px; border-left: 1px solid var(--line); min-width: 0; }
.leafwrap { min-width: 0; }

.tid {
  font: 11.5px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--faint); flex: none; padding-top: 1px;
}
.spacer { margin-left: auto; }
.chip {
  font-size: 11.5px; padding: 2.5px 8px; border-radius: 99px; white-space: nowrap; flex: none;
  border: 1px solid var(--line); color: var(--muted); background: var(--bg);
}
.chip.agent { border-color: color-mix(in srgb, var(--accent) 26%, transparent); color: var(--accent); background: var(--accent-soft); }
.chip.agent.unassigned { border-style: dashed; color: var(--faint); background: transparent; }
.chip.pct { font-variant-numeric: tabular-nums; }
.chip.st { border: none; padding-left: 0; }
.minibar { width: 54px; height: 4px; border-radius: 99px; background: var(--line); overflow: hidden; flex: none; }
.minibar i { display: block; height: 100%; background: var(--complete); }

.note { margin: 0 11px 8px 48px; font-size: 12.5px; color: var(--muted); }

/* ---- lists ---- */
.rows { list-style: none; margin: 0; padding: 5px; }
.rows li { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 7px; min-width: 0; flex-wrap: wrap; }
.rows li + li { border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent); }
.rows .where { font-size: 12px; color: var(--faint); }
.rows .why { font-size: 12.5px; color: var(--muted); }
.empty { padding: 15px 17px; color: var(--faint); font-size: 13.5px; }

/* ---- agents ---- */
.agents { list-style: none; margin: 0; padding: 5px; }
.agents li { display: flex; align-items: center; gap: 12px; padding: 9px 12px; }
.agents .who { font-weight: 540; min-width: 128px; }
.agents .bar { flex: 1; display: flex; height: 7px; border-radius: 99px; overflow: hidden; background: var(--line); }
.agents .bar i + i { border-left: 1px solid color-mix(in srgb, var(--panel) 60%, transparent); }
.agents .n { font-size: 12.5px; color: var(--muted); font-variant-numeric: tabular-nums; min-width: 62px; text-align: right; }

/* ---- problems ---- */
.problems { border-color: color-mix(in srgb, var(--failed) 42%, var(--line)); }
.problems > h2 { color: var(--failed); }
.problems ul { margin: 0; padding: 11px 17px 15px 33px; font-size: 13.5px; color: var(--ink); }
.problems li { margin: 4px 0; }

/* ---- coordination notices: present, but not alarming ---- */
.notices { border-color: color-mix(in srgb, var(--in_progress) 38%, var(--line)); }
.notices > h2 { color: var(--in_progress); }
.notices ul { margin: 0; padding: 11px 17px 15px 33px; font-size: 13.5px; color: var(--muted); }
.notices li { margin: 4px 0; }

/* ---- lock holder ---- */
.chip.held {
  background: var(--accent-soft); color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 22%, transparent);
}

footer.foot { margin-top: 30px; padding-top: 15px; border-top: 1px solid var(--line); color: var(--faint); font-size: 12px; display: flex; gap: 14px; flex-wrap: wrap; }
.hidden { display: none !important; }
@media (max-width: 620px) {
  .headline h1 { font-size: 21px; }
  .bigpct b { font-size: 38px; }
  .agents li { flex-wrap: wrap; }
  .agents .who { min-width: 0; }
  .tid, .chip.pct { display: none; }
}
"""

_TREE_JS = """
(function () {
  var root = document.getElementById('tree');
  if (!root) return;
  var search = document.getElementById('q');
  var filters = Array.prototype.slice.call(document.querySelectorAll('[data-status]'));
  var active = null;

  function nodes() { return Array.prototype.slice.call(root.querySelectorAll('[data-node]')); }

  function matches(el, term) {
    if (active && el.getAttribute('data-status') !== active) return false;
    if (!term) return true;
    return (el.getAttribute('data-search') || '').indexOf(term) !== -1;
  }

  function apply() {
    var term = (search.value || '').trim().toLowerCase();
    var filtering = !!term || !!active;
    nodes().forEach(function (el) { el.dataset.hit = '0'; });

    nodes().forEach(function (el) {
      if (!matches(el, term)) return;
      el.dataset.hit = '1';
      // A hit drags its ancestors along, or it would have nothing to hang from.
      var parent = el.parentElement;
      while (parent && parent !== root) {
        if (parent.hasAttribute('data-node')) { parent.dataset.hit = '1'; }
        parent = parent.parentElement;
      }
      // ...and its descendants, so a matched section still shows its contents.
      Array.prototype.forEach.call(el.querySelectorAll('[data-node]'), function (kid) {
        kid.dataset.hit = '1';
      });
    });

    nodes().forEach(function (el) {
      var show = !filtering || el.dataset.hit === '1';
      el.classList.toggle('hidden', !show);
      if (show && filtering && el.tagName === 'DETAILS') el.open = true;
    });
  }

  search.addEventListener('input', apply);
  filters.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var value = btn.getAttribute('data-status');
      active = (active === value) ? null : value;
      filters.forEach(function (other) { other.classList.toggle('on', other === btn && active); });
      apply();
    });
  });
  document.getElementById('expand').addEventListener('click', function () {
    root.querySelectorAll('details').forEach(function (d) { d.open = true; });
  });
  document.getElementById('collapse').addEventListener('click', function () {
    root.querySelectorAll('details').forEach(function (d) { d.open = false; });
  });
})();
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _shell(title: str, active: str, body: str, meta: dict[str, Any], script: str = "") -> str:
    project = meta.get("project") or "Project"
    version = meta.get("aef_version") or ""
    reader = meta.get("reader") or ""
    planned = meta.get("planned_at") or ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<header class="top">
  <div class="top-inner">
    <div class="brand">{_e(project)} <span class="v">AEF {_e(version)}</span></div>
    <nav class="nav">
      <a href="/" class="{'on' if active == 'tree' else ''}">Project tree</a>
      <a href="/progress" class="{'on' if active == 'progress' else ''}">Progress</a>
      <a href="/team" class="{'on' if active == 'team' else ''}">Team</a>
      <a href="/api/plan.json">JSON</a>
    </nav>
  </div>
</header>
<div class="wrap">
{body}
<footer class="foot">
  <span>Derived on read from <code>plan.yaml</code> + <code>tasks.yaml</code> + <code>locks.yaml</code> + <code>sessions.yaml</code> + <code>recommendations.yaml</code></span>
  <span>{_e(reader)}</span>
  {f'<span>Planned {_e(planned)}</span>' if planned else ''}
</footer>
</div>
{f'<script>{script}</script>' if script else ''}
</body>
</html>"""


def _meter(counts: dict[str, int], total: int) -> str:
    if total <= 0:
        return '<div class="meter"></div>'
    parts = []
    for status in _STATUS_ORDER:
        value = counts.get(status, 0)
        if value:
            width = 100.0 * value / total
            parts.append(
                f'<i style="width:{width:.4f}%;background:var(--{status})" '
                f'title="{_e(value)} {_e(status.replace("_", " "))}"></i>'
            )
    return f'<div class="meter">{"".join(parts)}</div>'


def _legend(counts: dict[str, int], labels: dict[str, str]) -> str:
    items = []
    for status in _STATUS_ORDER:
        value = counts.get(status, 0)
        if not value:
            continue
        items.append(
            f'<li><span class="dot {status}"></span><b>{_e(value)}</b> {_e(labels.get(status, status))}</li>'
        )
    return f'<ul class="legend">{"".join(items)}</ul>' if items else ""


def _problems(problems: list[str]) -> str:
    if not problems:
        return ""
    items = "".join(f"<li>{_e(problem)}</li>" for problem in problems)
    return (
        '<section class="panel problems">'
        f'<h2>Plan does not match the task graph<span class="count">{len(problems)}</span></h2>'
        f"<ul>{items}</ul>"
        "</section>"
    )


def _notices(notices: list[str]) -> str:
    """Coordination drift between locks.yaml and tasks.yaml.

    Visually quieter than `problems` and deliberately so: a notice is a live
    condition that resolves itself when the agent updates its status, not a
    defect in the plan. Showing it in the same red as a structural failure would
    train the reader to ignore both.
    """
    if not notices:
        return ""
    items = "".join(f"<li>{_e(notice)}</li>" for notice in notices)
    return (
        '<section class="panel notices">'
        f'<h2>Live coordination notices<span class="count">{len(notices)}</span></h2>'
        f"<ul>{items}</ul>"
        "</section>"
    )


# ---------------------------------------------------------------------------
# tree page
# ---------------------------------------------------------------------------

def _agent_chip(node: dict[str, Any]) -> str:
    agent = node.get("agent")
    if not agent:
        if node.get("leaf"):
            return '<span class="chip agent unassigned">unassigned</span>'
        return ""
    source = node.get("agent_source") or ""
    title = {"manual": "assigned by hand", "auto": "assigned automatically",
             "inherited": "inherited from a parent node"}.get(source, source)
    mark = "&#9679;&#65038; " if source == "manual" else ""
    return f'<span class="chip agent" title="{_e(title)}">{mark}{_e(agent)}</span>'


def _held_chip(node: dict[str, Any]) -> str:
    """Who is holding this node's files right now, from the lock.

    Separate from the agent chip because they answer different questions. The
    agent chip is the plan's intention; this is the session with the files open.
    On a single-agent project they agree and this chip never appears.
    """
    lock = node.get("lock")
    if not lock or not lock.get("agent"):
        return ""
    until = f' until {lock["expires_at"]}' if lock.get("expires_at") else ""
    return (
        f'<span class="chip held" title="holds a lock on {_e(lock.get("path") or "these paths")}'
        f'{_e(until)}">&#128274; {_e(lock["agent"])}</span>'
    )


def _node_html(node: dict[str, Any], depth: int = 0) -> str:
    status = node.get("status", "pending")
    label = node.get("status_label", status)
    search_blob = " ".join(filter(None, [
        str(node.get("title") or ""), str(node.get("id") or ""),
        str(node.get("task_id") or ""), str(node.get("agent") or ""),
    ])).lower()
    attrs = (
        f'data-node data-status="{_e(status)}" data-search="{_e(search_blob)}"'
    )
    dot = f'<span class="dot {status}" title="{_e(label)}"></span>'
    tid = f'<span class="tid">{_e(node.get("task_id"))}</span>' if node.get("task_id") else ""

    if node.get("leaf"):
        task = node.get("task") or {}
        bits = []
        if task.get("criteria_total"):
            bits.append(
                f'<span class="chip" title="acceptance criteria passed">'
                f'{_e(task["criteria_passed"])}/{_e(task["criteria_total"])} AC</span>'
            )
        note = ""
        reason = task.get("blocked_reason") or node.get("note")
        if reason and status in ("blocked", "failed"):
            note = f'<p class="note">{_e(reason)}</p>'
        return (
            f'<div class="leafwrap" {attrs}>'
            f'<div class="leaf"><span class="tw">&#9656;</span>{dot}'
            f'<span class="name" title="{_e(node.get("title"))}">{_e(node.get("title"))}</span>{tid}'
            f'<span class="spacer"></span>{"".join(bits)}{_held_chip(node)}{_agent_chip(node)}</div>'
            f"{note}</div>"
        )

    counts = node.get("counts") or {}
    percent = node.get("percent", 0)
    kids = "".join(_node_html(child, depth + 1) for child in node.get("children") or [])
    open_attr = " open" if depth < 1 else ""
    return (
        f"<details{open_attr} {attrs}>"
        f'<summary><span class="tw">&#9656;</span>{dot}'
        f'<span class="name" title="{_e(node.get("title"))}">{_e(node.get("title"))}</span>'
        f'<span class="spacer"></span>'
        f'<span class="minibar" title="{_e(percent)}% complete"><i style="width:{_e(percent)}%"></i></span>'
        f'<span class="chip pct">{_e(percent)}%</span>'
        f'<span class="chip">{_e(counts.get("complete", 0))}/{_e(node.get("leaf_count", 0))}</span>'
        f"{_agent_chip(node)}</summary>"
        f'<div class="kids">{kids}</div>'
        "</details>"
    )


def tree_page(data: dict[str, Any]) -> str:
    progress = data["progress"]
    tree = data["tree"]
    meta = data["meta"]
    labels = progress["labels"]

    filters = "".join(
        f'<button class="btn" data-status="{_e(status)}">'
        f'<span class="dot {status}"></span> {_e(labels.get(status, status))} '
        f'({_e(progress["counts"].get(status, 0))})</button>'
        for status in _STATUS_ORDER if progress["counts"].get(status)
    )

    body = f"""
<div class="headline">
  <h1>{_e(tree.get("title"))}</h1>
  <p>{_e(progress["leaf_count"])} tasks across the plan &middot; {_e(progress["percent"])}% complete</p>
</div>
{_problems(data.get("problems") or [])}
{_notices(data.get("notices") or [])}
<div class="toolbar">
  <input type="search" id="q" placeholder="Filter by task, id or agent&hellip;" autocomplete="off">
  <button class="btn" id="expand" type="button">Expand all</button>
  <button class="btn" id="collapse" type="button">Collapse all</button>
</div>
<div class="toolbar" style="margin-top:0">{filters}</div>
<section class="panel">
  <h2>Project tree<span class="count">{_e(progress["counts"].get("complete", 0))} of {_e(progress["leaf_count"])} done</span></h2>
  <div class="tree" id="tree">{_node_html(tree)}</div>
</section>
"""
    return _shell(f'{meta.get("project") or "Project"} — plan', "tree", body, meta, _TREE_JS)


# ---------------------------------------------------------------------------
# progress page
# ---------------------------------------------------------------------------

def _rows(items: list[dict[str, Any]], empty: str, show_reason: bool = False) -> str:
    if not items:
        return f'<p class="empty">{_e(empty)}</p>'
    out = []
    for item in items:
        where = " / ".join(item.get("path") or [])
        reason = ""
        if show_reason and item.get("reason"):
            reason = f'<span class="why">{_e(item["reason"])}</span>'
        agent = (
            f'<span class="chip agent">{_e(item["agent"])}</span>' if item.get("agent")
            else '<span class="chip agent unassigned">unassigned</span>'
        )
        out.append(
            "<li>"
            f'<span class="dot {_e(item.get("status"))}" title="{_e(item.get("status_label"))}"></span>'
            f'<span class="name">{_e(item.get("title"))}</span>'
            + (f'<span class="tid">{_e(item.get("task_id"))}</span>' if item.get("task_id") else "")
            + (f'<span class="where">{_e(where)}</span>' if where else "")
            + f'<span class="spacer"></span>{reason}{_held_chip(item)}{agent}</li>'
        )
    return f'<ul class="rows">{"".join(out)}</ul>'


def progress_page(data: dict[str, Any]) -> str:
    progress = data["progress"]
    meta = data["meta"]
    counts = progress["counts"]
    labels = progress["labels"]
    total = progress["leaf_count"]

    agent_rows = []
    for name, stats in (data.get("agents") or {}).items():
        segments = "".join(
            f'<i style="width:{100.0 * stats["counts"].get(status, 0) / max(stats["total"], 1):.4f}%;'
            f'background:var(--{status})" title="{_e(stats["counts"].get(status, 0))} '
            f'{_e(labels.get(status, status))}"></i>'
            for status in _STATUS_ORDER if stats["counts"].get(status)
        )
        done = stats["counts"].get("complete", 0)
        agent_rows.append(
            "<li>"
            + (f'<span class="who">{_e(name)}</span>' if name != "unassigned"
               else '<span class="who" style="color:var(--faint)">unassigned</span>')
            + f'<span class="bar">{segments}</span>'
            + f'<span class="n">{_e(done)}/{_e(stats["total"])}</span></li>'
        )

    body = f"""
<div class="headline">
  <h1>Progress</h1>
  <div class="bigpct"><b>{_e(progress["percent"])}%</b><span>of the planned work is complete</span></div>
  {_meter(counts, total)}
  {_legend(counts, labels)}
</div>
{_problems(data.get("problems") or [])}
{_notices(data.get("notices") or [])}

<section class="panel">
  <h2>Being worked on now<span class="count">{_e(len(data.get("current") or []))}</span></h2>
  {_rows(data.get("current") or [], "Nothing is claimed right now.")}
</section>

<section class="panel">
  <h2>Coming next<span class="count">ready to start</span></h2>
  {_rows(data.get("upcoming") or [], "Nothing is ready — every remaining task is blocked or waiting.")}
</section>

<section class="panel">
  <h2>Needs attention<span class="count">{_e(len(data.get("attention") or []))}</span></h2>
  {_rows(data.get("attention") or [], "Nothing blocked, failed or waiting on a dependency.", show_reason=True)}
</section>

<section class="panel">
  <h2>By agent<span class="count">{_e(len(data.get("agents") or {}))} assigned</span></h2>
  {f'<ul class="agents">{"".join(agent_rows)}</ul>' if agent_rows else '<p class="empty">No agents assigned yet.</p>'}
</section>
"""
    return _shell(f'{meta.get("project") or "Project"} — progress', "progress", body, meta)


# ---------------------------------------------------------------------------
# team page (0.4.0)
# ---------------------------------------------------------------------------

_SESSION_DOT = {"working": "in_progress", "idle": "pending", "blocked": "blocked",
                "stale": "failed", "ended": "complete"}


def _session_rows(sessions: list[dict[str, Any]], empty: str) -> str:
    if not sessions:
        return f'<p class="empty">{_e(empty)}</p>'
    out = []
    for session in sessions:
        status = str(session.get("status") or "idle")
        age = session.get("heartbeat_age_minutes")
        beat = f"{age:g} min ago" if isinstance(age, (int, float)) else "no heartbeat"
        bits = []
        if session.get("main_engineer"):
            bits.append('<span class="chip held">&#9733; Main Engineer</span>')
        if session.get("task"):
            bits.append(f'<span class="tid">{_e(session["task"])}</span>')
        if session.get("model"):
            bits.append(f'<span class="chip">{_e(session["model"])}</span>')
        note = ""
        if session.get("blocked_reason"):
            note = f'<p class="note">{_e(session["blocked_reason"])}</p>'
        elif session.get("activity"):
            note = f'<p class="note">{_e(session["activity"])}</p>'
        out.append(
            "<li>"
            f'<span class="dot {_SESSION_DOT.get(status, "pending")}" '
            f'title="{_e(session.get("status_label") or status)}"></span>'
            f'<span class="name">{_e(session.get("agent") or "unknown agent")}</span>'
            f'<span class="where">{_e(session.get("id"))}</span>'
            f'<span class="spacer"></span>'
            f'<span class="where">{_e(beat)}</span>'
            f'{"".join(bits)}'
            f'<span class="chip agent">{_e(session.get("status_label") or status)}</span>'
            f"{note}</li>"
        )
    return f'<ul class="rows">{"".join(out)}</ul>'


def _recommendation_rows(items: list[dict[str, Any]], empty: str) -> str:
    if not items:
        return f'<p class="empty">{_e(empty)}</p>'
    out = []
    for item in items:
        resolution = item.get("resolution") or {}
        detail = []
        if item.get("recommendation"):
            detail.append(f'<p class="note">{_e(item["recommendation"])}</p>')
        if item.get("reason"):
            detail.append(f'<p class="note"><b>Why:</b> {_e(item["reason"])}</p>')
        if resolution.get("reason"):
            detail.append(
                f'<p class="note"><b>{_e(str(item.get("status")).title())}:</b> '
                f'{_e(" ".join(str(resolution["reason"]).split()))}</p>'
            )
        for key, label in (("became_task", "task"), ("became_decision", "decision"),
                           ("merged_into", "merged into")):
            if resolution.get(key):
                detail.append(f'<p class="note">&rarr; {_e(label)} '
                              f'<span class="tid">{_e(resolution[key])}</span></p>')
        components = ", ".join(str(c) for c in (item.get("affected_components") or []))
        out.append(
            "<li>"
            f'<span class="dot {"failed" if item.get("severity") == "critical" else "pending"}" '
            f'title="{_e(item.get("severity"))}"></span>'
            f'<span class="name">{_e(item.get("title"))}</span>'
            f'<span class="tid">{_e(item.get("id"))}</span>'
            + (f'<span class="where">{_e(components)}</span>' if components else "")
            + f'<span class="spacer"></span>'
            f'<span class="chip">{_e(item.get("severity"))}</span>'
            f'<span class="chip agent">{_e(item.get("status"))}</span>'
            f'{"".join(detail)}</li>'
        )
    return f'<ul class="rows">{"".join(out)}</ul>'


def team_page(data: dict[str, Any], team: dict[str, Any]) -> str:
    meta = data["meta"]
    counts = team.get("counts") or {}
    sessions = team.get("sessions") or []
    live = [s for s in sessions if s.get("live")]
    stale = [s for s in sessions if s.get("status") == "stale"]
    me = team.get("main_engineer")

    workload_rows = []
    for name, stats in (team.get("workload") or {}).items():
        tasks = ", ".join(stats.get("tasks") or []) or "no task claimed"
        workload_rows.append(
            "<li>"
            f'<span class="who">{_e(name)}</span>'
            f'<span class="bar"><i style="width:100%;background:var(--in_progress)"></i></span>'
            f'<span class="n">{_e(stats.get("live", 0))} live</span>'
            f'<p class="note">{_e(tasks)}</p></li>'
        )

    body = f"""
<div class="headline">
  <h1>Team</h1>
  <div class="bigpct"><b>{_e(counts.get("working", 0))}</b><span>of
    {_e(counts.get("live", 0))} live session{"" if counts.get("live", 0) == 1 else "s"}
    {"is" if counts.get("working", 0) == 1 else "are"} working</span></div>
  <p>Main Engineer:
    {'<b>' + _e(me.get("id")) + '</b> &middot; ' + _e(me.get("agent") or "") if me
     else '<b>VACANT</b> &mdash; no live session holds the coordination post'}
    &middot; heartbeat goes stale after {_e(team.get("stale_minutes"))} min</p>
</div>
{_problems(data.get("problems") or [])}
{_notices((data.get("notices") or []) + (team.get("notices") or []))}

<section class="panel">
  <h2>Live sessions<span class="count">{_e(len(live))}</span></h2>
  {_session_rows(live, "No agent session is registered. Start one with "
                       "`aef.py session start`.")}
</section>

{'<section class="panel"><h2>Stale<span class="count">' + str(len(stale)) +
 '</span></h2>' + _session_rows(stale, "") +
 '</section>' if stale else ''}

<section class="panel">
  <h2>Awaiting a decision<span class="count">{_e(counts.get("open_recommendations", 0))}</span></h2>
  {_recommendation_rows(team.get("recommendations") or [],
                        "Nothing proposed. Agents record findings here rather than "
                        "acting on them or losing them.")}
</section>

<section class="panel">
  <h2>Accepted, rejected and merged<span class="count">kept on purpose</span></h2>
  {_recommendation_rows(team.get("resolved_recommendations") or [],
                        "None yet. A rejected proposal is kept here with its reason, "
                        "so the next agent does not re-propose it.")}
</section>

<section class="panel">
  <h2>Live workload<span class="count">by agent</span></h2>
  {f'<ul class="agents">{"".join(workload_rows)}</ul>' if workload_rows
   else '<p class="empty">No agent holds a live session.</p>'}
</section>
"""
    return _shell(f'{meta.get("project") or "Project"} — team', "team", body, meta)


def json_payload(data: dict[str, Any]) -> bytes:
    return json.dumps(data, indent=2, default=str).encode("utf-8")
