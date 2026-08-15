"""Shared fixtures.

Every test runs against a temporary data directory and a temporary credential
store, so no test can touch the operator's real database or real credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from promedia.config import DEFAULTS, Config  # noqa: E402
from promedia.core import db  # noqa: E402
from promedia.core.credentials import CredentialStore  # noqa: E402
from promedia.core.principal import agent, operator  # noqa: E402
from promedia.core.registry import Context  # noqa: E402

GB = 1024 ** 3


def make_config(tmp_path: Path, **overrides) -> Config:
    values = {section: dict(keys) for section, keys in DEFAULTS.items()}
    for dotted, value in overrides.items():
        section, key = dotted.split(".", 1)
        values[section][key] = value
    return Config(values=values, data_dir=tmp_path / "data", source=None)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture
def conn(config: Config):
    connection = db.connect(config.db_path)
    db.apply_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def store(tmp_path: Path) -> CredentialStore:
    return CredentialStore(tmp_path / "creds" / "credentials.json")


@pytest.fixture
def agent_ctx(config, conn) -> Context:
    return Context(config=config, conn=conn, principal=agent("test-agent"))


@pytest.fixture
def operator_ctx(config, conn) -> Context:
    return Context(config=config, conn=conn, principal=operator("test-operator"))


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake media bytes for testing" * 100)
    return path


def declaration_original() -> dict:
    return {"authorship": "operator_original", "third_party_material": []}


def attest(ctx, asset_id: str) -> None:
    """Operator-attest a declaration an agent proposed.

    Since the DECLARATION_NOT_OPERATOR_ATTESTED rule landed, an agent-declared
    asset escalates rather than permitting. Tests that need a PERMITTED asset
    must go through the operator, exactly as the real flow does.
    """
    from promedia.core.principal import operator
    from promedia.core.registry import Context, invoke

    op_ctx = Context(config=ctx.config, conn=ctx.conn, principal=operator("test-operator"))
    invoke(op_ctx, "attest-declaration", {"asset_id": asset_id})
    invoke(op_ctx, "determine-rights", {"asset_id": asset_id})


def declaration_uncleared() -> dict:
    return {"authorship": "operator_original", "third_party_material": ["background music"]}


def declaration_unknown() -> dict:
    return {"authorship": "unknown", "third_party_material": []}
