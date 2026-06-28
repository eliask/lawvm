"""Tests for owned Finland timeline version dedupe before materialization."""

from __future__ import annotations

import lawvm.finland.timeline_version_dedupe as timeline_version_dedupe
from lawvm.core.invariant_profiles import core_replay_strict_profile
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalAddress, OperationSource, ProvisionTimeline, ProvisionVersion
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_addresses import STRUCTURAL_RENUMBER_SNAPSHOT_ATTR
from lawvm.core.timeline_invariants import check_all_timeline_invariants_typed
from lawvm.finland.timeline_version_dedupe import (
    FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID,
    FI_TIMELINE_RESTRUCTURE_RELABEL_SHELL_SHADOW_COLLAPSE_RULE_ID,
    FI_TIMELINE_RESTRUCTURE_RELABEL_SNAPSHOT_SHADOW_COLLAPSE_RULE_ID,
    FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID,
    dedupe_finland_timelines,
)
from tests.corpus_pin_helpers import replay_xml_for_test


def _pv(
    *,
    effective: str,
    enacted: str,
    source_id: str,
    text: str | None,
    content_hash: str = "",
) -> ProvisionVersion:
    content = None
    if text is not None:
        content = IRNode(kind=IRNodeKind.SECTION, label="12", text=text)
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        variant_kind="permanent",
        content=content,
        source=OperationSource(statute_id=source_id, enacted=enacted),
        content_hash=content_hash,
    )


def test_absent_content_shadow_collapse_removes_competing_none_row() -> None:
    address = LegalAddress(path=(("section", "12"),))
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                _pv(
                    effective="2005-01-01",
                    enacted="2004-08-20",
                    source_id="2004/821",
                    text="12 §",
                ),
                _pv(
                    effective="2005-01-01",
                    enacted="2004-08-20",
                    source_id="2004/821",
                    text=None,
                ),
            ],
        )
    }
    deduped, records = dedupe_finland_timelines(timelines)
    assert len(deduped[address].versions) == 1
    assert deduped[address].versions[0].content is not None
    assert len(records) == 1
    assert records[0].witness_rule_id == FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID


def test_restructure_relabel_snapshot_shadow_collapse_prefers_payload_authority() -> None:
    address = LegalAddress(path=(("part", "2"), ("chapter", "2"), ("section", "8")))
    source = OperationSource(statute_id="2019/371", enacted="2019-03-29")
    restructure_snapshot = ProvisionVersion(
        effective="2019-04-01",
        enacted="2019-03-29",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="8",
            text="stale relabel snapshot",
            attrs={"lawvm_restructure_relabel_section_snapshot": "1"},
        ),
        source=source,
    )
    ordinary_payload = ProvisionVersion(
        effective="2019-04-01",
        enacted="2019-03-29",
        variant_kind="permanent",
        content=IRNode(kind=IRNodeKind.SECTION, label="8", text="ordinary payload"),
        source=source,
    )
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[restructure_snapshot, ordinary_payload],
        )
    }

    deduped, records = dedupe_finland_timelines(timelines)

    remaining_versions = deduped[address].versions
    assert len(remaining_versions) == 1
    remaining_content = remaining_versions[0].content
    assert remaining_content is not None
    assert timeline_version_dedupe.irnode_to_text(remaining_content) == "ordinary payload"
    assert len(records) == 1
    assert records[0].witness_rule_id == FI_TIMELINE_RESTRUCTURE_RELABEL_SNAPSHOT_SHADOW_COLLAPSE_RULE_ID


def test_restructure_relabel_snapshot_is_not_shadowed_by_structural_renumber_snapshot() -> None:
    address = LegalAddress(path=(("chapter", "6"), ("section", "24f")))
    source = OperationSource(statute_id="1998/658", enacted="1998-08-21")
    structural_renumber_snapshot = ProvisionVersion(
        effective="1998-08-21",
        enacted="1998-08-21",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="24f",
            text="24f section text",
            attrs={STRUCTURAL_RENUMBER_SNAPSHOT_ATTR: "1"},
        ),
        source=source,
    )
    restructure_snapshot = ProvisionVersion(
        effective="1998-08-21",
        enacted="1998-08-21",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="24f",
            text="24f § text",
            attrs={"lawvm_restructure_relabel_section_snapshot": "1"},
        ),
        source=source,
    )
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[structural_renumber_snapshot, restructure_snapshot],
        )
    }

    deduped, records = dedupe_finland_timelines(timelines)

    assert deduped[address].versions == (structural_renumber_snapshot, restructure_snapshot)
    assert records == ()


def test_restructure_relabel_snapshot_shadows_label_only_section_shell() -> None:
    address = LegalAddress(path=(("chapter", "7"), ("section", "61")))
    source = OperationSource(statute_id="1994/318", enacted="1994-04-29")
    restructure_snapshot = ProvisionVersion(
        effective="1994-07-01",
        enacted="1994-04-29",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="61",
            attrs={"lawvm_restructure_relabel_section_snapshot": "1"},
            children=(
                IRNode(kind=IRNodeKind.NUM, text="61 §"),
                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="moved body"),
            ),
        ),
        source=source,
    )
    label_only_shell = ProvisionVersion(
        effective="1994-07-01",
        enacted="1994-04-29",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="61",
            attrs={
                "lawvm_tail_policy": "replace_if_target_scope_requires",
                "lawvm_payload_completeness_kind": "complete",
            },
            children=(
                IRNode(kind=IRNodeKind.NUM, text="61 §"),
                IRNode(kind=IRNodeKind.OMISSION, attrs={"name": "omission"}),
            ),
        ),
        source=source,
    )
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[restructure_snapshot, label_only_shell],
        )
    }

    deduped, records = dedupe_finland_timelines(timelines)

    assert deduped[address].versions == (restructure_snapshot,)
    assert len(records) == 1
    assert records[0].witness_rule_id == FI_TIMELINE_RESTRUCTURE_RELABEL_SHELL_SHADOW_COLLAPSE_RULE_ID


def test_restructure_relabel_snapshot_does_not_shadow_repeal_placeholder() -> None:
    address = LegalAddress(path=(("part", "2"), ("chapter", "22a"), ("section", "218")))
    source = OperationSource(statute_id="2016/773", enacted="2016-09-09")
    restructure_snapshot = ProvisionVersion(
        effective="2017-01-01",
        enacted="2016-09-09",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="218",
            text="old carried body",
            attrs={"lawvm_restructure_relabel_section_snapshot": "1"},
        ),
        source=source,
    )
    repeal_placeholder = ProvisionVersion(
        effective="2017-01-01",
        enacted="2016-09-09",
        variant_kind="permanent",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="218",
            attrs={"lawvm_repeal_placeholder": "1"},
            children=(IRNode(kind=IRNodeKind.NUM, text="218 §"),),
        ),
        source=source,
    )
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[restructure_snapshot, repeal_placeholder],
        )
    }

    deduped, records = dedupe_finland_timelines(timelines)

    assert deduped[address].versions == (repeal_placeholder,)
    assert len(records) == 1
    assert records[0].witness_rule_id == FI_TIMELINE_RESTRUCTURE_RELABEL_SNAPSHOT_SHADOW_COLLAPSE_RULE_ID


def test_semantic_text_cache_reuses_content_hash(monkeypatch) -> None:
    calls = 0
    original_irnode_to_text = timeline_version_dedupe.irnode_to_text

    def counting_irnode_to_text(node: IRNode) -> str:
        nonlocal calls
        calls += 1
        return original_irnode_to_text(node)

    monkeypatch.setattr(
        timeline_version_dedupe,
        "irnode_to_text",
        counting_irnode_to_text,
    )
    address = LegalAddress(path=(("section", "12"),))
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                _pv(
                    effective="2005-01-01",
                    enacted="2004-08-20",
                    source_id="2004/821",
                    text="12§ text",
                    content_hash="same-content",
                ),
                _pv(
                    effective="2005-01-01",
                    enacted="2004-08-20",
                    source_id="2004/821",
                    text="12§ text",
                    content_hash="same-content",
                ),
            ],
        )
    }
    cache: timeline_version_dedupe.SemanticTextKeyCache = {}

    deduped, records = dedupe_finland_timelines(
        timelines,
        semantic_text_cache=cache,
    )

    assert calls == 1
    assert len(deduped[address].versions) == 1
    assert len(records) == 1
    assert records[0].witness_rule_id == FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID
    assert cache == {("content_hash", "same-content"): "12 § text"}


def test_1993_1054_corpus_no_longer_reports_overlapping_permanent() -> None:
    master = replay_xml_for_test("1993/1054", mode="legal_pit", quiet=True)
    products = master.products
    assert products.timelines is not None
    assert products.materialization_spec is not None

    violations = check_all_timeline_invariants_typed(
        products.materialized_state.ir,
        products.timelines,
        str(products.materialization_spec.as_of),
        families=core_replay_strict_profile("corpus_pin").timeline_invariants,
    )
    overlap = [v for v in violations if v.kind == "overlapping_permanent"]
    assert overlap == []
    assert products.timeline_version_dedupes


def test_2000_256_2024_273_item_repeals_survive_timeline_dedupe() -> None:
    master = replay_xml_for_test("2000/256", mode="legal_pit", quiet=True)
    section = next(
        child
        for chapter in master.materialized_state.ir.children
        if chapter.kind is IRNodeKind.CHAPTER and chapter.label == "2"
        for child in chapter.children
        if child.kind is IRNodeKind.SECTION and child.label == "5"
    )
    text = " ".join(timeline_version_dedupe.irnode_to_text(section).split())

    assert "4) hallintojohtajalla ja viestintäjohtajalla ylempi korkeakoulututkinto" in text
    assert "7 a) asiantuntijalla korkeakoulututkinto" in text
    assert "10) tiedottajalla" not in text
    assert "16) kirjastoamanuenssilla" not in text
    assert "17) kielenkääntäjällä korkeakoulututkinto tai muu soveltuva tutkinto" in text
    assert "perehtyneisyys viran tehtäväalaan" in text


def test_1940_378_1994_318_restructure_relabel_preserves_moved_voimaantulo_section() -> None:
    master = replay_xml_for_test("1940/378", mode="legal_pit", quiet=True)
    section = next(
        child
        for chapter in master.materialized_state.ir.children
        if chapter.kind is IRNodeKind.CHAPTER and chapter.label == "7"
        for child in chapter.children
        if child.kind is IRNodeKind.SECTION and child.label == "61"
    )
    text = " ".join(timeline_version_dedupe.irnode_to_text(section).split())

    assert "Tämä laki tulee voimaan 1 päivänä elokuuta 1940" in text
    assert "Tätä lakia sovelletaan niihinkin tapauksiin" in text
    assert "Jos perintö tai lahjaveroasia" in text
    assert any(
        record.address == "chapter:7/section:61"
        and record.source_statute == "1994/318"
        and record.witness_rule_id
        == FI_TIMELINE_RESTRUCTURE_RELABEL_SHELL_SHADOW_COLLAPSE_RULE_ID
        for record in master.products.timeline_version_dedupes
    )
