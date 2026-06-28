"""UK MigrationEvent emitter — derives ``lawvm.core.provenance.MigrationEvent``
records from the existing structural-mutation event stream.

CONTEXT
AGENTS.md §2.8 requires frontends to emit ``MigrationEvent`` records for moves,
renumbers, same-label rebirths, native-vs-migrated collisions, and repeal/
reinsert cycles so core can carry provision identity through PIT windows.
Finland owns a stateful ``MigrationLedger`` (``src/lawvm/finland/migration_
ledger.py:record_renumber`` / ``record_move``); UK has no analogue. The UK
replay fold records ``MutationEvent`` (the structural-mutation ledger at
``lawvm.core.mutation_events.MutationEvent``) per-apply from
``replay_state.py:_record_*_mutation_event`` (renumber_node /
descendant_renumber / promoted_child_renumber / replace_node-path-change /
remove_node / insert_node / children_splice / whole_act_repeal), but emits
ZERO ``MigrationEvent`` records. ``core/timeline_lineage.check_lineage_acyclic``
(LS-11) consumes ``MigrationEvent``; UK has no producer, so the cycle check is
dead code against UK replay.

This module is the UK frontend-level ``MigrationEvent`` emitter mandated by
§2.8. It is a PURE DETERMINISTIC TRANSFORM over the existing
``mutation_events_out`` stream — it adds NO new state, performs no replay
authorisation, and carries no overlay (⊥1.12). It exposes a single public
function ``derive_uk_migration_events(mutation_events) -> tuple[MigrationEvent,
...]`` that future wires can call at the replay fold-exit to feed core
lineage/identity consumers.

DERIVATION DISCIPLINE (what becomes a MigrationEvent, what doesn't)
* ``renumbered_paths`` non-empty (renumber_node / descendant_renumber /
  promoted_child_renumber): one ``MigrationEvent`` per ``(old_path, new_path)``
  pair. ``kind`` is decided by parent comparison: same parent → ``"renumber"``
  (relabel-in-place); different parent → ``"move"`` (cross-parent transfer).
* ``removed_paths`` AND ``created_paths`` both non-empty (replace_node
  path-change case at ``replay_state.py:_record_replace_node_mutation_event``
  lines 1076-1079): one ``MigrationEvent`` per zip-pair, with the same
  parent-comparison kind logic.
* ``removed_paths`` only (pure removal) / ``created_paths`` only (pure insert)
  / ``replaced_paths`` only (in-place content replace) / ``children_splice``
  on container / ``whole_act_repeal``: SKIP — these are not identity migration
  per §2.8. Removal is identity-end; insertion without subsequent renumber is
  identity-birth at a new address; whole-Act repeal is removal of the tree.
* Same-label rebirths (repeal + reinsert of the same address) are not yet
  distinguished at v0 — the structural MutationEvent ledger does not pair a
  removal with a later creation at the same path. Tier-B PR4 will extend this
  emitter to detect rebirth pairs once the MutationEvent ledger gains a stable
  per-op ``effect_id`` correlation key.

EVENT_ID DETERMINISM
Mirrors FI's ``MigrationLedger._make_event_id`` shape
(``f"mig:{source_statute}:{from_address}→{to_address}"``), with a ``uk:``
prefix to mark the carrier origin. The id is a pure function of (source, from,
to, kind) so the same renumber pair always yields the same event_id across
runs — making cycle witnesses stable. Per §2.8 the event_id MUST be non-empty
(the MigrationEvent ``__post_init__`` raises otherwise); the prefix guarantees
that even for empty ``source_statute``.

WHAT THIS DOES NOT PROMISE (honesty boundary):
* It does NOT authorise replay — emit-only. The structural MutationEvent
  stream is the structural-accounting plane; this emitter projects to the
  identity/lineage plane (§2.10 plane-distinct). A MigrationEvent produced here
  is evidence of identity continuity; it does not by itself mutate legal
  state.
* It does NOT emit ``"split"`` or ``"merge"`` kinds at v0 — those require
  source-text-carried intent (a repeal that splits one provision into two, or
  a merge of two into one). The structural MutationEvent ledger does not
  carry that intent today; same-label rebirth detection (PR4) is the path to
  ``"split"``.
* The v0 emitter does not pair removal-with-creation across ops — see §1.7
  (same effective date + same target + incompatible payload must be
  ambiguity until precedence is proven). A future strict-profile lane can
  flip the consumer side (PR2) to block on cycles.

§1.12 RE-DERIVATION RISK
NONE. Every ``TreePath`` carried in ``MutationEvent.renumbered_paths`` /
``removed_paths`` / ``created_paths`` originates from the source-side
``IRStatute`` — confirmed by the call site at ``replay_state.py:1189``:
``parent_path + ((_kind_str(new_node.kind), new_node.label or ""),)``. The
``op.source`` field on the original ``MutationEvent`` is the source-statute
identifier; the emitter re-uses it, never deriving semantics from rendered
or oracle text. The emitter's `from_address`/`to_address` are pure
``LegalAddress(path=tree_path)`` constructions — no semantic re-derivation.
"""
from __future__ import annotations

from typing import Iterable, Literal

from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.provenance import MigrationEvent

MigrationKind = Literal["renumber", "move", "split", "merge"]


def _make_event_id(
    *,
    source_statute: str,
    from_address: LegalAddress,
    to_address: LegalAddress,
    kind: str,
) -> str:
    """Deterministic event id mirroring FI's ``MigrationLedger._make_event_id``.

    The ``uk:`` prefix marks the carrier origin so a future cross-jurisdiction
    audit can distinguish UK-derived events from FI-derived ones (the FI
    pattern is ``mig:{source}:{from}→{to}`` — ``uk:mig:...`` distinguishes the
    UK emitter from a direct FI ledger emission).
    """
    return f"uk:mig:{source_statute or '_'}:{from_address!s}\u2192{to_address!s}:{kind}"


def _classify_pair_kind(old_path: tuple, new_path: tuple) -> MigrationKind:
    """Decide ``MigrationEvent.kind`` from a ``(old_path, new_path)`` pair.

    Per §2.8 / FI's ``MigrationLedger.record_renumber`` vs ``record_move``
    distinction: same parent path → ``"renumber"`` (relabel-in-place);
    different parent → ``"move"`` (cross-parent transfer).
    """
    old_parent = old_path[:-1] if old_path else ()
    new_parent = new_path[:-1] if new_path else ()
    return "move" if old_parent != new_parent else "renumber"


def derive_uk_migration_events(
    mutation_events: Iterable[MutationEvent],
) -> tuple[MigrationEvent, ...]:
    """Project the UK structural ``MutationEvent`` stream onto the
    ``MigrationEvent`` lineage plane (§2.8 / §2.10 plane-distinct).

    For each MutationEvent carrying ``renumbered_paths`` (a tuple of
    ``(old_tree_path, new_tree_path)`` pairs), emit one ``MigrationEvent`` per
    pair, with ``kind`` decided by parent comparison — ``"renumber"`` for
    relabel-in-place, ``"move"`` for cross-parent transfer.

    For each MutationEvent carrying both ``removed_paths`` AND
    ``created_paths`` (the replace_node path-change case at
    ``replay_state.py:_record_replace_node_mutation_event``), emit one
    ``MigrationEvent`` per zip-pair, with the same kind logic.

    Pure structural-mutation events (``insert_node`` alone, ``remove_node``
    alone, ``children_splice`` on a container, ``whole_act_repeal``) are
    not identity migration per §2.8 and yield no MigrationEvent — removal is
    identity-end, bare insertion is identity-birth, container splice is
    structural surgery, whole-Act repeal is removal of the tree.

    Returns a deterministic-stable tuple (no implicit ordering dependency) so
    a future consumer (PR2 wire of ``check_lineage_acyclic``) is run-to-run
    reproducible. The tuple preserves the source ``MutationEvent`` ordering so
    a same-day precedence witness (§1.7) is reachable.
    """
    emitted: list[MigrationEvent] = []
    for event in mutation_events:
        # source_statute: the op's source-statute identifier carried by the
        # structural MutationEvent. Used for event_id determinism + for cross-
        # jurisdiction traceability (an audit consumer can group events by
        # source statute).
        source_statute = str(event.source_statute or "")
        effective = ""  # v0: no source-carried effective date on MutationEvent
                          # today — the structural ledger doesn't carry it. The
                          # temporal_events axis is the right home for that
                          # provenance (Tier B PR3/PR4 territory).
        # 1. renumbered_paths — the canonical renumber case.
        for old_path, new_path in event.renumbered_paths:
            if not old_path or not new_path or old_path == new_path:
                continue
            kind = _classify_pair_kind(old_path, new_path)
            from_address = LegalAddress(path=tuple(old_path))
            to_address = LegalAddress(path=tuple(new_path))
            emitted.append(
                MigrationEvent(
                    event_id=_make_event_id(
                        source_statute=source_statute,
                        from_address=from_address,
                        to_address=to_address,
                        kind=kind,
                    ),
                    kind=kind,
                    from_address=from_address,
                    to_address=to_address,
                    effective=effective,
                    source_statute=source_statute,
                    witness=event,  # The structural MutationEvent is the witness
                                      # for the lineage event — per §2.8 a migration
                                      # event MUST carry a witness; the structural
                                      # MutationEvent carries the op_id + the resolved
                                      # target + the helper/name so a triager can trace.
                )
            )
        # 2. replace_node path-change case — ``removed_paths`` AND
        # ``created_paths`` both populated (only at replace_node where the node
        # changed path; pure in-place content replace leaves ``replaced_paths``
        # populated and these empty). Same parent → renumber; cross-parent →
        # move.
        if event.removed_paths and event.created_paths:
            # Pair removals with creations positionally. This mirrors the
            # replay_state.py:1076-1079 emit shape (single old_path → single
            # new_path in the replace_node path-change branch), so the zip is
            # 1:1 by construction.
            for old_path, new_path in zip(event.removed_paths, event.created_paths, strict=False):
                if not old_path or not new_path or old_path == new_path:
                    continue
                kind = _classify_pair_kind(old_path, new_path)
                from_address = LegalAddress(path=tuple(old_path))
                to_address = LegalAddress(path=tuple(new_path))
                emitted.append(
                    MigrationEvent(
                        event_id=_make_event_id(
                            source_statute=source_statute,
                            from_address=from_address,
                            to_address=to_address,
                            kind=kind,
                        ),
                        kind=kind,  # type: ignore[arg-type]
                        from_address=from_address,
                        to_address=to_address,
                        effective=effective,
                        source_statute=source_statute,
                        witness=event,
                    )
                )
    return tuple(emitted)


__all__ = ["derive_uk_migration_events"]
