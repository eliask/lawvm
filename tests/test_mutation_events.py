from __future__ import annotations

from lawvm.core.mutation_events import (
    DeclaredMutationAllowance,
    MutationEvent,
    mutation_event_declared_allowance_paths,
    mutation_event_touched_paths,
)


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
