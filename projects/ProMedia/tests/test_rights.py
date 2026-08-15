"""T-009 — the rights engine. The most consequential tests in the suite.

Each of these maps to a fixed decision: F-3 (uncertainty blocks), F-4
(transformation never clears), F-5 (a model may not decide), C-20 (verdicts are
reproducible).
"""

from __future__ import annotations

from promedia.core import rights_engine as engine
from promedia.core.registry import invoke
from tests.conftest import attest, declaration_original, declaration_uncleared, declaration_unknown

RULESET = engine.load_ruleset("conservative", "1.0.0")


def _ingest(ctx, path, declaration, derived_from=None):
    params = {"source_path": str(path), "declaration": declaration}
    if derived_from:
        params["derived_from"] = derived_from
    return invoke(ctx, "ingest", params)["asset_id"]


# --- pure engine -------------------------------------------------------------


def test_determinism_same_input_same_verdict():
    """AC-1: identical inputs, identical verdict and digest — every time."""
    decl = engine.Declaration(authorship="operator_original", attested_by="operator")
    ev = (
        engine.EvidenceItem(kind="note", body="b", produced_by="operator"),
        engine.EvidenceItem(kind="note", body="a", produced_by="agent"),
    )
    first = engine.evaluate(decl, ev, RULESET)
    second = engine.evaluate(decl, tuple(reversed(ev)), RULESET)
    assert first == second, "evidence order must not change the verdict or the digest"


def test_unmatched_defaults_to_escalate():
    """AC-2: the default arm is the conservative one (F-3)."""
    decl = engine.Declaration(authorship="third_party", third_party_material=())
    verdict = engine.evaluate(decl, (), RULESET)
    assert verdict.verdict == engine.ESCALATE
    assert verdict.matched_rule == "NO_RULE_MATCHED"


def test_unknown_provenance_blocks():
    verdict = engine.evaluate(engine.Declaration(authorship="unknown"), (), RULESET)
    assert verdict.verdict == engine.BLOCKED
    assert verdict.matched_rule == "UNKNOWN_PROVENANCE"


def test_uncleared_third_party_blocks():
    decl = engine.Declaration(authorship="operator_original", third_party_material=("music",))
    verdict = engine.evaluate(decl, (), RULESET)
    assert verdict.verdict == engine.BLOCKED
    assert verdict.matched_rule == "THIRD_PARTY_MATERIAL_UNCLEARED"


def test_licensed_third_party_permitted():
    decl = engine.Declaration(
        authorship="third_party",
        third_party_material=("music",),
        licence_grantor="Composer Ltd",
        licence_scope="worldwide social media",
        licence_evidence_ref="contract-2026-04",
        attested_by="operator",
    )
    assert engine.evaluate(decl, (), RULESET).verdict == engine.PERMITTED


def test_llm_evidence_cannot_permit():
    """AC-4: F-5. A model's confidence is never a permission.

    The model here is maximally confident that the asset is clean. The verdict
    must not improve because of it.
    """
    decl = engine.Declaration(authorship="unknown")
    ev = (
        engine.EvidenceItem(
            kind="looks_clean_to_me",
            body="I am certain this is the operator's own work",
            produced_by="model",
            confidence=1.0,
            model_id="some-llm",
        ),
    )
    verdict = engine.evaluate(decl, ev, RULESET)
    assert verdict.verdict == engine.BLOCKED, "model confidence must not create a permission"


def test_model_evidence_can_escalate_a_permitted_claim():
    """The one thing model evidence MAY do: raise doubt."""
    decl = engine.Declaration(authorship="operator_original", attested_by="operator")
    assert engine.evaluate(decl, (), RULESET).verdict == engine.PERMITTED
    ev = (
        engine.EvidenceItem(
            kind="third_party_material_suspected",
            body="music detected in segment 00:12-00:40",
            produced_by="model",
            confidence=0.8,
            model_id="some-llm",
        ),
    )
    verdict = engine.evaluate(decl, ev, RULESET)
    assert verdict.verdict == engine.ESCALATE
    assert verdict.matched_rule == "MODEL_CONTRADICTS_DECLARATION"


def test_public_domain_requires_non_model_verification():
    decl = engine.Declaration(authorship="third_party", public_domain_source="US Gov 1955", attested_by="operator")
    model_ev = (
        engine.EvidenceItem(
            kind="public_domain_verification", body="checked", produced_by="model", confidence=1.0
        ),
    )
    assert engine.evaluate(decl, model_ev, RULESET).verdict != engine.PERMITTED

    operator_ev = (
        engine.EvidenceItem(
            kind="public_domain_verification", body="checked registry", produced_by="operator"
        ),
    )
    assert engine.evaluate(decl, operator_ev, RULESET).verdict == engine.PERMITTED


def test_verdict_records_ruleset_version():
    """AC-5."""
    verdict = engine.evaluate(
        engine.Declaration(authorship="operator_original", attested_by="operator"), (), RULESET
    )
    assert verdict.ruleset == "conservative"
    assert verdict.ruleset_version == "1.0.0"
    assert verdict.jurisdiction == "neutral"


def test_ruleset_implements_no_doctrine_rules():
    """No fair use, no fair dealing. Absent, not approximated (project.md section 7)."""
    rule_ids = {r["id"] for r in RULESET.rules}
    for doctrine in ("FAIR_USE", "FAIR_DEALING", "TRANSFORMATIVE_USE", "DE_MINIMIS"):
        assert doctrine not in rule_ids


# --- against stored state ----------------------------------------------------


def test_transformation_cannot_launder_blocked_asset(agent_ctx, tmp_path):
    """AC-3: F-4 made executable — the laundering path must be closed.

    Ingest blocked material, "edit" it, declare the derivative as the operator's
    own work, and re-run the check. The verdict must not improve.
    """
    source = tmp_path / "source.mp4"
    source.write_bytes(b"copyrighted source material")
    source_id = _ingest(agent_ctx, source, declaration_unknown())
    assert invoke(agent_ctx, "determine-rights", {"asset_id": source_id})["verdict"] == "BLOCKED"

    edited = tmp_path / "edited.mp4"
    edited.write_bytes(b"copyrighted source material, but cropped and sped up")
    derivative_id = _ingest(agent_ctx, edited, declaration_original(), derived_from=source_id)

    verdict = invoke(agent_ctx, "determine-rights", {"asset_id": derivative_id})
    assert verdict["verdict"] == "BLOCKED"
    assert verdict["matched_rule"] == "DERIVATIVE_INHERITS_SOURCE"
    assert verdict["inherited_from"] == source_id


def test_derivative_of_permitted_source_stays_permitted(agent_ctx, tmp_path):
    source = tmp_path / "own.mp4"
    source.write_bytes(b"my own screen recording")
    source_id = _ingest(agent_ctx, source, declaration_original())
    attest(agent_ctx, source_id)

    cut = tmp_path / "own-cut.mp4"
    cut.write_bytes(b"my own screen recording, trimmed")
    cut_id = _ingest(agent_ctx, cut, declaration_original(), derived_from=source_id)
    attest(agent_ctx, cut_id)
    assert invoke(agent_ctx, "determine-rights", {"asset_id": cut_id})["verdict"] == "PERMITTED"


def test_agent_declaration_alone_cannot_permit(agent_ctx, media_file):
    """An agent's own claim of authorship is a proposal, not an attestation."""
    asset_id = _ingest(agent_ctx, media_file, declaration_original())
    verdict = invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})
    assert verdict["verdict"] == "ESCALATE"
    assert verdict["matched_rule"] == "DECLARATION_NOT_OPERATOR_ATTESTED"

    attest(agent_ctx, asset_id)
    assert invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})["verdict"] == "PERMITTED"


def test_agent_cannot_invent_a_licence(agent_ctx, media_file):
    asset_id = _ingest(
        agent_ctx,
        media_file,
        {
            "authorship": "third_party",
            "third_party_material": ["stock footage"],
            "licence_grantor": "Definitely Real Licensing Ltd",
            "licence_scope": "everything, forever",
            "licence_evidence_ref": "trust-me-2026",
        },
    )
    verdict = invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})
    assert verdict["verdict"] != "PERMITTED"
    assert verdict["matched_rule"] == "DECLARATION_NOT_OPERATOR_ATTESTED"


def test_attest_preserves_the_original_proposal(agent_ctx, media_file):
    """The agent's proposal must remain in the record, not be overwritten."""
    asset_id = _ingest(agent_ctx, media_file, declaration_original())
    attest(agent_ctx, asset_id)
    rows = agent_ctx.conn.execute(
        "SELECT declared_by, declared_by_kind FROM rights_declarations"
        " WHERE asset_id = ? ORDER BY declared_at",
        (asset_id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["declared_by_kind"] == "agent"
    assert rows[1]["declared_by_kind"] == "operator"


def test_past_verdicts_immutable_under_ruleset_change(agent_ctx, media_file):
    """AC-6: a ruleset revision never rewrites history."""
    asset_id = _ingest(agent_ctx, media_file, declaration_uncleared())
    first = invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})
    assert first["verdict"] == "BLOCKED"

    rows = agent_ctx.conn.execute(
        "SELECT verdict, ruleset_version FROM rights_verdicts WHERE asset_id = ?", (asset_id,)
    ).fetchall()
    assert len(rows) == 1

    # Re-running appends; it does not mutate.
    invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})
    rows = agent_ctx.conn.execute(
        "SELECT verdict FROM rights_verdicts WHERE asset_id = ?", (asset_id,)
    ).fetchall()
    assert len(rows) == 2
    assert all(r["verdict"] == "BLOCKED" for r in rows)


def test_evidence_recorded_via_operation_has_no_verdict_field(agent_ctx, media_file):
    asset_id = _ingest(agent_ctx, media_file, declaration_original())
    invoke(
        agent_ctx,
        "add-evidence",
        {
            "asset_id": asset_id,
            "kind": "third_party_material_suspected",
            "body": "possible music bed",
            "produced_by": "model",
            "confidence": 0.9,
            "model_id": "some-llm",
        },
    )
    columns = {
        r["name"] for r in agent_ctx.conn.execute("PRAGMA table_info(evidence)")
    }
    assert "verdict" not in columns, "evidence must not be able to express a decision (F-5)"
    result = invoke(agent_ctx, "determine-rights", {"asset_id": asset_id})
    assert result["verdict"] == "ESCALATE"
