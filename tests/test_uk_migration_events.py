"""Tests for the UK MigrationEvent emitter (§2.8 lineage carrier projection).

The emitter ``lawvm.uk_legislation.uk_migration_events.derive_uk_migration_
events`` projects the existing UK structural-mutation event stream
(``MutationEvent`` from ``core/mutation_events.py``) onto the
``MigrationEvent`` lineage plane (``core/provenance.py``). This is the §2.8
frontend responsibility — Finland owns a stateful ``MigrationLedger``
(``src/lawvm/finland/migration_ledger.py``); UK has no analogue today, so
``core/timeline_lineage.check_lineage_acyclic`` is dead code against UK replay.

The emitter is a PURE DETERMINISTIC TRANSFORM; no overlay, no replay
authorisation. These tests pin the projection shape per AGENTS.md §2.9:
synthetic positive+negative cases, a kind-classification discrimination test
(renumber vs move), an event_id-determinism pin, and an empty-input test.
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.provenance import MigrationEvent
from lawvm.uk_legislation.uk_migration_events import (
    derive_uk_migration_events,
    _classify_pair_kind,
    _make_event_id,
)


# TreePath shape: Tuple[Tuple[str, str], ...] — sequence of (kind, label) pairs.
# Reused here as the address shapes the structural MutationEvent ledger records.
_SECTION_1 = (("section", "1"),)
_SECTION_2 = (("section", "2"),)
_SUBSECTION_1A = (("section", "1"), ("subsection", "a"))
_SUBSECTION_1B = (("section", "1"), ("subsection", "b"))
_SECTION_1_SUBSECTION_A = (("section", "1"), ("subsection", "a"))


def _mut_event(
    *,
    op_id: str = "op-1",
    source_statute: str = "ukpga/2020/1",
    action: str = "replace",
    helper: str = "_renumber_node",
    outcome: str = "renumbered_node",
    renumbered_paths: tuple = (),
    removed_paths: tuple = (),
    created_paths: tuple = (),
    replaced_paths: tuple = (),
) -> MutationEvent:
    """Construct a minimal MutationEvent for emitter input."""
    return MutationEvent(
        op_id=op_id,
        source_statute=source_statute,
        action=action,
        helper=helper,
        outcome=outcome,
        renumbered_paths=renumbered_paths,
        removed_paths=removed_paths,
        created_paths=created_paths,
        replaced_paths=replaced_paths,
    )


def test_renumbered_paths_emit_one_migration_event_per_pair() -> None:
    """The canonical renumber case: a MutationEvent carrying
    ``renumbered_paths=((old, new),)`` produces one MigrationEvent.

    Section 1 → Section 2 with both at root (parent = ()) is the clean
    relabel-in-place case → kind="renumber" (no parent change).
    """
    events = derive_uk_migration_events(
        (
            _mut_event(
                renumbered_paths=(((_SECTION_1, _SECTION_2)),),
            ),
        )
    )
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, MigrationEvent)
    assert e.kind == "renumber"  # same root parent (()), label-only change
    assert e.from_address == LegalAddress(path=_SECTION_1)
    assert e.to_address == LegalAddress(path=_SECTION_2)
    assert e.source_statute == "ukpga/2020/1"
    # The structural MutationEvent is the witness per §2.8.
    assert e.witness is not None


def test_renumber_kind_for_same_parent() -> None:
    """Same parent, different label → ``kind="renumber"`` (relabel-in-place)."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                renumbered_paths=(((_SUBSECTION_1A, _SUBSECTION_1B)),),
            ),
        )
    )
    assert len(events) == 1
    assert events[0].kind == "renumber"


def test_move_kind_for_different_parent() -> None:
    """Different parent → ``kind="move"`` (cross-parent transfer)."""
    # Section 1 → Section 2 is a cross-section move (parents are the empty
    # root path before either, but the parents differ in their last segment).
    # Use subsections with different section parents to make the move case
    # unambiguous.
    events = derive_uk_migration_events(
        (
            _mut_event(
                renumbered_paths=(((_SUBSECTION_1A, (("section", "2"), ("subsection", "a")))),),
            ),
        )
    )
    assert len(events) == 1
    assert events[0].kind == "move"


def test_replace_node_path_change_emits_migration_event() -> None:
    """The replace_node path-change case (``removed_paths`` AND
    ``created_paths`` both populated, ``replaced_paths`` empty) emits a
    MigrationEvent — the node moved; this is identity migration, not pure
    content swap."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                action="replace",
                helper="_replace_node_in_statute",
                outcome="replaced_node",
                renumbered_paths=(),
                removed_paths=(_SECTION_1,),
                created_paths=(_SECTION_2,),
                replaced_paths=(),
            ),
        )
    )
    assert len(events) == 1
    e = events[0]
    # Section 1 → Section 2 — same (empty) parent → renumber per §2.8.
    assert e.kind == "renumber"
    assert e.from_address == LegalAddress(path=_SECTION_1)
    assert e.to_address == LegalAddress(path=_SECTION_2)


def test_pure_insert_node_emits_nothing() -> None:
    """A pure insert (``created_paths`` only, no removed, no renumbered) is
    identity-birth at a new address — NOT a migration per §2.8."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                action="insert",
                helper="_insert_node",
                outcome="inserted_node",
                created_paths=(_SECTION_2,),
                removed_paths=(),
                renumbered_paths=(),
                replaced_paths=(),
            ),
        )
    )
    assert events == ()


def test_pure_remove_node_emits_nothing() -> None:
    """A pure remove (``removed_paths`` only) is identity-END per §2.8 — NOT
    a migration. Removal without rebirth is not identity continuity."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                action="repeal",
                helper="_remove_node",
                outcome="removed_node",
                removed_paths=(_SECTION_1,),
                created_paths=(),
                renumbered_paths=(),
                replaced_paths=(),
            ),
        )
    )
    assert events == ()


def test_in_place_content_replace_emits_nothing() -> None:
    """A pure in-place content replace (``replaced_paths`` only, no
    removed/created/renumbered) is NOT identity migration — same path, same
    identity, just new content."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                action="replace",
                helper="_replace_node_in_statute",
                outcome="replaced_node",
                replaced_paths=(_SECTION_1,),
                removed_paths=(),
                created_paths=(),
                renumbered_paths=(),
            ),
        )
    )
    assert events == ()


def test_event_id_is_deterministic() -> None:
    """Same (source_statute, from, to, kind) → same event_id across runs.

    Determinism is load-bearing: a future cycle check (``check_lineage_
    acyclic``) returns the witnessed cycle as an ordered address list, and
    a stable event_id makes the witness reproducible across runs."""
    a = _make_event_id(
        source_statute="ukpga/2020/1",
        from_address=LegalAddress(path=_SECTION_1),
        to_address=LegalAddress(path=_SECTION_2),
        kind="renumber",
    )
    b = _make_event_id(
        source_statute="ukpga/2020/1",
        from_address=LegalAddress(path=_SECTION_1),
        to_address=LegalAddress(path=_SECTION_2),
        kind="renumber",
    )
    assert a == b


def test_event_id_distinguishes_source_statute() -> None:
    """Two op-source statutes producing the same from→to address pair must
    yield distinct event_ids so the lineage audit can group per-source
    (per the §2.8 frontend-responsibility: source_statute is the carrier)."""
    id_a = _make_event_id(
        source_statute="ukpga/2020/1",
        from_address=LegalAddress(path=_SECTION_1),
        to_address=LegalAddress(path=_SECTION_2),
        kind="renumber",
    )
    id_b = _make_event_id(
        source_statute="ukpga/2020/2",
        from_address=LegalAddress(path=_SECTION_1),
        to_address=LegalAddress(path=_SECTION_2),
        kind="renumber",
    )
    assert id_a != id_b


def test_classify_pair_kind_returns_renumber_for_same_parent() -> None:
    """The kind-classifier: same parent path prefix → renumber."""
    assert _classify_pair_kind(_SUBSECTION_1A, _SUBSECTION_1B) == "renumber"


def test_classify_pair_kind_returns_move_for_different_parent() -> None:
    """The kind-classifier: different parent path prefix → move."""
    other_section_subsection = (("section", "2"), ("subsection", "a"))
    assert _classify_pair_kind(_SUBSECTION_1A, other_section_subsection) == "move"


def test_empty_input_emits_empty_tuple() -> None:
    """An empty input stream emits an empty tuple — no false migrations."""
    assert derive_uk_migration_events(()) == ()


def test_emitter_preserves_input_ordering_for_same_day_precedence() -> None:
    """A sequence of renumber events emits MigrationEvents in source order —
    so a same-day precedence witness (§1.7) is reachable. The emitter does
    NOT reorder by source_statute or by address."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                op_id="op-1",
                renumbered_paths=(((_SECTION_1, _SECTION_2)),),
            ),
            _mut_event(
                op_id="op-2",
                renumbered_paths=(((_SUBSECTION_1A, _SUBSECTION_1B)),),
            ),
        )
    )
    assert len(events) == 2
    assert events[0].from_address == LegalAddress(path=_SECTION_1)
    assert events[1].from_address == LegalAddress(path=_SUBSECTION_1A)


def test_emitter_skips_no_op_renumber_pair() -> None:
    """A renumber pair where old_path == new_path is a no-op (no migration)
    — the emitter MUST skip it rather than produce a degenerate MigrationEvent
    with from_address == to_address."""
    events = derive_uk_migration_events(
        (
            _mut_event(
                renumbered_paths=(((_SECTION_1, _SECTION_1)),),
            ),
        )
    )
    assert events == ()
