"""Tests for UK action-family conversion ownership (AGENTS.md §1.2).

Covers two previously unowned conversion sites:

  Site 1 (replay_replace_apply.py):
    When a REPLACE op targets a leaf that is absent from the base shape but the
    replacement payload matches the missing leaf exactly, the inline fallback
    path calls _insert_node_v2.  The fix emits
    uk_replay_replace_materialized_as_insert_for_missing_leaf so that the
    REPLACE→INSERT conversion is owned and strict mode can gate on it.

  Site 2 (effect_text_fragment_lowering.py):
    When an effect feed row is labeled "substituted for words" (word-level) but
    the source carries a fully substituted structural node whose kind+label match
    the target leaf, lower_uk_text_fragment_rewrite silently upgrades to
    curr_action="replace".  The fix emits
    uk_effect_word_substitution_escalated_to_structural_replace so the
    escalation is observable and strict mode can inspect it.

AGENTS.md obligations covered:
  §15.1 synthetic unit test
  §15.2 negative test (nearby valid case emits no conversion adjudication)
  §15.3 finding/observation test (witness fields)
  §15.4 strict-mode test (strict_disposition=block)
"""
from __future__ import annotations

from typing import Any, Optional

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.effect_text_fragment_lowering import (
    UKTextFragmentLowering,
    lower_uk_text_fragment_rewrite,
    _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID,
)
from lawvm.uk_legislation.replay_replace_apply import (
    _UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID,
)
from lawvm.uk_legislation.uk_amendment_replay import replay_uk_ops


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


def _replace_op(target: LegalAddress, payload: IRNode, op_id: str = "uk-test-afi-op") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.REPLACE,
        target=target,
        payload=payload,
        source=_source(),
        sequence=1,
    )


def _minimal_effect(*, effect_type: str = "substituted for words") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test-afi-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/5/subsection/3A",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 5(3A)",
        affecting_uri="/id/ukpga/2024/99",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="99",
        affecting_provisions="s. 1",
        affecting_title="Test Amending Act 2024",
    )


# ===========================================================================
# Site 1 — replay_replace_apply.py:  REPLACE materialised as INSERT
# ===========================================================================

def _statute_missing_leaf() -> IRStatute:
    """Section 5 has no subsection 3A in the base shape.

    The parent section exists (so _find_node_by_target for the parent succeeds),
    but subsection 3A is absent.  A REPLACE op that carries a subsection 3A
    payload should fall into the uk_kind_matches branch and materialise as an
    INSERT.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Original subsection 1.",
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Original subsection 2.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _statute_present_leaf() -> IRStatute:
    """Section 5 already has subsection 3A.

    A REPLACE op on this statute should succeed via direct path lookup and
    must NOT emit the action-family conversion adjudication.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3A",
                            text="Original text for 3A.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _replace_op_for_subsection_3a() -> LegalOperation:
    """REPLACE op targeting section:5 / subsection:3A."""
    return _replace_op(
        target=LegalAddress(path=(("section", "5"), ("subsection", "3A"))),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="3A", text="Newly inserted 3A text."),
        op_id="uk-test-afi-replace-3a",
    )


# ---------------------------------------------------------------------------
# Test: REPLACE→INSERT conversion emits named adjudication
# ---------------------------------------------------------------------------

def test_replace_as_insert_emits_adjudication() -> None:
    """When target leaf is absent, REPLACE materialises as INSERT with adjudication."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_missing_leaf()
    op = _replace_op_for_subsection_3a()

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    # New subsection 3A should have been inserted
    section = result.body.children[0]
    labels = [child.label for child in section.children]
    assert "3A" in labels, f"Expected 3A inserted but got {labels!r}"
    inserted_child = next(c for c in section.children if c.label == "3A")
    assert inserted_child.text == "Newly inserted 3A text."

    # Named adjudication must be emitted
    rule_ids = [a.kind for a in adjudications]
    assert _UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID in rule_ids, (
        f"Expected {_UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID!r} in {rule_ids!r}"
    )


# ---------------------------------------------------------------------------
# Test: adjudication carries required metadata
# ---------------------------------------------------------------------------

def test_replace_as_insert_adjudication_has_metadata() -> None:
    """Adjudication from REPLACE→INSERT path carries the standard disposition fields."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_missing_leaf()
    op = _replace_op_for_subsection_3a()

    replay_uk_ops(statute, [op], adjudications_out=adjudications)

    recovery_adj = next(
        (a for a in adjudications
         if a.kind == _UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID),
        None,
    )
    assert recovery_adj is not None, "Action-family recovery adjudication must be emitted"

    detail = recovery_adj.detail
    assert detail["blocking"] is False
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "apply"
    assert detail["family"] == "target_resolution_recovery"
    assert detail["phase"] == "replay"
    target_resolution = detail["target_resolution"]
    assert target_resolution["target_resolution_status"] == "recovered"
    assert target_resolution["source_target"] == "section:5/subsection:3A"
    assert target_resolution["selected_target"] == "section:5/subsection:3A"
    assert target_resolution["scope_confidence"] == "fallback"


# ---------------------------------------------------------------------------
# Test: adjudication includes witness fields
# ---------------------------------------------------------------------------

def test_replace_as_insert_adjudication_has_witness_fields() -> None:
    """Adjudication records leaf_kind, parent_path, payload_kind, payload_label."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_missing_leaf()
    op = _replace_op_for_subsection_3a()

    replay_uk_ops(statute, [op], adjudications_out=adjudications)

    recovery_adj = next(
        (a for a in adjudications
         if a.kind == _UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID),
        None,
    )
    assert recovery_adj is not None
    detail = recovery_adj.detail

    assert "leaf_kind" in detail, "Must record leaf_kind"
    assert "parent_path" in detail, "Must record parent_path"
    assert "payload_kind" in detail, "Must record payload_kind"
    assert "payload_label" in detail, "Must record payload_label"
    assert detail["leaf_kind"] == "subsection"
    assert detail["payload_label"] == "3A"
    assert detail["target_resolution"]["target_candidates"][0]["target"] == "section:5/subsection:3A"


# ---------------------------------------------------------------------------
# Test: strict_disposition=block (strict-mode gate signal)
# ---------------------------------------------------------------------------

def test_replace_as_insert_strict_disposition_is_block() -> None:
    """The adjudication carries strict_disposition=block so strict-mode callers can gate."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_missing_leaf()
    op = _replace_op_for_subsection_3a()

    replay_uk_ops(statute, [op], adjudications_out=adjudications)

    recovery_adj = next(
        (a for a in adjudications
         if a.kind == _UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID),
        None,
    )
    assert recovery_adj is not None
    assert recovery_adj.detail["strict_disposition"] == "block", (
        "Strict-mode callers must see strict_disposition=block to gate on this conversion"
    )
    assert recovery_adj.detail["blocking"] is False, (
        "In quirks mode the conversion must proceed (blocking=False)"
    )


# ---------------------------------------------------------------------------
# Negative test: leaf present — NO conversion adjudication emitted
# ---------------------------------------------------------------------------

def test_replace_on_present_leaf_emits_no_conversion_adjudication() -> None:
    """When the target leaf exists in the tree, no REPLACE→INSERT adjudication fires."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_present_leaf()
    op = _replace_op_for_subsection_3a()

    result = replay_uk_ops(statute, [op], adjudications_out=adjudications)

    # The existing leaf should be replaced
    section = result.body.children[0]
    subsection_3a = next((c for c in section.children if c.label == "3A"), None)
    assert subsection_3a is not None
    assert subsection_3a.text == "Newly inserted 3A text."

    rule_ids = [a.kind for a in adjudications]
    assert _UK_REPLAY_REPLACE_MATERIALIZED_AS_INSERT_FOR_MISSING_LEAF_RULE_ID not in rule_ids, (
        "REPLACE→INSERT conversion adjudication must NOT fire when leaf is present"
    )


# ===========================================================================
# Site 2 — effect_text_fragment_lowering.py: word-to-structural escalation
# ===========================================================================

def _word_level_effect() -> UKEffectRecord:
    return _minimal_effect(effect_type="substituted for words")


def _target_for_subsection_3a() -> LegalAddress:
    return LegalAddress(path=(("section", "5"), ("subsection", "3A")))


def _content_ir_matching_subsection_3a() -> dict[str, Any]:
    """Structural payload whose kind=subsection and label=3A matches the target."""
    return {
        "kind": "subsection",
        "label": "3A",
        "text": "Replacement text for subsection 3A.",
        "children": [],
    }


def _call_lower_uk_text_fragment_rewrite(
    *,
    effect: Optional[UKEffectRecord] = None,
    effect_type: str = "substituted for words",
    curr_action: str = "replace",
    content_ir: Optional[dict[str, Any]] = None,
    is_word_level: bool = True,
    target: Optional[LegalAddress] = None,
    extracted_text: str = "subsection (3A)",
    lowering_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> UKTextFragmentLowering:
    if effect is None:
        effect = _word_level_effect()
    if target is None:
        target = _target_for_subsection_3a()
    return lower_uk_text_fragment_rewrite(
        effect=effect,
        effect_type=effect_type,
        curr_action=curr_action,
        content_ir=content_ir,
        fragment_subs=None,
        op_text_match=None,
        op_text_replacement=None,
        op_text_occurrence=0,
        op_text_end_occurrence=0,
        target=target,
        target_ref="s. 5(3A)",
        targets_str=["s. 5(3A)"],
        is_word_level=is_word_level,
        heading_facet_target=False,
        source_structural_payload_matches_target=False,
        source_carried_table_entry_paragraph_substitution=None,
        table_cell_selector=None,
        selector_rule_id="",
        structural_sibling_insert_detail=None,
        extracted_el=None,
        source_root=None,
        extracted_text=extracted_text,
        lowering_rejections_out=lowering_rejections_out,
    )


# ---------------------------------------------------------------------------
# Test: word-level effect with matching payload escalates and emits observation
# ---------------------------------------------------------------------------

def test_word_substitution_escalation_emits_observation() -> None:
    """When word-level effect carries structural payload matching target, observation is emitted."""
    observations: list[dict[str, Any]] = []
    result = _call_lower_uk_text_fragment_rewrite(
        content_ir=_content_ir_matching_subsection_3a(),
        lowering_rejections_out=observations,
    )

    # curr_action must be "replace" (the escalated action)
    assert result.curr_action == "replace", (
        f"Expected curr_action='replace' after escalation but got {result.curr_action!r}"
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID in rule_ids, (
        f"Expected {_UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID!r} "
        f"in {rule_ids!r}"
    )


# ---------------------------------------------------------------------------
# Test: observation carries required witness fields
# ---------------------------------------------------------------------------

def test_word_substitution_escalation_observation_has_witness_fields() -> None:
    """Observation records source_payload_kind/label and target_leaf_kind/label."""
    observations: list[dict[str, Any]] = []
    _call_lower_uk_text_fragment_rewrite(
        content_ir=_content_ir_matching_subsection_3a(),
        lowering_rejections_out=observations,
    )

    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"

    assert "source_payload_kind" in obs, "Must record source_payload_kind"
    assert "source_payload_label" in obs, "Must record source_payload_label"
    assert "target_leaf_kind" in obs, "Must record target_leaf_kind"
    assert "target_leaf_label" in obs, "Must record target_leaf_label"

    assert obs["source_payload_kind"] == "subsection"
    assert obs["source_payload_label"] == "3A"
    assert obs["target_leaf_kind"] == "subsection"
    assert obs["target_leaf_label"] == "3A"


# ---------------------------------------------------------------------------
# Test: observation carries strict_disposition=block and quirks_disposition=apply
# ---------------------------------------------------------------------------

def test_word_substitution_escalation_observation_has_strict_disposition() -> None:
    """Observation has strict_disposition=block so strict callers can gate on it."""
    observations: list[dict[str, Any]] = []
    _call_lower_uk_text_fragment_rewrite(
        content_ir=_content_ir_matching_subsection_3a(),
        lowering_rejections_out=observations,
    )

    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID),
        None,
    )
    assert obs is not None
    assert obs.get("strict_disposition") == "block", (
        f"Expected strict_disposition=block, got {obs.get('strict_disposition')!r}"
    )
    assert obs.get("quirks_disposition") == "apply", (
        f"Expected quirks_disposition=apply, got {obs.get('quirks_disposition')!r}"
    )
    # blocking=False because the lowering still proceeds in quirks mode
    assert obs.get("blocking") is False, (
        "Observation must be non-blocking (quirks mode allows escalation)"
    )


# ---------------------------------------------------------------------------
# Test: observation carries family=action_family_recovery
# ---------------------------------------------------------------------------

def test_word_substitution_escalation_observation_family() -> None:
    """Observation carries family=action_family_recovery."""
    observations: list[dict[str, Any]] = []
    _call_lower_uk_text_fragment_rewrite(
        content_ir=_content_ir_matching_subsection_3a(),
        lowering_rejections_out=observations,
    )

    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID),
        None,
    )
    assert obs is not None
    assert obs.get("family") == "action_family_recovery", (
        f"Expected family=action_family_recovery, got {obs.get('family')!r}"
    )


# ---------------------------------------------------------------------------
# Test: action is preserved (don't block the lowering — observe it)
# ---------------------------------------------------------------------------

def test_word_substitution_escalation_returns_replace_action() -> None:
    """In quirks mode the escalated curr_action='replace' is returned unchanged."""
    observations: list[dict[str, Any]] = []
    result = _call_lower_uk_text_fragment_rewrite(
        content_ir=_content_ir_matching_subsection_3a(),
        lowering_rejections_out=observations,
    )
    assert result.curr_action == "replace", (
        "Lowering must return curr_action='replace' after escalation (observe, don't block)"
    )
    assert result.content_ir is not None
    assert result.content_ir.get("kind") == "subsection"
    assert result.content_ir.get("label") == "3A"


# ---------------------------------------------------------------------------
# Negative test: kind mismatch — NO escalation observation emitted
# ---------------------------------------------------------------------------

def test_word_substitution_no_escalation_when_kind_mismatch() -> None:
    """When payload kind does not match target leaf kind, no escalation observation fires."""
    observations: list[dict[str, Any]] = []
    content_ir_mismatch = {
        "kind": "section",       # mismatch — target is subsection
        "label": "3A",
        "text": "Mismatch payload.",
        "children": [],
    }
    _call_lower_uk_text_fragment_rewrite(
        content_ir=content_ir_mismatch,
        lowering_rejections_out=observations,
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID not in rule_ids, (
        "Escalation observation must NOT fire when payload kind does not match target leaf kind"
    )


# ---------------------------------------------------------------------------
# Negative test: label mismatch — NO escalation observation emitted
# ---------------------------------------------------------------------------

def test_word_substitution_no_escalation_when_label_mismatch() -> None:
    """When payload label does not match target leaf label, no escalation observation fires."""
    observations: list[dict[str, Any]] = []
    content_ir_label_mismatch = {
        "kind": "subsection",
        "label": "4",             # mismatch — target is 3A
        "text": "Mismatch label payload.",
        "children": [],
    }
    _call_lower_uk_text_fragment_rewrite(
        content_ir=content_ir_label_mismatch,
        lowering_rejections_out=observations,
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID not in rule_ids, (
        "Escalation observation must NOT fire when payload label does not match target leaf label"
    )


# ---------------------------------------------------------------------------
# Negative test: not word-level — NO escalation observation emitted
# ---------------------------------------------------------------------------

def test_word_substitution_no_escalation_when_not_word_level() -> None:
    """When is_word_level=False the escalation branch is never reached."""
    observations: list[dict[str, Any]] = []
    _call_lower_uk_text_fragment_rewrite(
        content_ir=_content_ir_matching_subsection_3a(),
        is_word_level=False,
        lowering_rejections_out=observations,
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID not in rule_ids, (
        "Escalation observation must NOT fire when is_word_level=False"
    )


# ---------------------------------------------------------------------------
# Negative test: effect_type not "substituted for words" — NO observation
# ---------------------------------------------------------------------------

def test_word_substitution_no_escalation_for_other_effect_types() -> None:
    """Only 'substituted for words' triggers the escalation; other effect types do not."""
    observations: list[dict[str, Any]] = []
    effect = _minimal_effect(effect_type="words inserted")
    _call_lower_uk_text_fragment_rewrite(
        effect=effect,
        effect_type="words inserted",
        content_ir=_content_ir_matching_subsection_3a(),
        lowering_rejections_out=observations,
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_WORD_SUBSTITUTION_ESCALATED_TO_STRUCTURAL_REPLACE_RULE_ID not in rule_ids, (
        "Escalation observation must NOT fire for effect_type other than 'substituted for words'"
    )
