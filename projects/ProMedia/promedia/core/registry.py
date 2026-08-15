"""The operation registry — DR-002.

Every capability is registered here exactly once. Both surfaces are projections
of this dict: the CLI generates a subcommand per operation, the web app
generates a route per operation. Neither contains business logic, so a
capability reachable from one surface and not the other is not expressible
(F-1, S4). ``tests/test_parity.py`` asserts that property.

Authority (F-2) is a property of the operation, checked here in the operation
layer. Putting it in the adapters would mean enforcing it twice, and the second
copy is the one that eventually drifts.

Entity locking (C-19) is here for the same reason (T-027). The lock table in
promedia.core.db was implemented and unit-tested but called by nothing, so with
up to four concurrent agent sessions (C-18) two of them could write the same
asset or post with no owner recorded anywhere. Enforcing it in ``invoke`` means
the CLI and the web surface cannot enforce it differently, because neither of
them enforces it at all.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Config
from ..errors import EntityLocked, Forbidden, ProMediaError, ValidationError
from .principal import Principal

Handler = Callable[..., Any]


@dataclass
class Context:
    """Everything a handler is allowed to reach."""

    config: Config
    conn: sqlite3.Connection
    principal: Principal
    agent_id: str = "claude-code"
    model: str = "claude-opus-5"
    # Entities this session already owns, so a nested invoke() does not release
    # a lock the enclosing call still depends on (T-027). Not part of the
    # operation contract — bookkeeping for the duration of one session.
    held_locks: set[tuple[str, str]] = field(default_factory=set, repr=False, compare=False)


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "str"  # str | int | float | bool | json
    required: bool = True
    default: Any = None
    help: str = ""
    # T-024. A sensitive value must never appear in a place the OS or a browser
    # records: argv is readable by every process on the machine and survives in
    # shell history; a query string lands in browser history and Referer headers.
    # Declaring it here means BOTH surfaces enforce the same rule, rather than
    # each adapter remembering to — the same reasoning as authority (F-2).
    sensitive: bool = False

    def coerce(self, raw: Any) -> Any:
        """Convert a surface-supplied value to the declared type.

        Both surfaces deliver strings (argv, form fields), so coercion lives
        here rather than being implemented twice.
        """
        if raw is None:
            return None
        if self.type == "str":
            return str(raw)
        if self.type == "int":
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise ValidationError(f"parameter '{self.name}' must be an integer", parameter=self.name, got=str(raw))
        if self.type == "float":
            try:
                return float(raw)
            except (TypeError, ValueError):
                raise ValidationError(f"parameter '{self.name}' must be a number", parameter=self.name, got=str(raw))
        if self.type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if self.type == "json":
            if isinstance(raw, (dict, list)):
                return raw
            try:
                return json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    f"parameter '{self.name}' must be valid JSON: {exc}", parameter=self.name
                ) from exc
        raise ValidationError(f"unknown parameter type '{self.type}'", parameter=self.name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "default": self.default,
            "help": self.help,
            "sensitive": self.sensitive,
        }


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    handler: Handler
    params: tuple[Param, ...] = ()
    authority: str = "agent"  # 'agent' = either principal; 'operator' = operator only
    mutates: bool = False
    entity: str | None = None
    danger: str | None = None  # shown on the approval surface before the control
    # T-033. The parameters forming this entity's NATURAL key, for an operation
    # that writes an existing entity it was not handed an id for. Declared here
    # rather than resolved in the handler for the DR-002 reason: a rule stated
    # on the operation is enforced once, in invoke(), for both surfaces.
    lock_by: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "authority": self.authority,
            "mutates": self.mutates,
            "entity": self.entity,
            "danger": self.danger,
            "lock_by": list(self.lock_by),
            "params": [p.to_dict() for p in self.params],
        }


OPERATIONS: dict[str, Operation] = {}


def register(
    name: str,
    summary: str,
    *,
    params: tuple[Param, ...] = (),
    authority: str = "agent",
    mutates: bool = False,
    entity: str | None = None,
    danger: str | None = None,
    lock_by: tuple[str, ...] = (),
) -> Callable[[Handler], Handler]:
    """Register a capability. Duplicate names are a hard error at import time.

    Silent overwrite is the failure this guards against: two modules claiming
    the same operation name would leave one implementation unreachable while
    both surfaces still advertised it.
    """
    if authority not in {"agent", "operator"}:
        raise ValueError(f"unknown authority '{authority}' for operation '{name}'")
    declared = {p.name for p in params}
    unknown_key = [p for p in lock_by if p not in declared]
    if unknown_key:
        # A natural key naming a parameter that does not exist would silently
        # never lock. Caught at import time, like the duplicate-name check.
        raise ValueError(
            f"operation '{name}' declares lock_by {unknown_key} "
            f"which is not among its parameters {sorted(declared)}"
        )
    if lock_by and entity is None:
        raise ValueError(f"operation '{name}' declares lock_by but names no entity to lock")

    def decorate(fn: Handler) -> Handler:
        if name in OPERATIONS:
            raise ValueError(
                f"operation '{name}' is already registered by "
                f"{OPERATIONS[name].handler.__module__}"
            )
        OPERATIONS[name] = Operation(
            name=name,
            summary=summary,
            handler=fn,
            params=params,
            authority=authority,
            mutates=mutates,
            entity=entity,
            danger=danger,
            lock_by=lock_by,
        )
        return fn

    return decorate


def load_operations() -> dict[str, Operation]:
    """Import every operation module, then return the registry."""
    from . import ops  # noqa: F401  (import triggers registration)

    return OPERATIONS


def validate(op: Operation, raw: dict[str, Any]) -> dict[str, Any]:
    known = {p.name for p in op.params}
    unexpected = set(raw) - known
    if unexpected:
        raise ValidationError(
            f"unexpected parameter(s): {', '.join(sorted(unexpected))}",
            unexpected=sorted(unexpected),
            expected=sorted(known),
        )
    resolved: dict[str, Any] = {}
    for p in op.params:
        value = raw.get(p.name)
        if value is None or value == "":
            if p.required:
                raise ValidationError(
                    f"missing required parameter '{p.name}'", parameter=p.name, expected_type=p.type
                )
            resolved[p.name] = p.default
        else:
            resolved[p.name] = p.coerce(value)
    return resolved


def lock_target(op: Operation, params: dict[str, Any]) -> tuple[str, str] | None:
    """The ``(entity_type, entity_id)`` this call must own exclusively, or None.

    The id is read from the operation's own parameters under the convention
    ``<entity>_id`` — the same key set ``_entity_id`` reads out of a result, so
    the lock key and the audit key cannot drift apart. Deriving it from the
    entity type rather than listing it per operation is the DR-002 reasoning
    again: a rule stated once cannot be forgotten when a capability is added.

    Three cases, each deliberate:

    * **Not mutating, or no entity named** — nothing to lock. A read must never
      take a lock (C-19 constrains writers only), and ``init`` /
      ``reclaim-reservations`` mutate but name no entity, so there is no owner
      to record.
    * **No ``<entity>_id`` parameter declared, and no ``lock_by``** — the
      operation *creates* the entity (``ingest``, ``queue-post``). There is no
      id yet, so there is nothing another agent could be holding and nothing to
      wait for. Skipped explicitly, not by accident.
    * **Declared** — its value is the lock key.

    ``lock_by`` (T-033) covers the case between the two: an operation that
    writes an entity that may ALREADY EXIST but was not handed an id for it.
    ``connect-account`` is the only one — since T-023 a reconnect preserves the
    account id and rotates the credential, so it mutates an existing row, but
    its identity is the natural key ``platform:handle`` rather than an
    ``account_id`` parameter. Inventing an id to satisfy the id rule would have
    been the wrong shape; the key it actually has is the thing to lock.

    Natural keys are prefixed ``key:`` so they cannot collide with a generated
    id (``acct_…``) in the shared ``entity_locks`` table. That prefix also makes
    the two namespaces *visibly* different in ``list_locks``, which C-19 needs —
    an owner you cannot identify is not a visible owner. The cost is that an
    operation locking accounts by id would NOT exclude one locking by key: they
    are different rows. No operation does today, and
    ``tests/test_account_locking.py`` fails the day one is added rather than
    leaving that to be discovered.

    Key parts are stripped and lowercased, matching the normalisation
    ``connect-account`` itself applies (N13). They must agree: if the lock key
    case-folded but the handler did not, ``x/Case`` and ``x/case`` would take
    ONE lock and write TWO accounts. Where they cannot be proven to agree, over-
    locking is the safe direction — a spurious ENTITY_LOCKED is transient and
    retryable (DR-012), whereas under-locking is a concurrent write to one row.
    """
    if not op.mutates or op.entity is None:
        return None
    key = f"{op.entity}_id"
    if key in {p.name for p in op.params}:
        value = params.get(key)
        if not isinstance(value, str) or not value:
            # Unreachable while every such parameter is required — validate() has
            # already refused a missing one. A hard error rather than a silent
            # unlocked write if that ever changes: writing to a named entity with
            # no recorded owner is the exact hole C-19 exists to close.
            raise ValidationError(
                f"operation '{op.name}' mutates a {op.entity} but supplied no '{key}' to lock",
                operation=op.name,
                parameter=key,
            )
        return (op.entity, value)
    if op.lock_by:
        parts: list[str] = []
        for part_name in op.lock_by:
            value = params.get(part_name)
            if not isinstance(value, str) or not value.strip():
                # Same reasoning as the id branch above: every lock_by parameter
                # is required today, so validate() has already refused a missing
                # one. Refusing rather than falling through to "no lock" keeps
                # an unlocked write from being the failure mode.
                raise ValidationError(
                    f"operation '{op.name}' locks its {op.entity} by "
                    f"{list(op.lock_by)} but supplied no '{part_name}'",
                    operation=op.name,
                    parameter=part_name,
                )
            parts.append(value.strip().lower())
        return (op.entity, "key:" + ":".join(parts))
    return None


def invoke(ctx: Context, name: str, raw_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run an operation. The single entry point both surfaces use."""
    from . import db  # local imports keep CLI cold start light (C-4)
    from .audit import record

    registry = load_operations()
    op = registry.get(name)
    if op is None:
        raise ValidationError(f"unknown operation '{name}'", operation=name, known=sorted(registry))

    # Authority is checked BEFORE parameter validation so a forbidden call
    # cannot be probed for parameter shape, and before any side effect.
    if op.authority == "operator" and not ctx.principal.is_operator:
        record(ctx, op.name, outcome="denied", detail="operator authority required")
        raise Forbidden(
            f"operation '{op.name}' requires operator authority",
            operation=op.name,
            principal=ctx.principal.kind,
            remedy="approve in the ProMedia UI, or supply the operator token",
        )

    params = validate(op, raw_params or {})
    audited = op.authority == "operator" or op.mutates

    # C-19: exactly one writer per entity, with a visible owner. Taken after
    # authority and validation — a refused or malformed call must not be able
    # to park a lock on an entity — and always released below, so a handler
    # that raises cannot strand one. TTL comes from configuration; protocol 05
    # forbids a literal here, and it is what makes a crashed session's lock
    # reclaimable rather than permanent.
    target = lock_target(op, params)
    acquired = False
    if target is not None and target not in ctx.held_locks:
        try:
            db.acquire_lock(
                ctx.conn,
                target[0],
                target[1],
                task_id=op.name,
                agent=ctx.agent_id,
                model=ctx.model,
                ttl_minutes=int(ctx.config.get("locks", "ttl_minutes")),
            )
        except EntityLocked as exc:
            # A refusal on ownership is as much an attempt as a refusal on
            # authority, and the audit log exists to answer "what was tried".
            record(
                ctx,
                op.name,
                outcome="denied",
                detail=f"{exc.code}: owned by {exc.detail.get('owner')}",
                entity_type=target[0],
                entity_id=target[1],
            )
            raise
        ctx.held_locks.add(target)
        acquired = True

    try:
        result = op.handler(ctx, **params)
    except ProMediaError as exc:
        if audited:
            record(ctx, op.name, outcome="failed", detail=f"{exc.code}: {exc.message}",
                   entity_type=op.entity)
        raise
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see below
        # Finding I1: only ProMediaError was audited, so an unexpected failure
        # (a constraint violation, say) left NO record of an authority-gated
        # attempt at all — contradicting DR-008's stated mitigation that every
        # such attempt is recorded. It also reached the surfaces as a raw
        # traceback or a bare HTTP 500.
        #
        # Finding N1: the exception TYPE is recorded, never str(exc). Arbitrary
        # exception text can carry a credential — an HTTP client will happily
        # put a full request URL in its error — and audit_log lives in the
        # database, which is the backup artefact DR-008 works to keep secrets
        # out of. The type is enough to diagnose; the message is not worth the
        # risk of persisting it.
        if audited:
            record(ctx, op.name, outcome="failed",
                   detail=f"unexpected {type(exc).__name__}", entity_type=op.entity)
        raise ProMediaError(
            f"operation '{op.name}' failed unexpectedly ({type(exc).__name__});"
            " see the server console for detail",
            operation=op.name,
            exception_type=type(exc).__name__,
        ) from exc
    else:
        if audited:
            record(ctx, op.name, outcome="allowed", detail=None,
                   entity_type=op.entity, entity_id=_entity_id(result))
    finally:
        # Release only what this call took. Releasing a lock an ENCLOSING
        # invoke() still holds would hand the entity to another agent while the
        # outer handler was mid-write, which is worse than never locking at
        # all — the outer call would go on believing it had exclusivity.
        if acquired and target is not None:
            ctx.held_locks.discard(target)
            db.release_lock(ctx.conn, target[0], target[1], agent=ctx.agent_id)
    return result if isinstance(result, dict) else {"result": result}


def _entity_id(result: Any) -> str | None:
    if isinstance(result, dict):
        for key in ("id", "asset_id", "post_id", "account_id", "provenance_id"):
            if isinstance(result.get(key), str):
                return result[key]
    return None
