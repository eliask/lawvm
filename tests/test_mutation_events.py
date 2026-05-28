from __future__ import annotations

from collections import Counter

from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.mutation_boundary import path_has_prefix
from lawvm.core.mutation_events import (
    DeclaredMutationAllowance,
    MutationEvent,
    MutationEventPathSetReport,
    build_mutation_event_path_set_report,
    mutation_event_allowance_paths_by_kind,
    mutation_event_allowance_rule_ids_by_kind,
    mutation_event_declared_allowance_paths,
    mutation_event_matching_allowance_rule_ids,
    mutation_event_touched_paths,
)

TREE_PATHS = st.lists(
    st.tuples(
        st.sampled_from(("part", "chapter", "section", "subsection", "paragraph", "item")),
        st.text(min_size=0, max_size=3),
    ),
    max_size=3,
).map(tuple)

TREE_PATH_SETS = st.lists(TREE_PATHS, max_size=8).map(tuple)


def test_mutation_event_touched_paths_dedupes_all_path_channels() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="renumber",
        helper="helper",
        outcome="applied",
        consumed_paths=((("section", "1"),),),
        created_paths=((("section", "1"),), (("section", "2"),)),
        renumbered_paths=(((("section", "2"),), (("section", "3"),)),),
    )

    assert mutation_event_touched_paths(event) == (
        (("section", "1"),),
        (("section", "2"),),
        (("section", "3"),),
    )


def test_mutation_event_declared_allowance_paths_dedupes_non_empty_paths() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery",
                paths=((("section", "1"),), (), (("section", "1"),)),
                rule_id="rule",
            ),
        ),
    )

    assert mutation_event_declared_allowance_paths(event) == ((("section", "1"),),)


def test_mutation_event_allowance_paths_and_rule_ids_filter_by_kind() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery",
                paths=((("section", "1"),),),
                rule_id="recover-one",
            ),
            DeclaredMutationAllowance(
                kind="migration_path",
                paths=((("section", "2"),),),
                rule_id="migrate-two",
            ),
            DeclaredMutationAllowance(
                kind="recovery",
                paths=((("section", "1"),),),
                rule_id="recover-one",
            ),
        ),
    )

    assert mutation_event_allowance_paths_by_kind(event, "recovery", "recovery_path") == ((("section", "1"),),)
    assert mutation_event_allowance_rule_ids_by_kind(event, "recovery", "recovery_path") == ("recover-one",)
    assert mutation_event_allowance_paths_by_kind(event, "migration", "migration_path") == ((("section", "2"),),)
    assert mutation_event_allowance_rule_ids_by_kind(event, "migration", "migration_path") == ("migrate-two",)


def test_mutation_event_matching_allowance_rule_ids_reports_covering_rules() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery",
                paths=((("section", "1"),),),
                rule_id="recover-section",
            ),
            DeclaredMutationAllowance(
                kind="migration",
                paths=((("section", "2"),),),
                rule_id="migrate-section",
            ),
        ),
    )

    assert mutation_event_matching_allowance_rule_ids(event, (("section", "1"), ("subsection", "2"))) == (
        "recover-section",
    )
    assert mutation_event_matching_allowance_rule_ids(event, (("section", "3"),)) == ()


def test_build_mutation_event_path_set_report_partitions_target_allowances_and_out_of_scope_paths() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        resolved_target_path=(("section", "1"),),
        replaced_paths=(
            (("section", "1"),),
            (("section", "2"),),
            (("section", "3"),),
        ),
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery",
                paths=((("section", "2"),),),
                rule_id="recover-two",
            ),
        ),
    )

    assert build_mutation_event_path_set_report(event, ((("section", "1"),),)) == MutationEventPathSetReport(
        op_id="op",
        helper="helper",
        outcome="applied",
        touched_paths=(
            (("section", "1"),),
            (("section", "2"),),
            (("section", "3"),),
        ),
        changed_paths=(
            (("section", "1"),),
            (("section", "2"),),
            (("section", "3"),),
        ),
        allowed_effect_region_paths=((("section", "1"),),),
        declared_allowance_paths=((("section", "2"),),),
        declared_recovery_paths=((("section", "2"),),),
        declared_recovery_rule_ids=("recover-two",),
        declared_migration_paths=(),
        declared_migration_rule_ids=(),
        permitted_paths=(
            (("section", "1"),),
            (("section", "2"),),
        ),
        covered_changed_paths=(
            (("section", "1"),),
            (("section", "2"),),
        ),
        unexplained_changed_paths=((("section", "3"),),),
        allowed_non_target_paths=((("section", "2"),),),
        matched_allowance_rule_ids=("recover-two",),
        path_set_invariant_holds=False,
    )


@settings(max_examples=100)
@given(
    consumed_paths=TREE_PATH_SETS,
    created_paths=TREE_PATH_SETS,
    removed_paths=TREE_PATH_SETS,
    replaced_paths=TREE_PATH_SETS,
    allowed_effect_region_paths=TREE_PATH_SETS,
    recovery_paths=TREE_PATH_SETS,
    migration_paths=TREE_PATH_SETS,
)
def test_mutation_event_path_set_report_partition_invariant(
    consumed_paths: tuple[tuple[tuple[str, str], ...], ...],
    created_paths: tuple[tuple[tuple[str, str], ...], ...],
    removed_paths: tuple[tuple[tuple[str, str], ...], ...],
    replaced_paths: tuple[tuple[tuple[str, str], ...], ...],
    allowed_effect_region_paths: tuple[tuple[tuple[str, str], ...], ...],
    recovery_paths: tuple[tuple[tuple[str, str], ...], ...],
    migration_paths: tuple[tuple[tuple[str, str], ...], ...],
) -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        consumed_paths=consumed_paths,
        created_paths=created_paths,
        removed_paths=removed_paths,
        replaced_paths=replaced_paths,
        declared_allowances=(
            DeclaredMutationAllowance(kind="recovery", paths=recovery_paths, rule_id="recover"),
            DeclaredMutationAllowance(kind="migration", paths=migration_paths, rule_id="migrate"),
        ),
    )

    report = build_mutation_event_path_set_report(event, allowed_effect_region_paths)

    assert Counter(report.covered_changed_paths) + Counter(report.unexplained_changed_paths) == Counter(
        report.changed_paths
    )
    assert all(path_has_prefix(path, report.permitted_paths) for path in report.covered_changed_paths)
    assert not any(path_has_prefix(path, report.permitted_paths) for path in report.unexplained_changed_paths)
    assert report.path_set_invariant_holds == (not report.unexplained_changed_paths)
    assert all(path_has_prefix(path, report.declared_allowance_paths) for path in report.allowed_non_target_paths)
