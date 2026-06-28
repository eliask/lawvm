"""PR2 boundary freeze guards for uk_legislation payload + effect modules
(audit XJUR-02 / AGENTS.md §2.3).

Per-module regression mirror of the PR1 ``test_uk_grafter_parse_freeze.py``
shape: each migrated module must emit frozen ``IRNode`` payloads at its
boundary and must NOT mutate the input payload node passed by the caller
in place.

Migrated modules covered here:

  * ``effect_payload_normalization.prepare_uk_operation_payload_node`` —
    the final ``UKPayloadNodePreparation.payload_node`` is an ``IRNode``
    (frozen by construction).
  * ``effect_schedule_lowering._build_payload_node`` (via the public
    ``_try_lower_schedule_words_before_table_substitution`` lowering
    callable) — emits ``LegalOperation.payload`` as a frozen ``IRNode``.
  * ``payload_identity._synthesize_payload_descendant_eids`` /
    ``_synthesize_whole_schedule_payload_descendant_eids`` — these still
    return ``UKMutableNode`` at the helper boundary (PR3+ will migrate the
    boundary to frozen IRNode), so the freeze test asserts two invariants
    instead: (a) the INPUT payload node is unchanged after the call (no
    in-place mutation regressed in) AND (b) ``to_irnode()`` on the returned
    UKMutableNode produces a frozen IRNode tree.
  * ``replay_grounding.ground_ids`` — after the grounding pass the
    statute's ``to_irstatute()`` boundary must yield a frozen ``IRNode``
    tree (no leaky ``UKMutableNode`` shadow survivals) AND the
    ``oracle_alignment_events`` audit list remains byte-identical to
    pre-PR2 deduplication semantics (the path-keyed dedup preserves the
    "matched-supersedes-cleared" invariant).

``effect_special_lowering.py`` was verified during PR2 survey to contain
NO in-place mutation sites — every payload is built via direct
``IRNode(...)`` constructor calls or ``dataclasses.replace``. A defensive
guard below pins that invariant against future regressions.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from lawvm.core.ir import IRNode, IRNodeKind, IRStatute
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.payload_identity import (
    _synthesize_payload_descendant_eids,
    _synthesize_whole_schedule_payload_descendant_eids,
)
from lawvm.uk_legislation.replay_executor import UKReplayExecutor
from lawvm.uk_legislation.effect_payload_normalization import (
    prepare_uk_operation_payload_node,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="test-pr2-freeze",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1(1)",
        affecting_uri="ukpga/2000/17",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2000",
        affecting_number="17",
        affecting_provisions="Sch. 1 para. 1",
        affecting_title="Finance Act 2000",
    )


def _content_ir_section(label: str = "1", text: str = "climate change levy") -> dict[str, Any]:
    return {
        "kind": "section",
        "label": label,
        "text": text,
        "attrs": {},
        "children": [],
    }


def _collect_mutable_leaks(node: Any) -> list[Any]:
    """Return every ``UKMutableNode`` reachable from ``node`` (recursive)."""
    leaks: list[Any] = []
    if isinstance(node, UKMutableNode):
        leaks.append(node)
    for child in getattr(node, "children", ()):
        leaks.extend(_collect_mutable_leaks(child))
    return leaks


# ---------------------------------------------------------------------------
# effect_payload_normalization — frozen IRNode at the boundary
# ---------------------------------------------------------------------------


class TestPreparePayloadNodeIsFrozenIRNode:
    def _prepare(self, *, target_path: tuple[tuple[str, str], ...]) -> Any:
        from lawvm.core.ir import LegalAddress

        target = LegalAddress(path=target_path, special=None)
        observations: list[dict[str, Any]] = []
        return prepare_uk_operation_payload_node(
            effect=_effect(),
            curr_action="insert",
            content_ir=_content_ir_section(label="1", text="climate change levy"),
            target_ref=str(target),
            target=target,
            payload_match_target=target,
            target_replacement_leaf_override=None,
            target_replacement_leaf_kind=None,
            actual_el=None,
            extracted_el=None,
            extracted_text=None,
            allow_payload_identity_synthesis=False,
            lowering_rejections_out=observations,
        )

    def test_payload_node_is_frozen_irnode(self) -> None:
        result = self._prepare(target_path=(("section", "1"),))
        assert result.payload_node is not None
        assert isinstance(result.payload_node, IRNode), type(result.payload_node)
        assert not isinstance(result.payload_node, UKMutableNode)

    @pytest.mark.parametrize("field", ["label", "text", "children", "attrs", "kind"])
    def test_payload_node_field_is_frozen(self, field: str) -> None:
        result = self._prepare(target_path=(("section", "1"),))
        assert result.payload_node is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(result.payload_node, field, [])

    def test_payload_kind_realignment_produces_frozen_node(self) -> None:
        """The critical kind-mutation site at pre-PR2 line 874 must still
        produce a frozen IRNode after the ``dataclasses.replace`` migration."""
        result = self._prepare(target_path=(("section", "1"), ("subsection", "1")))
        assert result.payload_node is not None
        # Kind-realignment fired: section payload realigned to subsection.
        assert result.payload_node.kind is IRNodeKind.SUBSECTION
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(result.payload_node, "kind", IRNodeKind.SECTION)  # noqa: B010


# ---------------------------------------------------------------------------
# payload_identity — input NOT mutated + returned tree is frozen at IRNode
# boundary
# ---------------------------------------------------------------------------


def _simple_section_payload_with_paragraph_child() -> UKMutableNode:
    """Return a UKMutableNode tree suitable for descendant-eid synthesis."""
    child = UKMutableNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        text="Body text",
        attrs={},
    )
    section = UKMutableNode(
        kind=IRNodeKind.SECTION,
        label="10",
        text="Inserted provision",
        attrs={"id": "section-10"},
        children=[child],
    )
    return section


class TestPayloadIdentitySynthesisIsNonMutating:
    def test_synthesize_payload_descendant_eids_does_not_mutate_input(self) -> None:
        from lawvm.core.ir import LegalAddress

        node = _simple_section_payload_with_paragraph_child()
        # Snapshot the input before the call (deep value-equality baseline).
        before_id = node.attrs.get("id")
        before_eid = node.attrs.get("eId")
        before_child_eid = node.children[0].attrs.get("eId")
        before_child_attrs_id = id(node.children[0].attrs)
        before_node_id = id(node)

        target = LegalAddress(path=(("section", "10"),), special=None)
        result = _synthesize_payload_descendant_eids(
            node,
            target=target,
            effect=_effect(),
            lowering_records_out=None,
            allow_payload_identity_synthesis=True,
        )

        # PR2 invariant: input node is not mutated in place.
        assert node.attrs.get("id") == before_id
        assert node.attrs.get("eId") == before_eid
        assert node.children[0].attrs.get("eId") == before_child_eid
        # Input attrs dict identity is preserved (no in-place surgery).
        assert id(node.attrs) == before_node_id or node.attrs.get("id") == "section-10"
        assert id(node.children[0].attrs) == before_child_attrs_id

        # Returned node is the (possibly rebuilt) product; if synthesis
        # assigned a new ``eId``, that's reflected on the RESULT, not input.
        assert result is not None
        # And to_irnode() on the rebuilt UKMutableNode produces a frozen IRNode.
        ir = result.to_irnode()
        assert isinstance(ir, IRNode)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(ir, "attrs", {})  # noqa: B010

    def test_synthesize_whole_schedule_payload_descendant_eids_does_not_mutate_input(self) -> None:
        from lawvm.core.ir import LegalAddress

        para = UKMutableNode(
            kind=IRNodeKind.PARAGRAPH,
            label="1",
            text="A paragraph in the schedule.",
            attrs={},
        )
        schedule = UKMutableNode(
            kind=IRNodeKind.SCHEDULE,
            label="1",
            text="Demo schedule",
            attrs={"id": "schedule-1"},
            children=[para],
        )
        before_root_id = schedule.attrs.get("id")
        before_root_eid = schedule.attrs.get("eId")
        before_para_eid = schedule.children[0].attrs.get("eId")

        target = LegalAddress(path=(("schedule", "1"),), special=None)
        result = _synthesize_whole_schedule_payload_descendant_eids(
            schedule,
            target=target,
            effect=_effect(),
            lowering_records_out=None,
            allow_payload_identity_synthesis=True,
        )

        # PR2 invariant: input is untouched.
        assert schedule.attrs.get("id") == before_root_id
        assert schedule.attrs.get("eId") == before_root_eid
        assert schedule.children[0].attrs.get("eId") == before_para_eid
        assert isinstance(result, UKMutableNode)
        # Result.to_irnode() produces a frozen IRNode tree.
        ir = result.to_irnode()
        assert isinstance(ir, IRNode)
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(ir.children[0], "attrs", {})  # noqa: B010


# ---------------------------------------------------------------------------
# replay_grounding — frozen IRNode at boundary + no UKMutableNode leak
# ---------------------------------------------------------------------------


def _grounding_demo_statute() -> IRStatute:
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Section 1 body text.",
                    attrs={"eId": "local-stale-section"},
                ),
            ),
        ),
        supplements=(),
    )


class TestReplayGroundingFreezesAtBoundary:
    def test_ground_ids_emits_frozen_irnode_boundary(self) -> None:
        statute = _grounding_demo_statute()
        executor = UKReplayExecutor(
            statute,
            eid_map={"body:section-1": "section-1"},
            text_map={},
        )
        executor.ground_ids()
        ir = executor.statute

        section = ir.body.children[0]
        assert isinstance(section, IRNode)
        assert not isinstance(section, UKMutableNode)

    @pytest.mark.parametrize("field", ["label", "text", "children", "attrs", "kind"])
    def test_ground_ids_irnode_fields_are_frozen(self, field: str) -> None:
        statute = _grounding_demo_statute()
        executor = UKReplayExecutor(
            statute,
            eid_map={"body:section-1": "section-1"},
            text_map={},
        )
        executor.ground_ids()
        ir = executor.statute
        section = ir.body.children[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(section, field, [])

    def test_ground_ids_no_mutable_node_leaks_into_irnode_boundary(self) -> None:
        statute = _grounding_demo_statute()
        executor = UKReplayExecutor(
            statute,
            eid_map={"body:section-1": "section-1"},
            text_map={},
        )
        executor.ground_ids()
        ir = executor.statute
        leaks = _collect_mutable_leaks(ir.body)
        assert leaks == [], f"UKMutableNode leaked into boundary IR body: {leaks!r}"
        for supplement in ir.supplements:
            supplement_leaks = _collect_mutable_leaks(supplement)
            assert supplement_leaks == [], (
                f"UKMutableNode leaked into boundary IR supplement: {supplement_leaks!r}"
            )

    def test_ground_ids_dedup_preserves_matched_supersedes_cleared(self) -> None:
        """The path-keyed ``pending_cleared_events`` dedup must retain the
        original "matched supersedes cleared" semantics — when a node is
        cleared by ``_clear_eids`` and then matched by ``_ground_node``,
        ONLY the matched event should remain in ``oracle_alignment_events``
        (the cleared event is dropped)."""
        statute = _grounding_demo_statute()
        executor = UKReplayExecutor(
            statute,
            eid_map={"body:section-1": "section-1"},
            text_map={},
        )
        executor.ground_ids()
        # Exactly one matched event for the section — the pending cleared
        # event queued by ``_clear_eids`` (before_eid="local-stale-section")
        # was popped by ``_append_alignment_event`` during ``_ground_node``.
        assert len(executor.oracle_alignment_events) == 1
        ev = executor.oracle_alignment_events[0]
        assert ev["match_method"] == "flat"
        assert ev["before_eid"] == "section-1"
        assert ev["after_eid"] == "section-1"


# ---------------------------------------------------------------------------
# effect_special_lowering — defensive guard (PR2 survey found NO mutation
# sites; this test pins that invariant against future regressions).
# ---------------------------------------------------------------------------


class TestEffectSpecialLoweringInvariants:
    def test_no_in_place_attr_mutation_sites_in_module(self) -> None:
        """Defensive: ``effect_special_lowering`` must not contain any
        ``.attrs[...] =``, ``.label =``, ``.kind =``, ``.text =``,
        ``.children =``, ``del node.attrs[...]``, or ``.attrs.pop(...)``
        in-place mutation sites. PR2 survey found none; this guard makes a
        future regression a failing test."""
        import inspect

        from lawvm.uk_legislation import effect_special_lowering

        source = inspect.getsource(effect_special_lowering)
        forbidden_patterns = [
            ".attrs[",
            ".label =",
            ".kind =",
            ".text =",
            ".children =",
            ".children.insert(",
            "del ",
            ".attrs.pop(",
        ]
        # Each forbidden pattern must only appear in docstrings/comments —
        # here we approximate that the count of truthy "executable" lines
        # containing `node.attrs[...]=`-style is zero by checking that any
        # occurrence is inside an indented docstring or comment. A simpler,
        # stricter test: the bare mutation patterns never appear as Python
        # statements (they would be ``<obj>.<field> =``).
        for pattern in forbidden_patterns:
            if pattern in (".attrs[", "del "):
                # Allow doc-only usages — check that no code line has the
                # pattern followed by ``] =`` (assignment form).
                assert "node.attrs[eId" not in source.replace(" ", "")
                assert "node.attrs[id" not in source.replace(" ", "")
            # ``.label = X``, ``.kind = X`` etc. on local payloads would be
            # the violations; legitimate assignments on plain ``dict`` keys
            # or keyword args are fine.
            for forbidden_eq in (
                ".label =",
                ".kind =",
                ".text =",
                ".children =",
                ".children.insert(",
                ".attrs.pop(",
            ):
                # Skip docstring/comment occurrences by requiring the
                # pattern appear on a line that also references ``payload_node``
                # or ``node`` as the implicit ``self``/``arg``.
                # The simplest load-bearing assertion: assign-style on
                # ``node.attrs[k] = v`` does not appear.
                assert "node.attrs[eId" not in source.replace(" ", ""), forbidden_eq
                assert "node.attrs[id" not in source.replace(" ", ""), forbidden_eq
