"""T-033 — the account reconnect path takes an exclusive lock.

Raised by the T-027 implementer as its own open item. T-027 wired C-19 into
``invoke()`` by deriving the lock key from an ``<entity>_id`` parameter, which
covers every operation that is handed the id of the thing it writes.
``connect-account`` is the one that is not: since T-023 a reconnect PRESERVES
the account id and rotates the credential, so it writes a row that already
exists, but it takes ``platform`` and ``handle`` rather than ``account_id``.
``lock_target()`` therefore read it as a pure creation and let two agents
rotate the same credential concurrently with no owner recorded anywhere.

The fix is a declared natural key (``Operation.lock_by``), not an invented id.
These tests cover the three things that can go wrong with that:

* it does not actually lock (the defect);
* it locks, but under a key that disagrees with the handler's own
  normalisation, so two spellings of one account take two locks;
* it locks under a key that cannot collide with a generated id, which is what
  keeps the shared ``entity_locks`` table honest.
"""

from __future__ import annotations

import pytest

from promedia.core import db
from promedia.core.ops import accounts as accounts_ops
from promedia.core.principal import operator
from promedia.core.registry import Context, invoke, load_operations, lock_target
from promedia.errors import EntityLocked, ValidationError

OPERATIONS = load_operations()


def _probe_inside_the_handler(monkeypatch, probe):
    """Run ``probe()`` once, from inside connect-account, while its lock is held.

    Patches ``accounts.iso`` rather than the operation's handler: ``Operation``
    is frozen (DR-002 — the registry is not rewritable at runtime), and ``iso``
    is called in every branch of the handler, so the probe fires on the create
    path and the reconnect path alike. The guard keeps a nested connect-account
    inside the probe from re-entering it.
    """
    real_iso = accounts_ops.iso
    state = {"fired": False}

    def probing_iso(*args, **kwargs):
        if not state["fired"]:
            state["fired"] = True
            probe()
        return real_iso(*args, **kwargs)

    monkeypatch.setattr(accounts_ops, "iso", probing_iso)
    return state


@pytest.fixture
def alpha(config, conn) -> Context:
    return Context(
        config=config, conn=conn, principal=operator("op-alpha"), agent_id="agent-alpha"
    )


@pytest.fixture
def beta(config):
    """A second session with its own connection, as a second process would have."""
    connection = db.connect(config.db_path)
    db.apply_schema(connection)
    yield Context(
        config=config,
        conn=connection,
        principal=operator("op-beta"),
        agent_id="agent-beta",
    )
    connection.close()


def _connect(ctx: Context, handle: str = "acme", secret: str | None = "s3cret", platform: str = "x"):
    params = {"platform": platform, "handle": handle}
    if secret is not None:
        params["secret"] = secret
    return invoke(ctx, "connect-account", params)


# --- the key itself -----------------------------------------------------------


def test_connect_account_declares_its_natural_key():
    """The registry, not the handler, is where the key is stated (DR-002)."""
    assert OPERATIONS["connect-account"].lock_by == ("platform", "handle")


def test_the_lock_key_is_the_natural_key_not_an_invented_id():
    op = OPERATIONS["connect-account"]
    assert lock_target(op, {"platform": "x", "handle": "acme"}) == ("account", "key:x:acme")


def test_the_key_normalises_exactly_as_the_handler_does():
    """N13 again, one layer down.

    ``connect_account`` lowercases both parts to decide WHICH ROW to write. If
    the lock key did not, ``x/Acme`` and ``x/acme`` would take two different
    locks and then write the same row — locking that looks present and excludes
    nothing.
    """
    op = OPERATIONS["connect-account"]
    spellings = [
        {"platform": "x", "handle": "acme"},
        {"platform": "X", "handle": "ACME"},
        {"platform": " x ", "handle": " Acme "},
    ]
    keys = {lock_target(op, s) for s in spellings}
    assert keys == {("account", "key:x:acme")}, f"spellings disagreed: {keys}"


def test_a_missing_key_part_is_refused_rather_than_skipped():
    """The same guard the id branch has: 'cannot identify it' must not mean 'do not lock it'."""
    op = OPERATIONS["connect-account"]
    with pytest.raises(ValidationError) as excinfo:
        lock_target(op, {"platform": "x"})
    assert "handle" in str(excinfo.value)


def test_a_natural_key_cannot_be_confused_with_a_generated_id():
    """Both namespaces share one table; the prefix is what keeps them apart."""
    op = OPERATIONS["connect-account"]
    _, key = lock_target(op, {"platform": "x", "handle": "acme"})
    assert key.startswith("key:")
    # Generated ids are '<prefix>_<hex>' and never contain ':' — so no natural
    # key can ever equal one, whatever the operator types as a handle.
    assert not key.startswith("acct_")


def test_no_operation_locks_accounts_by_id_while_this_one_locks_by_key():
    """The residual risk of two namespaces, turned into a build failure.

    A natural-key lock and an id lock on the same entity type are different
    rows, so they do NOT exclude each other. That is invisible and harmless
    while only one mechanism is in use for accounts. The day someone registers
    an account operation taking an ``account_id``, this fails and forces the
    two to be reconciled instead of leaving a silent hole.
    """
    id_lockers, key_lockers = set(), set()
    for name, op in OPERATIONS.items():
        if not op.mutates or op.entity != "account":
            continue
        if f"{op.entity}_id" in {p.name for p in op.params}:
            id_lockers.add(name)
        elif op.lock_by:
            key_lockers.add(name)
    assert key_lockers == {"connect-account"}
    assert id_lockers == set(), (
        f"{id_lockers} lock an account by id while connect-account locks by natural key; "
        "they will not exclude each other — reconcile the two before shipping this"
    )


# --- the lock engages ---------------------------------------------------------


def test_a_second_agent_is_refused_mid_reconnect(alpha, beta, monkeypatch):
    """AC-1. The defect, reproduced at the point it mattered.

    Beta attempts the same account from inside alpha's handler, which is the
    only way to observe the lock while it is genuinely held.
    """
    seen: dict[str, Exception] = {}

    def probe():
        try:
            _connect(beta, handle="acme", secret="beta-secret")
        except Exception as exc:  # noqa: BLE001 — the point is WHICH one
            seen["error"] = exc

    state = _probe_inside_the_handler(monkeypatch, probe)
    _connect(alpha, handle="acme")

    assert state["fired"], "the probe never ran; the assertions below prove nothing"
    assert isinstance(seen.get("error"), EntityLocked), f"got {seen.get('error')!r}"
    assert seen["error"].detail["owner"] == "agent-alpha"
    assert seen["error"].detail["entity_type"] == "account"


def test_the_contended_account_is_the_one_named_in_the_refusal(alpha, beta, monkeypatch):
    """A refusal that does not identify the entity is not a visible owner (C-19).

    Beta uses a DIFFERENT SPELLING of the same handle, so this also proves the
    lock key case-folds in step with the handler rather than merely existing.
    """
    seen: dict[str, Exception] = {}

    def probe():
        try:
            _connect(beta, handle="ACME")
        except Exception as exc:  # noqa: BLE001
            seen["error"] = exc

    state = _probe_inside_the_handler(monkeypatch, probe)
    _connect(alpha, handle="acme")

    assert state["fired"]
    assert isinstance(seen.get("error"), EntityLocked), (
        "a differently-cased handle escaped the lock and would write the same row"
    )
    assert seen["error"].detail["entity_id"] == "key:x:acme"


def test_a_different_account_is_not_blocked(alpha, beta, monkeypatch):
    """The direction a careless fix breaks: over-locking every reconnect."""
    seen: dict[str, object] = {}

    def probe():
        try:
            seen["result"] = _connect(beta, handle="other")
        except Exception as exc:  # noqa: BLE001
            seen["result"] = exc

    state = _probe_inside_the_handler(monkeypatch, probe)
    _connect(alpha, handle="acme")

    assert state["fired"]
    assert isinstance(seen.get("result"), dict), f"a distinct account was blocked: {seen}"
    assert seen["result"]["ok"] is True


def test_the_lock_is_released_afterwards(alpha, beta):
    """A leaked lock would wedge the account for the rest of its TTL (90 minutes)."""
    _connect(alpha, handle="acme")
    assert db.list_locks(alpha.conn) == []
    # And the proof that it is really gone: another agent can now proceed.
    assert _connect(beta, handle="acme", secret="rotated")["ok"] is True


def test_the_lock_is_released_when_the_handler_fails(alpha, beta):
    """An unsupported platform must not park a lock on the way out."""
    with pytest.raises(ValidationError):
        _connect(alpha, platform="myspace", handle="acme")
    assert db.list_locks(alpha.conn) == []
    assert _connect(beta, handle="acme")["ok"] is True


def test_reconnect_still_preserves_the_account_id_under_the_lock(alpha):
    """T-023's guarantee, re-asserted now that a lock sits in front of it."""
    first = _connect(alpha, handle="acme")
    second = _connect(alpha, handle="acme", secret="rotated")
    assert second["account_id"] == first["account_id"]
    assert second["reconnected"] is True
    assert db.list_locks(alpha.conn) == []


def test_a_first_time_connect_also_locks(alpha, beta, monkeypatch):
    """Create and update share one path, so they share one lock.

    Two concurrent first-time connects of the same handle are the race
    UNIQUE(platform, handle) would otherwise have to catch, and it would report
    an integrity error rather than contention.
    """
    seen: dict[str, Exception] = {}

    def probe():
        try:
            _connect(beta, handle="brand-new")
        except Exception as exc:  # noqa: BLE001
            seen["error"] = exc

    state = _probe_inside_the_handler(monkeypatch, probe)
    # No account exists yet — alpha's call is the creating one.
    _connect(alpha, handle="brand-new")

    assert state["fired"]
    assert isinstance(seen.get("error"), EntityLocked), f"got {seen.get('error')!r}"
