from __future__ import annotations

from lawvm.uk_legislation.source_adjudication import (
    classify_uk_effect_compare_shape,
    classify_uk_effect_source_pathology,
    classify_uk_replay_residual,
    classify_uk_bench_comparison,
    is_core_uk_effect_compare_candidate,
    is_core_uk_effect_source_candidate,
    is_core_uk_comparison,
    normalize_uk_replay_compare_eids,
)


def test_classify_uk_no_oracle_as_non_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=72,
        n_oracle_eids=0,
        n_effects=11,
        raw_score=0.0,
    )

    assert comparison == "no_oracle_eids"
    assert is_core_uk_comparison(comparison) is False


def test_classify_uk_oracle_collapsed_structure_as_non_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=167,
        n_oracle_eids=1,
        n_effects=0,
        raw_score=0.006,
    )

    assert comparison == "oracle_collapsed_structure"
    assert is_core_uk_comparison(comparison) is False


def test_classify_uk_unapplied_expansion_as_core() -> None:
    comparison = classify_uk_bench_comparison(
        n_enacted_eids=46,
        n_oracle_eids=476,
        n_effects=12,
        raw_score=0.097,
    )

    assert comparison == "unapplied_oracle_expansion"
    assert is_core_uk_comparison(comparison) is True


def test_classify_uk_replay_residual_requires_replay_adjudication_for_proved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=[],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_text_match_missing_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_text_match_missing"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_text_match_missing_mixed_residual_eids"


def test_classify_uk_replay_residual_demotes_empty_schedule_shape_gap_to_unresolved() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_empty_schedule_shape_gap"],
    )

    assert tier == "UNRESOLVED"
    assert kind == "uk_empty_schedule_shape_gap"


def test_classify_uk_replay_residual_promotes_target_not_found_to_specific_proof() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=["uk_replay_target_not_found"],
    )

    assert tier == "PROVED_REPLAY_BUG"
    assert kind == "uk_replay_target_not_found"


def test_classify_uk_replay_residual_prefers_payload_over_text_family() -> None:
    tier, kind = classify_uk_replay_residual(
        only_in_replayed=["section-3"],
        only_in_oracle=["section-2"],
        adjudication_kinds=[
            "uk_replay_text_match_missing",
            "uk_replay_payload_mismatch",
        ],
    )

    assert tier == "PROVED_REPLAY_BUG"
    assert kind == "uk_replay_payload_mismatch"


def test_classify_uk_effect_missing_extracted_source() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag=None,
        extracted_text="",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
    )

    assert pathology == "missing_extracted_source"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_unhandled_instruction_text_without_ops() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text='In subsection (1A), at the end insert "(subject to section 33A)".',
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
    )

    assert pathology == "unhandled_instruction_text"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_reference_only_source_fragment() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P4",
        extracted_text="i section 206 (strategies),",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:206"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "reference_only_source_fragment"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_reference_only_short_title_fragment() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text="v Enterprise Act 2002",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:9/paragraph:5/subparagraph:3"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "reference_only_source_fragment"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_fragment_context_missing() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text="“ elderly person ” means a person who has attained the age of 60 years,",
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:146"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "fragment_context_missing"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_instruction_text_reused_as_payload() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="b after that subsection insert the subsections set out in subsection (2).",
        op_actions=["insert", "insert"],
        payload_kinds=["subsection", "subsection"],
        payload_texts=[
            "b after that subsection insert the subsections set out in subsection (2).",
            "b after that subsection insert the subsections set out in subsection (2).",
        ],
    )

    assert pathology == "instruction_text_reused_as_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_broad_source_reused_as_payload() -> None:
    broad_schedule = (
        "SCHEDULE 2 REPEALS AND REVOCATIONS Article 3(2) Reference Short title "
        "or title Extent of repeal or revocation 1863 c. 112 . The Telegraph Act "
        "1863. Section 45. 1868 c. 110 . The Telegraph Act 1868. Section 20."
    )
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="Schedule",
        extracted_text=broad_schedule,
        op_actions=["repeal"],
        payload_kinds=["schedule"],
        payload_texts=[""],
    )

    assert pathology == "broad_source_reused_as_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_misselected_target_context() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P2",
        extracted_text='4 In subsection (3) for "and on the authority" substitute "..., the authority..."',
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["section:21f"],
        effect_type="inserted",
        is_structural=True,
    )

    assert pathology == "misselected_target_context"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_nonstructural_root_gap() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "1 An assistant to the executive of a local authority is entitled to attend, "
            "and speak at, any meeting of the executive or of a committee of the executive. 2"
        ),
        op_actions=[],
        payload_kinds=[],
        payload_texts=[],
        target_paths=[],
        effect_type="words in Sch. 1 para. 5 renumbered as Sch. 1 para. 5(2)",
        is_structural=False,
    )

    assert pathology == "nonstructural_root_gap"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_non_substantive_shell_payload() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P1",
        extracted_text="2 . . . . . . . . . . . . . . . . . . . . .",
        op_actions=["insert"],
        payload_kinds=["schedule"],
        payload_texts=["2 . . . . . . . . . . . . . . . . . . . . ."],
        target_paths=["schedule:a1"],
        effect_type="inserted",
        is_structural=True,
    )

    assert pathology == "non_substantive_shell_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_non_substantive_shell_payload_with_leading_label() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="P3",
        extracted_text="b . . . . . . . . . . . . . . . . . . . . .",
        op_actions=["text_replace"],
        payload_kinds=[],
        payload_texts=[],
        target_paths=["schedule:10/paragraph:3/subparagraph:1"],
        effect_type="words substituted",
        is_structural=True,
    )

    assert pathology == "non_substantive_shell_payload"
    assert is_core_uk_effect_source_candidate(pathology) is False


def test_classify_uk_effect_collapsed_subtree_oracle_shape() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words inserted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-51-6"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=True,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_legacy_labeled_oracle_shape() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="substituted for s. 72(4)(c)-(e) and word",
        op_actions=["replace", "replace"],
        payload_texts=[],
        resolver_eids=["section-72-4-ba", "section-72-4-bb"],
        base_target_hits=[False, False],
        oracle_target_hits=[False, False],
        base_descendant_hits=[False, False],
        oracle_descendant_hits=[False, False],
        base_parent_hits=[True, True],
        oracle_parent_hits=[True, True],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "legacy_labeled_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_retained_repeal_oracle_branch() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="repealed",
        op_actions=["repeal", "repeal"],
        payload_texts=[],
        resolver_eids=["section-3", "section-4"],
        base_target_hits=[True, True],
        oracle_target_hits=[True, True],
        base_descendant_hits=[True, True],
        oracle_descendant_hits=[True, True],
        base_parent_hits=[False, False],
        oracle_parent_hits=[False, False],
        base_target_texts=[],
        oracle_target_texts=[],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "retained_repeal_oracle_branch"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_inserted_child_collapsed_into_oracle_parent() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["aa remuneration allowed to an employee by his employer"],
        resolver_eids=["schedule-7-paragraph-4-1-aa"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["1 none of the following shall be regarded as a donation"],
        oracle_parent_texts=[
            "1 none of the following shall be regarded as a donation aa remuneration allowed to an employee by his employer"
        ],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_replace_with_oracle_only_descendants() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-28-1"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[True],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=True,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_inserted_alphanumeric_child_collapsed_into_oracle_parent() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["This subsection applies to any donation received from a trustee"],
        resolver_eids=["section-162-3a"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["162 1 for the purposes of this act ... 3 this subsection applies ... 6 in this section ..."],
        oracle_parent_texts=["1621for the purposes of this act ... 3this subsection applies ... 3athis subsection applies to any donation received from a trustee ... 6in this section ..."],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "collapsed_subtree_oracle_shape"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_gibraltar_insert_missing_from_main_oracle() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        affecting_title=(
            "The European Parliamentary Elections (Combined Region and Campaign "
            "Expenditure) (United Kingdom and Gibraltar) Order 2004"
        ),
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["3 Paragraphs 3 and 5 to 11 do not apply in relation to a recognised Gibraltar third party."],
        resolver_eids=["schedule-10-paragraph-1-3"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_parent_texts=["1 In this Schedule... 2 For the purposes of this Schedule..."],
        oracle_parent_texts=["1 In this Schedule... 2 For the purposes of this Schedule..."],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "territorial_extension_oracle_gap"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_gibraltar_text_change_missing_from_main_oracle() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        affecting_title=(
            "The European Parliamentary Elections (Combined Region and Campaign "
            "Expenditure) (United Kingdom and Gibraltar) Order 2004"
        ),
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["section-162-4"],
        base_target_hits=[True],
        oracle_target_hits=[True],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[True],
        base_target_texts=[
            "4 for the purposes of subsection 3 the relevant information means ..."
        ],
        oracle_target_texts=[
            "4 for the purposes of subsection 3 the relevant information means ..."
        ],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=True,
        oracle_has_children=False,
    )

    assert compare_shape == "territorial_extension_oracle_gap"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_text_change_against_missing_oracle_branch() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="words substituted",
        op_actions=["text_replace"],
        payload_texts=[],
        resolver_eids=["schedule-5-paragraph-3-2-a"],
        base_target_hits=[True],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[False],
        base_parent_hits=[True],
        oracle_parent_hits=[False],
        base_target_texts=["(a) the Commission must prepare a report."],
        oracle_target_texts=[],
        base_parent_texts=["(2) In this paragraph..."],
        oracle_parent_texts=[],
        base_has_text=True,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "oracle_missing_live_branch"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_classify_uk_effect_inserted_wrapper_with_oracle_only_descendants() -> None:
    compare_shape = classify_uk_effect_compare_shape(
        effect_type="inserted",
        op_actions=["insert"],
        payload_texts=["Attribution of expenditure to different parliamentary constituencies"],
        resolver_eids=["schedule-10-paragraph-2a"],
        base_target_hits=[False],
        oracle_target_hits=[False],
        base_descendant_hits=[False],
        oracle_descendant_hits=[True],
        base_parent_hits=[False],
        oracle_parent_hits=[False],
        base_parent_texts=[],
        oracle_parent_texts=[],
        base_has_text=False,
        base_has_children=False,
        oracle_has_text=False,
        oracle_has_children=False,
    )

    assert compare_shape == "descendant_only_oracle_wrapper"
    assert is_core_uk_effect_compare_candidate(compare_shape) is False


def test_substantive_block_payload_is_not_misselected_target_context() -> None:
    pathology = classify_uk_effect_source_pathology(
        extracted_tag="BlockAmendment",
        extracted_text=(
            "5C Sections 2(2A) and 21(1A) of, and paragraph 5C(1) of Schedule 2 to, "
            "the Local Government Act 1972 are not to be taken to indicate any contrary intention."
        ),
        op_actions=["replace"],
        payload_kinds=["subsection"],
        payload_texts=["Sections 2(2A) and 21(1A) ..."],
        target_paths=["section:39/subsection:5c"],
        effect_type="substituted for s. 39(5)",
        is_structural=False,
    )

    assert pathology == ""


def test_normalize_uk_replay_compare_eids_handles_case_only_alphanumeric_drift() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-10-paragraph-2a-2"},
        {"schedule-10-paragraph-2A-2"},
    )

    assert replayed == {"schedule-10-paragraph-2a-2"}
    assert oracle == {"schedule-10-paragraph-2a-2"}


def test_normalize_uk_replay_compare_eids_drops_wrapper_with_oracle_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"schedule-10-paragraph-2a", "schedule-10-paragraph-2a-2"},
        {
            "schedule-10-paragraph-2A-1",
            "schedule-10-paragraph-2A-2",
            "schedule-10-paragraph-2A-3",
            "schedule-10-paragraph-2A-4",
        },
    )

    assert "schedule-10-paragraph-2a" not in replayed
    assert "schedule-10-paragraph-2a-2" in replayed
    assert "schedule-10-paragraph-2a-2" in oracle


def test_normalize_uk_replay_compare_eids_drops_collapsed_section_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {"section-142", "section-142-1", "section-142-2", "section-142-3"},
        {"section-142"},
    )

    assert replayed == {"section-142"}
    assert oracle == {"section-142"}


def test_normalize_uk_replay_compare_eids_drops_collapsed_crossheading_descendants() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "crossheading-transport",
            "crossheading-transport-10",
            "crossheading-transport-10-1",
            "crossheading-transport-11",
        },
        {"crossheading-transport"},
    )

    assert replayed == {"crossheading-transport"}
    assert oracle == {"crossheading-transport"}


def test_normalize_uk_replay_compare_eids_drops_part_and_crossheading_wrappers() -> None:
    replayed, oracle = normalize_uk_replay_compare_eids(
        {
            "schedule-13-part-I-paragraph-1",
            "schedule-13-part-I-crossheading-exclusions_paragraph-2",
            "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10-paragraph-5",
            "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10-paragraph-5-1",
        },
        {
            "schedule-13-part-I",
            "schedule-13-part-I-crossheading-exclusions",
            "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10",
        },
    )

    assert replayed == set()
    assert oracle == {
        "schedule-13-part-i",
        "schedule-13-part-i-crossheading-exclusions",
        "schedule-21-crossheading-european-parliamentary-elections-act-1978-c10",
    }
