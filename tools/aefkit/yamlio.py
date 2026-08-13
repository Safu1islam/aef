"""YAML reading for AEF state files, with no mandatory dependency.

PyYAML is used when it is importable, because a real parser beats a subset every
time. When it is absent, a bundled parser covers the subset AEF state files
actually use.

The bundled parser's contract is deliberately narrow and LOUD: anything it does
not understand raises YamlSubsetError naming the file and line, rather than
guessing. Constitution section 6 — a parser that silently mis-reads a plan is a
fabrication that reports itself as fact.

Supported subset, and every item here is exercised against the project's real
state files by aef/tools/tests/test_yamlio.py, which parses each one with BOTH
readers and asserts the results are equal:

    - block mappings and block sequences, nested by indentation
    - compact `- key: value` sequence-of-mapping entries
    - block scalars `|` and `>`, after a key or after a dash, with chomping and
      explicit-indent indicators
    - plain, 'single-quoted' and "double-quoted" scalars, INCLUDING ones that
      span several lines
    - flow collections [a, b] and {a: b}, possibly spanning lines
    - comments, blank lines, a leading `---`
    - null (empty, `null`, `~`), booleans, integers, floats

Not supported, and refused rather than approximated: anchors and aliases, tags,
multiple documents, complex keys.
"""

from __future__ import annotations

import datetime as _datetime
import re
from typing import Any

__all__ = ["load", "loads", "YamlSubsetError", "reader_name", "USING_PYYAML"]


class YamlSubsetError(ValueError):
    """The bundled parser met a construct it will not guess at."""


try:  # pragma: no cover - environment dependent
    import yaml as _pyyaml
except ImportError:  # pragma: no cover - environment dependent
    _pyyaml = None

USING_PYYAML = _pyyaml is not None


def reader_name() -> str:
    """Which reader is active. Surfaced in the dashboard footer and `aef doctor`."""
    if USING_PYYAML:
        return f"PyYAML {getattr(_pyyaml, '__version__', 'unknown')}"
    return "bundled subset reader (PyYAML not installed)"


def load(path, *, force_bundled: bool = False) -> Any:
    """Parse a YAML file. `force_bundled` exists so the tests can exercise the
    fallback on a machine that does have PyYAML."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return loads(text, name=str(path), force_bundled=force_bundled)


def loads(text: str, *, name: str = "<string>", force_bundled: bool = False) -> Any:
    if USING_PYYAML and not force_bundled:
        return _pyyaml.safe_load(text)
    return _Parser(text, name).parse()


# ---------------------------------------------------------------------------
# Bundled subset parser
#
# A cursor over raw lines. Comments and blanks are skipped when looking for the
# next token, never pre-stripped: a '#' inside a block scalar is text, and a
# blank line inside one is content.
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_BLOCK_RE = re.compile(r"^([|>])([+-]?)(\d*)([+-]?)\s*$")
# YAML 1.1 timestamps. Present so an unquoted `planned_at: 2026-08-08` yields the
# same datetime.date PyYAML yields — the two readers must not disagree about a
# value's TYPE, or behaviour would depend on whether PyYAML happens to be
# installed, which is the one divergence that would be genuinely dangerous.
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[Tt]|[ \t]+)"
    r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d*))?"
    r"(?:[ \t]*(?:(Z)|([-+])(\d{1,2})(?::(\d{2}))?))?$"
)
_KEY_RE = re.compile(
    r"""^(?P<key>
            "(?:[^"\\]|\\.)*"      # "double quoted"
          | '(?:[^']|'')*'         # 'single quoted'
          | [^:\#\[\]{}\-"'][^:\#]*?  # plain — never starts with a quote, or a
          | -[^:\#\s"'][^:\#]*?       # fully quoted scalar holding a colon
                                      # ("Scope: ..." ) would parse as a key
        )
        \s*:(?P<rest>\s.*|)$""",
    re.VERBOSE,
)
_UNSUPPORTED = {"&": "anchors", "*": "aliases", "!": "tags"}


class _Parser:
    def __init__(self, text: str, name: str):
        self.name = name
        self.raw = [line.replace("\t", "    ").rstrip("\r") for line in text.splitlines()]
        self.pos = 0

    # -- cursor -----------------------------------------------------------

    def _seek(self) -> int | None:
        """Index of the next line carrying a token, or None. Does not advance."""
        index = self.pos
        while index < len(self.raw):
            stripped = self.raw[index].strip()
            if stripped == "" or stripped.startswith("#") or stripped == "---":
                index += 1
                continue
            if stripped == "...":
                return None
            return index
        return None

    @staticmethod
    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _fail(self, index: int, why: str):
        line = self.raw[index] if 0 <= index < len(self.raw) else ""
        raise YamlSubsetError(
            f"{self.name}:{index + 1}: {why}\n"
            f"    {line.strip()}\n"
            "The bundled reader covers only the subset AEF state files use. "
            "Install PyYAML (pip install pyyaml) to parse this file."
        )

    # -- entry ------------------------------------------------------------

    def parse(self) -> Any:
        index = self._seek()
        if index is None:
            return None
        value = self._parse_node(self._indent(self.raw[index]))
        remaining = self._seek()
        if remaining is not None:
            self._fail(remaining, "unexpected content after the document body")
        return value

    def _parse_node(self, indent: int) -> Any:
        index = self._seek()
        if index is None:
            return None
        content = self.raw[index].strip()
        if content == "-" or content.startswith("- "):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    # -- sequences --------------------------------------------------------

    def _parse_sequence(self, indent: int) -> list:
        items: list[Any] = []
        while True:
            index = self._seek()
            if index is None or self._indent(self.raw[index]) != indent:
                break
            content = self.raw[index].strip()
            if not (content == "-" or content.startswith("- ")):
                break
            self.pos = index + 1
            rest = content[1:].strip()
            if rest == "":
                nxt = self._seek()
                if nxt is not None and self._indent(self.raw[nxt]) > indent:
                    items.append(self._parse_node(self._indent(self.raw[nxt])))
                else:
                    items.append(None)
                continue
            items.append(self._parse_item(index, rest, indent))
        return items

    def _parse_item(self, index: int, rest: str, dash_indent: int) -> Any:
        """Content that sits on the dash line itself.

        An inline mapping's remaining keys line up with the first key, which is
        two columns right of the dash. A block scalar or a plain scalar, by
        contrast, continues relative to the DASH — that difference is why both
        indents are needed here rather than one.
        """
        key_indent = dash_indent + 2
        rest = self._strip_comment(rest)
        block = _BLOCK_RE.match(rest)
        if block:
            return self._read_block_scalar(block, dash_indent)
        match = _KEY_RE.match(rest)
        if match and not self._colon_is_not_a_key(rest):
            mapping: dict[str, Any] = {}
            self._consume_pair(mapping, index, match, key_indent)
            self._parse_mapping(key_indent, into=mapping)
            return mapping
        return self._read_scalar(index, rest, dash_indent)

    @staticmethod
    def _colon_is_not_a_key(text: str) -> bool:
        """`- https://example.com/x` is a scalar, not a mapping. A key does not
        contain a slash immediately around its colon."""
        head = text.split(":", 1)[0]
        return "//" in head or head.endswith("/") or text.split(":", 1)[1][:1] == "/"

    # -- mappings ---------------------------------------------------------

    def _parse_mapping(self, indent: int, into: dict | None = None) -> dict:
        mapping: dict[str, Any] = into if into is not None else {}
        while True:
            index = self._seek()
            if index is None or self._indent(self.raw[index]) != indent:
                break
            content = self.raw[index].strip()
            if content == "-" or content.startswith("- "):
                break
            if content[0] in _UNSUPPORTED:
                self._fail(index, f"{_UNSUPPORTED[content[0]]} are not supported")
            match = _KEY_RE.match(content)
            if not match:
                self._fail(index, "expected `key: value`")
            self.pos = index + 1
            self._consume_pair(mapping, index, match, indent)
        return mapping

    def _consume_pair(self, mapping: dict, index: int, match, indent: int) -> None:
        key = self._key(match.group("key"))
        rest = self._strip_comment((match.group("rest") or "").strip())

        block = _BLOCK_RE.match(rest)
        if block:
            mapping[key] = self._read_block_scalar(block, indent)
            return

        if rest == "":
            nxt = self._seek()
            if nxt is None:
                mapping[key] = None
                return
            child_indent = self._indent(self.raw[nxt])
            child_content = self.raw[nxt].strip()
            if child_indent > indent:
                mapping[key] = self._parse_node(child_indent)
            elif child_indent == indent and (child_content == "-" or child_content.startswith("- ")):
                # A sequence is allowed to sit at the same column as its key.
                mapping[key] = self._parse_sequence(indent)
            else:
                mapping[key] = None
            return

        mapping[key] = self._read_scalar(index, rest, indent)

    def _key(self, text: str):
        """Keys resolve like any other plain scalar. `exit_codes: {0: ...}` has
        an INTEGER key in PyYAML, and a dict keyed by "0" is not the same dict."""
        text = text.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
            return self._unquote(text)
        if text in ("null", "~", "Null", "NULL"):
            return None
        lowered = text.lower()
        if lowered in ("true", "yes", "on"):
            return True
        if lowered in ("false", "no", "off"):
            return False
        if _INT_RE.match(text):
            return int(text)
        if _FLOAT_RE.match(text) and not _INT_RE.match(text):
            try:
                return float(text)
            except ValueError:
                return text
        stamp = self._timestamp(text)
        return stamp if stamp is not None else text

    # -- block scalars ----------------------------------------------------

    def _read_block_scalar(self, match, indent: int) -> str:
        style = match.group(1)
        chomp = match.group(2) or match.group(4) or ""
        explicit = int(match.group(3)) if match.group(3) else 0

        body: list[str] = []
        detected = indent + explicit if explicit else None
        while self.pos < len(self.raw):
            line = self.raw[self.pos]
            if line.strip() == "":
                body.append("")
                self.pos += 1
                continue
            current = self._indent(line)
            if current <= indent:
                break
            if detected is None:
                detected = current
            if current < detected:
                break
            body.append(line[detected:])
            self.pos += 1

        trailing = 0
        while body and body[-1] == "":
            body.pop()
            trailing += 1

        text = "\n".join(body) if style == "|" else self._fold(body)

        if chomp == "-":
            return text
        if chomp == "+":
            return text + "\n" * (trailing + 1) if text else "\n" * trailing
        return text + "\n" if text else ""

    @staticmethod
    def _fold(body: list[str]) -> str:
        """Folded (`>`) line-break handling.

        The separator between two content lines depends on how many blank lines
        sit between them and on whether either is MORE-INDENTED than the block:

            plain -> plain,  no blank line   ->  " "        (the fold)
            any   -> any,    n blank lines   ->  "\\n" * n
            more-indented on either side     ->  "\\n" * (n + 1)

        A break next to a more-indented line is never folded — that is what keeps
        an indented code sample inside a folded scalar readable. Getting this
        wrong swallows exactly one newline per sample, which is invisible until
        something diffs the result against a real parser.
        """
        chunks: list[tuple[str, bool, int]] = []   # text, more_indented, blanks before
        blanks = 0
        for entry in body:
            if entry.strip() == "":
                blanks += 1
                continue
            chunks.append((entry, entry.startswith(" "), blanks))
            blanks = 0

        if not chunks:
            return ""

        out = [chunks[0][0]]
        for index in range(1, len(chunks)):
            text, more, gap = chunks[index]
            previous_more = chunks[index - 1][1]
            if more or previous_more:
                out.append("\n" * (gap + 1))
            elif gap:
                out.append("\n" * gap)
            else:
                out.append(" ")
            out.append(text)
        return "".join(out)

    # -- scalars ----------------------------------------------------------

    def _read_scalar(self, index: int, rest: str, indent: int) -> Any:
        """Read a scalar that may continue over following, more-indented lines.

        Both quoted and plain scalars are allowed to span lines, and AEF state
        files use both. Stopping at the newline is what made the first version of
        this parser silently truncate half the decision records.
        """
        if rest[:1] in ("\"", "'") and not self._quote_closed(rest):
            joined = rest
            while self.pos < len(self.raw):
                nxt = self.raw[self.pos].strip()
                self.pos += 1
                joined = joined + ("" if joined.endswith(" ") or nxt == "" else " ") + nxt
                if self._quote_closed(joined):
                    break
            else:
                self._fail(index, "unterminated quoted scalar")
            return self._scalar(index, joined.strip())

        if rest[:1] in ("[", "{") and not self._flow_closed(rest):
            joined = rest
            while self.pos < len(self.raw):
                joined += " " + self.raw[self.pos].strip()
                self.pos += 1
                if self._flow_closed(joined):
                    break
            else:
                self._fail(index, "unterminated flow collection")
            return self._scalar(index, joined.strip())

        parts = [rest]
        while True:
            nxt = self._seek()
            if nxt is None:
                break
            line = self.raw[nxt]
            if self._indent(line) <= indent:
                break
            content = line.strip()
            if content == "-" or content.startswith("- "):
                break
            if _KEY_RE.match(content) and not self._colon_is_not_a_key(content):
                break
            parts.append(self._strip_comment(content))
            self.pos = nxt + 1
        if len(parts) == 1:
            return self._scalar(index, rest)
        # A multi-line plain scalar folds on single breaks, exactly like `>`.
        return " ".join(part for part in parts if part != "").strip()

    @staticmethod
    def _strip_comment(text: str) -> str:
        """Remove a trailing ` # comment` from a plain scalar.

        Quote- and bracket-aware, so `key: "a # b"` and `key: [a, b] # note` are
        both handled. A '#' that is still inside an unclosed quote is left alone;
        the scalar reader will pick up the continuation lines and the comment, if
        any, is stripped once the quote closes.
        """
        quote = None
        depth = 0
        for index, char in enumerate(text):
            if quote:
                if quote == '"' and char == "\\":
                    continue
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "#" and depth <= 0 and (index == 0 or text[index - 1] in " \t"):
                return text[:index].rstrip()
        return text

    @staticmethod
    def _quote_closed(text: str) -> bool:
        if not text or text[0] not in "\"'":
            return True
        quote = text[0]
        i = 1
        while i < len(text):
            char = text[i]
            if quote == '"' and char == "\\":
                i += 2
                continue
            if char == quote:
                if quote == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                    continue
                return True
            i += 1
        return False

    @staticmethod
    def _flow_closed(text: str) -> bool:
        depth = 0
        quote = None
        for char in text:
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
        return depth <= 0

    def _scalar(self, index: int, text: str) -> Any:
        text = text.strip()
        if text == "" or text in ("null", "~", "Null", "NULL"):
            return None
        if text[0] in _UNSUPPORTED:
            self._fail(index, f"{_UNSUPPORTED[text[0]]} are not supported")
        if text[0] == "[" and text[-1] == "]":
            inner = text[1:-1].strip()
            return [self._scalar(index, part) for part in self._split_flow(text[1:-1])] if inner else []
        if text[0] == "{" and text[-1] == "}":
            out: dict[str, Any] = {}
            for part in self._split_flow(text[1:-1]):
                if ":" not in part:
                    self._fail(index, "flow mapping entry without a colon")
                key, _, value = part.partition(":")
                out[self._key(key)] = self._scalar(index, value)
            return out
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'" and self._quote_closed(text):
            return self._unquote(text)
        lowered = text.lower()
        if lowered in ("true", "yes", "on"):
            return True
        if lowered in ("false", "no", "off"):
            return False
        if _INT_RE.match(text):
            return int(text)
        if _FLOAT_RE.match(text):
            try:
                return float(text)
            except ValueError:
                return text
        stamp = self._timestamp(text)
        if stamp is not None:
            return stamp
        return text

    @staticmethod
    def _timestamp(text: str):
        match = _DATE_RE.match(text)
        if match:
            try:
                return _datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
        match = _DATETIME_RE.match(text)
        if not match:
            return None
        year, month, day, hour, minute, second, fraction, zulu, sign, offset_h, offset_m = match.groups()
        microsecond = int((fraction or "").ljust(6, "0")[:6]) if fraction else 0
        try:
            value = _datetime.datetime(int(year), int(month), int(day),
                                       int(hour), int(minute), int(second), microsecond)
        except ValueError:
            return None
        if sign:
            delta = _datetime.timedelta(hours=int(offset_h), minutes=int(offset_m or 0))
            value = value - delta if sign == "+" else value + delta
        elif not zulu:
            return value
        return value

    def _split_flow(self, text: str) -> list[str]:
        parts: list[str] = []
        depth = 0
        quote = None
        current: list[str] = []
        for char in text:
            if quote:
                current.append(char)
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
                current.append(char)
            elif char in "[{":
                depth += 1
                current.append(char)
            elif char in "]}":
                depth -= 1
                current.append(char)
            elif char == "," and depth == 0:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        tail = "".join(current).strip()
        if tail:
            parts.append(tail)
        return [part for part in parts if part != ""]

    @staticmethod
    def _unquote(text: str) -> str:
        body = text[1:-1]
        if text[0] == "'":
            return body.replace("''", "'")
        out: list[str] = []
        i = 0
        escapes = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\",
                   "/": "/", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b"}
        while i < len(body):
            char = body[i]
            if char == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt == "u" and i + 6 <= len(body):
                    out.append(chr(int(body[i + 2:i + 6], 16)))
                    i += 6
                    continue
                out.append(escapes.get(nxt, nxt))
                i += 2
                continue
            out.append(char)
            i += 1
        return "".join(out)
