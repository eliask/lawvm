"""UK replay adjudication emission tests."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, cast

from lawvm.core.adjudication_evidence import adjudication_finding_evidence_rows
from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, OperationSource, TextPatchKindEnum, TextPatchSpec, TextSelector, StructuralAction
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.tree_ops import TreeInvariantViolation

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.definition_anchors import _uk_definition_term_lexical_variants
from lawvm.uk_legislation.effect_payload_normalization import prepare_uk_operation_payload_node
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_ir_node_kind
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.ordinals import _uk_ordinal_to_int
from lawvm.uk_legislation.replay_text_apply import (
    _collect_descendant_paths_by_label_and_kinds,
    _delete_source_carried_child_text,
    _definition_child_insert_payload,
    _find_descendant_path_by_kind_label,
    _find_text_range_start_index,
    _insert_at_end_of_definition_text,
    _insert_after_definition_text,
    _remove_trailing_context_word,
    _rewrite_after_anchor_to_end_text,
    _rewrite_anchor_in_definition_entry_text,
    _rewrite_definition_entry_text,
    _rewrite_definition_range_text,
    _rewrite_definition_range_to_end_text,
    _rewrite_each_anchor_in_definition_entry_text,
    _rewrite_flat_definition_child_inner_text,
    _rewrite_flat_definition_child_ordinal_text,
)
from lawvm.uk_legislation.replay_target_gaps import uk_item_order_shape_gap
from lawvm.uk_legislation.source_adjudication import classify_uk_replay_adjudication_bucket
from lawvm.uk_legislation.uk_amendment_replay import (
    UKReplayExecutor,
    UKReplayPipeline,
    UKEffectRecord,
    compile_effect_to_ir_ops,
    _prepare_replay_uk_ops,
    replay_uk_ops,
)


def _base_statute() -> IRStatute:
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Section one."),),
        ),
        supplements=(),
    )


def _source() -> OperationSource:
    return OperationSource(
        statute_id="ukpga/2026/1",
        title="Amending Act",
    )


def _duplicate_text_statute() -> IRStatute:
    shared_text = " ".join(["same", "text"] * 45)
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text=shared_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text=shared_text),),
        ),
        supplements=(),
    )


def test_replay_uk_ops_can_emit_core_mutation_event_for_node_replace() -> None:
    mutation_events: list[MutationEvent] = []
    op = LegalOperation(
        op_id="uk-test-replace-section-1",
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Replacement text."),
        source=_source(),
        sequence=1,
    )

    replayed = replay_uk_ops(_base_statute(), [op], mutation_events_out=mutation_events)

    assert replayed.body.children[0].text == "Replacement text."
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.op_id == "uk-test-replace-section-1"
    assert event.source_statute == "ukpga/2026/1"
    assert event.action == "replace"
    assert event.helper == "_replace_node_in_statute"
    assert event.outcome == "replaced_node"
    assert event.resolved_target_path == (("section", "1"),)
    assert event.parent_path == ()
    assert event.replaced_paths == ((("section", "1"),),)
    assert event.created_paths == ()
    assert event.removed_paths == ()


def test_replay_uk_ops_can_emit_core_mutation_event_for_node_repeal() -> None:
    mutation_events: list[MutationEvent] = []
    op = LegalOperation(
        op_id="uk-test-repeal-section-1",
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "1"),)),
        source=_source(),
        sequence=1,
    )

    replayed = replay_uk_ops(_base_statute(), [op], mutation_events_out=mutation_events)

    assert replayed.body.children == ()
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.op_id == "uk-test-repeal-section-1"
    assert event.source_statute == "ukpga/2026/1"
    assert event.action == "repeal"
    assert event.helper == "_remove_node"
    assert event.outcome == "removed_node"
    assert event.resolved_target_path == (("section", "1"),)
    assert event.parent_path == ()
    assert event.removed_paths == ((("section", "1"),),)
    assert event.created_paths == ()
    assert event.replaced_paths == ()


def test_replay_uk_ops_can_emit_core_mutation_event_for_node_insert() -> None:
    mutation_events: list[MutationEvent] = []
    op = LegalOperation(
        op_id="uk-test-insert-section-2",
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Inserted text."),
        source=_source(),
        sequence=1,
    )

    replayed = replay_uk_ops(_base_statute(), [op], mutation_events_out=mutation_events)

    assert [child.label for child in replayed.body.children] == ["1", "2"]
    assert len(mutation_events) == 1
    event = mutation_events[0]
    assert event.op_id == "uk-test-insert-section-2"
    assert event.source_statute == "ukpga/2026/1"
    assert event.action == "insert"
    assert event.helper == "_record_child_inserted"
    assert event.outcome == "inserted_node"
    assert event.resolved_target_path == (("section", "2"),)
    assert event.parent_path == ()
    assert event.created_paths == ((("section", "2"),),)
    assert event.removed_paths == ()
    assert event.replaced_paths == ()


def test_definition_anchor_lexical_variants_are_narrow_and_deduplicated() -> None:
    assert _uk_definition_term_lexical_variants("") == ()
    assert _uk_definition_term_lexical_variants("education") == ("educational",)
    assert _uk_definition_term_lexical_variants("educational") == ("education",)
    assert _uk_definition_term_lexical_variants("education educational") == (
        "educational educational",
        "education education",
    )
    assert _uk_definition_term_lexical_variants("educational educational") == (
        "education educational",
        "educational education",
    )
    assert _uk_definition_term_lexical_variants("health") == ()


def test_uk_ordinal_to_int_accepts_words_adverbs_and_numeric_suffixes() -> None:
    assert _uk_ordinal_to_int("") is None
    assert _uk_ordinal_to_int("second") == 2
    assert _uk_ordinal_to_int("secondly") == 2
    assert _uk_ordinal_to_int("  3rd. ") == 3
    assert _uk_ordinal_to_int("12th") == 12
    assert _uk_ordinal_to_int("eleventh") is None


def test_rewrite_definition_entry_text_records_predicate_recovery() -> None:
    rewritten, applied, recovery_rule_ids = _rewrite_definition_entry_text(
        '"regulated activity" shall be construed as activity within section 1;',
        term="regulated activity",
        replacement="",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == ""
    assert recovery_rule_ids == ("uk_replay_definition_predicate_shall_construed_normalized",)


def test_rewrite_definition_entry_text_rejects_missing_entry() -> None:
    original = '"charge" means one;'

    rewritten, applied, recovery_rule_ids = _rewrite_definition_entry_text(
        original,
        term="fee",
        replacement='"fee" means three;',
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is False
    assert rewritten == original
    assert recovery_rule_ids == ()


def test_remove_trailing_context_word_preserves_trailing_punctuation() -> None:
    assert _remove_trailing_context_word("paragraph (a), or;", "or") == (
        "paragraph (a);",
        True,
    )
    assert _remove_trailing_context_word("paragraph (a), or", "or") == (
        "paragraph (a)",
        True,
    )
    assert _remove_trailing_context_word("paragraph (a), then", "or") == (
        "paragraph (a), then",
        False,
    )


def test_delete_source_carried_child_text_uses_exact_witness_first() -> None:
    assert _delete_source_carried_child_text(
        "paragraph (a), or",
        original=", or",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    ) == ("paragraph (a)", True)


def test_delete_source_carried_child_text_uses_patch_pattern_recovery() -> None:
    assert _delete_source_carried_child_text(
        "the Welsh  Ministers",
        original="Welsh Ministers",
        allow_punctuation_spacing=True,
        allow_word_punctuation_elision=True,
    ) == ("the ", True)


def test_delete_source_carried_child_text_rejects_missing_witness() -> None:
    original = "paragraph (a), and"

    assert _delete_source_carried_child_text(
        original,
        original=", or",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    ) == (original, False)


def test_insert_at_end_of_definition_text_inserts_before_next_definition() -> None:
    rewritten, applied = _insert_at_end_of_definition_text(
        '"primary legislation" means an Act; "secondary legislation" means regulations;',
        term="primary legislation",
        replacement="or Measure",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"primary legislation" means an Act or Measure; '
        '"secondary legislation" means regulations;'
    )


def test_insert_at_end_of_definition_text_inserts_before_terminal_punctuation() -> None:
    rewritten, applied = _insert_at_end_of_definition_text(
        '"primary legislation" means an Act.',
        term="primary legislation",
        replacement=", Measure or Order",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == '"primary legislation" means an Act, Measure or Order.'


def test_insert_at_end_of_definition_text_rejects_ambiguous_definition() -> None:
    original = '"primary legislation" means an Act; "primary legislation" includes a Measure;'

    rewritten, applied = _insert_at_end_of_definition_text(
        original,
        term="primary legislation",
        replacement="or Order",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is False
    assert rewritten == original


def test_rewrite_definition_range_to_end_text_preserves_entry_terminator() -> None:
    rewritten, applied = _rewrite_definition_range_to_end_text(
        (
            '"primary legislation" means an Act, Measure or Order; '
            '"secondary legislation" means regulations;'
        ),
        term="primary legislation",
        start_anchor="Measure",
        replacement="instrument",
        occurrence=0,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"primary legislation" means an Act, instrument; '
        '"secondary legislation" means regulations;'
    )


def test_rewrite_definition_range_to_end_text_uses_source_occurrence() -> None:
    rewritten, applied = _rewrite_definition_range_to_end_text(
        (
            '"joint fire board" means a board constituted by a scheme, '
            'and includes a board under older law;'
        ),
        term="joint fire board",
        start_anchor="board",
        replacement="and rescue board constituted by an amalgamation scheme;",
        occurrence=2,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"joint fire board" means a and rescue board constituted by an amalgamation scheme;'
    )


def test_rewrite_definition_range_text_uses_requested_occurrence_pair() -> None:
    rewritten, applied = _rewrite_definition_range_text(
        '"entity" means firm, body, firm or partnership;',
        term="entity",
        start_anchor="firm",
        end_anchor="partnership",
        replacement="company",
        occurrence=2,
        end_occurrence=1,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == '"entity" means firm, body, company;'


def test_rewrite_each_anchor_in_definition_entry_text_rewrites_all_matches() -> None:
    rewritten, applied = _rewrite_each_anchor_in_definition_entry_text(
        '"entity" means firm, body, firm or partnership;',
        term="entity",
        anchor="firm",
        replacement="company",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == '"entity" means company, body, company or partnership;'


def test_rewrite_anchor_in_definition_entry_text_rejects_ambiguous_anchor() -> None:
    original = '"entity" means firm, body, firm or partnership;'

    rewritten, applied = _rewrite_anchor_in_definition_entry_text(
        original,
        term="entity",
        anchor="firm",
        replacement="company",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is False
    assert rewritten == original


def test_rewrite_anchor_in_definition_entry_text_rewrites_unique_anchor() -> None:
    rewritten, applied = _rewrite_anchor_in_definition_entry_text(
        '"entity" means firm, body or partnership;',
        term="entity",
        anchor="body",
        replacement="company",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == '"entity" means firm, company or partnership;'


def test_rewrite_after_anchor_to_end_text_uses_exact_anchor_occurrence() -> None:
    rewritten, applied = _rewrite_after_anchor_to_end_text(
        "before anchor one, before anchor two, discarded tail",
        anchor="anchor",
        replacement="kept tail",
        occurrence=2,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == "before anchor one, before anchor kept tail"


def test_rewrite_after_anchor_to_end_text_uses_normalized_anchor() -> None:
    rewritten, applied = _rewrite_after_anchor_to_end_text(
        "prefix Welsh  Ministers old tail",
        anchor="Welsh Ministers",
        replacement="new tail",
        occurrence=0,
        allow_punctuation_spacing=True,
        allow_word_punctuation_elision=True,
    )

    assert applied is True
    assert rewritten == "prefix Welsh Ministers new tail"


def test_rewrite_after_anchor_to_end_text_rejects_missing_anchor() -> None:
    original = "prefix Scottish Ministers old tail"

    rewritten, applied = _rewrite_after_anchor_to_end_text(
        original,
        anchor="Welsh Ministers",
        replacement="new tail",
        occurrence=0,
        allow_punctuation_spacing=True,
        allow_word_punctuation_elision=True,
    )

    assert applied is False
    assert rewritten == original


def test_find_text_range_start_index_exact_anchor_has_no_recovery() -> None:
    start_idx, recovery_rule_ids = _find_text_range_start_index(
        "alpha beta alpha gamma",
        "alpha",
        occurrence=0,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert start_idx == 0
    assert recovery_rule_ids == ()


def test_find_text_range_start_index_word_boundary_recovery_is_visible() -> None:
    start_idx, recovery_rule_ids = _find_text_range_start_index(
        "alpha secular secularism",
        "secular",
        occurrence=1,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert start_idx == 6
    assert recovery_rule_ids == ("uk_replay_text_range_anchor_word_boundary_normalized",)


def test_find_text_range_start_index_patch_pattern_fallback() -> None:
    start_idx, recovery_rule_ids = _find_text_range_start_index(
        "alpha Welsh  Ministers gamma",
        "Welsh Ministers",
        occurrence=0,
        allow_punctuation_spacing=True,
        allow_word_punctuation_elision=True,
    )

    assert start_idx == 6
    assert recovery_rule_ids == ()


def test_find_text_range_start_index_rejects_missing_anchor() -> None:
    start_idx, recovery_rule_ids = _find_text_range_start_index(
        "alpha beta gamma",
        "delta",
        occurrence=0,
        allow_punctuation_spacing=True,
        allow_word_punctuation_elision=True,
    )

    assert start_idx == -1
    assert recovery_rule_ids == ()


def test_find_descendant_path_by_kind_label_returns_first_document_order_match() -> None:
    root = UKMutableNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=[
            UKMutableNode(kind=IRNodeKind.PARAGRAPH, label="a"),
            UKMutableNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                children=[UKMutableNode(kind=IRNodeKind.PARAGRAPH, label="a")],
            ),
        ],
    )

    assert _find_descendant_path_by_kind_label(root, kind="paragraph", label="a") == (0,)
    assert _find_descendant_path_by_kind_label(root, kind="paragraph", label="b") is None


def test_collect_descendant_paths_by_label_and_kinds_preserves_document_order() -> None:
    root = UKMutableNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=[
            UKMutableNode(kind=IRNodeKind.ITEM, label="2"),
            UKMutableNode(kind=IRNodeKind.PARAGRAPH, label="2"),
            UKMutableNode(kind=IRNodeKind.SUBPARAGRAPH, label="3"),
        ],
    )

    assert _collect_descendant_paths_by_label_and_kinds(
        root,
        label="2",
        allowed_kinds={"paragraph", "item"},
    ) == [(0,), (1,)]


def test_definition_child_insert_payload_preserves_ordered_list_metadata() -> None:
    anchor_suffix, children = _definition_child_insert_payload(
        "; or c third condition; d fourth condition;",
        term="regulated activity",
    )

    assert anchor_suffix == "; or"
    assert [child.text for child in children] == ["third condition;", "fourth condition;"]
    assert [child.attrs["definition_child_label"] for child in children] == ["c", "d"]
    assert {child.attrs["definition_term"] for child in children} == {"regulated activity"}
    assert {child.attrs["source_rule_id"] for child in children} == {
        "uk_definition_ordered_list_child_preserved"
    }


def test_insert_after_definition_text_reports_anchor_recoveries_in_order() -> None:
    rewritten, applied, recovery_rule_ids = _insert_after_definition_text(
        (
            '"directed" and "intrusive", in relation to surveillance, '
            'shall be construed in accordance with section 1; "ordinary" means usual;'
        ),
        term="intrusive",
        replacement='"joint operation" means an operation involving two forces;',
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"directed" and "intrusive", in relation to surveillance, '
        'shall be construed in accordance with section 1; '
        '"joint operation" means an operation involving two forces; "ordinary" means usual;'
    )
    assert recovery_rule_ids == (
        "uk_replay_definition_anchor_qualifier_phrase_normalized",
        "uk_replay_definition_anchor_conjoined_term_normalized",
        "uk_replay_after_definition_text_insert_applied",
    )


def test_insert_after_definition_text_rejects_unbounded_anchor() -> None:
    original = 'The words directed and intrusive appear in prose; "ordinary" means usual;'

    rewritten, applied, recovery_rule_ids = _insert_after_definition_text(
        original,
        term="intrusive",
        replacement='"joint operation" means an operation involving two forces;',
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is False
    assert rewritten == original
    assert recovery_rule_ids == ()


def test_rewrite_flat_definition_child_ordinal_text_replaces_segment() -> None:
    rewritten, applied = _rewrite_flat_definition_child_ordinal_text(
        (
            '"review partner" means a local authority; '
            'a clinical commissioning group; a Health Authority; a person; '
            '"other" means another value;'
        ),
        term="review partner",
        child_label="c",
        replacement="an integrated care board, or",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"review partner" means a local authority; '
        'a clinical commissioning group; an integrated care board, or; '
        'a person; "other" means another value;'
    )


def test_rewrite_flat_definition_child_ordinal_text_deletes_segment() -> None:
    rewritten, applied = _rewrite_flat_definition_child_ordinal_text(
        (
            '"relevant provision" means section 39(1); section 40(1); '
            'section 41(1); section 42(1); "other provision" means paragraph (d);'
        ),
        term="relevant provision",
        child_label="d",
        replacement="",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"relevant provision" means section 39(1); section 40(1); '
        'section 41(1); "other provision" means paragraph (d);'
    )


def test_rewrite_flat_definition_child_ordinal_text_rejects_missing_child() -> None:
    original = '"review partner" means a local authority; "other" means another value;'

    rewritten, applied = _rewrite_flat_definition_child_ordinal_text(
        original,
        term="review partner",
        child_label="c",
        replacement="an integrated care board, or",
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is False
    assert rewritten == original


def test_rewrite_flat_definition_child_inner_text_appends_at_child_end() -> None:
    rewritten, applied = _rewrite_flat_definition_child_inner_text(
        (
            '"relevant policies" means first policy; second policy; '
            '"other" means another value;'
        ),
        term="relevant policies",
        child_label="b",
        pattern="",
        replacement_text="including local plans",
        child_after_anchor=False,
        child_at_end=True,
        occurrence=0,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"relevant policies" means first policy;second policy; including local plans '
        '"other" means another value;'
    )


def test_rewrite_flat_definition_child_inner_text_replaces_unique_child_witness() -> None:
    rewritten, applied = _rewrite_flat_definition_child_inner_text(
        (
            '"relevant policies" means first policy; second policy applies; '
            '"other" means another value;'
        ),
        term="relevant policies",
        child_label="b",
        pattern="applies",
        replacement_text="is relevant",
        child_after_anchor=False,
        child_at_end=False,
        occurrence=0,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is True
    assert rewritten == (
        '"relevant policies" means first policy; second policy is relevant; '
        '"other" means another value;'
    )


def test_rewrite_flat_definition_child_inner_text_rejects_ambiguous_child_witness() -> None:
    original = (
        '"relevant policies" means first policy; second policy and policy; '
        '"other" means another value;'
    )

    rewritten, applied = _rewrite_flat_definition_child_inner_text(
        original,
        term="relevant policies",
        child_label="b",
        pattern="policy",
        replacement_text="plan",
        child_after_anchor=False,
        child_at_end=False,
        occurrence=0,
        allow_punctuation_spacing=False,
        allow_word_punctuation_elision=False,
    )

    assert applied is False
    assert rewritten == original


def _uk_table_effect() -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-uk-table-inline",
        effect_type="words inserted",
        applied=True,
        requires_applied=False,
        modified="2022-07-15",
        affected_uri="",
        affected_class="WelshParliamentAct",
        affected_year="2021",
        affected_number="1",
        affected_provisions="s. 159(5) Table 2",
        affecting_uri="",
        affecting_class="WelshStatutoryInstrument",
        affecting_year="2022",
        affecting_number="797",
        affecting_provisions="reg. 7(c)(ii)",
        affecting_title="Corporate Joint Committees Regulations",
        in_force_dates=[{"date": "2022-07-15", "prospective": "false"}],
    )


def _table_cell_statute(*, duplicate_table: bool = False) -> IRStatute:
    table = IRNode(
        kind=IRNodeKind.TABLE,
        label=None,
        text="",
        children=(
            IRNode(
                kind=IRNodeKind.ROW,
                label=None,
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.CELL,
                        label=None,
                        text="The Welsh Ministers",
                        attrs={"rowspan": "2"},
                    ),
                    IRNode(
                        kind=IRNodeKind.CELL,
                        label=None,
                        text="Functions under Chapter 1 of Part 6.",
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.ROW,
                label=None,
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.CELL,
                        label=None,
                        text=(
                            "Functions under Chapter 1 of Part 6 "
                            "(performance of principal councils)."
                        ),
                    ),
                ),
            ),
        ),
    )
    tables = (table, table) if duplicate_table else (table,)
    return IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="159",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="5",
                            text="The following table has effect.",
                            children=tables,
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def test_executor_records_replay_target_not_found() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_replace_target_missing",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SUBSECTION, label="a", text="Missing replacement"),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_replace_payload_target_leaf_mismatch_gap"
    assert adjudications[0].detail["target"] == "section:9"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].source_statute == "ukpga/2026/1"


def test_executor_records_body_root_fallback_insert_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_body_root_fallback_insert",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "1"), ("section", "2"))),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Inserted section."),
            source=_source(),
        )
    )

    assert [child.label for child in executor.statute.body.children] == ["1", "2"]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_body_root_fallback_insert_resolved"
    assert classify_uk_replay_adjudication_bucket(adjudications[0].kind) == (
        "nonblocking_observation"
    )
    assert adjudications[0].detail["family"] == "target_resolution_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "apply"
    assert adjudications[0].detail["target"] == "chapter:1/section:2"
    assert adjudications[0].detail["payload_kind"] == "section"
    assert adjudications[0].detail["derived_target_eid"]


def test_executor_blocks_schedule_descendant_body_root_fallback() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = _base_statute()
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_descendant_body_root_fallback_blocked",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("schedule", "5A"), ("part", "1"))),
            payload=IRNode(kind=IRNodeKind.PART, label="Part 1", text="Inserted schedule part."),
            source=_source(),
        )
    )

    assert [child.label for child in executor.statute.body.children] == ["1"]
    assert executor.statute.supplements == []
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_schedule_branch_gap"
    assert classify_uk_replay_adjudication_bucket(adjudications[0].kind) == "source_shape"
    assert adjudications[0].detail["target"] == "schedule:5A/part:1"
    assert adjudications[0].detail["payload_kind"] == "part"
    assert adjudications[0].detail["payload_label"] == "Part 1"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_schedule_single_letter_item_before_alpha_suffix_is_order_shape_gap() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_item_alpha_suffix_order_gap",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(
            path=(
                ("schedule", "3A"),
                ("paragraph", "1"),
                ("subparagraph", "1"),
                ("item", "v"),
            )
        ),
        payload=IRNode(kind=IRNodeKind.ITEM, label="v", text="Inserted item."),
        source=_source(),
    )

    assert uk_item_order_shape_gap(
        op,
        "schedule:SCHEDULE 3A:schedule/part:PART 1/crossheading:?/paragraph:1/"
        "subparagraph:1: item out of order: v > ja",
    )


def test_item_order_shape_gap_accepts_typed_invariant_record() -> None:
    op = LegalOperation(
        op_id="uk_test_schedule_item_typed_order_gap",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(
            path=(
                ("schedule", "3A"),
                ("paragraph", "1"),
                ("subparagraph", "1"),
                ("item", "v"),
            )
        ),
        payload=IRNode(kind=IRNodeKind.ITEM, label="v", text="Inserted item."),
        source=_source(),
    )
    violation = TreeInvariantViolation(
        kind="sort_order",
        path=(("schedule", "SCHEDULE 3A"), ("paragraph", "1"), ("subparagraph", "1")),
        child_kind="item",
        previous_label="v",
        next_label="ja",
    )

    assert uk_item_order_shape_gap(op, violation)


def test_executor_classifies_absent_child_repeal_under_present_parent() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/11",
        title="Absent Child Repeal Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="3A",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="4",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="2",
                                text="The Secretary of State is also a supervisory authority.",
                                children=(IRNode(kind=IRNodeKind.ITEM, label="da", text="Existing item."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_absent_child_repeal_target_gap",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(
                path=(
                    ("schedule", "3A"),
                    ("paragraph", "4"),
                    ("subparagraph", "2"),
                    ("item", "f"),
                )
            ),
            source=_source(),
        )
    )

    parent = executor.statute.supplements[0].children[0].children[0]
    assert [(child.kind, child.label) for child in parent.children] == [(IRNodeKind.ITEM, "da")]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_absent_child_repeal_target_gap"
    assert classify_uk_replay_adjudication_bucket(adjudications[0].kind) == "source_shape"
    assert adjudications[0].detail["target"] == "schedule:3A/paragraph:4/subparagraph:2/item:f"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_executor_resolves_source_parent_range_schedule_paragraph_target_to_unique_item() -> None:
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        attrs={"eId": "schedule-1-paragraph-1"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="d",
                                text="old d",
                                attrs={"eId": "schedule-1-paragraph-1-d"},
                            ),
                            IRNode(kind=IRNodeKind.ITEM, label="e", text="old e"),
                            IRNode(kind=IRNodeKind.ITEM, label="f", text="old f"),
                            IRNode(kind=IRNodeKind.ITEM, label="g", text="old g"),
                        ),
                    ),
                ),
            ),
        ),
    )
    replacement = IRNode(
        kind=IRNodeKind.ITEM,
        label="d",
        text="new d",
        children=(
            IRNode(kind=IRNodeKind.ITEM, label="i", text="new i"),
            IRNode(kind=IRNodeKind.ITEM, label="ii", text="new ii"),
            IRNode(kind=IRNodeKind.ITEM, label="iii", text="new iii"),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    witness_rule = "uk_effect_source_parent_substitution_range_payload_lowered"

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_parent_range_replace",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "d"))),
            payload=replacement,
            source=_source(),
            witness_rule_id=witness_rule,
        )
    )
    for idx, label in enumerate(("e", "f", "g")):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_source_parent_range_repeal_{idx}",
                sequence=2 + idx,
                action=StructuralAction.REPEAL,
                target=LegalAddress(path=(("schedule", "1"), ("paragraph", label))),
                source=_source(),
                witness_rule_id=witness_rule,
            )
        )

    paragraph = executor.statute.supplements[0].children[0]
    assert [child.label for child in paragraph.children] == ["d"]
    assert paragraph.children[0].text == "new d"
    assert [child.label for child in paragraph.children[0].children] == ["i", "ii", "iii"]
    recovery_rows = [
        row
        for row in adjudications
        if row.kind == "uk_replay_schedule_item_target_from_parent_substitution_resolved"
    ]
    assert len(recovery_rows) == 4
    assert {row.detail["action"] for row in recovery_rows} == {"replace", "repeal"}
    assert all(row.detail["strict_disposition"] == "block" for row in recovery_rows)
    assert all(row.detail["quirks_disposition"] == "apply" for row in recovery_rows)


def test_executor_classifies_direct_section_paragraph_missing_carrier_as_source_shape() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    attrs={"eId": "section-48"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="authority text authority text",
                            attrs={"eId": "section-48-1"},
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="authority text authority text",
                            attrs={"eId": "section-48-2"},
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_direct_section_paragraph_missing_carrier",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="authority", occurrence=2),
                replacement="authority (i) ",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_direct_section_paragraph_carrier_gap"
    assert classify_uk_replay_adjudication_bucket(adjudications[0].kind) == "source_shape"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_uk_table_entry_inline_text_insertion_lowers_to_owned_table_cell_selector() -> None:
    lowering_records: list[dict[str, object]] = []
    extracted = ET.fromstring(
        "<P4>ii in the second column, in the second entry relating to the Welsh "
        "Ministers, after \u201c(performance of principal councils)\u201d insert "
        "\u201c, Chapter 1A of Part 6 (performance of corporate joint committees).\u201d</P4>"
    )

    ops = compile_effect_to_ir_ops(
        _uk_table_effect(),
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "159"), ("subsection", "5")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "(performance of principal councils)"
    assert op.text_patch.replacement == (
        "(performance of principal councils), Chapter 1A of Part 6 "
        "(performance of corporate joint committees)."
    )
    assert any(
        str(tag).startswith("table_cell_selector:")
        and '"column_index": 2' in str(tag)
        and '"entry_index": 2' in str(tag)
        for tag in op.provenance_tags
    )
    assert any(
        row.get("rule_id") == "uk_effect_table_entry_inline_text_insertion"
        and row.get("blocking") is False
        and row.get("strict_disposition") == "record"
        for row in lowering_records
    )


def test_uk_preposed_passive_word_substitution_lowers_to_text_replace() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-b514f36ba66275b696cc718d2ec64ac2",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2024-12-19",
        affected_uri="/id/ukpga/1990/16/section/9/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="16",
        affected_provisions="s. 9(5)",
        affecting_uri="/id/uksi/2004/3279",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2004",
        affecting_number="3279",
        affecting_provisions="reg. 11(b)",
        affecting_title="General Food Regulations 2004",
    )
    extracted = ET.fromstring(
        "<P3>b in subsection (5) there shall be substituted for the words "
        "\u201cor 8 above\u201d the words \u201c or regulation 4(a) of the General Food "
        "Regulations 2004 \u201d .</P3>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "9"), ("subsection", "5")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "or 8 above"
    assert op.text_patch.replacement == "or regulation 4(a) of the General Food Regulations 2004"
    assert lowering_records == []


def test_uk_preposed_beginning_word_insert_lowers_to_text_replace() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-1a109cef2fe1892b82c9fa2922aec374",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2024-12-19",
        affected_uri="/id/ukpga/1990/16/section/40/4/a",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="16",
        affected_provisions="s. 40(4)(a)",
        affecting_uri="/id/uksi/2004/2990",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2004",
        affecting_number="2990",
        affecting_provisions="reg. 4(a)",
        affecting_title="General Food Regulations 2004",
    )
    extracted = ET.fromstring(
        "<P3>a in subsection (4)(a) there shall be inserted at the beginning "
        "the words \u201csubject to subsection (4B) below,\u201d;</P3>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "40"), ("subsection", "4"), ("paragraph", "a")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "TEXT_BEGINNING"
    assert op.text_patch.replacement == "subject to subsection (4B) below,"
    assert lowering_records == []


def test_uk_passive_quoted_word_omission_lowers_to_text_repeal() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-8bfffcac10012e3aab654502eb0da22b",
        effect_type="word omitted",
        applied=True,
        requires_applied=True,
        modified="2024-12-19",
        affected_uri="/id/ukpga/1990/16/section/9/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1990",
        affected_number="16",
        affected_provisions="s. 9(1)",
        affecting_uri="/id/uksi/2004/3279",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2004",
        affecting_number="3279",
        affecting_provisions="reg. 11(a)(i)",
        affecting_title="General Food Regulations 2004",
    )
    extracted = ET.fromstring(
        "<P4>i after paragraph (a) the word \u201cor\u201d shall be omitted; and</P4>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPEAL
    assert op.target == LegalAddress(path=(("section", "9"), ("subsection", "1")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "or"
    assert op.text_patch.replacement is None
    assert lowering_records == []


def test_uk_from_beginning_passive_substitution_lowers_to_text_replace() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-b6e69d8fa4c579f7b4ace4cb45cbdeb0",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1991/22/section/10/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1991",
        affected_number="22",
        affected_provisions="s. 10(1)",
        affecting_uri="/id/uksi/2003/1398",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2003",
        affecting_number="1398",
        affecting_provisions="Sch. para. 18(2)(a)",
        affecting_title="Enterprise Act 2002 (Part 8 Domestic Infringements) Order 2003",
    )
    extracted = ET.fromstring(
        "<P3>a in subsection (1) for the words from the beginning of the subsection "
        "to \u201ca person\u201d are substituted the words \u201cFor the purposes of the "
        "Enterprise Act 2002, a person\u201d; and</P3>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "10"), ("subsection", "1")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "TEXT_FROM__TO_a person"
    assert op.text_patch.replacement == "For the purposes of the Enterprise Act 2002, a person"
    assert lowering_records == []


def test_uk_passive_range_to_end_repeal_lowers_to_text_repeal() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-917ac841868ff6a93a8f6605650de783",
        effect_type="words repealed",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1991/22/section/114/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1991",
        affected_number="22",
        affected_provisions="s. 114(1)",
        affecting_uri="/id/asp/2005/12",
        affecting_class="ScottishAct",
        affecting_year="2005",
        affecting_number="12",
        affecting_provisions="s. 19(4)(a)",
        affecting_title="Transport (Scotland) Act 2005",
    )
    extracted = ET.fromstring(
        "<P3>a in subsection (1) the words from \u201cto\u201d, where thirdly occurring, "
        "to the end are repealed; and</P3>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPEAL
    assert op.target == LegalAddress(path=(("section", "114"), ("subsection", "1")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "TEXT_FROM_to_TO_END"
    assert op.text_patch.selector.occurrence == 3
    assert op.text_patch.replacement is None
    assert lowering_records == []


def test_uk_after_parenthesized_anchor_insert_lowers_to_text_replace() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-f1113ffbe8d438095b776f0d04b0fa77",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2026-03-13",
        affected_uri="/id/ukpga/1991/22/section/48/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1991",
        affected_number="22",
        affected_provisions="s. 48(5)",
        affecting_uri="/id/ukpga/2025/34",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="34",
        affecting_provisions="s. 49(5)(b)",
        affecting_title="Planning and Infrastructure Act 2025",
    )
    extracted = ET.fromstring("<P3>b after (3) insert \u201cor (3ZA)\u201d .</P3>")

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "48"), ("subsection", "5")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "(3)"
    assert op.text_patch.replacement == "(3) or (3ZA)"
    assert lowering_records == []


def test_uk_bare_range_unquoted_substitution_lowers_to_text_replace() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-30388dad5dbe57f6b427629a3a50cf41",
        effect_type="words substituted",
        applied=True,
        requires_applied=True,
        modified="2026-03-13",
        affected_uri="/id/ukpga/1991/22/section/48/5",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1991",
        affected_number="22",
        affected_provisions="s. 48(5)",
        affecting_uri="/id/ukpga/2025/34",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="34",
        affecting_provisions="s. 49(5)(a)",
        affecting_title="Planning and Infrastructure Act 2025",
    )
    extracted = ET.fromstring(
        "<P3>a from \u201care to\u201d to \u201clicence\u201d substitute "
        "(including public charge points) are to the person entitled, by virtue of\u2014 "
        "a a statutory right, b a street works licence, or c where the apparatus "
        "is a public charge point installed in England in pursuance of a street "
        "works permit, the permit, ;</P3>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "48"), ("subsection", "5")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "TEXT_FROM_are to_TO_licence"
    assert op.text_patch.replacement == (
        "(including public charge points) are to the person entitled, by virtue of\u2014 "
        "a a statutory right, b a street works licence, or c where the apparatus "
        "is a public charge point installed in England in pursuance of a street "
        "works permit, the permit, ;"
    )
    assert lowering_records == []


def test_uk_insert_text_at_end_lowers_to_append_patch() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-b4df2158228bbb9e4b7b6dae7b5bddeb",
        effect_type="word inserted",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/103/4/a/ii",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 103(4)(a)(ii)",
        affecting_uri="/id/uksi/2011/1484",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2011",
        affecting_number="1484",
        affecting_provisions="Sch. 7 para. 15(a)(i)",
        affecting_title="Pensions Act 2008 (Abolition of Contracting-out for Defined Contribution Pension Schemes) (Consequential Amendments) Regulations 2011",
    )
    extracted = ET.fromstring("<P4>i insert \u201c or \u201d at the end of sub-paragraph (ii);</P4>")

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(
        path=(("section", "103"), ("subsection", "4"), ("paragraph", "a"), ("subparagraph", "ii"))
    )
    assert op.text_patch is not None
    assert op.text_patch.kind is TextPatchKindEnum.APPEND
    assert op.text_patch.selector.match_text == "TEXT_END"
    assert op.text_patch.replacement == "or"
    assert lowering_records == []


def test_uk_nested_quote_definition_after_anchor_insert_lowers_to_text_replace() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-f38bfb45f70c9f787e2a554d2e4bffa2",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/115B/9",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 115B(9)",
        affecting_uri="/id/uksi/2014/1283",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2014",
        affecting_number="1283",
        affecting_provisions="Sch. para. 4(c)(ii)",
        affecting_title=(
            "Social Security (Contributions) (Amendment No. 4) Regulations 2014"
        ),
    )
    extracted = ET.fromstring(
        "<P4>ii in the definition of \u201ccontributions\u201d after "
        "\u201cin respect of contributions\u201d insert \u201c(and accordingly, in the "
        "definition of \u201cthe Class 1 element\u201d given by this subsection, "
        "\u201cClass 1 contributions\u201d includes any interest or penalty in respect "
        "of Class 1 contributions)\u201d.</P4>"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "115b"), ("subsection", "9")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == (
        "TEXT_IN_DEFINITION_contributions\x1fAFTER\x1fin respect of contributions"
    )
    assert op.text_patch.replacement == (
        "in respect of contributions (and accordingly, in the definition of "
        "\u201cthe Class 1 element\u201d given by this subsection, "
        "\u201cClass 1 contributions\u201d includes any interest or penalty in respect "
        "of Class 1 contributions)"
    )
    assert len(lowering_records) == 1
    observation = lowering_records[0]
    assert observation["rule_id"] == "uk_effect_in_definition_after_anchor_insert_text_patch"
    assert observation["reason_code"] == "explicit_definition_scoped_after_anchor_insert_text_patch"
    assert observation["blocking"] is False
    assert observation["strict_disposition"] == "record"


def test_uk_compound_subsection_child_insert_lowers_to_child_selector() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-bf6536b7a0a6398f32dc77868e8b6bc4",
        effect_type="word inserted",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/103/4/a",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 103(4)(a)",
        affecting_uri="/id/uksi/2019/479",
        affecting_class="UnitedKingdomStatutoryInstrument",
        affecting_year="2019",
        affecting_number="479",
        affecting_provisions="reg. 67(a)",
        affecting_title="Social Security (Miscellaneous Amendments) Regulations 2019",
    )
    extracted = ET.fromstring("<P3>a after subsection (4)(a)(i), insert \u201c or \u201d ;</P3>")

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(
        path=(("section", "103"), ("subsection", "4"), ("paragraph", "a"))
    )
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "TEXT_AFTER_CHILD_subparagraph_i"
    assert op.text_patch.replacement == "or"
    assert lowering_records == []


def test_uk_grouped_after_insert_child_row_lowers_from_source_parent_payload() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-ab53f11b55f85b914b9cfbee30e92de3",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/138/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 138(1)",
        affecting_uri="/id/ukpga/2005/6",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2005",
        affecting_number="6",
        affecting_provisions="Sch. 1 para. 51(a)",
        affecting_title="Child Benefit Act 2005",
    )
    source_root = ET.fromstring(
        """
        <Legislation>
          <P1 id="schedule-1-paragraph-51">
            <Pnumber>51</Pnumber>
            <P1para>
              <Text>In section 138(1), after\u2014</Text>
              <P3 id="schedule-1-paragraph-51-a">
                <Pnumber>a</Pnumber>
                <P3para><Text>\u201ca child\u201d, and</Text></P3para>
              </P3>
              <P3 id="schedule-1-paragraph-51-b">
                <Pnumber>b</Pnumber>
                <P3para><Text>\u201cof the child\u201d, in both places,</Text></P3para>
              </P3>
              <Text>insert \u201cor qualifying young person\u201d.</Text>
            </P1para>
          </P1>
        </Legislation>
        """
    )
    extracted = next(
        el for el in source_root.iter() if el.get("id") == "schedule-1-paragraph-51-a"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "138"), ("subsection", "1")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "a child"
    assert op.text_patch.replacement == "a child or qualifying young person"
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_source_parent_grouped_after_anchor_insert_text_patch"
    ]
    assert lowering_records[0]["source_parent_id"] == "schedule-1-paragraph-51"


def test_uk_grouped_after_insert_all_occurrences_child_row_lowers_from_source_parent_payload() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-f026c0b6abe1ba905b5fad1098dc70a1",
        effect_type="words inserted",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/138/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 138(1)",
        affecting_uri="/id/ukpga/2005/6",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2005",
        affecting_number="6",
        affecting_provisions="Sch. 1 para. 51(b)",
        affecting_title="Child Benefit Act 2005",
    )
    source_root = ET.fromstring(
        """
        <Legislation>
          <P1 id="schedule-1-paragraph-51">
            <Pnumber>51</Pnumber>
            <P1para>
              <Text>In section 138(1), after\u2014</Text>
              <P3 id="schedule-1-paragraph-51-a">
                <Pnumber>a</Pnumber>
                <P3para><Text>\u201ca child\u201d, and</Text></P3para>
              </P3>
              <P3 id="schedule-1-paragraph-51-b">
                <Pnumber>b</Pnumber>
                <P3para><Text>\u201cof the child\u201d, in both places,</Text></P3para>
              </P3>
              <Text>insert \u201cor qualifying young person\u201d.</Text>
            </P1para>
          </P1>
        </Legislation>
        """
    )
    extracted = next(
        el for el in source_root.iter() if el.get("id") == "schedule-1-paragraph-51-b"
    )

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target == LegalAddress(path=(("section", "138"), ("subsection", "1")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "of the child"
    assert op.text_patch.selector.occurrence == 0
    assert op.text_patch.replacement == "of the child or qualifying young person"
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_source_parent_grouped_after_anchor_all_occurrences_insert_text_patch",
        "uk_effect_source_parent_grouped_after_anchor_all_occurrences_insert_text_patch",
    ]
    source_context_record = next(
        record for record in lowering_records if record["family"] == "source_context_elaboration"
    )
    assert source_context_record["all_occurrences"] is True
    assert source_context_record["source_parent_id"] == "schedule-1-paragraph-51"


def test_uk_final_bare_quoted_word_repeal_lowers_to_final_text_repeal() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-d3e56242f5a1e2af629389e9f2e56818",
        effect_type="word repealed",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/103/4/aa",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 103(4)(aa)",
        affecting_uri="/id/nisr/2012/413",
        affecting_class="NorthernIrelandStatutoryRule",
        affecting_year="2012",
        affecting_number="413",
        affecting_provisions="Sch. 4 para. 3(a)",
        affecting_title="Pensions Act 2008 (Abolition of Contracting-out for Defined Contribution Pension Schemes) (Consequential Amendments) Order (Northern Ireland) 2012",
    )
    extracted = ET.fromstring("<P3>a the \u201cand\u201d at the end of paragraph (aa) is repealed;</P3>")

    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
        lowering_rejections_out=lowering_records,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPEAL
    assert op.target == LegalAddress(path=(("section", "103"), ("subsection", "4"), ("paragraph", "aa")))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "and"
    assert op.text_patch.selector.occurrence == -1
    assert op.text_patch.replacement is None
    assert lowering_records == []


def test_uk_mixed_repeal_table_lowers_separately_named_structural_target() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-292dcbda92d5cb86343fe00b5daed96c",
        effect_type="repealed",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/142/2",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 142(2)",
        affecting_uri="/id/ukpga/2002/19",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2002",
        affecting_number="19",
        affecting_provisions="Sch. 2",
        affecting_title="Tax Credits Act 2002",
        affected_title="Social Security Administration (Northern Ireland) Act 1992",
    )
    source_root = ET.fromstring(
        """
        <Schedule>
          <Table>
            <TR>
              <TH>Short title and chapter</TH>
              <TH>Extent of repeal</TH>
            </TR>
            <TR>
              <TD>Social Security Administration (Northern Ireland) Act 1992 (c. 8)</TD>
              <TD>In section 142, in subsection (1), the words
              \u201cfrom contributions of any class,\u201d and the words
              \u201cin the case of contributions of that class\u201d and subsection (2).</TD>
            </TR>
          </Table>
        </Schedule>
        """
    )

    ops = compile_effect_to_ir_ops(
        effect,
        source_root,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.REPEAL
    assert op.target == LegalAddress(path=(("section", "142"), ("subsection", "2")))
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_repeal_table_structural_repeal"
    ]
    assert (
        lowering_records[0]["reason_code"]
        == "mixed_structural_and_word_repeal_split_structural_target"
    )
    assert lowering_records[0]["split_from_mixed_extent_row"] is True


def test_uk_mixed_repeal_table_rejects_word_scoped_subsection_as_structural() -> None:
    lowering_records: list[dict[str, object]] = []
    effect = UKEffectRecord(
        effect_id="key-292dcbda92d5cb86343fe00b5daed96c-negative",
        effect_type="repealed",
        applied=True,
        requires_applied=True,
        modified="2025-08-01",
        affected_uri="/id/ukpga/1992/8/section/142/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="1992",
        affected_number="8",
        affected_provisions="s. 142(1)",
        affecting_uri="/id/ukpga/2002/19",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2002",
        affecting_number="19",
        affecting_provisions="Sch. 2",
        affecting_title="Tax Credits Act 2002",
        affected_title="Social Security Administration (Northern Ireland) Act 1992",
    )
    source_root = ET.fromstring(
        """
        <Schedule>
          <Table>
            <TR>
              <TH>Short title and chapter</TH>
              <TH>Extent of repeal</TH>
            </TR>
            <TR>
              <TD>Social Security Administration (Northern Ireland) Act 1992 (c. 8)</TD>
              <TD>In section 142, in subsection (1), the words
              \u201cfrom contributions of any class,\u201d and the words
              \u201cin the case of contributions of that class\u201d and subsection (2).</TD>
            </TR>
          </Table>
        </Schedule>
        """
    )

    ops = compile_effect_to_ir_ops(
        effect,
        source_root,
        lowering_rejections_out=lowering_records,
        source_root=source_root,
    )

    assert ops == []
    assert [record["rule_id"] for record in lowering_records] == [
        "uk_effect_repeal_table_structural_repeal_unresolved"
    ]
    assert lowering_records[0]["reason_code"] == "mixed_structural_and_word_repeal_requires_split"
    assert lowering_records[0]["blocking"] is True


def test_executor_applies_table_entry_inline_text_insertion_to_rowspanned_cell_only() -> None:
    extracted = ET.fromstring(
        "<P4>ii in the second column, in the second entry relating to the Welsh "
        "Ministers, after \u201c(performance of principal councils)\u201d insert "
        "\u201c, Chapter 1A of Part 6 (performance of corporate joint committees).\u201d</P4>"
    )
    ops = compile_effect_to_ir_ops(_uk_table_effect(), extracted)
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(_table_cell_statute(), ops, adjudications_out=adjudications)

    subsection = replayed.body.children[0].children[0]
    table = subsection.children[0]
    first_entry = table.children[0].children[1]
    second_entry = table.children[1].children[0]
    assert "corporate joint committees" not in first_entry.text
    assert "corporate joint committees" in second_entry.text
    assert adjudications == []


def test_executor_blocks_table_entry_inline_text_insertion_when_table_not_unique() -> None:
    op = compile_effect_to_ir_ops(
        _uk_table_effect(),
        ET.fromstring(
            "<P4>ii in the second column, in the second entry relating to the Welsh "
            "Ministers, after \u201c(performance of principal councils)\u201d insert "
            "\u201c, Chapter 1A of Part 6 (performance of corporate joint committees).\u201d</P4>"
        ),
    )[0]
    adjudications: list[CompileAdjudication] = []

    replay_uk_ops(_table_cell_statute(duplicate_table=True), [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_table_entry_inline_text_insertion_unresolved"
    assert adjudications[0].detail["reason_code"] == "table_not_unique"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_executor_records_text_match_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_no_match",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="does-not-exist", occurrence=0),
                replacement="updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_missing"
    assert adjudications[0].detail["action"] == "text_replace"
    assert adjudications[0].detail["text_match"] == "does-not-exist"


def test_executor_records_missing_structured_text_patch_payload() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_missing_structured_payload",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == "Section one."
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_patch_missing_structured_payload"
    assert adjudications[0].detail["action"] == "text_replace"
    assert adjudications[0].detail["target"] == "section:1"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["reason_code"] == "missing_structured_text_patch"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


def test_executor_recovers_implicit_first_subparagraph_parent_text_patch() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="6",
                text="",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="43A",
                        text=(
                            "Where a youth rehabilitation order imposes an "
                            "electronic monitoring requirement, the offender must comply."
                        ),
                        children=(
                            IRNode(kind=IRNodeKind.ITEM, label="i", text="a separate item"),
                            IRNode(kind=IRNodeKind.ITEM, label="a", text="first item"),
                            IRNode(kind=IRNodeKind.ITEM, label="b", text="second item"),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_implicit_first_subparagraph_parent_text",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(
                path=(
                    ("schedule", "6"),
                    ("paragraph", "43A"),
                    ("subparagraph", "1"),
                ),
            ),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="electronic monitoring requirement", occurrence=0),
                replacement="electronic compliance monitoring requirement",
            ),
            source=_source(),
        )
    )

    paragraph = executor.statute.supplements[0].children[0]
    assert "electronic compliance monitoring requirement" in paragraph.text
    assert [child.label for child in paragraph.children] == ["i", "a", "b"]
    assert paragraph.children[0].text == "a separate item"
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_implicit_first_subparagraph_parent_text_recovered"
    assert adjudications[0].detail["target"] == "schedule:6/paragraph:43A/subparagraph:1"
    assert adjudications[0].detail["recovery_target"] == "schedule:6/paragraph:43A"
    assert adjudications[0].detail["family"] == "target_resolution_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "apply"


def test_executor_recovers_direct_section_paragraph_text_patch_from_unique_child() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="local transport authority means a transport authority",
                        ),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="authority"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_direct_section_paragraph_child_text",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="authority", occurrence=2),
                replacement="authority (i) ",
            ),
            source=_source(),
        )
    )

    section = executor.statute.body.children[0]
    assert "transport authority (i) " in section.children[0].text
    assert section.children[1].text == "authority"
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_direct_section_paragraph_child_text_recovered"
    assert adjudications[0].detail["target"] == "section:48/paragraph:a"
    assert adjudications[0].detail["recovery_target"] == "section:48/subsection:1"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "apply"


def test_executor_blocks_direct_section_paragraph_text_patch_when_child_text_ambiguous() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="authority authority"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="authority authority"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_direct_section_paragraph_ambiguous_child_text",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="authority", occurrence=2),
                replacement="authority (i) ",
            ),
            source=_source(),
        )
    )

    section = executor.statute.body.children[0]
    assert section.children[0].text == "authority authority"
    assert section.children[1].text == "authority authority"
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_direct_section_paragraph_carrier_gap"


def test_executor_records_punctuation_spacing_text_match_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Reference to the Fatal Accidents and Sudden Deaths Inquiry (Scotland) Act 1976 (c. 14).",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_citation_spacing",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="Fatal Accidents and Sudden Deaths Inquiry (Scotland) Act 1976 (c.14)",
                    occurrence=0,
                ),
                replacement="Inquiries into Fatal Accidents and Sudden Deaths etc. (Scotland) Act 2016",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == (
        "Reference to the Inquiries into Fatal Accidents and Sudden Deaths etc. (Scotland) Act 2016."
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["family"] == "text_match_recovery"


def test_executor_records_punctuation_spacing_text_match_recovery_when_feed_has_citation_space() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="26B",
                    text="which contravenes the Data Protection Act 1998 (c.29),",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_citation_feed_spacing",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "26B"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="the Data Protection Act 1998 (c. 29)",
                    occurrence=0,
                ),
                replacement="the data protection legislation",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == (
        "which contravenes the data protection legislation,"
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]


def test_executor_records_punctuation_spacing_text_match_recovery_with_trailing_feed_space() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="22",
                    text=(
                        "final for the purposes of section 28 (appeals) "
                        "of the Sheriff Courts (Scotland) Act 1907 (c. 51)."
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_trailing_feed_space",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "22"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "section 28 (appeals) of the Sheriff Courts "
                        "(Scotland) Act 1907 (c.51) "
                    ),
                    occurrence=0,
                ),
                replacement="section 114(1) of the Courts Reform (Scotland) Act 2014",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == (
        "final for the purposes of section 114(1) of the Courts Reform (Scotland) Act 2014."
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]


def test_executor_records_punctuation_spacing_text_from_to_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="old words from a reference to the Fire Services Act 1947 (c. 41) and more",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_from_to_citation_spacing",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_FROM_a_TO_(c.41)", occurrence=0),
                replacement="an employee of a relevant authority",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == (
        "old words from an employee of a relevant authority and more"
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]


def test_executor_text_from_to_occurrence_counts_single_word_tokens() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="16",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text=(
                                        "any refusal of an ordinary Surveillance Commissioner "
                                        "to approve an authorisation for the carrying out of "
                                        "intrusive surveillance;"
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_range_occurrence_word_token",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "16"), ("subsection", "1"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_FROM_an_TO_surveillance",
                    occurrence=2,
                ),
                replacement="the authorisation",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].children[0].text == (
        "any refusal of an ordinary Surveillance Commissioner to approve the authorisation;"
    )
    assert [row.kind for row in adjudications] == [
        "uk_replay_text_range_anchor_word_boundary_normalized",
        "uk_replay_node_local_range_text_rewrite_applied",
    ]
    assert adjudications[0].detail["family"] == "text_match_recovery"
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[1].detail["family"] == "text_rewrite_recovery"
    assert adjudications[1].detail["blocking"] is False
    assert adjudications[1].detail["strict_disposition"] == "record"
    assert adjudications[1].detail["source_shape"] == "node_local_range_selector"


def test_executor_records_word_punctuation_elision_text_match_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2003/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="21",
                    text="The tenants soninlaw may apply.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_word_punctuation_elision",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "21"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="tenant's son-in-law", occurrence=0),
                replacement="eligible family member",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == "The eligible family member may apply."
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_word_punctuation_elided"
    ]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["family"] == "text_match_recovery"


def test_executor_does_not_word_punctuation_recover_across_whitespace() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2003/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="21",
                    text="The tenant s son in law may apply.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_word_punctuation_elision_negative",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "21"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="tenant's son-in-law", occurrence=0),
                replacement="eligible family member",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == "The tenant s son in law may apply."
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_normalized_preimage_present_gap"
    ]


def test_executor_records_rotated_trailing_comma_omission_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text=(
                        "Schedule 15 (life sentence for second offence: "
                        "listed offences), Part 4 is amended as follows."
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_rotated_trailing_comma_omission",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="Part 4,", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == (
        "Schedule 15 (life sentence for second offence: listed offences), "
        "is amended as follows."
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_rotated_trailing_comma_omission"
    ]
    assert str(adjudications[0].detail["applied_match"]).strip() == "Part 4"
    assert adjudications[0].detail["source_shape"] == "trailing_comma_rotated_before_phrase"
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["family"] == "text_match_recovery"


def test_executor_does_not_rotate_trailing_comma_omission_when_phrase_is_not_unique() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Before Part 4 and, Part 4 is amended as follows.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_rotated_trailing_comma_omission_not_unique",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="Part 4,", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == "Before Part 4 and, Part 4 is amended as follows."
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_normalized_preimage_present_gap"
    ]
    assert adjudications[0].detail["blocking"] is True


def test_executor_classifies_text_match_already_rewritten() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Alpha new Beta"),),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_already_rewritten",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_already_rewritten"
    assert adjudications[0].detail["text_match"] == "old"
    assert adjudications[0].detail["replacement_text"] == "new"
    assert executor.statute.body.children[0].text == "Alpha new Beta"


def test_executor_uses_typed_text_patch_without_legacy_text_fields() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Alpha old Beta"),),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_typed_patch",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
        )
    )

    assert adjudications == []
    assert executor.statute.body.children[0].text == "Alpha new Beta"


def test_executor_classifies_same_target_text_patch_preimage_drift() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Alpha old Beta"),),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_first",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
        )
    )
    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_preimage_drift",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="other",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_patch_preimage_drift"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["prior_same_target_text_patch_op_ids"] == ("uk_test_text_replace_first",)
    assert adjudications[0].detail["prior_same_target_text_patch_count"] == 1
    assert adjudications[0].detail["target_container"] == "section"
    assert adjudications[0].detail["target_granularity"] == "section"
    assert executor.statute.body.children[0].text == "Alpha new Beta"


def test_executor_records_normalized_replacement_already_present_before_preimage_drift() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Alpha old Beta"),),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_first",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="30 December 2020",
            ),
            source=_source(),
        )
    )
    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_replacement_normalized_present",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="\u201830 September 2020\u2019", occurrence=0),
                replacement="\u201830 December 2020",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_replacement_normalized_present"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "replacement_normalized_present"
    assert adjudications[0].detail["prior_same_target_text_patch_op_ids"] == ("uk_test_text_replace_first",)
    assert executor.statute.body.children[0].text == "Alpha 30 December 2020 Beta"


def test_executor_classifies_multi_prior_same_target_text_patch_preimage_drift() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Alpha old one two"),),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    for sequence, op_id, match_text, replacement in (
        (1, "uk_test_text_replace_first", "old", "new"),
        (2, "uk_test_text_replace_second", "one", "uno"),
        (3, "uk_test_text_replace_multi_drift", "old", "other"),
    ):
        executor.apply_op(
            LegalOperation(
                op_id=op_id,
                sequence=sequence,
                action=StructuralAction.TEXT_REPLACE,
                target=LegalAddress(path=(("section", "1"),)),
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=match_text, occurrence=0),
                    replacement=replacement,
                ),
                source=_source(),
            )
        )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_patch_preimage_drift_multi_prior_same_target"
    assert adjudications[0].detail["prior_same_target_text_patch_op_ids"] == (
        "uk_test_text_replace_first",
        "uk_test_text_replace_second",
    )
    assert adjudications[0].detail["prior_same_target_text_patch_count"] == 2
    assert executor.statute.body.children[0].text == "Alpha new uno two"


def test_executor_recovers_unique_numeric_list_trailing_comma_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/5",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="17",
                    text="Subject to sections 18, 19, 20, 23, 27, 28 and 60 of this Act.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    for sequence, op_id, match_text, replacement in (
        (1, "uk_test_numeric_list_first", "18", "18 to 18C "),
        (2, "uk_test_numeric_list_second", "27,", "27, 27A, "),
        (3, "uk_test_numeric_list_trailing_comma", "28,", "28, 28A, "),
    ):
        executor.apply_op(
            LegalOperation(
                op_id=op_id,
                sequence=sequence,
                action=StructuralAction.TEXT_REPLACE,
                target=LegalAddress(path=(("section", "17"),)),
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=match_text, occurrence=0),
                    replacement=replacement,
                ),
                source=_source(),
            )
        )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_numeric_list_trailing_comma_anchor_normalized"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "numeric_list_trailing_comma_before_conjunction"
    assert adjudications[0].detail["applied_match"] == "28"
    assert adjudications[0].detail["prior_same_target_text_patch_op_ids"] == (
        "uk_test_numeric_list_first",
        "uk_test_numeric_list_second",
    )
    assert "28, 28A, and 60" in executor.statute.body.children[0].text


def test_executor_rejects_ambiguous_numeric_list_trailing_comma_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/5",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="17",
                    text="Sections 28 and 60 apply; sections 28 and 61 also apply.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    for sequence, op_id, match_text, replacement in (
        (1, "uk_test_numeric_list_prior", "Sections", "Provisions"),
        (2, "uk_test_numeric_list_ambiguous", "28,", "28, 28A, "),
    ):
        executor.apply_op(
            LegalOperation(
                op_id=op_id,
                sequence=sequence,
                action=StructuralAction.TEXT_REPLACE,
                target=LegalAddress(path=(("section", "17"),)),
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=match_text, occurrence=0),
                    replacement=replacement,
                ),
                source=_source(),
            )
        )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_patch_preimage_drift"
    assert adjudications[0].detail["blocking"] is True
    assert executor.statute.body.children[0].text == (
        "Provisions 28 and 60 apply; sections 28 and 61 also apply."
    )


def test_executor_classifies_synthetic_text_selector_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_synthetic_text_selector_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_FROM_opening_TO_END", occurrence=0),
                replacement="replacement",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_synthetic_selector_gap"
    assert adjudications[0].detail["blocking"] is True
    assert executor.statute.body.children[0].text == "Section one."


def test_executor_classifies_range_synthetic_text_selector_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_range_synthetic_text_selector_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="FROM_informed_TO_practicable and", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_synthetic_selector_gap"
    assert adjudications[0].detail["blocking"] is True
    assert executor.statute.body.children[0].text == "Section one."


def test_executor_applies_labeled_child_end_range_without_target_hijack() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="58",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text=(
                                "In making a guardianship order the sheriff shall, "
                                "except where— require an individual appointed as guardian "
                                "to find caution."
                            ),
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="the individual is unable to find caution; but",
                                ),
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text=(
                                        "the sheriff is satisfied that nevertheless he is "
                                        "suitable to be appointed guardian,"
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_labeled_child_end_range",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "58"), ("subsection", "6"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=f"TEXT_FROM_CHILD_END{US}paragraph{US}b{US}shall",
                    occurrence=0,
                ),
                replacement="may",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == (
        "In making a guardianship order the sheriff may require an individual "
        "appointed as guardian to find caution."
    )
    assert subsection.children == ()
    assert [row.kind for row in adjudications] == ["uk_replay_labeled_child_end_range_applied"]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_executor_blocks_labeled_child_end_range_when_child_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="58",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text="The sheriff shall, except where— require caution.",
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="condition a"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_labeled_child_end_range_missing_child",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "58"), ("subsection", "6"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=f"TEXT_FROM_CHILD_END{US}paragraph{US}b{US}shall",
                    occurrence=0,
                ),
                replacement="may",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "The sheriff shall, except where— require caution."
    assert len(subsection.children) == 1
    assert [row.kind for row in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["blocking"] is True


def test_executor_applies_after_anchor_to_end_text_patch_without_flattening_children() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="224",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="imprisonment for a term of not more than 1 year",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="child text preserved",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_after_anchor_to_end",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "224"), ("subsection", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_more than_TO_END", occurrence=0),
                replacement="6 months or 12 months",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "imprisonment for a term of not more than 6 months or 12 months"
    assert subsection.children[0].text == "child text preserved"
    assert [row.kind for row in adjudications] == ["uk_replay_after_anchor_to_end_text_rewrite_applied"]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "after_anchor_to_end_selector"


def test_executor_applies_before_child_text_patch_without_flattening_children() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="323",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="The old opening words, taking into account",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="first factor",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_before_child_text_replace",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "323"), ("subsection", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_BEFORE_CHILD_paragraph_a",
                    occurrence=0,
                ),
                replacement="The minimum term must be adjusted, taking into account-",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "The minimum term must be adjusted, taking into account-"
    assert subsection.children[0].text == "first factor"
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_before_child_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == "TEXT_BEFORE_CHILD_paragraph_a"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "source_carried_before_child_selector"


def test_executor_blocks_after_child_text_patch_when_anchor_is_ambiguous() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="11",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    children=(
                                        IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="i", text="first"),
                                        IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="i", text="second"),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_after_child_ambiguous_anchor",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "11"), ("subsection", "1"), ("paragraph", "b"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_subparagraph_i", occurrence=0),
                replacement="or",
            ),
            source=_source(),
        )
    )

    subparagraphs = executor.statute.body.children[0].children[0].children[0].children
    assert [child.text for child in subparagraphs] == ["first", "second"]
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["text_match"] == "TEXT_AFTER_CHILD_subparagraph_i"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_executor_classifies_normalized_preimage_present_text_match_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="The Data Protection Act 1998 c29 applies.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_normalized_preimage_present",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Data Protection Act 1998 (c. 29)", occurrence=0),
                replacement="UK GDPR",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_normalized_preimage_present_gap"
    assert adjudications[0].detail["source_shape"] == "normalized_preimage_present"
    assert adjudications[0].detail["blocking"] is True
    assert executor.statute.body.children[0].text == "The Data Protection Act 1998 c29 applies."


def test_executor_classifies_non_substantive_text_selector_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_non_substantive_text_selector_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text=".....", occurrence=0),
                replacement="replacement",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_non_substantive_selector_gap"
    assert adjudications[0].detail["blocking"] is True
    assert executor.statute.body.children[0].text == "Section one."


def test_executor_classifies_multi_fragment_text_selector_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="The first phrase remains, and the second phrase remains too.",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_multi_fragment_text_selector_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text="first phrase”, “second phrase",
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_multi_fragment_selector_gap"
    assert adjudications[0].detail["source_shape"] == "multi_fragment_text_selector"
    assert adjudications[0].detail["blocking"] is True
    assert executor.statute.body.children[0].text == (
        "The first phrase remains, and the second phrase remains too."
    )


def test_executor_classifies_citation_tail_surface_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text=(
                        "This is construed in accordance with section 2(28) of "
                        "the Regulation of Care (Scotland) Act, of a person who cares."
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_citation_tail_surface_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="section 2(28) of the Regulation of Care (Scotland) Act 2001",
                    occurrence=0,
                ),
                replacement="paragraph 20 of schedule 12",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_citation_tail_surface_gap"
    assert adjudications[0].detail["source_shape"] == "citation_tail_surface_gap"
    assert adjudications[0].detail["blocking"] is True
    assert "Act," in executor.statute.body.children[0].text


def test_executor_classifies_valid_alphanumeric_section_gap_under_body_wrappers() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/3",
        title="Containerized Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="1",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="6B", text="Previous alpha section."),
                        IRNode(kind=IRNodeKind.SECTION, label="7", text="Next section."),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_repeal_absent_6c",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "6C"),)),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_sectionlike_range_gap"
    assert adjudications[0].detail["target"] == "section:6C"


def test_executor_classifies_absent_sibling_range_gap_separately_from_repeal() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/3",
        title="Sibling Range Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="10",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="First."),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Third."),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_absent_sibling_range_gap",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "10"), ("subsection", "2"))),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_absent_sibling_range_gap"
    assert adjudications[0].detail["target"] == "section:10/subsection:2"
    assert [child.label for child in statute.body.children[0].children] == ["1", "3"]


def test_executor_preserves_direct_section_paragraph_descendant_target_shape() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Direct Section Paragraph Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="399",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label="c",
                            text="a sentence is required by one of the following provisions—",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBPARAGRAPH,
                                    label="ii",
                                    text="ii\n\nsection 312(2) minimum sentence.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_direct_section_paragraph_descendant_patch",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "399"), ("paragraph", "c"), ("subparagraph", "ii"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="312(2)", occurrence=0),
                replacement="312(2) or (2A)",
            ),
            source=_source(),
            witness_rule_id="uk_effect_direct_section_paragraph_target_normalized",
        )
    )

    subparagraph = executor.statute.body.children[0].children[0].children[0]
    assert subparagraph.text == "ii\n\nsection 312(2) or (2A) minimum sentence."
    assert adjudications == []


def test_executor_classifies_missing_schedule_range_gap_separately_from_repeal() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/3",
        title="Schedule Range Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Schedule 1."),
            IRNode(kind=IRNodeKind.SCHEDULE, label="3", text="Schedule 3."),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_missing_schedule_range_gap",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("schedule", "2"),)),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_schedule_range_gap"
    assert adjudications[0].detail["target"] == "schedule:2"
    assert [schedule.label for schedule in statute.supplements] == ["1", "3"]


def test_executor_classifies_missing_schedule_range_gap_for_text_patch() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/3",
        title="Schedule Range Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Schedule 1."),
            IRNode(kind=IRNodeKind.SCHEDULE, label="3", text="Schedule 3."),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_missing_schedule_range_text_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "2"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_schedule_range_gap"
    assert adjudications[0].detail["target"] == "schedule:2"


def test_executor_classifies_missing_schedule_branch_gap_separately_from_repeal() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/3",
        title="Schedule Branch Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="Schedule 1."),),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_missing_schedule_branch_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "2"), ("paragraph", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_schedule_branch_gap"
    assert adjudications[0].detail["target"] == "schedule:2/paragraph:1"
    assert [schedule.label for schedule in statute.supplements] == ["1"]


def test_executor_classifies_missing_heading_carrier_for_text_patch() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_missing_heading_carrier_text_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "13"),), special=FacetKind.HEADING),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Uniform", occurrence=0),
                replacement="Uniform and publication of images",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_heading_facet_target_gap"
    assert adjudications[0].detail["target"] == "section:13/heading"
    assert classify_uk_replay_adjudication_bucket(adjudications[0].kind) == "source_shape"


def test_executor_records_unsupported_action() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_unsupported",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_unsupported_action"
    assert adjudications[0].detail["action"] == "renumber"
    assert adjudications[0].detail["rule_id"] == "uk_replay_unsupported_action"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].op_id == "uk_test_renumber_unsupported"


def test_executor_classifies_missing_source_for_supported_descendant_renumber() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_missing_source",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "676af"),)),
            destination=LegalAddress(path=(("section", "676af"), ("subsection", "1"))),
            source=_source(),
            witness_rule_id="uk_effect_metadata_renumber_lowered",
        )
    )

    assert len(adjudications) == 1
    adjudication = adjudications[0]
    assert adjudication.kind == "uk_replay_missing_source_target_gap"
    assert adjudication.detail["action"] == "renumber"
    assert adjudication.detail["target"] == "section:676af"
    assert adjudication.detail["destination"] == "section:676af/subsection:1"
    assert adjudication.detail["reason_code"] == "renumber_source_target_absent"
    assert adjudication.detail["family"] == "source_shape_gap"
    assert classify_uk_replay_adjudication_bucket(adjudication.kind) == "source_shape"


def test_executor_classifies_existing_destination_for_supported_sibling_renumber() -> None:
    statute = IRStatute(
        statute_id="ukpga/2010/15",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="26",
                attrs={"eId": "schedule-26"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="5",
                        text="5 Source paragraph.",
                        attrs={"eId": "schedule-26-paragraph-5"},
                    ),
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="15",
                        text="15 Destination already present.",
                        attrs={"eId": "schedule-26-paragraph-15"},
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_destination_exists",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("schedule", "26"), ("paragraph", "5"))),
            destination=LegalAddress(path=(("schedule", "26"), ("paragraph", "15"))),
            source=_source(),
            witness_rule_id="uk_effect_metadata_sibling_renumber_lowered",
        )
    )

    assert len(adjudications) == 1
    adjudication = adjudications[0]
    assert adjudication.kind == "uk_replay_existing_target_conflict_gap"
    assert adjudication.detail["action"] == "renumber"
    assert adjudication.detail["target"] == "schedule:26/paragraph:5"
    assert adjudication.detail["destination"] == "schedule:26/paragraph:15"
    assert adjudication.detail["reason_code"] == "renumber_destination_target_present"
    assert adjudication.detail["family"] == "source_shape_gap"
    assert classify_uk_replay_adjudication_bucket(adjudication.kind) == "source_shape"
    assert [child.label for child in executor.statute.supplements[0].children] == ["5", "15"]


def test_executor_applies_same_provision_descendant_renumber_then_text_patch() -> None:
    statute = IRStatute(
        statute_id="ukpga/2024/3",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="9",
                text="",
                attrs={"eId": "schedule-9"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="132",
                        text="132\n\nA rule relating to a member's entitlement to benefits.",
                        attrs={"eId": "schedule-9-paragraph-132"},
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_132",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("schedule", "9"), ("paragraph", "132"))),
            destination=LegalAddress(
                path=(("schedule", "9"), ("paragraph", "132"), ("subparagraph", "1"))
            ),
            source=_source(),
            witness_rule_id="uk_effect_metadata_renumber_lowered",
        )
    )
    executor.apply_op(
        LegalOperation(
            op_id="uk_test_patch_132_1",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(
                path=(("schedule", "9"), ("paragraph", "132"), ("subparagraph", "1"))
            ),
            source=_source(),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="member's entitlement to", occurrence=0),
                replacement="member's entitlement to, or to the payment of,",
            ),
        )
    )

    paragraph = executor.statute.supplements[0].children[0]
    assert paragraph.kind is IRNodeKind.PARAGRAPH
    assert paragraph.label == "132"
    assert paragraph.text == ""
    assert len(paragraph.children) == 1
    subparagraph = paragraph.children[0]
    assert subparagraph.kind is IRNodeKind.SUBPARAGRAPH
    assert subparagraph.label == "1"
    assert subparagraph.attrs["eId"] == "schedule-9-paragraph-132-1"
    assert subparagraph.text == "1A rule relating to a member's entitlement to, or to the payment of, benefits."
    assert adjudications == []


def test_executor_applies_same_provision_descendant_renumber_with_existing_children() -> None:
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="26",
                attrs={"eId": "schedule-26"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="12",
                        text="12 Existing paragraph opening words.",
                        attrs={"eId": "schedule-26-paragraph-12"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="a",
                                text="a Existing item.",
                                attrs={"eId": "schedule-26-paragraph-12-a"},
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_26_12",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("schedule", "26"), ("paragraph", "12"))),
            destination=LegalAddress(
                path=(("schedule", "26"), ("paragraph", "12"), ("subparagraph", "1"))
            ),
            source=_source(),
            witness_rule_id="uk_effect_metadata_renumber_lowered",
        )
    )

    paragraph = executor.statute.supplements[0].children[0]
    assert paragraph.label == "12"
    assert paragraph.text == ""
    assert len(paragraph.children) == 1
    subparagraph = paragraph.children[0]
    assert subparagraph.kind is IRNodeKind.SUBPARAGRAPH
    assert subparagraph.label == "1"
    assert subparagraph.attrs["eId"] == "schedule-26-paragraph-12-1"
    assert subparagraph.children[0].label == "a"
    assert adjudications == []


def test_executor_descendant_renumber_ignores_recursive_item_label_collision() -> None:
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="26",
                attrs={"eId": "schedule-26"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="12",
                        text="In section 218A (life sentence for second listed offence)—",
                        attrs={"eId": "schedule-26-paragraph-12"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="c",
                                text="c Existing item.",
                                attrs={"eId": "schedule-26-paragraph-12-c"},
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="i",
                                        text="i Existing nested item.",
                                        attrs={"eId": "schedule-26-paragraph-12-c-i"},
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_26_12_recursive_collision",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("schedule", "26"), ("paragraph", "12"))),
            destination=LegalAddress(
                path=(("schedule", "26"), ("paragraph", "12"), ("subparagraph", "1"))
            ),
            source=_source(),
            witness_rule_id="uk_effect_metadata_renumber_lowered",
        )
    )

    paragraph = executor.statute.supplements[0].children[0]
    assert paragraph.label == "12"
    assert paragraph.text == ""
    assert len(paragraph.children) == 1
    subparagraph = paragraph.children[0]
    assert subparagraph.kind is IRNodeKind.SUBPARAGRAPH
    assert subparagraph.label == "1"
    assert subparagraph.attrs["eId"] == "schedule-26-paragraph-12-1"
    assert subparagraph.children[0].label == "c"
    assert subparagraph.children[0].children[0].label == "i"
    assert adjudications == []


def test_executor_applies_same_parent_sibling_renumber_after_repeal() -> None:
    statute = IRStatute(
        statute_id="asc/2024/6",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="16",
                    attrs={"eId": "section-16"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="8",
                            text="8 Old subsection.",
                            attrs={"eId": "section-16-8"},
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="9",
                            text="9 Later subsection.",
                            attrs={"eId": "section-16-9"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_repeal_16_8",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "16"), ("subsection", "8"))),
            source=_source(),
        )
    )
    executor.apply_op(
        LegalOperation(
            op_id="uk_test_renumber_16_9_to_16_8",
            sequence=2,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "16"), ("subsection", "9"))),
            destination=LegalAddress(path=(("section", "16"), ("subsection", "8"))),
            source=_source(),
            witness_rule_id="uk_effect_metadata_sibling_renumber_lowered",
        )
    )

    section = executor.statute.body.children[0]
    assert [(child.label, child.text, child.attrs["eId"]) for child in section.children] == [
        ("8", "8Later subsection.", "section-16-8")
    ]
    assert adjudications == []


def test_executor_records_payload_mismatch() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_payload_mismatch",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "9"), ("subsection", "1"))),
            payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Inserted subsection."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_root_parent_shape_gap"
    assert adjudications[0].detail["target"] == "section:9/subsection:1"


def test_executor_records_missing_parent_grandparent_present_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_missing_parent_grandparent_present",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"), ("subsection", "9"), ("paragraph", "a"))),
            payload=IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Inserted paragraph."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_missing_parent_grandparent_present_gap"
    assert adjudications[0].detail["target"] == "section:1/subsection:9/paragraph:a"
    assert executor.statute.body.children[0].text == "Section one."


def test_repeal_ignores_coarse_eid_candidate_when_leaf_target_differs() -> None:
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="265",
                    attrs={"eId": "section-265"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            attrs={"eId": "section-265-1"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="the offender-",
                                    attrs={"eId": "section-265-1-b"},
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="i",
                                            text="was aged 18 or over, and",
                                            attrs={"eId": "section-265-1-b-i"},
                                        ),
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="ii",
                                            text="is aged under 21.",
                                            attrs={"eId": "section-265-1-b-ii"},
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=[])

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_repeal_subparagraph_i_not_paragraph_b",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(
                path=(
                    ("section", "265"),
                    ("subsection", "1"),
                    ("paragraph", "b"),
                    ("subparagraph", "i"),
                )
            ),
            source=_source(),
        )
    )

    paragraph_b = executor.statute.body.children[0].children[0].children[0]
    assert paragraph_b.kind == IRNodeKind.PARAGRAPH
    assert paragraph_b.label == "b"
    assert [(child.kind, child.label, child.text) for child in paragraph_b.children] == [
        (IRNodeKind.SUBPARAGRAPH, "ii", "is aged under 21.")
    ]


def test_executor_records_payload_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_payload_missing",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"), ("subsection", "2"))),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_payload_missing"
    assert adjudications[0].detail["action"] == "insert"
    assert adjudications[0].detail["target"] == "section:1/subsection:2"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


def test_executor_records_replace_payload_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_replace_payload_missing",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_payload_missing"
    assert adjudications[0].detail["action"] == "replace"
    assert adjudications[0].detail["target"] == "section:1"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"


def test_executor_records_existing_target_conflict_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_duplicate_section",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Duplicate section."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_existing_target_conflict_gap"
    assert adjudications[0].detail["target"] == "section:1"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["existing_text_preview"] == "section one"
    assert adjudications[0].detail["payload_text_preview"] == "duplicate section"


def test_executor_insert_target_lookup_does_not_hijack_nested_roman_item() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="26",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="12",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="1",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="c",
                                        children=(
                                            IRNode(kind=IRNodeKind.ITEM, label="i", text="first nested item"),
                                            IRNode(kind=IRNodeKind.ITEM, label="ii", text="second nested item"),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_subparagraph_without_nested_roman_hijack",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("schedule", "26"), ("paragraph", "12"), ("subparagraph", "2"))),
            payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="2", text="Inserted subparagraph."),
            source=_source(),
        )
    )

    paragraph = executor.statute.supplements[0].children[0]
    assert [(child.kind, child.label, child.text) for child in paragraph.children] == [
        (IRNodeKind.SUBPARAGRAPH, "1", ""),
        (IRNodeKind.SUBPARAGRAPH, "2", "Inserted subparagraph."),
    ]
    nested_item = paragraph.children[0].children[0].children[1]
    assert (nested_item.kind, nested_item.label, nested_item.text) == (
        IRNodeKind.ITEM,
        "ii",
        "second nested item",
    )
    assert [row.kind for row in adjudications] == []


def test_executor_records_existing_target_already_materialized() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_already_materialized_section",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Section one."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_existing_target_already_materialized"
    assert adjudications[0].detail["target"] == "section:1"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "record"


def test_executor_records_crossheading_insert_target_gap() -> None:
    statute = IRStatute(
        statute_id="asp/2003/13",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(IRNode(kind=IRNodeKind.CROSSHEADING, label=None, text="Existing crossheading"),),
        ),
        supplements=(),
    )
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_insert_unanchored_crossheading",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("crossheading", ""),)),
            payload=IRNode(kind=IRNodeKind.CROSSHEADING, label=None, text="New crossheading"),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_crossheading_target_gap"
    assert adjudications[0].detail["target"] == "crossheading:"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_replay_uk_ops_collects_adjudications() -> None:
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_replay_api_collects",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "9"),)),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="a", text="Missing replacement"),
        source=_source(),
    )

    replay_uk_ops(_base_statute(), [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_replace_payload_target_leaf_mismatch_gap"
    assert adjudications[0].op_id == "uk_test_replay_api_collects"


def test_executor_records_alpha_subsection_under_numeric_subsections_as_malformed() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    text="",
                    attrs={"eId": "section-48"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="local transport authority means the authority",
                            attrs={"eId": "section-48-1"},
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Other definitions",
                            attrs={"eId": "section-48-2"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_replace_alpha_subsection_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"), ("subsection", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="authority", occurrence=0),
                replacement="authority (i)",
            ),
            source=_source(),
            provenance_tags=("original_ref: subsection (1)(a)",),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_subsection_descendant_target_collapse_gap"
    assert adjudications[0].detail["target"] == "section:48/subsection:a"
    assert statute.body.children[0].children[0].text == "local transport authority means the authority"


def test_executor_records_placeholder_label_malformed_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Existing paragraph."),),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_placeholder_label_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"), ("subsection", "1"), ("paragraph", "[inserted]"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Section", occurrence=0),
                replacement="Updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_placeholder_label_gap"
    assert adjudications[0].detail["target"] == "section:1/subsection:1/paragraph:[inserted]"
    assert statute.body.children[0].children[0].children[0].text == "Existing paragraph."


def test_executor_records_note_or_crossheading_malformed_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="Existing paragraph."),),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_note_label_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"), ("subsection", "1"), ("paragraph", "note"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Section", occurrence=0),
                replacement="Updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_note_or_crossheading_gap"
    assert adjudications[0].detail["target"] == "section:1/subsection:1/paragraph:note"


def test_executor_records_granularity_collapse_malformed_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Numeric subsection."),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Second subsection."),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_granularity_collapse_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"), ("subsection", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Numeric", occurrence=0),
                replacement="Updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_granularity_collapse_gap"
    assert adjudications[0].detail["target"] == "section:48/subsection:a"
    assert statute.body.children[0].children[0].text == "Numeric subsection."


def test_executor_records_sectionlike_label_malformed_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_sectionlike_label_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "and"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Section", occurrence=0),
                replacement="Updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_sectionlike_label_gap"
    assert adjudications[0].detail["target"] == "section:and"


def test_executor_records_nested_sectionlike_label_malformed_before_missing_parent() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_nested_sectionlike_label_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "appt"), ("subsection", "day"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="appointed day", occurrence=0),
                replacement="replacement",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_sectionlike_label_gap"
    assert adjudications[0].detail["target"] == "section:appt/subsection:day"


def test_executor_records_schedule_root_label_malformed_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_root_label_malformed",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", ""),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Schedule", occurrence=0),
                replacement="Updated",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_malformed_target_schedule_root_label_gap"
    assert adjudications[0].detail["target"] == "schedule:"


def test_executor_records_schedule_partition_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="2",
                attrs={"eId": "schedule-2"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="2",
                        attrs={"eId": "schedule-2-part-2"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="79",
                                text="Partitioned paragraph.",
                                attrs={"eId": "schedule-2-part-2-paragraph-79"},
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_partition_target_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "2"), ("paragraph", "80"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Partitioned", occurrence=0),
                replacement="Changed",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_partition_part_target_gap"
    assert adjudications[0].detail["target"] == "schedule:2/paragraph:80"
    assert statute.supplements[0].children[0].children[0].text == "Partitioned paragraph."


def test_executor_records_schedule_paragraph_carrier_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2010/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label="1",
                        attrs={"eId": "schedule-1-paragraph-1"},
                        children=(),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_paragraph_carrier_gap",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "1"), ("subparagraph", "3A"))),
            payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="3A", text="New subparagraph."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_p1group_wrapper_carrier_gap"
    assert adjudications[0].detail["target"] == "schedule:1/paragraph:1/subparagraph:3A"


def test_executor_resolves_unlabeled_schedule_p1group_single_paragraph_child() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2010/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.P1GROUP, label=None, children=()),
                    IRNode(kind=IRNodeKind.P1GROUP, label=None, children=()),
                    IRNode(kind=IRNodeKind.P1GROUP, label=None, children=()),
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        children=(
                            IRNode(
                                kind=IRNodeKind.PARAGRAPH,
                                label="4",
                                text="4 Existing paragraph.",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBPARAGRAPH,
                                        label="2B",
                                        text="Old subparagraph.",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_p1group_wrapper_resolved",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "4"), ("subparagraph", "2B"))),
            payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="2B", text="New subparagraph."),
            source=_source(),
        )
    )

    paragraph = executor.statute.supplements[0].children[3].children[0]
    assert paragraph.children[0].text == "New subparagraph."
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_schedule_p1group_paragraph_wrapper_resolved"
    ]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "apply"
    assert adjudications[0].detail["family"] == "target_resolution_recovery"


def test_executor_keeps_ambiguous_unlabeled_schedule_p1group_blocked() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2010/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.P1GROUP,
                        label=None,
                        children=(
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="1 Existing paragraph."),
                            IRNode(kind=IRNodeKind.PARAGRAPH, label="1A", text="1A Ambiguous carried paragraph."),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_p1group_wrapper_ambiguous",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "1"), ("subparagraph", "3A"))),
            payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="3A", text="New subparagraph."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_p1group_wrapper_carrier_gap"
    assert adjudications[0].detail["target"] == "schedule:1/paragraph:1/subparagraph:3A"


def test_executor_records_schedule_paragraph_absent_carrier_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2010/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        attrs={"eId": "schedule-1-paragraph-2"},
                        text="Existing paragraph.",
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_paragraph_absent_carrier_gap",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "1"), ("subparagraph", "3A"))),
            payload=IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="3A", text="New subparagraph."),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_paragraph_carrier_gap"
    assert adjudications[0].detail["target"] == "schedule:1/paragraph:1/subparagraph:3A"


def test_executor_recovers_empty_descendant_text_patch_on_parent_text() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        attrs={"eId": "schedule-1-paragraph-1"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="d",
                                attrs={"eId": "schedule-1-paragraph-1-d"},
                                text="Flat parent text mentions section 7(2)(b).",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_empty_descendant_parent_text_recovery",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "1"), ("item", "d"), ("item", "i"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="section 7(2)(b)", occurrence=0),
                replacement="section 59(2)(b)",
            ),
            source=_source(),
        )
    )

    item = executor.statute.supplements[0].children[0].children[0]
    assert item.text == "Flat parent text mentions section 59(2)(b)."
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_empty_descendant_parent_text_recovered"
    assert adjudications[0].detail["target"] == "schedule:1/paragraph:1/item:d/item:i"
    assert adjudications[0].detail["recovery_target"] == "schedule:1/paragraph:1/item:d"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "apply"


def test_executor_materializes_source_carried_labeled_child_text_substitution() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="11",
                    attrs={"eId": "section-11"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            attrs={"eId": "section-11-4"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    attrs={"eId": "section-11-4-b"},
                                    text="An authorisation insofar as relating to a police force,",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_labeled_child_text_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "11"), ("subsection", "4"), ("paragraph", "b"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="a police force,", occurrence=0),
                replacement=(
                    "i where that individual is a member of a police force, a police force; or "
                    "ii where that individual is a police member of the Agency, that Agency,"
                ),
            ),
            source=_source(),
            provenance_tags=(
                "text_rewrite_rule:uk_effect_source_carried_quoted_text_substitution_text_patch",
            ),
        )
    )

    paragraph = executor.statute.body.children[0].children[0].children[0]
    assert paragraph.text == "An authorisation insofar as relating to"
    assert [(child.kind, child.label, child.text, child.attrs.get("eId")) for child in paragraph.children] == [
        (
            IRNodeKind.SUBPARAGRAPH,
            "i",
            "where that individual is a member of a police force, a police force; or",
            "section-11-4-b-i",
        ),
        (
            IRNodeKind.SUBPARAGRAPH,
            "ii",
            "where that individual is a police member of the Agency, that Agency,",
            "section-11-4-b-ii",
        ),
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_source_carried_labeled_child_text_substitution_recovered"
    assert adjudications[0].detail["child_labels"] == ("i", "ii")
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "apply"


def test_executor_materializes_source_carried_labeled_child_text_substitution_prefix() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    attrs={"eId": "section-5"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            attrs={"eId": "section-5-2"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    attrs={"eId": "section-5-2-a"},
                                    text=(
                                        "Scottish Water must have regard to such "
                                        "representations, reports and recommendations "
                                        "as are mentioned in section 2(5)"
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_labeled_child_text_substitution_prefix",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(
                path=(("section", "5"), ("subsection", "2"), ("paragraph", "a"))
            ),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "to such representations, reports and recommendations "
                        "as are mentioned in section 2(5)"
                    ),
                    occurrence=0,
                ),
                replacement=(
                    "to— i any representations made to it by a Customer Panel, and "
                    "ii any recommendations made to it under section 2(4)"
                ),
            ),
            source=_source(),
            provenance_tags=(
                "text_rewrite_rule:uk_effect_source_carried_quoted_text_substitution_text_patch",
            ),
        )
    )

    paragraph = executor.statute.body.children[0].children[0].children[0]
    assert paragraph.text == "Scottish Water must have regard to"
    assert [(child.kind, child.label, child.text, child.attrs.get("eId")) for child in paragraph.children] == [
        (
            IRNodeKind.SUBPARAGRAPH,
            "i",
            "any representations made to it by a Customer Panel, and",
            "section-5-2-a-i",
        ),
        (
            IRNodeKind.SUBPARAGRAPH,
            "ii",
            "any recommendations made to it under section 2(4)",
            "section-5-2-a-ii",
        ),
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_source_carried_labeled_child_text_substitution_recovered"
    assert adjudications[0].detail["source_parent_prefix"] == "to"
    assert adjudications[0].detail["blocking"] is False


def test_executor_materializes_source_carried_alpha_child_text_substitution() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2006/52",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="130",
                    attrs={"eId": "section-130"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            attrs={"eId": "section-130-3"},
                            text=(
                                "This subsection applies if the charge is amended "
                                "after referral."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_alpha_child_text_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "130"), ("subsection", "3"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="if the charge is amended after referral.",
                    occurrence=0,
                ),
                replacement=(
                    "a where the charge is amended after referral; "
                    "b to any charge substituted for or added to the charge after referral; or "
                    "c where extended powers are obtained after referral"
                ),
            ),
            source=_source(),
            provenance_tags=(
                "text_rewrite_rule:uk_effect_source_carried_quoted_text_substitution_text_patch",
            ),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "This subsection applies"
    assert [(child.kind, child.label, child.text, child.attrs.get("eId")) for child in subsection.children] == [
        (
            IRNodeKind.PARAGRAPH,
            "a",
            "where the charge is amended after referral;",
            "section-130-3-a",
        ),
        (
            IRNodeKind.PARAGRAPH,
            "b",
            "to any charge substituted for or added to the charge after referral; or",
            "section-130-3-b",
        ),
        (
            IRNodeKind.PARAGRAPH,
            "c",
            "where extended powers are obtained after referral",
            "section-130-3-c",
        ),
    ]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_source_carried_labeled_child_text_substitution_recovered"
    assert adjudications[0].detail["child_kind"] == "paragraph"
    assert adjudications[0].detail["child_labels"] == ("a", "b", "c")
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_uk_mutable_ir_maps_source_point_alias_to_item_kind() -> None:
    assert uk_ir_node_kind("point") == IRNodeKind.ITEM
    assert UKMutableNode(kind=cast(Any, "point"), label="i").kind == IRNodeKind.ITEM


def test_prepare_replay_uk_ops_canonicalizes_point_address_alias_for_core_ir_boundary() -> None:
    op = LegalOperation(
        op_id="uk_test_point_address_alias",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(("section", "1"), ("subsection", "1"), ("paragraph", "a"), ("point", "i"))
        ),
        payload=IRNode(kind=IRNodeKind.ITEM, label="i", text="replacement"),
        source=_source(),
    )

    prepared = _prepare_replay_uk_ops([op])

    assert prepared.accepted_ops[0].target.path == (
        ("section", "1"),
        ("subsection", "1"),
        ("paragraph", "a"),
        ("item", "i"),
    )
    assert "uk_address_alias:point_to_item" in prepared.accepted_ops[0].provenance_tags
    assert op.target.path[-1] == ("point", "i")


def test_prepare_replay_uk_ops_canonicalizes_point_alias_before_overlap_classification() -> None:
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.ITEM,
                                            label="i",
                                            text="alpha quality partnership scheme beta scheme",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    source = OperationSource(statute_id="ukpga/2020/1", effective="2020-01-01")
    broad = LegalOperation(
        op_id="uk_test_point_alias_broad",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("section", "1"), ("subsection", "1"), ("paragraph", "a"), ("item", "i"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="quality partnership scheme", occurrence=0),
            replacement="partnership scheme",
        ),
        source=source,
    )
    ordinal = LegalOperation(
        op_id="uk_test_point_alias_ordinal",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(
            path=(("section", "1"), ("subsection", "1"), ("paragraph", "a"), ("point", "i"))
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="scheme", occurrence=1),
            replacement="scheme or framework",
        ),
        source=source,
    )

    prepared = _prepare_replay_uk_ops([broad, ordinal], base_ir=statute)

    assert [op.op_id for op in prepared.accepted_ops] == ["uk_test_point_alias_broad"]
    assert [row.kind for row in prepared.rejected_adjudications] == [
        "uk_replay_same_source_text_patch_overlap_blocked"
    ]
    assert prepared.rejected_adjudications[0].op_id == "uk_test_point_alias_ordinal"


def test_executor_canonicalizes_source_point_alias_in_labeled_child_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    attrs={"eId": "section-1"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            attrs={"eId": "section-1-1"},
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    attrs={"eId": "section-1-1-a"},
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.ITEM,
                                            label="i",
                                            attrs={"eId": "section-1-1-a-i"},
                                            text="Parent has old words.",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_point_alias_child_text_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(
                path=(("section", "1"), ("subsection", "1"), ("paragraph", "a"), ("item", "i"))
            ),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old words", occurrence=0),
                replacement="i first child; or ii second child",
            ),
            source=_source(),
            provenance_tags=(
                "text_rewrite_rule:uk_effect_source_carried_quoted_text_substitution_text_patch",
            ),
        )
    )

    item = executor.statute.body.children[0].children[0].children[0].children[0]
    assert item.text == "Parent has"
    assert [(child.kind, child.label, child.text) for child in item.children] == [
        (IRNodeKind.ITEM, "i", "first child; or"),
        (IRNodeKind.ITEM, "ii", "second child"),
    ]
    assert len(adjudications) == 1
    assert adjudications[0].detail["child_kind"] == "item"
    assert adjudications[0].detail["source_child_kind"] == "point"


def test_prepare_uk_operation_payload_node_canonicalizes_point_leaf_kind() -> None:
    effect = UKEffectRecord(
        effect_id="test-effect",
        effect_type="inserted",
        applied=True,
        requires_applied=False,
        modified="2026-01-01",
        affected_uri="",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2026",
        affecting_number="1",
        affecting_provisions="s. 1",
        affecting_title="Amending Act",
    )
    target = LegalAddress(
        path=(("section", "1"), ("subsection", "1"), ("paragraph", "a"), ("point", "i"))
    )

    lowering_rejections: list[dict[str, object]] = []
    prepared = prepare_uk_operation_payload_node(
        effect=effect,
        curr_action="insert",
        content_ir={"kind": "subparagraph", "label": "i", "text": "payload", "children": []},
        target_ref="s. 1(1)(a)(i)",
        target=target,
        payload_match_target=target,
        target_replacement_leaf_override=None,
        target_replacement_leaf_kind=None,
        actual_el=None,
        extracted_el=None,
        extracted_text=None,
        allow_payload_identity_synthesis=False,
        lowering_rejections_out=lowering_rejections,
    )

    assert prepared.skip_effect is False
    assert prepared.payload_node is not None
    assert prepared.payload_node.kind == IRNodeKind.ITEM
    assert lowering_rejections == []


def test_executor_does_not_materialize_labeled_child_text_without_source_rule() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="11",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="An authorisation insofar as relating to a police force,",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_plain_labeled_text_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "11"), ("subsection", "4"), ("paragraph", "b"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="a police force,", occurrence=0),
                replacement="i first visible limb; or ii second visible limb",
            ),
            source=_source(),
        )
    )

    paragraph = executor.statute.body.children[0].children[0].children[0]
    assert paragraph.children == []
    assert "i first visible limb" in paragraph.text
    assert not any(
        row.kind == "uk_replay_source_carried_labeled_child_text_substitution_recovered"
        for row in adjudications
    )


def test_executor_recovers_source_carried_structured_tail_substitution() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="20",
                attrs={"eId": "schedule-20"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="5",
                        attrs={"eId": "schedule-20-paragraph-5"},
                        text="An offence where old tail words.",
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    source = OperationSource(
        statute_id="ukpga/2020/17",
        title="Amending Act",
        raw_text=(
            "5 In paragraph 5, for the words following \u201cwhere\u201d substitute \u2014 "
            "a the first condition, or b the second condition."
        ),
    )

    for label, text in (("a", "the first condition, or"), ("b", "the second condition.")):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_source_carried_structured_tail_substitution_{label}",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("schedule", "20"), ("paragraph", "5"), ("item", label))),
                payload=IRNode(kind=IRNodeKind.ITEM, label=label, text=text),
                source=source,
            )
        )

    paragraph = executor.statute.supplements[0].children[0]
    assert paragraph.text == "An offence where"
    assert [(child.kind, child.label, child.text) for child in paragraph.children] == [
        (IRNodeKind.ITEM, "a", "the first condition, or"),
        (IRNodeKind.ITEM, "b", "the second condition."),
    ]
    assert [row.kind for row in adjudications] == [
        "uk_replay_source_carried_structured_tail_substitution_recovered",
        "uk_replay_source_carried_structured_tail_substitution_recovered",
    ]
    assert adjudications[0].detail["source_anchor"] == "where"
    assert adjudications[0].detail["parent_tail_trimmed"] is True
    assert adjudications[1].detail["parent_tail_trimmed"] is False
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "apply"


def test_executor_recovers_source_carried_structured_tail_substitution_from_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="The order may last for more than the old limit.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    source = OperationSource(
        statute_id="ukpga/2026/2",
        title="Amending Act",
        raw_text=(
            "2 In subsection (2), for the words from “more than” to the end "
            "of the subsection substitute — a more than 6 months, or b more than 12 months."
        ),
    )

    for label, text in (("a", "more than 6 months"), ("b", "more than 12 months")):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_source_carried_structured_tail_from_{label}",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "5"), ("subsection", "2"), ("paragraph", label))),
                payload=IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=text),
                source=source,
            )
        )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "The order may last for"
    assert [(child.kind, child.label, child.text) for child in subsection.children] == [
        (IRNodeKind.PARAGRAPH, "a", "more than 6 months"),
        (IRNodeKind.PARAGRAPH, "b", "more than 12 months"),
    ]
    assert [row.kind for row in adjudications] == [
        "uk_replay_source_carried_structured_tail_substitution_recovered",
        "uk_replay_source_carried_structured_tail_substitution_recovered",
    ]
    assert adjudications[0].detail["source_anchor"] == "more than"
    assert adjudications[0].detail["trim_selector"] == "TEXT_FROM_more than_TO_END"
    assert adjudications[0].detail["trim_mode"] == "from_quoted_text_to_end"
    assert adjudications[0].detail["parent_tail_trimmed"] is True
    assert adjudications[1].detail["parent_tail_trimmed"] is False


def test_executor_classifies_repeated_form_label_payload_shape_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    payload = IRNode(
        kind=IRNodeKind.SCHEDULE,
        label="5A",
        attrs={"eId": "schedule-5a"},
        children=(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="4",
                attrs={"eId": "schedule-5a-paragraph-4"},
                children=(
                    IRNode(kind=IRNodeKind.ITEM, label="a", text="First field."),
                    IRNode(kind=IRNodeKind.ITEM, label="b", text="Second field."),
                    IRNode(kind=IRNodeKind.ITEM, label="a", text="Repeated first field."),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="uk_test_repeated_form_label_payload",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("schedule", "5A"),)),
        payload=payload,
        source=_source(),
    )

    replay_uk_ops(_base_statute(), [op], adjudications_out=adjudications)

    assert {adjudication.kind for adjudication in adjudications} == {
        "uk_replay_repeated_form_label_payload_shape_gap"
    }
    assert len(adjudications) == 2
    assert adjudications[0].detail["target"] == "schedule:5A"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert "duplicate item:a" in adjudications[0].detail["payload_violations"]


def test_executor_records_schedule_unlabeled_paragraph_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="",
                        attrs={"eId": "schedule-1-paragraph"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBPARAGRAPH,
                                label="1",
                                text="Unlabeled paragraph descendant.",
                                attrs={"eId": "schedule-1-paragraph-1"},
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_unlabeled_paragraph_target_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("paragraph", "1"), ("subparagraph", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Unlabeled", occurrence=0),
                replacement="Changed",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_unlabeled_paragraph_target_gap"
    assert adjudications[0].detail["target"] == "schedule:1/paragraph:1/subparagraph:2"
    assert statute.supplements[0].children[0].children[0].text == "Unlabeled paragraph descendant."


def test_executor_records_annex_schedule_reference_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(_base_statute(), adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_annex_schedule_reference_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old", occurrence=0),
                replacement="new",
            ),
            source=_source(),
            provenance_tags=("original_ref: Annex 1",),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_annex_schedule_reference_gap"
    assert adjudications[0].detail["target"] == "schedule:1"


def test_executor_records_schedule_container_text_target_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/11",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="2",
                        text="Existing paragraph.",
                        attrs={"eId": "schedule-1-paragraph-2"},
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_schedule_container_text_target_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "1"), ("part", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Existing", occurrence=0),
                replacement="Changed",
            ),
            source=_source(),
            provenance_tags=("original_ref: paragraph 2 of schedule 1",),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_schedule_container_text_target_gap"
    assert adjudications[0].detail["target"] == "schedule:1/part:2"
    assert statute.supplements[0].children[0].text == "Existing paragraph."


def test_executor_records_heading_text_preimage_gap_without_mutating_heading_carrier() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/7",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    label=None,
                    text="Appointment of Chief Investigating Officer and staff",
                    attrs={"eId": "section-9-p1group"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="9",
                            text="The Commission may appoint staff.",
                            attrs={"eId": "section-9"},
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_heading_text_preimage_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "9"),), special=FacetKind.HEADING),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="Public Standards Commissioner for Scotland", occurrence=0),
                replacement="Commissioner for Ethical Standards in Public Life in Scotland",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_heading_text_preimage_gap"
    assert adjudications[0].detail["target"] == "section:9/heading"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "heading_preimage_absent"
    assert statute.body.children[0].text == "Appointment of Chief Investigating Officer and staff"


def test_executor_records_text_insert_anchor_preimage_gap_without_inserting_by_guess() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="14",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="5",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text=(
                                        "in relation to an authorisation granted on the application "
                                        "of a member of the Scottish Crime Squad"
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_insert_anchor_preimage_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "14"), ("subsection", "5"), ("paragraph", "b"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="General", occurrence=0),
                replacement="General or the Deputy Director General ",
            ),
            source=_source(),
        )
    )

    paragraph = statute.body.children[0].children[0].children[0]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_insert_anchor_preimage_gap"
    assert adjudications[0].detail["target"] == "section:14/subsection:5/paragraph:b"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "insert_anchor_preimage_absent"
    assert paragraph.text == (
        "in relation to an authorisation granted on the application "
        "of a member of the Scottish Crime Squad"
    )


def test_executor_records_monetary_amount_preimage_gap_without_substituting_amount() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="The maximum amount is not yet represented in this replay surface.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_monetary_amount_preimage_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "4"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="\u00a3626,571,000", occurrence=0),
                replacement="\u00a3626,568,000",
            ),
            source=_source(),
        )
    )

    subsection = statute.body.children[0].children[0]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_monetary_amount_preimage_gap"
    assert adjudications[0].detail["target"] == "section:4/subsection:1"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "monetary_amount_preimage_absent"
    assert subsection.text == "The maximum amount is not yet represented in this replay surface."


def test_executor_records_parenthetical_omission_preimage_gap_without_deleting_parent() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="9",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Keeper receipts are paid into the Scottish Consolidated Fund.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_parenthetical_omission_preimage_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "9"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="(other than payments of stamp duty land tax)", occurrence=0),
                replacement=None,
            ),
            source=_source(),
        )
    )

    subsection = statute.body.children[0].children[0]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_parenthetical_omission_preimage_gap"
    assert adjudications[0].detail["target"] == "section:9/subsection:1"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "parenthetical_omission_preimage_absent"
    assert subsection.text == "Keeper receipts are paid into the Scottish Consolidated Fund."


def test_executor_records_citation_connector_surface_gap_without_fuzzy_replace() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="39",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text=(
                                        "operated a local service in contravention of that "
                                        "section section 8(4) 22(1)(b) (2) of this Act;"
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_citation_connector_surface_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "39"), ("subsection", "1"), ("paragraph", "b"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="8(4) or 22(1)(b) or (2)", occurrence=0),
                replacement="3F(1) or 13B(1)(b) or (3)",
            ),
            source=_source(),
        )
    )

    paragraph = statute.body.children[0].children[0].children[0]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_citation_connector_surface_gap"
    assert adjudications[0].detail["target"] == "section:39/subsection:1/paragraph:b"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "citation_connector_surface_gap"
    assert "3F(1)" not in paragraph.text


def test_executor_records_article_phrase_surface_gap_without_fuzzy_replace() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="57",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text=(
                                        "a medical practitioner approved for the purposes "
                                        "of section 20 of the 1984 Act"
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_article_phrase_surface_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "57"), ("subsection", "3"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="an approved", occurrence=0),
                replacement="a relevant",
            ),
            source=_source(),
        )
    )

    paragraph = statute.body.children[0].children[0].children[0]
    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_match_article_phrase_surface_gap"
    assert adjudications[0].detail["target"] == "section:57/subsection:3/paragraph:a"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "article_phrase_content_word_surface_gap"
    assert "a relevant" not in paragraph.text


def test_executor_applies_definition_entry_repeal_without_phrase_deletion() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cquality contract\u201d means a contract scheme; "
                                "\u201cquality partnership scheme\u201d means a quality partnership scheme "
                                "or a quality contract scheme;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "48"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_quality contract", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cquality partnership scheme\u201d means a quality partnership scheme "
        "or a quality contract scheme;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_definition_entry_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_entry_selector"


def test_executor_applies_definition_entry_repeal_with_qualifier_before_predicate() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="31",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cpolice force\u201d means a force; "
                                "\u201cpolice member\u201d, in relation to the Agency, "
                                "means a person appointed as such a member; "
                                "\u201cprivate vehicle\u201d means a vehicle;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_qualifier_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "31"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_police member", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cpolice force\u201d means a force; \u201cprivate vehicle\u201d means a vehicle;"
    )
    assert [row.kind for row in adjudications] == [
        "uk_replay_definition_entry_qualifier_phrase_normalized",
        "uk_replay_definition_entry_text_rewrite_applied",
    ]
    assert adjudications[0].detail["family"] == "definition_entry_predicate_recovery"
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[1].detail["family"] == "text_rewrite_recovery"
    assert adjudications[1].detail["strict_disposition"] == "record"


def test_executor_applies_definition_entry_repeal_with_orphan_separator() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="31",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cPolice Service\u201d means the Police Service of Scotland; , "
                                "\u201c police member \u201d, in relation to the Agency, "
                                "means a person appointed as such a member; "
                                "\u201cprivate vehicle\u201d means a vehicle;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_orphan_separator_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "31"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_police member", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cPolice Service\u201d means the Police Service of Scotland; "
        "\u201cprivate vehicle\u201d means a vehicle;"
    )
    assert [row.kind for row in adjudications] == [
        "uk_replay_definition_entry_qualifier_phrase_normalized",
        "uk_replay_definition_entry_orphan_separator_normalized",
        "uk_replay_definition_entry_text_rewrite_applied",
    ]
    assert adjudications[1].detail["family"] == "definition_entry_separator_recovery"
    assert adjudications[1].detail["strict_disposition"] == "record"
    assert adjudications[2].detail["family"] == "text_rewrite_recovery"
    assert adjudications[2].detail["strict_disposition"] == "record"


def test_executor_does_not_treat_plain_comma_as_definition_entry_separator() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="31",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "This subsection mentions, \u201cpolice member\u201d, "
                                "in relation to the Agency, means something in a note."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_plain_comma_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "31"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_police member", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "This subsection mentions, \u201cpolice member\u201d, "
        "in relation to the Agency, means something in a note."
    )
    assert [row.kind for row in adjudications] == ["uk_replay_definition_entry_shape_gap"]


def test_executor_records_absent_definition_entry_repeal_without_shape_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/13",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="28",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cthe 2001 Act\u201d means the International Criminal Court Act 2001; "
                                "\u201cUnited Kingdom national\u201d means a person of a described kind;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_already_absent",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "28"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_United Kingdom resident", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cthe 2001 Act\u201d means the International Criminal Court Act 2001; "
        "\u201cUnited Kingdom national\u201d means a person of a described kind;"
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_definition_entry_already_absent_observed"
    ]
    assert classify_uk_replay_adjudication_bucket(adjudications[0].kind) == "nonblocking_observation"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_executor_applies_bilingual_definition_entry_repeal_without_phrase_deletion() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asc/2023/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="45",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\n  In this Part\u2014\n  \u201cthe Public Contracts Regulations\u201d "
                                "(\u201cy Rheoliadau Contractau Cyhoeddus\u201d) "
                                "means the Public Contracts Regulations 2015 (S.I. 2015/102); "
                                "\u201cworks\u201d means paragraph 2 of regulation 2(1) "
                                "of the Public Contracts Regulations;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_bilingual_definition_entry_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "45"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text="TEXT_DEFINITION_ENTRY_the Public Contracts Regulations",
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "In this Part\u2014 \u201cworks\u201d means paragraph 2 of regulation 2(1) "
        "of the Public Contracts Regulations;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_definition_entry_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_entry_selector"


def test_executor_applies_definition_entry_substitution_without_phrase_deletion() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2021/3",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="42",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text=(
                                "\u201cmedical devices provision\u201d means an old meaning; "
                                "\u201crelevant provision\u201d means another meaning;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "42"), ("subsection", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_medical devices provision", occurrence=0),
                replacement=(
                    "\u201cmedical devices provision\u201d, in Chapter 1, "
                    "has the meaning given by section 17(2);"
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cmedical devices provision\u201d, in Chapter 1, "
        "has the meaning given by section 17(2); "
        "\u201crelevant provision\u201d means another meaning;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_definition_entry_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_entry_selector"


def test_executor_applies_definition_entry_repeal_with_shall_be_construed_predicate() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201coperational date\u201d shall be construed in accordance "
                                "with section 14(1) of this Act; "
                                "\u201ctraffic commissioner\u201d means the commissioner for Scotland;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_repeal_shall_construed",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "48"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_operational date", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201ctraffic commissioner\u201d means the commissioner for Scotland;"
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_definition_predicate_shall_construed_normalized",
        "uk_replay_definition_entry_text_rewrite_applied",
    ]
    assert adjudications[0].detail["family"] == "definition_entry_predicate_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[1].detail["family"] == "text_rewrite_recovery"
    assert adjudications[1].detail["strict_disposition"] == "record"


def test_executor_applies_in_definition_after_anchor_insert_without_global_rewrite() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2024/21",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="17",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text=(
                                "\u201centitled to practise\u201d means entitled under a 2007 scheme; "
                                "\u201cqualified lawyer\u201d means a person authorised by the 2007 Act;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_in_definition_after_anchor_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "17"), ("subsection", "6"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_IN_DEFINITION_qualified lawyer\x1fAFTER\x1f2007",
                    occurrence=0,
                ),
                replacement="2007 or a person who is a registered foreign lawyer",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201centitled to practise\u201d means entitled under a 2007 scheme; "
        "\u201cqualified lawyer\u201d means a person authorised by the 2007 "
        "or a person who is a registered foreign lawyer Act;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_after_anchor_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_after_anchor_selector"


def test_executor_applies_in_definition_range_to_end_without_global_rewrite() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="87",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cadult\u201d means a person who has attained the age of 16 years; "
                                "\u201cmental disorder\u201d means mental illness, personality disorder "
                                "or learning disability; "
                                "\u201cnearest relative\u201d means the nearest relative within the meaning "
                                "of section 1;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_range_to_end_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "87"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_IN_DEFINITION_mental disorder\x1fFROM\x1fmeans\x1fTO_END",
                    occurrence=0,
                ),
                replacement="has the meaning given by section 328 of the 2003 Act",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cadult\u201d means a person who has attained the age of 16 years; "
        "\u201cmental disorder\u201d has the meaning given by section 328 of the 2003 Act; "
        "\u201cnearest relative\u201d means the nearest relative within the meaning of section 1;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_range_to_end_text_rewrite_applied"
    ]
    assert (
        adjudications[0].detail["text_match"]
        == "TEXT_IN_DEFINITION_mental disorder\x1fFROM\x1fmeans\x1fTO_END"
    )
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_range_to_end_selector"


def test_executor_applies_in_definition_bounded_range_with_observation() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201clocal transport strategy\u201d means a strategy in the Transport Act "
                                "for passenger services; "
                                "\u201ctraffic authority\u201d means the authority for the road;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_bounded_range_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_IN_DEFINITION_local transport strategy\x1fFROM\x1fin\x1fTO\x1fAct",
                    occurrence=1,
                ),
                replacement="under current law",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201clocal transport strategy\u201d means a strategy under current law for passenger "
        "services; \u201ctraffic authority\u201d means the authority for the road;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_range_text_rewrite_applied"
    ]
    assert (
        adjudications[0].detail["text_match"]
        == "TEXT_IN_DEFINITION_local transport strategy\x1fFROM\x1fin\x1fTO\x1fAct"
    )
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_range_selector"


def test_executor_applies_after_definition_insert_to_unique_definition_text_child() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="87",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="In this Act, unless the context otherwise requires-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="\u201cadult\u201d means a person who has attained the age of 16 years",
                                ),
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text=(
                                        "\u201cmental disorder\u201d has the meaning given by "
                                        "section 328 of the 2003 Act; but an adult is not "
                                        "treated as suffering from mental disorder by reason only "
                                        "of conduct"
                                    ),
                                ),
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="\u201cnearest relative\u201d means a relative",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_after_definition_child_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "87"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_DEFINITION_mental disorder", occurrence=0),
                replacement=(
                    "\u201cmental health officer\u201d has the meaning given by "
                    "section 329 of the 2003 Act;"
                ),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "In this Act, unless the context otherwise requires-"
    assert len(subsection.children) == 3
    assert subsection.children[1].text == (
        "\u201cmental disorder\u201d has the meaning given by section 328 of the 2003 Act; "
        "but an adult is not treated as suffering from mental disorder by reason only of conduct "
        "\u201cmental health officer\u201d has the meaning given by section 329 of the 2003 Act;"
    )
    assert subsection.children[2].text == "\u201cnearest relative\u201d means a relative"
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_after_definition_text_insert_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "after_definition_text_insert_selector"


def test_executor_records_definition_anchor_education_lexical_variant_recovery() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/6",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="58",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cannual statement of education improvement objectives\u201d "
                                "has the meaning given by section 5(2); "
                                "\u201cland\u201d includes buildings."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_anchor_education_variant",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "58"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_AFTER_DEFINITION_annual statement of educational "
                        "improvement objectives"
                    ),
                    occurrence=0,
                ),
                replacement=(
                    "\u201cenforcement direction\u201d means a direction under "
                    "section 10C(1);"
                ),
            ),
            source=OperationSource(statute_id="asp/2004/12"),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cannual statement of education improvement objectives\u201d "
        "has the meaning given by section 5(2); "
        "\u201cenforcement direction\u201d means a direction under section 10C(1); "
        "\u201cland\u201d includes buildings."
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_definition_anchor_lexical_variant_recovered",
        "uk_replay_after_definition_text_insert_applied",
    ]
    assert adjudications[0].detail["family"] == "target_resolution_recovery"
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[1].detail["family"] == "text_rewrite_recovery"
    assert adjudications[1].detail["strict_disposition"] == "record"


def test_executor_applies_after_definition_insert_for_conjoined_qualified_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="31",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cdirected\u201d and \u201cintrusive\u201d, in relation to surveillance, "
                                "shall be construed in accordance with section 1; "
                                "\u201cordinary Surveillance Commissioner\u201d means a Commissioner;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_after_definition_conjoined_qualified_anchor",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "31"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_DEFINITION_intrusive", occurrence=0),
                replacement=(
                    "\u201cjoint surveillance operation\u201d means a case involving two forces;"
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cdirected\u201d and \u201cintrusive\u201d, in relation to surveillance, "
        "shall be construed in accordance with section 1; "
        "\u201cjoint surveillance operation\u201d means a case involving two forces; "
        "\u201cordinary Surveillance Commissioner\u201d means a Commissioner;"
    )
    assert [row.kind for row in adjudications] == [
        "uk_replay_definition_anchor_qualifier_phrase_normalized",
        "uk_replay_definition_anchor_conjoined_term_normalized",
        "uk_replay_after_definition_text_insert_applied",
    ]
    assert adjudications[0].detail["family"] == "target_resolution_recovery"
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[1].detail["family"] == "target_resolution_recovery"
    assert adjudications[1].detail["strict_disposition"] == "record"
    assert adjudications[2].detail["family"] == "text_rewrite_recovery"
    assert adjudications[2].detail["strict_disposition"] == "record"


def test_executor_does_not_apply_after_definition_insert_for_unbounded_conjoined_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="31",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "The words directed and intrusive appear in prose; "
                                "\u201cordinary Surveillance Commissioner\u201d means a Commissioner;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_after_definition_unbounded_conjoined_anchor",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "31"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_DEFINITION_intrusive", occurrence=0),
                replacement="\u201cjoint surveillance operation\u201d means a case;",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "The words directed and intrusive appear in prose; "
        "\u201cordinary Surveillance Commissioner\u201d means a Commissioner;"
    )
    assert [row.kind for row in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]


def test_executor_normalizes_space_before_nested_quote_in_text_match() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="50",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            text=(
                                "The Commission shall nominate a medical practitioner "
                                "(the\u201cnominated medical practitioner\u201d) from the list."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_nested_quote_spacing_text_match",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "50"), ("subsection", "4"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "a medical practitioner (the \u201cnominated medical practitioner\u201d)"
                    ),
                    occurrence=0,
                ),
                replacement="a practitioner (the \u201cnominated practitioner\u201d)",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "The Commission shall nominate a practitioner (the \u201cnominated practitioner\u201d) "
        "from the list."
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_text_match_punctuation_space_normalized"
    ]
    assert adjudications[0].detail["strict_disposition"] == "record"


def test_replay_prepare_blocks_same_source_ordinal_patch_overlapping_broader_selector() -> None:
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Adults with Incapacity (Scotland) Act 2000",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="50",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="4",
                            text=(
                                "Where the medical practitioner primarily responsible for the "
                                "medical treatment of the adult has nominated a medical "
                                "practitioner (the \u201cnominated medical practitioner\u201d) from the list."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    target = LegalAddress(path=(("section", "50"), ("subsection", "4")))
    source = OperationSource(statute_id="asp/2005/13", effective="2005-12-19")
    ops = [
        LegalOperation(
            op_id="uk_test_overlap_broad_first",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "medical practitioner primarily responsible for the medical treatment "
                        "of the adult"
                    ),
                    occurrence=0,
                ),
                replacement="person who issued the certificate",
            ),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_overlap_short_ordinal",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="medical practitioner", occurrence=2),
                replacement="person who issued the certificate",
            ),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_overlap_broader_nested",
            sequence=3,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="a medical practitioner (the \u201cnominated medical practitioner\u201d)",
                    occurrence=0,
                ),
                replacement="a practitioner (the \u201cnominated practitioner\u201d)",
            ),
            source=source,
        ),
    ]
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(statute, ops, adjudications_out=adjudications)

    text = replayed.body.children[0].children[0].text
    assert "person who issued the certificate" in text
    assert "a practitioner (the \u201cnominated practitioner\u201d)" in text
    assert "nominated medical practitioner" not in text
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_same_source_text_patch_overlap_blocked"
    ]
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_replay_prepare_allows_same_source_disjoint_ordinal_patch() -> None:
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Transport (Scotland) Act 2001",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="47",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "Alpha quality partnership scheme, beta quality contract scheme "
                                "gamma ticketing scheme delta scheme epsilon scheme."
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    target = LegalAddress(path=(("section", "47"), ("subsection", "1")))
    source = OperationSource(statute_id="asp/2019/17", effective="2023-12-04")
    ops = [
        LegalOperation(
            op_id="uk_test_disjoint_broad_first",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="quality partnership scheme,", occurrence=0),
                replacement="partnership scheme",
            ),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_disjoint_broad_second",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="quality contract scheme", occurrence=0),
                replacement="franchising framework",
            ),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_disjoint_ordinal",
            sequence=3,
            action=StructuralAction.TEXT_REPLACE,
            target=target,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="scheme", occurrence=4),
                replacement="scheme or framework",
            ),
            source=source,
        ),
    ]
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(statute, ops, adjudications_out=adjudications)

    text = replayed.body.children[0].children[0].text
    assert "partnership scheme" in text
    assert "franchising framework" in text
    assert "delta scheme or framework" in text
    assert "epsilon scheme." in text
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_same_source_text_patch_overlap_disjoint"
    ]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["ordered_before_op_ids"] == (
        "uk_test_disjoint_broad_first",
        "uk_test_disjoint_broad_second",
    )


def test_replay_prepare_preserves_duplicate_effect_id_structural_ops_with_text_before_edges() -> None:
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Adults with Incapacity (Scotland) Act 2000",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="47",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="alpha ticketing scheme beta quality partnership scheme gamma scheme",
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="87",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="two"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="three"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    source = OperationSource(statute_id="asp/2003/13", effective="2005-10-05")
    target_text = LegalAddress(path=(("section", "47"), ("subsection", "1")))
    ops = [
        LegalOperation(
            op_id="uk_test_disjoint_ordinal",
            sequence=0,
            action=StructuralAction.TEXT_REPLACE,
            target=target_text,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="scheme", occurrence=1),
                replacement="scheme or framework",
            ),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_broad_scheme",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=target_text,
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="quality partnership scheme", occurrence=0),
                replacement="partnership scheme",
            ),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_same_effect",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "87"), ("subsection", "2"))),
            source=source,
        ),
        LegalOperation(
            op_id="uk_test_same_effect",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "87"), ("subsection", "3"))),
            source=source,
        ),
    ]

    prepared = _prepare_replay_uk_ops(ops, base_ir=statute)

    assert [str(op.target) for op in prepared.accepted_ops] == [
        "section:47/subsection:1",
        "section:47/subsection:1",
        "section:87/subsection:2",
        "section:87/subsection:3",
    ]

    adjudications: list[CompileAdjudication] = []
    replayed = replay_uk_ops(statute, ops, adjudications_out=adjudications)

    section_87 = replayed.body.children[1]
    assert section_87.kind is IRNodeKind.SECTION
    assert section_87.children == ()
    assert "uk_replay_same_source_text_patch_overlap_disjoint" in {
        adjudication.kind for adjudication in adjudications
    }
    assert "uk_replay_repealed_target_gap" not in {adjudication.kind for adjudication in adjudications}


def test_executor_observes_descendant_repeal_after_prior_parent_repeal() -> None:
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Adults with Incapacity (Scotland) Act 2000",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="38",
                    text="",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="one"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="four"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    ops = [
        LegalOperation(
            op_id="uk_test_repeal_section_38",
            sequence=0,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "38"),)),
            source=OperationSource(statute_id="asp/2001/8"),
        ),
        LegalOperation(
            op_id="uk_test_repeal_section_38_4",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "38"), ("subsection", "4"))),
            source=OperationSource(statute_id="asp/2003/13"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []

    replayed = replay_uk_ops(statute, ops, adjudications_out=adjudications)

    assert replayed.body.children == ()
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_repeal_target_already_absent_observed"
    ]
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["reason_code"] == "target_previously_repealed"


def test_executor_applies_before_definition_insert_at_explicit_definition_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2024/21",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="17",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text=(
                                "\u201centitled to practise\u201d means authorised; "
                                "\u201cqualified lawyer\u201d means a lawyer;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_before_definition_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "17"), ("subsection", "6"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_BEFORE_DEFINITION_entitled to practise",
                    occurrence=0,
                ),
                replacement=(
                    "\u201cCriminal Injuries Compensation Scheme\u201d means a scheme;"
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cCriminal Injuries Compensation Scheme\u201d means a scheme; "
        "\u201centitled to practise\u201d means authorised; "
        "\u201cqualified lawyer\u201d means a lawyer;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_before_definition_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == "TEXT_BEFORE_DEFINITION_entitled to practise"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "flat_definition_text_selector"


def test_executor_applies_before_definition_insert_when_term_has_comma_qualifier() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2024/21",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="17",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text=(
                                "In this section— "
                                "\u201centitled to practise\u201d, in relation to a regulated profession, "
                                "is to be read in accordance with section 19(2); "
                                "\u201cqualified lawyer\u201d means a lawyer;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_before_definition_insert_comma_qualifier",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "17"), ("subsection", "6"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_BEFORE_DEFINITION_entitled to practise",
                    occurrence=0,
                ),
                replacement=(
                    "\u201cCriminal Injuries Compensation Scheme\u201d means a scheme;"
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "In this section— "
        "\u201cCriminal Injuries Compensation Scheme\u201d means a scheme; "
        "\u201centitled to practise\u201d, in relation to a regulated profession, "
        "is to be read in accordance with section 19(2); "
        "\u201cqualified lawyer\u201d means a lawyer;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_before_definition_text_rewrite_applied"
    ]


def test_executor_blocks_before_definition_insert_when_target_has_children() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2024/21",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="17",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text="Definitions.",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="\u201centitled to practise\u201d means authorised;",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_before_definition_insert_with_children",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "17"), ("subsection", "6"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_BEFORE_DEFINITION_entitled to practise",
                    occurrence=0,
                ),
                replacement="\u201cCriminal Injuries Compensation Scheme\u201d means a scheme;",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "Definitions."
    assert [child.label for child in subsection.children] == ["a"]
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["text_match"] == "TEXT_BEFORE_DEFINITION_entitled to practise"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_executor_applies_after_definition_insert_to_comma_separated_definition_list() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="23",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "In this Act, unless the context otherwise requires— "
                                "“action” includes failure to act, "
                                "“the Ombudsman” means the Scottish Public Services Ombudsman, "
                                "“request” means a request for a review,"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_after_definition_insert_comma_list",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "23"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_AFTER_DEFINITION_the Ombudsman",
                    occurrence=0,
                ),
                replacement=(
                    "\u201c the Ombudsman's functions \u201d includes the Ombudsman's functions "
                    "under the 2015 Act,"
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "In this Act, unless the context otherwise requires— "
        "“action” includes failure to act, "
        "“the Ombudsman” means the Scottish Public Services Ombudsman, "
        "“ the Ombudsman's functions ” includes the Ombudsman's functions under the 2015 Act, "
        "“request” means a request for a review,"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_after_definition_text_insert_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "after_definition_text_insert_selector"


def test_executor_applies_at_end_definition_insert_to_named_definition_only() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/11",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="23",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "In this Act— "
                                "“person aggrieved” means a person who has made a complaint, "
                                "“request” means a request for a review,"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_in_definition_at_end_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "23"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_IN_DEFINITION_person aggrieved\x1fAT_END",
                    occurrence=0,
                ),
                replacement="or (as the case may be) section 6A(5)",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "In this Act— "
        "“person aggrieved” means a person who has made a complaint "
        "or (as the case may be) section 6A(5), "
        "“request” means a request for a review,"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_at_end_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == "TEXT_IN_DEFINITION_person aggrieved\x1fAT_END"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_at_end_selector"


def test_executor_applies_definition_child_repeal_without_bare_term_deletion() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/12",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="42",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text=(
                                "\u201crelevant provision\u201d means section 39(1); "
                                "section 40(1); section 41(1); section 42(1); "
                                "\u201cother provision\u201d means paragraph (d);"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "42"), ("subsection", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text="TEXT_DEFINITION_CHILD_PARAGRAPH_relevant provision\x1fd",
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201crelevant provision\u201d means section 39(1); "
        "section 40(1); section 41(1); "
        "\u201cother provision\u201d means paragraph (d);"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_definition_child_flat_ordinal_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "flat_definition_child_ordinal_selector"


def test_executor_applies_definition_child_repeal_to_preserved_ordered_list_child() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/12",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="42",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="\u201crelevant provision\u201d means-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="section 13(2),",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "relevant provision",
                                        "definition_child_label": "a",
                                    },
                                ),
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="paragraph 1(3) or 18(1) of Schedule 11.",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "relevant provision",
                                        "definition_child_label": "d",
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_repeal_preserved_ordered_list",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "42"), ("subsection", "2"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text="TEXT_DEFINITION_CHILD_PARAGRAPH_relevant provision\x1fd",
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert [child.attrs["definition_child_label"] for child in subsection.children] == ["a"]
    assert subsection.text == "\u201crelevant provision\u201d means-"
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_definition_child_structured_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "structured_definition_child_selector"


def test_executor_applies_definition_child_substitution_inside_definition_entry() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2022/32",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="36",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201creview partner\u201d means a local authority; "
                                "a clinical commissioning group; "
                                "a Health Authority; a person; "
                                "\u201cother\u201d means another value;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "36"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_DEFINITION_CHILD_PARAGRAPH_review partner\x1fc",
                    occurrence=0,
                ),
                replacement="an integrated care board, or",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201creview partner\u201d means a local authority; "
        "a clinical commissioning group; an integrated care board, or; "
        "a person; \u201cother\u201d means another value;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_definition_child_flat_ordinal_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "flat_definition_child_ordinal_selector"


def test_executor_inserts_source_carried_definition_children_after_anchor() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="“local transport authority” means-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="a local authority; or",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "local transport authority",
                                        "definition_child_label": "a",
                                    },
                                ),
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="the Strathclyde Passenger Transport Authority;",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "local transport authority",
                                        "definition_child_label": "b",
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_definition_child_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_AFTER_DEFINITION_PARAGRAPH_local transport authority_AFTER_a",
                    occurrence=0,
                ),
                replacement=(
                    "aa the Shetland Transport Partnership; "
                    "ab the South-West of Scotland Transport Partnership; ,"
                ),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert [child.attrs["definition_child_label"] for child in subsection.children] == [
        "a",
        "aa",
        "ab",
        "b",
    ]
    assert [child.text for child in subsection.children] == [
        "a local authority; or",
        "the Shetland Transport Partnership;",
        "the South-West of Scotland Transport Partnership;",
        "the Strathclyde Passenger Transport Authority;",
    ]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_after_definition_child_structured_insert_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert (
        adjudications[0].detail["source_shape"]
        == "structured_after_definition_child_insert_selector"
    )


def test_executor_inserts_definition_child_and_appends_anchor_connector() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="“local transport authority” means-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="the Strathclyde Passenger Transport Authority",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "local transport authority",
                                        "definition_child_label": "b",
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_definition_child_insert_suffix",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text="TEXT_AFTER_DEFINITION_PARAGRAPH_local transport authority_AFTER_b",
                    occurrence=0,
                ),
                replacement="; or c the West of Scotland Transport Partnership; .",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert [child.attrs["definition_child_label"] for child in subsection.children] == [
        "b",
        "c",
    ]
    assert [child.text for child in subsection.children] == [
        "the Strathclyde Passenger Transport Authority ; or",
        "the West of Scotland Transport Partnership;",
    ]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_after_definition_child_structured_insert_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert (
        adjudications[0].detail["source_shape"]
        == "structured_after_definition_child_insert_selector"
    )


def test_executor_scopes_source_carried_anchor_insert_to_definition_entry() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "“local authority” means a council; "
                                "“local transport strategy” means the strategy prepared by authority;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_scoped_definition_anchor_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_local transport strategy"
                        f"{US}AFTER{US}"
                        "authority"
                    ),
                    occurrence=0,
                ),
                replacement="authority; or b a local traffic authority,",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == (
        "“local authority” means a council; “local transport strategy” means "
        "the strategy prepared by authority; or b a local traffic authority,;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_after_anchor_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_after_anchor_selector"


def test_executor_applies_definition_scoped_from_to_range() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "In this Act— “charging scheme” means a scheme made under this Act; "
                                "“local transport strategy” means the strategy prepared by authority "
                                "in accordance with section 79 of this Act; "
                                "“local authority” means a council;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_scoped_from_to_range",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_local transport strategy"
                        f"{US}FROM{US}"
                        f"in{US}TO{US}Act"
                    ),
                    occurrence=1,
                ),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == (
        "In this Act— “charging scheme” means a scheme made under this Act; "
        "“local transport strategy” means the strategy prepared by authority ; "
        "“local authority” means a council;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_range_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "definition_range_selector"


def test_executor_applies_definition_child_scoped_word_omission() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="“local transport authority” means-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="a local authority; or",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "local transport authority",
                                        "definition_child_label": "a",
                                    },
                                ),
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="the Strathclyde Passenger Transport Authority;",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "local transport authority",
                                        "definition_child_label": "b",
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_scoped_word_omission",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_CHILD_PARAGRAPH_local transport authority"
                        f"{US}a{US}"
                        "or"
                    ),
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert [child.text for child in subsection.children] == [
        "a local authority;",
        "the Strathclyde Passenger Transport Authority;",
    ]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_child_structured_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "structured_in_definition_child_selector"


def test_executor_applies_definition_child_scoped_word_omission_to_flat_entry() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="82",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "“local transport authority” means a local authority; "
                                "or the Strathclyde Passenger Transport Authority;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_scoped_word_omission_flat",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "82"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_CHILD_PARAGRAPH_local transport authority"
                        f"{US}a{US}"
                        "or"
                    ),
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == (
        "“local transport authority” means a local authority; the Strathclyde "
        "Passenger Transport Authority;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "flat_in_definition_child_ordinal_selector"


def test_executor_applies_definition_child_scoped_after_insert_to_structured_entry() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="“relevant general policies” means-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="where the authority is a local authority, policies;",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "relevant general policies",
                                        "definition_child_label": "a",
                                    },
                                ),
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text="where the authority is another body, policies;",
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "relevant general policies",
                                        "definition_child_label": "b",
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_scoped_after_insert_structured",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_CHILD_PARAGRAPH_relevant general policies"
                        f"{US}a{US}AFTER{US}"
                        "authority"
                    ),
                    occurrence=2,
                ),
                replacement="authority (i) ",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert [child.text for child in subsection.children] == [
        "where the authority is a local authority (i) , policies;",
        "where the authority is another body, policies;",
    ]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_child_structured_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "structured_in_definition_child_selector"


def test_executor_applies_definition_child_scoped_at_end_insert_to_structured_entry() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="“relevant general policies” means-",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.ITEM,
                                    label=None,
                                    text=(
                                        "where the authority is a local authority (i), "
                                        "policies under section 63;"
                                    ),
                                    attrs={
                                        "source_rule_id": "uk_definition_ordered_list_child_preserved",
                                        "definition_term": "relevant general policies",
                                        "definition_child_label": "a",
                                    },
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_scoped_at_end_insert_structured",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_CHILD_PARAGRAPH_relevant general policies"
                        f"{US}a{US}"
                        "AT_END"
                    ),
                    occurrence=0,
                ),
                replacement="; or ii policies which relate to matters;",
            ),
            source=_source(),
        )
    )

    child = executor.statute.body.children[0].children[0].children[0]
    assert child.text == (
        "where the authority is a local authority (i), policies under section 63; "
        "or ii policies which relate to matters;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_child_structured_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "structured_in_definition_child_selector"


def test_executor_applies_definition_child_scoped_after_insert_to_flat_entry() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "“relevant general policies” means where the authority is a local "
                                "authority, policies; where the authority is another body, policies;"
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text="Unrelated authority text must not be selected.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_child_scoped_after_insert_flat",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "48"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(
                    match_text=(
                        "TEXT_IN_DEFINITION_CHILD_PARAGRAPH_relevant general policies"
                        f"{US}a{US}AFTER{US}"
                        "authority"
                    ),
                    occurrence=2,
                ),
                replacement="authority (i) ",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == (
        "“relevant general policies” means where the authority is a local "
        "authority (i) , policies; where the authority is another body, policies;"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied"
    ]
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "flat_in_definition_child_ordinal_selector"


def test_executor_deletes_source_carried_child_tail_from_collapsed_parent_text() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="21",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="5",
                            text=(
                                "For the purposes of subsection (3)(b) a person "
                                "is qualified if that person is— and “EEA State” "
                                "means a Contracting Party."
                            ),
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="eligible for appointment as an auditor, or",
                                ),
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="b",
                                    text="a member of a body of accountants;",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_child_tail_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "21"), ("subsection", "5"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_TAIL_paragraph_b", occurrence=0),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "For the purposes of subsection (3)(b) a person is qualified if that person is—"
    assert [child.label for child in subsection.children] == ["a", "b"]
    assert [child.text for child in subsection.children] == [
        "eligible for appointment as an auditor, or",
        "a member of a body of accountants;",
    ]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_child_tail_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == "TEXT_AFTER_CHILD_TAIL_paragraph_b"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "source_carried_child_tail_selector"


def test_executor_replaces_source_carried_child_tail_in_collapsed_parent_text() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="224",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="The court may impose a sentence for— and old tail words.",
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="condition a, or"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="condition b;"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_child_tail_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "224"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_TAIL_paragraph_b", occurrence=0),
                replacement="for a term exceeding the applicable limit",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "The court may impose a sentence for— for a term exceeding the applicable limit"
    assert [child.label for child in subsection.children] == ["a", "b"]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_child_tail_text_rewrite_applied"
    ]


def test_executor_replaces_source_carried_child_tail_when_tail_is_not_connector_word() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="224",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "A magistrates' court does not have power to impose— "
                                "for more than 6 months in respect of any one offence"
                            ),
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="imprisonment, or"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="detention;"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_child_tail_substitution_non_connector_tail",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "224"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_TAIL_paragraph_b", occurrence=0),
                replacement="for a term exceeding the applicable limit in respect of any one offence",
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == (
        "A magistrates' court does not have power to impose— "
        "for a term exceeding the applicable limit in respect of any one offence"
    )
    assert [child.label for child in subsection.children] == ["a", "b"]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_child_tail_text_rewrite_applied"
    ]


def test_executor_rejects_child_tail_delete_when_anchor_is_not_last_child() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="21",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="5",
                            text="Opening words— and tail text.",
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="first;"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="second;"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="c", text="third;"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_child_tail_repeal_not_last",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "21"), ("subsection", "5"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_TAIL_paragraph_b", occurrence=0),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "Opening words— and tail text."
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["blocking"] is True


def test_executor_deletes_source_carried_child_list_tail_from_collapsed_parent_text() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/1970/9",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="9",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            text=(
                                "Where a return does not include a self-assessment— "
                                "and references in this Act to a self-assessment include one made by an officer."
                            ),
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="make the assessment; and"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="send a copy of it."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_child_list_tail_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "9"), ("subsection", "3"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_LIST_TAIL_paragraph", occurrence=0),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "Where a return does not include a self-assessment—"
    assert [child.label for child in subsection.children] == ["a", "b"]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_child_list_tail_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == "TEXT_AFTER_CHILD_LIST_TAIL_paragraph"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "source_carried_child_list_tail_selector"


def test_executor_rejects_child_list_tail_delete_when_final_child_is_not_in_list() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/1970/9",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="9",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            text="Opening words— and tail text.",
                            children=(
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="a", text="first;"),
                                IRNode(kind=IRNodeKind.PARAGRAPH, label="b", text="second;"),
                                IRNode(kind=IRNodeKind.SUBPARAGRAPH, label="i", text="not part of the paragraph list."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_child_list_tail_repeal_blocked",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "9"), ("subsection", "3"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_LIST_TAIL_paragraph", occurrence=0),
            ),
            source=_source(),
        )
    )

    subsection = executor.statute.body.children[0].children[0]
    assert subsection.text == "Opening words— and tail text."
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["blocking"] is True


def test_executor_deletes_source_carried_subparagraph_tail_from_collapsed_parent_text() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="9",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="The condition is met if— and the trailing words apply.",
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="i",
                                            text="first condition, or",
                                        ),
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="ii",
                                            text="second condition;",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_subparagraph_tail_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "9"), ("subsection", "3"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_TAIL_subparagraph_2", occurrence=0),
            ),
            source=_source(),
        )
    )

    paragraph = executor.statute.body.children[0].children[0].children[0]
    assert paragraph.text == "The condition is met if—"
    assert [child.label for child in paragraph.children] == ["i", "ii"]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_child_tail_text_rewrite_applied"
    ]


def test_executor_rejects_subparagraph_tail_delete_when_anchor_is_not_last_child() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="9",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.PARAGRAPH,
                                    label="a",
                                    text="Opening words— and tail text.",
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="i",
                                            text="first;",
                                        ),
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="ii",
                                            text="second;",
                                        ),
                                        IRNode(
                                            kind=IRNodeKind.SUBPARAGRAPH,
                                            label="iii",
                                            text="third;",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_subparagraph_tail_repeal_not_last",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "9"), ("subsection", "3"), ("paragraph", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_AFTER_CHILD_TAIL_subparagraph_2", occurrence=0),
            ),
            source=_source(),
        )
    )

    paragraph = executor.statute.body.children[0].children[0].children[0]
    assert paragraph.text == "Opening words— and tail text."
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["blocking"] is True


def test_executor_deletes_source_carried_multi_subunit_text_only_from_named_children() -> None:
    adjudications: list[CompileAdjudication] = []
    omitted = "(in a case where the incapacity of the granter is by reason of mental disorder)"
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="22",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=f"Notify the local authority and {omitted} the Commission.",
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="2",
                            text=f"Notify the granter and {omitted} the Commission.",
                        ),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="3",
                            text=f"Do not touch {omitted} here.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_multi_subunit_repeal",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "22"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text=f"TEXT_IN_CHILDREN_subsection_1_2\x1f{omitted}",
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    section = executor.statute.body.children[0]
    assert [child.text for child in section.children] == [
        "Notify the local authority and  the Commission.",
        "Notify the granter and  the Commission.",
        f"Do not touch {omitted} here.",
    ]
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_source_carried_multi_child_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == f"TEXT_IN_CHILDREN_subsection_1_2\x1f{omitted}"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "source_carried_multi_child_selector"


def test_executor_rejects_multi_subunit_text_delete_when_named_child_missing() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2000/4",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="22",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="mental disorder"),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_source_carried_multi_subunit_missing_child",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "22"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(
                    match_text="TEXT_IN_CHILDREN_subsection_1_2\x1fmental disorder",
                    occurrence=0,
                ),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == "mental disorder"
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["blocking"] is True


def test_executor_rewrites_inserted_payload_of_target_amendment_instruction() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text=""),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="17",
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="a",
                                text=(
                                    "after paragraph (a) insert— aa its chief executive "
                                    "appointed under section 54 of the 2021 Act;"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_amendment_inserted_text_substitution",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "5"), ("paragraph", "17"), ("item", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_AMENDMENT_INSERT_TO_END", occurrence=0),
                replacement=(
                    "aa its chief executive appointed under— i section 54 of the 2021 Act, "
                    "or ii regulations made under Part 5 of that Act"
                ),
            ),
            source=_source(),
        )
    )

    item = executor.statute.supplements[0].children[0].children[0]
    assert item.text == (
        "after paragraph (a) insert— aa its chief executive appointed under— "
        "i section 54 of the 2021 Act, or ii regulations made under Part 5 of that Act"
    )
    assert [finding.kind for finding in adjudications] == [
        "uk_replay_amendment_insert_tail_text_rewrite_applied"
    ]
    assert adjudications[0].detail["text_match"] == "TEXT_AFTER_AMENDMENT_INSERT_TO_END"
    assert adjudications[0].detail["family"] == "text_rewrite_recovery"
    assert adjudications[0].detail["blocking"] is False
    assert adjudications[0].detail["strict_disposition"] == "record"
    assert adjudications[0].detail["source_shape"] == "amendment_instruction_insert_tail_selector"


def test_executor_rejects_inserted_payload_rewrite_without_insert_verb() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asc/2021/1",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text=""),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="17",
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="a",
                                text="after paragraph (a) omit the old words",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_amendment_inserted_text_substitution_no_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "5"), ("paragraph", "17"), ("item", "a"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="TEXT_AFTER_AMENDMENT_INSERT_TO_END", occurrence=0),
                replacement="aa replacement text",
            ),
            source=_source(),
        )
    )

    item = executor.statute.supplements[0].children[0].children[0]
    assert item.text == "after paragraph (a) omit the old words"
    assert [finding.kind for finding in adjudications] == ["uk_replay_text_match_synthetic_selector_gap"]
    assert adjudications[0].detail["blocking"] is True


def test_executor_occurrence_text_replacements_preserve_later_occurrences() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="6",
                            text="retained status, retained law and retained references",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)
    target = LegalAddress(path=(("section", "2"), ("subsection", "6")))

    for sequence, occurrence in ((1, 2), (2, 1)):
        executor.apply_op(
            LegalOperation(
                op_id=f"uk_test_first_second_occurrence_{occurrence}",
                sequence=sequence,
                action=StructuralAction.TEXT_REPLACE,
                target=target,
                text_patch=TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text="retained", occurrence=occurrence),
                    replacement="assimilated",
                ),
                source=_source(),
            )
        )

    assert executor.statute.body.children[0].children[0].text == (
        "assimilated status, assimilated law and retained references"
    )
    assert adjudications == []


def test_executor_zero_occurrence_text_replacement_updates_all_matches() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="138",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "payment in respect of the child and recovery "
                                "in respect of the child"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_all_occurrences_grouped_after_insert",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "138"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="of the child", occurrence=0),
                replacement="of the child or qualifying young person",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "payment in respect of the child or qualifying young person and recovery "
        "in respect of the child or qualifying young person"
    )
    assert adjudications == []


def test_executor_final_occurrence_text_repeal_preserves_earlier_occurrences() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2020/17",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="and first and second and",
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_final_and_omission",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="and", occurrence=-1),
                replacement="",
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].text == "and first and second "
    assert adjudications == []


def test_executor_does_not_definition_entry_repeal_bare_phrase_occurrence() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/2",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="48",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text=(
                                "\u201cquality partnership scheme\u201d means a quality partnership scheme "
                                "or a quality contract scheme;"
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_definition_entry_repeal_negative",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "48"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text="TEXT_DEFINITION_ENTRY_quality contract", occurrence=0),
            ),
            source=_source(),
        )
    )

    assert executor.statute.body.children[0].children[0].text == (
        "\u201cquality partnership scheme\u201d means a quality partnership scheme "
        "or a quality contract scheme;"
    )
    assert [adjudication.kind for adjudication in adjudications] == [
        "uk_replay_definition_entry_shape_gap"
    ]
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"


def test_executor_classifies_broad_schedule_text_miss_without_table_shape() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="Budget table collapsed to schedule title only",
                attrs={"eId": "schedule-1"},
                children=(),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_broad_schedule_table_shape_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="£12,100,000", occurrence=0),
                replacement="£127,870,000",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_broad_schedule_table_shape_gap"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "broad_schedule_without_table_or_provision_structure"


def test_executor_classifies_broad_schedule_part_text_miss_without_table_shape() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2001/4",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="2",
                attrs={"eId": "schedule-2"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PART,
                        label="3",
                        text="Scottish Executive Education Department",
                        attrs={"eId": "schedule-2-part-3"},
                        children=(),
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_broad_schedule_part_table_shape_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "2"), ("part", "3"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="unparsed table amount", occurrence=0),
                replacement="replacement amount",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_broad_schedule_part_table_shape_gap"
    assert adjudications[0].detail["target_granularity"] == "part"
    assert adjudications[0].detail["source_shape"] == "broad_schedule_without_table_or_provision_structure"


def test_executor_records_paragraph_schedule_monetary_amount_preimage_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="asp/2002/7",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=()),
        supplements=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="",
                attrs={"eId": "schedule-1"},
                children=(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label="1",
                        text="A real paragraph body without the requested amount",
                        attrs={"eId": "schedule-1-paragraph-1"},
                    ),
                ),
            ),
        ),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_broad_schedule_paragraph_text_miss",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("schedule", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="£12,100,000", occurrence=0),
                replacement="£127,870,000",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_monetary_amount_preimage_gap"
    assert adjudications[0].detail["target_text_preview"] == "A real paragraph body without the requested amount"
    assert adjudications[0].detail["source_shape"] == "monetary_amount_preimage_absent"
    assert adjudications[0].detail["prior_same_target_text_patch_count"] == 0
    assert adjudications[0].detail["target_container"] == "schedule"
    assert adjudications[0].detail["target_granularity"] == "schedule"


def test_executor_records_text_target_empty_surface_gap() -> None:
    adjudications: list[CompileAdjudication] = []
    statute = IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=""),),
                ),
            ),
        ),
        supplements=(),
    )
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    executor.apply_op(
        LegalOperation(
            op_id="uk_test_text_target_empty_surface_gap",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"), ("subsection", "1"))),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="missing text", occurrence=0),
                replacement="new text",
            ),
            source=_source(),
        )
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_text_target_empty_surface_gap"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["source_shape"] == "target_subtree_without_text_surface"
    assert adjudications[0].detail["target_text_preview"] == ""
    assert adjudications[0].detail["target_text_normalized_preview"] == ""


def test_replay_uk_ops_applies_whole_act_repeal() -> None:
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_whole_act_repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        source=_source(),
    )

    replayed = replay_uk_ops(_base_statute(), [op], adjudications_out=adjudications)

    assert adjudications == []
    assert replayed.body.children == ()
    assert replayed.supplements == ()


def test_replay_uk_ops_records_prepare_filtered_unsupported_whole_act_target() -> None:
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_whole_act_prepare_filter",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        payload=IRNode(kind=IRNodeKind.BODY),
        source=_source(),
    )

    replayed = replay_uk_ops(_base_statute(), [op], adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_unsupported_action"
    assert adjudications[0].op_id == "uk_test_whole_act_prepare_filter"
    assert adjudications[0].detail == {
        "action": "replace",
        "blocking": True,
        "family": "unsupported_or_unresolved_action",
        "phase": "replay",
        "quirks_disposition": "record",
        "reason": "whole_act_prepare_filter",
        "rule_id": "uk_replay_unsupported_action",
        "strict_disposition": "block",
        "target": "/whole_act",
    }
    assert tuple(child.label for child in replayed.body.children) == ("1",)


def test_prepare_replay_uk_ops_preserves_rejected_whole_act_adjudication_without_sink() -> None:
    whole_act_replace = LegalOperation(
        op_id="uk_test_prepare_filter_rejected",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        payload=IRNode(kind=IRNodeKind.BODY),
        source=_source(),
    )
    whole_act_repeal = LegalOperation(
        op_id="uk_test_prepare_filter_repeal",
        sequence=2,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        payload=None,
        source=_source(),
    )
    section_replace = LegalOperation(
        op_id="uk_test_prepare_filter_normal",
        sequence=3,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="new"),
        source=_source(),
    )

    prepared = _prepare_replay_uk_ops([whole_act_replace, whole_act_repeal, section_replace])

    assert prepared.accepted_ops == (whole_act_repeal, section_replace)
    assert len(prepared.rejected_adjudications) == 1
    rejection = prepared.rejected_adjudications[0]
    assert rejection.kind == "uk_replay_unsupported_action"
    assert rejection.op_id == "uk_test_prepare_filter_rejected"
    assert rejection.detail == {
        "action": "replace",
        "blocking": True,
        "family": "unsupported_or_unresolved_action",
        "phase": "replay",
        "quirks_disposition": "record",
        "reason": "whole_act_prepare_filter",
        "rule_id": "uk_replay_unsupported_action",
        "strict_disposition": "block",
        "target": "/whole_act",
    }


def test_pipeline_apply_ops_records_prepare_filtered_unsupported_whole_act_target() -> None:
    adjudications: list[CompileAdjudication] = []
    op = LegalOperation(
        op_id="uk_test_pipeline_whole_act_prepare_filter",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(), special=FacetKind.WHOLE_ACT),
        payload=IRNode(kind=IRNodeKind.BODY),
        source=_source(),
    )

    replayed = UKReplayPipeline(Path(".")).apply_ops(
        _base_statute(),
        [op],
        adjudications_out=adjudications,
    )

    assert len(adjudications) == 1
    assert adjudications[0].kind == "uk_replay_unsupported_action"
    assert adjudications[0].op_id == "uk_test_pipeline_whole_act_prepare_filter"
    assert adjudications[0].source_statute == "ukpga/2026/1"
    assert adjudications[0].detail == {
        "action": "replace",
        "blocking": True,
        "family": "unsupported_or_unresolved_action",
        "phase": "replay",
        "quirks_disposition": "record",
        "reason": "whole_act_prepare_filter",
        "rule_id": "uk_replay_unsupported_action",
        "strict_disposition": "block",
        "target": "/whole_act",
    }
    assert tuple(child.label for child in replayed.body.children) == ("1",)


def test_replay_uk_ops_collects_text_duplication_warnings() -> None:
    adjudications: list[CompileAdjudication] = []

    replay_uk_ops(_duplicate_text_statute(), [], adjudications_out=adjudications)

    duplication_adjudications = [
        adjudication for adjudication in adjudications if adjudication.kind == "text_duplication_warning"
    ]

    assert [adjudication.detail.get("phase") for adjudication in duplication_adjudications] == ["replay_fold"]
    assert duplication_adjudications[0].detail["root"] == "body"
    assert duplication_adjudications[0].detail["blocking"] is False
    assert duplication_adjudications[0].detail["strict_disposition"] == "record"
    assert duplication_adjudications[0].detail["quirks_disposition"] == "record"

    evidence_rows = adjudication_finding_evidence_rows(
        duplication_adjudications,
        frontend_id="uk",
        base_id="ukpga/2000/1",
        as_of="2026-05-12",
    )
    assert evidence_rows[0].blocking is False
    assert evidence_rows[0].strict_disposition == "record"
    assert evidence_rows[0].quirks_disposition == "record"
