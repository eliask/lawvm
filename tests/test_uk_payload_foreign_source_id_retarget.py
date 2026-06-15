"""Foreign physical source id retargeting on inserted-provision payloads.

An inserted provision carried from the AFFECTING act keeps that act's physical
``id`` (``pNNNN``) on its root node. That id is meaningless in the affected
act's eId scheme, so descendant-eid synthesis must re-anchor the root to the
affected act's target-derived eId — otherwise the whole inserted provision and
its descendants land in a foreign id namespace (``p02828-1`` …) that never
matches the affected act's structural eIds.
"""

from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.mutable_ir import UKMutableNode
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


def _section_payload(*, root_id_key: str, root_id_value: str) -> UKMutableNode:
    """An inserted section whose root carries an identity under *root_id_key*."""
    return UKMutableNode(
        kind="section",
        label="138A",
        attrs={root_id_key: root_id_value},
        children=[
            UKMutableNode(
                kind="subsection",
                label="1",
                children=[
                    UKMutableNode(kind="paragraph", label="a"),
                    UKMutableNode(kind="paragraph", label="b"),
                ],
            ),
            UKMutableNode(kind="subsection", label="2"),
        ],
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


def test_foreign_source_id_not_retargeted_when_target_underivable() -> None:
    """No guess: a target that derives no eId leaves the foreign id untouched.

    A whole-chapter target (no section leaf) has no body fallback eId, so the
    fix must not invent placement — the foreign id is preserved and no
    retargeting record is emitted.
    """
    payload = UKMutableNode(
        kind="chapter",
        label="Chapter 5C",
        attrs={"id": "p03769"},
        children=[UKMutableNode(kind="section", label="41C")],
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

    assert result.attrs.get("id") == "p03769"
    assert result.attrs.get("eId") in (None, "")
    assert not any(
        r.get("rule_id") == UK_PAYLOAD_FOREIGN_SOURCE_ID_RETARGETED_RULE_ID
        for r in records
    )


def _collect(node: UKMutableNode) -> set[str]:
    eids: set[str] = set()
    identity = str(node.attrs.get("eId") or node.attrs.get("id") or "")
    if identity:
        eids.add(identity)
    for child in node.children:
        eids |= _collect(child)
    return eids
