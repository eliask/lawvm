"""Tests for C2: Admissible Binding Certificate.

Verifies that subsection slot assignments produce correct admissibility
classifications: single (deterministic), ambiguous (multiple candidates),
or fallback (positional assignment).
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.compile_result import AdmissibleBindingCoverage
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.observation_registry import (
    FINDING_REGISTRY,
    finding_codes_by_role,
    get_finding_spec,
)
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.payload_normalize import (
    SubsectionSlotInputs,
    _assign_subsection_slots,
)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

def test_ambiguous_binding_observation_registered() -> None:
    """ELAB.AMBIGUOUS_BINDING must exist in the observation-role registry."""
    observation_codes = set(finding_codes_by_role("observation"))
    assert "ELAB.AMBIGUOUS_BINDING" in observation_codes
    spec = FINDING_REGISTRY["ELAB.AMBIGUOUS_BINDING"]
    assert spec.phase == "sparse_subsection_elaboration"


def test_ambiguous_binding_finding_registered() -> None:
    """ELAB.AMBIGUOUS_BINDING must exist in FINDING_REGISTRY with correct metadata."""
    assert "ELAB.AMBIGUOUS_BINDING" in FINDING_REGISTRY
    spec = FINDING_REGISTRY["ELAB.AMBIGUOUS_BINDING"]
    assert spec.family == "ambiguity"
    assert spec.default_enforcement == "strict_fail"
    assert spec.owner == "payload_normalize"
    assert "ambiguity_resolution" in spec.proof_categories


def test_ambiguous_binding_finding_lookup() -> None:
    """get_finding_spec should resolve ELAB.AMBIGUOUS_BINDING."""
    spec = get_finding_spec("ELAB.AMBIGUOUS_BINDING")
    assert spec is not None
    assert spec.code == "ELAB.AMBIGUOUS_BINDING"


def test_positional_fallback_binding_finding_registered() -> None:
    """The calm split-off ELAB.POSITIONAL_FALLBACK_BINDING must be registered.

    Fallback is the safe common case (label mismatch → order-only mapping); it
    is registered as a non-blocking ``warn`` observation so that
    AMBIGUOUS_BINDING stays reserved for genuine label-ties.
    """
    spec = get_finding_spec("ELAB.POSITIONAL_FALLBACK_BINDING")
    assert spec is not None
    assert spec.code == "ELAB.POSITIONAL_FALLBACK_BINDING"
    assert spec.phase == "sparse_subsection_elaboration"
    assert spec.owner == "payload_normalize"
    assert spec.default_enforcement == "warn"
    assert "ELAB.POSITIONAL_FALLBACK_BINDING" in set(finding_codes_by_role("observation"))


def test_positional_fallback_order_mismatch_finding_registered() -> None:
    """The defect-grade ELAB.POSITIONAL_FALLBACK_ORDER_MISMATCH must be registered."""
    spec = get_finding_spec("ELAB.POSITIONAL_FALLBACK_ORDER_MISMATCH")
    assert spec is not None
    assert spec.code == "ELAB.POSITIONAL_FALLBACK_ORDER_MISMATCH"
    assert spec.phase == "sparse_subsection_elaboration"
    assert spec.owner == "payload_normalize"
    assert spec.default_enforcement == "strict_fail"


# ---------------------------------------------------------------------------
# AdmissibleBindingCoverage type tests
# ---------------------------------------------------------------------------

def test_admissible_binding_coverage_single() -> None:
    cert = AdmissibleBindingCoverage(
        slot_id=1,
        amendment_id="2024/100",
        candidate_count=1,
        admissibility="single",
    )
    assert cert.admissibility == "single"
    assert cert.candidate_count == 1


def test_admissible_binding_coverage_ambiguous() -> None:
    cert = AdmissibleBindingCoverage(
        slot_id=2,
        amendment_id="2024/200",
        candidate_count=3,
        admissibility="ambiguous",
    )
    assert cert.admissibility == "ambiguous"
    assert cert.candidate_count == 3


def test_admissible_binding_coverage_fallback() -> None:
    cert = AdmissibleBindingCoverage(
        slot_id=1,
        amendment_id="2024/300",
        candidate_count=5,
        admissibility="fallback",
    )
    assert cert.admissibility == "fallback"


def test_admissible_binding_coverage_frozen() -> None:
    cert = AdmissibleBindingCoverage(
        slot_id=1,
        amendment_id="2024/100",
        candidate_count=1,
        admissibility="single",
    )
    with pytest.raises(AttributeError):
        cast(Any, cert).slot_id = 99


# ---------------------------------------------------------------------------
# Integration: slot assignment produces binding certificates
# ---------------------------------------------------------------------------

def _make_op(
    target_paragraph: int,
    op_type: OpType = OpType.REPLACE,
    source_statute: str = "2024/100",
    target_item: str | None = None,
    target_special: str | None = None,
) -> AmendmentOp:
    """Create a minimal AmendmentOp for slot-assignment tests."""
    return AmendmentOp(
        op_type=op_type,
        target_section="1",
        target_unit_kind="section",
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_special=target_special,
        source_statute=source_statute,
    )


def _make_subsection(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label)


def test_single_candidate_slot_gets_single_admissibility() -> None:
    """One op targeting paragraph 1, one payload slot labeled '1' -> single."""
    op = _make_op(target_paragraph=1)
    subs = [_make_subsection("1")]
    inputs = SubsectionSlotInputs(
        amend_subs=tuple(subs),
        payload_subsec_ops=(op,),
        intro_subsec_ops=(),
        renumber_subsec_ops=(),
        duplicate_targets=(),
        has_omission_slots=False,
    )
    result = _assign_subsection_slots(inputs)
    assert len(result.binding_certificates) == 1
    cert = result.binding_certificates[0]
    assert cert.admissibility == "single"
    assert cert.candidate_count == 1
    assert cert.amendment_id == "2024/100"


def test_multiple_same_label_slots_gets_ambiguous() -> None:
    """Two payload slots with same label '1' -> ambiguous."""
    op = _make_op(target_paragraph=1)
    subs = [_make_subsection("1"), _make_subsection("1")]
    inputs = SubsectionSlotInputs(
        amend_subs=tuple(subs),
        payload_subsec_ops=(op,),
        intro_subsec_ops=(),
        renumber_subsec_ops=(),
        duplicate_targets=(),
        has_omission_slots=False,
    )
    result = _assign_subsection_slots(inputs)
    assert len(result.binding_certificates) >= 1
    cert = result.binding_certificates[0]
    assert cert.admissibility == "ambiguous"
    assert cert.candidate_count == 2


def test_fallback_binding_when_labels_dont_match() -> None:
    """Two visible numeric slots with one later target still produce fallback."""
    op = _make_op(target_paragraph=3)
    subs = [_make_subsection("1"), _make_subsection("2")]
    inputs = SubsectionSlotInputs(
        amend_subs=tuple(subs),
        payload_subsec_ops=(op,),
        intro_subsec_ops=(),
        renumber_subsec_ops=(),
        duplicate_targets=(),
        has_omission_slots=False,
    )
    result = _assign_subsection_slots(inputs)
    assert len(result.binding_certificates) >= 1
    cert = result.binding_certificates[0]
    assert cert.admissibility == "fallback"


def test_mixed_single_and_fallback() -> None:
    """Two ops: one exact match, one positional fallback."""
    op1 = _make_op(target_paragraph=1, source_statute="2024/100")
    op2 = _make_op(target_paragraph=5, source_statute="2024/200")
    subs = [_make_subsection("1"), _make_subsection("2")]
    inputs = SubsectionSlotInputs(
        amend_subs=tuple(subs),
        payload_subsec_ops=(op1, op2),
        intro_subsec_ops=(),
        renumber_subsec_ops=(),
        duplicate_targets=(),
        has_omission_slots=False,
    )
    result = _assign_subsection_slots(inputs)
    assert len(result.binding_certificates) == 2
    certs_by_slot = {c.slot_id: c for c in result.binding_certificates}
    # Op1 targets paragraph 1, slot labeled "1" -> single
    cert1 = certs_by_slot.get(1)
    assert cert1 is not None
    assert cert1.admissibility == "single"
    # Op2 targets paragraph 5, only slot "2" left -> fallback
    cert2 = certs_by_slot.get(2)
    assert cert2 is not None
    assert cert2.admissibility == "fallback"


def test_no_ops_produces_empty_certificates() -> None:
    """No ops to assign -> empty binding_certificates."""
    inputs = SubsectionSlotInputs(
        amend_subs=(_make_subsection("1"),),
        payload_subsec_ops=(),
        intro_subsec_ops=(),
        renumber_subsec_ops=(),
        duplicate_targets=(),
        has_omission_slots=False,
    )
    result = _assign_subsection_slots(inputs)
    assert result.binding_certificates == ()


def test_fallback_order_mismatch_none_when_slots_in_declared_order() -> None:
    """Fallback bindings whose slots rise with declared target order are safe."""
    from lawvm.finland.payload_normalize import _detect_fallback_order_mismatches

    # Declared order (paragraph asc): 3 -> slot 1, 5 -> slot 2.  Monotone: safe.
    fallback = [
        (3, None, None, 1, "2024/100"),
        (5, None, None, 2, "2024/100"),
    ]
    assert _detect_fallback_order_mismatches(fallback) == ()


def test_fallback_order_mismatch_detected_when_slots_inverted() -> None:
    """A later-declared target consuming an earlier slot is a mis-bind."""
    from lawvm.finland.payload_normalize import _detect_fallback_order_mismatches

    # Declared order (paragraph asc): 3 -> slot 2, 5 -> slot 1.  Slot 1 < slot 2
    # for the later-declared moment 5 => inversion.
    fallback = [
        (3, None, None, 2, "2024/100"),
        (5, None, None, 1, "2024/100"),
    ]
    mismatches = _detect_fallback_order_mismatches(fallback)
    assert len(mismatches) == 1
    mismatch = mismatches[0]
    assert mismatch.target_paragraph == 5
    assert mismatch.payload_slot_index == 1
    assert mismatch.prev_target_paragraph == 3
    assert mismatch.prev_payload_slot_index == 2
    assert mismatch.amendment_id == "2024/100"


def test_fallback_order_mismatch_empty_for_single_binding() -> None:
    """A lone fallback binding cannot be out of order."""
    from lawvm.finland.payload_normalize import _detect_fallback_order_mismatches

    assert _detect_fallback_order_mismatches([(3, None, None, 1, "2024/100")]) == ()


def test_empty_assignment_result_has_empty_certificates() -> None:
    """SubsectionSlotAssignmentResult default has empty certificates list."""
    from lawvm.finland.payload_normalize import (
        SubsectionSlotAssignmentResult,
        SubsectionSlotMap,
    )
    result = SubsectionSlotAssignmentResult(
        subsec_map=SubsectionSlotMap(),
        sparse_slot_bindings=(),
        used_subs=(),
        unassigned_payload_slots=(),
    )
    assert result.binding_certificates == ()
