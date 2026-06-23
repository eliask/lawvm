"""Regression: a part-level snapshot must not drop its unamended sections.

A chapter-restructure amendment can re-emit a whole part (or chapter) as a
single snapshot version that carries the intermediate container plus all of its
sections. When that snapshot's effective date is newer than the per-section
base versions, the intermediate chapter timeline entry is superseded as
redundant. Materialization then needed a chapter container to host the few
sections that DID receive their own newer override — and previously synthesized
an EMPTY one. That empty container shadowed the part snapshot's full chapter
content during overlay, silently dropping every section that lacked a surviving
section-level override (pure whole-section content loss against the oracle).

Observed live on Maakaari (1995/540): part 2 / chapter 6 sections 1,4,5,6,8,9,
10,11,12,13 vanished from replay while the oracle carried full bodies, because
2011/96 emitted a part:2 snapshot newer than those base sections.

This test reproduces the structural shape with a minimal synthetic timeline so
it stays deterministic and corpus-independent.

The masking-vs-preservation decision is gated on snapshot OWNERSHIP: only a
container snapshot stamped as a complete owner (``lawvm_tail_policy`` /
``lawvm_payload_completeness_kind``, as a real whole-part re-emission is — see
``_stamp_complete_snapshot_owner``) is authorized to mask its older active
section children. An UNowned (attr-less) container snapshot must instead
preserve live descendants (test_timeline_properties.
``test_materialize_pit_unowned_chapter_snapshot_does_not_mask_live_section_child``).
This synthetic timeline therefore stamps the re-emitted part as a complete owner.
"""
from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import materialize_pit_ex


def _section(label: str, body: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label} §"),
            IRNode(kind=IRNodeKind.CONTENT, text=body),
        ),
    )


def _chapter(label: str, sections: list[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.CHAPTER,
        label=label,
        children=(IRNode(kind=IRNodeKind.NUM, text=f"{label} luku"), *sections),
    )


def _part(label: str, chapters: list[IRNode]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.PART,
        label=label,
        children=(IRNode(kind=IRNodeKind.NUM, text=f"{label} OSA"), *chapters),
    )


def _materialized_section_labels(
    statute: IRStatute, part_label: str, chapter_label: str
) -> list[str]:
    labels: list[str] = []
    for part in statute.body.children:
        if part.kind != IRNodeKind.PART or part.label != part_label:
            continue
        for chapter in part.children:
            if chapter.kind != IRNodeKind.CHAPTER or chapter.label != chapter_label:
                continue
            for node in chapter.children:
                if node.kind == IRNodeKind.SECTION and node.label is not None:
                    labels.append(node.label)
    return labels


def test_part_snapshot_retains_unamended_sections() -> None:
    """Sections carried only by a newer part snapshot must survive overlay."""
    section_labels = ["1", "2", "3", "4"]

    base_sections = [_section(s, f"base body {s}") for s in section_labels]
    base_chapter = _chapter("6", base_sections)
    base_part = _part("2", [base_chapter])
    base = IRStatute(
        statute_id="1995/540",
        body=IRNode(kind=IRNodeKind.BODY, children=(base_part,)),
        title="Synthetic",
    )

    src = OperationSource(
        statute_id="2011/96",
        title="Laki muuttamisesta",
        enacted="2011-02-04",
        effective="2011-02-04",
    )

    # The restructure amendment re-emits the whole part as one snapshot carrying
    # every section with full body text. A real whole-part re-emission is stamped
    # as a complete snapshot owner (lawvm_tail_policy / payload_completeness_kind);
    # only an owned container snapshot is authorized to mask its older active
    # section children during overlay. An UNowned (attr-less) container snapshot
    # must instead preserve live descendants — see
    # test_timeline_properties.test_materialize_pit_unowned_chapter_snapshot_does_not_mask_live_section_child.
    _COMPLETE_OWNER_ATTRS = {
        "lawvm_tail_policy": "replace_if_target_scope_requires",
        "lawvm_payload_completeness_kind": "complete",
    }

    def _own(node: IRNode) -> IRNode:
        return IRNode(
            kind=node.kind,
            label=node.label,
            text=node.text,
            attrs={**node.attrs, **_COMPLETE_OWNER_ATTRS},
            children=node.children,
        )

    snapshot_part = _own(
        _part(
            "2",
            [_chapter("6", [_section(s, f"snapshot body {s}") for s in section_labels])],
        )
    )

    timelines: dict[LegalAddress, ProvisionTimeline] = {}

    def add(addr_path, versions):
        addr = LegalAddress(path=addr_path)
        timelines[addr] = ProvisionTimeline(address=addr, versions=versions)

    add(
        (("part", "2"),),
        [
            ProvisionVersion(
                effective="0000-00-00",
                enacted="1995-04-12",
                variant_kind="permanent",
                content=base_part,
            ),
            ProvisionVersion(
                effective="2011-02-04",
                enacted="2011-02-04",
                variant_kind="permanent",
                source=src,
                content=snapshot_part,
            ),
        ],
    )
    add(
        (("part", "2"), ("chapter", "6")),
        [
            ProvisionVersion(
                effective="0000-00-00",
                enacted="1995-04-12",
                variant_kind="permanent",
                content=base_chapter,
            )
        ],
    )
    # Only section 2 receives its own newer override; 1, 3, 4 stay base-only and
    # are masked by the 2011 part snapshot. Without retention they vanish.
    for s in section_labels:
        versions = [
            ProvisionVersion(
                effective="0000-00-00",
                enacted="1995-04-12",
                variant_kind="permanent",
                content=_section(s, f"base body {s}"),
            )
        ]
        if s == "2":
            versions.append(
                ProvisionVersion(
                    effective="2011-02-04",
                    enacted="2011-02-04",
                    variant_kind="permanent",
                    source=src,
                    content=_section(s, "amended body 2"),
                )
            )
        add((("part", "2"), ("chapter", "6"), ("section", s)), versions)

    result = materialize_pit_ex(timelines, "9999-12-31", base=base)
    statute = result.statute

    got = _materialized_section_labels(statute, "2", "6")
    assert got == section_labels, (
        f"part snapshot dropped sections: expected {section_labels}, got {got}"
    )

    # The amended section keeps its override; the rest keep the snapshot body.
    text_by_label = {}
    for part in statute.body.children:
        if part.label != "2":
            continue
        for chapter in part.children:
            if chapter.label != "6":
                continue
            for node in chapter.children:
                if node.kind == IRNodeKind.SECTION:
                    text_by_label[node.label] = "".join(
                        c.text for c in node.children if c.text
                    )
    assert "amended body 2" in text_by_label["2"]
    for s in ("1", "3", "4"):
        assert f"snapshot body {s}" in text_by_label[s]
