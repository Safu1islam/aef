"""Repo-callable surface (DR-005).

A thin, generic adapter: argv in, JSON out. Every subcommand is generated from
the registry, so an operation present in the UI is present here by
construction (F-1, S4). There is no business logic in this file and there must
never be — logic here would be logic the web surface does not have.

Cold start matters (C-4). Nothing at module scope imports the web framework,
and the registry is loaded lazily.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .errors import ValidationError

from .config import load as load_config
from .errors import ProMediaError

ENV_OPERATOR_TOKEN = "PROMEDIA_OPERATOR_TOKEN"


def _build_parser(operations: dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promedia",
        description=(
            "ProMedia — single-operator content production and publishing. "
            "Every capability here is the same implementation the UI calls."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON (default for agents).")
    parser.add_argument(
        "--operator-token",
        default=None,
        help=f"Operator authority token. Also read from ${ENV_OPERATOR_TOKEN}.",
    )
    subparsers = parser.add_subparsers(dest="operation", metavar="OPERATION")
    for name, op in sorted(operations.items()):
        authority_note = "" if op.authority == "agent" else "  [OPERATOR AUTHORITY REQUIRED]"
        # N12: without this, `--secret` was blocked only by argparse abbreviation
        # AMBIGUITY (matching both --secret-stdin and --secret-file), which is an
        # accident — a sensitive param with a single out-of-band form would not
        # be ambiguous and the inline value would be accepted.
        sub = subparsers.add_parser(name, help=f"{op.summary}{authority_note}", allow_abbrev=False)
        # Accepted before OR after the operation name. Agents write
        # `promedia status --json` far more naturally than the reverse, and a
        # usage error there costs a whole invocation to discover.
        sub.add_argument("--json", action="store_true", dest="json_after", default=False)
        sub.add_argument("--operator-token", dest="operator_token_after", default=None)
        for p in op.params:
            flag = p.name.replace("_", "-")
            if p.sensitive:
                # T-024: no inline flag carries a sensitive value, so there is
                # nothing for the shell to record and nothing for another process
                # to read out of argv. The value arrives out of band.
                #
                # The inline flag IS registered, as a zero-argument switch that
                # errors with instructions (N12). Leaving it unregistered relied
                # on argparse abbreviation ambiguity to reject it — an accident
                # rather than a control — and told the operator only "ambiguous
                # option", which does not say what to do instead.
                # nargs="?" so the flag CONSUMES its value rather than leaving it
                # to be reported as an unrecognised positional. That lets the
                # error name the right alternative instead of argparse's
                # "unrecognized arguments: <the secret>" — which would also have
                # echoed the credential back to the terminal.
                sub.add_argument(
                    f"--{flag}",
                    dest=f"{p.name}__inline",
                    nargs="?",
                    const=True,
                    default=None,
                    help=argparse.SUPPRESS,
                )
                sub.add_argument(
                    f"--{flag}-stdin",
                    dest=f"{p.name}__stdin",
                    action="store_true",
                    help=f"Read {p.name} from stdin (recommended).",
                )
                sub.add_argument(
                    f"--{flag}-file",
                    dest=f"{p.name}__file",
                    default=None,
                    help=f"Read {p.name} from a file. The file is not modified.",
                )
                continue
            sub.add_argument(
                f"--{flag}",
                dest=p.name,
                required=False,  # required-ness is enforced in the operation layer
                default=None,
                help=f"{p.help} ({'required' if p.required else 'optional'}, {p.type})",
            )
    return parser


def _read_sensitive(op: Any, args: Any) -> dict[str, Any]:
    """Collect sensitive values from stdin or a file, never from argv."""
    import sys as _sys
    from pathlib import Path as _Path

    from .errors import ValidationError

    collected: dict[str, Any] = {}
    for p in op.params:
        if not p.sensitive:
            continue
        if getattr(args, f"{p.name}__inline", None) is not None:
            raise ValidationError(
                f"'{p.name}' is sensitive and cannot be passed on the command line —"
                " it would be visible to every process on this machine and kept in"
                f" shell history. Use --{p.name.replace('_', '-')}-stdin or"
                f" --{p.name.replace('_', '-')}-file instead.",
                parameter=p.name,
            )
        from_file = getattr(args, f"{p.name}__file", None)
        from_stdin = getattr(args, f"{p.name}__stdin", False)
        if from_file and from_stdin:
            raise ValidationError(
                f"give either --{p.name}-stdin or --{p.name}-file, not both",
                parameter=p.name,
            )
        if from_file:
            path = _Path(from_file).expanduser()
            if not path.is_file():
                raise ValidationError(
                    f"no file at {path}", parameter=p.name, path=str(path)
                )
            collected[p.name] = path.read_text(encoding="utf-8").strip()
        elif from_stdin:
            value = _sys.stdin.read().strip()
            if not value:
                raise ValidationError(
                    f"nothing read from stdin for '{p.name}'", parameter=p.name
                )
            collected[p.name] = value
    return collected


def _emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, default=str))
        return
    if payload.get("ok") is False:
        print(f"{payload.get('error', 'ERROR')}: {payload.get('message', '')}", file=sys.stderr)
        detail = payload.get("detail") or {}
        for key, value in detail.items():
            print(f"  {key}: {value}", file=sys.stderr)
        return
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Imported here, not at module scope, to keep cold start inside C-4.
    from .core import db
    from .core.credentials import CredentialStore
    from .core.principal import resolve
    from .core.registry import Context, invoke, load_operations

    operations = load_operations()
    parser = _build_parser(operations)
    args = parser.parse_args(argv)

    if not args.operation:
        parser.print_help()
        return 2

    as_json = bool(args.json or getattr(args, "json_after", False))
    config = load_config()

    try:
        import os

        supplied = (
            args.operator_token
            or getattr(args, "operator_token_after", None)
            or os.environ.get(ENV_OPERATOR_TOKEN)
        )
        expected = CredentialStore().operator_token()
        principal = resolve(supplied, expected, identifier="cli")

        conn = db.connect(
            config.db_path, busy_timeout_ms=int(config.get("database", "busy_timeout_ms"))
        )
        try:
            if args.operation != "init":
                db.apply_schema(conn)  # idempotent; keeps a fresh checkout usable
            ctx = Context(config=config, conn=conn, principal=principal)
            op = operations[args.operation]
            raw = {
                p.name: getattr(args, p.name, None)
                for p in op.params
                if not p.sensitive
            }
            raw = {k: v for k, v in raw.items() if v is not None}
            raw.update(_read_sensitive(op, args))
            result = invoke(ctx, args.operation, raw)
        finally:
            conn.close()
    except ProMediaError as exc:
        _emit(exc.to_dict(), as_json)
        return exc.exit_code
    except KeyboardInterrupt:  # pragma: no cover
        return 130

    _emit(result, as_json)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
