from __future__ import annotations

from collections import Counter
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lawvm.core.mutation_accounting import (
    MutationAccountingResult,
    MutationInvariantReport,
    build_mutation_invariant_reports,
    check_mutation_accounting,
    mutation_event_outcome_family,
)
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
    validate_declared_mutation_allowance,
    validate_mutation_event_allowances,
    validate_mutation_event_paths,
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


def test_mutation_accounting_result_validates_direct_construction() -> None:
    result = MutationAccountingResult(
        code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        op_id="op",
        helper="helper",
        touched_count=1,
        allowed_roots=cast(Any, [[("section", "1")]]),
        matched_allowance_rule_ids=cast(Any, ["rule"]),
    )

    assert result.allowed_roots == ((("section", "1"),),)
    assert result.matched_allowance_rule_ids == ("rule",)
    with pytest.raises(ValueError, match="known mutation-accounting code"):
        MutationAccountingResult(
            code="PYTHON_ORDER_GUESSED",
            op_id="op",
            helper="helper",
        )
    with pytest.raises(ValueError, match="touched_count"):
        MutationAccountingResult(
            code="REPLAY_FAILED_OP_MUTATED_TREE",
            op_id="op",
            helper="helper",
            touched_count=cast(Any, -1),
        )


def test_mutation_invariant_report_validates_direct_construction() -> None:
    result = MutationAccountingResult(
        code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        op_id="op",
        helper="helper",
    )
    report = MutationInvariantReport(
        op_id="op",
        helper="helper",
        outcome="applied",
        touched_paths=cast(Any, [[("section", "2")]]),
        results=cast(Any, [result]),
    )

    assert report.touched_paths == ((("section", "2"),),)
    assert report.results == (result,)
    with pytest.raises(ValueError, match="path_set_invariant_holds"):
        MutationInvariantReport(
            op_id="op",
            helper="helper",
            outcome="applied",
            path_set_invariant_holds=cast(Any, "yes"),
        )
    with pytest.raises(ValueError, match="results"):
        MutationInvariantReport(
            op_id="op",
            helper="helper",
            outcome="applied",
            results=cast(Any, [object()]),
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


def test_validate_declared_mutation_allowance_requires_kind_and_rule_for_paths() -> None:
    allowance = DeclaredMutationAllowance(
        kind="",
        paths=((("section", "1"),),),
    )

    assert validate_declared_mutation_allowance(allowance) == (
        "declared mutation allowance requires a non-empty kind",
        "declared mutation allowance with paths requires a rule_id",
    )


def test_validate_declared_mutation_allowance_allows_named_root_allowance() -> None:
    allowance = DeclaredMutationAllowance(
        kind="migration",
        paths=((),),
        rule_id="whole_act_migration",
    )

    assert validate_declared_mutation_allowance(allowance) == ()


def test_validate_mutation_event_paths_rejects_empty_path_kinds() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="insert",
        helper="helper",
        outcome="applied",
        created_paths=((("", "1"),),),
    )

    assert validate_mutation_event_paths(event) == (
        "mutation event created_paths path step 0 requires a non-empty kind",
    )


def test_build_mutation_event_path_set_report_rejects_malformed_allowances() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        replaced_paths=((("section", "1"),),),
        declared_allowances=(
            DeclaredMutationAllowance(
                kind="recovery",
                paths=((("section", "2"),),),
            ),
        ),
    )

    assert validate_mutation_event_allowances(event) == (
        "declared mutation allowance with paths requires a rule_id",
    )
    try:
        build_mutation_event_path_set_report(event, ((("section", "1"),),))
    except ValueError as exc:
        assert "requires a rule_id" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected malformed allowance rejection")


def test_build_mutation_event_path_set_report_rejects_malformed_event_paths() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="insert",
        helper="helper",
        outcome="applied",
        created_paths=((("", "1"),),),
    )

    try:
        build_mutation_event_path_set_report(event, ())
    except ValueError as exc:
        assert "created_paths path step 0 requires a non-empty kind" in str(exc)
    else:  # pragma: no cover - explicit failure branch
        raise AssertionError("expected malformed event path rejection")


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


def test_core_mutation_accounting_flags_out_of_scope_touches() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="helper",
        outcome="applied",
        resolved_target_path=(("section", "1"),),
        replaced_paths=((("section", "2"),),),
    )

    assert check_mutation_accounting([event]) == [
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET op_id=op helper=helper touched=1"
    ]
    assert build_mutation_invariant_reports([event]) == (
        MutationInvariantReport(
            op_id="op",
            helper="helper",
            outcome="applied",
            touched_paths=((("section", "2"),),),
            changed_paths=((("section", "2"),),),
            allowed_roots=((("section", "1"),),),
            allowed_effect_region_paths=((("section", "1"),),),
            permitted_paths=((("section", "1"),),),
            unexplained_changed_paths=((("section", "2"),),),
            out_of_scope_paths=((("section", "2"),),),
            path_set_invariant_holds=False,
            results=(
                MutationAccountingResult(
                    code="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
                    op_id="op",
                    helper="helper",
                    touched_count=1,
                    allowed_roots=((("section", "1"),),),
                    out_of_scope_paths=((("section", "2"),),),
                ),
            ),
        ),
    )


def test_core_mutation_accounting_classifies_frontend_specific_applied_outcomes() -> None:
    assert mutation_event_outcome_family("replaced_node") == "applied"
    assert mutation_event_outcome_family("table_rows_inserted") == "applied"
    assert mutation_event_outcome_family("skipped") == "skipped"
    assert mutation_event_outcome_family("failed") == "failed"
    assert mutation_event_outcome_family("local_observation") == "unknown"


def test_core_mutation_accounting_checks_specific_applied_outcomes() -> None:
    event = MutationEvent(
        op_id="uk-op",
        source_statute="ukpga/2000/1",
        action="replace",
        helper="_replace_node_in_statute",
        outcome="replaced_node",
        resolved_target_path=(("section", "1"),),
        replaced_paths=((("section", "2"),),),
    )

    reports = build_mutation_invariant_reports([event])

    assert check_mutation_accounting([event]) == [
        "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET op_id=uk-op helper=_replace_node_in_statute touched=1"
    ]
    assert reports[0].outcome == "replaced_node"
    assert reports[0].allowed_roots == ((("section", "1"),),)
    assert reports[0].out_of_scope_paths == ((("section", "2"),),)
    assert reports[0].path_set_invariant_holds is False


def test_core_mutation_accounting_parent_boundary_helpers_are_configurable() -> None:
    event = MutationEvent(
        op_id="op",
        source_statute="src",
        action="replace",
        helper="dispatch",
        outcome="applied",
        resolved_target_path=(("section", "1"), ("subsection", "2")),
        parent_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
    )

    default_report = build_mutation_invariant_reports([event])[0]
    configured_report = build_mutation_invariant_reports(
        [event],
        parent_boundary_helpers=frozenset({"dispatch"}),
    )[0]

    assert default_report.allowed_roots == (
        (("section", "1"), ("subsection", "2")),
        (("section", "1"),),
    )
    assert default_report.path_set_invariant_holds is True
    assert configured_report.allowed_roots == ((("section", "1"), ("subsection", "2")),)
    assert configured_report.path_set_invariant_holds is False
