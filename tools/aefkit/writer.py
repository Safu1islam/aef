"""A tiny, deterministic YAML emitter for machine-managed state.

`yamlio` is a READER by contract and stays one. This is its counterpart, and it
is deliberately not general: it emits the small subset that `sessions.yaml` and
`recommendations.yaml` need, and nothing else.

Which files may be written by machine, and why the list is short:

    sessions.yaml         yes — heartbeats every few minutes; no human writes it
    recommendations.yaml  yes — appended by agents, resolved by command
    plan.yaml             NO  — surgical single-line edit only (assign.py)
    tasks.yaml            NO  — carries evidence a human reads and reasons about
    locks.yaml            NO  — hand-written notes explaining each claim

The rule behind that split: a file people read and comment gets edited in place,
because a round-trip through any dumper destroys ordering, comments and the
author's paragraphing. A file only machines read may be re-serialised whole.

Two properties this emitter guarantees, both pinned by tests:

  * **Round-trip.** Anything written here is read back identically by BOTH the
    bundled reader and PyYAML. AEF ships without PyYAML, so agreement between the
    two is a correctness requirement, not a nicety.
  * **Determinism.** Same data in, byte-identical file out. A heartbeat that
    reordered keys would make every diff unreadable and every commit noisy.

Strings are always double-quoted. That is uglier than plain scalars and it is
the point: no value can accidentally become a bool, a number, a date or `null`
because of what it happens to look like. `status: "ended"` cannot surprise
anyone; `status: no` would.
"""

from __future__ import annotations

from typing import Any

__all__ = ["dump", "dumps"]

_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _quote(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        # repr round-trips; str() can lose precision on some values.
        return repr(value)
    return _quote(str(value))


def _emit(value: Any, indent: int, lines: list[str]) -> None:
    pad = "  " * indent

    if isinstance(value, dict):
        for key in value:
            item = value[key]
            if item is None:
                continue  # an absent key and a null key mean the same thing here
            if isinstance(item, dict):
                if not item:
                    continue
                lines.append(f"{pad}{key}:")
                _emit(item, indent + 1, lines)
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{pad}{key}: []")
                    continue
                lines.append(f"{pad}{key}:")
                _emit(item, indent + 1, lines)
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
        return

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                # First key on the dash line, the rest aligned under it.
                keys = [k for k in item if item[k] is not None
                        and not (isinstance(item[k], dict) and not item[k])]
                if not keys:
                    lines.append(f"{pad}- {{}}")
                    continue
                first, rest = keys[0], keys[1:]
                head = item[first]
                if isinstance(head, (dict, list)):
                    lines.append(f"{pad}-")
                    _emit({k: item[k] for k in keys}, indent + 1, lines)
                else:
                    lines.append(f"{pad}- {first}: {_scalar(head)}")
                    if rest:
                        _emit({k: item[k] for k in rest}, indent + 1, lines)
            else:
                lines.append(f"{pad}- {_scalar(item)}")
        return

    lines.append(f"{pad}{_scalar(value)}")


def dumps(data: dict[str, Any], header: str = "") -> str:
    """Serialise a mapping. `header` is emitted as comment lines at the top."""
    lines: list[str] = []
    if header:
        for line in header.strip("\n").split("\n"):
            lines.append(f"# {line}".rstrip())
        lines.append("")
    _emit(data, 0, lines)
    return "\n".join(lines).rstrip("\n") + "\n"


def dump(path: str, data: dict[str, Any], header: str = "") -> None:
    """Write atomically-ish: full content in one call, newline-terminated.

    `newline="\\n"` is explicit because these files are read by a byte-comparing
    round-trip test, and on Windows the default would translate line endings and
    make determinism platform-dependent.
    """
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dumps(data, header))
