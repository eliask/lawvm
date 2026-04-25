"""Tests for C2: Admissible Binding Certificate.

Verifies that subsection slot assignments produce correct admissibility
classifications: single (deterministic), ambiguous (multiple candidates),
or fallback (positional assignment).
"""

from __future__ import annotations

from typing import Any, cast

from lawvm.core.compile_result import AdmissibleBindingCertificate
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


# ---------------------------------------------------------------------------
# AdmissibleBindingCertificate type tests
# ---------------------------------------------------------------------------

def test_admissible_binding_certificate_single() -> None:
    cert = AdmissibleBindingCertificate(
        slot_id=1,
        amendment_id="2024/100",
        candidate_count=1,
        admissibility="single",
    )
    assert cert.admissibility == "single"
    assert cert.candidate_count == 1


def test_admissible_binding_certificate_ambiguous() -> None:
    cert = AdmissibleBindingCertificate(
        slot_id=2,
        amendment_id="2024/200",
        candidate_count=3,
        admissibility="ambiguous",
    )
    assert cert.admissibility == "ambiguous"
    assert cert.candidate_count == 3


def test_admissible_binding_certificate_fallback() -> None:
    cert = AdmissibleBindingCertificate(
        slot_id=1,
        amendment_id="2024/300",
        candidate_count=5,
        admissibility="fallback",
    )
    assert cert.admissibility == "fallback"


def test_admissible_binding_certificate_frozen() -> None:
    cert = AdmissibleBindingCertificate(
        slot_id=1,
        amendment_id="2024/100",
        candidate_count=1,
        admissibility="single",
    )
    try:
        cast(Any, cert).slot_id = 99
        assert False, "should be frozen"
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Integration: slot assignment produces binding certificates
# ---------------------------------------------------------------------------

def _make_op(
    target_paragraph: int,
    op_type: OpType = "REPLACE",
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
        duplicate_targets=(),
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
        duplicate_targets=(),
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
        duplicate_targets=(),
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
        duplicate_targets=(),
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
        duplicate_targets=(),
    )
    result = _assign_subsection_slots(inputs)
    assert result.binding_certificates == ()


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
