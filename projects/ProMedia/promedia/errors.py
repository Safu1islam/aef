"""Error taxonomy.

Every failure path in ProMedia raises one of these. Protocol 05 forbids silent
failure: each error carries a machine-readable ``code`` so both surfaces can
report it identically, and a ``detail`` mapping so an agent can act on it
without parsing prose.
"""

from __future__ import annotations

from typing import Any


class ProMediaError(Exception):
    """Base class. Never raised directly."""

    code = "ERROR"
    exit_code = 1

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, "detail": self.detail}


class ValidationError(ProMediaError):
    """A parameter was missing, malformed, or out of range."""

    code = "VALIDATION"
    exit_code = 2


class NotFound(ProMediaError):
    code = "NOT_FOUND"


class Forbidden(ProMediaError):
    """The principal lacks authority for this operation (F-2).

    Exit code 3 is distinct so an agent can tell "I am not allowed to do this"
    apart from "this failed". The first means hand it to the operator; the
    second means retry or report.
    """

    code = "FORBIDDEN"
    exit_code = 3


class EntityLocked(ProMediaError):
    """Another writer owns this entity right now (C-19).

    Exit code 4 is distinct so an agent can tell "this call was wrong" from
    "this call was early". Exit 1 means the operation failed and repeating it
    unchanged will fail again; 4 means nothing is wrong with the request at all
    — another writer holds the entity for the moment, so the correct response is
    the one protocol 05 already prescribes: take a different ready task and come
    back to this one later.

    Without a code of its own, contention was indistinguishable from a
    business-rule refusal on both surfaces, and an agent could not act on that
    instruction (DR-012 supersedes DR-005's exit-code table).

    4 is used because it is the first free value: 0 success, 1 failed,
    2 usage/validation, 3 FORBIDDEN, and 130 is KeyboardInterrupt.
    """

    code = "ENTITY_LOCKED"
    exit_code = 4


class CeilingExceeded(ProMediaError):
    """Admission control refused the reservation (F-7).

    Carries ``shortfall_bytes`` so the caller knows how much must be released.
    """

    code = "CEILING_EXCEEDED"


class RightsBlocked(ProMediaError):
    """The rights gate refused (F-3). Never overridable by an agent."""

    code = "RIGHTS_BLOCKED"


class ApprovalRequired(ProMediaError):
    """Operator approval is absent (F-2)."""

    code = "APPROVAL_REQUIRED"
    exit_code = 3


class IntegrityError(ProMediaError):
    """A sealed record failed verification (F-8)."""

    code = "INTEGRITY"


class MediaUnavailable(ProMediaError):
    """The media this operation needs is not on disk (F-7 retention, T-029).

    Deliberately NOT ``RightsBlocked``. Whether an asset may be used is a rights
    question and survives deletion by design (F-8); whether its bytes still
    exist is an availability question and does not. Reporting the second as the
    first would tell the operator that a clean asset was blocked on rights, and
    would invite "fixing" the rights record for a problem the rights record does
    not have.

    Deliberately not ``NotFound`` either: the asset, its declaration, its
    verdict and its sealed provenance are all present and readable. It is the
    media alone that is gone, and that distinction is the point.

    Exit code 2, like ValidationError: retrying the identical call cannot
    succeed. Retention deletion is final (project.md section 10).
    """

    code = "MEDIA_UNAVAILABLE"
    exit_code = 2


class LedgerDrift(ProMediaError):
    """The storage ledger and reality disagree (F-7).

    Raised rather than swallowed: the ledger is the only source of truth for
    usage, so an unnoticed mismatch means the ceiling silently stops being a
    ceiling.
    """

    code = "LEDGER_DRIFT"


class ConfigurationError(ProMediaError):
    code = "CONFIGURATION"
    exit_code = 2


class PlatformError(ProMediaError):
    """A real call to an external platform (T-019) failed or was refused.

    Deliberately distinct from ``ConfigurationError``: a configuration error is
    a problem this process could have caught before making the call (a
    malformed credential, a missing field); a ``PlatformError`` is the
    platform itself failing or refusing the request over the network, and the
    caller cannot fix it by reading its own config. Both surfaces only give
    structured, non-traceback output for ``ProMediaError`` subclasses (T-003
    AC-4), so a real HTTP failure needs one of these or it reaches an operator
    as a raw stack trace instead of a reported error.
    """

    code = "PLATFORM_ERROR"
