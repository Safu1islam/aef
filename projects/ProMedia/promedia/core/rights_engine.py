"""Rights determination (DR-007, F-3, F-4, F-5, C-20).

A pure function. Same declaration + same evidence + same ruleset version yields
the same verdict, for ever, with no clock, no network and no model in the path.
That reproducibility is the whole value of this component: the question it must
answer is not "is this probably fine" but "on what basis, exactly, did this
system permit publication eighteen months ago".

Three properties are load-bearing:

  * The default arm is ESCALATE. A permission requires a rule; a refusal does
    not (F-3).
  * Model-authored evidence can escalate but can never permit (F-5). The
    permitting rules do not read model evidence at all.
  * Transformation never changes a verdict. Derivatives inherit their source's
    verdict rather than being re-evaluated (F-4) — enforced in ``rights.py``,
    where lineage is known.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError
from .db import canonical_json

# PyYAML is NOT imported at module scope. It costs ~104ms of the CLI's ~213ms
# package import, and every repo-callable invocation pays it — including the
# many that never evaluate a ruleset. C-4 is the constraint DR-001 identified as
# this language's weakest point, so the import is deferred into load_ruleset().

PERMITTED = "PERMITTED"
BLOCKED = "BLOCKED"
ESCALATE = "ESCALATE"

_RULESET_DIR = Path(__file__).with_name("rulesets")


@dataclass(frozen=True)
class Declaration:
    authorship: str = "unknown"  # operator_original | third_party | unknown
    third_party_material: tuple[str, ...] = ()
    source_url: str | None = None
    licence_grantor: str | None = None
    licence_scope: str | None = None
    licence_evidence_ref: str | None = None
    public_domain_source: str | None = None
    # Which principal attested to this. Derived from the caller, never supplied.
    # A permitting rule may only fire on an operator attestation — an agent
    # claiming "this is the operator's own work" is a proposal, not a fact, and
    # treating it as one would let an agent manufacture PERMITTED (F-2/F-3).
    attested_by: str = "agent"

    @property
    def has_licence(self) -> bool:
        return bool(self.licence_grantor and self.licence_scope and self.licence_evidence_ref)

    @property
    def operator_attested(self) -> bool:
        return self.attested_by == "operator"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["third_party_material"] = list(self.third_party_material)
        return d


@dataclass(frozen=True)
class EvidenceItem:
    kind: str
    body: str
    produced_by: str  # operator | agent | model | system
    confidence: float | None = None
    model_id: str | None = None

    @property
    def is_model(self) -> bool:
        return self.produced_by == "model"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Verdict:
    verdict: str
    matched_rule: str
    reasons: tuple[str, ...]
    ruleset: str
    ruleset_version: str
    jurisdiction: str
    evidence_digest: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        return d


@dataclass(frozen=True)
class Ruleset:
    name: str
    version: str
    jurisdiction: str
    rules: tuple[dict[str, Any], ...]
    default: dict[str, Any]
    parameters: dict[str, Any] = field(default_factory=dict)


def load_ruleset(name: str, version: str) -> Ruleset:
    import yaml  # deferred: see the note at the top of this module

    path = _RULESET_DIR / f"{name}-{version}.yaml"
    if not path.is_file():
        raise ConfigurationError(
            f"no ruleset '{name}' version '{version}'", ruleset=name, version=version, path=str(path)
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Ruleset(
        name=data["ruleset"],
        version=str(data["version"]),
        jurisdiction=data["jurisdiction"],
        rules=tuple(data["rules"]),
        default=data["default"],
        parameters=data.get("parameters", {}),
    )


def evidence_digest(declaration: Declaration, evidence: tuple[EvidenceItem, ...]) -> str:
    """Stable fingerprint of the exact inputs a verdict was derived from.

    Sorted so that evidence insertion order cannot change the digest — two
    identical evidence sets must fingerprint identically for C-20 to hold.
    """
    payload = {
        "declaration": declaration.to_dict(),
        "evidence": sorted((e.to_dict() for e in evidence), key=canonical_json),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --- rule predicates ---------------------------------------------------------
# Each returns True when the rule fires. Rule *composition and order* live in
# the YAML ruleset; only the predicates are code.


def _unknown_provenance(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    return d.authorship == "unknown"


def _third_party_uncleared(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    if not d.third_party_material:
        return False
    return not d.has_licence


def _model_contradicts(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    """Model evidence that disagrees with a claim of sole authorship.

    This is the only rule that reads model evidence, and its outcome is
    ESCALATE. There is deliberately no path by which a model's confidence
    produces PERMITTED.
    """
    if d.authorship != "operator_original":
        return False
    threshold = float(p.get("model_contradiction_threshold", 0.5))
    for item in ev:
        if not item.is_model:
            continue
        if item.kind != "third_party_material_suspected":
            continue
        if item.confidence is not None and item.confidence >= threshold:
            return True
    return False


def _declaration_not_attested(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    """Fires when a permission WOULD be reachable on an unattested declaration.

    Sits ahead of every permitting rule. It deliberately does not fire when the
    declaration could only ever block or escalate, so an agent can still ingest
    and get a truthful BLOCKED without an operator round-trip.
    """
    if d.operator_attested:
        return False
    would_permit = (
        (d.authorship == "operator_original" and not d.third_party_material)
        or d.has_licence
        or bool(d.public_domain_source)
    )
    return would_permit


def _operator_original(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    return (
        d.operator_attested
        and d.authorship == "operator_original"
        and not d.third_party_material
    )


def _explicit_licence(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    return d.operator_attested and d.has_licence


def _public_domain_verified(d: Declaration, ev: tuple[EvidenceItem, ...], p: dict[str, Any]) -> bool:
    if not d.operator_attested or not d.public_domain_source:
        return False
    # Verification by a model does not count (F-5).
    return any(
        e.kind == "public_domain_verification" and e.produced_by in {"operator", "system"}
        for e in ev
    )


PREDICATES = {
    "UNKNOWN_PROVENANCE": _unknown_provenance,
    "THIRD_PARTY_MATERIAL_UNCLEARED": _third_party_uncleared,
    "MODEL_CONTRADICTS_DECLARATION": _model_contradicts,
    "DECLARATION_NOT_OPERATOR_ATTESTED": _declaration_not_attested,
    "OPERATOR_ORIGINAL": _operator_original,
    "EXPLICIT_LICENCE": _explicit_licence,
    "PUBLIC_DOMAIN_VERIFIED": _public_domain_verified,
}


def evaluate(
    declaration: Declaration,
    evidence: tuple[EvidenceItem, ...],
    ruleset: Ruleset,
) -> Verdict:
    """Pure. No clock, no I/O, no model."""
    digest = evidence_digest(declaration, evidence)
    for rule in ruleset.rules:
        rule_id = rule["id"]
        predicate = PREDICATES.get(rule_id)
        if predicate is None:
            raise ConfigurationError(
                f"ruleset references unknown rule '{rule_id}'", rule=rule_id, ruleset=ruleset.name
            )
        if predicate(declaration, evidence, ruleset.parameters):
            return Verdict(
                verdict=rule["outcome"],
                matched_rule=rule_id,
                reasons=(str(rule.get("because", "")).strip(),),
                ruleset=ruleset.name,
                ruleset_version=ruleset.version,
                jurisdiction=ruleset.jurisdiction,
                evidence_digest=digest,
            )
    default = ruleset.default
    return Verdict(
        verdict=default["outcome"],
        matched_rule=default["id"],
        reasons=(str(default.get("because", "")).strip(),),
        ruleset=ruleset.name,
        ruleset_version=ruleset.version,
        jurisdiction=ruleset.jurisdiction,
        evidence_digest=digest,
    )
