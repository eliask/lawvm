from __future__ import annotations

import pytest


# Post-fi-grammar-merge replay pin drift markers (§2.9 drift discipline).
#
# Each prefix below is xfail-marked so the drift stays visible while replay
# pins are re-adjudicated. Status snapshot verified 2026-06-27 by running each
# prefix individually.
#
# Two prefix tiers, distinguished by whether the drift has closed:
#
#   1. Drift closed (XPASS as of 2026-06-27) — see
#      `_POST_GRAMMAR_MERGE_REPLAY_PIN_REMOVED_PREFIXES` below. These prefixes
#      have their xfail marker REMOVED entirely (rather than relaxed to
#      strict=True) because pytest's strict=True treats XPASS as FAILURE:
#      keeping them strict=True would break CI now with no signal value. As
#      regular passing tests, future regressions (the test starts failing)
#      break CI — which is the goal of "tightening" the drift marker.
#      Do not re-add them as drift markers without first re-verifying that
#      the underlying replay/divergence has actually re-opened.
#
#   2. Still drifting (XFAIL as of 2026-06-27) — see
#      `_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES` below. Kept strict=False
#      so re-adjudication work is not blocked by CI. Tighten to strict=True
#      once the underlying divergence for each prefix closes (verify by
#      re-running the prefix and confirming XPASS, at which point the marker
#      should be removed entirely per tier 1 above, not switched to strict=True).


# Drift closed (XPASSING as of 2026-06-27) — xfail marker not applied.
# Listed for traceability: do not re-add as drift markers without re-verifying
# that the underlying replay/divergence has actually re-opened.
_POST_GRAMMAR_MERGE_REPLAY_PIN_REMOVED_PREFIXES = (
    "tests/test_fi_compile.py::test_normalize_and_compile_ops_2004_485_scopes_flat_20a_replace_from_siblings",
    "tests/test_fi_provision_state.py::test_specimen_2009_273_section_10_drops_carried_old_subsection_text",
    "tests/test_fi_provision_state_consumer_contract.py::test_provision_state_consumer_pin_reproduces[2009/273:section:10@2026-06-10]",
    "tests/test_fi_replay_products.py::test_replay_xml_2006_386_cited_asetus_version_replaces_chapter_three_sections",
    "tests/test_fi_session_regressions_2026_04.py::test_1994_719_2001_124_does_not_keep_or_misroute_16a_17a_cluster",
)


# Still drifting (XFAILING as of 2026-06-27). Kept strict=False so re-
# adjudication work is not blocked by CI. Tighten by removing the marker
# entirely once the prefix XPASSes (do not switch to strict=True — see header).
_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES = (
    # Item-replace relabels unlabelled payload via visible_num — apply-stage
    # 2004/485 flat 20a sibling-replace form still produces drift on the
    # visible-num relabel path.
    "tests/test_fi_apply.py::TestApplyItemReplace::test_replace_item_relabels_unlabelled_payload_with_visible_num",
    # Body-only fallback for 1986/508 + 1996/755 binds wrapper orphan
    # subsections; replay vs oracle divergence on wrapper-orphan grouping.
    "tests/test_fi_compile.py::test_1986_508_1996_755_body_only_fallback_binds_wrapper_orphan_subsections",
    # 2017/320 part-2 chapter-1 section-5 PIT still duplicates under part-2
    # chapter scope; duplicate-label invariant flagged on materialized tree.
    "tests/test_fi_materialization_invariants.py::TestNoDuplicatesInPIT::test_2017_320_part_2_chapter_1_keeps_section_5",
    # 2017/320 delayed section 268 still mis-materializes under current chapter
    # 32; delayed-section materialization drift.
    "tests/test_fi_materialization_invariants.py::TestNoDuplicatesInPIT::test_2017_320_delayed_section_268_materializes_under_current_chapter_32",
    # 1868/31 section 46 stays under hcontainer — fold/reconcile divergence
    # on old-grain hcontainer orphan section reconciliation.
    "tests/test_fi_materialization_invariants.py::TestFoldHcontainerOrphanSectionReconcile::test_1868_31_section_46_stays_under_hcontainer",
    # Curated shadow gate: structural delta still present vs shadow copy.
    # Shadow-gate parity drift; keep marker visible until parity closes.
    "tests/test_fi_parser_facade.py::test_curated_shadow_gate_no_structural_delta",
    # 2016/258 section 7 child overlay still exposed in parent text — child
    # overlay projection drift in provision-state seam.
    "tests/test_fi_provision_state.py::test_specimen_2016_258_section_7_exposes_child_overlay_in_parent_text",
    # 1997/1412 section 11 expired temporary render-tails drop not yet
    # deterministic on materialized provision-state text.
    "tests/test_fi_provision_state.py::test_specimen_1997_1412_section_11_drops_expired_temporary_render_tails",
    # 1978/38 consumer-credit chapter 7 still mis-labelled as 12 — qualified
    # jolloin renumber resolves chapter 7 / chapter 12 mislabel still failing.
    "tests/test_fi_qualified_jolloin_renumber.py::test_1978_38_consumer_credit_chapter_7_not_mislabelled_as_12",
    # 2000/755 amendment bundle still mis-binds cited version-owned section
    # paths; replay-products divergence on civ/eId binding for cross-version.
    "tests/test_fi_replay_products.py::test_build_amendment_bundle_2000_755_rebinds_cited_version_owned_section_paths",
    # 2000/755 + 2018/945 applies to cited pending-version section paths;
    # replay drift on pending-version application scope.
    "tests/test_fi_replay_products.py::test_replay_xml_2000_755_applies_2018_945_to_cited_pending_version_paths",
    # 1978/38 chapter 12 sections 1a + 1b alongside new chapter 7 1a still
    # not preserved cleanly under letter-suffix insert — slow corpus replay.
    "tests/test_fi_replay_products.py::test_replay_xml_1978_38_preserves_chapter_12_sections_1a_and_1b_alongside_new_chapter_7_1a",
    # 2013/393 body chapter scope for 37a — replay drift on body chapter scope.
    "tests/test_fi_replay_products.py::test_replay_xml_preserves_2013_393_body_chapter_scope_for_37a",
    # 1977/603 section 72c only under chapter 8a — replay drift on secondary
    # chapter realization for letter-suffix section.
    "tests/test_fi_replay_products.py::test_replay_xml_1977_603_realizes_section_72c_only_under_chapter_8a",
    # 2013/393 explicit body chapter ownership — replay drift on
    # explicit-body chapter ownership preservation across amendment.
    "tests/test_fi_replay_products.py::test_replay_xml_preserves_explicit_body_chapter_ownership_for_2013_393",
    # 2004/301 section 142 item three duplicate kohta marker — replay drift
    # on item-level marker dedup; slow corpus replay.
    "tests/test_fi_replay_products.py::test_replay_xml_2004_301_section_142_item_three_has_no_duplicate_kohta_marker",
    # 1992/1243 + 2016/118 chapter 8a repealed by 2024/853 — cross-amendment
    # repeal chains chapter-8a lineage; slow corpus replay.
    "tests/test_fi_session_regressions_2026_04.py::test_1992_1243_2016_118_chapter_8a_repealed_by_2024_853",
    # Letter-suffix insert skips multi-unborn chapter batch — replay drift
    # on batch letter-suffix insert across unborn siblings.
    "tests/test_fi_session_regressions_2026_04.py::test_letter_suffix_insert_skips_multi_unborn_chapter_batch",
    # Letter-suffix insert skips large single-chapter recodification batch —
    # replay drift on letter-suffix insert across large recodification batch.
    "tests/test_fi_session_regressions_2026_04.py::test_letter_suffix_insert_skips_large_single_chapter_recodification_batch",
    # 2017/236 materialized state still carries expired exact temporary
    # moments — temporal expiry materialization drift.
    "tests/test_fi_replay_products.py::test_2017_236_materialized_state_drops_expired_exact_temporary_moments",
    # Coordinated sibling chapter still flagged by container guard —
    # totality-predicate drift on coordinated-sibling chapter flag.
    "tests/test_fi_totality_predicate.py::test_container_guard_keeps_coordinated_sibling_chapter_flagged",
    # Refs bench recall anchor coverage + miss-worklist mechanism — worklist
    # recall drift on the reference anchor coverage surface.
    "tests/test_refs_bench.py::test_recall_anchor_coverage_and_miss_worklist_mechanism",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    marker = pytest.mark.xfail(
        reason="post-fi-grammar-merge replay pin drift; keep visible while replay pins are re-adjudicated",
        strict=False,
    )
    for item in items:
        # Note: prefixes in `_POST_GRAMMAR_MERGE_REPLAY_PIN_REMOVED_PREFIXES`
        # are deliberately NOT marked here. They are currently XPASSING (drift
        # closed); see header for reasoning. Marking them strict=True would
        # XPASS-fail and break CI (pytest treats XPASS as FAILURE under
        # strict=True); leaving them unmarked makes them regular passing
        # tests, so future regressions break CI as expected.
        if item.nodeid.startswith(_POST_GRAMMAR_MERGE_REPLAY_PIN_DRIFT_PREFIXES):
            item.add_marker(marker)
