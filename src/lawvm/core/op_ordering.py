"""Unified op-ordering kernel (Wave 0 of the pipeline-unification plan).

Design reference: ``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.2 (ordering
kernel interface) and §4 Wave 0. This module is the single composition point for
the per-op ordering algebra that today lives — divergently — across the
frontends (matrix divergences #1 same-moment detector, #2 temporal key, #11
renumber vacate-ordering).

``order_ops`` is an **orchestrator, not a reimplementation**: the same-moment
cross-act conflict step DELEGATES verbatim to
``lawvm.core.cross_act_same_moment.detect_cross_act_same_moment_conflicts`` (the
existing shared detector EE/NO/SE already consume). This module composes that
detector with a configurable temporal key and a small set of tiebreaks, and
exposes optional hooks (``prospective_gate`` / ``renumber_vacate``) that are
no-ops today and will be implemented in later waves (UK PIT gating / NO
topological vacate) without breaking the SE contract.

v1 algebra composition (fixed precedence):

  1. **temporal** — stable sort by ``profile.temporal_key(op)`` then by
     ``op.sequence`` (the universal stable secondary). ``default_temporal_key``
     is a *sequence-identity* key, so a frontend with no temporal dating (SE)
     is byte-unaffected: the ops come back in their input order.
  2. **prospective/commencement gating** — optional hook
     (``profile.prospective_gate``); ``None`` = no-op. (UK, a later wave.)
  3. **same-moment cross-act conflict** — DELEGATES to
     ``detect_cross_act_same_moment_conflicts`` passing the profile's
     ``finder_kind_prefix`` / ``incompatible_payload_predicate`` /
     ``precedence_claims`` / ``unproven_resolution_label``. An unresolved
     collision with no validated claim yields a blocking ``CompileAdjudication``
     in ``findings`` and a deterministic ``sequence_order_unproven`` order
     (apply order is unchanged — preserving today's byte output, mirroring the
     detector's ADDITIVE contract).
  4. **lex posterior** — affecting-act lexical tiebreak, applied ONLY when
     ``profile.lex_posterior`` is set (UK; off for SE/EE/NO today).
  5. **structural vacate ordering** — optional hook
     (``profile.renumber_vacate``); ``False`` = no-op. (NO/UK, later waves.)
  6. **stable sequence** — the final tiebreak is always ``op.sequence`` (already
     folded into step 1's stable sort; re-stated here as the invariant).

The kernel is grounding-neutral by construction for SE: with the default
temporal key, ``lex_posterior=False`` and both optional hooks unset, step 1 is a
stable sort whose key is constant across ops (so order == input order) and step 3
delegates to the very detector SE calls today with identical arguments. The
parallel-run equality gate (old direct-detector path vs ``order_ops``) is the
proof; see ``tests/test_se_order_ops_parallel_run.py``.
"""
from __future__ import annotations

from collections.abc import Sequence as AbcSequence
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from lawvm.core.cross_act_same_moment import (
    DEFAULT_UNPROVEN_RESOLUTION_LABEL,
    SameMomentPrecedenceClaim,
    detect_cross_act_same_moment_conflicts,
)
from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.replay_adjudication import CompileAdjudication

__all__ = [
    "PayloadPredicate",
    "TemporalKey",
    "default_temporal_key",
    "OrderingProfile",
    "OrderingDecision",
    "OrderedOps",
    "order_ops",
]


# A frontend-supplied ``(op, op) -> bool`` incompatible-payload comparator. When
# ``None`` the shared detector's default conservative predicate is used.
PayloadPredicate = Callable[[LegalOperation, LegalOperation], bool]

# The opaque key the temporal sort orders by. It must be a stable, comparable
# value (any tuple of comparables). The kernel never interprets its internals.
TemporalKey = Any


def default_temporal_key(op: LegalOperation) -> TemporalKey:
    """Sequence-identity temporal key (the no-temporal-dating default).

    A frontend with no commencement/effective dating in its ordering contract
    (SE today) gets a key that is just the op's ``sequence``. Because the
    temporal sort is *stable* and uses ``op.sequence`` as its secondary, a
    constant-per-op-relative key reduces the whole sort to "input order" — so
    such a frontend's ops come back byte-identical. Frontends that DO date their
    ordering (UK/NO, later waves) supply a key fn returning, e.g.,
    ``(effective_date, enacted_date, ...)``.
    """
    return op.sequence


@dataclass(frozen=True, slots=True)
class OrderingProfile:
    """Per-jurisdiction ordering policy fed to :func:`order_ops`.

    The required ``finder_kind_prefix`` is threaded verbatim into the shared
    same-moment detector (so each frontend's finding ``kind`` and claim
    validation/rejection ``rule_id``s stay frontend-distinct). All other fields
    default to the SE/EE/NO shape:

    - ``incompatible_payload_predicate``: ``None`` -> detector's default
      conservative predicate (EE supplies its own; SE/NO use the default).
    - ``precedence_claims``: validated same-moment precedence claims (UK today;
      empty for the rest).
    - ``temporal_key``: :func:`default_temporal_key` (sequence-identity).
    - ``lex_posterior``: ``False`` -> no affecting-act lexical tiebreak (SE/EE/
      NO). UK sets ``True`` in a later wave.
    - ``unproven_resolution_label``: the detector's ``resolution`` label for the
      no-claim case; default ``"sequence_order_unproven"`` (UK overrides).
    - ``prospective_gate`` / ``renumber_vacate``: OPTIONAL hooks for later waves
      (UK PIT-in-force gating / NO topological vacate ordering). They are
      no-ops when unset (``None`` / ``False``), so the SE contract is unaffected
      and the signatures extend without breaking it.
    """

    finder_kind_prefix: str
    incompatible_payload_predicate: Optional[PayloadPredicate] = None
    precedence_claims: Sequence[SameMomentPrecedenceClaim] = ()
    temporal_key: Callable[[LegalOperation], TemporalKey] = default_temporal_key
    lex_posterior: bool = False
    unproven_resolution_label: str = DEFAULT_UNPROVEN_RESOLUTION_LABEL
    fragment_action_allowlist: Optional[frozenset[StructuralAction]] = None
    # ── Later-wave hooks (no-ops today; design §3.2 steps 2 and 5). ──────────
    # UK PIT-in-force commencement gate: ``(op, jurisdiction_or_date) -> bool``.
    # None today = every op passes (no gating). Implemented in the UK wave.
    prospective_gate: Optional[Callable[[LegalOperation, str], bool]] = None
    # NO topological renumber vacate-ordering / UK text-patch chain ordering.
    # False today = no structural reordering. Implemented in NO/UK waves.
    renumber_vacate: bool = False


@dataclass(frozen=True, slots=True)
class OrderingDecision:
    """Why a single op landed at its position in the ordered output.

    A minimal, deterministic justification carrier so the ordering is auditable
    (design §3.2: ``justification`` records why each op is ordered). v1 records
    the op id, its final 0-based position, and the stage that placed it
    (``temporal_sequence_stable`` for the SE/default lane).
    """

    op_id: str
    position: int
    stage: str


@dataclass(frozen=True, slots=True)
class OrderedOps:
    """Result of :func:`order_ops`: the ordered ops + justification + findings."""

    ops: tuple[LegalOperation, ...]
    justification: tuple[OrderingDecision, ...] = ()
    findings: tuple[CompileAdjudication, ...] = ()


def order_ops(
    ops: AbcSequence[LegalOperation],
    profile: OrderingProfile,
) -> OrderedOps:
    """Compose the v1 ordering algebra for ``ops`` under ``profile``.

    Returns an :class:`OrderedOps` with the ordered op tuple, a per-op
    :class:`OrderingDecision` justification, and the same-moment conflict
    ``findings`` (blocking ``CompileAdjudication``s for unresolved collisions).

    Empty input -> empty result (no findings, no justification). The order is
    deterministic for any fixed input. See the module docstring for the fixed
    precedence of the composed stages.
    """
    ops_list = list(ops)
    if not ops_list:
        return OrderedOps(ops=(), justification=(), findings=())

    # ── Stage 1: temporal sort (stable; secondary key is op.sequence). ───────
    # ``sorted`` is stable, so equal (temporal_key, sequence) pairs keep their
    # input order. With ``default_temporal_key`` the key collapses to
    # ``(sequence, sequence)`` and the result is input order (SE byte-identity).
    temporal_key = profile.temporal_key
    ordered = sorted(ops_list, key=lambda op: (temporal_key(op), op.sequence))

    # ── Stage 2: prospective/commencement gating (optional hook; no-op now). ─
    # UK (later wave) supplies a gate that drops not-yet-in-force ops. Unset =>
    # every op passes, so the list is unchanged. The hook is threaded here so
    # the signature is stable across waves; SE never sets it.
    gate = profile.prospective_gate
    if gate is not None:  # pragma: no cover — exercised in the UK wave
        ordered = [op for op in ordered if gate(op, "")]

    # ── Stage 3: same-moment cross-act conflict — DELEGATE verbatim. ─────────
    # This is the orchestration boundary: ``order_ops`` does NOT reimplement the
    # precedence rule. The detector is ADDITIVE — it never reorders or drops an
    # op; it emits findings. So apply order stays the deterministic temporal +
    # sequence order ("sequence_order_unproven" for an unresolved collision),
    # preserving today's byte output while surfacing the ambiguity.
    adjudications_out: list[CompileAdjudication] = []
    detect_cross_act_same_moment_conflicts(
        ordered,
        finder_kind_prefix=profile.finder_kind_prefix,
        precedence_claims=profile.precedence_claims,
        incompatible_payload_predicate=profile.incompatible_payload_predicate,
        fragment_action_allowlist=profile.fragment_action_allowlist,
        unproven_resolution_label=profile.unproven_resolution_label,
        adjudications_out=adjudications_out,
    )

    # ── Stage 4: lex-posterior tiebreak (only when the profile opts in). ─────
    # Affecting-act lexical order as a STABLE tiebreak on top of the temporal
    # order: a same-(temporal_key, sequence) tie is broken by affecting act id.
    # Off for SE/EE/NO (so byte output is unchanged); UK sets it in a later
    # wave. Applied as a stable sort keyed on the affecting act id so it only
    # reorders genuine ties.
    if profile.lex_posterior:  # pragma: no cover — exercised by UK + synthetic test
        ordered = sorted(
            ordered,
            key=lambda op: (
                temporal_key(op),
                _affecting_act_id(op),
                op.sequence,
            ),
        )

    # ── Stage 5: structural vacate ordering (optional hook; no-op now). ──────
    # NO topological renumber vacate / UK text-patch chain ordering. False =>
    # no reordering. Implemented in the NO/UK waves. The flag is read here so
    # the kernel shape is stable; SE never sets it.
    if profile.renumber_vacate:  # pragma: no cover — exercised in NO/UK waves
        raise NotImplementedError(
            "renumber_vacate structural ordering is a later-wave hook "
            "(NO/UK); not implemented in the Wave 0 SE kernel"
        )

    # ── Stage 6: stable sequence final tiebreak — already an invariant of the
    # stable sorts above (every sort carries ``op.sequence`` as its last key).

    justification = tuple(
        OrderingDecision(
            op_id=op.op_id,
            position=position,
            stage="temporal_sequence_stable",
        )
        for position, op in enumerate(ordered)
    )
    return OrderedOps(
        ops=tuple(ordered),
        justification=justification,
        findings=tuple(adjudications_out),
    )


def _affecting_act_id(op: LegalOperation) -> str:
    """Affecting act id (``OperationSource.statute_id``) for the lex tiebreak.

    Mirrors ``cross_act_same_moment._op_affecting_act_id`` (kept private there);
    re-expressed locally so the kernel does not reach into the detector's
    internals. ``""`` when the op carries no source.
    """
    if op.source is None:
        return ""
    return str(getattr(op.source, "statute_id", "") or "")
