"""``lawvm.core.compare_eid_parity_audit`` — D10 ``COMPARE.DETERMINISTIC_GAP_VS_MANUAL_FRONTIER_PARITY``.

Per :file:`notes/LAWVM_AUDIT_REGISTRY_ROADMAP.md` (D10): the oracle-comparison
plane partitions each divergent eId into exactly one bucket
(``deterministic_gap`` / ``manual_frontier`` / ``oracle_suspect`` /
``text_diff``). Today the per-bucket *counts* sum to a parity total, but nothing
asserts the partition is a true partition — that no single eId (under canonical
comparison identity) lands in two buckets at once. A double-classification means
the comparison plane is making two contradictory claims about the same provision
(``section-II`` is a deterministic gap AND a manual frontier), which silently
corrupts every downstream count and triage decision.

This audit makes that exclusivity assertion explicit. For any canonical eId
appearing in >1 bucket it emits a typed
:class:`~lawvm.core.phase_result.Observation` of kind
``COMPARE.EID_DOUBLE_CLASSIFIED`` carrying the canonical eId, the colliding
buckets, and the raw (un-normalized) eIds that aliased to it. The carrier is a
``role="observation"`` finding (mirrors D7's precedent): the wire consumer
decides whether the surfaced collision becomes a strict-mode barrier or a quirks
observation — this function emits observations only, never raises, never mutates
the buckets.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). Evidence-plane audit lane. It inspects
a passed bucket-assignment mapping and returns observations; it does NOT re-tune
the buckets, drop an eId, or pick a "winning" classification. Hiding a real
collision by silently de-duplicating would be exactly the §0 forbidden move — a
double-classified eId is genuine surfaced evidence that the upstream partition
logic is wrong, and it must be reported, not absorbed.

IDENTITY MODEL (AGENTS.md §2.8). Comparison identity is supplied by the
``canonicalize`` callable (UK injects
:func:`lawvm.uk_legislation.canonicalize.canonicalize_compare_eid`, which folds
Roman ``section-II`` and Arabic ``section-2`` to one key). The audit reuses that
single normalization authority rather than reinventing eId folding, so the
partition is checked under the *same* identity the scorer matches with. A
jurisdiction whose comparison plane has no aliasing may pass the identity
function (the default), in which case raw string equality is the identity.

JURISDICTION-NEUTRAL. The bucket carrier is a generic
``Mapping[str, Sequence[str]]`` (bucket-name -> eIds) and the identity is an
injected callable, so this audit is a core surface usable by any frontend that
produces an oracle-comparison partition. UK is the first wire site.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from lawvm.core.phase_result import Observation

# Public finding code, registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
COMPARE_EID_DOUBLE_CLASSIFIED = "COMPARE.EID_DOUBLE_CLASSIFIED"

# Audit-stage / owner stamped into the emitted Observations. Mirror the registry
# row's phase/owner so the wire point and the registry agree.
_COMPARE_PARITY_AUDIT_STAGE = "compare_oracle_classification"
_COMPARE_PARITY_AUDIT_OWNER = "compare_oracle_classification"
_COMPARE_PARITY_AUDIT_REASON = "eid_in_multiple_comparison_buckets"


def _identity(eid: str) -> str:
    """Default comparison identity: raw string (no jurisdiction folding)."""
    return eid


def assert_compare_eid_parity(
    bucket_assignments: Mapping[str, Sequence[str]],
    *,
    canonicalize: Callable[[str], str] = _identity,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """One :class:`Observation` per canonical eId that appears in more than one bucket.

    Args:
        bucket_assignments: the oracle-comparison partition — a mapping of
            bucket name (e.g. ``"deterministic_gap"``) to the eIds assigned to
            that bucket. The UK carrier is the ``dict[str, list[str]]`` returned
            by ``uk_oracle_check._classify_divergences``.
        canonicalize: the comparison-identity function. UK injects
            ``canonicalize_compare_eid`` so Roman/Arabic numbering aliases
            (``section-II`` ≡ ``section-2``) collapse to one key; the default is
            raw string identity. It must be pure and total (called once per
            raw eId); per AGENTS.md §1.10 an exception here is a caller-side
            programming bug that fails loud rather than being swallowed.
        source_statute: the base statute id under comparison, carried into each
            observation so a multi-statute bench run can route the finding back.

    Returns:
        Tuple of Observations, one per canonical eId found in ≥2 buckets, in
        ascending canonical-eId order (deterministic). An eId appearing twice
        within a SINGLE bucket is NOT a cross-bucket collision and is ignored
        here (intra-bucket de-dup is the bucket producer's concern, not the
        partition-exclusivity claim). The caller decides enforcement; this
        function emits observations only, never raises (beyond a caller-bug
        canonicalize failure), never mutates the buckets.

    Per AGENTS.md §0: a double-classified eId is surfaced evidence that the
    partition is not a true partition. The audit never resolves the collision by
    silently picking a winning bucket — it reports the contradiction.
    """
    # canonical eId -> bucket name -> sorted unique raw eIds that mapped there.
    by_canonical: dict[str, dict[str, set[str]]] = {}
    for bucket_name in sorted(bucket_assignments):
        for raw_eid in bucket_assignments[bucket_name]:
            canonical = canonicalize(raw_eid)
            buckets_for_canonical = by_canonical.setdefault(canonical, {})
            buckets_for_canonical.setdefault(bucket_name, set()).add(raw_eid)

    findings: list[Observation] = []
    for canonical in sorted(by_canonical):
        bucket_map = by_canonical[canonical]
        if len(bucket_map) < 2:
            continue
        colliding_buckets = tuple(sorted(bucket_map))
        raw_eids = tuple(
            sorted({raw for raws in bucket_map.values() for raw in raws})
        )
        detail = {
            "canonical_eid": canonical,
            "colliding_buckets": colliding_buckets,
            "raw_eids": raw_eids,
            "reason": _COMPARE_PARITY_AUDIT_REASON,
            "owner": _COMPARE_PARITY_AUDIT_OWNER,
        }
        findings.append(
            Observation(
                kind=COMPARE_EID_DOUBLE_CLASSIFIED,
                stage=_COMPARE_PARITY_AUDIT_STAGE,
                detail=detail,
                source_statute=source_statute,
            )
        )
    return tuple(findings)


__all__ = [
    "COMPARE_EID_DOUBLE_CLASSIFIED",
    "assert_compare_eid_parity",
]
