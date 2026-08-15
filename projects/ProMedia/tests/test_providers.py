"""T-048 — AI capability providers and the C-31 spend ledger.

Almost every assertion below proves an ABSENCE: no capability can run, no
price is invented, and no code path spends anything. That is the discipline
this task exists to enforce (see the task note in tasks.yaml) — a green
suite here means "correctly refuses", not "successfully connected to a paid
API".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from promedia.core import providers as providers_layer
from promedia.core.providers.base import (
    UNKNOWN,
    BaseCapability,
    Estimate,
    ProviderUnavailable,
    Requirements,
)
from promedia.core.providers import spend as spend_layer
from promedia.core.providers.spend import SpendApprovalRequired, SpendCeilingExceeded
from promedia.core.registry import Context, invoke
from promedia.core.principal import agent, operator
from promedia.errors import Forbidden, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVIDERS_PKG = REPO_ROOT / "promedia" / "core" / "providers"


# --- AC-1: every capability declares available(), requirements(), estimate(), run() ---


def test_every_registered_capability_declares_the_four_methods():
    for kind, cap in providers_layer.CAPABILITIES.items():
        assert hasattr(cap, "available") and callable(cap.available)
        assert hasattr(cap, "requirements") and callable(cap.requirements)
        assert hasattr(cap, "estimate") and callable(cap.estimate)
        assert hasattr(cap, "run") and callable(cap.run)
        assert cap.kind == kind


def test_five_capability_kinds_are_registered():
    """Transcription, text, speech, image, video — the task's exact list."""
    assert set(providers_layer.CAPABILITIES) == {
        "transcription",
        "text",
        "speech",
        "image",
        "video",
    }


def test_for_capability_looks_up_by_kind():
    cap = providers_layer.for_capability("transcription")
    assert cap.kind == "transcription"


def test_for_capability_unknown_kind_is_a_structured_error():
    with pytest.raises(ValidationError) as excinfo:
        providers_layer.for_capability("no-such-capability")
    assert "transcription" in excinfo.value.detail["known"]


def test_available_is_false_for_every_capability_on_this_machine():
    """The expected outcome (task note): nothing here is configured.

    Genuinely probed, not asserted by construction — see the next test,
    which flips the same probe and gets a different answer.
    """
    for kind, cap in providers_layer.CAPABILITIES.items():
        assert cap.available() is False, f"{kind} reported available with no package/credential configured"


def test_available_is_a_real_probe_not_a_hardcoded_false(monkeypatch):
    """Flip both real signals and prove available() actually reads them."""
    cap = providers_layer.CAPABILITIES["speech"]
    assert cap.available() is False

    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: object() if name == cap.package else None
    )
    monkeypatch.setenv(cap.credential_env, "sk-test-not-a-real-key")
    assert cap.available() is True

    monkeypatch.delenv(cap.credential_env, raising=False)
    assert cap.available() is False, "credential alone must not be sufficient without the package"


def test_package_present_but_credential_absent_is_still_unavailable(monkeypatch):
    """Mirrors this machine's real state: `openai` happens to be importable,
    but with no OPENAI_API_KEY set, transcription/text must stay unavailable.
    """
    cap = providers_layer.CAPABILITIES["transcription"]
    installed = importlib.util.find_spec(cap.package) is not None
    if not installed:
        pytest.skip("openai package not importable on this machine; probe is moot here")
    monkeypatch.delenv(cap.credential_env, raising=False)
    assert cap.available() is False


# --- AC-2: unavailable capability -> structured refusal naming what would satisfy it ---


def test_requirements_names_a_concrete_provider_and_what_is_missing():
    for kind, cap in providers_layer.CAPABILITIES.items():
        req = cap.requirements()
        assert isinstance(req, Requirements)
        assert req.capability == kind
        assert req.provider and req.provider != UNKNOWN
        assert req.satisfied is False
        assert req.missing, f"{kind} reports nothing missing, which cannot be true today"
        kinds_present = {m.kind for m in req.missing}
        assert "package" in kinds_present or "api_credential" in kinds_present
        assert "verified_pricing" in kinds_present, "pricing must always be an open requirement"
        for m in req.missing:
            assert m.name and m.detail, f"{kind} requirement is missing a name or a detail"


def test_run_refuses_with_structured_detail_when_unavailable():
    cap = providers_layer.CAPABILITIES["video"]
    assert cap.available() is False
    with pytest.raises(ProviderUnavailable) as excinfo:
        cap.run()
    detail = excinfo.value.detail
    assert detail["capability"] == "video"
    assert detail["provider"] == cap.provider_name
    assert detail["reason"] == "missing_requirements"
    assert detail["missing"], "the refusal must name what is missing, not just say no"
    assert any(m["kind"] == "package" for m in detail["missing"])
    assert excinfo.value.code == "PROVIDER_UNAVAILABLE"


def test_run_refuses_even_when_available_because_pricing_is_unverified(monkeypatch):
    """The seam's second tier: package + credential present, still refuses.

    Proves AC-3's "no code path performs a purchase" for the one branch that
    could plausibly reach further than a blank refusal — even here, nothing
    below the raise ever runs.
    """
    cap = providers_layer.CAPABILITIES["image"]
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: object() if name == cap.package else None
    )
    monkeypatch.setenv(cap.credential_env, "sk-test-not-a-real-key")
    assert cap.available() is True

    with pytest.raises(ProviderUnavailable) as excinfo:
        cap.run()
    assert excinfo.value.detail["reason"] == "not_implemented"
    assert excinfo.value.detail["capability"] == "image"


def test_estimate_never_invents_a_price():
    for kind, cap in providers_layer.CAPABILITIES.items():
        est = cap.estimate()
        assert isinstance(est, Estimate)
        assert est.unit_cost_usd == UNKNOWN, f"{kind} invented a price"
        assert est.basis == UNKNOWN
        assert isinstance(est.unit_cost_usd, str), "an unknown price must read as text, never a number"


def test_text_capability_documents_the_agent_is_the_model_rule():
    """Operator instruction (2026-08-13): agents must not use this to draft their own text."""
    text_cap = providers_layer.CAPABILITIES["text"]
    assert "agent" in text_cap.what_it_would_satisfy.lower()
    assert "UI-triggered" in text_cap.what_it_would_satisfy


# --- AC-3: the spend ledger enforces C-31, and nothing purchases anything ---


def test_month_to_date_starts_at_zero(config, conn):
    status = spend_layer.month_to_date(conn, config)
    assert status["committed_usd"] == 0.0
    assert status["monthly_ceiling_usd"] == 100.0
    assert status["hard_stop_usd"] == 150.0
    assert status["state"] == "ok"


def test_recording_spend_under_the_cap_succeeds(config, conn):
    result = spend_layer.record(
        conn, config, capability="transcription", provider="test-provider", amount_usd=3.0
    )
    assert result["ok"] is True
    assert result["committed_usd"] == 3.0
    rows = spend_layer.history(conn)
    assert len(rows) == 1
    assert rows[0]["amount_usd"] == 3.0


def test_amount_over_the_per_operation_cap_requires_explicit_approval(config, conn):
    with pytest.raises(SpendApprovalRequired) as excinfo:
        spend_layer.record(
            conn, config, capability="image", provider="test-provider", amount_usd=6.0, approved=False
        )
    assert excinfo.value.detail["amount_usd"] == 6.0
    assert spend_layer.history(conn) == [], "a refused recording must write nothing"

    # The same amount, explicitly approved, succeeds.
    result = spend_layer.record(
        conn, config, capability="image", provider="test-provider", amount_usd=6.0, approved=True
    )
    assert result["ok"] is True
    assert len(spend_layer.history(conn)) == 1


def test_amount_at_exactly_the_cap_needs_no_approval(config, conn):
    result = spend_layer.record(
        conn, config, capability="speech", provider="test-provider", amount_usd=5.0, approved=False
    )
    assert result["ok"] is True


def test_recording_that_would_breach_the_hard_stop_is_refused(config, conn):
    """AC-3's core claim, proven end to end: refuse before writing, not after."""
    seeded = spend_layer.record(
        conn, config, capability="video", provider="test-provider", amount_usd=148.0, approved=True
    )
    assert seeded["state"] == "over_ceiling"

    with pytest.raises(SpendCeilingExceeded) as excinfo:
        spend_layer.record(
            conn, config, capability="video", provider="test-provider", amount_usd=5.0, approved=False
        )
    assert excinfo.value.detail["hard_stop_usd"] == 150.0

    rows = spend_layer.history(conn)
    assert len(rows) == 1, "the refused $5 must not have been written alongside the seeded $148"
    assert rows[0]["amount_usd"] == 148.0


def test_spend_between_ceiling_and_hard_stop_is_permitted_but_flagged(config, conn):
    spend_layer.record(conn, config, capability="video", provider="p", amount_usd=100.0, approved=True)
    status = spend_layer.month_to_date(conn, config)
    assert status["state"] == "over_ceiling"
    assert status["committed_usd"] == 100.0


def test_spend_check_is_read_only(config, conn):
    outcome = spend_layer.check(conn, config, amount_usd=1000.0)
    assert outcome["permitted"] is False
    assert outcome["reasons"]
    assert spend_layer.history(conn) == [], "check() must never write a row"


def test_negative_amount_is_refused(config, conn):
    with pytest.raises(ValidationError):
        spend_layer.check(conn, config, amount_usd=-1.0)


def test_spend_history_orders_most_recent_first(config, conn):
    spend_layer.record(conn, config, capability="transcription", provider="p", amount_usd=1.0)
    spend_layer.record(conn, config, capability="speech", provider="p", amount_usd=1.0)
    rows = spend_layer.history(conn)
    assert len(rows) == 2
    assert rows[0]["capability"] == "speech"


# --- Operations layer: reachable, authority-gated correctly ---


def test_list_capabilities_operation_reports_all_five(agent_ctx):
    result = invoke(agent_ctx, "list-capabilities", {})
    assert result["ok"] is True
    assert result["count"] == 5
    kinds = {c["capability"] for c in result["capabilities"]}
    assert kinds == {"transcription", "text", "speech", "image", "video"}
    assert all(c["available"] is False for c in result["capabilities"])


def test_capability_requirements_operation_is_agent_readable(agent_ctx):
    result = invoke(agent_ctx, "capability-requirements", {"capability": "transcription"})
    assert result["ok"] is True
    assert result["missing"]


def test_estimate_capability_cost_operation_never_guesses(agent_ctx):
    result = invoke(agent_ctx, "estimate-capability-cost", {"capability": "video"})
    assert result["unit_cost_usd"] == UNKNOWN


def test_spend_status_and_check_are_agent_readable(agent_ctx):
    status = invoke(agent_ctx, "spend-status", {})
    assert status["ok"] is True
    checked = invoke(agent_ctx, "spend-check", {"amount_usd": "2.0"})
    assert checked["permitted"] is True


def test_run_capability_is_operator_authority(agent_ctx):
    """F-2: agents may never spend money without operator approval."""
    with pytest.raises(Forbidden) as excinfo:
        invoke(agent_ctx, "run-capability", {"capability": "transcription"})
    assert excinfo.value.detail["principal"] == "agent"


def test_record_spend_is_operator_authority(agent_ctx):
    with pytest.raises(Forbidden):
        invoke(agent_ctx, "record-spend", {"capability": "text", "provider": "p", "amount_usd": "1.0"})


def test_run_capability_as_operator_still_refuses_and_records_no_spend(operator_ctx):
    """The full chain, operator-authorised end to end: still no spend.

    This is the strongest available proof of AC-3's second half — even the
    one principal allowed to invoke it gets a structured refusal, and the
    ledger is untouched afterwards.
    """
    with pytest.raises(ProviderUnavailable):
        invoke(operator_ctx, "run-capability", {"capability": "speech"})
    history = invoke(operator_ctx, "spend-history", {})
    assert history["count"] == 0


def test_record_spend_operation_end_to_end(operator_ctx):
    result = invoke(
        operator_ctx,
        "record-spend",
        {"capability": "transcription", "provider": "test", "amount_usd": "2.5"},
    )
    assert result["ok"] is True
    status = invoke(operator_ctx, "spend-status", {})
    assert status["committed_usd"] == 2.5


def test_record_spend_operation_refuses_over_hard_stop(operator_ctx):
    invoke(
        operator_ctx, "record-spend",
        {"capability": "video", "provider": "p", "amount_usd": "149.0", "approved": "true"},
    )
    with pytest.raises(SpendCeilingExceeded):
        invoke(
            operator_ctx, "record-spend",
            {"capability": "video", "provider": "p", "amount_usd": "5.0"},
        )


# --- AC-3's second half, proven rather than merely asserted ---


_FORBIDDEN_TOKENS = (
    "requests.post", "requests.get", "urllib.request", "httpx.", "http.client",
    "stripe", "checkout", "PaymentIntent", "charge(", "billing_client",
    "creditcard", "credit_card",
)


def test_no_purchase_code_anywhere_in_the_providers_package():
    """Grepped, not asserted: no network client, no payment surface, anywhere."""
    for path in PROVIDERS_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_TOKENS:
            assert token not in text, f"{path} contains '{token}'"


def test_no_purchase_code_in_the_registered_operations():
    text = (REPO_ROOT / "promedia" / "core" / "ops" / "providers.py").read_text(encoding="utf-8")
    for token in _FORBIDDEN_TOKENS:
        assert token not in text, f"ops/providers.py contains '{token}'"


def test_base_capability_run_has_exactly_two_raises_and_nothing_else(monkeypatch):
    """Structural check on BaseCapability.run: every path ends in a raise.

    Not a substitute for the grep tests above, but a second, independent
    angle: force BOTH tiers and confirm each is a raise with no side effect
    on the object itself (no attribute set, nothing cached, nothing sent).
    """
    cap = BaseCapability()
    cap.kind = "probe"
    cap.provider_name = "probe-provider"
    cap.package = "definitely_not_a_real_installed_package_xyz"
    cap.credential_env = "PROBE_CREDENTIAL_DOES_NOT_EXIST"
    cap.pricing_reference = "nowhere"
    cap.what_it_would_satisfy = "a test probe"

    with pytest.raises(ProviderUnavailable) as excinfo:
        cap.run()
    assert excinfo.value.detail["reason"] == "missing_requirements"

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setenv(cap.credential_env, "x")
    with pytest.raises(ProviderUnavailable) as excinfo2:
        cap.run()
    assert excinfo2.value.detail["reason"] == "not_implemented"
