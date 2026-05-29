"""Tests for UK descendant-renumber MutationEvent emission (AGENTS.md §1.6).

Covers the lineage/provenance requirement for the
_apply_same_provision_descendant_renumber path in replay_renumber_apply.py.

When a provision is rewritten into a parent-with-child shape (e.g. paragraph 12
→ section 12 / sub-paragraph (1)), a renumber-specific MutationEvent must be
emitted so PIT materialization can reconstruct the relocation provenance.

Rule ID verified:
  uk_replay_descendant_renumber_provision (UK_REPLAY_DESCENDANT_RENUMBER_RULE_ID)

AGENTS.md obligations covered:
  §15.1 synthetic positive test
  §15.2 real corpus regression note (see note at end of file)
  §15.3 finding/observation test (MutationEvent witness fields)
  §15.4 negative test
  §15.5 strict-mode behavior (proceed — descendant renumber is a legitimate
        lineage operation, not a heuristic recovery)
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_renumber_apply import UK_REPLAY_DESCENDANT_RENUMBER_RULE_ID
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor, replay_uk_ops


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


def _renumber_op(
    *,
    target: LegalAddress,
    destination: LegalAddress,
    op_id: str = "uk-test-drd-op",
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.RENUMBER,
        target=target,
        destination=destination,
        source=_source(),
        sequence=1,
    )


# ---------------------------------------------------------------------------
# Statute builders
# ---------------------------------------------------------------------------

def _statute_section_with_text() -> IRStatute:
    """Section 12 with plain text but no sub-provisions.

    The descendant renumber will rewrite section 12 into a container whose
    single child is sub-paragraph (1), inheriting section 12's text.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="12",
                    text="Original section twelve text.",
                ),
            ),
        ),
        supplements=(),
    )


def _statute_section_with_sub() -> IRStatute:
    """Section 5 with an existing subsection (1).

    For the negative test: a same-parent sibling renumber (section 5 → section 6)
    must NOT emit a descendant-renumber event.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Subsection one.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _statute_para_with_single_subpara() -> IRStatute:
    """Section 12 / paragraph (1) containing a single sub-paragraph (a).

    This tests the descendant renumber shape where the source already has one
    child that matches the destination kind+label.  With a single matching child,
    _apply_same_provision_descendant_renumber returns False (no apply) — so the
    event must NOT fire.
    """
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="12",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Sub one text.",
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


# ---------------------------------------------------------------------------
# Test: positive — MutationEvent emitted with lineage
# ---------------------------------------------------------------------------

def test_descendant_renumber_emits_mutation_event_with_lineage() -> None:
    """Descendant renumber emits a renumber MutationEvent carrying old→new path.

    Source: section:12
    Destination: section:12 / subsection:(1)

    Expected:
    - Replace succeeds (result tree has section 12 with a subsection (1) child).
    - A MutationEvent with outcome='renumbered_node' is emitted.
    - renumbered_paths carries (old_path, new_child_path).
    """
    mutation_events: list[MutationEvent] = []
    adjudications: list[CompileAdjudication] = []
    statute = _statute_section_with_text()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
    )

    result = replay_uk_ops(
        statute,
        [op],
        adjudications_out=adjudications,
        mutation_events_out=mutation_events,
    )

    # Tree shape: section 12 should now contain a subsection (1) child.
    section = result.body.children[0]
    assert section.label == "12", f"Expected section 12, got {section.label!r}"
    assert len(section.children) == 1, (
        f"Expected section 12 to have 1 child, got {len(section.children)}: "
        f"{[c.label for c in section.children]}"
    )
    subsection = section.children[0]
    assert subsection.label == "1", f"Expected subsection (1), got {subsection.label!r}"

    # MutationEvent with renumbered_paths must be present.
    renumber_events = [
        ev for ev in mutation_events
        if ev.outcome == "renumbered_node"
        and ev.helper == "_apply_same_provision_descendant_renumber"
    ]
    assert renumber_events, (
        f"Expected a renumber MutationEvent with helper='_apply_same_provision_descendant_renumber' "
        f"but mutation_events={[ev.outcome + '/' + ev.helper for ev in mutation_events]!r}"
    )

    ev = renumber_events[0]
    assert len(ev.renumbered_paths) == 1, (
        f"Expected exactly one renumbered_paths pair, got {ev.renumbered_paths!r}"
    )
    old_path, new_child_path = ev.renumbered_paths[0]

    # old_path = path to section:12
    assert old_path == (("section", "12"),), (
        f"Expected old_path=(('section', '12'),), got {old_path!r}"
    )
    # new_child_path = path to section:12 / subsection:1
    assert len(new_child_path) == 2, (
        f"Expected new_child_path of length 2, got {new_child_path!r}"
    )
    assert new_child_path[0] == ("section", "12"), (
        f"Expected new_child_path[0]=('section','12'), got {new_child_path[0]!r}"
    )
    assert new_child_path[1][0] == "subsection", (
        f"Expected new_child_path[1] kind='subsection', got {new_child_path[1][0]!r}"
    )
    assert new_child_path[1][1] == "1", (
        f"Expected new_child_path[1] label='1', got {new_child_path[1][1]!r}"
    )


# ---------------------------------------------------------------------------
# Test: MutationEvent fields witness the correct op and action
# ---------------------------------------------------------------------------

def test_descendant_renumber_mutation_event_carries_op_and_action() -> None:
    """MutationEvent op_id, source_statute, and action fields are correctly set."""
    mutation_events: list[MutationEvent] = []
    statute = _statute_section_with_text()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
        op_id="uk-drd-witness-test",
    )

    replay_uk_ops(
        statute,
        [op],
        mutation_events_out=mutation_events,
    )

    renumber_ev = next(
        (ev for ev in mutation_events
         if ev.outcome == "renumbered_node"
         and ev.helper == "_apply_same_provision_descendant_renumber"),
        None,
    )
    assert renumber_ev is not None, "Renumber MutationEvent must be emitted"
    assert renumber_ev.op_id == "uk-drd-witness-test", (
        f"Expected op_id='uk-drd-witness-test', got {renumber_ev.op_id!r}"
    )
    assert renumber_ev.source_statute == "ukpga/2026/99", (
        f"Expected source_statute='ukpga/2026/99', got {renumber_ev.source_statute!r}"
    )
    assert renumber_ev.action == "renumber", (
        f"Expected action='renumber', got {renumber_ev.action!r}"
    )


# ---------------------------------------------------------------------------
# Test: resolved_target_path on the MutationEvent
# ---------------------------------------------------------------------------

def test_descendant_renumber_mutation_event_has_resolved_target_path() -> None:
    """MutationEvent carries resolved_target_path from the op's target address."""
    mutation_events: list[MutationEvent] = []
    statute = _statute_section_with_text()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
    )

    replay_uk_ops(statute, [op], mutation_events_out=mutation_events)

    renumber_ev = next(
        (ev for ev in mutation_events
         if ev.outcome == "renumbered_node"
         and ev.helper == "_apply_same_provision_descendant_renumber"),
        None,
    )
    assert renumber_ev is not None
    # resolved_target_path mirrors the source target address path.
    assert renumber_ev.resolved_target_path == (("section", "12"),), (
        f"Expected resolved_target_path=(('section','12'),), "
        f"got {renumber_ev.resolved_target_path!r}"
    )


# ---------------------------------------------------------------------------
# Test: PIT materialization — renumbered_paths is consumable
# ---------------------------------------------------------------------------

def test_descendant_renumber_mutation_event_renumbered_paths_consumable() -> None:
    """renumbered_paths on the MutationEvent is a valid ((old, new),) tuple.

    This verifies the shape PIT materialization expects to consume for lineage
    reconstruction.  The test is intentionally shallow: it checks the MutationEvent
    shape rather than invoking the full PIT stack, since the PIT materialization
    integration path depends on a larger UK execution context.

    Remaining gap: a full PIT-round-trip test (statute → replay → PIT → lineage
    query) is deferred until the UK PIT consumer has a stable API for
    renumbered_paths ingestion.
    """
    mutation_events: list[MutationEvent] = []
    statute = _statute_section_with_text()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
    )

    replay_uk_ops(statute, [op], mutation_events_out=mutation_events)

    renumber_ev = next(
        (ev for ev in mutation_events
         if ev.outcome == "renumbered_node"
         and ev.helper == "_apply_same_provision_descendant_renumber"),
        None,
    )
    assert renumber_ev is not None
    paths = renumber_ev.renumbered_paths
    assert isinstance(paths, tuple), f"renumbered_paths must be a tuple, got {type(paths)}"
    assert len(paths) == 1, f"Expected exactly one (old, new) pair, got {paths!r}"
    old_path, new_path = paths[0]
    assert isinstance(old_path, tuple), f"old_path must be a tuple, got {type(old_path)}"
    assert isinstance(new_path, tuple), f"new_path must be a tuple, got {type(new_path)}"
    # new_path must be strictly longer than old_path (descendant, not sibling)
    assert len(new_path) > len(old_path), (
        f"new_path {new_path!r} must be longer than old_path {old_path!r} "
        "for a descendant renumber"
    )
    # new_path must start with the old_path (child of the original provision)
    assert new_path[:len(old_path)] == old_path, (
        f"new_path {new_path!r} must share the old_path {old_path!r} as a prefix"
    )


# ---------------------------------------------------------------------------
# Test: rule ID constant is consistent with helper string
# ---------------------------------------------------------------------------

def test_descendant_renumber_rule_id_constant_value() -> None:
    """UK_REPLAY_DESCENDANT_RENUMBER_RULE_ID has the expected stable string value."""
    assert UK_REPLAY_DESCENDANT_RENUMBER_RULE_ID == "uk_replay_descendant_renumber_provision", (
        f"Rule ID constant changed unexpectedly: {UK_REPLAY_DESCENDANT_RENUMBER_RULE_ID!r}"
    )


# ---------------------------------------------------------------------------
# Negative test: sibling renumber emits NO descendant-renumber event
# ---------------------------------------------------------------------------

def test_sibling_renumber_emits_no_descendant_renumber_event() -> None:
    """A same-parent sibling renumber (section:5 → section:6) must NOT emit a
    descendant-renumber MutationEvent.  The sibling path emits its own
    renumber event via _record_renumber_node_mutation_event.
    """
    mutation_events: list[MutationEvent] = []
    adjudications: list[CompileAdjudication] = []
    statute = _statute_section_with_sub()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "5"),)),
        destination=LegalAddress(path=(("section", "6"),)),
        op_id="uk-drd-sibling-neg-test",
    )

    replay_uk_ops(
        statute,
        [op],
        adjudications_out=adjudications,
        mutation_events_out=mutation_events,
    )

    descendant_events = [
        ev for ev in mutation_events
        if ev.helper == "_apply_same_provision_descendant_renumber"
    ]
    assert not descendant_events, (
        f"Sibling renumber must NOT emit a descendant-renumber event, "
        f"but got: {descendant_events!r}"
    )


# ---------------------------------------------------------------------------
# Negative test: no destination → no event
# ---------------------------------------------------------------------------

def test_no_destination_emits_no_descendant_renumber_event() -> None:
    """A RENUMBER op with no destination must not emit any event via the
    descendant-renumber path.
    """
    mutation_events: list[MutationEvent] = []
    statute = _statute_section_with_text()
    op = LegalOperation(
        op_id="uk-drd-no-dest-test",
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("section", "12"),)),
        destination=None,
        source=_source(),
        sequence=1,
    )

    replay_uk_ops(statute, [op], mutation_events_out=mutation_events)

    descendant_events = [
        ev for ev in mutation_events
        if ev.helper == "_apply_same_provision_descendant_renumber"
    ]
    assert not descendant_events, (
        f"No-destination op must not emit a descendant-renumber event, "
        f"got: {descendant_events!r}"
    )


# ---------------------------------------------------------------------------
# Negative test: existing sole child at destination → no apply, no event
# ---------------------------------------------------------------------------

def test_sole_existing_child_at_destination_no_event() -> None:
    """When source already has a single child that matches the destination
    kind+label, _apply_same_provision_descendant_renumber returns False and
    no event is emitted.
    """
    mutation_events: list[MutationEvent] = []
    adjudications: list[CompileAdjudication] = []
    statute = _statute_para_with_single_subpara()
    # Destination matches the single existing child: kind=subsection, label=1.
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
        op_id="uk-drd-existing-child-test",
    )

    replay_uk_ops(
        statute,
        [op],
        adjudications_out=adjudications,
        mutation_events_out=mutation_events,
    )

    descendant_events = [
        ev for ev in mutation_events
        if ev.helper == "_apply_same_provision_descendant_renumber"
    ]
    assert not descendant_events, (
        f"No descendant-renumber event should fire when sole child already exists, "
        f"got: {descendant_events!r}"
    )


# ---------------------------------------------------------------------------
# Test: mutation_events_out=None — no error, just no collection
# ---------------------------------------------------------------------------

def test_no_mutation_events_out_does_not_raise() -> None:
    """When mutation_events_out is not provided, the apply still succeeds silently."""
    adjudications: list[CompileAdjudication] = []
    statute = _statute_section_with_text()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
    )

    result = replay_uk_ops(
        statute,
        [op],
        adjudications_out=adjudications,
        mutation_events_out=None,
    )

    # The apply should still succeed even without mutation event collection.
    section = result.body.children[0]
    assert len(section.children) == 1
    assert section.children[0].label == "1"


# ---------------------------------------------------------------------------
# Test: UKReplayExecutor directly — MutationEvent via executor instance
# ---------------------------------------------------------------------------

def test_executor_direct_emits_descendant_renumber_event() -> None:
    """UKReplayExecutor.apply_op collects the descendant-renumber MutationEvent.

    We use apply_op (not _apply_renumber_op directly) because _current_mutation_op
    is set by apply_op; calling the private method bypasses that context.
    """
    mutation_events: list[MutationEvent] = []
    adjudications: list[CompileAdjudication] = []
    statute = _statute_section_with_text()
    op = _renumber_op(
        target=LegalAddress(path=(("section", "12"),)),
        destination=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
        op_id="uk-drd-executor-direct",
    )

    executor = UKReplayExecutor(
        statute,
        adjudications_out=adjudications,
        mutation_events_out=mutation_events,
    )
    executor.apply_op(op)

    renumber_events = [
        ev for ev in mutation_events
        if ev.outcome == "renumbered_node"
        and ev.helper == "_apply_same_provision_descendant_renumber"
    ]
    assert renumber_events, (
        "UKReplayExecutor.apply_op must emit a descendant-renumber MutationEvent"
    )


# ---------------------------------------------------------------------------
# Corpus regression note
# ---------------------------------------------------------------------------
# The UK inner-loop bench (bench_corpus_hard_smoke_source_closed.csv) does not
# directly expose which statutes exercise the descendant-renumber path.  The
# `_apply_same_provision_descendant_renumber` code path was previously exercised
# silently (no lineage event) on statutes where a provision is reshaped into a
# parent-with-child form.  The bench delta check (actuator3_before vs
# actuator3_after) confirms no regression.  A corpus-pinned regression test
# would require identifying a specific statute that triggers this path, which
# is left for future investigation via:
#   uv run lawvm -j uk bench --corpus ... --label ... --replay --top 20
