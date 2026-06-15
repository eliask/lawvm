"""Tests for the replay occupancy model (OccupancyClass, SlotIdentity, SlotState,
validate_transition).

Covers valid and invalid occupancy transitions as defined in the replay
constitution (LAWVM_CONSTITUTION.md §4).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from typing import Any, cast

from lawvm.core.occupancy import (
    InvalidOccupancyTransition,
    OccupancyAction,
    OccupancyClass,
    SlotIdentity,
    SlotState,
    VALID_TRANSITIONS,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(label: str = "6", kind: str = "section") -> SlotIdentity:
    return SlotIdentity(parent_path=(), kind=kind, label=label)


# ---------------------------------------------------------------------------
# Valid transitions
# ---------------------------------------------------------------------------


def test_replace_substantive_stays_substantive():
    """replace on SUBSTANTIVE yields SUBSTANTIVE (content update)."""
    result = validate_transition(OccupancyAction.REPLACE, OccupancyClass.SUBSTANTIVE)
    assert result == OccupancyClass.SUBSTANTIVE


def test_insert_absent_yields_substantive():
    """insert on ABSENT yields SUBSTANTIVE (new content)."""
    result = validate_transition(OccupancyAction.INSERT, OccupancyClass.ABSENT)
    assert result == OccupancyClass.SUBSTANTIVE


def test_insert_tombstone_yields_substantive():
    """insert on TOMBSTONE yields SUBSTANTIVE (reenactment)."""
    result = validate_transition(OccupancyAction.INSERT, OccupancyClass.TOMBSTONE)
    assert result == OccupancyClass.SUBSTANTIVE


def test_insert_scaffold_yields_substantive():
    """insert on SCAFFOLD yields SUBSTANTIVE (compatibility reenactment)."""
    result = validate_transition(OccupancyAction.INSERT, OccupancyClass.SCAFFOLD)
    assert result == OccupancyClass.SUBSTANTIVE


def test_repeal_substantive_yields_tombstone():
    """repeal on SUBSTANTIVE yields TOMBSTONE (preserves addressability)."""
    result = validate_transition(OccupancyAction.REPEAL, OccupancyClass.SUBSTANTIVE)
    assert result == OccupancyClass.TOMBSTONE


def test_repeal_tombstone_is_idempotent() -> None:
    """repeal on TOMBSTONE stays TOMBSTONE (idempotent repeal)."""
    result = validate_transition(OccupancyAction.REPEAL, OccupancyClass.TOMBSTONE)
    assert result == OccupancyClass.TOMBSTONE


def test_occupancy_action_is_value_stringified_not_string_comparable() -> None:
    assert str(OccupancyAction.REPLACE) == "replace"
    assert OccupancyAction.REPLACE != "replace"


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


def test_replace_absent_is_invalid():
    """replace on ABSENT is not valid — slot does not exist yet."""
    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(OccupancyAction.REPLACE, OccupancyClass.ABSENT)


def test_replace_tombstone_is_invalid():
    """replace on TOMBSTONE is not valid — tombstone must be reenacted first."""
    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(OccupancyAction.REPLACE, OccupancyClass.TOMBSTONE)


def test_repeal_absent_is_invalid():
    """repeal on ABSENT is not valid — cannot repeal something that never existed."""
    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(OccupancyAction.REPEAL, OccupancyClass.ABSENT)


def test_unknown_action_is_invalid():
    """Unknown action enum raises InvalidOccupancyTransition."""

    # Create a fake OccupancyAction-like object with unknown value
    class FakeAction:
        value = "frobnicate"

    with pytest.raises(InvalidOccupancyTransition):
        validate_transition(cast(Any, FakeAction()), OccupancyClass.SUBSTANTIVE)


# ---------------------------------------------------------------------------
# SlotIdentity and SlotState construction
# ---------------------------------------------------------------------------


def test_slot_identity_is_frozen():
    """SlotIdentity is immutable (frozen dataclass)."""
    identity = _identity()
    with pytest.raises(FrozenInstanceError):
        cast(Any, identity).label = "7"


def test_slot_state_carries_tombstone_text():
    """SlotState can carry tombstone text for display."""
    identity = _identity("82 a")
    state = SlotState(
        identity=identity,
        occupancy=OccupancyClass.TOMBSTONE,
        last_modified_by="2020/766",
        tombstone_text="82 a § on kumottu L:lla 13.11.2020/766",
    )
    assert state.occupancy == OccupancyClass.TOMBSTONE
    assert "kumottu" in (state.tombstone_text or "")


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS coverage
# ---------------------------------------------------------------------------


def test_valid_transitions_table_has_six_entries():
    """The canonical valid transitions table has exactly the documented cases."""
    assert len(VALID_TRANSITIONS) == 6


# ---------------------------------------------------------------------------
# _check_occupancy_policy: observation must track the slot the apply resolves
# ---------------------------------------------------------------------------


from types import SimpleNamespace

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_policy import _check_occupancy_policy
from lawvm.finland.statute import ReplayState


def _section(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _replace_intent():
    from lawvm.core.canonical_intent import (
        CoverageMode,
        ExecutionContract,
        IntentKind,
        NodeTarget,
        OccupancyPolicy,
        Replace,
    )

    address = LegalAddress(path=(("section", "75"),))
    return Replace(
        kind=IntentKind.REPLACE,
        target=NodeTarget(address=address),
        payload=cast(Any, _section("75", _content("payload body"))),
        contract=ExecutionContract(
            occupancy=OccupancyPolicy.same_slot_replace(),
            coverage=CoverageMode.EXACT,
        ),
    )


def _fake_replace_rop(*, muutos_ir, target_special=None):
    return SimpleNamespace(
        resolved_action_type="REPLACE",
        move_clause_target_unit_kind=None,
        effective_target_paragraph=None,
        effective_target_item_label=None,
        effective_target_special=target_special,
        muutos_ir=muutos_ir,
        resolved_source_statute="1922/144",
        op_id="op-test",
        target_norm="75",
        resolved_target_address=LegalAddress(path=(("section", "75"),)),
        targets_whole_unit=lambda kind: kind == "section",
    )


def test_occupancy_skips_base_frame_empty_whole_section_replace() -> None:
    """A whole-section REPLACE that installs into an empty base frame is not flagged.

    Sparse historical codes (1734/4-000, 1868/31-000) carry a §X the amendment
    REPLACE-targets even though the slot never existed in the base IR; the apply
    turns that into a create. With sec_path None and a substantive section
    payload the occupancy precondition is not contradicted, so no
    OCCUPANCY_POLICY_VIOLATION must be recorded.
    """
    state = ReplayState(ir=_body())  # empty base frame: §75 absent
    payload = _section("75", _content("installed body text"))
    rop = _fake_replace_rop(muutos_ir=payload)
    findings: list[Finding] = []
    _check_occupancy_policy(
        state, rop, _replace_intent(), None, "[1922/144] REPLACE 75 §",
        findings_out=findings,
    )
    assert findings == []


def test_occupancy_flags_replace_on_absent_with_no_substantive_payload() -> None:
    """A REPLACE that resolves absent and carries no body is a genuine violation."""
    state = ReplayState(ir=_body())  # §75 absent
    # Heading/num shell only — no substantive child to install: a dropped-create,
    # not a legitimate base-frame install.
    shell = _section("75", IRNode(kind=IRNodeKind.HEADING, text="Otsikko"))
    rop = _fake_replace_rop(muutos_ir=shell)
    findings: list[Finding] = []
    _check_occupancy_policy(
        state, rop, _replace_intent(), None, "[1922/144] REPLACE 75 §",
        findings_out=findings,
    )
    assert [f.kind for f in findings] == ["APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert findings[0].detail["current_occupancy"] == "absent"


def test_occupancy_not_flagged_when_apply_resolves_substantive_via_fallback() -> None:
    """When the apply's ladder binds a substantive slot, the REPLACE is allowed.

    Mirrors the part-nested / live-unique-global case: the narrow scoped lookup
    would miss the slot, but the apply resolves it to a live substantive
    section. The occupancy observation reads the resolved path and sees
    SUBSTANTIVE, which is allowed_from for same_slot_replace — no violation.
    """
    from lawvm.core import tree_ops as _tops

    live = _section("75", _content("existing body"))
    state = ReplayState(ir=_body(live))
    sec_path = _tops.find(state.ir, "section", "75")
    assert sec_path is not None
    payload = _section("75", _content("new body"))
    rop = _fake_replace_rop(muutos_ir=payload)
    findings: list[Finding] = []
    _check_occupancy_policy(
        state, rop, _replace_intent(), sec_path, "[1922/144] REPLACE 75 §",
        findings_out=findings,
    )
    assert findings == []
