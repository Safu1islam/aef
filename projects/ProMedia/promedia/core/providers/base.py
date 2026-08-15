"""AI capability provider interface (T-048).

Mirrors DR-010's publisher pattern (``promedia/core/publishers/base.py``): one
interface, several capability kinds, and a discipline borrowed directly from
T-012 AC-3 — an unknown limit or an unknown price must read as unknown, never
as a plausible guess. ``estimate()`` below never invents a dollar figure.
Model memory is not an admissible source for API pricing in this repository
(project.md section 6, open item O-3), and that rule applies to this module
exactly as it applied to the publisher adapters.

NO PURCHASING OR PAYMENT CODE lives anywhere behind this interface — an
explicit operator instruction, restated here because it is the thing this
module exists to prove rather than merely claim. A capability may only:

  * report whether it can run right now (``available()``);
  * report exactly what would make it able to (``requirements()``);
  * report a cost estimate for operator awareness, never for an automatic
    charge, and never invented (``estimate()``);
  * attempt to run, which today always refuses (``run()``).

``run()`` never reaches a network client, a billing endpoint, or a payment
method, on any path, for any capability defined here — see
``BaseCapability.run`` below for the three refusal tiers and why each is
unconditional. ``promedia/core/providers/spend.py`` is the sibling module
that enforces the C-31 ceiling on the RECORD of past spend; it also never
spends anything.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from typing import Any, Protocol

from ...errors import ProMediaError

UNKNOWN = (
    "UNKNOWN — no verified price for this provider exists in this repository;"
    " API pricing may never be written from model memory (project.md O-3)"
)


class ProviderUnavailable(ProMediaError):
    """A capability cannot run right now (T-048; mirrors DR-010's Publisher pattern).

    Not defined in ``promedia/errors.py``: that file is owned by another
    task's claim in this parallel run (T-019 — see ``.ai/state/locks.yaml``),
    and Constitution rule 3 forbids editing a path another agent owns.
    Subclassing ``ProMediaError`` from the module that owns the domain is an
    established pattern already in this codebase —
    ``promedia/core/media/ffmpeg.py:RenderFailed`` does the same.

    Carries enough in ``.detail`` for a caller to act on: which capability,
    which provider would satisfy it, exactly what is missing, and why —
    never a bare "not available".
    """

    code = "PROVIDER_UNAVAILABLE"


@dataclass(frozen=True)
class Requirement:
    """One concrete thing standing between "unavailable" and "available"."""

    kind: str  # "package" | "api_credential" | "verified_pricing"
    name: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name, "detail": self.detail}


@dataclass(frozen=True)
class Requirements:
    capability: str
    provider: str
    satisfied: bool
    missing: tuple[Requirement, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "provider": self.provider,
            "satisfied": self.satisfied,
            "missing": [m.to_dict() for m in self.missing],
            "note": self.note,
        }


@dataclass(frozen=True)
class Estimate:
    capability: str
    provider: str
    unit: str = UNKNOWN
    unit_cost_usd: float | str = UNKNOWN
    basis: str = UNKNOWN
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "provider": self.provider,
            "unit": self.unit,
            "unit_cost_usd": self.unit_cost_usd,
            "basis": self.basis,
            "note": self.note,
        }


class Capability(Protocol):
    """AC-1: every AI capability declares exactly these four."""

    kind: str
    provider_name: str

    def available(self) -> bool: ...

    def requirements(self) -> Requirements: ...

    def estimate(self, **kwargs: Any) -> Estimate: ...

    def run(self, **kwargs: Any) -> dict[str, Any]: ...


class BaseCapability:
    """Shared refusal machinery. Every concrete capability is honest by construction.

    Subclasses set five class attributes and inherit everything else:
    ``kind``, ``provider_name``, ``package`` (the pip package that would
    provide a client), ``credential_env`` (the environment variable an
    operator would set), and ``pricing_reference`` (where to verify a real
    number — never a number itself).

    ``available()`` performs a REAL probe — ``importlib.util.find_spec`` for
    the package, ``os.environ`` for the credential — rather than a hardcoded
    False. On the machine this was built on, every capability here reports
    unavailable because neither half is present for any of them (verified in
    ``tests/test_providers.py`` by inspection, not asserted); the check is
    real, not theatre, so a future operator who installs a package and sets a
    key changes the answer without a code change.

    ``run()`` has three tiers, and all three end in a raise:

      1. Not available (the ordinary case, and the expected one — see the
         T-048 task note: almost every provider here will be unavailable,
         and that is correct, not a defect to paper over). Refuses naming
         exactly what is missing.
      2. Available (package AND credential present) but pricing is
         unverified — which is EVERY capability, always, because this
         module never records a price it has not independently verified.
         A call cannot be safely gated against the C-31 ceiling without a
         real number, so this refuses too, rather than treating an unknown
         cost as free.
      3. A final, unreachable-by-construction refusal for completeness: even
         with a package, a credential AND a verified price, the live call
         itself is not implemented by this task. T-048 built the seam — the
         interface and the spend gate — so a future task can add a live
         adapter behind it without a rewrite, exactly as T-019 added live
         publisher adapters behind the interface T-012 froze.

    There is no line of code below tier 1 that calls a network client, a
    billing endpoint, or stores a payment method — grep confirms it (see the
    T-048 verification report).
    """

    kind: str = ""
    provider_name: str = ""
    package: str = ""
    credential_env: str = ""
    pricing_reference: str = ""
    what_it_would_satisfy: str = ""

    def _package_installed(self) -> bool:
        if not self.package:
            return False
        try:
            return importlib.util.find_spec(self.package) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            # find_spec can raise on a malformed or partially-shadowed name
            # rather than simply returning None; either way, "installed"
            # is not something this probe can affirm, so it is absent.
            return False

    def _credential_present(self) -> bool:
        return bool(self.credential_env) and bool(os.environ.get(self.credential_env))

    def available(self) -> bool:
        """Package AND credential present. Never a hardcoded answer."""
        return self._package_installed() and self._credential_present()

    def requirements(self) -> Requirements:
        missing: list[Requirement] = []
        if not self._package_installed():
            missing.append(
                Requirement(
                    kind="package",
                    name=self.package,
                    detail=(
                        f"pip install {self.package}. The exact current package "
                        f"name, version and client API must be confirmed against "
                        f"{self.provider_name}'s own current documentation before "
                        "installing — not from model memory."
                    ),
                )
            )
        if not self._credential_present():
            missing.append(
                Requirement(
                    kind="api_credential",
                    name=f"{self.credential_env} (environment variable)",
                    detail=f"An account and API key with {self.provider_name}, set as {self.credential_env}.",
                )
            )
        # Always present, deliberately: no capability in this repository has
        # a verified price (see estimate()), so a call can never be safely
        # gated against C-31 yet, regardless of what else is installed.
        missing.append(
            Requirement(
                kind="verified_pricing",
                name="a cited, current price for this provider",
                detail=(
                    f"Current pricing verified against {self.pricing_reference} "
                    "(project.md O-3: never from model memory), recorded here "
                    "with its source, before any call is gated against the "
                    "C-31 ceiling."
                ),
            )
        )
        return Requirements(
            capability=self.kind,
            provider=self.provider_name,
            satisfied=not missing,
            missing=tuple(missing),
            note=self.what_it_would_satisfy,
        )

    def estimate(self, **kwargs: Any) -> Estimate:
        return Estimate(
            capability=self.kind,
            provider=self.provider_name,
            unit=UNKNOWN,
            unit_cost_usd=UNKNOWN,
            basis=UNKNOWN,
            note=(
                f"No verified price exists for {self.provider_name} in this "
                f"repository. Check {self.pricing_reference} and record a "
                "cited figure before relying on this for anything other than "
                "'unknown' (project.md O-3)."
            ),
        )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        if not self.available():
            req = self.requirements()
            raise ProviderUnavailable(
                f"{self.kind} capability '{self.provider_name}' is not available on this machine",
                capability=self.kind,
                provider=self.provider_name,
                reason="missing_requirements",
                missing=[m.to_dict() for m in req.missing],
                would_satisfy=self.what_it_would_satisfy,
            )
        # UNREACHABLE TODAY. available() requires both the package and the
        # credential; on the machine this was built on, neither is present
        # for any capability defined here (tests/test_providers.py verifies
        # this by real probe, not by assertion). Kept for the future
        # operator who installs both: even then, requirements() above always
        # lists verified_pricing as missing, so a call still cannot be
        # gated against C-31 honestly, and this refuses instead of guessing.
        raise ProviderUnavailable(
            f"{self.kind} capability '{self.provider_name}' has a package and "
            "a credential, but no verified price exists to gate a call "
            "against the C-31 ceiling, and the live call itself is not "
            "implemented by this task — T-048 built the interface and the "
            "spend gate; a live adapter is a separate, later task, mirroring "
            "T-019 for the publisher interface",
            capability=self.kind,
            provider=self.provider_name,
            reason="not_implemented",
            would_satisfy=self.what_it_would_satisfy,
        )
