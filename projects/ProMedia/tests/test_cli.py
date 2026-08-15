"""T-004 — the repo-callable surface, as an agent actually uses it.

These run the CLI as a subprocess rather than calling main() in-process,
because exit codes and stdout parseability are the contract agents depend on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def run_cli(args, tmp_path, env_extra=None):
    env = dict(os.environ)
    env["PROMEDIA_DATA_DIR"] = str(tmp_path / "data")
    env["PROMEDIA_CREDENTIAL_STORE"] = str(tmp_path / "creds.json")
    env["PYTHONPATH"] = str(REPO)
    env.pop("PROMEDIA_OPERATOR_TOKEN", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "promedia", *args],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        env=env,
        timeout=120,
    )


def _mint_operator_token(tmp_path):
    """Create the operator token in the same store the CLI subprocess reads."""
    import sys as _sys

    _sys.path.insert(0, str(REPO))
    from promedia.core.credentials import CredentialStore

    return CredentialStore(tmp_path / "creds.json").ensure_operator_token()


def test_ops_listing_is_complete(tmp_path):
    """AC-1: an agent discovers the whole contract in one call."""
    proc = run_cli(["ops", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    names = {op["name"] for op in payload["operations"]}
    for expected in (
        "ingest", "determine-rights", "seal-provenance", "queue-post",
        "approve-post", "publish-post", "storage-status", "audit",
    ):
        assert expected in names
    approve = next(op for op in payload["operations"] if op["name"] == "approve-post")
    assert approve["authority"] == "operator"
    assert [p["name"] for p in approve["params"]] == ["post_id", "decision"]


def test_operator_operation_exits_3_for_agent(tmp_path):
    """AC-2: exit 3 means 'hand this to the operator', distinct from failure."""
    proc = run_cli(
        ["approve-post", "--post-id", "post_x", "--json"], tmp_path
    )
    assert proc.returncode == 3, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["error"] == "FORBIDDEN"
    assert "remedy" in payload["detail"]


def test_json_output_on_success_and_failure(tmp_path):
    """AC-3."""
    ok = run_cli(["status", "--json"], tmp_path)
    assert ok.returncode == 0
    assert json.loads(ok.stdout)["ok"] is True

    bad = run_cli(["asset", "--asset-id", "nope", "--json"], tmp_path)
    assert bad.returncode == 1
    payload = json.loads(bad.stdout)
    assert payload["ok"] is False
    assert payload["error"] == "NOT_FOUND"


def test_missing_required_parameter_exits_2(tmp_path):
    proc = run_cli(["asset", "--json"], tmp_path)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"] == "VALIDATION"


def test_full_slice_via_cli(tmp_path):
    """The whole vertical slice, driven only from the repo-callable surface.

    Publishing still requires the operator token, so this proves the agent path
    reaches exactly as far as F-2 allows and stops.
    """
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"screen recording bytes")

    ingest = run_cli(
        [
            "ingest",
            "--source-path", str(media),
            "--declaration", json.dumps({"authorship": "operator_original", "third_party_material": []}),
            "--json",
        ],
        tmp_path,
    )
    assert ingest.returncode == 0, ingest.stdout + ingest.stderr
    asset_id = json.loads(ingest.stdout)["asset_id"]

    # An agent's own declaration is a proposal, not an attestation.
    rights = run_cli(["determine-rights", "--asset-id", asset_id, "--json"], tmp_path)
    assert json.loads(rights.stdout)["verdict"] == "ESCALATE"
    assert json.loads(rights.stdout)["matched_rule"] == "DECLARATION_NOT_OPERATOR_ATTESTED"

    attest_as_agent = run_cli(["attest-declaration", "--asset-id", asset_id, "--json"], tmp_path)
    assert attest_as_agent.returncode == 3, "attestation is operator authority"

    token = _mint_operator_token(tmp_path)
    run_cli(["attest-declaration", "--asset-id", asset_id, "--json"], tmp_path,
            {"PROMEDIA_OPERATOR_TOKEN": token})
    rights = run_cli(["determine-rights", "--asset-id", asset_id, "--json"], tmp_path)
    assert json.loads(rights.stdout)["verdict"] == "PERMITTED"

    seal = run_cli(["seal-provenance", "--asset-id", asset_id, "--json"], tmp_path)
    assert seal.returncode == 0
    provenance_id = json.loads(seal.stdout)["provenance_id"]

    verify = run_cli(["verify-provenance", "--provenance-id", provenance_id, "--json"], tmp_path)
    assert json.loads(verify.stdout)["integrity_verified"] is True

    # The agent cannot connect an account — that is operator authority.
    connect = run_cli(["connect-account", "--platform", "x", "--handle", "me", "--json"], tmp_path)
    assert connect.returncode == 3
