from __future__ import annotations

import copy
import datetime as dt
from contextlib import redirect_stdout
from io import StringIO
from typing import Any, cast

import lxml.etree as etree
import pytest

from lawvm.core.invariant_profiles import structural_product_hierarchical_profile
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.ir import IRStatute
from lawvm.core.ir import LegalAddress
from lawvm.core.ir import OperationSource
from lawvm.core.ir import ProvisionTimeline
from lawvm.core.ir import ProvisionVersion
from lawvm.core.effect_lifecycle import (
    EffectLifecycleEvent,
    EffectRef,
    EffectRelation,
    SourceInstrumentRef,
    SourceProvisionRef,
)
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.tree_ops import check_invariants
from lawvm.core.ir import LegalOperation
from lawvm.core.provenance import MigrationEvent
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.finland.apply import apply_op
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.compile_amendment import compile_amendment_ops
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.corpus import get_corpus
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.kumotaan_replay import (
    _inject_pure_kumotaan_subsection_repeal_ops,
    _live_suffix_section_labels_for_numeric_kumotaan_ranges,
)
from tests.corpus_pin_helpers import replay_xml_for_test
from lawvm.core.timeline import compile_timelines
from lawvm.core.timeline import materialize_pit_ex
from lawvm.core.timeline import select_active_version
from lawvm.core.timeline import materialize_pit
from lawvm.core.timeline_results import MaterializationLineagePlan
from lawvm.tools.section_keys import extract_ir_sections
from lawvm.finland.replay_products import ReplayProducts
from lawvm.finland.replay_products import FinlandLineageBridgeClassification
from lawvm.finland.replay_products import _FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE
from lawvm.finland.replay_products import _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR
from lawvm.finland.replay_products import _cleanup_sourceless_base_merge_conflicts
from lawvm.finland.replay_products import _reconcile_materialized_fold_hcontainer_sections
from lawvm.finland.replay_products import _restore_replay_fold_repeal_placeholders
from lawvm.finland.replay_products import _rekey_timelines_with_migration_events
from lawvm.finland.replay_products import _renumber_source_prefix_may_match_cached
from lawvm.finland.replay_products import _classify_finland_lineage_bridge
from lawvm.finland.replay_products import _select_pit_lineage_inputs
from lawvm.finland.replay_products import _temporal_events_from_lo_ops
from lawvm.finland.replay_products import _merge_temporal_events
from lawvm.finland.replay_products import build_replay_products
from lawvm.finland.replay_products import fi_product_tree_invariant_dicts
from lawvm.finland.replay_products import project_materialized_provisions_wrapper
from lawvm.finland.replay_products import validate_replay_products
from lawvm.finland.replay_fold_projection import ReplayFoldProjectionRequest, project_replay_fold
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.core.timeline_addresses import _retarget_root_node
from lawvm.tools.inspect_amendment import build_amendment_bundle
from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.statute import ReplayResult, ReplayState, StatuteContext
from lawvm.finland.xml_ir import fi_xml_to_ir_node


@pytest.fixture(scope="module")
def replay_1994_674_finlex_oracle() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("1994/674", mode="official_consolidation", quiet=True))


@pytest.fixture(scope="module")
def replay_1999_488_legal_pit() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("1999/488", mode="legal_pit", quiet=True))


@pytest.fixture(scope="module")
def replay_2012_916_finlex_oracle() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay(
            "2012/916",
            mode="official_consolidation",
            quiet=True,
            build_full_products=False,
        ),
    )


@pytest.fixture(scope="module")
def replay_2014_1429_finlex_oracle() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay(
            "2014/1429",
            mode="official_consolidation",
            quiet=True,
            build_full_products=False,
        ),
    )


@pytest.fixture(scope="module")
def replay_2006_395_finlex_oracle() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("2006/395", mode="official_consolidation", quiet=True))


@pytest.fixture(scope="module")
def replay_2009_953_legal_pit() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("2009/953", mode="legal_pit", quiet=True))


@pytest.fixture(scope="module")
def replay_1992_552_finlex_oracle() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay(
            "1992/552",
            mode="official_consolidation",
            quiet=True,
            build_full_products=False,
        ),
    )


@pytest.fixture(scope="module")
def replay_2014_938_finlex_oracle() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("2014/938", mode="official_consolidation", quiet=True))


@pytest.fixture(scope="module")
def replay_1965_40_finlex_oracle() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay(
            "1965/40",
            mode="official_consolidation",
            quiet=True,
            build_full_products=False,
        ),
    )


@pytest.fixture(scope="module")
def replay_1929_234_finlex_oracle() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay(
            "1929/234",
            mode="official_consolidation",
            quiet=True,
            build_full_products=False,
        ),
    )


@pytest.fixture(scope="module")
def replay_1967_550_legal_pit_with_lo_ops() -> tuple[ReplayResult, list[LegalOperation]]:
    lo_ops: list[LegalOperation] = []
    replay = cast(
        ReplayResult,
        pinned_replay("1967/550", mode="legal_pit", quiet=True, lo_ops_out=lo_ops),
    )
    return replay, lo_ops


@pytest.fixture(scope="module")
def replay_2009_1672_finlex_oracle_with_lo_ops() -> tuple[ReplayResult, list[LegalOperation]]:
    lo_ops: list[LegalOperation] = []
    replay = cast(
        ReplayResult,
        pinned_replay("2009/1672", mode="official_consolidation", quiet=True, lo_ops_out=lo_ops),
    )
    return replay, lo_ops


@pytest.fixture(scope="module")
def replay_2010_76_finlex_oracle_with_lo_ops() -> tuple[ReplayResult, list[LegalOperation]]:
    lo_ops: list[LegalOperation] = []
    replay = cast(
        ReplayResult,
        pinned_replay("2010/76", mode="official_consolidation", quiet=True, lo_ops_out=lo_ops),
    )
    return replay, lo_ops


@pytest.fixture(scope="module")
def replay_1997_1339_finlex_oracle_full_products() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay("1997/1339", mode="official_consolidation", quiet=True, build_full_products=True),
    )


@pytest.fixture(scope="module")
def replay_2002_1244_finlex_oracle_full_products() -> ReplayResult:
    return cast(
        ReplayResult,
        pinned_replay("2002/1244", mode="official_consolidation", quiet=True, build_full_products=True),
    )


@pytest.fixture(scope="module")
def replay_1977_603_finlex_oracle() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("1977/603", mode="official_consolidation", quiet=True))


@pytest.fixture(scope="module")
def replay_1990_845_finlex_oracle() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("1990/845", mode="official_consolidation", quiet=True))


def test_replay_xml_exposes_typed_replay_products(replay_2009_953_legal_pit: ReplayResult) -> None:
    assert replay_2009_953_legal_pit.products.replay_fold_state is replay_2009_953_legal_pit.replay_fold_state
    assert replay_2009_953_legal_pit.products.materialized_state is replay_2009_953_legal_pit.materialized_state
    assert replay_2009_953_legal_pit.products.timelines is replay_2009_953_legal_pit.timelines
    assert replay_2009_953_legal_pit.materialization_spec is not None
    assert replay_2009_953_legal_pit.source_adjudication is not None
    assert (
        replay_2009_953_legal_pit.materialization_spec.as_of
        == replay_2009_953_legal_pit.source_adjudication.cutoff_date
    )
    assert replay_2009_953_legal_pit.materialization_spec.lineage_mode in {
        "rekeyed_with_migrations",
        "rekeyed_only",
        "raw_with_migrations",
    }
    assert (
        replay_2009_953_legal_pit.materialization_spec.lineage_plan.mode
        == replay_2009_953_legal_pit.materialization_spec.lineage_mode
    )
    assert replay_2009_953_legal_pit.materialization_spec.lineage_reason in {
        "default_migration_projection",
        "native_rebirth_after_renumber",
        "leaf_stable_scope_renumber",
        "destination_occupancy_collision",
        "scope_changing_migration_fallback",
    }
    assert isinstance(
        replay_2009_953_legal_pit.materialization_spec.bridge_classification,
        FinlandLineageBridgeClassification,
    )
    assert replay_2009_953_legal_pit.source_adjudication.statute_id == "2009/953"


def test_1998_805_materialized_state_restores_sections_after_expired_temporary_chain() -> None:
    replay = replay_xml_for_test("1998/805", mode="official_consolidation", quiet=True)

    sections = extract_ir_sections(replay.materialized_state.ir)
    assert "section:1" in sections
    assert "section:2" in sections
    assert "Kansanopiston ja valtakunnallisen liikunnan koulutuskeskuksen" in irnode_to_text(
        sections["section:1"]
    )
    assert "Suoritteiden laskeminen" in irnode_to_text(sections["section:2"])


def test_reconcile_fold_sections_does_not_restore_into_attachments() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Operative text"),),
    )
    attachment = IRNode(
        kind=IRNodeKind.HCONTAINER,
        attrs={"name": "attachments"},
        children=(
            IRNode(kind=IRNodeKind.HCONTAINER, attrs={"name": "attachment"}, text="Fee table"),
            section,
        ),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(attachment,),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(section,),
            ),
            attachment,
        ),
    )

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, replay_fold)

    assert len(reconciled.children) == 2
    provisions = reconciled.children[0]
    assert provisions.kind is IRNodeKind.HCONTAINER
    assert provisions.attrs.get("name") == "statuteProvisionsWrapper"
    assert [child.label for child in provisions.children if child.kind is IRNodeKind.SECTION] == ["1"]
    assert reconciled.children[1].attrs.get("name") == "attachments"
    assert all(child.kind is not IRNodeKind.SECTION for child in reconciled.children[1].children)


def test_reconcile_fold_sections_does_not_split_attachments_for_mixed_chapter_wrapper() -> None:
    section = IRNode(
        kind=IRNodeKind.SECTION,
        label="38",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Trailing direct section"),),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="8"),
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "attachments"},
                children=(section,),
            ),
        ),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(
                    IRNode(kind=IRNodeKind.CHAPTER, label="8"),
                    section,
                ),
            ),
        ),
    )

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, replay_fold)

    assert [child.attrs.get("name") for child in reconciled.children if child.kind is IRNodeKind.HCONTAINER] == [
        "attachments"
    ]
    attachments = next(child for child in reconciled.children if child.attrs.get("name") == "attachments")
    assert [child.label for child in attachments.children if child.kind is IRNodeKind.SECTION] == ["38"]


def test_reconcile_fold_sections_preserves_scoped_same_label_sections() -> None:
    body_section_4 = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Body-level section"),),
    )
    chapter_section_4 = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Chapter 3a section"),),
    )
    chapter_3a = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3a",
        children=(chapter_section_4,),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(body_section_4, chapter_3a),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(body_section_4,),
            ),
            chapter_3a,
        ),
    )

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, replay_fold)
    sections = extract_ir_sections(reconciled)

    assert "section:4" in sections
    assert "chapter:3a/section:4" in sections
    assert "Body-level section" in irnode_to_text(sections["section:4"])
    assert "Chapter 3a section" in irnode_to_text(sections["chapter:3a/section:4"])


def test_reconcile_fold_sections_preserves_unowned_scoped_section_in_local_run() -> None:
    body_section_4 = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Body-level section"),),
    )
    chapter_3a = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3a",
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="3"),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="4",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="Chapter 3a section"),),
            ),
            IRNode(kind=IRNodeKind.SECTION, label="5"),
        ),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(body_section_4, chapter_3a),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(body_section_4,),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3a",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="3"),
                    IRNode(kind=IRNodeKind.SECTION, label="5"),
                ),
            ),
        ),
    )

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, replay_fold)
    sections = extract_ir_sections(reconciled)

    assert "section:4" in sections
    assert "chapter:3a/section:4" in sections
    assert "Chapter 3a section" in irnode_to_text(sections["chapter:3a/section:4"])


def test_reconcile_fold_sections_does_not_preserve_synthetic_same_label_sections() -> None:
    body_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="59a",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Body-level section"),),
    )
    synthetic_section_a = IRNode(
        kind=IRNodeKind.SECTION,
        label="59a",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Synthetic 4a section"),),
    )
    synthetic_section_b = IRNode(
        kind=IRNodeKind.SECTION,
        label="59a",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Synthetic 11 section"),),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            body_section,
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4a",
                attrs={"lawvm_synthesized_container": "active_descendant"},
                children=(synthetic_section_a,),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="11",
                attrs={"lawvm_synthesized_container": "active_descendant"},
                children=(synthetic_section_b,),
            ),
        ),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(body_section,),
            ),
            IRNode(kind=IRNodeKind.CHAPTER, label="11"),
        ),
    )

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, replay_fold)
    sections = extract_ir_sections(reconciled)

    assert "section:59a" in sections
    assert "chapter:4a/section:59a" not in sections
    assert "chapter:11/section:59a" not in sections
    assert "Body-level section" in irnode_to_text(sections["section:59a"])


def test_reconcile_fold_sections_does_not_preserve_unowned_real_scoped_collision() -> None:
    body_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="59a",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Body-level section"),),
    )
    collided_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="59a",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Wrong chapter section"),),
    )
    local_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="22a",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Local chapter section"),),
    )
    chapter_4a = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="4a",
        children=(local_section, collided_section),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(body_section, chapter_4a),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(body_section,),
            ),
            IRNode(kind=IRNodeKind.CHAPTER, label="4a"),
        ),
    )

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, replay_fold)
    sections = extract_ir_sections(reconciled)

    assert "section:59a" in sections
    assert "chapter:4a/section:22a" in sections
    assert "chapter:4a/section:59a" not in sections
    assert "Body-level section" in irnode_to_text(sections["section:59a"])


def test_project_materialized_provisions_wrapper_unwraps_direct_sections_without_chapters() -> None:
    section_1 = IRNode(kind=IRNodeKind.SECTION, label="1")
    section_2 = IRNode(kind=IRNodeKind.SECTION, label="2")
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(section_1, section_2),
            ),
        ),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(section_1, section_2),
            ),
        ),
    )

    projected = project_materialized_provisions_wrapper(materialized, replay_fold)

    assert [child.kind for child in projected.children] == [
        IRNodeKind.SECTION,
        IRNodeKind.SECTION,
    ]
    assert [child.label for child in projected.children] == ["1", "2"]


def test_restore_replay_fold_repeal_placeholders_preserves_editorial_notice_slots() -> None:
    replay_placeholder = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        attrs={"lawvm_repeal_placeholder": "1", "lawvm_restore_materialized_stale_item_slot": "1"},
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3)"),),
    )
    unmarked_replay_placeholder = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        attrs={"lawvm_repeal_placeholder": "1"},
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3)"),),
    )
    materialized_notice = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3 kohta on kumottu L:lla 16.1.2026/45."),),
    )
    materialized_stale = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3) stale substantive text"),),
    )
    materialized_parent_missing = IRNode(kind=IRNodeKind.SUBSECTION, label="1")
    replay_parent_with_missing_placeholder = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        children=(replay_placeholder,),
    )

    assert _restore_replay_fold_repeal_placeholders(materialized_notice, replay_placeholder) is materialized_notice
    assert (
        _restore_replay_fold_repeal_placeholders(materialized_stale, unmarked_replay_placeholder)
        is materialized_stale
    )
    restored = _restore_replay_fold_repeal_placeholders(materialized_stale, replay_placeholder)
    projected_parent = _restore_replay_fold_repeal_placeholders(
        materialized_parent_missing,
        replay_parent_with_missing_placeholder,
    )

    assert restored is replay_placeholder
    assert projected_parent is materialized_parent_missing


def test_editorial_repeal_notice_substring_path_is_witnessed_not_silent() -> None:
    from lawvm.finland.replay_products import (
        EditorialRepealNoticeSubstringWitness,
        FI_EDITORIAL_REPEAL_NOTICE_SUBSTRING_RULE_ID,
        _content_is_editorial_repeal_notice,
    )

    # Typed-first: a replay-minted placeholder is recognised by the typed attr
    # and never touches the substring path (no witness emitted).
    typed_placeholder = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        attrs={"lawvm_repeal_placeholder": "1"},
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3) something"),),
    )
    typed_sink: list[EditorialRepealNoticeSubstringWitness] = []
    assert _content_is_editorial_repeal_notice(typed_placeholder, witness_sink=typed_sink) is True
    assert typed_sink == []

    # Residual substring path: no typed marker, but the consolidation text shows
    # "kumottu". The decision still fires (representation parity preserved) AND a
    # witness is recorded — the surface predicate is no longer silent.
    notice = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3 kohta on kumottu L:lla 16.1.2026/45."),),
    )
    sink: list[EditorialRepealNoticeSubstringWitness] = []
    assert _content_is_editorial_repeal_notice(notice, witness_sink=sink) is True
    assert len(sink) == 1
    witness = sink[0]
    assert witness.label == "3"
    assert "kumottu" in witness.clause_text
    assert witness.witness_rule_id == FI_EDITORIAL_REPEAL_NOTICE_SUBSTRING_RULE_ID

    # Negative: ordinary substantive text is not misfired and emits no witness.
    plain = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="4",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="4) substantive in-force text"),),
    )
    plain_sink: list[EditorialRepealNoticeSubstringWitness] = []
    assert _content_is_editorial_repeal_notice(plain, witness_sink=plain_sink) is False
    assert plain_sink == []


def test_restore_replay_fold_repeal_placeholders_threads_substring_witness() -> None:
    from lawvm.finland.replay_products import EditorialRepealNoticeSubstringWitness

    replay_placeholder = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        attrs={"lawvm_repeal_placeholder": "1", "lawvm_restore_materialized_stale_item_slot": "1"},
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3)"),),
    )
    materialized_notice = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="3",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="3 kohta on kumottu L:lla 16.1.2026/45."),),
    )
    sink: list[EditorialRepealNoticeSubstringWitness] = []
    result = _restore_replay_fold_repeal_placeholders(
        materialized_notice, replay_placeholder, witness_sink=sink
    )
    # Representation parity: still returns the materialized notice unchanged.
    assert result is materialized_notice
    assert len(sink) == 1
    assert sink[0].label == "3"


def test_project_materialized_provisions_wrapper_reparents_eid_sections_to_chapters() -> None:
    section_17g = IRNode(
        kind=IRNodeKind.SECTION,
        label="17g",
        attrs={"eId": "chp_3__sec_17g"},
    )
    section_2 = IRNode(kind=IRNodeKind.SECTION, label="2", attrs={"eId": "sec_2"})
    chapter_3 = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="3",
        attrs={"lawvm_synthesized_container": "active_descendant"},
        children=(IRNode(kind=IRNodeKind.NUM, text="3"),),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            chapter_3,
            IRNode(kind=IRNodeKind.HCONTAINER, children=(section_2, section_17g)),
        ),
    )
    replay_fold = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(section_2, section_17g),
            ),
        ),
    )

    projected = project_materialized_provisions_wrapper(materialized, replay_fold)

    projected_chapter = next(child for child in projected.children if child.kind is IRNodeKind.CHAPTER)
    assert [child.label for child in projected_chapter.children if child.kind is IRNodeKind.SECTION] == ["17g"]
    projected_wrapper = next(child for child in projected.children if child.kind is IRNodeKind.HCONTAINER)
    assert [child.label for child in projected_wrapper.children if child.kind is IRNodeKind.SECTION] == ["2"]


def test_2009_1182_materialized_text_keeps_operatives_outside_attachments() -> None:
    replay = replay_xml_for_test("2009/1182", mode="official_consolidation", quiet=True)

    text = replay.serialize_text()
    sections = extract_ir_sections(replay.materialized_state.ir)
    attachments = [
        child for child in replay.materialized_state.ir.children if child.attrs.get("name") == "attachments"
    ]

    assert "Opetus- ja kulttuuriministeriön suoritteista" in text
    assert "section:1" in sections
    assert attachments
    assert all(child.kind is not IRNodeKind.SECTION for child in attachments[0].children)
    findings = [
        finding
        for finding in replay.findings
        if finding.kind == "REPLAY.MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT"
    ]
    assert findings
    assert findings[0].detail.get("witness_rule_id") == "fi_materialized_attachments_wrapper_split_v1"
    assert findings[0].detail.get("moved_section_labels") == ("1", "2", "3", "4")


def test_fi_xml_ingest_merges_unlabeled_heading_section_into_empty_numbered_shell() -> None:
    body = etree.fromstring(
        """
        <body>
          <hcontainer name="statuteProvisionsWrapper">
            <section eId="sec_11"><num>11 §</num></section>
            <section eId="entryIntoForce">
              <heading>Voimaantulo</heading>
              <subsection><content>Tämä asetus tulee voimaan 1 päivänä tammikuuta.</content></subsection>
            </section>
          </hcontainer>
        </body>
        """.encode()
    )

    ir = fi_xml_to_ir_node(body)

    wrapper = ir.children[0]
    sections = [child for child in wrapper.children if child.kind is IRNodeKind.SECTION]
    assert len(sections) == 1
    section = sections[0]
    assert section.label == "11"
    assert section.attrs.get("lawvm_source_normalization_rule") == "fi_empty_numbered_section_shell_merge_v1"
    assert "Voimaantulo" in irnode_to_text(section)


def test_fi_xml_ingest_does_not_merge_unlabeled_heading_section_after_nonempty_section() -> None:
    body = etree.fromstring(
        """
        <body>
          <hcontainer name="statuteProvisionsWrapper">
            <section><num>10 §</num><heading>Already complete</heading></section>
            <section><heading>Standalone heading</heading></section>
          </hcontainer>
        </body>
        """.encode()
    )

    ir = fi_xml_to_ir_node(body)

    wrapper = ir.children[0]
    sections = [child for child in wrapper.children if child.kind is IRNodeKind.SECTION]
    assert len(sections) == 2
    assert sections[0].label == "10"
    assert sections[1].label is None


def test_2001_621_materialized_state_keeps_operatives_outside_attachments_and_merges_section_11() -> None:
    replay = replay_xml_for_test("2001/621", mode="official_consolidation", quiet=True)

    section_11 = replay.materialized_state.find_section("11")
    attachments = [
        child for child in replay.materialized_state.ir.children if child.attrs.get("name") == "attachments"
    ]
    root_subsections = [
        child for child in replay.materialized_state.ir.children if child.kind is IRNodeKind.SUBSECTION
    ]

    assert section_11 is not None
    section_11_text = " ".join(irnode_to_text(section_11).split())
    assert section_11_text.startswith("11 § Voimaantulo")
    assert "Tämä asetus tulee voimaan 3 päivänä tammikuuta 2002." in section_11_text
    assert not root_subsections
    assert attachments
    assert all(child.kind is not IRNodeKind.SECTION for child in attachments[0].children)


def test_replay_xml_2001_1488_materialized_state_keeps_chapter_scoped_first_sections() -> None:
    """Unnumbered topical heading wrappers must not displace chapter ``1 §``."""
    replay = replay_xml_for_test("2001/1488", mode="official_consolidation", quiet=True)

    sections = extract_ir_sections(replay.materialized_state.ir)

    assert "chapter:2/section:1" in sections
    assert "chapter:10/section:1" in sections
    assert "section:1" in sections
    assert "Perustajat Osuuskunnan voi perustaa" in irnode_to_text(
        sections["chapter:2/section:1"]
    )
    assert "Voimaantulo Tämän lain voimaantulosta" in irnode_to_text(sections["section:1"])


def test_replay_xml_1990_1295_materialized_state_drops_lone_unanchored_scoped_duplicate() -> None:
    """A lone unanchored scoped duplicate is still reconciled to the body section."""
    replay = replay_xml_for_test("1990/1295", mode="official_consolidation", quiet=True)

    sections = extract_ir_sections(replay.materialized_state.ir)

    assert "chapter:4a/section:59a" not in sections
    assert "section:59a" in sections


def test_2017_236_materialized_state_drops_expired_exact_temporary_moments() -> None:
    replay = replay_xml_for_test("2017/236", mode="official_consolidation", quiet=True)

    sections = extract_ir_sections(replay.materialized_state.ir)
    section_4_text = irnode_to_text(sections["section:4"])
    section_7_text = irnode_to_text(sections["section:7"])

    assert "Poiketen siitä, mitä 1 momentissa säädetään" not in section_4_text
    assert "Edellä 3 momentin 1 kohdassa tarkoitetussa kalastuksessa" not in section_4_text
    assert "Kun 3 §:n 4 momentissa, 4 §:n 4 momentissa" not in section_7_text
    assert "Lounais-Suomen elinvoimakeskukselle" in section_7_text


def test_1996_931_materialized_state_drops_expired_applicability_window_sections() -> None:
    lo_ops = []
    replay = replay_xml_for_test(
        "1996/931",
        mode="official_consolidation",
        quiet=True,
        lo_ops_out=lo_ops,
    )

    window_ops = [
        op
        for op in lo_ops
        if op.source.statute_id == "2007/171"
        and str(op.target) in {"chapter:6/section:43b", "chapter:6/section:43c"}
    ]
    assert {str(op.target): op.source.expires for op in window_ops} == {
        "chapter:6/section:43b": "2013-01-01",
        "chapter:6/section:43c": "2013-01-01",
    }

    sections = extract_ir_sections(replay.materialized_state.ir)
    assert "chapter:6/section:43b" not in sections
    assert "chapter:6/section:43c" not in sections
    assert "chapter:6/section:43a" in sections


def test_2018_1069_whole_section_replace_keeps_owned_body_after_temporary_overlay() -> None:
    replay = replay_xml_for_test("2018/1069", mode="official_consolidation", quiet=True)

    section_4 = replay.materialized_state.find_section("4")
    section_6 = replay.materialized_state.find_section("6")

    assert section_4 is not None
    assert section_6 is not None
    section_4_text = " ".join(irnode_to_text(section_4).split())
    section_6_text = " ".join(irnode_to_text(section_6).split())

    assert "Valtionavustusta yksityistien rakentamiseen on haettava" in section_4_text
    assert "Avustusta ei makseta ilman erityistä syytä" in section_4_text
    assert "Tiekunnan on vuosittain haettava avustuksen maksatusta" in section_6_text
    assert "Työllisyys-, kehittämis- ja hallintokeskus maksaa" in section_6_text


def test_2006_1096_temporary_subsection_replace_preserves_untouched_base_subsection() -> None:
    replay = replay_xml_for_test("2006/1096", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("1")

    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert "Valtioneuvoston jäsenille maksetaan tehtävän asianmukaisen hoitamisen vaatima palkkio." in text
    assert "vähennettynä viidellä prosentilla" in text


def test_2019_1239_permanent_subsection_replace_rebases_over_active_temporary_parent() -> None:
    replay = replay_xml_for_test("2019/1239", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("1")

    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert "alueen pilaantuneisuuden selvittämisen ja pilaantuneen alueen puhdistamisen järjestämisessä" in text
    assert "Avustusta ei myönnetä yritykselle" in text


def test_2014_1444_subsection_group_rebase_drops_expired_temporary_child() -> None:
    replay = replay_xml_for_test("2014/1444", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("3", "1")

    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert "Rahoitusta voidaan myöntää vain toimintaan, joka tapahtuu rahoitushakemuksen jättämisen jälkeen." in text
    assert "Edellä 1 §:n 6 momentissa tarkoitettua rahoitusta voidaan myöntää" not in text


def test_replay_xml_2016_258_section_3_matches_oracle_version_anchor() -> None:
    """official_consolidation should anchor 2016/258 to the oracle-version amendment date.

    Oracle version ``fin@20211199`` is keyed by amendment ``2021/1199``, whose
    own entry-into-force date is ``2021-12-31``. On that anchored date the
    temporary 3 § text from 2019/1458 (valid through 2021-12-31, exclusive
    expires 2022-01-01) is STILL IN FORCE, and the Finlex consolidation keeps
    it — both moments (the 7 §:n 2 mom sairaankuljetus moment and the
    6 §:n 1 mom omakustannus moment) are present, matching the oracle.
    """
    replay = pinned_replay("2016/258", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("3")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2021-12-31"

    assert text.count("Valtion maksuperustelain 7 §:n 2 momentissa") == 1
    assert text.count("Valtion maksuperustelain 6 §:n 1 momentissa") == 1


def test_replay_xml_2022_213_keeps_future_repeal_at_oracle_cutoff() -> None:
    replay = pinned_replay("2022/213", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("3")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2026-01-16"
    assert "kuluttajansuojalain (38/1978) 6 a luvun 11 §:n 2 momenttia;" in text
    assert "3) 4)" not in text


def test_replay_xml_2021_616_applies_corrigendum_without_collapsing_spacing() -> None:
    replay = pinned_replay("2021/616", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_node("section", "69", "chapter", "8")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert "Lain 2 § tulee kuitenkin voimaan vasta 1 päivänä tammikuuta 2023." in text
    assert "voimaavasta" not in text
    assert "voimaanvasta" not in text


def test_replay_xml_1982_91_repairs_source_sec_131_label() -> None:
    replay = pinned_replay("1982/91", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("1")
    assert section is not None
    assert replay.materialized_state.find_section("131") is None

    text = " ".join(irnode_to_text(section).split())
    assert "Maa-aineslain 2 §:n 1 momentin 2 kohdassa" in text
    assert "rakennuslupaa;" in text
    assert "muuta näihin verrattavaa lupaa tai suunnitelmaa." in text
    assert "Täten kumotaan 5 päivänä helmikuuta 1982 annetun maa-ainesasetuksen" not in text


def test_replay_xml_1973_36_materializes_live_missing_sections() -> None:
    """1973/36 must retain the live Finland bug-family sections end to end."""
    replay = pinned_replay(
        "1973/36",
        mode="official_consolidation",
        quiet=True,
    )

    def _section_text(chapter_label: str, section_label: str) -> str:
        section = replay.materialized_state.find_node("section", section_label, "chapter", chapter_label)
        assert section is not None, f"missing chapter {chapter_label} / section {section_label}"
        return " ".join(irnode_to_text(section).split())

    assert _section_text("3", "15").startswith("15 § Yksityisellä lasten päivähoidolla tarkoitetaan")
    assert _section_text("3", "16").startswith("16 § Jollei tässä laissa muuta säädetä")
    assert _section_text("3", "17").startswith("17 § Yksityisen lasten päivähoidon osalta")
    assert _section_text("3", "18").startswith("18 § Sosiaali- ja terveysalan lupa- ja valvontavirasto")
    assert _section_text("4", "27").startswith("27 § Kunnan on huolehdittava siitä")
    assert _section_text("4", "32").startswith("32 § Hallinto-oikeuden päätökseen ei saa hakea muutosta")


@pytest.mark.slow
def test_replay_xml_1987_1250_chapter_scoped_kumotaan_repeals_right_section() -> None:
    """A chapter-scoped kumotaan repeals the named chapter's section, not a homonym.

    2004/1320 declares "kumotaan ... 10 luvun 5 d ja 9 b § ...".  Section 5 d also
    exists in chapter 1, so an unscoped uncovered-kumotaan recovery resolved the
    bare label to the first 5 d in document order (chapter 1) and removed the wrong
    section, leaving the genuinely-repealed chapter:10/section:5d live.  The
    chapter-aware recovery must repeal chapter:10/section:5d and leave
    chapter:1/section:5d untouched.
    """
    replay = pinned_replay("1987/1250", mode="official_consolidation", quiet=True)

    repealed = replay.materialized_state.find_node("section", "5d", "chapter", "10")
    assert repealed is None, "10 luvun 5 d § must be repealed by 2004/1320"

    untouched = replay.materialized_state.find_node("section", "5d", "chapter", "1")
    assert untouched is not None, "1 luvun 5 d § must remain live"
    assert " ".join(irnode_to_text(untouched).split()).startswith("5 d § Pääomalainalle")


@pytest.mark.slow
def test_replay_xml_1996_931_temporary_whole_section_insert_snapshots_source_payload() -> None:
    """2023/1191 §43a is a temporary whole-section overlay, not stale fold text."""
    lo_ops: list[LegalOperation] = []
    replay = pinned_replay(
        "1996/931",
        mode="official_consolidation",
        quiet=True,
        lo_ops_out=lo_ops,
    )

    snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_section_43a"
        and op.source is not None
        and op.source.statute_id == "2023/1191"
    )
    assert snapshot.payload is not None
    snapshot_text = " ".join(irnode_to_text(snapshot.payload).split())
    assert "616/2021" in snapshot_text
    assert "vuosina 2004" not in snapshot_text

    section = replay.materialized_state.find_node("section", "43a", "chapter", "6")
    assert section is not None
    rendered = " ".join(irnode_to_text(section).split())
    assert "616/2021" in rendered
    assert "vuosina 2004" not in rendered


def test_replay_xml_2011_806_pure_kumotaan_subsection_keeps_chapter_scope() -> None:
    """2025/1104 repeals 8 luvun 21 §:n 3 momentti, not chapter 3's §21."""
    lo_ops: list[LegalOperation] = []
    replay = replay_xml_for_test(
        "2011/806",
        mode="legal_pit",
        quiet=True,
        lo_ops_out=lo_ops,
        as_of="2026-01-01",
    )

    pure_ops = [
        op
        for op in lo_ops
        if op.source is not None
        and op.source.statute_id == "2025/1104"
        and op.op_id.startswith("pure_subsec_repeal_21_3_")
    ]
    assert len(pure_ops) == 1
    assert pure_ops[0].target == LegalAddress(
        path=(("chapter", "8"), ("section", "21"), ("subsection", "3"))
    )
    assert pure_ops[0].payload is not None
    assert pure_ops[0].payload.attrs.get("lawvm_repeal_placeholder") == "1"

    chapter_3_section_21 = replay.materialized_state.find_section("21", "3")
    assert chapter_3_section_21 is not None
    chapter_3_subsection_3 = next(
        child
        for child in chapter_3_section_21.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "3"
    )
    assert chapter_3_subsection_3.attrs.get("lawvm_repeal_placeholder") is None
    assert replay.materialized_state.find_section("21", "8") is not None


@pytest.mark.slow
def test_replay_xml_1980_55_sec1_keeper_repeal_list_reaches_section_12f() -> None:
    """2015/521 sec_1 fallback must not truncate parent-owned targets after a conjunction."""
    lo_ops: list[LegalOperation] = []
    replay = replay_xml_for_test(
        "1980/55",
        mode="official_consolidation",
        quiet=True,
        lo_ops_out=lo_ops,
    )

    repeal_markers = [
        op
        for op in lo_ops
        if op.source is not None
        and op.source.statute_id == "2015/521"
        and op.target == LegalAddress(path=(("section", "12f"),))
        and op.payload is not None
        and op.payload.attrs.get("lawvm_repeal_placeholder") == "1"
    ]
    assert len(repeal_markers) == 1

    section = replay.materialized_state.find_node("section", "12f")
    assert section is not None
    assert section.attrs.get("lawvm_repeal_placeholder") == "1"
    assert " ".join(irnode_to_text(section).split()) == "12 f §"


def test_pure_kumotaan_subsection_injection_skips_unscoped_duplicate_sections() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="21",
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="3"),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="21",
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="3"),),
                    ),
                ),
            ),
        ),
    )

    lo_ops: list[LegalOperation] = []
    result = _inject_pure_kumotaan_subsection_repeal_ops(
        lo_ops,
        amendment_id="2025/1104",
        source_title="",
        kumotaan_subsection_map={"21": ["3"]},
        amendment_effective_date=dt.date(2025, 12, 15),
        state=ReplayState(ir=body),
        source_raw_text="kumotaan 21 §:n 3 momentti",
    )

    assert result.injected_count == 0
    assert lo_ops == []
    assert len(result.skipped_targets) == 1
    skipped = result.skipped_targets[0]
    assert skipped.reason == "ambiguous_duplicate_section_label_without_source_scope"
    assert skipped.section_label == "21"
    assert skipped.subsection_labels == ("3",)
    assert {address.path for address in skipped.candidate_paths} == {
        (("chapter", "3"), ("section", "21")),
        (("chapter", "8"), ("section", "21")),
    }


def test_replay_xml_1998_132_sparse_osalta_omission_repeals_branch_row() -> None:
    replay_meta: dict[str, object] = {}
    replay = replay_xml_for_test("1998/132", mode="legal_pit", quiet=True, replay_meta_out=replay_meta)
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_1_text = " ".join(irnode_to_text(sections["section:1"]).split())
    observations = cast(list[dict[str, object]], replay_meta.get("elaboration_observations") or [])

    assert "Oulunseutu (Oulussa)" in section_1_text
    assert "Pudasjärvi (st)" not in section_1_text
    assert any(
        isinstance(row, dict)
        and row.get("kind") == "ELAB.SPARSE_PARTIAL_SCOPE_ROW_OMISSION_REPEAL"
        and row.get("source_statute") == "1999/77"
        for row in observations
    )


def test_replay_xml_1953_317_routes_title_mismatch_to_vts_side_repeal_only() -> None:
    replay = pinned_replay("1953/317", mode="official_consolidation", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_5_text = " ".join(irnode_to_text(sections["chapter:2/section:5"]).split())

    assert "section:20" not in sections
    assert "Tasavallan presidentti määrää" in section_5_text


def test_replay_xml_1987_1203_preserves_jolloin_section_renumber_chain() -> None:
    replay = pinned_replay("1987/1203", mode="official_consolidation", quiet=True)

    section_11 = replay.materialized_state.find_section("11")
    section_12 = replay.materialized_state.find_section("12")

    assert section_11 is not None
    assert section_12 is not None

    text_11 = " ".join(irnode_to_text(section_11).split())
    text_12 = " ".join(irnode_to_text(section_12).split())

    assert text_11.startswith("11 § Tarkemmat määräykset")
    assert "valtiovarainministeriö" in text_11
    assert text_12.startswith("12 § Voimaantulo")
    assert "Tämä asetus tulee voimaan 1 päivänä tammikuuta 1988." in text_12


@pytest.mark.slow
def test_replay_xml_1968_360_handles_temporary_tax_year_window_without_crashing() -> None:
    replay = pinned_replay("1968/360", mode="official_consolidation", quiet=True, build_full_products=False)

    section = replay.materialized_state.find_section("46b")
    assert section is not None
    text = " ".join(irnode_to_text(section).split()).lower()
    assert "vuodelta 1982 toimitettavassa verotuksessa" in text
    assert "vuodelta 1983 toimitettavassa verotuksessa" in text


@pytest.mark.slow
def test_replay_xml_1966_722_expires_temporary_current_tax_year_section() -> None:
    replay = pinned_replay("1966/722", mode="official_consolidation", quiet=True)
    addr = LegalAddress(path=(("section", "9a"),))

    assert replay.timelines is not None
    timeline = replay.timelines[addr]
    version = timeline.versions[-1]
    assert version.variant_kind == "temporary"
    assert version.source is not None
    assert version.source.statute_id == "2000/877"
    assert version.source.expires == "2001-01-01"
    assert select_active_version(timeline, "2000-12-31") is not None
    assert select_active_version(timeline, "2001-01-01") is None


def test_replay_xml_1987_322_repeals_sections_10a_to_10f_after_2023_741() -> None:
    replay = pinned_replay("1987/322", mode="official_consolidation", quiet=True)
    for label in ("10a", "10b", "10c", "10d", "10e", "10f"):
        section = replay.materialized_state.find_section(label)
        assert section is not None
        assert section.attrs.get("lawvm_repeal_placeholder") == "1"
        assert not any(child.kind == IRNodeKind.SUBSECTION for child in section.children)


def test_replay_xml_1992_772_applies_1994_1281_replace_to_section_6() -> None:
    replay = pinned_replay("1992/772", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("6")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("6 § Terveyshaitan arvioimiseksi tarvittavat lisätiedot")
    assert "terveydensuojelulain (763/94)" in text
    assert "terveydensuojeluasetuksen (1280/94)" in text
    assert "terveydenhoitolain (469/65)" not in text


def test_replay_xml_1992_371_preserves_section_6_table_on_johd_replace() -> None:
    replay = pinned_replay(
        "1992/371",
        oracle_version="20100130",
        mode="official_consolidation",
        quiet=True,
    )

    section = replay.materialized_state.find_section("6")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert "mittanormaalijärjestelmästä annetun lain 3 §:n 1 momentissa" in text
    assert "mittaamisvälineiden vakaamisesta annetun lain 4 §:n 1 momentissa" not in text
    assert "Etuliite Tunnus Tekijä, jolla mittayksikkö tulee kerrotuksi" in text
    assert "jokto y 0,000 000 000 000 000 000 000 001 = 10 -24" in text


def test_build_amendment_bundle_2002_1000_does_not_collapse_dotted_kohta_repeal_to_section_repeal() -> None:
    bundle = build_amendment_bundle("2002/1000", "2007/180", "official_consolidation")
    all_ops = [op for group in bundle["groups"] for op in group["ops_final"]]
    assert "REPEAL 1 §" not in all_ops


def test_build_amendment_bundle_1954_243_routes_selaisena_typo_provenance() -> None:
    bundle = build_amendment_bundle("1954/243", "1978/676", "official_consolidation")

    assert bundle["route"]["should_apply"] is True
    assert bundle["route"]["reason"] == "references_parent"

    all_ops = [op for group in bundle["groups"] for op in group["ops_final"]]
    assert all_ops == ["REPLACE 1 luku 11 § 2 mom 1 kohta"]


def test_replay_xml_2002_1000_keeps_section_1_after_dotted_kohta_repeal_clause() -> None:
    replay = pinned_replay("2002/1000", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("1")
    assert section is not None
    assert section.attrs.get("lawvm_repeal_placeholder") != "1"


def test_build_amendment_bundle_1992_552_keeps_heading_and_subsection_scope_separate() -> None:
    bundle = build_amendment_bundle("1992/552", "2016/784", "official_consolidation")
    group8 = next(group for group in bundle["groups"] if group["target_norm"] == "8")

    # The johtolause says "8 §:n otsikko ja 3 momentti" — both the heading and
    # the subsection appear as separate ops at the raw stage.
    assert group8["ops_raw"] == ["REPEAL 8 § 1 mom", "REPLACE 8 § otsikko", "REPLACE 8 § 3 mom"]
    assert "REPLACE 8 §" not in group8["ops_final"]
    assert "REPLACE 8 § otsikko" in group8["ops_final"]


def test_build_amendment_bundle_1973_692_1979_229_keeps_valiotsake_subsection_insert() -> None:
    bundle = build_amendment_bundle("1973/692", "1979/229", "legal_pit")
    group19 = next(group for group in bundle["groups"] if group["target_norm"] == "19")

    assert "INSERT 19 § 2 mom" in bundle["compiled_ops"]
    assert "INSERT 19 § 2 mom" in group19["ops_final"]
    assert "RENUMBER 19 § 2 mom" in group19["ops_final"]
    assert "RENUMBER 19 § 3 mom" in group19["ops_final"]


def test_replay_xml_1992_552_preserves_section_8_subsection_4(
    replay_1992_552_finlex_oracle: ReplayResult,
) -> None:
    section = replay_1992_552_finlex_oracle.materialized_state.find_section("8")
    assert section is not None
    subsection_labels = [child.label for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert "4" in subsection_labels


def test_replay_xml_1992_552_updates_section_8_subsection_3_intro_text(
    replay_1992_552_finlex_oracle: ReplayResult,
) -> None:
    section = replay_1992_552_finlex_oracle.materialized_state.find_section("8")
    assert section is not None
    subsection = next(
        child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "3"
    )
    text = " ".join(irnode_to_text(subsection).split())

    assert text.startswith("Vero kohdistetaan sille kalenterikuukaudelle:")
    assert "Kalenterikuukaudeksi, jolta vero suoritetaan" not in text


def test_build_amendment_bundle_2000_755_rebinds_cited_version_owned_section_paths() -> None:
    bundle = build_amendment_bundle("2000/755", "2018/945", "official_consolidation")
    all_ops = [op for group in bundle["groups"] for op in group["ops_final"]]

    assert "REPLACE 6 luku 23 §" in all_ops
    assert "REPLACE 6 luku 24c §" in all_ops
    assert "REPLACE 6 luku 30b §" in all_ops
    assert "REPLACE 3 luku 34a §" in all_ops
    assert "REPLACE 30b §" not in all_ops


def test_build_amendment_bundle_2022_972_2024_70_parses_plural_section_marker() -> None:
    bundle = build_amendment_bundle("2022/972", "2024/70", "legal_pit")

    assert bundle["compiled_ops"] == [
        "REPLACE 2 luku 3 §",
        "REPLACE 4 luku 18 §",
        "REPLACE 4 luku 22 §",
        "REPLACE 5 luku 24 §",
        "REPLACE 5 luku 32 §",
        "REPLACE 5 luku 35 §",
        "REPLACE 9 luku 52 §",
        "INSERT 5 luku 34a §",
        "INSERT 6 luku 40a §",
    ]
    final_ops_by_target = {
        group["target_norm"]: group["ops_final"]
        for group in bundle["groups"]
    }
    assert final_ops_by_target["34a"] == ["INSERT 5 luku 34a §"]
    assert final_ops_by_target["40a"] == ["INSERT 6 luku 40a §"]


def test_replay_xml_1960_282_1965_667_materializes_bare_lisataan_momentti_inserts() -> None:
    replay = replay_xml_for_test("1960/282", mode="official_consolidation", quiet=True)
    section_by_label = {
        child.label: child
        for child in replay.materialized_state.ir.children
        if child.kind is IRNodeKind.SECTION
    }

    section_17_subsections = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section_by_label["17"].children
        if child.kind is IRNodeKind.SUBSECTION
    }
    assert set(section_17_subsections) >= {"1", "2", "3", "4", "5"}
    assert "Maata älköön 1 momentissa tarkoitetuin tavoin" in section_17_subsections["2"]
    assert "Aikaisemmassa maanmittaustoimituksessa erotettu yhteinen tiealue" in section_17_subsections["4"]
    assert "Uusjaossa voidaan aikaisemmassa maanmittaustoimituksessa erotetun muun yhteisen alueen" in section_17_subsections["5"]

    section_44_subsections = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section_by_label["44"].children
        if child.kind is IRNodeKind.SUBSECTION
    }
    assert set(section_44_subsections) == {"1", "2"}
    assert "alle tuhat markkaa" in section_44_subsections["1"]
    assert "Rajakaistan käyttöoikeuden menetyksestä" in section_44_subsections["2"]


def test_replay_xml_2000_755_applies_2018_945_to_cited_pending_version_paths() -> None:
    replay = pinned_replay("2000/755", mode="legal_pit", quiet=True, as_of="2020-01-02")

    sec24c = replay.materialized_state.find_node("section", "24c", "chapter", "6")
    sec30b = replay.materialized_state.find_node("section", "30b", "chapter", "6")
    sec34a = replay.materialized_state.find_node("section", "34a", "chapter", "3")

    assert sec24c is not None
    assert sec30b is not None
    assert sec34a is not None

    text24c = " ".join(irnode_to_text(sec24c).split())
    text30b = " ".join(irnode_to_text(sec30b).split())
    text34a = " ".join(irnode_to_text(sec34a).split())

    assert "Liikenne- ja viestintäviraston" in text24c
    assert "Liikenne- ja viestintäviraston" in text30b
    assert "Liikenne- ja viestintäviraston" in text34a
    assert "Liikenneviraston" not in text24c
    assert "Liikenneviraston" not in text30b
    assert "Liikenneviraston" not in text34a

    timeline_24c = replay.timelines[LegalAddress(path=(("chapter", "6"), ("section", "24c")))]
    timeline_30b = replay.timelines[LegalAddress(path=(("chapter", "6"), ("section", "30b")))]
    timeline_34a = replay.timelines[LegalAddress(path=(("chapter", "3"), ("section", "34a")))]

    assert any(
        version.source is not None
        and version.source.statute_id == "2018/945"
        and version.effective == "2019-01-01"
        for version in timeline_24c.versions
    )
    assert any(
        version.source is not None
        and version.source.statute_id == "2018/945"
        and version.effective == "2019-01-01"
        for version in timeline_30b.versions
    )
    assert any(
        version.source is not None
        and version.source.statute_id == "2018/945"
        and version.effective == "2019-01-01"
        for version in timeline_34a.versions
    )

    root_30b = replay.timelines.get(LegalAddress(path=(("section", "30b"),)))
    assert root_30b is None or select_active_version(root_30b, "2020-01-02") is None


def test_replay_xml_2005_623_applies_2018_947_after_separate_commencement_law() -> None:
    replay = replay_xml_for_test("2005/623", mode="legal_pit", quiet=True, as_of="2026-01-01")
    section = replay.materialized_state.find_section("2")

    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert "Liikenne- ja viestintävirastoa" in text
    assert "Väylävirastoa" in text
    assert "Liikenteen turvallisuusvirastoa" not in text

    resolved = [
        finding
        for finding in replay.findings
        if finding.kind == "TIME.RESOLVED_CONTINGENT_EFFECTIVE_DATE"
        and finding.source_statute == "2018/947"
    ]
    assert resolved
    assert resolved[0].detail.get("target_amendment") == "2018/947"
    assert resolved[0].detail.get("effective_date") == "2019-01-01"
    assert resolved[0].detail.get("witness_statute") == "2018/937"
    assert resolved[0].detail.get("witness_ref") == "2018/937/1"


def test_replay_xml_1991_1707_delays_scoped_2006_application_date() -> None:
    lo_ops: list[LegalOperation] = []
    replay = replay_xml_for_test(
        "1991/1707",
        mode="official_consolidation",
        quiet=True,
        lo_ops_out=lo_ops,
    )
    section = replay.materialized_state.find_section("4")

    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert "97 prosenttia yleisesti verovelvollisen merenkulkijan" in text
    assert "1 päivän tammikuuta 2007 ja 31 päivän joulukuuta 2009 väliseltä ajalta" not in text

    retimed_2006_targets = {
        str(op.target): op.source.effective
        for op in lo_ops
        if op.source is not None and op.source.statute_id == "2006/1322"
    }
    assert retimed_2006_targets["section:4"] == "2007-01-01"
    assert retimed_2006_targets["section:4/subsection:2"] == "2007-01-01"


def test_replay_xml_2011_1552_composes_pending_amendment_children() -> None:
    replay = pinned_replay("2011/1552", mode="official_consolidation", quiet=True)

    sec88 = replay.materialized_state.find_section("88")
    sec109 = replay.materialized_state.find_section("109")
    sec126 = replay.materialized_state.find_section("126")

    assert sec88 is not None
    assert sec109 is not None
    assert sec126 is not None

    text88 = " ".join(irnode_to_text(sec88).split())
    text109 = " ".join(irnode_to_text(sec109).split())
    text126 = " ".join(irnode_to_text(sec126).split())

    assert "3 §:n 1 ja 4–6 kohdassa" in text88
    assert "(1301/2014)" not in text88
    assert "mukaiseen" in text88
    assert "1, 2 ja 4–6 kohdassa" in text109
    assert "Rajavartiolaitoksen" in text126


def test_replay_xml_2014_938_composes_pending_amendment_children(
    replay_2014_938_finlex_oracle: ReplayResult,
) -> None:
    sec29 = replay_2014_938_finlex_oracle.materialized_state.find_section("29")
    sec41 = replay_2014_938_finlex_oracle.materialized_state.find_section("41")

    assert sec29 is not None
    assert sec41 is not None

    text29 = " ".join(irnode_to_text(sec29).split())
    text41 = " ".join(irnode_to_text(sec41).split())

    assert "8 tai 8 a §:n mukaan" in text29
    assert "tai hän on tullut oikeutetuksi" in text41
    assert "8 §:n 2 momentin tai 8 a §:n" in text41


def test_replay_xml_1972_484_composes_pending_amendment_after_renamed_base() -> None:
    replay = pinned_replay(
        "1972/484",
        oracle_version="20211061",
        mode="official_consolidation",
        quiet=True,
    )

    sec18 = replay.materialized_state.find_section("18")

    assert sec18 is not None
    text18 = " ".join(irnode_to_text(sec18).split())
    assert "1,2 miljardia euroa" in text18
    assert "600 miljoonaa erityisnosto-oikeutta" not in text18
    assert any(
        str(f.kind or "") == "APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET"
        and str(f.source_statute or "") == "2021/1061"
        and str(f.detail.get("target_amendment_id") or "") == "2005/493"
        for f in replay.findings or ()
    )


def test_replay_xml_2014_938_keeps_permanent_section_25_change_after_temporary_51_expires(
    replay_2014_938_finlex_oracle: ReplayResult,
) -> None:
    sec25 = replay_2014_938_finlex_oracle.materialized_state.find_section("25")
    sec51 = replay_2014_938_finlex_oracle.materialized_state.find_section("51")

    assert sec25 is not None
    assert sec51 is not None

    text25 = " ".join(irnode_to_text(sec25).split())
    text51 = " ".join(irnode_to_text(sec51).split())

    assert "kokonaan tai osittain" in text25
    assert "vuonna 2023 hyväksytään" not in text51


@pytest.mark.slow
def test_replay_xml_1940_378_keeps_voimaantulo_section_under_chapter_7_after_1994_318() -> None:
    replay = pinned_replay("1940/378", mode="official_consolidation", quiet=True)

    moved = replay.materialized_state.find_node("section", "61", "chapter", "7")
    assert moved is not None
    moved_text = " ".join(irnode_to_text(moved).split())
    assert moved_text.startswith("61 § Tämä laki tulee voimaan 1 päivänä elokuuta 1940")
    assert replay.materialized_state.find_section("73") is None


def test_replay_xml_1929_234_materializes_part_v_after_2001_1226(
    replay_1929_234_finlex_oracle: ReplayResult,
) -> None:
    replay = replay_1929_234_finlex_oracle
    sec109 = replay.materialized_state.find_section("109", chapter_num="1", part_num="5")
    sec110 = replay.materialized_state.find_section("110", chapter_num="1", part_num="5")
    sec111 = replay.materialized_state.find_section("111", chapter_num="1", part_num="5")
    sec112 = replay.materialized_state.find_section("112", chapter_num="1", part_num="5")
    sec113 = replay.materialized_state.find_section("113", chapter_num="1", part_num="5")
    sec142 = replay.materialized_state.find_section("142", chapter_num="5", part_num="5")

    assert sec109 is not None
    assert sec110 is not None
    assert sec111 is not None
    assert sec112 is not None
    assert sec113 is not None
    assert sec142 is not None
    assert "Suomen viranomainen voi myöntää 9 §:ssä tarkoitetun luvan" in " ".join(irnode_to_text(sec109).split())
    sec110_text = " ".join(irnode_to_text(sec110).split())
    assert sec110_text.startswith("110 §")
    assert "avioliitto aiotaan solmia Suomen viranomaisen edessä vieraassa valtiossa" in sec110_text
    assert "Tarkemmat säännökset tämän osan täytäntöönpanosta" in " ".join(irnode_to_text(sec142).split())


def test_replay_xml_1929_234_part_v_rebirth_does_not_repeal_unrelated_part_chapters(
    replay_1929_234_finlex_oracle: ReplayResult,
) -> None:
    """2001/1226 inserts part V; same-numbered chapters in parts II/IV must survive."""
    replay = replay_1929_234_finlex_oracle
    sec46 = replay.materialized_state.find_section("46", chapter_num="4", part_num="2")
    assert sec46 is not None
    sec46_text = " ".join(irnode_to_text(sec46).split())
    assert sec46_text.startswith("46 §")

    part4_ch1 = replay.materialized_state.find_node("chapter", "1", scope_kind="part", scope_label="4")
    part2_ch4 = replay.materialized_state.find_node("chapter", "4", scope_kind="part", scope_label="2")
    assert part4_ch1 is not None
    assert part2_ch4 is not None


@pytest.mark.slow
def test_replay_xml_1994_674_keeps_section_1_under_inserted_chapter_11a(
    replay_1994_674_finlex_oracle: ReplayResult,
) -> None:
    inserted = replay_1994_674_finlex_oracle.materialized_state.find_node("section", "1", "chapter", "11a")
    assert inserted is not None
    inserted_text = " ".join(irnode_to_text(inserted).split())
    assert inserted_text.startswith("1 § Nairobin yleissopimuksen soveltaminen Suomessa")


@pytest.mark.slow
def test_replay_xml_1994_674_replaces_section_6_without_stale_subsection_tail(
    replay_1994_674_finlex_oracle: ReplayResult,
) -> None:
    section = replay_1994_674_finlex_oracle.materialized_state.find_node("section", "6", "chapter", "16")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1"]

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("6 § Pelastuspalkkion suuruuden määrääminen")
    assert "Sama koskee muulla tavalla tehtyä sopimusta" not in text


@pytest.mark.slow
def test_replay_xml_1994_674_replaces_section_1_without_stale_subsection_tail(
    replay_1994_674_finlex_oracle: ReplayResult,
) -> None:

    section = replay_1994_674_finlex_oracle.materialized_state.find_node("section", "1", "chapter", "16")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1"]

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("1 § Määritelmät")
    assert "Sillä, joka vastoin aluksen päällikön nimenomaista ja oikeutettua kieltoa" not in text
    assert "Pelastuspalkkiota on vaadittaessa suoritettava myös silloin" not in text


@pytest.mark.slow
def test_replay_xml_1994_674_repeals_section_6_1_second_subsection_without_resurrection(
    replay_1994_674_finlex_oracle: ReplayResult,
) -> None:
    section = replay_1994_674_finlex_oracle.materialized_state.find_node("section", "1", "chapter", "6")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]
    assert subsections[1].attrs.get("lawvm_repeal_placeholder") == "1"

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("1 § Päällikön kansalaisuus")
    assert "Päällikön ja muiden päällystöön kuuluvien muista kelpoisuusvaatimuksista säädetään asetuksella." not in text


def test_replay_xml_1965_40_keeps_sections_12a_and_1a_in_their_explicit_insert_chapters(
    replay_1965_40_finlex_oracle: ReplayResult,
) -> None:
    sec12a = replay_1965_40_finlex_oracle.materialized_state.find_node("section", "12a", "chapter", "19")
    sec1a = replay_1965_40_finlex_oracle.materialized_state.find_node("section", "1a", "chapter", "25")

    assert sec12a is not None
    assert sec1a is not None
    assert "Jos on syytä epäillä, että pesän varat eivät riitä" in " ".join(irnode_to_text(sec12a).split())
    assert "Tässä luvussa tarkoitetaan:" in " ".join(irnode_to_text(sec1a).split())


def test_replay_xml_1965_40_materializes_sections_20_21_22_under_chapter_19(
    replay_1965_40_finlex_oracle: ReplayResult,
) -> None:
    sec20 = replay_1965_40_finlex_oracle.materialized_state.find_node("section", "20", "chapter", "19")
    sec21 = replay_1965_40_finlex_oracle.materialized_state.find_node("section", "21", "chapter", "19")
    sec22 = replay_1965_40_finlex_oracle.materialized_state.find_node("section", "22", "chapter", "19")

    assert sec20 is not None
    assert sec21 is not None
    assert sec22 is not None
    assert "Pesänselvittäjällä on oikeus saada pesän varoista" in " ".join(irnode_to_text(sec20).split())
    assert "Testamentin toimeenpanijalla on" in " ".join(irnode_to_text(sec21).split())
    assert "Oikeuden tai tuomarin päätökseen" in " ".join(irnode_to_text(sec22).split())


def test_replay_xml_nests_mixed_single_and_compound_letters_for_1997_1339_section_1(
    replay_1997_1339_finlex_oracle_full_products: ReplayResult,
) -> None:
    """Regression: the 2015/1752 amendment must not flatten repeated paragraph labels."""
    replay = replay_1997_1339_finlex_oracle_full_products

    for state in (replay.replay_fold_state, replay.materialized_state):
        subsection = state.find_node("subsection", "1", "section", "1")
        assert subsection is not None

        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == [
            "1.",
            "2.",
            "3.",
            "4.",
            "5.",
            "6.",
            "7.",
            "8.",
            "9.",
            "10.",
            "11.",
            "12.",
            "13.",
            "14.",
            "15.",
        ]

        para5 = next(p for p in paragraphs if p.label == "5.")
        assert [sp.label for sp in para5.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "aa", "ab", "b"]

        para6 = next(p for p in paragraphs if p.label == "6.")
        assert [sp.label for sp in para6.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "ba", "bb"]

        para10 = next(p for p in paragraphs if p.label == "10.")
        assert [sp.label for sp in para10.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d", "e", "f", "g"]

        para12 = next(p for p in paragraphs if p.label == "12.")
        assert [sp.label for sp in para12.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c"]


def test_replay_xml_keeps_inserted_moments_separate_for_2005_452_section_6() -> None:
    """Regression: 2012/317 inserts 6 § moments 2 and 3, not item 2 inside moment 1."""
    replay = pinned_replay("2005/452", mode="official_consolidation", quiet=True)

    section = replay.replay_fold_state.find_section("6", "2")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in subsections] == ["1", "2", "3"]

    second = next(sub for sub in subsections if sub.label == "2")
    second_text = " ".join(irnode_to_text(second).split())
    assert "Tiivistelmässä annettavia keskeisiä tietoja ovat esimerkiksi:" in second_text
    assert "lyhyt kuvaus arvopaperiin tehtävän sijoituksen" in second_text or "lyhyt kuvaus kyseiseen arvopaperiin tehtävän sijoituksen" in second_text


@pytest.mark.slow
def test_replay_xml_1967_550_section_8_preserves_subsection_1_repeal_in_export(
    replay_1967_550_legal_pit_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    oracle_lo_ops: list[LegalOperation] = []
    oracle = pinned_replay("1967/550", mode="official_consolidation", quiet=True, lo_ops_out=oracle_lo_ops)
    legal, legal_lo_ops = replay_1967_550_legal_pit_with_lo_ops

    assert any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "2"), ("section", "8"), ("subsection", "1"))
        for op in legal_lo_ops
    )
    assert any(
        op.target.path == (("chapter", "2"), ("section", "8"), ("subsection", "1"))
        and (
            op.action is StructuralAction.REPEAL
            or (op.payload is not None and op.payload.attrs.get("lawvm_repeal_placeholder") == "1")
        )
        for op in oracle_lo_ops
    )

    oracle_section = oracle.materialized_state.find_section("8", "2")
    assert oracle_section is not None
    oracle_subsections = [child for child in oracle_section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in oracle_subsections] == ["1", "2", "3", "4", "5", "6", "7"]
    assert oracle_subsections[0].attrs.get("lawvm_repeal_placeholder") == "1"

    legal_section = legal.materialized_state.find_section("8", "2")
    assert legal_section is not None
    legal_subsections = [child for child in legal_section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in legal_subsections] == ["2", "3", "4", "5", "6", "7"]


@pytest.mark.slow
def test_replay_xml_1967_550_section_8_keeps_distinct_sparse_tail_moments(
    replay_1967_550_legal_pit_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    replay, _lo_ops = replay_1967_550_legal_pit_with_lo_ops

    section = replay.materialized_state.find_section("8", "2")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    sixth = next(sub for sub in subsections if sub.label == "6")
    seventh = next(sub for sub in subsections if sub.label == "7")

    sixth_text = " ".join(irnode_to_text(sixth).split())
    seventh_text = " ".join(irnode_to_text(seventh).split())

    assert "Hakijan on suoritettava vahvistettu hakemusmaksu." in sixth_text
    assert "Hakemuksesta on myös suoritettava vahvistettu vuosimaksu" in sixth_text
    assert "Maksuvuosi lasketaan ensimmäisen kerran siitä päivästä" in seventh_text
    assert sixth_text != seventh_text


@pytest.mark.slow
def test_replay_xml_1967_550_section_70p_preserves_reborn_moment_slots(
    replay_1967_550_legal_pit_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    replay, lo_ops = replay_1967_550_legal_pit_with_lo_ops
    first_path = (("chapter", "9b"), ("section", "70p"), ("subsection", "1"))
    second_path = (("chapter", "9b"), ("section", "70p"), ("subsection", "2"))
    third_path = (("chapter", "9b"), ("section", "70p"), ("subsection", "3"))
    fourth_path = (("chapter", "9b"), ("section", "70p"), ("subsection", "4"))

    migration_events = {
        (event.from_address.path, event.to_address.path): event
        for event in replay.migration_events
        if event.source_statute == "2011/743" and "70p" in str(event.from_address)
    }
    assert (first_path, third_path) in migration_events
    assert (second_path, fourth_path) in migration_events
    assert {event.effective for event in migration_events.values()} == {"2011-06-17"}

    snapshots_2013 = {
        op.target.path: op
        for op in lo_ops
        if op.source is not None
        and op.source.statute_id == "2013/101"
        and op.op_id.startswith("snapshot_subsection_")
        and "70p" in str(op.target)
    }
    assert set(snapshots_2013) == {first_path, second_path, third_path, fourth_path}

    section = replay.materialized_state.find_section("70p", "9b")
    assert section is not None
    subsections = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section.children
        if child.kind is IRNodeKind.SUBSECTION and child.label
    }

    assert list(subsections) == ["1", "2", "3", "4"]
    assert subsections["1"].startswith("Tähän lakiin perustuvassa eurooppapatenttia koskevassa")
    assert subsections["2"].startswith("Edellä 66 §:ssä tarkoitetussa eurooppapatenttia koskevassa")
    assert subsections["3"].startswith("Jollei 70 h ja 70 n §:ssä tarkoitettu käännös")
    assert subsections["4"].startswith("Edellä 52 §:ssä tarkoitetussa mitättömyysoikeudenkäynnissä")


def test_replay_xml_1966_657_section_3_keeps_distinct_tail_moments() -> None:
    replay = pinned_replay("1966/657", mode="legal_pit", quiet=True)

    section = replay.materialized_state.find_section("3")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in subsections] == ["1", "2", "3", "4", "5"]

    third = next(sub for sub in subsections if sub.label == "3")
    fourth = next(sub for sub in subsections if sub.label == "4")
    fifth = next(sub for sub in subsections if sub.label == "5")

    third_text = " ".join(irnode_to_text(third).split())
    fourth_text = " ".join(irnode_to_text(fourth).split())
    fifth_text = " ".join(irnode_to_text(fifth).split())

    assert "rahoituskaudella 2023–2027" in third_text
    assert "rahoituskaudella 2023–2027" not in fourth_text
    assert "maidon viitemäärien ostamiseen" in fourth_text
    assert "Valtioneuvosto voi vastikkeetta luovuttaa" in fifth_text
    assert third_text != fourth_text


def test_replay_xml_1973_692_splits_cross_paragraph_sparse_item_payload() -> None:
    replay = replay_xml_for_test(
        "1973/692",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    )

    section = replay.materialized_state.find_section("3")
    assert section is not None

    subsections = {
        child.label: child
        for child in section.children
        if child.kind is IRNodeKind.SUBSECTION and child.label
    }
    second = subsections["2"]
    third = subsections["3"]

    second_item_labels = {
        child.label
        for child in second.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    }
    third_item_labels = {
        child.label
        for child in third.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    }

    assert "6" not in second_item_labels
    assert "10" not in second_item_labels
    assert {"6", "10"} <= third_item_labels


def test_replay_xml_recovers_1935_419_full_section_replace_for_1922_312_section_8() -> None:
    """Authority-citation lead-ins must not collapse 1935/419 to a fake 6 § replace."""
    replay = pinned_replay("1922/312", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("8")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]

    assert "kutsuntatoimiston sihteeriltä" in text
    assert "kutsuntatoimiston piiripäälliköltä" not in text
    assert "sekä alipäällystöltä" in text
    assert "Pääsemistä varten alipäällystön toimeen" not in text
    assert [sub.label for sub in subsections] == ["1", "2", "3"]

    third = next(sub for sub in subsections if sub.label == "3")
    third_text = " ".join(irnode_to_text(third).split())
    assert "sekä alipäällystöltä" in third_text


def test_replay_xml_applies_1935_141_passive_replacements_for_1922_148() -> None:
    """Historical ``on muutettava`` formulas must pass the live replay gate."""
    from tests.corpus_pin_helpers import replay_xml_for_test

    replay = replay_xml_for_test("1922/148", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("20")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert "Valtionrautateiden virka-alueet" in text
    assert "Liikennepaikat ovat yksikielisiä" in text
    assert "Valtionrautateiden viranomaisten virkakielestä" not in text


def test_replay_xml_2019_610_uses_section_specific_oracle_version_horizon() -> None:
    """fin@20240538 reflects §11/§11a from 2024/538 effective 2025-01-01."""
    from tests.corpus_pin_helpers import replay_xml_for_test

    replay = replay_xml_for_test("2019/610", mode="official_consolidation", quiet=True)

    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2025-01-01"
    section_11 = replay.materialized_state.find_section("11")
    section_11a = replay.materialized_state.find_section("11a")
    assert section_11 is not None
    assert section_11a is not None

    section_11_text = " ".join(irnode_to_text(section_11).split())
    section_11a_text = " ".join(irnode_to_text(section_11a).split())

    assert "Kansainvälinen järjestö ja kansainvälisen järjestön Suomessa sijaitseva toimipaikka" in section_11_text
    assert "Kansainvälisen järjestön Suomessa sijaitsevan toimipaikan merkittävät julkiset tehtävät" in section_11a_text


def test_replay_xml_2006_386_cited_asetus_version_replaces_chapter_three_sections() -> None:
    """2016/1021 points 4a-4c at 2011/81; earlier seasonal inserts expire."""
    replay = replay_xml_for_test("2006/386", mode="legal_pit", quiet=True)
    sections = extract_ir_sections(replay.materialized_state.ir)

    assert "chapter:2/section:4a" not in sections
    assert "chapter:3/section:5a" not in sections
    assert "chapter:3/section:5b" not in sections
    assert "chapter:3/section:5c" not in sections
    section_4a = sections["chapter:3/section:4a"]
    section_4b = sections["chapter:3/section:4b"]
    section_4c = sections["chapter:3/section:4c"]

    assert "joulukuun alun ja toukokuun lopun" in " ".join(irnode_to_text(section_4a).split())
    assert "joulukuun alun ja toukokuun lopun" in " ".join(irnode_to_text(section_4b).split())
    assert "joulukuun alusta toukokuun loppuun" in " ".join(irnode_to_text(section_4c).split())


def test_replay_xml_2010_290_cited_version_item_does_not_shadow_same_date_section_replace() -> None:
    """2017/1087 targets 3 § 4 kohta as in 2017/898; it must not hide 2017/898's §3."""
    replay = replay_xml_for_test(
        "2010/290",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_3 = sections["chapter:1/section:3"]

    text = " ".join(irnode_to_text(section_3).split())

    assert "jos edustaja toimii ainoastaan maksajan tai maksunsaajan puolesta" in text
    assert "samaan maksulaitoslain (297/2010) 5 §:n 8 kohdassa tarkoitettuun ryhmään" in text
    assert "kirjanpitolaissa (1336/1997) tarkoitettu emoyritys" not in text


def test_replay_xml_2003_1086_cited_version_item_keeps_current_snapshot_payload() -> None:
    """2025/1417 changes a cited-version item; its snapshot is current, not stale."""
    replay = replay_xml_for_test(
        "2003/1086",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_2 = sections["chapter:1/section:2"]

    text = " ".join(irnode_to_text(section_2).split())

    assert "ne työvoimaviranomaiset ja elinvoimakeskukset" in text
    assert "joiden toimialueeseen 1 kohdassa mainitut kunnat kokonaan tai osittain kuuluvat" in text


def test_replay_xml_2013_185_cited_version_group_keeps_new_inserted_items() -> None:
    """2025/1360 cites earlier item versions and also inserts new items in those sections."""
    replay = replay_xml_for_test(
        "2013/185",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)

    section_2_text = " ".join(irnode_to_text(sections["section:2"]).split())
    section_3_text = " ".join(irnode_to_text(sections["section:3"]).split())

    assert "15) ilmoitetaan asianomaiselle toisen Euroopan unionin jäsenvaltion toimivaltaiselle viranomaiselle täydennysveron tietoilmoitusta koskevat tiedot" in section_2_text
    assert "11) täydennysveron tietoilmoitusta koskevien tietojen ilmoittamisen automaattisella tietojenvaihdolla" in section_3_text


def _synthetic_cited_version_drop_products():
    """Build replay products containing a real cited-version snapshot op-drop.

    Two same-effective, same-target ops: the later amending act's item-scoped
    cited-version clause (kept op stream removes it) and the cited act's broader
    same-effective snapshot. ``build_replay_products`` runs the production drop
    filter, so ``dropped_cited_version_snapshots`` is populated by the real
    pipeline rather than constructed by hand.
    """
    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="base text for section 5"),),
            ),
        ),
    )
    ctx = StatuteContext(
        id="synthetic/cited-version-drop",
        title="Synthetic cited-version drop",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )
    current_payload = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="short current"),),
    )
    cited_payload = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=(
                    "much longer cited snapshot text that materially covers the "
                    "current one with extra body content"
                ),
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="extra covered subsection"),),
            ),
        ),
    )
    current_src = OperationSource(
        statute_id="2020/9",
        title="amend",
        enacted="2020-01-01",
        effective="2020-06-01",
        raw_text="muutetaan 5 §:n 2 kohta, sellaisena kuin se on laissa 123/2019",
    )
    cited_src = OperationSource(
        statute_id="2019/123",
        title="cited",
        enacted="2019-01-01",
        effective="2020-06-01",
        raw_text="muutetaan 5 §",
    )
    ops = [
        LegalOperation(
            op_id="current_cited_version_op",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=current_payload,
            source=current_src,
        ),
        LegalOperation(
            op_id="cited_snapshot_op",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=cited_payload,
            source=cited_src,
        ),
    ]
    products = build_replay_products(
        ctx=ctx,
        statute_id=ctx.id,
        replay_fold_state=ReplayState(ir=base_body),
        lo_ops_out=ops,
        as_of="2021-01-01",
    )
    return ctx, products


def test_cited_version_snapshot_drop_is_witnessed_through_assembly_projection_and_certificate() -> None:
    """A dropped cited-version legal-state op reaches the certificate consumer.

    Closes leak-ledger rank 2 sub-fix (a): the typed ``CitedVersionSnapshotDrop``
    must survive ``_normalize_product_trees`` (sever-point 1), surface as a typed
    projection finding (sever-point 2), and be readable by the certificate's
    findings ledger (sever-point 3) so a clean certificate can never sit silently
    over a real materialized-state op drop.
    """
    from lawvm.finland.replay_product_assembly import _normalize_product_trees
    from lawvm.finland.replay_product_projection import (
        ReplayProductProjectionRequest,
        project_replay_products,
    )
    from lawvm.tools.certificate_bundle import (
        BundleSpecError,
        _finding_row,
        build_diagnostic_registry_rows,
    )

    ctx, products = _synthetic_cited_version_drop_products()
    assert len(products.dropped_cited_version_snapshots) == 1

    # Sever-point 1: the assembly rebuild must NOT reset the witness to ().
    normalized = _normalize_product_trees(products)
    assert normalized.dropped_cited_version_snapshots == products.dropped_cited_version_snapshots

    # Sever-point 2: projection surfaces the drop as a typed observation finding.
    findings: list = []
    project_replay_products(
        ReplayProductProjectionRequest(
            ctx=ctx,
            products=normalized,
            parent_id=ctx.id,
            replay_findings=findings,
            replay_meta_out=None,
            replay_print=lambda _s: None,
            debug_enabled=False,
            debug_log=lambda *_a: None,
        )
    )
    cited_findings = [f for f in findings if f.kind == "REPLAY.CITED_VERSION_SNAPSHOT_DROP"]
    assert len(cited_findings) == 1
    finding = cited_findings[0]
    assert finding.role == "observation"
    assert finding.detail["op_id"] == "current_cited_version_op"
    assert tuple(finding.detail["target_path"]) == ("section:5",)
    assert finding.detail["witness_rule_id"] == "fi.replay.cited_version_ancestor_snapshot_drop"

    # Sever-point 3: the certificate's findings-ledger consumer reads it. The
    # production loop (build_certificate_bundle, §5.7) iterates result.findings,
    # rejects any kind not in the pinned registry, then ledgers it into a finding
    # row. Drive that exact consumer logic over the projected finding.
    registry_rows = build_diagnostic_registry_rows({"allows_estimated_dates": True})
    registered_codes = frozenset(r["code"] for r in registry_rows)
    assert finding.kind in registered_codes  # else the certificate would raise

    finding_rows: list = []
    for f in findings:
        if f.kind not in registered_codes:
            raise BundleSpecError(f"unexpected unregistered finding {f.kind!r}")
        finding_rows.append(
            _finding_row(
                diagnostic_code=f.kind,
                role=f.role,
                blocking=bool(f.blocking),
                address=None,
                date_range=[None, None],
                source_refs=[],
                phase=f.stage,
                detail=f.detail,
            )
        )
    ledgered = [r for r in finding_rows if r["diagnostic_code"] == "REPLAY.CITED_VERSION_SNAPSHOT_DROP"]
    assert len(ledgered) == 1
    assert ledgered[0]["detail"]["op_id"] == "current_cited_version_op"


def test_no_cited_version_drop_replay_stays_quiet() -> None:
    """A replay with no covered ancestor snapshot emits no drop finding."""
    from lawvm.finland.replay_product_assembly import _normalize_product_trees
    from lawvm.finland.replay_product_projection import (
        ReplayProductProjectionRequest,
        project_replay_products,
    )

    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="base"),),
            ),
        ),
    )
    ctx = StatuteContext(
        id="synthetic/no-cited-version-drop",
        title="Synthetic no drop",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )
    ops = [
        LegalOperation(
            op_id="plain_replace",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            payload=IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="ordinary amendment"),),
            ),
            source=OperationSource(
                statute_id="2020/9",
                title="amend",
                enacted="2020-01-01",
                effective="2020-06-01",
                raw_text="muutetaan 5 §",
            ),
        )
    ]
    products = build_replay_products(
        ctx=ctx,
        statute_id=ctx.id,
        replay_fold_state=ReplayState(ir=base_body),
        lo_ops_out=ops,
        as_of="2021-01-01",
    )
    assert products.dropped_cited_version_snapshots == ()

    normalized = _normalize_product_trees(products)
    findings: list = []
    project_replay_products(
        ReplayProductProjectionRequest(
            ctx=ctx,
            products=normalized,
            parent_id=ctx.id,
            replay_findings=findings,
            replay_meta_out=None,
            replay_print=lambda _s: None,
            debug_enabled=False,
            debug_log=lambda *_a: None,
        )
    )
    assert [f for f in findings if f.kind == "REPLAY.CITED_VERSION_SNAPSHOT_DROP"] == []


def test_replay_xml_2009_862_complete_section_replace_retires_old_transition_subsections() -> None:
    pathologies: list[object] = []
    replay = replay_xml_for_test(
        "2009/862",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
        source_pathologies_out=pathologies,
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_6_text = " ".join(irnode_to_text(sections["chapter:3/section:6"]).split())
    section_7_text = " ".join(irnode_to_text(sections["chapter:3/section:7"]).split())

    assert "Väylävirasto voi siirtää yksityiselle tai julkiselle palveluntarjoajalle" in section_6_text
    assert "Väylävirasto kantaa ja vastaa valtion puolesta" in section_7_text
    assert "Ratahallintokeskuksen, Tiehallinnon ja Merenkulkulaitoksen" not in section_6_text
    assert "Tämän lain voimaan tullessa vireillä olevat" not in section_6_text
    assert "Liikennevirastoon perustetaan liikenne- ja viestintäministeriön" not in section_7_text
    assert any(
        getattr(pathology, "code", "") == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        and getattr(pathology, "detail", {}).get("recovery_kind")
        == "section_snapshot_repeal_absent_complete_replacement_subsection"
        for pathology in pathologies
    )


def test_replay_xml_preserves_native_same_label_section_after_1958_496_renumber() -> None:
    """1999/1249 must preserve both the migrated 5 c § and the new native 5 b §."""
    replay = pinned_replay("1958/496", mode="legal_pit", quiet=True, stop_before="2004/697")

    section_5b = replay.materialized_state.find_section("5b")
    section_5c = replay.materialized_state.find_section("5c")

    assert section_5b is not None
    assert section_5c is not None

    text_5b = " ".join(irnode_to_text(section_5b).split())
    text_5c = " ".join(irnode_to_text(section_5c).split())

    assert "EU-luettelo" in text_5b
    assert "salassapitovelvollisuuden rikkomisesta" not in text_5b
    assert "salassapitovelvollisuuden rikkomisesta" in text_5c
    assert "EU-luettelo" not in text_5c


@pytest.mark.slow
def test_replay_xml_keeps_1994_1486_uncovered_sections_under_part_scoped_chapter() -> None:
    """Uncovered-body recovery must not emit bare root chapter:5 extras for 1994/1486."""
    replay = pinned_replay("1993/1501", mode="legal_pit", quiet=True, stop_before="1995/347")

    root_level_extras = {
        str(addr)
        for addr in replay.timelines
        if str(addr) in {
            "chapter:5/section:70",
            "chapter:5/section:74",
            "chapter:5/section:75",
            "chapter:5/section:76",
            "chapter:5/section:78",
            "chapter:5/section:79",
            "chapter:5/section:83",
            "chapter:5/section:88",
        }
    }
    part_scoped_recovered = {
        str(addr)
        for addr in replay.timelines
        if str(addr) in {
            "part:1/chapter:6/section:70",
            "part:1/chapter:7/section:74",
            "part:1/chapter:7/section:75",
            "part:1/chapter:7/section:76",
            "part:1/chapter:7/section:78",
            "part:1/chapter:7/section:79",
            "part:1/chapter:7/section:83",
            "part:1/chapter:9/section:88",
        }
    }

    assert root_level_extras == set()
    assert part_scoped_recovered == {
        "part:1/chapter:6/section:70",
        "part:1/chapter:7/section:74",
        "part:1/chapter:7/section:75",
        "part:1/chapter:7/section:76",
        "part:1/chapter:7/section:78",
        "part:1/chapter:7/section:79",
        "part:1/chapter:7/section:83",
        "part:1/chapter:9/section:88",
    }


@pytest.mark.slow
def test_replay_xml_1978_38_section_12_1_full_replace_does_not_preserve_stale_list_items() -> None:
    replay = pinned_replay("1978/38", mode="official_consolidation", quiet=True, stop_before="2022/697")

    section = replay.materialized_state.find_node("section", "1", "chapter", "12")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert "Kulutushyödykkeen välittäjän vastuu" in text
    assert "vastaa hyödykkeen hankkivalle kuluttajalle sopimuksen täyttämisestä" in text
    assert "sen paikkakunnan yleisessä alioikeudessa" not in text
    assert "väestökirjalain" not in text
    assert "Välittäjän vastuu ei rajoita kuluttajan oikeuksia" in text
    assert "Kiinteistönvälittäjän vastuusta on voimassa" in text


@pytest.mark.slow
def test_replay_xml_1978_38_preserves_chapter_12_sections_1a_and_1b_alongside_new_chapter_7_1a() -> None:
    replay = pinned_replay("1978/38", mode="official_consolidation", quiet=True)

    chapter7_1a = replay.materialized_state.find_node("section", "1a", "chapter", "7")
    chapter12_1a = replay.materialized_state.find_node("section", "1a", "chapter", "12")
    chapter12_1b = replay.materialized_state.find_node("section", "1b", "chapter", "12")

    assert chapter7_1a is not None
    assert chapter12_1a is not None
    assert chapter12_1b is not None

    chapter7_1a_text = " ".join(irnode_to_text(chapter7_1a).split())
    chapter12_1a_text = " ".join(irnode_to_text(chapter12_1a).split())
    chapter12_1b_text = " ".join(irnode_to_text(chapter12_1b).split())

    assert "Säännösten soveltamisen rajoitukset maksunlykkäyksinä myönnettävissä luotoissa" in chapter7_1a_text
    assert "Vahingonkorvausta koskeva kanneaika eräissä tapauksissa" in chapter12_1a_text
    assert "Suhde Vahingonkorvauslakiin ja muihin lakeihin" in chapter12_1b_text

def test_replay_xml_1962_184_applies_formula_and_body_prose_repeals() -> None:
    replay = pinned_replay(
        "1962/184",
        mode="official_consolidation",
        quiet=True,
    )

    assert replay.materialized_state.find_section("9") is None
    assert replay.materialized_state.find_section("17") is None


def test_replay_xml_1967_551_strips_inline_corrigendum_note_from_section_2() -> None:
    replay = pinned_replay(
        "1967/551",
        mode="official_consolidation",
        quiet=True,
    )

    section = replay.materialized_state.find_section("2")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert "Merkitty kohta oikaistu" not in text
    assert "Euroopan patenttisopimuksessa (SopS 8/96)" in text
    assert "tarkoitettua eurooppapatenttia koskeva hakemus" in text


def test_replay_xml_nests_simple_letter_subparagraphs_for_1997_1339_section_4(
    replay_1997_1339_finlex_oracle_full_products: ReplayResult,
) -> None:
    """Regression: section 4 must keep its simple letter families nested.

    This is the live 1997/1339 <- 2015/1752 mixed-scope tail family: the
    chapter-scoped 4 § group and the bare 4 § group must not both replay the
    same `7 kohta` tail.
    """
    replay = replay_1997_1339_finlex_oracle_full_products

    section = replay.replay_fold_state.find_section("4", "1")
    assert section is not None

    subsection = next(child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10."]

    para1 = next(p for p in paragraphs if p.label == "1.")
    assert [sp.label for sp in para1.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d"]

    para3 = next(p for p in paragraphs if p.label == "3.")
    assert [sp.label for sp in para3.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
    ]


def test_normalize_and_compile_ops_1997_1339_rejects_ambiguous_unscoped_fallback_insert() -> None:
    base_replay = pinned_replay(
        "1997/1339",
        mode="legal_pit",
        stop_before="2015/1752",
        quiet=True,
        build_full_products=False,
    )
    xml_bytes = get_corpus().read_source("2015/1752")
    assert xml_bytes is not None
    muutos_tree = etree.fromstring(xml_bytes)
    johto = get_johtolause(xml_bytes)
    title_el = muutos_tree.find(".//{*}docTitle")
    source_title = (
        etree.tostring(title_el, method="text", encoding="unicode").strip()
        if title_el is not None
        else "Unknown"
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=base_replay.replay_fold_state,
        amendment_id="2015/1752",
        source_title=source_title,
        used_preamble_body_fallback=False,
        parent_id="1997/1339",
        strict_profile=None,
    )

    descriptions = [op.description() for op in phase2.output]
    assert "INSERT 4 § 1 mom 7 kohta" not in descriptions
    assert "REPLACE 1 luku 4 § 1 mom" in descriptions
    assert any(
        finding.kind == "ELAB.REJECTED_OPERATION"
        and finding.detail.get("reason_code") == "ELAB.AMBIGUOUS_UNSCOPED_FALLBACK_INSERT_MULTI_SCOPE"
        for finding in phase2.finding_ledger
    )


def test_normalize_and_compile_ops_2007_626_rejects_single_payload_fallback_reuse() -> None:
    with redirect_stdout(StringIO()):
        base_replay = replay_xml_for_test(
            "1972/66",
            mode="legal_pit",
            stop_before="2007/626",
            quiet=True,
            build_full_products=False,
        )
    xml_bytes = get_corpus().read_source("2007/626")
    assert xml_bytes is not None
    muutos_tree = etree.fromstring(xml_bytes)
    johto = get_johtolause(xml_bytes)
    title_el = muutos_tree.find(".//{*}docTitle")
    source_title = (
        etree.tostring(title_el, method="text", encoding="unicode").strip()
        if title_el is not None
        else "Unknown"
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=base_replay.replay_fold_state,
        amendment_id="2007/626",
        source_title=source_title,
        used_preamble_body_fallback=False,
        parent_id="1972/66",
        strict_profile=None,
    )

    descriptions = [op.description() for op in phase2.output]
    assert "INSERT 2 luku 4 § 3 mom" in descriptions
    assert "INSERT 3 luku 14 § 6 mom" in descriptions
    assert "INSERT 4 § 6 mom" not in descriptions
    assert "INSERT 2 luku 4 § 6 mom" not in descriptions
    assert any(
        finding.kind == "ELAB.REJECTED_OPERATION"
        and finding.detail.get("reason_code") == "ELAB.FALLBACK_INSERT_SINGLE_PAYLOAD_ALREADY_OWNED"
        and finding.detail.get("description") == "INSERT 2 luku 4 § 6 mom"
        for finding in phase2.finding_ledger
    )


def test_replay_xml_1972_66_repeals_live_suffix_section_in_numeric_range() -> None:
    with redirect_stdout(StringIO()):
        replay = replay_xml_for_test("1972/66", mode="official_consolidation", quiet=True)
    addr = LegalAddress(path=(("chapter", "4"), ("section", "27a")))
    timeline = replay.timelines[addr]
    selected = select_active_version(timeline, "2020-12-11", query_type="in_force")

    assert replay.materialized_state.find_section("27a", chapter_num="4") is None
    assert selected is not None
    assert selected.content is None
    assert selected.source is not None
    assert selected.source.statute_id == "1982/684"
    assert selected.source.title == "Laki kansanterveyslain muuttamisesta"
    assert selected.source.enacted == "1982-09-17"
    assert selected.effective == "1984-01-01"
    assert "kumotaan" in selected.source.raw_text
    assert "4 luvun otsikko 27-39 §" in selected.source.raw_text


class _RangeExpansionState:
    def __init__(self, ir: IRNode) -> None:
        self.ir = ir

    def find_chapter(self, chapter: str | None) -> IRNode | None:
        if chapter is None:
            return None
        for child in self.ir.children:
            if child.kind is IRNodeKind.CHAPTER and str(child.label).lower() == chapter.lower():
                return child
        return None


def _chapter(label: str, section_labels: list[str]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.CHAPTER,
        label=label,
        text="",
        attrs={},
        children=tuple(
            IRNode(kind=IRNodeKind.SECTION, label=section, text="", attrs={}, children=())
            for section in section_labels
        ),
    )


def test_kumotaan_numeric_range_suffix_expansion_stays_in_resolved_chapter_scope() -> None:
    state = _RangeExpansionState(
        IRNode(
            kind=IRNodeKind.HCONTAINER,
            label="",
            text="",
            attrs={},
            children=(
                _chapter("9", ["67", "68", "69", "70"]),
                _chapter("9a", ["70a", "70c", "70e"]),
                _chapter("9b", ["70f", "70j"]),
            ),
        )
    )

    labels = _live_suffix_section_labels_for_numeric_kumotaan_ranges(
        "kumotaan 61 §:n 2 momentti, 63 §:n 3 momentti sekä 64 ja 67-70 §, muutetaan 26 §",
        state=state,  # type: ignore
    )

    assert labels == {}


def test_kumotaan_numeric_range_suffix_expansion_preserves_explicit_chapter_case() -> None:
    state = _RangeExpansionState(
        IRNode(
            kind=IRNodeKind.HCONTAINER,
            label="",
            text="",
            attrs={},
            children=(
                _chapter("4", ["27", "27a", "28", "39"]),
                _chapter("5", ["27b"]),
            ),
        )
    )

    labels = _live_suffix_section_labels_for_numeric_kumotaan_ranges(
        "kumotaan 4 luvun otsikko 27-39 §, muutetaan 1 §",
        state=state,  # type: ignore
    )

    assert labels == {"4": {"27a"}}


def test_replay_xml_1967_550_kumotaan_numeric_range_does_not_repeal_later_chapter_suffix_sections() -> None:
    replay = pinned_replay("1967/550", mode="official_consolidation", quiet=True)

    assert replay.materialized_state.find_section("70a", chapter_num="9a") is not None
    assert replay.materialized_state.find_section("70c", chapter_num="9a") is not None
    assert replay.materialized_state.find_section("70e", chapter_num="9a") is not None
    assert replay.materialized_state.find_section("70f", chapter_num="9b") is not None
    assert replay.materialized_state.find_section("70j", chapter_num="9b") is not None


def test_replay_xml_nests_simple_digit_subparagraphs_for_1997_108() -> None:
    """Regression: repeated digit families in 1997/108 must not stay as duplicate labels."""
    replay = pinned_replay("1997/108", mode="official_consolidation", quiet=True, build_full_products=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        section2 = state.find_section("2")
        assert section2 is not None
        subsection2 = next(child for child in section2.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
        paras2 = [child for child in subsection2.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paras2] == ["1", "2", "3", "4"]
        para4 = next(p for p in paras2 if p.label == "4")
        assert [sp.label for sp in para4.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["1", "2", "3", "4", "5", "6", "7"]

        section3 = state.find_section("3")
        assert section3 is not None
        subsection3 = next(child for child in section3.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
        paras3 = [child for child in subsection3.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paras3] == ["1."]
        para1 = paras3[0]
        assert [sp.label for sp in para1.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["2"]


def test_replay_xml_splits_digit_reset_subparagraph_run_for_2000_154() -> None:
    """Regression: 2000/154 must split the buried 5)-reset into its own paragraph."""
    replay = pinned_replay("2000/154", mode="official_consolidation", quiet=True, build_full_products=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        section = state.find_section("1", "1")
        assert section is not None
        subsection = next(
            child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        paragraphs = [child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == ["1", "2", "3", "4", "5", "6"]

        para4 = next(p for p in paragraphs if p.label == "4")
        para5 = next(p for p in paragraphs if p.label == "5")
        assert [sp.label for sp in para4.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d", "e"]
        assert [sp.label for sp in para5.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["a", "b"]


def test_replay_xml_nests_repeated_roman_subitems_for_2002_1244_section_21c(
    replay_2002_1244_finlex_oracle_full_products: ReplayResult,
) -> None:
    """Regression: malformed flat i/ii runs in 2018/1184 must nest under d/e in §21c."""
    replay = replay_2002_1244_finlex_oracle_full_products

    for state in (replay.replay_fold_state, replay.materialized_state):
        section = state.find_section("21c", "3")
        assert section is not None
        subsection = next(
            child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        paragraphs = [child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == ["a", "b", "c", "d", "e"]

        para_d = next(p for p in paragraphs if p.label == "d")
        assert [sp.label for sp in para_d.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]

        para_e = next(p for p in paragraphs if p.label == "e")
        assert [sp.label for sp in para_e.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]


def test_finlex_oracle_replay_uses_cutoff_materialization_spec() -> None:
    replay = pinned_replay("2009/953", mode="official_consolidation", quiet=True)

    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2020-01-01"


def test_replay_products_validate_cleanly_for_known_statute(
    replay_2009_953_legal_pit: ReplayResult,
) -> None:
    violations = validate_replay_products(
        replay_2009_953_legal_pit.ctx,
        replay_2009_953_legal_pit.products,
        deep_materialization_check=True,
    )

    assert violations == []


def test_replay_products_validate_cleanly_for_2004_1287_deep_materialization() -> None:
    replay = pinned_replay("2004/1287", mode="official_consolidation", quiet=True)

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=True,
    )

    assert violations == []


def test_cleanup_sourceless_base_merge_conflicts_keeps_base_and_stronger_later_lineage() -> None:
    versions = [
        ProvisionVersion(
            effective="0000-00-00",
            enacted="0000-00-00",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko"),
            source=None,
        ),
        ProvisionVersion(
            effective="0000-00-00",
            enacted="0000-00-00",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko"),
            source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
        ),
        ProvisionVersion(
            effective="0000-00-00",
            enacted="0000-00-00",
            content=IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                text="5 § Otsikko lisäys Tässä laissa tarkoitetaan jotain enemmän.",
            ),
            source=OperationSource(statute_id="2002/1", effective="2002-01-01"),
        ),
    ]

    cleaned = _cleanup_sourceless_base_merge_conflicts(versions)

    assert _FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE == "fi_sourceless_base_merge_cleanup_v1"
    assert len(cleaned) == 2
    assert cleaned[0].source is None
    assert cleaned[1].source is not None
    assert cleaned[1].source.statute_id == "2002/1"


def test_cleanup_sourceless_base_merge_conflicts_preserves_later_tombstone() -> None:
    tombstone_source = OperationSource(statute_id="2016/773", effective="2017-01-01")
    versions = [
        ProvisionVersion(
            effective="0000-00-00",
            enacted="1993-12-30",
            content=IRNode(kind=IRNodeKind.SECTION, label="218", text="218 § Base text"),
            source=None,
        ),
        ProvisionVersion(
            effective="2013-12-01",
            enacted="2013-11-08",
            content=IRNode(kind=IRNodeKind.SECTION, label="218", text="218 § Later text"),
            source=OperationSource(statute_id="2013/785", effective="2013-12-01"),
        ),
        ProvisionVersion(
            effective="2017-01-01",
            enacted="2016-09-09",
            content=None,
            source=tombstone_source,
        ),
    ]

    cleaned = _cleanup_sourceless_base_merge_conflicts(versions)

    assert cleaned[-1].content is None
    assert cleaned[-1].source == tombstone_source


def test_cleanup_sourceless_base_merge_conflicts_is_noop_without_sourceless_base() -> None:
    versions = [
        ProvisionVersion(
            effective="2001-01-01",
            enacted="2001-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko"),
            source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
        ),
        ProvisionVersion(
            effective="2002-01-01",
            enacted="2002-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko Tässä laissa tarkoitetaan."),
            source=OperationSource(statute_id="2002/1", effective="2002-01-01"),
        ),
    ]

    cleaned = _cleanup_sourceless_base_merge_conflicts(versions)

    assert cleaned == versions


@pytest.mark.slow
def test_replay_xml_surfaces_migration_events_for_renumbered_statute() -> None:
    replay = pinned_replay(
        "2017/320",
        mode="legal_pit",
        quiet=True,
        build_full_products=False,
        stop_before="2019/484",
    )

    assert replay.migration_events
    assert any(event.kind == "renumber" for event in replay.migration_events)
    assert len(replay.identity_ledger) == len(replay.migration_events)


def test_replay_xml_can_skip_full_products_for_fast_bench() -> None:
    replay = pinned_replay(
        "2009/953",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    )

    assert replay.products.replay_fold_state is replay.replay_fold_state
    assert replay.products.materialized_state == replay.replay_fold_state
    assert replay.products.timelines is None
    assert replay.materialization_spec is None


def test_fold_timeline_backfill_materializes_restructure_renumbered_sections() -> None:
    """Fold-owned sections must survive PIT when only payload-less RENUMBER LOs exist."""
    from lawvm.core.provenance import MigrationEvent
    from lawvm.finland.replay_fold_timeline_backfill import FI_REPLAY_FOLD_TIMELINE_BACKFILL_RULE_ID

    def _section(label: str, heading: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(
                IRNode(kind=IRNodeKind.NUM, text=f"{label} §"),
                IRNode(kind=IRNodeKind.HEADING, text=heading),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text=f"Body {label}"),),
                ),
            ),
        )

    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="V OSA"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="4",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="4 luku"),
                            _section("1", "Old one"),
                            _section("2", "Old two"),
                        ),
                    ),
                ),
            ),
        ),
    )
    fold_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="5",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="V OSA"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="25",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="25 luku"),
                            _section("209", "Renumbered one"),
                            _section("210", "Renumbered two"),
                        ),
                    ),
                ),
            ),
        ),
    )
    ctx = StatuteContext(
        id="synthetic/fold-backfill",
        title="Synthetic fold backfill",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )
    source = OperationSource(
        statute_id="2020/1256",
        title="Restructure amendment",
        enacted="2021-02-01",
        effective="2021-02-01",
    )
    lo_ops = [
        LegalOperation(
            op_id="restructure_renumber_chapter",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("part", "5"), ("chapter", "4"))),
            destination=LegalAddress(path=(("part", "5"), ("chapter", "25"))),
            source=source,
            group_id="finland-restructure:2020/1256",
        ),
        LegalOperation(
            op_id="restructure_renumber_section_1",
            sequence=0,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("part", "5"), ("chapter", "4"), ("section", "1"))),
            destination=LegalAddress(path=(("part", "5"), ("chapter", "4"), ("section", "209"))),
            source=source,
            group_id="finland-restructure:2020/1256",
        ),
    ]
    migration_events = (
        MigrationEvent(
            event_id="mig:ch4-ch25",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "5"), ("chapter", "4"))),
            to_address=LegalAddress(path=(("part", "5"), ("chapter", "25"))),
            effective="2021-02-01",
            source_statute="2020/1256",
        ),
    )
    products = build_replay_products(
        ctx=ctx,
        statute_id="synthetic/fold-backfill",
        replay_fold_state=ReplayState(ir=fold_body),
        lo_ops_out=lo_ops,
        migration_events=migration_events,
    )
    addr_209 = LegalAddress(path=(("part", "5"), ("chapter", "25"), ("section", "209")))
    addr_210 = LegalAddress(path=(("part", "5"), ("chapter", "25"), ("section", "210")))

    assert products.timelines is not None
    assert addr_209 in products.timelines
    assert addr_210 in products.timelines
    assert products.materialized_state.find_section("209", "25", "5") is not None
    assert products.materialized_state.find_section("210", "25", "5") is not None
    assert any(record.address == str(addr_209) for record in products.fold_timeline_backfills)
    assert all(
        record.witness_rule_id == FI_REPLAY_FOLD_TIMELINE_BACKFILL_RULE_ID
        for record in products.fold_timeline_backfills
    )


def test_fold_timeline_backfill_distinguishes_same_label_sections_across_chapters() -> None:
    """Same section label in different chapters must each get a distinct backfill op.

    Finnish chaptered statutes repeat section labels (``1 §`` exists in every
    chapter). A bare-label op_id collides the two distinct sections, so the
    ``existing_op_ids`` dedup drops the second and its fold-owned content
    silently vanishes from PIT. The op_id must be keyed by the full address.
    """
    from lawvm.finland.replay_fold_timeline_backfill import _fold_backfill_op_id

    def _section(label: str, heading: str) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(
                IRNode(kind=IRNodeKind.NUM, text=f"{label} §"),
                IRNode(kind=IRNodeKind.HEADING, text=heading),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.CONTENT, text=f"Body {heading}"),),
                ),
            ),
        )

    # Base IR is empty of these chapters: the fold carries both chapter-1
    # sections, and timeline compilation has no authority for either.
    base_body = IRNode(kind=IRNodeKind.BODY, children=())
    fold_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 luku"),
                    _section("1", "Chapter 1 section 1"),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="2 luku"),
                    _section("1", "Chapter 2 section 1"),
                ),
            ),
        ),
    )

    addr_ch1 = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    addr_ch2 = LegalAddress(path=(("chapter", "2"), ("section", "1")))

    # The OLD bare-label op_id collides the two distinct sections; the NEW
    # address-keyed op_id distinguishes them.
    old_op_id_ch1 = f"snapshot_section_{'1'}_fold_timeline_backfill"
    old_op_id_ch2 = f"snapshot_section_{'1'}_fold_timeline_backfill"
    assert old_op_id_ch1 == old_op_id_ch2  # collision the dedup acted on
    new_op_id_ch1 = _fold_backfill_op_id(addr_ch1)
    new_op_id_ch2 = _fold_backfill_op_id(addr_ch2)
    assert new_op_id_ch1 != new_op_id_ch2
    assert new_op_id_ch1.startswith("snapshot_section_")
    assert new_op_id_ch2.startswith("snapshot_section_")
    # Deterministic / stable.
    assert _fold_backfill_op_id(addr_ch1) == new_op_id_ch1

    ctx = StatuteContext(
        id="synthetic/fold-backfill-collision",
        title="Synthetic fold backfill collision",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )

    products = build_replay_products(
        ctx=ctx,
        statute_id="synthetic/fold-backfill-collision",
        replay_fold_state=ReplayState(ir=fold_body),
        lo_ops_out=[],
    )

    # Both same-label sections survive PIT materialization with distinct
    # timelines and distinct backfill records.
    assert products.timelines is not None
    assert addr_ch1 in products.timelines
    assert addr_ch2 in products.timelines
    assert products.materialized_state.find_section("1", "1") is not None
    assert products.materialized_state.find_section("1", "2") is not None
    backfilled_addresses = {record.address for record in products.fold_timeline_backfills}
    assert str(addr_ch1) in backfilled_addresses
    assert str(addr_ch2) in backfilled_addresses


def test_fold_timeline_backfill_reuses_preview_timelines_when_no_backfills(monkeypatch) -> None:
    import lawvm.core.timeline as timeline_mod
    import lawvm.finland.replay_fold_timeline_backfill as backfill_mod

    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="base"),),
            ),
        ),
    )
    ctx = StatuteContext(
        id="synthetic/no-fold-backfill",
        title="Synthetic no fold backfill",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )
    compile_calls = 0
    real_compile_timelines = timeline_mod.compile_timelines

    def counting_compile_timelines(*args, **kwargs):
        nonlocal compile_calls
        compile_calls += 1
        return real_compile_timelines(*args, **kwargs)

    monkeypatch.setattr(timeline_mod, "compile_timelines", counting_compile_timelines)
    monkeypatch.setattr(backfill_mod, "compile_timelines", counting_compile_timelines)

    products = build_replay_products(
        ctx=ctx,
        statute_id=ctx.id,
        replay_fold_state=ReplayState(ir=base_body),
        lo_ops_out=[],
    )

    assert compile_calls == 1
    assert products.fold_timeline_backfills == ()
    assert products.materialized_state.find_section("1") is not None


def test_build_replay_products_reuses_static_lo_preparation_cache(monkeypatch) -> None:
    import lawvm.finland.replay_products as replay_products_mod

    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="base"),),
            ),
        ),
    )
    ctx = StatuteContext(
        id="synthetic/static-lo-prep-cache",
        title="Synthetic static LO prep cache",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )
    source = OperationSource(
        statute_id="2020/1",
        title="Synthetic insert",
        enacted="2020-01-01",
        effective="2020-01-01",
        raw_text="lisätään 1 §:n 1 kohta, sellaisena kuin se on laissa 1/2020",
    )
    lo_ops = [
        LegalOperation(
            op_id="insert_section_2",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="inserted"),),
            ),
            source=source,
        )
    ]
    real_drop = replay_products_mod._drop_cited_version_item_ancestor_snapshots
    drop_calls = 0

    def counting_drop(ops):
        nonlocal drop_calls
        drop_calls += 1
        return real_drop(ops)

    monkeypatch.setattr(
        replay_products_mod,
        "_drop_cited_version_item_ancestor_snapshots",
        counting_drop,
    )
    cache: dict[object, object] = {}

    for as_of in ("2020-01-01", "2021-01-01"):
        products = build_replay_products(
            ctx=ctx,
            statute_id=ctx.id,
            replay_fold_state=ReplayState(ir=base_body),
            lo_ops_out=lo_ops,
            as_of=as_of,
            fold_backfill_preview_cache=cache,
        )
        assert products.materialized_state.find_section("1") is not None

    assert drop_calls == 1


def test_build_replay_products_reuses_static_temporal_and_base_date_cache(monkeypatch) -> None:
    import lawvm.finland.metadata as metadata_mod
    import lawvm.finland.replay_products as replay_products_mod

    base_body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="base"),),
            ),
        ),
    )
    ctx = StatuteContext(
        id="synthetic/static-temporal-cache",
        title="Synthetic static temporal cache",
        base_ir=base_body,
        base_xml_bytes=b"<body/>",
    )
    source = OperationSource(
        statute_id="2020/1",
        title="Synthetic temporary insert",
        enacted="2020-01-01",
        effective="2020-01-01",
        expires="2021-01-01",
    )
    lo_ops = [
        LegalOperation(
            op_id="insert_section_2",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(
                kind=IRNodeKind.SECTION,
                label="2",
                children=(IRNode(kind=IRNodeKind.CONTENT, text="inserted"),),
            ),
            source=source,
            group_id="synthetic-temporary-insert",
        )
    ]
    temporal_calls = 0
    issue_date_calls = 0
    real_temporal_events_from_lo_ops = replay_products_mod._temporal_events_from_lo_ops

    def counting_temporal_events_from_lo_ops(*args, **kwargs):
        nonlocal temporal_calls
        temporal_calls += 1
        return real_temporal_events_from_lo_ops(*args, **kwargs)

    def counting_statute_issue_date(tree):
        nonlocal issue_date_calls
        issue_date_calls += 1
        return None

    monkeypatch.setattr(
        replay_products_mod,
        "_temporal_events_from_lo_ops",
        counting_temporal_events_from_lo_ops,
    )
    monkeypatch.setattr(metadata_mod, "_statute_issue_date", counting_statute_issue_date)
    cache: dict[object, object] = {}

    for as_of in ("2020-06-01", "2020-12-31"):
        products = build_replay_products(
            ctx=ctx,
            statute_id=ctx.id,
            replay_fold_state=ReplayState(ir=base_body),
            lo_ops_out=lo_ops,
            as_of=as_of,
            expires_as_of=as_of,
            fold_backfill_preview_cache=cache,
        )
        assert products.materialized_state.find_section("1") is not None

    assert temporal_calls == 1
    assert issue_date_calls == 1


@pytest.mark.slow
def test_replay_xml_2017_320_emits_relabel_section_snapshots_at_live_paths() -> None:
    """2019/371 section relabels must snapshot at live IR paths, not amendment frames."""
    from lawvm.finland.restructure_plan_replay import (
        FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_RULE_ID,
    )

    lo_ops: list[LegalOperation] = []
    pinned_replay(
        "2017/320",
        quiet=True,
        lo_ops_out=lo_ops,
        stop_before="2020/1256",
    )
    snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_section_209_restructure_2019/371"
    )
    assert snapshot.witness_rule_id == FI_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_RULE_ID
    assert str(snapshot.target) == "part:5/chapter:4/section:209"
    assert snapshot.payload is not None


@pytest.mark.slow
def test_replay_xml_2017_320_materializes_part_5_chapter_25_sections() -> None:
    """Regression: restructure renumber waves must not drop fold-owned ch25 sections from PIT."""
    replay = pinned_replay("2017/320", mode="legal_pit", quiet=True)
    fold_sections = extract_ir_sections(replay.replay_fold_state.ir)
    materialized_sections = extract_ir_sections(replay.materialized_state.ir)
    ch25_keys = sorted(
        key for key in fold_sections if key.startswith("part:5/chapter:25/section:")
    )
    assert ch25_keys, "fold must carry part:5/chapter:25 sections"
    assert all(key in materialized_sections for key in ch25_keys)
    # Upstream relabel snapshots at part:5/chapter:4 own timeline authority for the
    # 209-215 family before 2020/1256 moves chapter 4 into chapter 25. Fold backfill
    # must not be the graft that materializes ch25/209.
    assert not any(
        finding.kind == "REPLAY.FOLD_TIMELINE_BACKFILL"
        and "part:5/chapter:25/section:209" in str(finding.detail.get("address") or "")
        for finding in replay.findings
    )


@pytest.mark.slow
def test_replay_xml_2019_371_johto_guard_skips_omission_shell_uncovered_recovery() -> None:
    """2019/371 omission-only destination shells must not be inserted via uncovered recovery."""
    replay_meta: dict[str, object] = {}
    replay = pinned_replay(
        "2017/320",
        quiet=True,
        stop_before="2020/1256",
        replay_meta_out=replay_meta,
    )
    audits = cast(list[dict[str, object]], replay_meta.get("uncovered_body_candidate_audits") or [])
    guarded = {
        str(row.get("target_section"))
        for row in audits
        if isinstance(row, dict)
        and row.get("source_statute") == "2019/371"
        and row.get("reason") == "johto_guard"
        and row.get("disposition") == "SKIP"
    }
    assert {"209", "210", "211", "212"}.issubset(guarded)
    findings = [
        finding
        for finding in replay.findings
        if finding.kind == "APPLY.UNCOVERED_BODY_PREAMBLE_GUARD"
        and finding.source_statute == "2019/371"
        and str((finding.detail or {}).get("target_section")) in {"209", "210", "211", "212"}
    ]
    assert len(findings) == 4
    assert all(str((finding.detail or {}).get("reason")) == "johto_guard" for finding in findings)


@pytest.mark.slow
def test_replay_xml_emits_payloaded_part_snapshot_for_2020_1256() -> None:
    lo_ops: list[LegalOperation] = []

    pinned_replay(
        "2017/320",
        quiet=True,
        build_full_products=False,
        lo_ops_out=lo_ops,
        stop_before="2021/91",
    )

    snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_part_6"
        and op.source is not None
        and op.source.statute_id == "2020/1256"
    )

    assert snapshot.payload is not None


def test_replay_xml_expires_2021_984_temporary_21b_section(
    replay_1999_488_legal_pit: ReplayResult,
) -> None:
    addr = LegalAddress(path=(("chapter", "5"), ("section", "21b")))

    assert replay_1999_488_legal_pit.timelines is not None
    assert addr in replay_1999_488_legal_pit.timelines
    assert replay_1999_488_legal_pit.timelines[addr].versions[-1].expires == "2022-01-31"
    assert select_active_version(replay_1999_488_legal_pit.timelines[addr], "2025-01-01") is None
    assert replay_1999_488_legal_pit.replay_fold_state.find_section("21b") is not None
    assert replay_1999_488_legal_pit.materialized_state.find_section("21b") is None


def test_replay_xml_expires_2020_292_temporary_99a_section() -> None:
    replay = pinned_replay("2015/410", mode="legal_pit", quiet=True)
    addr = LegalAddress(path=(("part", "5"), ("chapter", "12"), ("section", "99a")))

    assert replay.timelines is not None
    assert addr in replay.timelines
    # Prose: "on voimassa 31 päivään toukokuuta 2021" (in force THROUGH May 31)
    # → kernel exclusive cutoff June 1.
    assert replay.timelines[addr].versions[-1].expires == "2021-06-01"
    assert select_active_version(replay.timelines[addr], "2025-01-01") is None
    assert replay.replay_fold_state.find_section("99a", "12", "5") is not None
    assert replay.materialized_state.find_section("99a", "12", "5") is None


def test_replay_xml_expires_2010_1386_base_chapter_9_sections() -> None:
    replay = replay_xml_for_test("2010/1386", mode="legal_pit", quiet=True, as_of="2014-01-01")
    addr = LegalAddress(path=(("chapter", "9"), ("section", "63")))
    amended_addr = LegalAddress(path=(("chapter", "9"), ("section", "64")))

    assert replay.timelines is not None
    assert addr in replay.timelines
    assert amended_addr in replay.timelines
    assert any(
        event.event_id == "fi-base-chapter-expiry:2010/1386:chapter:9"
        and event.expires == "2014-01-01"
        for event in replay.temporal_events
    )
    assert select_active_version(replay.timelines[addr], "2013-12-31") is not None
    assert select_active_version(replay.timelines[addr], "2014-01-01") is None
    assert select_active_version(replay.timelines[amended_addr], "2014-01-01") is None
    assert replay.replay_fold_state.find_section("63", "9") is not None
    assert replay.materialized_state.find_section("63", "9") is None
    assert replay.materialized_state.find_section("64", "9") is None


def test_temporal_events_from_lo_ops_keeps_expire_when_group_has_explicit_commence_only() -> None:
    addr = LegalAddress(path=(("chapter", "5"), ("section", "21b")))
    op = LegalOperation(
        op_id="t21b",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=addr,
        group_id="finland-johto:2021/984:section_commencement",
        source=OperationSource(
            statute_id="2021/984",
            title="Laki lääketieteellisestä tutkimuksesta annetun lain muuttamisesta",
            enacted="2021-11-19",
            effective="2021-11-24",
            expires="2022-01-31",
        ),
    )
    explicit_commence = TemporalEvent(
        event_id="fi-temporal:finland-johto:2021/984:section_commencement",
        kind="commence",
        scope=TemporalScope(target_statute="1999/488", exact_addresses=(addr,)),
        effective="2021-11-24",
        source=op.source,
        group_id="finland-johto:2021/984:section_commencement",
    )

    assert explicit_commence.group_id is not None
    got = _temporal_events_from_lo_ops(
        [op],
        target_statute="1999/488",
        covered_commence_group_ids=frozenset({explicit_commence.group_id}),
        covered_expiry_signatures=frozenset(),
    )

    assert [event.kind for event in got] == ["expire"]
    assert got[0].expires == "2022-01-31"
    assert tuple(got[0].scope.exact_addresses or ()) == (addr,)


def test_replay_xml_expires_2018_11_temporary_content_before_later_permanent_merges() -> None:
    """Later permanent sparse merges must not bake expired 2021/513 content in."""
    replay = pinned_replay("2018/11", mode="legal_pit", quiet=True)

    # Expired temporary overlays are a PIT/product obligation.  The replay fold
    # is the raw mutation fold and may still carry expired overlay residue until
    # timeline materialization selects the in-force version.
    for state in (replay.materialized_state,):
        sec25 = state.find_section("25", "4")
        assert sec25 is not None
        sec25_mom1 = next(
            child for child in sec25.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        sec25_paragraphs = [child for child in sec25_mom1.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [child.label for child in sec25_paragraphs] == ["1", "2"]
        assert "41 §:n 1 momentin 2 kohdassa tarkoitetun maksujärjestelyn kohteena olevan veron suoritukseksi" not in (
            " ".join(irnode_to_text(sec25_mom1).split())
        )

        sec26 = state.find_section("26", "4")
        assert sec26 is not None
        assert next(
            (
                child
                for child in sec26.children
                if child.kind is IRNodeKind.SUBSECTION and child.label == "5"
            ),
            None,
        ) is None

        sec43 = state.find_section("43", "7")
        assert sec43 is not None
        assert next(
            (
                child
                for child in sec43.children
                if child.kind is IRNodeKind.SUBSECTION and child.label == "6"
            ),
            None,
        ) is None


def test_replay_xml_keeps_2021_984_permanent_inserts_active(
    replay_1999_488_legal_pit: ReplayResult,
) -> None:
    """Regression: 2021/984 permanent inserts stay live in the replay products.

    The replay fold already contains the inserted sections. Materialization at a
    later PIT must keep the permanent inserts visible while the temporary
    chapter-5 `21b §` expires.
    """
    replay = replay_1999_488_legal_pit

    assert replay.replay_fold_state.find_section("4a", "2") is not None
    assert replay.replay_fold_state.find_section("5a", "2") is not None
    assert replay.replay_fold_state.find_section("7a", "2") is not None
    assert replay.replay_fold_state.find_section("18a", "4") is not None
    assert replay.replay_fold_state.find_section("18b", "4") is not None
    assert replay.replay_fold_state.find_section("18c", "4") is not None
    assert replay.replay_fold_state.find_section("21a", "5") is not None
    assert replay.replay_fold_state.find_section("21b", "5") is not None
    assert replay.replay_fold_state.find_section("21c", "5") is not None
    assert replay.replay_fold_state.find_section("22b", "5") is not None
    assert replay.replay_fold_state.find_section("18a", "3") is None
    assert replay.replay_fold_state.find_section("18b", "3") is None
    assert replay.replay_fold_state.find_section("18c", "3") is None
    assert replay.replay_fold_state.find_section("21a", "3") is None
    assert replay.replay_fold_state.find_section("21b", "3") is None
    assert replay.replay_fold_state.find_section("21c", "3") is None
    assert replay.replay_fold_state.find_section("21a", "4") is None
    assert replay.replay_fold_state.find_section("21b", "4") is None
    assert replay.replay_fold_state.find_section("21c", "4") is None
    assert replay.replay_fold_state.find_section("22b", "3") is None

    assert replay.materialized_state.find_section("4a", "2") is not None
    assert replay.materialized_state.find_section("5a", "2") is not None
    assert replay.materialized_state.find_section("7a", "2") is not None
    assert replay.materialized_state.find_section("18a", "4") is not None
    assert replay.materialized_state.find_section("18b", "4") is not None
    assert replay.materialized_state.find_section("18c", "4") is not None
    assert replay.materialized_state.find_section("21a", "5") is not None
    assert replay.materialized_state.find_section("21b", "5") is None
    assert replay.materialized_state.find_section("21c", "5") is not None
    assert replay.materialized_state.find_section("22b", "5") is not None
    assert replay.materialized_state.find_section("18a", "3") is None
    assert replay.materialized_state.find_section("18b", "3") is None
    assert replay.materialized_state.find_section("18c", "3") is None
    assert replay.materialized_state.find_section("21a", "3") is None
    assert replay.materialized_state.find_section("21b", "3") is None
    assert replay.materialized_state.find_section("21c", "3") is None
    assert replay.materialized_state.find_section("21a", "4") is None
    assert replay.materialized_state.find_section("21b", "4") is None
    assert replay.materialized_state.find_section("21c", "4") is None
    assert replay.materialized_state.find_section("22b", "3") is None


def test_replay_xml_1999_488_places_replaced_section_18_under_chapter_4_after_2021_984(
    replay_1999_488_legal_pit: ReplayResult,
) -> None:
    sec18 = replay_1999_488_legal_pit.materialized_state.find_section("18", "4")
    assert sec18 is not None
    text18 = " ".join(irnode_to_text(sec18).split())

    assert "Alueellisen toimikunnan kokoonpano" in text18
    assert "Alueellisessa lääketieteellisessä tutkimuseettisessä toimikunnassa on oltava puheenjohtaja" in text18
    assert replay_1999_488_legal_pit.materialized_state.find_section("18", "3") is None


@pytest.mark.slow
def test_replay_xml_2004_1224_keeps_permanent_2016_1100_insert_sections_active() -> None:
    """2016/1100's alpha-suffix chapter-6 inserts must remain live after 2019.

    Regression: the real LISATA verb group was truncated by a false authority-
    lead-in skip at the first provenance CITATION_SPAN. Replay then treated the
    temporary `6 a §` as the only inserted section from that family, leaving the
    permanent `7 b §`, `18 a §`, and `22 b §` missing from the final PIT.
    """
    replay = pinned_replay(
        "2004/1224",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
        stop_before="2020/249",
    )

    assert replay.materialized_state.find_section("7b", "6") is not None
    assert replay.materialized_state.find_section("18a", "6") is not None
    assert replay.materialized_state.find_section("22b", "6") is not None


def test_replay_xml_applies_2025_1162_sparse_section_replace_to_22a(
    replay_1999_488_legal_pit: ReplayResult,
) -> None:
    sec22a = replay_1999_488_legal_pit.materialized_state.find_section("22a", "5")

    assert sec22a is not None
    text = irnode_to_text(sec22a)
    assert (
        "sekä 21 c §:n 1 momentissa tarkoitetun viranomaisen tekemään "
        "rekisteritietojen luovuttamista koskevaan päätökseen"
    ) in text
    assert replay_1999_488_legal_pit.materialized_state.find_section("21b", "5") is None


def test_replay_xml_applies_2025_1162_21c_then_22a_sequentially_without_staling_22a() -> None:
    """Regression: the later 22a replace must not inherit stale text after 21c.

    The replay compiler emits both 21c and 22a for 2025/1162.  Applying the
    compiled ops sequentially must yield the same 22a text as applying 22a from
    the same pre-amendment state directly.
    """
    base_replay = pinned_replay("1999/488", mode="legal_pit", quiet=True, build_full_products=False)
    xml_bytes = get_corpus().read_source("2025/1162")
    assert xml_bytes is not None
    muutos_tree = etree.fromstring(xml_bytes)
    johto = get_johtolause(xml_bytes)
    title_el = muutos_tree.find(".//{*}docTitle")
    source_title = (
        etree.tostring(title_el, method="text", encoding="unicode").strip()
        if title_el is not None
        else "Unknown"
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=base_replay.replay_fold_state,
        amendment_id="2025/1162",
        source_title=source_title,
        used_preamble_body_fallback=False,
        parent_id="1999/488",
        strict_profile=None,
    )
    resolved = compile_amendment_ops(
        base_replay.replay_fold_state,
        phase2.output,
        AmendmentSourceModel.from_tree(muutos_tree, source_ref="2025/1162"),
        johto,
        "legal_pit",
        source_ref="2025/1162",
        source_title=source_title,
        target_statute="1999/488",
    ).output

    relevant = [rop for rop in resolved if rop.resolved_target_label in {"21c", "22a"}]
    # 2025/1162 emits two ops for 22a (heading + subsection body) plus one for 21c
    assert [rop.resolved_target_label for rop in relevant] == ["21c", "22a", "22a"]

    op_21c = relevant[0]
    # The subsection body op carries target_paragraph; use it for the anti-staling check
    op_22a_body = next(
        r for r in relevant
        if r.resolved_target_label == "22a" and r.resolved_target_scope_view.target_paragraph is not None
    )

    seq_state = apply_op(base_replay.replay_fold_state, None, base_replay.ctx, None, rop=op_21c)
    seq_state = apply_op(seq_state, None, base_replay.ctx, None, rop=op_22a_body)
    direct_state = apply_op(base_replay.replay_fold_state, None, base_replay.ctx, None, rop=op_22a_body)

    seq_22a = seq_state.find_section("22a", "5")
    direct_22a = direct_state.find_section("22a", "5")
    assert seq_22a is not None
    assert direct_22a is not None
    assert irnode_to_text(seq_22a) == irnode_to_text(direct_22a)


def test_replay_xml_preserves_2013_393_body_chapter_scope_for_37a() -> None:
    """Regression: §37a must stay in chapter 6, not rehome to the old family."""
    lo_ops: list[LegalOperation] = []
    replay = pinned_replay("2013/393", mode="legal_pit", quiet=True, lo_ops_out=lo_ops)

    snapshot = next(op for op in lo_ops if op.op_id == "snapshot_section_37a")
    assert tuple(snapshot.target.path) == (("chapter", "6"), ("section", "37a"))

    found_paths: list[tuple[tuple[str, str], ...]] = []

    def _walk(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> None:
        next_path = path + ((node.kind.value, node.label or ""),)
        if node.kind == IRNodeKind.SECTION and node.label == "37a":
            found_paths.append(next_path)
        for child in node.children:
            _walk(child, next_path)

    _walk(replay.materialized_state.ir)
    assert any(("chapter", "6") in path for path in found_paths)
    assert not any(("chapter", "5") in path for path in found_paths)


def test_replay_xml_repeals_2021_984_range_sections_10d_to_10i(
    replay_1999_488_legal_pit: ReplayResult,
) -> None:
    """Regression: 2021/984 repeals the 10d–10i tail from the live statute."""

    for label in ("10d", "10e", "10f", "10g", "10h", "10i"):
        assert replay_1999_488_legal_pit.materialized_state.find_section(label, "2a") is None


@pytest.mark.slow
def test_replay_xml_2009_1672_whole_chapter_replace_retires_7a_2abc_from_materialized_state(
    replay_2009_1672_finlex_oracle_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    """Regression: later whole-chapter replace must retire historic non-base child sections."""
    replay, lo_ops = replay_2009_1672_finlex_oracle_with_lo_ops

    for label in ("2a", "2b", "2c"):
        assert replay.replay_fold_state.find_section(label, "7a") is None
        assert replay.materialized_state.find_section(label, "7a") is None

    repeal_targets = {
        tuple(op.target.path)
        for op in lo_ops
        if op.action is StructuralAction.REPEAL
        and op.source is not None
        and op.source.statute_id == "2024/1116"
        and op.target.path[:1] == (("chapter", "7a"),)
    }
    assert (("chapter", "7a"), ("section", "2a")) in repeal_targets
    assert (("chapter", "7a"), ("section", "2b")) in repeal_targets
    assert (("chapter", "7a"), ("section", "2c")) in repeal_targets


@pytest.mark.slow
def test_replay_xml_2009_1672_keeps_section_2_8_body_when_vts_repeals_only_subsection(
    replay_2009_1672_finlex_oracle_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    """Ambiguous granular voimaantulo repeal must not hijack the parent section snapshot."""
    replay, _ = replay_2009_1672_finlex_oracle_with_lo_ops

    section = replay.replay_fold_state.find_section("8", "2")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert "Öljyn kuljettaminen sisävesialueella" in text
    assert "Sisävesialueella liikennöivässä öljysäiliöaluksessa" in text
    assert "Muut haitallisten nestemäisten aineiden kuljetuksen todistuskirjat" not in text


@pytest.mark.slow
def test_replay_xml_2009_1672_sparse_chapter_replace_does_not_drop_section_5(
    replay_2009_1672_finlex_oracle_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    replay, _ = replay_2009_1672_finlex_oracle_with_lo_ops
    section = replay.materialized_state.find_section("5", "1")
    assert section is not None


def test_replay_xml_2010_76_whole_chapter_replace_drops_omitted_section_3(
    replay_2010_76_finlex_oracle_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    """Regression: a whole-chapter REPLACE must drop sections its payload omits.

    Chapter 7 of 2010/76 originally had sections 1-3. 2017/411 and later 2021/674
    each replace the whole chapter with a payload containing only sections 1 and 2.
    An earlier merge-style apply left the base section 3 orphaned, so it surfaced
    as an EXTRA chapter:7/section:3 against the Finlex oracle. The complete
    whole-chapter replacement payload is authoritative: section 3 must be retired,
    not snapshotted forward.
    """
    replay, lo_ops = replay_2010_76_finlex_oracle_with_lo_ops

    # The materialized product (what the oracle is compared against) must retire the
    # omitted section while keeping the sections the replacement payload contains.
    assert replay.materialized_state.find_section("3", "7") is None
    assert replay.materialized_state.find_section("1", "7") is not None
    assert replay.materialized_state.find_section("2", "7") is not None

    # The omitted section must be retired via a REPEAL on the timeline, not left as
    # a stale REPLACE snapshot carrying the old base text forward.
    section_3_path = (("chapter", "7"), ("section", "3"))
    section_3_ops = [op for op in lo_ops if tuple(op.target.path) == section_3_path]
    assert section_3_ops, "expected a timeline op for chapter:7/section:3"
    assert section_3_ops[-1].action is StructuralAction.REPEAL


@pytest.mark.slow
def test_replay_xml_1940_378_chapter_range_replace_retires_omitted_sections() -> None:
    """Regression: a chapter-RANGE REPLACE must drop sections its payloads omit.

    1994/318 replaces ``4-7 luku`` (four whole-chapter REPLACE ops). The new
    chapter 6 contains only sections 58-60 and the new chapter 7 only the
    relocated voimaantulo section 61, but an earlier merge-style apply left the
    old chapter 6 (56,57,62,63,63a-c) and chapter 7 (64,66-72) sections orphaned.
    The complete whole-chapter replacement payloads are authoritative even though
    each one's section set is much smaller than the merge-polluted live tree, so
    the omitted old sections must be retired, not snapshotted forward.
    """
    replay = pinned_replay("1940/378", mode="official_consolidation", quiet=True)
    state = replay.materialized_state

    # New chapter 6 keeps exactly its authoritative section set.
    for keep in ("58", "59", "60"):
        assert state.find_node("section", keep, "chapter", "6") is not None, keep
    # Old chapter 6 sections the replacement payload omits are retired.
    for orphan in ("56", "57", "62", "63", "63a", "63b", "63c"):
        assert state.find_node("section", orphan, "chapter", "6") is None, orphan

    # The relocation 73 § -> 61 § must still place the voimaantulo section in
    # chapter 7, and the merge-polluted old chapter 7 sections are retired.
    assert state.find_node("section", "61", "chapter", "7") is not None
    for orphan in ("64", "66", "67", "68", "69", "70", "71", "72"):
        assert state.find_node("section", orphan, "chapter", "7") is None, orphan


@pytest.mark.slow
def test_replay_xml_1987_1250_chapter_9_replace_retires_orphans_keeps_chapter_2() -> None:
    """Regression: a complete chapter-9 REPLACE retires its omitted sections.

    2000/340 replaces the whole chapter 9 with a payload owning sections 1-7
    (2 a § is added later). An earlier merge-style apply left the old sections
    5a and 8-14 orphaned in chapter 9, where the authoritative payload set (7
    sections) is smaller than the merge-polluted live tree (15 sections). Those
    orphans must be retired. Chapter 2 — which legitimately carries its own
    sections 8 and 9 — must be left untouched: the orphan drop only retires
    sections present in the freshly-replaced chapter's live tree, never a sibling
    chapter's untouched sections.
    """
    replay = pinned_replay("1987/1250", mode="official_consolidation", quiet=True)
    state = replay.materialized_state

    for keep in ("1", "2", "2a", "3", "4", "6", "7"):
        assert state.find_node("section", keep, "chapter", "9") is not None, keep
    for orphan in ("5a", "8", "9", "10", "11", "12", "13", "14"):
        assert state.find_node("section", orphan, "chapter", "9") is None, orphan

    # The sibling chapter 2 keeps its own sections 8 and 9 (not collateral
    # damage from the chapter-9 orphan drop).
    assert state.find_node("section", "8", "chapter", "2") is not None
    assert state.find_node("section", "9", "chapter", "2") is not None


@pytest.mark.slow
def test_replay_xml_2009_1672_does_not_import_laivavarustelaki_section_13_11(
    replay_2009_1672_finlex_oracle_with_lo_ops: tuple[ReplayResult, list[LegalOperation]],
) -> None:
    replay, lo_ops = replay_2009_1672_finlex_oracle_with_lo_ops

    # The host statute cross-references laivavarustelaki; its §11 must never be
    # imported as a chapter 13 section of the host. The current Finlex
    # consolidation horizon has no chapter 13 / §11, so replay must surface none.
    assert replay.replay_fold_state.find_section("11", "13") is None
    assert replay.materialized_state.find_section("11", "13") is None

    # No amendment in the consolidation window may insert a chapter 13 / §11
    # lineage into the host statute (the cross-referenced foreign-act section).
    assert not any(
        op.action is StructuralAction.INSERT
        and op.target.path[:2] == (("chapter", "13"), ("section", "11"))
        for op in lo_ops
    )


def test_replay_xml_repealed_2009_375_sections_25_26_do_not_revive_live_text(
    replay_1999_488_legal_pit: ReplayResult,
) -> None:
    """Regression: repealed 25–26 must not revive stale permanent body text."""

    sec25 = replay_1999_488_legal_pit.materialized_state.find_section("25", "6")
    sec26 = replay_1999_488_legal_pit.materialized_state.find_section("26", "6")

    assert sec25 is None
    assert sec26 is None


@pytest.mark.slow
def test_replay_xml_1988_161_pseudo_chapter_marker_moves_section_55_to_7c() -> None:
    """Regression: 1996/473 restructures chapter 7 → 7a/7b/7c via pseudo-markers.

    §55 must be moved from chapter 7 to chapter 7c (not left in chapter 7 nor
    duplicated).  2008/732 later repeals chapter 7; if §55 stays in chapter 7
    it gets wiped and appears MISSING in the final replay.
    """
    replay = pinned_replay("1988/161", mode="legal_pit", quiet=True)

    # §55 must be in chapter 7c
    sec55_in_7c = replay.materialized_state.find_section("55", "7c")
    assert sec55_in_7c is not None, "§55 must be in chapter 7c after pseudo-chapter restructuring"

    # §55 must NOT remain in chapter 7 (it was moved away)
    sec55_in_7 = replay.materialized_state.find_section("55", "7")
    assert sec55_in_7 is None, "§55 must not remain in chapter 7 after move to 7c"


@pytest.mark.slow
def test_replay_xml_1988_161_unscoped_replaces_do_not_become_7c_inserts() -> None:
    """1996/473 bare REPLACE payloads after 7c must not be inserted into 7c."""

    replay = pinned_replay("1988/161", mode="legal_pit", quiet=True)

    for label in ("59", "62", "66", "72", "113", "125", "131", "133", "134", "135", "142", "145"):
        assert replay.materialized_state.find_section(label, "7c") is None


def test_replay_xml_2009_617_moves_sections_39_to_41_into_inserted_chapter_4a() -> None:
    """Regression: 2016/533 splits chapter 4 and moves §§39–41 under 4 a luku.

    Before the fix, replay inserted chapter 4a as an empty shell and left the
    existing section family under chapter 4 because the structural move bridge
    only trusted pseudo-chapter marker sections, not real inserted chapters.
    """
    replay = pinned_replay("2009/617", stop_before="2017/816", mode="legal_pit", quiet=True)

    for label in ("39", "40", "41"):
        sec_in_4a = replay.materialized_state.find_section(label, "4a")
        assert sec_in_4a is not None, f"§{label} must be moved into chapter 4a after 2016/533"

        sec_in_4 = replay.materialized_state.find_section(label, "4")
        assert sec_in_4 is None, f"§{label} must not remain in chapter 4 after move to 4a"


@pytest.mark.slow
def test_replay_xml_2002_1090_relocates_sections_into_sibling_chapters_5a_5b() -> None:
    """Regression: 2009/226 splits chapter 5 into 5a/5b and moves §§41–50 in.

    The amendment inserts new chapter headings before §41 and §47, relocating
    §§41–46 under 5 a luku and §§47–50 under 5 b luku. Replay emits the move as
    an explicit repeal-at-source + insert-at-destination pair, plus a ``move``
    migration event for lineage. Before the fix, the migration event rekeyed the
    old-address timeline (tombstone included) onto the destination, leaving the
    old chapter slot untombstoned so the base content survived as orphan copies
    of §§41–50 under chapter 5.

    §44a additionally exercises the absent-from-base path: it was inserted into
    chapter 5 by an earlier amendment (2006/362), so the move-source tombstone
    must be synthesised at the live source chapter address, not the base tree.
    """
    replay = replay_xml_for_test("2002/1090", mode="official_consolidation", quiet=True)

    relocations = {
        "41": "5a",
        "42": "5a",
        "43": "5a",
        "44": "5a",
        "44a": "5a",
        "45": "5a",
        "46": "5a",
        "47": "5b",
        "48": "5b",
        "49": "5b",
        "50": "5b",
    }
    for label, new_chapter in relocations.items():
        assert (
            replay.materialized_state.find_section(label, new_chapter) is not None
        ), f"§{label} must be relocated into chapter {new_chapter}"
        assert (
            replay.materialized_state.find_section(label, "5") is None
        ), f"§{label} must not remain in chapter 5 after relocation (orphan)"


def test_replay_xml_1977_603_top_level_pseudo_chapter_marker_inserts_sections(
    replay_1977_603_finlex_oracle: ReplayResult,
) -> None:
    """Regression: 1996/476 introduces §72a/§72b/§72c under a top-level pseudo-chapter-marker
    '8 a luku' (not inside a <chapter> element).

    The uncovered-body recovery primary coverage path was comparing CoverageUnit.kind (str)
    to IRNodeKind.SECTION (enum) with `is not`, which always evaluated True and skipped
    all sections in the supplemental_candidates loop.  The fix changes to `!= "section"`.
    """
    replay = replay_1977_603_finlex_oracle

    # All three sections must be present after the fix
    sec72a = replay.materialized_state.find_section("72a")
    assert sec72a is not None, "§72a must be inserted by 1996/476 (uncovered recovery fix)"
    sec72b = replay.materialized_state.find_section("72b")
    assert sec72b is not None, "§72b must be inserted by 1996/476"
    sec72c = replay.materialized_state.find_section("72c")
    assert sec72c is not None, "§72c must be inserted by 1996/476"


def test_replay_xml_1977_603_realizes_section_72c_only_under_chapter_8a(
    replay_1977_603_finlex_oracle: ReplayResult,
) -> None:
    """Later chapter 8a realization must not leave a standalone §72c timeline bucket."""
    replay = replay_1977_603_finlex_oracle

    assert replay.products is not None
    products = replay.products
    assert products.timelines is not None
    timeline_keys = {str(address) for address in products.timelines}
    assert "chapter:8a/section:72c" in timeline_keys
    assert "section:72c" not in timeline_keys

    chapter_8a = replay.materialized_state.find_chapter("8a")
    assert chapter_8a is not None
    chapter_section_labels = [child.label for child in chapter_8a.children if child.kind == IRNodeKind.SECTION]
    assert chapter_section_labels == ["72a", "72b", "72c", "72d"]

    root_section_labels = [child.label for child in replay.materialized_state.ir.children if child.kind == IRNodeKind.SECTION]
    assert "72c" not in root_section_labels


@pytest.mark.slow
def test_replay_xml_1958_370_retargets_143b_away_from_stale_chapter_scope() -> None:
    """A stale carried scope must not promote its payload into root PIT timelines.

    1995/1062 says "2 a luvun otsikko, 17 § ja 143 b §:n 1 momentti";
    §143b actually lives under part 4 / chapter 17 in the replay fold. The
    frontend must retarget the stale carried 2a scope to the unique live
    section, not create a root ``section:143b`` timeline bucket.
    """
    from lawvm.finland.ops import FailedOp

    failed: list[FailedOp] = []
    replay = replay_xml_for_test(
        "1958/370",
        mode="official_consolidation",
        quiet=True,
        failed_ops_out=failed,
    )

    assert not any(
        op.amendment_id == "1995/1062"
        and op.reason_code == "section_not_found"
        and op.target_section == "143b"
        for op in failed
    )

    assert replay.timelines is not None
    timeline_keys = {str(address) for address in replay.timelines}
    assert "part:4/chapter:17/section:143b" in timeline_keys
    assert "part:4/chapter:17/section:143b/subsection:1" in timeline_keys
    assert "section:143b" not in timeline_keys
    assert "section:143b/subsection:1" not in timeline_keys

    root_section_labels = [child.label for child in replay.materialized_state.ir.children if child.kind == IRNodeKind.SECTION]
    assert "143b" not in root_section_labels


def test_replay_xml_1996_1260_orphaned_uusi_multi_target_lisataan() -> None:
    """Regression: 2022/958 lisätään clause with three targets where the first
    sub-target qualifier ('c alakohta') is removed by annotate_qualifiers,
    leaving an orphaned UUSI token immediately before the COMMA separator.

    Surface parse continuation loop was treating UUSI as a failed parse and
    breaking out of the loop instead of skipping the orphaned marker and
    continuing to the next COMMA-separated target (§8b INSERT via DOC:ILL
    Pattern C) and the §20b momentti 2 INSERT after it.
    """
    replay = pinned_replay("1996/1260", mode="official_consolidation", quiet=True)

    sec8b = replay.materialized_state.find_section("8b")
    assert sec8b is not None, "§8b must be inserted by 2022/958 (orphaned UUSI fix)"

    sec20b = replay.materialized_state.find_section("20b")
    assert sec20b is not None, "§20b must exist"
    from lawvm.core.ir import IRNodeKind
    subs_20b = [c for c in sec20b.children if c.kind == IRNodeKind.SUBSECTION]
    assert any(s.label == "2" for s in subs_20b), "§20b must have momentti 2 inserted by 2022/958"


def test_replay_xml_repealed_2007_435_sections_do_not_revive_live_text() -> None:
    """Whole-section kumotaan repeals must not revive base text in official_consolidation."""
    replay = pinned_replay("1995/355", mode="official_consolidation", quiet=True)

    assert replay.replay_fold_state.find_section("8a", "3") is not None
    assert replay.materialized_state.find_section("5", "2") is None
    assert replay.materialized_state.find_section("7", "2") is None
    assert replay.materialized_state.find_section("8a", "3") is None


def test_replay_xml_repealed_2006_764_sections_do_not_revive_live_text() -> None:
    """Zero-day repeal placeholders must not surface stale sections after PIT selection."""
    replay = pinned_replay("2003/343", mode="official_consolidation", quiet=True)

    assert replay.materialized_state.find_section("32", "5") is None
    assert replay.materialized_state.find_section("35", "5") is None
    assert replay.materialized_state.find_section("40", "5") is None


def test_replay_xml_repealed_2003_750_sections_stay_absent_on_same_day_oracle_horizon() -> None:
    """Same-day permanent repeals must not be ignored under detached horizons."""
    replay = pinned_replay("1998/461", mode="official_consolidation", quiet=True)

    assert replay.materialized_state.find_section("16") is None
    assert replay.materialized_state.find_section("17") is None
    assert replay.materialized_state.find_section("18") is None
    assert replay.materialized_state.find_section("19") is None


def test_replay_xml_repealed_1974_258_section_15_stays_absent() -> None:
    """A whole-section repeal with a johto commencement date must reach timelines."""
    replay = pinned_replay("1974/258", mode="official_consolidation", quiet=True)

    assert replay.materialized_state.find_section("15") is None


def test_replay_xml_1901_15_applies_1987_411_source_vts_side_repeal() -> None:
    """A source-VTS side repeal must not compile the amendment's main target ops."""
    replay = pinned_replay("1901/15-001", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_section("15")
    assert section is not None
    assert section.attrs.get("lawvm_repeal_placeholder") == "1"
    assert "oikeus ratkaiskoon" not in irnode_to_text(section).lower()


def test_replay_xml_1951_83_ignores_self_relabel_bridge_from_1982_601() -> None:
    """A restructure self-relabel must not revive stale pre-3a-luku section 19."""
    replay = replay_xml_for_test(
        "1951/83",
        mode="legal_pit",
        quiet=True,
        as_of="1999-04-22",
    )

    assert replay.materialized_state.find_section("19", "3") is None
    section_3a_19 = replay.materialized_state.find_section("19", "3a")
    assert section_3a_19 is not None
    section_3a_19_text = irnode_to_text(section_3a_19)
    assert "Vaikka asiakirja ei ole julkinen" in section_3a_19_text
    assert "viranomaiselle antamansa tai lähettämänsä asiakirjan takaisin" not in section_3a_19_text

    relabel_skips = [
        finding
        for finding in replay.findings
        if finding.kind == "APPLY.RELABEL_SKIP"
        and finding.source_statute == "1982/601"
        and finding.detail.get("reason_code") == "self_relabel_noop"
    ]
    assert relabel_skips


def test_replay_xml_recycle_rename_kumotaan_muutetaan_preserves_new_section_2010_128() -> None:
    """Recycle-and-rename: section in both kumotaan AND muutetaan must survive as new content.

    2019/1330 repeals old §44 (kumotaan 43 ja 44 §) and simultaneously
    introduces new §44 content (muutetaan ... 44 §). The kumotaan-muutetaan
    recycle guard must exclude §44 from the expiry override so the new §44
    is preserved permanently rather than being converted to a repeal.

    Regression for the bug where _rewrite_kumotaan_snapshot_replaces_to_repeal
    incorrectly converted the new §44 to a REPEAL, leaving it absent from
    the materialized product.
    """
    replay = pinned_replay("2010/128", mode="official_consolidation", quiet=True)

    # §43 was genuinely repealed by 2019/1330 (not in muutetaan)
    assert replay.materialized_state.find_section("43") is None, "§43 should be repealed"

    # §44 was recycled: old §44 repealed, new §44 introduced via muutetaan
    sec44 = replay.materialized_state.find_section("44")
    assert sec44 is not None, "§44 (new Ahvenanmaa content) must be present after recycle fix"
    recycle_findings = [
        finding
        for finding in replay.findings
        if finding.kind == "PARSE.REPEAL_RECYCLE_GUARD"
        and finding.source_statute == "2019/1330"
    ]
    assert recycle_findings
    assert recycle_findings[0].detail["recycled_labels"] == ("44",)


def test_replay_xml_later_inserted_whole_section_repeal_respects_oracle_horizon(
    replay_1990_845_finlex_oracle: ReplayResult,
) -> None:
    """Oracle PIT extends to the effective date of the latest amendment repeal.

    Oracle fin@20110427 was consolidated around 2011-05-05.  Amendment 2011/427
    repeals §31a with effective date 2011-06-01.  The oracle PIT is extended
    to 2011-06-01 so that the materialized state reflects the completed repeal,
    matching what the Finlex consolidated XML shows.
    """
    replay = replay_1990_845_finlex_oracle

    sec31a = replay.materialized_state.find_section("31a")
    assert sec31a is None


def test_replay_xml_retargets_stale_body_chapter_scope_to_live_current_chapter_2016_1285() -> None:
    replay = pinned_replay("2016/1285", mode="official_consolidation", quiet=True)

    for label in ("17", "18", "19", "20"):
        assert replay.replay_fold_state.find_section(label, "5") is not None
        assert replay.replay_fold_state.find_section(label, "3") is None
        assert replay.materialized_state.find_section(label, "5") is not None
        assert replay.materialized_state.find_section(label, "3") is None

    assert replay.replay_fold_state.find_section("24", "6") is not None
    assert replay.replay_fold_state.find_section("24", "3") is None
    assert replay.materialized_state.find_section("24", "6") is not None
    assert replay.materialized_state.find_section("24", "3") is None


def test_replay_xml_preserves_sparse_insert_before_terminal_voimaantulo_for_2006_766() -> None:
    replay = pinned_replay("2006/766", mode="official_consolidation", quiet=True)
    body = replay.materialized_state.ir

    top_labels = [
        child.label
        for child in body.children
        if child.kind in {IRNodeKind.SECTION, IRNodeKind.CHAPTER}
    ]
    assert top_labels == ["1", "2", "3", "3a", "4"]

    section_3a = replay.materialized_state.find_section("3a")
    assert section_3a is not None
    heading = next(child for child in section_3a.children if child.kind == IRNodeKind.HEADING)
    assert "Vastuullisuuden huomiointi" in (heading.text or "")

    section_4 = replay.materialized_state.find_section("4")
    assert section_4 is not None
    assert irnode_to_text(section_4).startswith("4 § Voimaantulo")


def test_replay_xml_explicit_insert_section_keeps_terminal_voimaantulo_label_for_2020_1266() -> None:
    replay = replay_xml_for_test("2020/1266", mode="official_consolidation", quiet=True)
    body = replay.materialized_state.ir

    assert replay.materialized_state.find_section("26a", "6") is None

    section_27 = replay.materialized_state.find_section("27", "6")
    assert section_27 is not None
    text = irnode_to_text(section_27)
    assert text.startswith("27 § Rehualan toimijan ja tilarehustamon vuosi-ilmoitusvelvollisuus")
    assert "Tämä asetus tulee voimaan 1 päivänä tammikuuta 2021" not in text


def test_replay_xml_preserves_inserted_chapter_topology_for_2014_1429(
    replay_2014_1429_finlex_oracle: ReplayResult,
) -> None:
    body = replay_2014_1429_finlex_oracle.materialized_state.ir

    chapter_labels = [child.label for child in body.children if child.kind == IRNodeKind.CHAPTER]
    assert "3a" in chapter_labels
    assert "5a" in chapter_labels
    assert "5b" in chapter_labels
    assert "6" in chapter_labels

    def _chapter_section_labels(chapter_label: str) -> list[str]:
        chapter = next(
            child
            for child in body.children
            if child.kind == IRNodeKind.CHAPTER and child.label == chapter_label
        )
        return [
            child.label
            for child in chapter.children
            if child.kind == IRNodeKind.SECTION and child.label is not None
        ]

    assert _chapter_section_labels("5a")[:4] == ["29a", "29b", "29c", "29d"]
    assert "29e" in _chapter_section_labels("5b")
    assert "29f" in _chapter_section_labels("5b")
    assert "29g" in _chapter_section_labels("5b")

    chapter_3a = next(
        child for child in body.children if child.kind == IRNodeKind.CHAPTER and child.label == "3a"
    )
    section_18a = next(
        child for child in chapter_3a.children if child.kind == IRNodeKind.SECTION and child.label == "18a"
    )
    assert irnode_to_text(section_18a).startswith("18 a § Pakottavuus")


def test_replay_xml_keeps_2014_1429_18e_as_single_subsection_list_section(
    replay_2014_1429_finlex_oracle: ReplayResult,
) -> None:
    sec18e = replay_2014_1429_finlex_oracle.materialized_state.find_section("18e", "3a")
    assert sec18e is not None

    subsection_labels = [child.label for child in sec18e.children if child.kind == IRNodeKind.SUBSECTION]
    assert subsection_labels == ["1"]

    sub1 = next(child for child in sec18e.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1")
    child_kinds = [child.kind for child in sub1.children]
    assert child_kinds[:4] == [
        IRNodeKind.INTRO,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
    ]
    assert child_kinds[4:] == [
        IRNodeKind.CONTENT,
        IRNodeKind.CONTENT,
        IRNodeKind.WRAP_UP,
    ]
    assert "Määräaikaisen sopimuksen ehtoja ei kuitenkaan saa muuttaa" in irnode_to_text(sub1)


def test_replay_xml_keeps_2022_1384_tree_definition_inside_subsection_2() -> None:
    replay = pinned_replay("2022/1384", mode="official_consolidation", quiet=True)

    sec1 = replay.materialized_state.find_section("1")
    assert sec1 is not None

    subsection_labels = [child.label for child in sec1.children if child.kind == IRNodeKind.SUBSECTION]
    assert subsection_labels == ["1", "2"]

    sub2 = next(child for child in sec1.children if child.kind == IRNodeKind.SUBSECTION and child.label == "2")
    child_kinds = [child.kind for child in sub2.children]
    assert child_kinds[:4] == [
        IRNodeKind.INTRO,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
    ]
    assert child_kinds[4:] == [IRNodeKind.WRAP_UP]
    assert "Tätä asetusta sovelletaan vain puihin" in irnode_to_text(sub2)


def test_replay_xml_drops_tax_year_scoped_temporary_sections_for_1967_543() -> None:
    replay = pinned_replay(
        "1967/543",
        mode="official_consolidation",
        quiet=True,
    )

    assert replay.materialized_state.find_section("12a") is None
    assert replay.materialized_state.find_section("12b") is None


def test_replay_xml_moves_2014_1429_29e_into_chapter_5b(
    replay_2014_1429_finlex_oracle: ReplayResult,
) -> None:
    """29e follows the move clause into chapter 5b at the oracle horizon."""

    chapter_5a_29e = replay_2014_1429_finlex_oracle.materialized_state.find_section("29e", "5a")
    chapter_5b_29e = replay_2014_1429_finlex_oracle.materialized_state.find_section("29e", "5b")

    assert chapter_5a_29e is None
    assert chapter_5b_29e is not None
    assert "Datakeskuksen hukkalämmön hyödyntäminen" in irnode_to_text(chapter_5b_29e)


@pytest.mark.slow
def test_replay_xml_applies_2024_483_kieliasu_section_list_for_2008_550() -> None:
    """Language-variant residue must not block the later long section list in 2024/483."""
    replay = pinned_replay("2008/550", mode="official_consolidation", quiet=True)

    section_10 = replay.materialized_state.find_section("10")
    assert section_10 is not None
    assert irnode_to_text(section_10).startswith("10 § Ministeriön virkamiesjohdon kokous")


def test_replay_xml_applies_2019_511_luvun_insert_chain_for_2012_746() -> None:
    """Anaphoric `luvun` insert continuations in 2019/511 must materialize under the right chapters."""
    replay = pinned_replay("2012/746", mode="official_consolidation", quiet=True)

    assert replay.materialized_state.find_section("1a", "8") is not None
    assert replay.materialized_state.find_section("5a", "8") is not None
    assert replay.materialized_state.find_section("10", "8") is not None
    assert replay.materialized_state.find_section("1", "10a") is not None
    assert replay.materialized_state.find_section("2", "10a") is not None
    assert replay.materialized_state.find_section("5", "10a") is not None


def test_replay_xml_preserves_explicit_body_chapter_ownership_for_2013_393() -> None:
    """An explicit chapter wrapper in the amendment body must stay on the inserted section."""
    replay = pinned_replay("2013/393", mode="official_consolidation", quiet=True)

    assert replay.materialized_state.find_section("37a", "6") is not None
    assert replay.materialized_state.find_section("37a", "5") is None


@pytest.mark.slow
def test_replay_xml_preserves_2006_395_targeted_merge_sections(
    replay_2006_395_finlex_oracle: ReplayResult,
) -> None:
    """Replay/product regression for 2006/395 targeted merge semantics."""
    cases = (
        (
            "32",
            "3",
            ["1", "2", "3"],
            [
                "Jos ammatillinen kuntoutus keskeytyy yli 30 kalenteripäivän ajaksi",
                "Kuntoutusraha tai kuntoutuskorotus voidaan lakkauttaa",
                "Työntekijällä ei ole oikeutta ilman pätevää syytä työkyvyttömyyseläkkeeseen",
            ],
        ),
        (
            "118",
            "8",
            ["1", "2", "3", "4", "5"],
            [
                "Jos työntekijälle on maksettu sairausvakuutuslain mukaista sairauspäivärahaa",
                "Jos täysi työkyvyttömyyseläke myönnetään takautuvasti 41 §:n 1 momentissa",
                "Jos kuntoutusraha tai -korotus myönnetään takautuvasti",
                "Jos kuntoutusraha, kuntoutuskorotus tai työkyvyttömyyseläke",
                "Jos työuraeläke myönnetään takautuvasti",
            ],
        ),
        (
            "122",
            "8",
            ["1", "2", "3", "4"],
            [
                "Eläkelaitos voi eläkkeensaajan suostumuksella päättää",
                "Esityksen eläkkeen maksamisesta hyvinvointialueelle voi tehdä",
                "Eläkettä ei saa käyttää vastoin eläkkeensaajan suostumusta",
                "Mitä tässä pykälässä säädetään hyvinvointialueesta",
            ],
        ),
        (
            "205",
            "14",
            ["1", "2", "3", "4"],
            [
                "Eläkelaitoksella ja Eläketurvakeskuksella on oikeus salassapitosäännösten",
                "Annettavia tietoja ovat:",
                "Eläkelaitoksella ja Eläketurvakeskuksella on oikeus antaa 2 momentissa tarkoitettuja tietoja",
                "Tässä pykälässä tarkoitetuissa tilanteissa ei kuitenkaan saa antaa työntekijän terveydentilaa koskevia tietoja",
            ],
        ),
        (
            "70",
            "4",
            ["1", "2", "3", "4", "5"],
            [
                "Eläkkeen perusteena olevaa työansiota määrättäessä otetaan huomioon palkka",
                "Eläkkeen perusteena olevaan työansioon luetaan myös työstä maksettava vastike, joka on osaksi tai kokonaan sovittu hyvitettäväksi",
                "Edellä 1 momentissa tarkoitettuna vastikkeena työstä ei pidetä muun muassa",
                "Edellä 3 momentin 11 kohdassa tarkoitetussa tilanteessa edellytyksenä on lisäksi",
                "Yleisöltä palvelurahaa saavan työntekijän on ilmoitettava työnantajalleen veron perusteena olevan palvelurahan määrä",
            ],
        ),
    )

    for section_num, chapter_num, expected_labels, expected_snippets in cases:
        for state in (
            replay_2006_395_finlex_oracle.replay_fold_state,
            replay_2006_395_finlex_oracle.materialized_state,
        ):
            section = state.find_section(section_num, chapter_num)
            assert section is not None
            subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
            assert [child.label for child in subsections] == expected_labels
            assert len(subsections) == len(expected_snippets)
            for subsection, snippet in zip(subsections, expected_snippets, strict=True):
                text = " ".join(irnode_to_text(subsection).split())
                assert snippet in text


def test_validate_replay_products_detects_materialized_tree_invariants() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    ctx = StatuteContext(
        id="test/1",
        title="Test",
        base_ir=IRNode(kind=IRNodeKind.BODY),
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        materialization_spec=None,
        source_adjudication=None,
    )

    violations = validate_replay_products(
        ctx,
        products,
        deep_materialization_check=False,
    )

    assert "materialized_tree:body: duplicate section:1 (2 times)" in violations


def test_validate_replay_products_detects_fi_label_identity_collisions() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="4a"),
            IRNode(kind=IRNodeKind.SECTION, label="iva"),
        ),
    )
    ctx = StatuteContext(
        id="test/1",
        title="Test",
        base_ir=IRNode(kind=IRNodeKind.BODY),
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=body),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        materialization_spec=None,
        source_adjudication=None,
    )

    violations = validate_replay_products(
        ctx,
        products,
        deep_materialization_check=False,
    )

    assert (
        "replay_fold_tree:body: label-normalization collision section:4a "
        "from labels 4a, iva"
    ) in violations
    assert (
        "materialized_tree:body: label-normalization collision section:4a "
        "from labels 4a, iva"
    ) in violations


def test_replay_fold_projection_typed_invariants_include_profile_metadata() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(IRNode(kind=IRNodeKind.CHAPTER, label="1"),),
            ),
        ),
    )
    meta: dict[str, object] = {}

    project_replay_fold(
        ReplayFoldProjectionRequest(
            state=ReplayState(ir=body),
            parent_id="test/1",
            replay_findings=[],
            replay_meta_out=meta,
            replay_print=lambda _message: None,
        )
    )

    rows = cast(list[dict[str, object]], meta["typed_invariant_violations"])
    assert rows[0]["surface"] == "replay_fold_tree"
    assert rows[0]["profile_id"] == "core_structural_tree_all"
    assert rows[0]["kind"] == "unexpected_child_kind"
    profiles = cast(list[dict[str, object]], meta["replay_invariant_profiles"])
    assert profiles == [
        {
            "profile_id": "core_replay_strict_v1",
            "tree_profiles": (
                {
                    "surface": "replay_fold_tree",
                    "profile_id": "core_replay_delta_minimal",
                    "families": ("duplicate_label", "sort_order"),
                },
            ),
            "mutation_accounting": "hard",
            "transition_detectors": (
                "descendant_sibling_loss",
                "same_source_descendant_snapshot_shadow",
            ),
            "timeline_invariants": (
                "temporal_overlap",
                "temporary_overlay",
                "expiry_chain",
                "replay_timeline_robust",
            ),
            "warnings": ("text_duplication", "flattened_sublist_family", "label_sequence_gap"),
            "local_allowance_policy": "frontend_required",
            "local_classifier_policy": "frontend_required",
            "safe_default": "profile_is_declarative_not_replay_authorization",
            "replay_authorization_claims": False,
        }
    ]


def test_validate_replay_products_detects_mixed_hierarchy_products() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="15",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="148"),),
                    ),
                    IRNode(kind=IRNodeKind.SECTION, label="149"),
                ),
            ),
        ),
    )
    ctx = StatuteContext(
        id="test/1",
        title="Test",
        base_ir=IRNode(kind=IRNodeKind.BODY),
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=body),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        materialization_spec=None,
        source_adjudication=None,
    )

    violations = validate_replay_products(
        ctx,
        products,
        deep_materialization_check=False,
    )

    assert (
        "replay_fold_tree:body/hcontainer:?: direct section:149 alongside chapter:15"
        in violations
    )
    assert (
        "materialized_tree:body/hcontainer:?: direct section:149 alongside chapter:15"
        in violations
    )
    rows = fi_product_tree_invariant_dicts(
        body,
        structural_product_hierarchical_profile("materialized_tree"),
    )
    assert rows[0]["surface"] == "materialized_tree"
    assert rows[0]["profile_id"] == "core_structural_product_hierarchical"
    assert rows[0]["kind"] == "mixed_hierarchy_child"


def test_validate_replay_products_allows_terminal_fi_commencement_section() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="4",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="22"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="23",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="23 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"),
                        ),
                    ),
                ),
            ),
        ),
    )
    ctx = StatuteContext(
        id="test/1",
        title="Test",
        base_ir=IRNode(kind=IRNodeKind.BODY),
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=body),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        materialization_spec=None,
        source_adjudication=None,
    )

    violations = validate_replay_products(
        ctx,
        products,
        deep_materialization_check=False,
    )

    assert violations == []


def test_validate_replay_products_still_flags_non_commencement_mixed_section() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="4",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="22"),),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="23",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="23 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Soveltamisala"),
                        ),
                    ),
                ),
            ),
        ),
    )
    ctx = StatuteContext(
        id="test/1",
        title="Test",
        base_ir=IRNode(kind=IRNodeKind.BODY),
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=body),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        materialization_spec=None,
        source_adjudication=None,
    )

    violations = validate_replay_products(
        ctx,
        products,
        deep_materialization_check=False,
    )

    assert (
        "replay_fold_tree:body/hcontainer:?: direct section:23 alongside chapter:4"
        in violations
    )


@pytest.mark.slow
def test_2014_527_legal_pit_does_not_leave_reinstated_section_family_at_root() -> None:
    replay = pinned_replay("2014/527", mode="legal_pit", quiet=True)

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=False,
    )

    assert not any("section:149" in violation for violation in violations)
    assert replay.materialized_state.find_section("149a", "15") is not None
    assert replay.materialized_state.find_section("211b", "20") is not None


def test_2004_1287_legal_pit_allows_source_authored_final_commencement_section() -> None:
    replay = pinned_replay("2004/1287", mode="legal_pit", quiet=True)

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=False,
    )

    assert not any("direct section:23 alongside chapter:4" in violation for violation in violations)


@pytest.mark.slow
def test_1958_370_allows_source_authored_final_commencement_section() -> None:
    replay = replay_xml_for_test("1958/370", mode="official_consolidation", quiet=True)

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=False,
    )

    assert not any("direct section:152 alongside part" in violation for violation in violations)


@pytest.mark.slow
def test_1958_370_reinstated_114_keeps_prior_chapter_scope() -> None:
    replay = replay_xml_for_test("1958/370", mode="legal_pit", quiet=True)

    def has_root_section_114(node: IRNode, chapter_seen: bool = False) -> bool:
        next_chapter_seen = chapter_seen or node.kind is IRNodeKind.CHAPTER
        if node.kind is IRNodeKind.SECTION and node.label == "114":
            return not chapter_seen
        return any(has_root_section_114(child, next_chapter_seen) for child in node.children)

    for state in (replay.replay_fold_state, replay.materialized_state):
        assert state.find_section("114", chapter_num="11", part_num="3") is not None
        assert not has_root_section_114(state.ir)


@pytest.mark.slow
def test_1958_370_retargets_1968_493_stale_body_chapter_scope() -> None:
    failed_ops = []
    replay = replay_xml_for_test("1958/370", mode="legal_pit", quiet=True, failed_ops_out=failed_ops)

    assert failed_ops == []
    for state in (replay.replay_fold_state, replay.materialized_state):
        assert state.find_section("56", chapter_num="7", part_num="2") is not None
        assert state.find_section("58", chapter_num="7", part_num="2") is not None
        assert state.find_section("70", chapter_num="8", part_num="2") is not None
        assert state.find_section("110", chapter_num="11", part_num="3") is not None
        assert state.find_section("56", chapter_num="5", part_num="2") is None
        assert state.find_section("58", chapter_num="5", part_num="2") is None
        assert state.find_section("70", chapter_num="5", part_num="2") is None
        assert state.find_section("110", chapter_num="5", part_num="2") is None


def test_replay_fold_does_not_duplicate_temporary_section_chain_for_1995_1556() -> None:
    replay = pinned_replay("1995/1556", mode="legal_pit", stop_before="2022/439", quiet=True)

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=False,
    )

    assert "replay_fold_tree:body/hcontainer:?: duplicate section:5e (2 times)" not in violations


def test_replay_fold_splits_sparse_combined_subsection_replace_for_1991_827() -> None:
    replay = pinned_replay("1991/827", mode="legal_pit", stop_before="1995/1387", quiet=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        sec6 = state.find_section("6")
        assert sec6 is not None
        sec6_text = irnode_to_text(sec6)
        subsections = [child for child in sec6.children if child.kind is IRNodeKind.SUBSECTION]

        assert [child.label for child in subsections] == ["1", "2", "3"]
        assert sec6_text.count(
            "Edellä 1 momentissa mainitun oikeuden, rajoituksen tai toimenpiteen kirjauksessa"
        ) == 1
        assert sec6_text.count("Arvo-osuustilille, jolle jo on kirjattu panttaus") == 1


def test_materialize_pit_preserves_base_schedules() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="body"),))
    schedule = IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="schedule text")
    base = IRStatute(
        statute_id="test/schedules",
        title="Schedules",
        body=body,
        supplements=(schedule,),
    )
    timelines = compile_timelines(base, [], base_date="2020-01-01")
    pit = materialize_pit(timelines, "2025-01-01", base=base)

    assert len(pit.supplements) == 1


def test_materialize_pit_drops_zero_day_repeal_placeholder_under_detached_horizon() -> None:
    def _find_section(node: IRNode, label: str) -> IRNode | None:
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label == label:
                return child
            found = _find_section(child, label)
            if found is not None:
                return found
        return None

    base = IRStatute(
        statute_id="test/zero-day-repeal",
        title="Zero-day repeal",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2019-12-19",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={
                            "lawvm_repeal_placeholder": "1",
                            _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                        },
                        children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                    ),
                    source=OperationSource(
                        statute_id="2019/1",
                        enacted="2019-12-19",
                        effective="2020-01-01",
                    ),
                ),
            ],
        )
    }

    pit = materialize_pit(
        timelines,
        "9999-12-31",
        base=base,
        expires_as_of="2023-10-01",
    )

    assert _find_section(pit.body, "1") is None


def test_materialize_pit_keeps_non_zero_day_repeal_placeholder_visible_under_detached_horizon() -> None:
    def _find_section(node: IRNode, label: str) -> IRNode | None:
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label == label:
                return child
            found = _find_section(child, label)
            if found is not None:
                return found
        return None

    base = IRStatute(
        statute_id="test/permanent-repeal-placeholder",
        title="Permanent placeholder",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),
                ),
                ProvisionVersion(
                    effective="2024-01-01",
                    enacted="2023-04-14",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={"lawvm_repeal_placeholder": "1"},
                        children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                    ),
                    source=OperationSource(
                        statute_id="2023/741",
                        enacted="2023-04-14",
                        effective="2024-01-01",
                    ),
                ),
            ],
        )
    }

    pit = materialize_pit(
        timelines,
        "9999-12-31",
        base=base,
        expires_as_of="2024-01-01",
    )

    section = _find_section(pit.body, "1")
    assert section is not None
    assert section.attrs.get("lawvm_repeal_placeholder") == "1"


def test_materialize_pit_drops_marked_future_repeal_under_detached_horizon() -> None:
    def _find_section(node: IRNode, label: str) -> IRNode | None:
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label == label:
                return child
            found = _find_section(child, label)
            if found is not None:
                return found
        return None

    base = IRStatute(
        statute_id="test/future-repeal-detached",
        title="Future repeal under detached horizon",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="19", text="Base 19 §"),)),
    )
    addr = LegalAddress(path=(("section", "19"),))
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="19", text="Base 19 §"),
                ),
                ProvisionVersion(
                    effective="2006-01-01",
                    enacted="2005-11-11",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="19",
                        attrs={
                            "lawvm_repeal_placeholder": "1",
                            _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                        },
                    ),
                    source=OperationSource(
                        statute_id="2005/886",
                        enacted="2005-11-11",
                        effective="2006-01-01",
                    ),
                ),
            ],
        )
    }

    pit = materialize_pit(
        timelines,
        "2006-01-01",
        base=base,
        expires_as_of="2005-11-11",
    )

    assert _find_section(pit.body, "19") is None


def test_build_replay_products_accepts_temporal_events_for_materialization() -> None:
    ctx = StatuteContext(
        id="test/temporal-products",
        title="Temporal replay products",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:fi-replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2005-01-01",
                effective="2005-01-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-products",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="fi:commence",
                group_id="g:fi-replay",
                kind="commence",
                scope=TemporalScope(target_statute="test/temporal-products"),
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-products:source",
                    raw_text="commence",
                    effective="2010-01-01",
                ),
            ),
        ),
    )

    assert products.timelines is not None
    assert len(products.temporal_events) == 1
    assert products.temporal_events[0].source is not None
    assert products.temporal_events[0].source.statute_id == "test/temporal-products:source"
    assert products.temporal_events[0].source.effective == "2010-01-01"
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.effective == "2010-01-01"
    assert products.materialized_state.ir.children[0].text == "Updated"


def test_build_replay_products_accepts_lifecycle_events_for_materialization() -> None:
    ctx = StatuteContext(
        id="test/lifecycle-products",
        title="Lifecycle replay products",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:fi-lifecycle-replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2005-01-01",
                effective="2010-01-01",
            ),
        )
    ]
    instrument = SourceInstrumentRef(
        instrument_id="2011/200",
        effective="2010-01-01",
    )
    witness = SourceProvisionRef(
        instrument=instrument,
        path=("voimaantulo",),
        text_excerpt="Tulee voimaan 1.1.2010.",
    )
    effect = EffectRef(
        effect_id="effect:2010/100:replace_1",
        source_instrument=SourceInstrumentRef(instrument_id="2010/100"),
        target_statute="test/lifecycle-products",
        target_address=LegalAddress(path=(("section", "1"),)),
        projection_group_id="g:fi-lifecycle-replay",
    )
    relation = EffectRelation(
        relation_id="relation:2011/200:replace_1:expiry",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="life:2011/200:replace_1:expiry",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2012-01-01",
        executable=True,
    )

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/lifecycle-products",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
        source_effects=(effect,),
        effect_relations=(relation,),
        effect_lifecycle_events=(lifecycle,),
    )

    assert products.timelines is not None
    lifecycle_temporal = next(
        event
        for event in products.temporal_events
        if event.event_id == "life:2011/200:replace_1:expiry:temporal"
    )
    assert lifecycle_temporal.group_id == "g:fi-lifecycle-replay"
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.effective == "2010-01-01"
    assert active.expires == "2012-01-01"
    assert products.materialized_state.ir.children[0].text == "Updated"


def test_replay_products_require_typed_effect_graph_records() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))

    with pytest.raises(TypeError, match="temporal_events"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            temporal_events=cast(Any, ("temporal:1",)),
        )
    with pytest.raises(TypeError, match="migration_events"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            migration_events=cast(Any, ("migration:1",)),
        )
    with pytest.raises(TypeError, match="source_effects"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            source_effects=cast(Any, ("effect:1",)),
        )
    with pytest.raises(TypeError, match="effect_relations"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            effect_relations=cast(Any, ("relation:1",)),
        )
    with pytest.raises(TypeError, match="effect_lifecycle_events"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            effect_lifecycle_events=cast(Any, ("lifecycle:1",)),
        )


def test_replay_products_reject_duplicate_effect_graph_ids() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect_a = EffectRef(effect_id="effect:1", source_instrument=instrument)
    effect_b = EffectRef(effect_id="effect:1", source_instrument=instrument)
    target_effect = EffectRef(effect_id="effect:target", source_instrument=instrument)
    relation_a = EffectRelation(
        relation_id="relation:1",
        kind="modifies_effect",
        source_provision=witness,
        target_effect=target_effect,
    )
    relation_b = EffectRelation(
        relation_id="relation:1",
        kind="repeals_effect",
        source_provision=witness,
        target_effect=target_effect,
    )
    lifecycle_a = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )
    lifecycle_b = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="unresolved_effect_target",
        source_provision=witness,
        executable=False,
    )

    with pytest.raises(ValueError, match="duplicate effect_id"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            source_effects=(effect_a, effect_b),
        )
    with pytest.raises(ValueError, match="duplicate relation_id"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            effect_relations=(relation_a, relation_b),
        )
    with pytest.raises(ValueError, match="duplicate lifecycle_event_id"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            effect_lifecycle_events=(lifecycle_a, lifecycle_b),
        )


def test_replay_products_require_closed_effect_graph() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    effect = EffectRef(effect_id="effect:1", source_instrument=instrument)
    relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=effect,
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=effect,
        relation=relation,
        expires="2021-01-01",
    )

    with pytest.raises(ValueError, match="missing target_effect"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            effect_relations=(relation,),
        )
    with pytest.raises(ValueError, match="missing relation"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            source_effects=(effect,),
            effect_lifecycle_events=(lifecycle,),
        )


def test_replay_products_reject_stale_effect_graph_endpoint_records() -> None:
    state = ReplayState(ir=IRNode(kind=IRNodeKind.BODY))
    instrument = SourceInstrumentRef(instrument_id="2020/1")
    witness = SourceProvisionRef(instrument=instrument, path=("1",))
    graph_effect = EffectRef(
        effect_id="effect:1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "1"),)),
    )
    stale_effect = EffectRef(
        effect_id="effect:1",
        source_instrument=instrument,
        target_statute="1999/1",
        target_address=LegalAddress(path=(("section", "2"),)),
    )
    relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=stale_effect,
    )
    graph_relation = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=graph_effect,
    )
    relation_with_detail = EffectRelation(
        relation_id="relation:1",
        kind="extends_effect_expiry",
        source_provision=witness,
        target_effect=graph_effect,
        detail={"note": "stale"},
    )
    lifecycle = EffectLifecycleEvent(
        lifecycle_event_id="lifecycle:1",
        kind="change_effect_expiry",
        source_provision=witness,
        effect=graph_effect,
        relation=relation_with_detail,
        expires="2021-01-01",
    )

    with pytest.raises(ValueError, match="target_effect differs from graph effect"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            source_effects=(graph_effect,),
            effect_relations=(relation,),
        )
    with pytest.raises(ValueError, match="relation differs from graph relation"):
        ReplayProducts(
            replay_fold_state=state,
            materialized_state=state,
            timelines=None,
            source_effects=(graph_effect,),
            effect_relations=(graph_relation,),
            effect_lifecycle_events=(lifecycle,),
        )


def test_build_replay_products_requires_explicit_effective_date_for_derived_temporal_events() -> None:
    ctx = StatuteContext(
        id="test/temporal-products-no-fallback",
        title="Temporal replay products without fallback",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:fi-replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2005-01-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-products-no-fallback",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
    )

    assert products.temporal_events == ()
    assert products.materialized_state.ir.children[0].text == "Base"


def test_build_replay_products_merges_existing_temporal_events_with_synthesized_ops() -> None:
    ctx = StatuteContext(
        id="test/temporal-products-merge",
        title="Temporal replay products merge",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2009-01-01",
                effective="2010-01-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-products-merge",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="other:set_applicability",
                group_id="g:other",
                kind="set_applicability",
                scope=TemporalScope(target_statute="test/temporal-products-merge"),
                source=OperationSource(statute_id="test/temporal-products-merge:other"),
            ),
        ),
    )

    assert len(products.temporal_events) == 2
    assert products.materialized_state.ir.children[0].text == "Updated"


def test_merge_temporal_events_rejects_conflicting_duplicate_event_ids() -> None:
    existing = TemporalEvent(
        event_id="temporal:1",
        group_id="g:1",
        kind="commence",
        scope=TemporalScope(target_statute="1999/1"),
        effective="2020-01-01",
    )
    identical = TemporalEvent(
        event_id="temporal:1",
        group_id="g:1",
        kind="commence",
        scope=TemporalScope(target_statute="1999/1"),
        effective="2020-01-01",
    )
    same_signature = TemporalEvent(
        event_id="temporal:1:alias",
        group_id="g:1",
        kind="commence",
        scope=TemporalScope(target_statute="1999/1"),
        effective="2020-01-01",
    )
    conflicting = TemporalEvent(
        event_id="temporal:1",
        group_id="g:1",
        kind="expire",
        scope=TemporalScope(target_statute="1999/1"),
        expires="2021-01-01",
    )

    assert _merge_temporal_events((existing,), (identical,)) == (existing,)
    assert _merge_temporal_events((existing,), (same_signature,)) == (existing,)
    with pytest.raises(ValueError, match="conflicting duplicate event_id"):
        _merge_temporal_events((existing,), (conflicting,))


def test_retarget_root_node_preserves_existing_num_suffix_for_section() -> None:
    source_node = IRNode(
        kind=IRNodeKind.SECTION,
        label="10",
        text="10 § old ten",
        children=(IRNode(kind=IRNodeKind.NUM, text="10 §"),),
    )

    retargeted = _retarget_root_node(
        source_node,
        LegalAddress(path=(("section", "11"),)),
    )

    assert retargeted.label == "11"
    assert retargeted.children[0].text == "11 §"
    assert retargeted.text == "10 § old ten"


def test_build_replay_products_carries_migration_events() -> None:
    ctx = StatuteContext(
        id="test/migration-products",
        title="Migration replay products",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    migration_event = MigrationEvent(
        event_id="mig:test/1:section:1→section:2",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "1"),)),
        to_address=LegalAddress(path=(("section", "2"),)),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/migration-products",
        replay_fold_state=replay_fold_state,
        lo_ops_out=[],
        migration_events=(migration_event,),
    )

    assert products.migration_events == (migration_event,)
    assert len(products.identity_ledger) == 1
    resolved = products.identity_ledger.current_address(
        LegalAddress(path=(("section", "1"),)),
        as_of_date="2021-01-01",
    )
    assert resolved == LegalAddress(path=(("section", "2"),))


def test_rekey_timelines_prefers_destination_native_lineage_over_migrated_source_history() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § old lineage"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § native lineage"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/section:5→section:159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {destination_addr}
    destination_versions = rekeyed[destination_addr].versions
    assert len(destination_versions) == 1
    assert destination_versions[0].content is not None
    assert destination_versions[0].content.text == "159 § native lineage"


def test_renumber_source_prefix_predicate_reuses_cached_normalized_path(monkeypatch) -> None:
    import lawvm.finland.migration_ledger as migration_ledger

    calls = 0
    real_normalize_address_path = migration_ledger.normalize_address_path

    def counted_normalize_address_path(path):
        nonlocal calls
        calls += 1
        return real_normalize_address_path(path)

    path = (("part", "III"), ("chapter", "2"), ("section", "5"))
    renumber_sources = frozenset({(("part", "3"),)})
    _renumber_source_prefix_may_match_cached.cache_clear()
    real_normalize_address_path.cache_clear()
    monkeypatch.setattr(
        migration_ledger,
        "normalize_address_path",
        counted_normalize_address_path,
    )
    try:
        assert _renumber_source_prefix_may_match_cached(path, renumber_sources)
        assert _renumber_source_prefix_may_match_cached(tuple(path), renumber_sources)
        assert calls == 1
    finally:
        _renumber_source_prefix_may_match_cached.cache_clear()
        real_normalize_address_path.cache_clear()


def test_rekey_timelines_walks_migration_chains_across_distinct_waves_regardless_of_input_order() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    final_addr = LegalAddress(path=(("section", "159a"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="old lineage"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
    }
    first = MigrationEvent(
        event_id="mig:test/section:5→section:159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )
    second = MigrationEvent(
        event_id="mig:test/section:159→section:159a",
        kind="renumber",
        from_address=destination_addr,
        to_address=final_addr,
        effective="2021-01-01",
        source_statute="2021/1",
    )

    forward = _rekey_timelines_with_migration_events(
        timelines,
        (first, second),
        as_of="2025-01-01",
    )
    reverse = _rekey_timelines_with_migration_events(
        timelines,
        (second, first),
        as_of="2025-01-01",
    )

    assert set(forward) == {final_addr}
    assert set(reverse) == {final_addr}


def test_rekey_timelines_native_rebirth_same_wave_chain_does_not_double_migrate() -> None:
    source_addr = LegalAddress(path=(("section", "10"),))
    destination_addr = LegalAddress(path=(("section", "11"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="10 § old lineage"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="1992-10-01",
                    enacted="1992-10-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="10 § native rebirth"),
                    source=OperationSource(statute_id="1992/878", effective="1992-10-01"),
                ),
            ],
        ),
    }
    same_wave = (
        MigrationEvent(
            event_id="mig:test/9-10",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "9"),)),
            to_address=LegalAddress(path=(("section", "10"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
        MigrationEvent(
            event_id="mig:test/10-11",
            kind="renumber",
            from_address=source_addr,
            to_address=destination_addr,
            effective="1992-10-01",
            source_statute="1992/878",
        ),
        MigrationEvent(
            event_id="mig:test/11-12",
            kind="renumber",
            from_address=destination_addr,
            to_address=LegalAddress(path=(("section", "12"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        same_wave,
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {source_addr, destination_addr}
    destination_versions = rekeyed[destination_addr].versions
    assert len(destination_versions) == 1
    assert destination_versions[0].content is not None
    assert destination_versions[0].content.label == "11"
    assert destination_versions[0].source is None

    source_versions = rekeyed[source_addr].versions
    assert len(source_versions) == 1
    assert source_versions[0].content is not None
    assert source_versions[0].content.label == "10"
    assert source_versions[0].source is not None
    assert source_versions[0].source.statute_id == "1992/878"


def test_rekey_timelines_same_wave_incoming_prefix_does_not_double_migrate_sibling_source() -> None:
    address = LegalAddress(path=(("part", "7"), ("chapter", "32"), ("section", "268")))
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                ProvisionVersion(
                    effective="2020-06-01",
                    enacted="2018-08-10",
                    content=IRNode(kind=IRNodeKind.SECTION, label="268", text="268 §"),
                    source=OperationSource(statute_id="2018/731", effective="2020-06-01"),
                ),
            ],
        ),
    }
    same_wave = (
        MigrationEvent(
            event_id="mig:2019/371:part6-part7",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "6"),)),
            to_address=LegalAddress(path=(("part", "7"),)),
            effective="2019-04-01",
            source_statute="2019/371",
        ),
        MigrationEvent(
            event_id="mig:2019/371:part7-part8",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "7"),)),
            to_address=LegalAddress(path=(("part", "8"),)),
            effective="2019-04-01",
            source_statute="2019/371",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        same_wave,
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {address}


def test_rekey_timelines_post_renumber_descendant_stays_with_native_source_lineage() -> None:
    source_addr = LegalAddress(path=(("section", "10"), ("subsection", "2")))
    destination_addr = LegalAddress(path=(("section", "11"), ("subsection", "2")))
    timelines = {
        LegalAddress(path=(("section", "10"),)): ProvisionTimeline(
            address=LegalAddress(path=(("section", "10"),)),
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="historical section 10"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="1992-10-01",
                    enacted="1992-10-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="native rebirth section 10"),
                    source=OperationSource(statute_id="1992/878", effective="1992-10-01"),
                ),
            ],
        ),
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="1992-12-09",
                    enacted="1992-12-09",
                    content=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="native descendant"),
                    source=OperationSource(statute_id="1992/1195", effective="1992-12-09"),
                ),
            ],
        ),
    }
    same_wave = (
        MigrationEvent(
            event_id="mig:test/10-11",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "10"),)),
            to_address=LegalAddress(path=(("section", "11"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
        MigrationEvent(
            event_id="mig:test/11-12",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "11"),)),
            to_address=LegalAddress(path=(("section", "12"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        same_wave,
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {
        LegalAddress(path=(("section", "10"),)),
        LegalAddress(path=(("section", "11"),)),
        source_addr,
    }
    versions = rekeyed[source_addr].versions
    assert len(versions) == 1
    assert versions[0].content is not None
    assert versions[0].content.label == "2"
    assert destination_addr not in rekeyed

def test_rekey_timelines_rewrites_root_num_child_for_migrated_section() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="5",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="5 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Heading"),
                        ),
                    ),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/section:5→section:159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    migrated = rekeyed[destination_addr].versions[0].content
    assert migrated is not None
    assert migrated.label == "159"
    assert migrated.children[0].text == "159 §"


def test_rekey_timelines_merges_ancestor_only_migration_into_native_destination_lineage() -> None:
    source_addr = LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "159")))
    destination_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-12-30",
                    enacted="2020-12-30",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="159",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="159 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Updated heading"),
                        ),
                    ),
                    source=OperationSource(statute_id="2020/1256", effective="2020-12-30"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="native lineage"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/III/2/159→IV/18/159",
        kind="renumber",
        from_address=LegalAddress(path=(("part", "III"), ("chapter", "2"))),
        to_address=LegalAddress(path=(("part", "4"), ("chapter", "18"))),
        effective="2020-12-30",
        source_statute="2020/1256",
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    destination_versions = rekeyed[destination_addr].versions
    assert len(destination_versions) == 2
    assert destination_versions[-1].source is not None
    assert destination_versions[-1].source.statute_id == "2020/1256"


def test_rekey_timelines_same_wave_incoming_section_still_follows_ancestor_migration() -> None:
    source_addr = LegalAddress(path=(("part", "3"), ("chapter", "2"), ("section", "159")))
    destination_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    part_addr = LegalAddress(path=(("part", "3"),))
    timelines = {
        part_addr: ProvisionTimeline(
            address=part_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.PART, label="3", text="part 3 before"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.PART, label="3", text="part 3 same-wave version"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                ),
            ],
        ),
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § migrated same-wave section"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                ),
            ],
        ),
    }
    events = (
        MigrationEvent(
            event_id="mig:2019/371:part3/chapter2/section5-section159",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "3"), ("chapter", "2"), ("section", "5"))),
            to_address=source_addr,
            effective="2019-04-01",
            source_statute="2019/371",
        ),
        MigrationEvent(
            event_id="mig:2019/371:part3-part4",
            kind="renumber",
            from_address=part_addr,
            to_address=LegalAddress(path=(("part", "4"),)),
            effective="2019-04-01",
            source_statute="2019/371",
        ),
        MigrationEvent(
            event_id="mig:2020/1256:part4/chapter2-chapter18",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "4"), ("chapter", "2"))),
            to_address=LegalAddress(path=(("part", "4"), ("chapter", "18"))),
            effective="2021-02-01",
            source_statute="2020/1256",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        events,
        as_of="2026-01-01",
    )

    assert destination_addr in rekeyed
    assert source_addr not in rekeyed
    versions = rekeyed[destination_addr].versions
    assert len(versions) == 1
    assert versions[0].source is not None
    assert versions[0].source.statute_id == "2019/371"


def test_select_pit_lineage_inputs_prefers_rekeyed_native_rebirth_over_scope_changing_migration() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    raw_destination_addr = LegalAddress(path=(("part", "I"), ("chapter", "2"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § historical lineage"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native rebirth"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        raw_destination_addr: ProvisionTimeline(
            address=raw_destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native destination lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/ch1-5→partI-ch2-5",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "I"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert len(migration_event.from_address.path) != len(migration_event.to_address.path)
    assert dict(lineage_decision.timelines) == rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == ()
    assert lineage_decision.lineage_plan.mode == "rekeyed_only"
    assert lineage_decision.reason == "native_rebirth_after_renumber"
    assert set(lineage_decision.timelines) == {source_addr, destination_addr}

    source_versions = lineage_decision.timelines[source_addr].versions
    assert len(source_versions) == 1
    assert source_versions[0].source is not None
    assert source_versions[0].source.statute_id == "2020/1"

    destination_versions = lineage_decision.timelines[destination_addr].versions
    assert len(destination_versions) == 2
    assert destination_versions[0].source is None
    assert destination_versions[1].source is not None
    assert destination_versions[1].source.statute_id == "2019/1"


def test_select_pit_lineage_inputs_keeps_rekeyed_with_migrations_for_leaf_stable_scope_renumber() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    raw_destination_addr = LegalAddress(path=(("part", "I"), ("chapter", "2"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § migrated lineage"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        raw_destination_addr: ProvisionTimeline(
            address=raw_destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native destination lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/ch1-5→partI-ch2-5",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "I"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert dict(lineage_decision.timelines) == rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "leaf_stable_scope_renumber"
    assert set(lineage_decision.timelines) == {destination_addr}

    active = select_active_version(lineage_decision.timelines[destination_addr], as_of="2025-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2020/1"
    assert active.content is not None
    assert "migrated lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_keeps_rekeyed_with_migrations_for_noncolliding_scope_renumber() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="migrated lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:5->2/7",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    assert dict(lineage_decision.timelines) == rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "default_migration_projection"
    assert set(lineage_decision.timelines) == {destination_addr}

    active = select_active_version(lineage_decision.timelines[destination_addr], as_of="2002-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2001/1"
    assert active.content is not None
    assert active.content.label == "7"
    assert "migrated lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_keeps_rekeyed_with_migrations_for_noncolliding_scope_move() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    assert dict(lineage_decision.timelines) == rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "default_migration_projection"
    assert set(lineage_decision.timelines) == {destination_addr}

    active = select_active_version(lineage_decision.timelines[destination_addr], as_of="2002-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2001/1"
    assert active.content is not None
    assert active.content.label == "7"
    assert "moved lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_ignores_future_scope_move_when_choosing_current_pit_lineage_inputs() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:future",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2005-01-01",
        source_statute="2005/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2004-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2004-01-01",
    )

    assert dict(lineage_decision.timelines) == rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "default_migration_projection"
    assert set(lineage_decision.timelines) == {source_addr}

    active = select_active_version(lineage_decision.timelines[source_addr], as_of="2004-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2001/1"
    assert active.content is not None
    assert "moved lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_reports_destination_occupancy_collision_for_scope_move() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="1999-01-01",
                    enacted="1999-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="native destination lineage"),
                    source=OperationSource(statute_id="1999/1", effective="1999-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:occupied",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    assert dict(lineage_decision.timelines) == raw_timelines
    assert lineage_decision.timeline_source == "raw"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "raw_with_migrations"
    assert lineage_decision.reason == "destination_occupancy_collision"


def test_classify_finland_lineage_bridge_reports_native_rebirth_after_renumber() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § old lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native rebirth"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(address=destination_addr, versions=[]),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/ch1-5→part1-ch2-5",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "1"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    classification = _classify_finland_lineage_bridge(
        raw_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert classification == FinlandLineageBridgeClassification(
        native_rebirth_after_renumber=True,
        leaf_stable_scope_renumber=True,
        active_scope_changing=True,
        noncolliding_scope_migrations=False,
        destination_occupancy_collision=True,
    )


def test_replay_xml_exposes_finland_lineage_bridge_classification(
    replay_2009_953_legal_pit: ReplayResult,
) -> None:
    assert replay_2009_953_legal_pit.materialization_spec is not None
    bridge_classification = replay_2009_953_legal_pit.materialization_spec.bridge_classification
    assert bridge_classification == FinlandLineageBridgeClassification(
        native_rebirth_after_renumber=True,
        leaf_stable_scope_renumber=False,
        active_scope_changing=False,
        noncolliding_scope_migrations=False,
        destination_occupancy_collision=False,
    )


def test_materialize_pit_ex_rejects_both_lineage_plan_and_migration_events() -> None:
    addr = LegalAddress(path=(("section", "5"),))
    migration_event = MigrationEvent(
        event_id="mig:test:5->7",
        kind="move",
        from_address=addr,
        to_address=LegalAddress(path=(("section", "7"),)),
        effective="2001-01-01",
        source_statute="2001/1",
    )
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="text"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }

    with pytest.raises(ValueError, match="either lineage_plan or migration_events"):
        materialize_pit_ex(
            timelines,
            "2002-01-01",
            migration_events=(migration_event,),
            lineage_plan=MaterializationLineagePlan(
                mode="raw_with_migrations",
                migration_events=(migration_event,),
            ),
        )


def test_lineage_plan_round_trips_core_materialize_for_destination_occupancy_collision() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="1999-01-01",
                    enacted="1999-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="native destination lineage"),
                    source=OperationSource(statute_id="1999/1", effective="1999-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:occupied",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )
    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    result = materialize_pit_ex(
        lineage_decision.timelines,
        "2002-01-01",
        base=IRStatute(
            statute_id="test/occupancy-roundtrip",
            title="Occupancy roundtrip",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="5", text="base source"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="7", text="base destination"),),
                    ),
                ),
            ),
        ),
        lineage_plan=lineage_decision.lineage_plan,
    )

    assert result.materialization_status == "degraded_missing_scope"
    assert result.certificate is not None
    assert result.certificate.ambiguous_address_count == 1


def test_build_replay_products_requires_caller_to_lower_temporal_events() -> None:
    """Callers must lower effect intents before calling build_replay_products.

    The temporal phase boundary is now explicit: the caller owns the lowering
    step; build_replay_products only accepts already-lowered temporal_events.
    """
    import datetime as dt

    from lawvm.core.effect_intent import Commencement
    from lawvm.core.effect_lowering import lower_effect_intents_to_temporal_events

    ctx = StatuteContext(
        id="test/effect-intents-caller-lowers",
        title="Caller lowers effect intents",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    intent = Commencement(
        effective_date=dt.date(2010, 1, 1),
        raw_text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2010.",
    )

    # Caller explicitly lowers before calling build_replay_products
    lowered_events = tuple(
        lower_effect_intents_to_temporal_events(
            [intent],
            source_ref="test/effect-intents-caller-lowers",
            source_title="Caller lowers effect intents",
            source_issue_date=dt.date(2009, 1, 1),
            source_effective_date=dt.date(2010, 1, 1),
            group_id_prefix="explicit-lowering",
            target_statute="test/effect-intents-caller-lowers",
        )
    )

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/effect-intents-caller-lowers",
        replay_fold_state=replay_fold_state,
        lo_ops_out=[],
        as_of="2011-01-01",
        temporal_events=lowered_events,
    )

    assert len(products.temporal_events) == 1
    assert products.temporal_events[0].kind == "commence"
    assert products.temporal_events[0].source is not None
    assert products.temporal_events[0].source.title == "Caller lowers effect intents"
    assert products.temporal_events[0].source.enacted == "2009-01-01"
    assert products.temporal_events[0].source.effective == "2010-01-01"


def test_build_replay_products_can_enforce_strict_johto_temporal_for_mismatch() -> None:
    ctx = StatuteContext(
        id="test/temporal-default-strict",
        title="Temporal default strict",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="finland-johto:1999/1",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2010-01-01",
                effective="2010-06-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-default-strict",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        temporal_events=(
            TemporalEvent(
                event_id="ev:different-group",
                group_id="finland-johto:2000/2",
                kind="commence",
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-default-strict",
                    effective="2010-01-01",
                ),
                scope=TemporalScope(target_statute="test/temporal-default-strict"),
            ),
        ),
    )

    assert products.timelines is not None
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.content is not None
    assert active.content.text == "Updated"
    assert active.effective == "2010-06-01"


def test_build_replay_products_does_not_synthesize_fallback_for_covered_group() -> None:
    ctx = StatuteContext(
        id="test/temporal-covered-group",
        title="Temporal covered group",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="finland-johto:1999/1",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2010-01-01",
                effective="2010-06-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-covered-group",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        temporal_events=(
            TemporalEvent(
                event_id="ev:matching-group",
                group_id="finland-johto:1999/1",
                kind="commence",
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-covered-group",
                    effective="2010-01-01",
                ),
                scope=TemporalScope(target_statute="test/temporal-covered-group"),
            ),
        ),
    )

    assert len(products.temporal_events) == 1
    assert products.temporal_events[0].group_id == "finland-johto:1999/1"
    assert products.timelines is not None
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.effective == "2010-01-01"


def test_materialize_pit_overlays_active_schedule_versions() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="body"),))
    schedule = IRNode(
        kind=IRNodeKind.SCHEDULE,
        label="1",
        text="old schedule",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="old paragraph"),),
    )
    base = IRStatute(
        statute_id="test/schedules",
        title="Schedules",
        body=body,
        supplements=(schedule,),
    )
    timelines = compile_timelines(base, [], base_date="2020-01-01")

    schedule_addr = LegalAddress(path=(("schedule", "1"),))
    timelines[schedule_addr] = ProvisionTimeline(
        address=schedule_addr,
        versions=[
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2024-01-01",
                content=IRNode(
                    kind=IRNodeKind.SCHEDULE,
                    label="1",
                    text="new schedule",
                    children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="new paragraph"),),
                ),
                source=OperationSource(statute_id="test/act", effective="2024-01-01"),
            )
        ],
    )

    pit = materialize_pit(timelines, "2025-01-01", base=base)

    assert len(pit.supplements) == 1
    assert pit.supplements[0].text == "new schedule"
    assert pit.supplements[0].children[0].text == "new paragraph"

def test_build_replay_products_rejects_payloadless_replace_timeline_ops() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),))
    ctx = StatuteContext(
        id="test/missing-payload",
        title="Missing payload",
        base_ir=body,
        base_xml_bytes=b"<body/>",
    )
    replay_fold = ReplayState(ir=copy.deepcopy(body))
    op = LegalOperation(
        op_id="test_replace_missing_payload",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=None,
        source=OperationSource(statute_id="2024/1", effective="2024-01-01"),
    )

    with pytest.raises(RuntimeError, match="FI_TIMELINE_PAYLOADLESS_REPLACE"):
        build_replay_products(
            ctx=ctx,
            statute_id="test/missing-payload",
            replay_fold_state=replay_fold,
            lo_ops_out=[op],
        )


@pytest.mark.slow
@pytest.mark.parametrize(
    "statute_id",
    [
        "1974/412",  # chapter:4 duplicate from INSERT 4 luku onto existing ch4
        "1961/264",  # chapter:12 duplicate
        "1989/495",  # chapter:2 duplicate
        "1997/689",  # chapter:8 duplicate
        "2001/604",  # chapter:4 duplicate
        "2009/1698",  # chapter:9 duplicate
    ],
)
def test_chapter_insert_onto_existing_chapter_produces_no_duplicate_label(statute_id: str) -> None:
    """INSERT chapter:X onto an already-existing base chapter must merge, not duplicate.

    Regression for the legal_pit mode path where `replace_same_numbered_container_insert=False`
    previously fell through to insert_sorted, creating a duplicate chapter label.
    This is replay-fold invariant coverage; PIT/materialization coverage lives
    in the statute-specific replay-product tests.
    """
    stop_before_by_statute = {
        "1961/264": "2000/689",
    }
    replay = pinned_replay(
        statute_id,
        mode="legal_pit",
        quiet=True,
        build_full_products=False,
        stop_before=stop_before_by_statute.get(statute_id),
    )
    viols = [
        dict(getattr(f, "detail", {}) or {}).get("violation", "")
        for f in replay.findings
        if dict(getattr(f, "detail", {}) or {}).get("barrier_code") == "APPLY.TREE_INVARIANT_VIOLATION"
    ]
    chapter_dups = [v for v in viols if "duplicate chapter" in v]
    assert chapter_dups == [], f"{statute_id}: unexpected duplicate chapter: {chapter_dups}"


def test_replay_xml_1974_412_preserves_1979_middle_subsection_through_1991_update() -> None:
    """1979/373 + 1991/1423 must leave chapter 4 / section 2 with moments 1-4.

    1979/373 inserts new 2 and 3 moments under 4 luvun 2 §. 1991/1423 later
    replaces moments 1 and 3 and inserts moment 4. If 1979/373 collapses to a
    chapter-level insert, replay reaches 1991 with only moment 1 and the old
    1979 middle moment disappears permanently.
    """
    replay = pinned_replay("1974/412", mode="official_consolidation", quiet=True)

    section = replay.materialized_state.find_node("section", "2", "chapter", "4")
    assert section is not None, "chapter 4 / section 2 must exist"

    subsections = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section.children
        if child.kind == IRNodeKind.SUBSECTION and child.label
    }

    assert list(subsections) == ["1", "2", "3", "4"]
    assert subsections["2"].startswith("Asevelvollisuuslain (452/50) nojalla annetun")
    assert subsections["3"].startswith("Sotilasrikossäännösten alainen")
    assert subsections["4"].startswith("Julkisyhteisön luottamushenkilön")


def test_replay_xml_2011_756_inserts_section_8a_into_chapter_5() -> None:
    """Regression for cross-chapter INSERT remap bug.

    Amendment 2022/33 inserts section 8a into chapter 5. The master already has
    section 8a in chapter 2 (from 2016/1115). The chapter-remap logic was wrongly
    remapping the INSERT to chapter 2 because the section label existed there,
    silently dropping the new chapter:5/section:8a.

    Fix: pure-INSERT groups are exempt from the chapter-remap correction because
    they create new sections that don't yet exist in the target chapter.
    """
    replay = pinned_replay("2011/756", mode="legal_pit", quiet=True)
    body = replay.materialized_state.ir

    def _chapter_section_labels(chapter_label: str) -> list[str]:
        chapter = next(
            (child for child in body.children if child.kind.name == "CHAPTER" and child.label == chapter_label),
            None,
        )
        if chapter is None:
            return []
        return [child.label for child in chapter.children if child.kind.name == "SECTION"]

    # Section 8a should be in chapter 2 (from 2016/1115) AND in chapter 5 (from 2022/33)
    assert "8a" in _chapter_section_labels("2"), "chapter 2 must still have section 8a from 2016/1115"
    assert "8a" in _chapter_section_labels("5"), "chapter 5 must have section 8a inserted by 2022/33"


@pytest.mark.slow
def test_replay_xml_2012_916_keeps_section_8_in_chapter_13(
    replay_2012_916_finlex_oracle: ReplayResult,
) -> None:
    """Whole-chapter REPLACE must not drop new sections shadowed in another chapter.

    Amendment 2022/337 replaces chapter 13 and also separately replaces
    chapter 3 / section 8. The chapter REPLACE payload legitimately includes
    chapter 13 / section 8, and apply must not strip it just because another
    chapter has a same-labeled standalone section op.
    """
    chapter = next(
        (
            child
            for child in replay_2012_916_finlex_oracle.materialized_state.ir.children
            if child.kind.name == "CHAPTER" and child.label == "13"
        ),
        None,
    )
    assert chapter is not None, "chapter 13 must exist"
    section_labels = [child.label for child in chapter.children if child.kind.name == "SECTION"]
    assert "8" in section_labels, "chapter 13 must keep section 8 from 2022/337"


@pytest.mark.slow
def test_replay_xml_2012_916_keeps_section_1_family_in_chapter_13(
    replay_2012_916_finlex_oracle: ReplayResult,
) -> None:
    """Later degraded subsection inserts must not erase chapter 13 / section 1.

    The current tree carries accepted degraded source lanes for 2022/244 and
    2023/371 that rewrite `13 luku 1 § 1 momentti` as subsection-level insert
    snapshots with flat content rather than preserving the earlier paragraph
    structure from 2022/337. The durable replay-products contract here is that
    chapter 13 / section 1 stays in place with its subsection family intact,
    not that the original paragraph numbering survives those later source-owned
    subsection replacements.
    """
    chapter = next(
        (
            child
            for child in replay_2012_916_finlex_oracle.materialized_state.ir.children
            if child.kind.name == "CHAPTER" and child.label == "13"
        ),
        None,
    )
    assert chapter is not None, "chapter 13 must exist"
    section = next(
        (child for child in chapter.children if child.kind.name == "SECTION" and child.label == "1"),
        None,
    )
    assert section is not None, "chapter 13 / section 1 must exist"
    heading = next((child.text for child in section.children if child.kind.name == "HEADING"), "")
    assert heading == "Käyttötarkoitukset"
    subsection = next((child for child in section.children if child.kind.name == "SUBSECTION" and child.label == "1"), None)
    assert subsection is not None, "section 1 must keep subsection 1"
    subsection_labels = [child.label for child in section.children if child.kind.name == "SUBSECTION"]
    # The Finlex consolidation keeps §1 as a three-momentti family: the kohta
    # list lives inside the first momentti (1)–4) kohta), so the section has
    # subsections 1–3, not a fourth momentti. Replay must match that family.
    assert subsection_labels[:3] == ["1", "2", "3"]
    subsection_text = " ".join(
        (child.text or "").strip()
        for child in subsection.children
        if (child.text or "").strip()
    )
    assert "Työ- ja elinkeinotoimiston asiakastietojärjestelmää käytetään" in subsection_text


@pytest.mark.slow
def test_replay_xml_2012_916_surfaces_degraded_2023_371_subsection_lane(
    replay_2012_916_finlex_oracle: ReplayResult,
) -> None:
    """The degraded 2023/371 subsection lane must stay explicitly owned.

    This statute currently carries accepted uncovered-body degradation for the
    `13 luku 1 § 1 momentti 4 kohta` lane: replay keeps the section family
    alive, but the subsection-targeted follow-up is not a clean deterministic
    apply path. Future runs should not have to rediscover that from scratch.
    """
    pathology_rows = [
        row
        for row in replay_2012_916_finlex_oracle.source_pathology_rows()
        if row.get("source_statute") == "2023/371"
    ]
    assert any(
        row.get("code") == "ITEM_TARGET_STRUCTURE_ABSENT"
        and row.get("target_label") == "1 § 1 mom 4 kohta"
        for row in pathology_rows
    )

    degraded_findings = [
        finding
        for finding in replay_2012_916_finlex_oracle.findings
        if finding.kind == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
        and finding.source_statute == "2023/371"
    ]
    assert degraded_findings, "2023/371 degraded uncovered-body lane must stay visible"

    failed_ops = [
        finding
        for finding in replay_2012_916_finlex_oracle.findings
        if finding.kind == "APPLY.FAILED_OPERATION"
        and (finding.detail or {}).get("amendment_id") == "2023/371"
    ]
    # The 2023/371 op targets `13 luku 1 § 1 mom 4 kohta`, an item inside a
    # momentti that carries flat text and no paragraph/item children, so the
    # refined failure taxonomy reports the specific `item_no_paragraphs` reason
    # rather than the generic `no_deterministic_path`.
    assert any(
        (finding.detail or {}).get("reason_code") == "item_no_paragraphs"
        and (finding.detail or {}).get("target_section") == "1"
        and (finding.detail or {}).get("target_chapter") == "13"
        for finding in failed_ops
    )


def test_replay_xml_1995_370_does_not_leave_stale_cross_chapter_23_snapshots() -> None:
    """Cross-chapter same-label chapter snapshots must not preserve stale 23 § families."""
    replay = pinned_replay("1995/370", mode="official_consolidation", quiet=True)

    section_paths = {str(addr) for addr in replay.timelines if "section:23" in str(addr)}

    assert "chapter:5/section:23" not in section_paths
    assert "chapter:3/section:23" not in section_paths
    assert "chapter:6/section:23" in section_paths


def test_replay_xml_1997_1339_anaphoric_pykala_ill_with_provenance_qualifier(
    replay_1997_1339_finlex_oracle_full_products: ReplayResult,
) -> None:
    """Regression for anaphoric 'pykälään, sellaisena kuin se on ..., uusi N momentti/kohta'.

    Amendment 2015/1752 to 1997/1339 (kirjanpitoasetus) has a lisätään clause whose last
    target is '5 a lukuun uusi 2 §'. The clause also contains 'pykälään, sellaisena kuin
    se on osaksi asetuksissa 748/2001 ja 1313/2004, uusi 7 momentti' — an anaphoric section
    reference with an interleaved provenance qualifier (COMMA + CITATION_SPAN before 'uusi').

    The parser previously stopped at 'pykälään' because the handler only handled
    'pykälään uusi N' (no qualifier), not 'pykälään , [CITE] uusi N'. Once that pattern
    broke the parse chain, all subsequent targets including '5 a lukuun uusi 2 §' were
    silently dropped, leaving chapter 5a with only §1 and missing §2.
    """
    replay = replay_1997_1339_finlex_oracle_full_products
    ch5a = replay.materialized_state.find_chapter("5a")
    assert ch5a is not None, "chapter 5a must be present in replay"
    sec_labels = [c.label for c in ch5a.children if c.kind.name == "SECTION"]
    assert "2" in sec_labels, "§2 must be inserted into chapter 5a by 2015/1752"


def test_replay_xml_2020_87_inserts_subsection_into_existing_section() -> None:
    """Regression for Pattern B3: '4 §:n uusi 2 momentti' must insert a new subsection
    into section 4 rather than replacing the whole section (which would lose subsection 1).

    Amendment 2020/326 uses the pattern '§:GEN uusi N momentti:NOM' — genitive case on
    the section reference followed immediately by 'uusi'.  The parser previously missed
    this pattern and emitted a whole-section L op (momentti=0) instead of a subsection
    INSERT (momentti=2), causing subsection 1 to be overwritten.
    """
    replay = pinned_replay("2020/87", mode="official_consolidation", quiet=True)
    sec4 = replay.materialized_state.find_section("4")
    assert sec4 is not None, "section 4 must be present in replay"
    sub_labels = [
        child.label
        for child in sec4.children
        if child.kind.name == "SUBSECTION"
    ]
    assert "1" in sub_labels, "subsection 1 must survive after 2020/326 inserts subsection 2"
    assert "2" in sub_labels, "subsection 2 must be inserted by 2020/326"


def test_replay_xml_2017_571_inserts_second_subsection_into_2002_1244_section_1(
    replay_2002_1244_finlex_oracle_full_products: ReplayResult,
) -> None:
    """DOC:ILL + provenance must still insert subsection 2 into existing section 1.

    2017/571 says ``lisätään asetukseen, sellaisena kuin se on asetuksessa
    543/2015 uusi 1 §:n 2 momentti``. The DOC:ILL parser path previously failed
    to skip the comma+provenance span and degraded this into a whole-section
    insert, wiping the older subsection chain in replay.
    """
    replay = replay_2002_1244_finlex_oracle_full_products
    sec1 = replay.materialized_state.find_section("1")
    assert sec1 is not None, "section 1 must be present in replay"
    sub_labels = [
        child.label
        for child in sec1.children
        if child.kind.name == "SUBSECTION"
    ]
    assert "1" in sub_labels, "subsection 1 must survive after 2017/571 inserts subsection 2"
    assert "2" in sub_labels, "subsection 2 must be inserted by 2017/571"


def test_replay_xml_1993_1495_1994_931_keeps_infinitive_item_insert() -> None:
    """1994/931 says ``sekä lisätä 1 §:ään uuden 7 kohdan``.

    The infinitive ``lisätä`` must compile as an insertion verb; otherwise the
    old item 7 remains at slot 7 and the source-owned hunting-law item is absent.
    """
    replay = replay_xml_for_test("1993/1495", mode="official_consolidation", quiet=True)
    sec1 = replay.materialized_state.find_section("1")
    assert sec1 is not None, "section 1 must be present in replay"
    text = " ".join(irnode_to_text(sec1).split())

    assert "metsästyslain (615/93) 19 §:ssä" in text
    assert "sekä metsästysasetuksen (666/93)" in text
    assert "7) metsästyslain" in text
    assert "8) valtion varoista myönnettävää avustusta" in text
    assert "9) ministeriölle kuuluvat yleiset ohjaus-" in text


def test_replay_xml_1998_986_inserts_provenance_qualified_plural_subsections_into_section_22() -> None:
    """Plural `uusi N ja M momentti` must survive partial PEG success on mixed clauses.

    Amendment 2005/865 to 1998/986 says:
    `muutetaan 22 §:n 1 momentti sekä lisätään 22 §:ään, sellaisena kuin se on
    osaksi ... 693/2003, uusi 5 ja 6 momentti`.

    The PEG/legal-op path currently recovers the replace for `1 momentti` but can
    miss the coordinated inserts `5` and `6`. The fallback insert heuristic must
    be allowed to add those missing subsection INSERT ops without discarding the
    PEG-produced replace.
    """
    replay = pinned_replay("1998/986", mode="official_consolidation", quiet=True)
    sec22 = replay.materialized_state.find_section("22")
    assert sec22 is not None, "section 22 must be present in replay"
    sub_labels = [
        child.label
        for child in sec22.children
        if child.kind.name == "SUBSECTION"
    ]
    assert "5" in sub_labels, "subsection 5 must be inserted by 2005/865"
    assert "6" in sub_labels, "subsection 6 must be inserted by 2005/865"


def test_replay_xml_2019_571_recovers_17g_with_sellaisenaan_body_text() -> None:
    """Ordinary body text containing ``sellaisenaan`` must not be ignored as provenance.

    Amendment 2025/863 inserts 3 luku / 17 a-17 h §. The parser currently
    misses that law-level suffix range in the full mixed clause, so uncovered
    body recovery owns the section insertions. 17 g § contains the operative
    word ``sellaisenaan``; body coverage previously tagged that whole section
    as provenance and made it non-actionable.
    """
    replay = replay_xml_for_test("2019/571", mode="official_consolidation", quiet=True)
    section = replay.materialized_state.find_section("17g", "3")

    assert section is not None, "2025/863 must insert chapter 3 section 17g"
    text = " ".join(irnode_to_text(section).split())
    assert "siirtää näiltä vastaanotetut tiedot sellaisenaan" in text


def test_replay_xml_2020_811_inserts_4a_and_11a_sections() -> None:
    """Authority-attributed finite-verb insertions must not be dropped pre-Phase-2.

    2021/278 and 2021/407 are phrased as ``Verohallinto lisää ... uuden N §:n``.
    The replay ingress used an operative-keyword guard before
    ``normalize_and_compile_ops()`` and previously omitted finite present
    ``lisää``, so both amendments were silently skipped and sections 11a and 4a
    never entered replay.
    """

    replay = pinned_replay("2020/811", mode="official_consolidation", quiet=True)

    assert replay.find_section("4a") is not None, "2021/407 must insert section 4a"
    assert replay.find_section("11a") is not None, "2021/278 must insert section 11a"


@pytest.mark.slow
def test_replay_xml_2004_301_2016_454_snapshots_86b_under_chapter_five() -> None:
    """2016/454 must not mint a bare root timeline for uniquely hosted §86b."""
    lo_ops: list[LegalOperation] = []
    replay_xml_for_test("2004/301", quiet=True, lo_ops_out=lo_ops, stop_before="2016/505")
    snapshot = next(op for op in lo_ops if op.op_id == "snapshot_section_86b")
    assert str(snapshot.target) == "chapter:5/section:86b"


@pytest.mark.slow
def test_replay_xml_2004_301_has_no_orphan_bare_86b_timeline_after_repeal() -> None:
    """2023/216 repeal must not leave a viewer-visible bare §86b tombstone."""
    replay = replay_xml_for_test("2004/301", mode="legal_pit", quiet=True, as_of="2024-01-01")
    assert replay.products is not None
    assert replay.products.timelines is not None
    bare_addr = LegalAddress(path=(("section", "86b"),))
    assert bare_addr not in replay.products.timelines


@pytest.mark.slow
def test_replay_xml_2004_301_section_142_item_three_has_no_duplicate_kohta_marker() -> None:
    """§142 2 mom 3 kohta must not carry a redundant ``3)`` body prefix.

    2022/821 uncovered recovery flattened the item body onto ``paragraph.text``
    while the structured label already owns the kohta marker. The viewer renders
    ``label + text``, so a carried ``3)`` prefix becomes ``3)3)`` on screen.
    """
    replay = replay_xml_for_test("2004/301", mode="legal_pit", quiet=True, as_of="2024-01-01")
    section142 = replay.materialized_state.find_section("142", "9")
    assert section142 is not None
    subsection2 = next(
        child
        for child in section142.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "2"
    )
    item3 = next(
        child
        for child in subsection2.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label == "3"
    )
    assert item3.text is not None
    assert not item3.text.lstrip().startswith("3)")
    assert item3.text.startswith("kolmannen maan kansalaisen")


@pytest.mark.slow
def test_replay_xml_2004_301_section_78_moment_three_nests_abc_under_item_four() -> None:
    """2018/121 §78 3 mom must keep a–c under 4) jos:, not flat siblings before 1–7."""
    replay = replay_xml_for_test("2004/301", quiet=True, stop_before="2018/720")
    section78 = replay.materialized_state.find_section("78", "5")
    assert section78 is not None
    subsection3 = next(
        child
        for child in section78.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "3"
    )
    top_paragraph_labels = [
        child.label
        for child in subsection3.children
        if child.kind is IRNodeKind.PARAGRAPH
    ]
    assert top_paragraph_labels == ["1", "2", "3", "4", "5", "6", "7"]
    item_four = next(
        child
        for child in subsection3.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label == "4"
    )
    nested_labels = [
        child.label
        for child in item_four.children
        if child.kind is IRNodeKind.SUBPARAGRAPH
    ]
    assert nested_labels == ["a", "b", "c"]


@pytest.mark.slow
def test_replay_xml_2017_320_section_19_definitions_do_not_emit_flattened_sublist_warning() -> None:
    """Part II ch.2 §19 subs:1 is a legitimate definitions list, not a nesting bug."""
    replay = replay_xml_for_test("2017/320", mode="legal_pit", quiet=True)
    flat_warnings = [
        finding
        for finding in replay.findings
        if finding.kind == "flattened_sublist_family_warning"
        and "section:19" in str(finding.detail.get("path") or "")
    ]
    assert flat_warnings == []
    section = replay.materialized_state.find_section("19", chapter_num="2", part_num="2")
    assert section is not None
    assert check_invariants(section) == []

    subsection = next(child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    paragraph_labels = [child.label for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH]
    assert paragraph_labels == ["1", "2"]
    item_one = next(child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH and child.label == "1")
    subparagraph_labels = [child.label for child in item_one.children if child.kind is IRNodeKind.SUBPARAGRAPH]
    assert subparagraph_labels == ["a", "b", "c"]


@pytest.mark.slow
def test_replay_xml_2017_320_2018_984_bare_section_replace_declares_observed_write() -> None:
    replay = replay_xml_for_test("2017/320", mode="official_consolidation", quiet=True)

    undeclared = [
        finding
        for finding in replay.findings
        if finding.kind == "APPLY.REPLAY_UNDECLARED_TREE_TOUCH"
        and finding.detail.get("source_statute") == "2018/984"
        and finding.detail.get("op_id") == "mixed_bare_section_replace_18_11"
    ]
    assert undeclared == []

    receipt = next(
        receipt
        for receipt in replay.write_receipts
        if receipt.op_id == "mixed_bare_section_replace_18_11"
    )
    assert receipt.landed_primary_path == (
        ("hcontainer", ""),
        ("part", "2"),
        ("chapter", "3"),
    )


@pytest.mark.slow
def test_replay_xml_2004_301_2023_389_does_not_duplicate_applicability_subsections() -> None:
    """2023/389 moment-scoped uncovered merges must not leave stale 72 a clauses.

    Amendment 2023/389 changes only ``74 §:n 4 momentti`` and ``75 §:n 3
    momentti``. Uncovered-body omission merge previously bound the lone payload
    subsection to moment 2 while moment 4 kept the older ``72 a`` applicability
    wording, producing near-duplicate ``72 ja 72 b`` / ``72, 72 a ja 72 b`` pairs.
    """
    replay = replay_xml_for_test("2004/301", mode="legal_pit", quiet=True)
    ms = replay.materialized_state

    section74 = ms.find_section("74", "5")
    section75 = ms.find_section("75", "5")
    assert section74 is not None
    assert section75 is not None

    text74 = " ".join(irnode_to_text(section74).split())
    text75 = " ".join(irnode_to_text(section75).split())

    assert text74.count("Oleskeluluvan myöntämiseen ei sovelleta 72") == 1
    assert "72, 72 a ja 72 b" not in text74
    assert "Jos työnteko kestää kauemmin" in text74
    assert "Oleskeluluvan myöntämiseen ei sovelleta 72 ja 72 b §:ää." in text74

    assert text75.count("Oleskeluluvan myöntämiseen ei sovelleta 72") == 1
    assert "72, 72 a ja 72 b" not in text75
    assert "39 §:n mukaisesti" in text75
    assert "Oleskeluluvan myöntämiseen ei sovelleta 72 ja 72 b §:ää." in text75


def test_replay_xml_1999_1352_places_inserted_section_headings_after_num() -> None:
    """``N §:ään uusi otsikko`` inserts the section's own heading after the num.

    2025/12 to 1999/1352 says ``lisätään 3 §:ään uusi otsikko, 4 §:ään ... uusi
    otsikko ja 6 §:ään uusi otsikko``.  The new heading is the section's own
    ``otsikko`` and must render as ``N § Otsikko`` (num then heading), not as a
    preceding heading block ``Otsikko N §``.  Section 4 also receives a
    same-amendment subsection replace (``4 §:n 2 momentti``); the section-snapshot
    rebase onto the prior exact parent must carry the new heading forward rather
    than inherit the headingless prior snapshot.
    """
    replay = pinned_replay("1999/1352", mode="official_consolidation", quiet=True)

    expected_headings = {
        "3": "Osakekannan omistus",
        "4": "Hallinnolliset säännökset",
        "6": "Voimaantulo",
    }
    for label, heading_text in expected_headings.items():
        section = replay.materialized_state.find_section(label)
        assert section is not None, f"section {label} must be present in replay"
        kinds = [child.kind for child in section.children]
        assert IRNodeKind.NUM in kinds, f"section {label} must keep its num"
        assert IRNodeKind.HEADING in kinds, (
            f"section {label} must carry the inserted heading {heading_text!r}"
        )
        assert kinds.index(IRNodeKind.NUM) < kinds.index(IRNodeKind.HEADING), (
            f"section {label} heading must follow the num, not precede it"
        )
        heading = next(child for child in section.children if child.kind is IRNodeKind.HEADING)
        assert irnode_to_text(heading).strip() == heading_text


def test_replay_xml_1994_1575_subsection_insert_preserves_shifted_live_siblings() -> None:
    """A new inserted momentti must shift later live momentit in timeline export.

    1998/492 inserts a new 4 § 2 mom and says the old 2 and 3 mom become 3 and 4.
    The live replay fold already performs that shift; the section-snapshot export
    must mirror it instead of merging the new payload over old label 2 and leaving
    old label 3 at 3.
    """
    replay = replay_xml_for_test("1994/1575", mode="legal_pit", quiet=True)

    section = replay.materialized_state.find_section("4")
    assert section is not None
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    texts_by_label = {
        str(subsection.label): " ".join(irnode_to_text(subsection).split())
        for subsection in subsections
        if subsection.label
    }

    assert "3" in texts_by_label
    assert "4" in texts_by_label
    assert "kirjaimet FIN" in texts_by_label["3"]
    assert "kirjaimet KAL" in texts_by_label["4"]


@pytest.mark.slow
def test_replay_xml_1999_132_2024_899_cited_section_replace_rebirths_chapter_parent() -> None:
    """2024/899 replaces 131 § by citing 2014/41 after chapter 19 was repealed.

    The cited-version selector and replay history prove the exact historical
    path ``19 luku / 131 §``.  Replay may scaffold that parent and insert the
    replacement, but the recovery must be witnessed.
    """
    failed_ops = []
    pathologies = []

    replay = replay_xml_for_test(
        "1999/132",
        mode="official_consolidation",
        quiet=True,
        failed_ops_out=failed_ops,
        source_pathologies_out=pathologies,
    )

    section = replay.materialized_state.find_section("131", "19")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert "Rakentamislupahakemus" in text
    assert "rakentamislupahakemuksen ratkaisemiseksi tarvittava olennainen selvitys" in text
    assert not any(
        failed.amendment_id == "2024/899"
        and failed.target_unit_kind == "section"
        and failed.target_section == "131"
        for failed in failed_ops
    )
    assert any(
        pathology.code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
        and pathology.source_statute == "2024/899"
        and pathology.detail.get("recovery_kind")
        == "section_replace_bootstrap_cited_parent_scaffold"
        for pathology in pathologies
    )


# ---------------------------------------------------------------------------
# StageResult endgame WAIST #3 — the structural write-footprint carrier
# ---------------------------------------------------------------------------


def test_structural_stage_carrier_is_populated_and_clean_on_green_corpus(
    replay_1997_1339_finlex_oracle_full_products: ReplayResult,
) -> None:
    """The #3 carrier carries a clean structural footprint account on green corpus.

    ``ReplayProducts.structural_stage`` aggregates the per-op
    ``structural_stage_result`` accounts over every landed write. On the green
    corpus every container write explains its boundary
    (``divergence_explained=True``) → empty blocking residuals + clean coverage.
    This is a CONTRACT test: the cert-consumer + severance fire-drill land in the
    next (cert) lane.
    """
    replay = replay_1997_1339_finlex_oracle_full_products
    stage = replay.products.structural_stage
    assert stage is not None, "full-products replay must carry the #3 structural stage"
    # The carrier is anchored on the replay's materialized IR tree.
    assert stage.value is replay.products.materialized_state.ir
    # Coverage: footprint paths, all owned, a genuine partition, and clean.
    assert stage.coverage.unit == "paths"
    assert stage.coverage.owned > 0, "the replay landed writes with a declared footprint"
    assert stage.coverage.is_partition()
    assert stage.coverage.is_clean
    assert stage.coverage.violation == 0
    # No blocking residuals: every container write explains its boundary.
    assert stage.residuals == ()
    assert not stage.has_blocking_residual
    # Authority is the neutral firewall surface (authorization rides #7).
    assert stage.authority.is_neutral


def test_aggregate_structural_stage_surfaces_unexplained_divergence() -> None:
    """A landed write with unexplained divergence becomes one blocking residual.

    Aggregator unit test (no corpus): one receipt whose ``bound != landed`` with
    no named recovery rule (``divergence_explained is False``) folds into exactly
    one blocking ``unowned_violation`` residual and ``coverage.violation == 1``;
    an explained write contributes only owned footprint.
    """
    from lawvm.core.write_receipt import WriteReceipt
    from lawvm.finland.replay_products import aggregate_structural_stage

    section_1: tuple[tuple[str, str], ...] = (("section", "1"),)
    section_2: tuple[tuple[str, str], ...] = (("section", "2"),)
    explained = WriteReceipt(
        op_id="op-explained",
        helper="fi.apply.resolved_op_write",
        action="replace",
        bound_target_path=section_1,
        landed_primary_path=section_1,
        replaced_paths=(section_1,),
    )
    unexplained = WriteReceipt(
        op_id="op-unexplained",
        helper="fi.apply.resolved_op_write",
        action="replace",
        bound_target_path=section_1,
        landed_primary_path=section_2,
        replaced_paths=(section_2,),
    )
    materialized_ir = IRNode(kind=IRNodeKind.BODY, children=())

    stage = aggregate_structural_stage(
        materialized_ir=materialized_ir,
        write_receipts=(explained, unexplained),
    )
    assert stage.value is materialized_ir
    assert stage.coverage.unit == "paths"
    # Two landed writes each declare one footprint path → owned == 2.
    assert stage.coverage.owned == 2
    assert stage.coverage.violation == 1
    assert stage.coverage.is_partition()
    assert not stage.coverage.is_clean
    assert len(stage.residuals) == 1
    residual = stage.residuals[0]
    assert residual.kind == "unowned_violation"
    assert residual.blocking is True
    assert stage.has_blocking_residual

    # Empty receipts → trivially clean, empty account.
    empty = aggregate_structural_stage(materialized_ir=materialized_ir, write_receipts=())
    assert empty.coverage.owned == 0
    assert empty.coverage.violation == 0
    assert empty.residuals == ()
    assert empty.coverage.is_clean

    # A bound=None op-level receipt (no resolver binding) has no bound→landed
    # divergence to account: its footprint is owned, it emits NO residual (the
    # #3/#7 boundary contract — the green FI corpus is entirely such receipts).
    bound_none = WriteReceipt(
        op_id="op-bound-none",
        helper="fi.apply.resolved_op_write",
        action="replace",
        bound_target_path=None,
        landed_primary_path=section_1,
        replaced_paths=(section_1,),
    )
    none_stage = aggregate_structural_stage(
        materialized_ir=materialized_ir, write_receipts=(bound_none,)
    )
    assert none_stage.coverage.owned == 1
    assert none_stage.coverage.violation == 0
    assert none_stage.residuals == ()
    assert none_stage.coverage.is_clean


def test_replay_xml_2008_1005_preserves_explicit_item_insertions_during_snapshot_prune() -> None:
    replay = replay_xml_for_test(
        "2008/1005",
        mode="legal_pit",
        quiet=True,
        as_of="2022-04-11",
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_37_text = " ".join(irnode_to_text(sections["chapter:7/section:37"]).split())

    assert (
        "14) markkinavalvonta-asetuksen 4 artiklan 3 kohdan a alakohdassa"
        in section_37_text
    )
    assert (
        "15) markkinavalvonta-asetuksen 4 artiklan 3 kohdan b alakohdassa"
        in section_37_text
    )
    assert (
        "16) markkinavalvonta-asetuksen 4 artiklan 3 kohdan c alakohdassa"
        in section_37_text
    )
    assert (
        "17) markkinavalvonta-asetuksen 4 artiklan 3 kohdan d alakohdassa"
        in section_37_text
    )


def test_replay_xml_1973_935_folds_single_insert_list_tail_before_later_insert() -> None:
    replay = replay_xml_for_test(
        "1973/935",
        mode="legal_pit",
        quiet=True,
        as_of="2004-12-21",
    )
    section_16 = extract_ir_sections(replay.materialized_state.ir)["section:16"]
    subsections = [child for child in section_16.children if child.kind is IRNodeKind.SUBSECTION]
    section_text = " ".join(irnode_to_text(section_16).split())

    assert len(subsections) == 3
    assert (
        "työkyvyttömyysajalta maksamastaan palkasta, mikäli selvityksen esittäminen"
        in section_text
    )
    assert section_text.index("mikäli selvityksen esittäminen") < section_text.index(
        "Valtiokonttorilla on salassapitosäännösten"
    )
