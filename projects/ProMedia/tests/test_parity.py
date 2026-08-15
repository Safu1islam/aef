"""T-020 / T-028 — dual-surface parity as a build gate (F-1, S4).

S4 calls a single-surface capability a build failure, not a gap. These tests
are what make that true: they enumerate the registry and assert both surfaces
cover it, so adding an operation reachable from only one place fails the suite
rather than shipping.

T-028 — why this file was rewritten. The web half used to fetch ``/api/ops``
and compare the names against ``load_operations()``. But ``api_ops`` serialises
that same dict, so the assertion reduced to ``set(X) == set(X)``: it could not
fail, and it never touched the route an agent actually calls. The guard on the
project's headline guarantee was decorative.

What replaces it: every operation is *invoked* on both surfaces with the same
inputs, and the outcome classes must match. That is the property F-1 actually
claims — not "both surfaces list the same names" but "both surfaces do the same
thing" — and it fails loudly when one surface loses a capability.

Nothing destructive or external runs. Two probes cover the registry safely:

  * an operator-authority operation is refused on authority before parameter
    validation and before any side effect, so calling one as an agent is both a
    perfectly good parity signal and inert;
  * everything else is called with either no parameters (missing-required is a
    validation refusal) or with sentinel identifiers that match no entity.

``publishing.allow_simulation`` stays false throughout, so the stub publisher
(fabrication F-001) refuses even if a publish path were somehow reached.
"""

from __future__ import annotations

import contextlib
import io
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promedia import cli
from promedia.cli import _build_parser
from promedia.config import load as load_config
from promedia.core.registry import Operation, load_operations
from promedia.web.app import create_app
from tests.conftest import make_config

REPO = Path(__file__).resolve().parents[1]
OPERATIONS = load_operations()

# Matches no asset, post, account or provenance record. Handlers therefore stop
# at a lookup rather than doing anything.
SENTINEL = "parity-probe-no-such-entity"

# The surface-native signal each error class must carry. Agents branch on these:
# test_cli.py documents exit 3 as "hand this to the operator", and the UI maps
# the same class to 403. A class that reports differently on the two surfaces is
# a parity defect even when the code string matches.
#
# ENTITY_LOCKED (T-032) is here for completeness of the contract, NOT because
# these probes reach it: all 62 run against sentinel identifiers in a store with
# no locks outstanding, so none of them can produce contention. This entry keeps
# the gate honest if a future probe ever does; the coverage that actually proves
# 409/4 is tests/test_surface_signals.py, which contends a real entity.
SURFACE_SIGNALS = {
    "OK": (200, 0),
    "VALIDATION": (400, 2),
    "FORBIDDEN": (403, 3),
    "NOT_FOUND": (404, 1),
    "ENTITY_LOCKED": (409, 4),
}


@dataclass(frozen=True)
class Outcome:
    """What a surface reported: the error class, and its native signal."""

    outcome: str  # "OK", or the ProMediaError code
    signal: int  # HTTP status on the web, exit code on the CLI
    message: str


@pytest.fixture
def surfaces(tmp_path, monkeypatch):
    """Both adapters over one configuration and one data directory.

    Config and data dir are held constant deliberately: the only variable in
    these tests is the surface. The CLI loads configuration through its own real
    code path (an agent's invocation is not special-cased), and the web app is
    handed the result of that same load, so a divergence in behaviour cannot be
    blamed on the two surfaces reading different settings.

    No operator token is written, so both surfaces run with agent authority —
    which is what makes the F-2 refusals below reachable and safe.
    """
    monkeypatch.setenv("PROMEDIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    monkeypatch.setenv("PROMEDIA_CONFIG", str(REPO / "promedia.toml"))
    monkeypatch.delenv("PROMEDIA_OPERATOR_TOKEN", raising=False)

    cfg = load_config()
    assert cfg.get("publishing", "allow_simulation") is False, (
        "these tests must not run with simulation enabled (fabrication F-001)"
    )
    return TestClient(create_app(cfg))


def _probe_params(op: Operation) -> dict[str, str]:
    """A value for every required parameter that resolves to nothing real."""
    values: dict[str, str] = {}
    for p in op.params:
        if not p.required or p.sensitive:
            continue
        if p.type == "json":
            values[p.name] = "{}"
        elif p.type in ("int", "float"):
            values[p.name] = "1"
        elif p.type == "bool":
            values[p.name] = "false"
        else:
            values[p.name] = SENTINEL
    return values


def _classify(payload: dict, signal: int) -> Outcome:
    if payload.get("ok") is False:
        return Outcome(str(payload.get("error")), signal, str(payload.get("message", "")))
    return Outcome("OK", signal, "")


def call_web(client: TestClient, name: str, params: dict[str, str]) -> Outcome:
    """Invoke over HTTP, on the generic route an agent or the UI would use.

    Always POST: T-025 refuses a state-changing operation over GET, so POST is
    the one method that reaches every operation and keeps the comparison fair.
    """
    response = client.post(f"/api/op/{name}", data=params)
    try:
        payload = response.json()
    except ValueError:  # pragma: no cover - only reachable if the route breaks
        pytest.fail(f"web surface returned non-JSON for '{name}': {response.text[:200]}")
    return _classify(payload, response.status_code)


def call_cli(name: str, params: dict[str, str]) -> Outcome:
    """Invoke through the CLI adapter: argv in, JSON and an exit code out.

    In-process rather than as a subprocess. tests/test_cli.py already pins the
    subprocess contract an agent depends on; what is needed here is one call per
    operation per probe, and 58 interpreter starts would cost more than the rest
    of the suite combined without testing anything the adapter does not already
    do in-process.
    """
    argv = [name, "--json"]
    for key, value in params.items():
        argv += [f"--{key.replace('_', '-')}", str(value)]

    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = cli.main(argv)
    except SystemExit as exc:  # argparse rejected the subcommand or a flag
        pytest.fail(
            f"'{name}' is not callable on the CLI surface — argparse exited "
            f"{exc.code} for `promedia {' '.join(argv)}`: {stderr.getvalue().strip()}"
        )

    try:
        payload = json.loads(stdout.getvalue())
    except ValueError:
        pytest.fail(f"CLI emitted non-JSON for '{name}': {stdout.getvalue()[:200]}")
    return _classify(payload, int(exit_code))


def _expected_outcome_without_parameters(op: Operation) -> str:
    """What the registry alone says an empty call must produce.

    Derived from operation metadata rather than listed per operation, so a new
    capability is covered the day it is registered and cannot be forgotten here
    — the same reasoning that put authority on the operation in DR-002.
    """
    if op.authority == "operator":
        # Authority is checked before validation, so this refusal is reached
        # without supplying anything and without touching state.
        return "FORBIDDEN"
    if any(p.required for p in op.params):
        return "VALIDATION"
    return "OK"


def _assert_agreed(name: str, probe: str, web: Outcome, cli_: Outcome) -> None:
    assert web.outcome == cli_.outcome, (
        f"F-1 violation: '{name}' behaves differently per surface on the {probe} probe — "
        f"web returned {web.outcome} ({web.message!r}), "
        f"CLI returned {cli_.outcome} ({cli_.message!r})"
    )
    expected = SURFACE_SIGNALS.get(web.outcome)
    if expected is not None:
        http_status, exit_code = expected
        assert web.signal == http_status, (
            f"'{name}' returned {web.outcome} with HTTP {web.signal}, expected {http_status}"
        )
        assert cli_.signal == exit_code, (
            f"'{name}' returned {cli_.outcome} with exit {cli_.signal}, expected {exit_code}"
        )


def test_every_operation_on_both_surfaces():
    """AC-1: the registry is the contract; both surfaces must satisfy it."""
    parser = _build_parser(OPERATIONS)
    cli_commands: set[str] = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            cli_commands |= set(choices)

    missing_from_cli = set(OPERATIONS) - cli_commands
    assert not missing_from_cli, f"operations absent from the CLI surface: {sorted(missing_from_cli)}"

    # The other direction: a subcommand with no registry entry is a capability
    # that exists on one surface only, which S4 calls a build failure just the
    # same. The old test looked only one way.
    unknown_to_registry = cli_commands - set(OPERATIONS)
    assert not unknown_to_registry, (
        f"CLI subcommands with no registered operation: {sorted(unknown_to_registry)}"
    )


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_operation_refuses_identically_on_both_surfaces(surfaces, name):
    """T-028 AC-1: invoke with no parameters on both surfaces; compare.

    This is the replacement for the tautology. It exercises /api/op/{name} and
    the CLI adapter for real, and the outcome each must produce is computed from
    the registry — so a capability that stops answering on one surface fails
    here by name.
    """
    op = OPERATIONS[name]
    web = call_web(surfaces, name, {})
    cli_ = call_cli(name, {})

    assert "unknown operation" not in web.message, (
        f"'{name}' is not reachable at /api/op/{name}: {web.message}"
    )
    _assert_agreed(name, "no-parameter", web, cli_)
    assert web.outcome == _expected_outcome_without_parameters(op), (
        f"'{name}' produced {web.outcome} on both surfaces, but the registry "
        f"(authority={op.authority}, required params="
        f"{[p.name for p in op.params if p.required]}) implies "
        f"{_expected_outcome_without_parameters(op)}"
    )


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_operation_handler_agrees_across_surfaces(surfaces, name):
    """T-028 AC-1: the same populated call, through both adapters.

    The no-parameter probe stops at the authority and validation gates. This one
    supplies every required parameter, so validation passes and the handler
    itself runs on both surfaces — with identifiers that match no entity, so the
    handler stops at a lookup instead of doing anything.
    """
    web = call_web(surfaces, name, _probe_params(OPERATIONS[name]))
    cli_ = call_cli(name, _probe_params(OPERATIONS[name]))
    _assert_agreed(name, "populated", web, cli_)


def test_authority_identical_across_surfaces(tmp_path, monkeypatch):
    """AC-2 / T-003 AC-3: the same call is denied identically on both surfaces.

    Authority lives in the operation layer, so this holds by construction — the
    test exists to detect anyone moving it into an adapter.
    """
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path)

    # Web surface, no operator token in the store -> agent authority.
    client = TestClient(create_app(cfg))
    response = client.post("/api/op/approve-post", data={"post_id": "post_missing"})
    assert response.status_code == 403
    assert response.json()["error"] == "FORBIDDEN"

    # Operation layer directly, agent principal -> the same refusal.
    from promedia.core import db
    from promedia.core.principal import agent
    from promedia.core.registry import Context, invoke
    from promedia.errors import Forbidden

    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=agent("cli"))
    with pytest.raises(Forbidden):
        invoke(ctx, "approve-post", {"post_id": "post_missing"})
    conn.close()


def test_operation_metadata_matches_across_surfaces(tmp_path):
    """Parameters and authority must be described identically, or agents mis-call."""
    cfg = make_config(tmp_path)
    client = TestClient(create_app(cfg))
    web = {op["name"]: op for op in client.get("/api/ops").json()["operations"]}
    for name, op in OPERATIONS.items():
        assert web[name]["authority"] == op.authority
        assert [p["name"] for p in web[name]["params"]] == [p.name for p in op.params]


def test_no_business_logic_in_adapters():
    """The invariant that keeps parity true: adapters must stay thin.

    A crude but effective guard — adapters may not import domain modules
    directly, because doing so is how logic starts living on one surface.
    """
    root = REPO / "promedia"
    domain_modules = ("core.posts", "core.rights_engine", "core.ingest", "core.provenance")
    for adapter in (root / "cli.py", root / "web" / "app.py"):
        text = adapter.read_text(encoding="utf-8")
        for module in domain_modules:
            assert f"import {module}" not in text and f"from ..{module}" not in text, (
                f"{adapter.name} reaches into {module}; adapters must go through the registry"
            )
