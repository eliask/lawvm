"""Foreign physical source id retargeting on inserted-provision payloads.

An inserted provision carried from the AFFECTING act keeps that act's physical
``id`` (``pNNNN``) on its root node. That id is meaningless in the affected
act's eId scheme, so descendant-eid synthesis must re-anchor the root to the
affected act's target-derived eId — otherwise the whole inserted provision and
its descendants land in a foreign id namespace (``p02828-1`` …) that never
matches the affected act's structural eIds.
"""

from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.payload_identity import (
    UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID,
    _is_foreign_physical_source_id,
    _synthesize_payload_descendant_eids,
)


def _effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-test",
        effect_type="inserted",
        applied=True,
        requires_applied=True,
        modified="2026-01-01",
        affected_uri="https://www.legislation.gov.uk/id/ukpga/2003/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="1",
        affected_provisions="s. 138A",
        affecting_uri="https://www.legislation.gov.uk/id/ukpga/2026/11",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2026",
        affecting_number="11",
        affecting_provisions="s. 19(3)(4)",
        affecting_title="Finance Act 2026",
    )


def _section_payload(*, root_id_key: str, root_id_value: str) -> IRNode:
    """An inserted section whose root carries an identity under *root_id_key*."""
    return IRNode(
        kind=IRNodeKind.SECTION,
        label="138A",
        attrs={root_id_key: root_id_value},
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="a"),
                    IRNode(kind=IRNodeKind.PARAGRAPH, label="b"),
                ),
            ),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
        ),
    )


def test_foreign_physical_source_id_detection() -> None:
    assert _is_foreign_physical_source_id("p02828")
    assert _is_foreign_physical_source_id("p21270")
    assert _is_foreign_physical_source_id("p02828-1")
    # Real affected-act eIds are not foreign physical ids.
    assert not _is_foreign_physical_source_id("section-138A")
    assert not _is_foreign_physical_source_id("schedule-1-paragraph-3")
    assert not _is_foreign_physical_source_id("")
    # ``part``/``paragraph`` kind names must not be mistaken for physical ids.
    assert not _is_foreign_physical_source_id("paragraph-1")


def test_foreign_source_id_reanchored_to_target_eid() -> None:
    payload = _section_payload(root_id_key="id", root_id_value="p02828")
    target = LegalAddress((("section", "138a"),))
    records: list[dict[str, object]] = []

    result = _synthesize_payload_descendant_eids(
        payload,
        target=target,
        effect=_effect(),
        lowering_records_out=records,
        allow_payload_identity_synthesis=True,
    )

    # Root re-anchored to the affected act's target-derived eId; foreign id dropped.
    assert result.attrs.get("eId") == "section-138a"
    assert "id" not in result.attrs
    # Descendants synthesized under the affected act's scheme, not ``p02828-…``.
    sub1 = result.children[0]
    assert sub1.attrs.get("eId") == "section-138a-1"
    assert sub1.children[0].attrs.get("eId") == "section-138a-1-a"
    assert sub1.children[1].attrs.get("eId") == "section-138a-1-b"
    assert result.children[1].attrs.get("eId") == "section-138a-2"
    # No descendant retains a foreign physical-id-rooted eId.
    eids = _collect(result)
    assert not any(e.startswith("p02828") for e in eids)
    # The retargeting is recorded as a non-blocking lowering observation.
    retarget = [
        r
        for r in records
        if r.get("rule_id") == UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID
    ]
    assert len(retarget) == 1
    assert retarget[0]["blocking"] is False
    assert retarget[0]["foreign_source_id"] == "p02828"
    assert retarget[0]["root_eid"] == "section-138a"


def test_real_affected_act_eid_is_preserved() -> None:
    """An authoritative eId in the affected act's scheme must not be retargeted."""
    payload = _section_payload(root_id_key="eId", root_id_value="section-200")
    target = LegalAddress((("section", "138a"),))
    records: list[dict[str, object]] = []

    result = _synthesize_payload_descendant_eids(
        payload,
        target=target,
        effect=_effect(),
        lowering_records_out=records,
        allow_payload_identity_synthesis=True,
    )

    # Authoritative eId wins over the target leaf; no retargeting record.
    assert result.attrs.get("eId") == "section-200"
    assert not any(
        r.get("rule_id") == UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID
        for r in records
    )


def test_whole_chapter_target_reanchored_to_container_eid() -> None:
    """A whole-chapter target derives a hierarchical container eId.

    A part/chapter insert target (no section leaf) carries the UK hierarchical
    container eId (``part-2-chapter-5C``). The chapter payload's foreign physical
    id is re-anchored to that derived eId, and the foreign id is dropped.
    """
    payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="Chapter 5C",
        attrs={"id": "p03769"},
        children=(IRNode(kind=IRNodeKind.SECTION, label="41C"),),
    )
    target = LegalAddress((("part", "2"), ("chapter", "5c")))
    records: list[dict[str, object]] = []

    result = _synthesize_payload_descendant_eids(
        payload,
        target=target,
        effect=_effect(),
        lowering_records_out=records,
        allow_payload_identity_synthesis=True,
    )

    # Chapter container re-anchored to its derived hierarchical eId; foreign id dropped.
    assert result.attrs.get("eId") == "part-2-chapter-5c"
    assert "id" not in result.attrs
    retarget = [
        r
        for r in records
        if r.get("rule_id") == UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID
    ]
    assert len(retarget) == 1
    assert retarget[0]["root_eid"] == "part-2-chapter-5c"


def test_whole_chapter_child_section_gets_flat_eid() -> None:
    """Sections nested in an inserted chapter payload get FLAT ``section-NNN`` eIds.

    UK body sections always carry a flat root eId even when nested under a part
    or chapter, so a chapter payload's section children must re-root the
    descendant namespace rather than inherit the container eId.
    """
    payload = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="Chapter 7A",
        attrs={"eId": "part-4-chapter-7A"},
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="289A",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="a"),
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="b"),
                        ),
                    ),
                    IRNode(kind=IRNodeKind.SUBSECTION, label="2"),
                ),
            ),
            IRNode(kind=IRNodeKind.SECTION, label="289B"),
        ),
    )
    target = LegalAddress((("part", "4"), ("chapter", "7a")))
    records: list[dict[str, object]] = []

    result = _synthesize_payload_descendant_eids(
        payload,
        target=target,
        effect=_effect(),
        lowering_records_out=records,
        allow_payload_identity_synthesis=True,
    )

    # The chapter container keeps its hierarchical eId.
    assert result.attrs.get("eId") == "part-4-chapter-7A"
    section_289a = result.children[0]
    section_289b = result.children[1]
    # Sections get a FLAT root eId, not ``part-4-chapter-7A-section-289A``.
    assert section_289a.attrs.get("eId") == "section-289a"
    assert section_289b.attrs.get("eId") == "section-289b"
    # Section descendants are suffixed from the flat section root.
    sub1 = section_289a.children[0]
    assert sub1.attrs.get("eId") == "section-289a-1"
    assert sub1.children[0].attrs.get("eId") == "section-289a-1-a"
    assert sub1.children[1].attrs.get("eId") == "section-289a-1-b"
    assert section_289a.children[1].attrs.get("eId") == "section-289a-2"
    # No descendant retains a container-prefixed section eId.
    eids = _collect(result)
    assert not any(e.startswith("part-4-chapter-7A-section") for e in eids)


def _collect(node: IRNode) -> set[str]:
    eids: set[str] = set()
    identity = str(node.attrs.get("eId") or node.attrs.get("id") or "")
    if identity:
        eids.add(identity)
    for child in node.children:
        eids |= _collect(child)
    return eids
