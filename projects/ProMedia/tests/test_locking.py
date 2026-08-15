"""T-027 — C-19 entity locking, enforced in the operation layer.

The lock table in ``promedia.core.db`` was implemented and unit-tested by T-002
(``tests/test_db.py``), but no operation ever called it. C-18 allows four
concurrent agent sessions, so "exactly one writer per entity, with a visible
owner" was a claim the running system did not make. tests/test_db.py proves the
table works; this file proves the system *uses* it.

Why the tests are shaped this way. A lock is only observable while it is held,
and ``invoke`` releases before it returns — so proving engagement from outside
would only prove the table is empty afterwards. Two of these tests therefore
wrap the domain function an operation calls with a probe that runs *inside* the
handler, while the lock is held, and then delegates to the real implementation.
The probe adds no behaviour and fabricates no data; it is a window, not a
double. See the note in .ai/state/fabrications.yaml.

Locking is asserted through ``invoke`` and through both surfaces, never by
calling ``db.acquire_lock`` to demonstrate the thing it already demonstrates.
The one exception is where a test needs a *second* session to be mid-operation:
there, the row is written the same way that session's own ``invoke`` would have
written it, and the comment says so.
"""

from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promedia import cli
from promedia.config import load as load_config
from promedia.core import db
from promedia.core import rights as rights_layer
from promedia.core.principal import agent
from promedia.core.registry import (
    Context,
    Operation,
    Param,
    invoke,
    load_operations,
)
from promedia.errors import EntityLocked, NotFound, ProMediaError
from promedia.web.app import create_app
from tests.conftest import declaration_original

REPO = Path(__file__).resolve().parents[1]
OPERATIONS = load_operations()

# The operations that mutate an entity they were handed the id of. Every one is
# a write to something that already exists, which is exactly what C-19 governs.
LOCKING_OPERATIONS = {
    "add-evidence",
    "attest-declaration",
    "determine-rights",
    "seal-provenance",
    "approve-post",
    "publish-post",
    "release-publish-claim",
    # T-042. render-project matters most of any of these: it reads the edit and
    # then runs for minutes, so without the lock an edit changed mid-render
    # produces an output attributed to a version it did not come from.
    "set-edl",
    "render-project",
    # R-006. Same reasoning as render-project: the entity is the project, not
    # the render, because a delete racing a concurrent edit or render of the
    # SAME project is the hazard C-19 exists to close.
    "delete-render",
}

# Mutating operations that CREATE their entity. There is no id to lock because
# the entity does not exist yet — recorded here so the skip is a stated
# decision rather than a gap nobody noticed.
#
# connect-account was in this set until T-033 and should not have been: since
# T-023 a reconnect preserves the account id, so it writes an EXISTING row. It
# now locks on its natural key instead — see NATURAL_KEY_OPERATIONS and
# tests/test_account_locking.py.
CREATING_OPERATIONS = {"ingest", "queue-post", "create-project"}

# Mutating operations that write an entity that may already exist but were not
# handed its id. They lock on a declared natural key (Operation.lock_by).
NATURAL_KEY_OPERATIONS = {"connect-account"}


@pytest.fixture
def alpha(config, conn) -> Context:
    """Session A."""
    return Context(config=config, conn=conn, principal=agent("alpha"), agent_id="agent-alpha")


@pytest.fixture
def beta(config) -> Context:
    """Session B — its own connection, as a second agent process would have."""
    connection = db.connect(config.db_path)
    db.apply_schema(connection)
    yield Context(
        config=config, conn=connection, principal=agent("beta"), agent_id="agent-beta"
    )
    connection.close()


def _ingest(ctx: Context, media_file: Path) -> str:
    result = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    return result["asset_id"]


# --- which operations lock, and which deliberately do not ---------------------


def _probe_params(op) -> dict[str, str]:
    """Every parameter lock_target() could read, so a probe never starves it.

    Supplies the natural key as well as the id (T-033): omitting it would make
    lock_target refuse, and a test that cannot tell "refused because the key is
    missing" from "declined to lock" is not testing the rule.
    """
    params = {f"{op.entity}_id": "entity_1"} if op.entity else {}
    params.update({part: "probe" for part in op.lock_by})
    return params


def test_lock_target_follows_the_registry_rule():
    """A capability locks iff it mutates a named entity it can identify.

    Asserted as a rule over the whole registry rather than a list, so a new
    operation is covered the day it is registered (the DR-002 reasoning that
    put authority on the Operation).
    """
    from promedia.core.registry import lock_target

    for name, op in OPERATIONS.items():
        target = lock_target(op, _probe_params(op))
        declares_id = op.entity is not None and f"{op.entity}_id" in {p.name for p in op.params}
        if op.mutates and declares_id:
            assert target == (op.entity, "entity_1"), f"'{name}' should lock its entity"
        elif op.mutates and op.lock_by:
            expected = "key:" + ":".join("probe" for _ in op.lock_by)
            assert target == (op.entity, expected), f"'{name}' should lock its natural key"
        else:
            assert target is None, f"'{name}' must not take a lock"


def test_read_only_operations_never_lock():
    """C-19 constrains writers. A read that took a lock would block one."""
    from promedia.core.registry import lock_target

    for name, op in OPERATIONS.items():
        if op.mutates:
            continue
        assert lock_target(op, _probe_params(op)) is None, f"read-only '{name}' took a lock"


def test_the_locking_and_creating_sets_are_what_this_task_intended():
    """Pins the sets above against the registry as it actually stands."""
    from promedia.core.registry import lock_target

    by_id, by_key = set(), set()
    for name, op in OPERATIONS.items():
        target = lock_target(op, _probe_params(op))
        if target is None:
            continue
        (by_key if target[1].startswith("key:") else by_id).add(name)
    assert by_id == LOCKING_OPERATIONS
    assert by_key == NATURAL_KEY_OPERATIONS

    for name in CREATING_OPERATIONS:
        op = OPERATIONS[name]
        assert op.mutates and op.entity is not None
        assert lock_target(op, {}) is None, (
            f"'{name}' creates its {op.entity}; there is no id to lock yet"
        )


def test_a_declared_entity_id_that_is_missing_is_refused_not_skipped():
    """The guard that keeps 'no id' from meaning 'no lock' by accident.

    Unreachable today — every such parameter is required, so validate() has
    already refused. If one is ever made optional, this is the difference
    between a loud error and a silent unlocked write to a live entity.
    """
    from promedia.core.registry import lock_target
    from promedia.errors import ValidationError

    hypothetical = Operation(
        name="update-asset",
        summary="hypothetical",
        handler=lambda ctx: None,
        params=(Param("asset_id", "str", required=False),),
        mutates=True,
        entity="asset",
    )
    with pytest.raises(ValidationError) as excinfo:
        lock_target(hypothetical, {"asset_id": None})
    assert "asset_id" in str(excinfo.value)


# --- the lock engages ---------------------------------------------------------


def test_lock_is_held_by_the_operation_layer_while_the_handler_runs(
    alpha, media_file, monkeypatch, config
):
    """AC-1, first half: invoke() takes the lock, with a visible owner (C-19).

    The probe reads the lock table from inside the handler, then delegates.
    """
    asset_id = _ingest(alpha, media_file)
    seen: dict = {}
    real = rights_layer.determine

    def probe(ctx, asset_id_arg):
        seen["locks"] = db.list_locks(ctx.conn)
        return real(ctx, asset_id_arg)

    monkeypatch.setattr(rights_layer, "determine", probe)
    invoke(alpha, "determine-rights", {"asset_id": asset_id})

    assert seen, "the probe never ran; the assertions below proved nothing"
    assert len(seen["locks"]) == 1
    held = seen["locks"][0]
    assert held["entity_type"] == "asset"
    assert held["entity_id"] == asset_id
    assert held["agent"] == "agent-alpha"  # C-19: the owner is visible
    assert held["task_id"] == "determine-rights"
    assert held["model"] == alpha.model
    # TTL from configuration, never a literal (protocol 05).
    ttl_minutes = int(config.get("locks", "ttl_minutes"))
    span = datetime.fromisoformat(held["expires_at"]) - datetime.fromisoformat(
        held["acquired_at"]
    )
    assert span == timedelta(minutes=ttl_minutes)


def test_second_agent_is_refused_while_the_first_holds_the_entity(
    alpha, beta, media_file, monkeypatch
):
    """AC-1, second half: ENTITY_LOCKED, naming the first agent as owner.

    Two agent ids, two connections, genuinely overlapping: agent-beta attempts
    the asset from inside agent-alpha's handler, i.e. while alpha's operation
    is in flight.
    """
    asset_id = _ingest(alpha, media_file)
    captured: dict = {}
    real = rights_layer.determine

    def probe(ctx, asset_id_arg):
        with pytest.raises(EntityLocked) as excinfo:
            invoke(beta, "determine-rights", {"asset_id": asset_id_arg})
        captured["error"] = excinfo.value
        return real(ctx, asset_id_arg)

    monkeypatch.setattr(rights_layer, "determine", probe)
    invoke(alpha, "determine-rights", {"asset_id": asset_id})

    assert captured, "agent-beta never attempted the entity"
    error = captured["error"]
    assert error.code == "ENTITY_LOCKED"
    assert error.message == f"asset {asset_id} is locked by agent-alpha"
    assert error.detail["owner"] == "agent-alpha"
    assert error.detail["owner_task"] == "determine-rights"
    assert error.detail["entity_type"] == "asset"
    assert error.detail["entity_id"] == asset_id
    assert error.detail["expires_at"]


def test_refusal_on_ownership_is_audited(alpha, beta, media_file, monkeypatch):
    """A denial is an attempt, and the audit log answers 'what was tried'."""
    asset_id = _ingest(alpha, media_file)
    real = rights_layer.determine

    def probe(ctx, asset_id_arg):
        with pytest.raises(EntityLocked):
            invoke(beta, "determine-rights", {"asset_id": asset_id_arg})
        return real(ctx, asset_id_arg)

    monkeypatch.setattr(rights_layer, "determine", probe)
    invoke(alpha, "determine-rights", {"asset_id": asset_id})

    denials = [
        e
        for e in invoke(beta, "audit", {"limit": 50})["entries"]
        if e["outcome"] == "denied" and e["operation"] == "determine-rights"
    ]
    assert len(denials) == 1
    assert denials[0]["entity_id"] == asset_id
    assert "agent-alpha" in denials[0]["detail"]


# --- the lock releases --------------------------------------------------------


def test_lock_released_after_a_successful_operation(alpha, beta, media_file):
    """AC-1: a completed write hands the entity back."""
    asset_id = _ingest(alpha, media_file)
    invoke(alpha, "determine-rights", {"asset_id": asset_id})
    assert db.list_locks(alpha.conn) == []

    # The proof that matters: a DIFFERENT agent can now write it.
    result = invoke(beta, "determine-rights", {"asset_id": asset_id})
    assert result["ok"] is True
    assert db.list_locks(beta.conn) == []


def test_lock_released_after_a_failing_operation(alpha, beta):
    """A handler that raises must not strand the entity (hence the finally)."""
    with pytest.raises(NotFound):
        invoke(alpha, "determine-rights", {"asset_id": "as_does_not_exist"})
    assert db.list_locks(alpha.conn) == []

    # Refused for the same reason, not because agent-alpha still owns it.
    with pytest.raises(NotFound):
        invoke(beta, "determine-rights", {"asset_id": "as_does_not_exist"})


def test_lock_released_after_an_unexpected_exception(alpha, beta, media_file, monkeypatch):
    """The catch-all path releases too — that is where a stranded lock hides."""
    asset_id = _ingest(alpha, media_file)

    def explode(ctx, asset_id_arg):
        raise ZeroDivisionError("injected fault")

    monkeypatch.setattr(rights_layer, "determine", explode)
    with pytest.raises(ProMediaError) as excinfo:
        invoke(alpha, "determine-rights", {"asset_id": asset_id})
    assert excinfo.value.detail["exception_type"] == "ZeroDivisionError"

    monkeypatch.undo()
    assert db.list_locks(alpha.conn) == []
    assert invoke(beta, "determine-rights", {"asset_id": asset_id})["ok"] is True


# --- one session must not block itself ----------------------------------------


def test_same_agent_may_write_the_same_entity_repeatedly(alpha, media_file):
    """Sequential calls in one session: the normal flow, and it must not stall."""
    asset_id = _ingest(alpha, media_file)
    for operation, params in (
        ("add-evidence", {"asset_id": asset_id, "kind": "note", "body": "b", "produced_by": "agent"}),
        ("determine-rights", {"asset_id": asset_id}),
        ("determine-rights", {"asset_id": asset_id}),
        ("seal-provenance", {"asset_id": asset_id}),
    ):
        assert invoke(alpha, operation, params)["ok"] is True
    assert db.list_locks(alpha.conn) == []


def test_nested_invoke_does_not_release_the_outer_lock(alpha, beta, media_file, monkeypatch):
    """Re-entrancy: an inner call must not hand the entity away mid-write.

    Without the held_locks guard the inner finally would delete the row while
    the outer handler was still running — leaving the outer call believing it
    had exclusivity it no longer had, which is worse than never locking.
    """
    asset_id = _ingest(alpha, media_file)
    observed: dict = {}
    real = rights_layer.determine

    def probe(ctx, asset_id_arg):
        # Same session, same entity, different operation — the nested case.
        invoke(ctx, "add-evidence", {
            "asset_id": asset_id_arg, "kind": "note", "body": "b", "produced_by": "agent",
        })
        observed["after_inner"] = db.list_locks(ctx.conn)
        with pytest.raises(EntityLocked):
            invoke(beta, "determine-rights", {"asset_id": asset_id_arg})
        observed["beta_refused"] = True
        return real(ctx, asset_id_arg)

    monkeypatch.setattr(rights_layer, "determine", probe)
    invoke(alpha, "determine-rights", {"asset_id": asset_id})

    assert observed.get("beta_refused"), "the nested probe never ran"
    assert [(r["entity_id"], r["agent"]) for r in observed["after_inner"]] == [
        (asset_id, "agent-alpha")
    ]
    assert db.list_locks(alpha.conn) == []
    assert alpha.held_locks == set()


# --- both surfaces refuse identically (F-1, S4) -------------------------------


@pytest.fixture
def surfaces(tmp_path, monkeypatch):
    """Both adapters over one configuration and one data directory.

    Mirrors tests/test_parity.py::surfaces deliberately: the only variable is
    the surface. No operator token is written, so both run as an agent — which
    is what makes determine-rights (agent authority, mutating, entity=asset)
    reachable on both.
    """
    monkeypatch.setenv("PROMEDIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    monkeypatch.setenv("PROMEDIA_CONFIG", str(REPO / "promedia.toml"))
    monkeypatch.delenv("PROMEDIA_OPERATOR_TOKEN", raising=False)

    cfg = load_config()
    connection = db.connect(cfg.db_path)
    db.apply_schema(connection)
    # A third agent is mid-operation on this asset. Written the way that
    # session's own invoke() would have written it — there is no other way to
    # have a lock outstanding while a separate process makes its attempt.
    db.acquire_lock(
        connection,
        "asset",
        "as_owned_by_someone_else",
        task_id="determine-rights",
        agent="agent-gamma",
        model="claude-opus-5",
        ttl_minutes=int(cfg.get("locks", "ttl_minutes")),
    )
    connection.close()
    yield TestClient(create_app(cfg))


def _cli(name: str, params: dict[str, str]) -> tuple[dict, int]:
    argv = [name, "--json"]
    for key, value in params.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = cli.main(argv)
    return json.loads(stdout.getvalue()), int(exit_code)


def test_entity_locked_is_refused_identically_on_both_surfaces(surfaces):
    """F-1: locking lives in the operation layer, so neither surface can differ.

    The asset does not exist, so a handler that ran would raise NOT_FOUND.
    Both surfaces returning ENTITY_LOCKED also pins the ordering: ownership is
    settled before the handler is allowed to touch anything.
    """
    params = {"asset_id": "as_owned_by_someone_else"}

    response = surfaces.post("/api/op/determine-rights", data=params)
    web = response.json()
    cli_payload, exit_code = _cli("determine-rights", params)

    assert web["error"] == "ENTITY_LOCKED" == cli_payload["error"]
    assert web["message"] == cli_payload["message"]
    assert web["message"] == "asset as_owned_by_someone_else is locked by agent-gamma"
    assert web["detail"]["owner"] == cli_payload["detail"]["owner"] == "agent-gamma"
    assert web["detail"]["owner_task"] == cli_payload["detail"]["owner_task"]
    assert web["detail"]["expires_at"] == cli_payload["detail"]["expires_at"]
    # Surface-native signals (T-032, DR-012). Contention now has a signal of
    # its own on both surfaces: 409 Conflict on the web, exit code 4 on the CLI.
    # It is deliberately NOT the generic domain refusal (400 / exit 1), because
    # nothing is wrong with the request — another writer holds the entity, and
    # protocol 05 tells a blocked agent to take a different ready task, which it
    # cannot do while contention is indistinguishable from a business-rule
    # failure. Pinned here so a change on one surface alone is a parity failure.
    assert response.status_code == 409
    assert exit_code == 4
