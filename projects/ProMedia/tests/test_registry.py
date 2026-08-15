"""T-003 — registry, authority (F-2), validation."""

from __future__ import annotations

import pytest

from promedia.core.registry import OPERATIONS, Param, invoke, load_operations, register
from promedia.errors import Forbidden, ValidationError


def test_duplicate_registration_rejected():
    """AC-1: silent overwrite would leave one implementation unreachable."""
    load_operations()
    with pytest.raises(ValueError, match="already registered"):

        @register("status", "duplicate of an existing operation")
        def _dup(ctx):  # pragma: no cover
            return {}


def test_agent_denied_operator_operation(agent_ctx):
    """AC-2: FORBIDDEN, and no side effect."""
    before = agent_ctx.conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
    with pytest.raises(Forbidden) as excinfo:
        invoke(agent_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "s"})
    assert excinfo.value.detail["principal"] == "agent"
    after = agent_ctx.conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
    assert after == before, "a denied operation must not write"


def test_operator_allowed_operator_operation(operator_ctx):
    result = invoke(operator_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": None})
    assert result["ok"] is True


def test_authority_checked_before_validation(agent_ctx):
    """A forbidden call must not leak parameter shape by validating first."""
    with pytest.raises(Forbidden):
        invoke(agent_ctx, "connect-account", {})


def test_missing_parameter_structured_error(agent_ctx):
    """AC-4: names the parameter; never a traceback."""
    with pytest.raises(ValidationError) as excinfo:
        invoke(agent_ctx, "ingest", {})
    assert excinfo.value.detail["parameter"] in {"source_path", "declaration"}
    assert excinfo.value.code == "VALIDATION"


def test_unexpected_parameter_rejected(agent_ctx):
    with pytest.raises(ValidationError) as excinfo:
        invoke(agent_ctx, "status", {"nope": "1"})
    assert "nope" in excinfo.value.detail["unexpected"]


def test_unknown_operation_lists_known(agent_ctx):
    with pytest.raises(ValidationError) as excinfo:
        invoke(agent_ctx, "no-such-op", {})
    assert "status" in excinfo.value.detail["known"]


def test_param_coercion():
    assert Param("n", "int").coerce("42") == 42
    assert Param("f", "float").coerce("1.5") == 1.5
    assert Param("b", "bool").coerce("true") is True
    assert Param("b", "bool").coerce("no") is False
    assert Param("j", "json").coerce('{"a":1}') == {"a": 1}
    with pytest.raises(ValidationError):
        Param("n", "int").coerce("not-a-number")


def test_no_rights_override_operation_exists():
    """F-3 admits no override path, so none is offered on any surface."""
    ops = load_operations()
    forbidden_names = {"clear-rights-flag", "override-rights", "force-publish", "bypass-rights"}
    assert forbidden_names.isdisjoint(ops.keys())


def test_operator_only_operations_are_the_expected_set():
    """F-2: the exact set that requires the human. Growth here needs a decision.

    attest-declaration is operator-only because an agent asserting authorship or
    a licence is a proposal, not an attestation — permitting rules fire only on
    an operator attestation.

    publish-tick joined the set in T-018, and the pin firing is what forced the
    question rather than letting it slip in. It reaches external platforms, so it
    carries publish-post's authority even though every post it touches was
    already approved through the F-2 gate: the tick executes prior authorisation
    and never creates any. Windows Task Scheduler presents the operator token the
    same way the CLI does (PROMEDIA_OPERATOR_TOKEN) — there is no
    scheduler-specific credential path, which is the thing that would have
    deserved a decision record.

    record-spend and run-capability joined the set in T-048, and this pin firing
    is what forced that reasoning to be written down too rather than assumed.
    Both are mutating and reach money-adjacent state: run-capability is the one
    call that would ever reach a paid API if a live adapter existed behind it
    (today it always structurally refuses — see capability-requirements), and
    record-spend writes the financial record itself. F-2 reserves exactly this
    class for the operator ("agents may never spend money without operator
    approval"), the same reasoning that put publish-post and publish-tick here.
    Verified independently (coordinator, 2026-08-14): sabotaging spend.record()
    to skip its C-31 refusal check failed exactly the three ceiling/cap tests in
    tests/test_providers.py; reverted, spend.py SHA-256
    4ed1eebd63d06d48413f0e120a6b4e6c65c4864e0564a6232d9ba89c8a4e2e0d confirmed
    identical pre/post.
    """
    ops = load_operations()
    operator_ops = {name for name, op in ops.items() if op.authority == "operator"}
    assert operator_ops == {
        "connect-account",
        "approve-post",
        "publish-post",
        "attest-declaration",
        "release-publish-claim",
        "publish-tick",
        "export-permanent-set",
        "restore-permanent-set",
        "record-spend",
        "run-capability",
    }


def test_the_backup_export_is_the_only_operator_only_read():
    """Why one read-only operation needs the human, when no other does.

    export-permanent-set mutates nothing, so by the ordinary rule it would be
    agent authority like every other read. It is not, because it collects the
    ENTIRE audit log and publication history into one portable file at a path
    the caller chooses. 'An agent may read the audit log' and 'an agent may
    write the whole of it anywhere' are different powers, and the authority flag
    is the only thing between them.

    Asserted as a rule rather than a name so that a second operator-only read
    added later has to justify itself here.
    """
    ops = load_operations()
    operator_reads = {
        name for name, op in ops.items() if op.authority == "operator" and not op.mutates
    }
    assert operator_reads == {"export-permanent-set"}
