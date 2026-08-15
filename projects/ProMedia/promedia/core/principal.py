"""Who is calling (F-2).

Two principals exist and there will never be more (F-9):

  operator  the sole human. May publish, spend, and clear rights flags.
  agent     Claude Code, Codex, any automation. May draft, ingest, analyse,
            rights-check and queue. Never the three above.

The operator principal requires a token held in the credential store, which
lives outside the repository (DR-008). An agent reading the repo therefore
cannot mint operator authority from anything it can see.

Residual risk, stated rather than hidden: an agent with read access to the
operator's profile directory can read the token file. On a single-user machine
no candidate mechanism closes that gap — see DR-008 threat model. The token
raises the bar from "trivially bypassed by passing a flag" to "requires
reaching outside the repository", which is the boundary that actually exists.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Literal

PrincipalKind = Literal["operator", "agent"]


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    id: str

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id}


def agent(identifier: str = "agent") -> Principal:
    return Principal(kind="agent", id=identifier)


def operator(identifier: str = "operator") -> Principal:
    return Principal(kind="operator", id=identifier)


def resolve(supplied_token: str | None, expected_token: str | None, *, identifier: str = "cli") -> Principal:
    """Return the operator principal only on a constant-time token match.

    A missing expected token means the operator has not initialised the
    credential store, so operator authority is unavailable — the safe direction.
    """
    if not supplied_token or not expected_token:
        return agent(identifier)
    if hmac.compare_digest(supplied_token, expected_token):
        return operator(identifier)
    return agent(identifier)
