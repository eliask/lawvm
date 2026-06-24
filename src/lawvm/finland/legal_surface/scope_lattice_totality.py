"""Scope-lattice totality sweep (registry rows SCOPE-01/02).

A *read-only* per-address scope-lattice sweep over the already-produced replay
output (:class:`~lawvm.finland.replay_products.ReplayProducts`). It does NOT
touch the fold/replay/apply engine, the selection engine, or metadata: it
CONSUMES the finished ``timelines`` carrier and emits a typed observation. The
spirit is the audit-registry §0 generative principle — every selection-relevant
co-effective collision is either resolved by an explicit precedence rail, carried
as a scope distinguisher, or surfaced as a finding; never silently broken by list
order.

The carrier truth (assessed HONESTLY before building)
=====================================================
SCOPE-01's full form asks for a *scope lattice*: scope predicates with stable
identity + canonical normalization, two co-effective rows admitted only if their
scope predicates are DISJOINT or a precedence rail chooses one. The core IR DOES
carry a structured scope predicate — :class:`lawvm.core.ir.ScopePredicate`
(``dimension`` + ``includes``) rides ``ProvisionVersion.applicability`` — and the
selection engine already evaluates it (``applicability_matches`` /
``required_scope_dimensions`` -> ``ambiguous_missing_scope``). BUT over the FI
corpus that predicate is **structurally empty**: across the bench corpus 0 of
~35k materialized versions carry ANY applicability predicate (only a ``territory``
``set_applicability`` event would populate one, and the corpus contains none).

So the load-bearing question — "do FI selection rows carry a structured,
normalizable scope predicate?" — answers **NO, not populated**. SCOPE-01's full
disjointness-lattice is therefore PART with a NAMED MISSING CARRIER: there is no
populated scope predicate on FI selection rows to test disjointness against. This
sweep does NOT fabricate one.

What IS checkable (and is implemented here)
===========================================
The precedence rail itself is real and enforced. Production selection
(``timeline_selection.pick_latest`` + ``equal_rank_same_source_conflicts``)
resolves co-effective candidates by ``(effective, enacted, source_statute)`` lex
posterior; a residual collision with the SAME rank key AND distinct legal content
is an *equal-rank tie* whose winner depends on list order, not a proved legal
precedence. That is exactly the SCOPE-01 violation shape restricted to the only
discriminator the corpus actually has:

    two co-effective rows that the precedence rail does NOT separate
    (same effective/enacted/source, distinct content) AND that carry NO
    scope distinguisher (disjoint applicability) to admit the overlap
    -> SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE.

SCOPE-02 (overlapping scope + missing required query dimension ->
``ambiguous_missing_scope``) is already IMPL in the selection engine for the
*territory* dimension (``required_scope_dimensions`` forces
``ambiguous_missing_scope`` when a populated territory predicate is present and no
territory is supplied). Over the corpus that arm is at 0 (no populated
predicate). This sweep adds the read-only TOTALITY mirror of the SCOPE-02 arm: a
co-effective collision with NO scope-distinguishing field is the candidate
ambiguity the missing carrier would otherwise hide — surfaced under the SAME
finding code rather than silently order-resolved.

Standing-guard status
=====================
Over the bench corpus the sweep is at **0** findings: every address is either
single-versioned, sequentially superseded (the precedence rail resolves it), or
deduped to identical content. The sweep PINS that no two co-effective
distinct-content rows are admitted at the same address without precedence
resolution or a scope distinguisher. The synthetic unit-level bite is the
guard-liveness fire-drill.

Disposition (tag-don't-guess)
=============================
OBSERVATION-role, non-blocking — the SAME disposition as the prior totality
sweeps (SURF-*, EV-03, SCHED-*). A residual co-effective tie over a real corpus is
a REAL legal fact about the source (genuinely ambiguous co-effective text), to be
surfaced, not a pipeline crash; blocking would contradict tag-don't-guess and
could regress the corpus.

This sweep is PURE: it reads already-produced carriers and returns typed finding
records. It sits off the replay/apply path and mutates nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash

if TYPE_CHECKING:
    from lawvm.finland.replay_products import ReplayProducts

# ---------------------------------------------------------------------------
# Finding code (closed set; registered in core/observation_registry.py)
# ---------------------------------------------------------------------------

SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE = "SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE"


# ---------------------------------------------------------------------------
# Typed sweep finding (self-evidencing per AGENTS.md §1.8 / EV-07)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeOverlapFinding:
    """One SCOPE fact: a co-effective collision the precedence rail cannot resolve.

    A pair of versions at the SAME address that overlap in time, carry DISTINCT
    legal content, share the SAME precedence-rail rank key
    (``(effective, enacted, source_statute)`` — so lex-posterior ordering does
    NOT separate them), and are NOT admitted by disjoint scope predicates. The
    winner of such a pair would depend on list order, not a proved legal
    precedence or a scope distinction — the SCOPE-01 violation shape.

    Attributes:
        code:           ``SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE``.
        address:        The provision address whose timeline carries the pair
                        (string form for self-evidencing).
        effective:      The shared effective date of the co-effective pair.
        enacted:        The shared enacted date (rank-key component).
        source_statute: The shared source statute id (rank-key component).
        variant_kind:   The shared variant kind (overlay/background rail).
        candidate_count:
                        How many distinct-content rows share this rank key
                        (>= 2).
        left_content_hash:  Content hash of the first colliding row.
        right_content_hash: Content hash of a second, DISTINCT-content colliding
                        row (the evidence that the collision is real, not a
                        duplicate that dedupe should collapse).
        scope_disjoint: Whether disjoint scope predicates admit the overlap
                        (always ``False`` for an emitted finding; a disjoint pair
                        is silent). Carried for symmetry/audit.
        detail:         SELF-EVIDENCING message naming the address, rank key, and
                        the two distinct content hashes, so the finding is
                        auditable from the record alone.
    """

    code: str
    address: str
    effective: str
    enacted: str
    source_statute: str
    variant_kind: str
    candidate_count: int
    left_content_hash: str
    right_content_hash: str
    scope_disjoint: bool
    detail: str


# ---------------------------------------------------------------------------
# Internal: precedence-rail rank key + scope-disjointness predicate
# ---------------------------------------------------------------------------


def _rank_key(version: ProvisionVersion) -> tuple[str, str, str, str]:
    """The precedence-rail rank key for one version.

    Mirrors the production selection rank discriminator
    (``timeline_selection._VersionSelectionConflictKey``): two versions whose
    keys DIFFER are separated by lex-posterior ordering (``pick_latest`` chooses
    a deterministic winner); two versions whose keys are EQUAL are an equal-rank
    tie the rail does not resolve. Read-only: derives the key, never mutates.
    """
    source_statute = version.source.statute_id if version.source is not None else ""
    return (version.variant_kind, version.effective, version.enacted, source_statute)


def _content_hash(version: ProvisionVersion) -> str:
    """Stable content hash for a version (``<absent>`` for an empty/None body).

    Used to decide DISTINCT content: an equal-rank collision is only a real
    SCOPE fact when the colliding rows carry distinct legal content (otherwise
    timeline dedupe collapses them and there is nothing to disambiguate).
    """
    if version.content is None:
        return "<absent>"
    existing = version.content_hash
    if existing:
        return existing
    return irnode_content_hash(version.content)


def _scope_predicate_signature(version: ProvisionVersion) -> frozenset[tuple[str, frozenset[str]]]:
    """Canonical, hashable signature of a version's structured scope predicate.

    Every ``ScopePredicate`` (``dimension`` + ``includes``) is normalized to a
    ``(dimension, frozenset(includes))`` pair; the whole applicability set folds
    to a frozenset. An empty signature means "no scope distinguisher" (wildcard /
    applies-everywhere). This is the SCOPE-01 canonical-normalization surface
    restricted to what the carrier actually populates.
    """
    return frozenset(
        (predicate.dimension, frozenset(predicate.includes))
        for predicate in version.applicability
    )


def _scopes_admit_overlap(
    left: ProvisionVersion,
    right: ProvisionVersion,
) -> bool:
    """Whether disjoint scope predicates admit a co-effective overlap.

    Two co-effective rows may legitimately coexist if their scope predicates are
    DISJOINT (e.g. distinct ``territory`` includes-sets with no intersection on a
    shared dimension): the query's scope, not list order, chooses between them.
    A pair where EITHER row carries no scope predicate, or they share a dimension
    with overlapping includes, is NOT admitted by scope.

    Discipline: this only returns ``True`` when a STRUCTURED predicate actually
    separates the rows. Over the FI corpus no version carries a predicate, so this
    is always ``False`` there — the honest reflection of the missing carrier.
    """
    left_sig = _scope_predicate_signature(left)
    right_sig = _scope_predicate_signature(right)
    if not left_sig or not right_sig:
        # At least one row applies everywhere -> no scope distinction admits the
        # overlap. (Missing carrier: the corpus-wide case.)
        return False
    # Per shared dimension, disjoint includes-sets admit the overlap.
    left_by_dim = {dim: incl for dim, incl in left_sig}
    right_by_dim = {dim: incl for dim, incl in right_sig}
    shared_dims = set(left_by_dim) & set(right_by_dim)
    if not shared_dims:
        # No shared dimension to disjoin on -> not separable by scope.
        return False
    return all(
        left_by_dim[dim].isdisjoint(right_by_dim[dim]) for dim in shared_dims
    )


# ---------------------------------------------------------------------------
# SCOPE-01/02 — co-effective-collision precedence totality
# ---------------------------------------------------------------------------


def sweep_scope_lattice(
    products: "ReplayProducts",
) -> tuple[ScopeOverlapFinding, ...]:
    """Assert co-effective-collision precedence totality over one replay output.

    For each address in ``products.timelines``, group the versions by the
    precedence-rail rank key (``(variant_kind, effective, enacted,
    source_statute)``). A rank-key group holding two or more rows with DISTINCT
    legal content is an equal-rank collision the precedence rail does NOT resolve
    (SCOPE-01: the overlap is not chosen by lex posterior). If no disjoint scope
    predicate admits that overlap (SCOPE-02: there is no scope distinguisher to
    force ``ambiguous_missing_scope`` cleanly), the collision is
    ``SCOPE.OVERLAP_WITHOUT_DISJOINT_SCOPE`` (self-evidencing).

    Args:
        products: The already-produced :class:`ReplayProducts` (re-folds NOTHING,
                  re-applies NOTHING, re-selects NOTHING).

    Returns:
        A tuple of :class:`ScopeOverlapFinding`, sorted by
        ``(address, effective, enacted, source_statute, variant_kind)``. Empty
        when every co-effective collision is resolved by the precedence rail
        (distinct rank keys) or admitted by a disjoint scope predicate — the
        standing guard's clean state, which the FI corpus is in.

    Discipline (tag-don't-guess): the sweep NEVER mutates a timeline, fabricates a
    scope predicate, or re-orders versions. It reports the rows the final output
    actually carries.
    """
    timelines: Mapping[LegalAddress, ProvisionTimeline] = products.timelines or {}

    findings: list[ScopeOverlapFinding] = []
    for address, timeline in timelines.items():
        versions = list(timeline.versions)
        if len(versions) < 2:
            continue
        # Group by precedence-rail rank key; only EQUAL keys are unresolved by
        # lex-posterior ordering.
        groups: dict[tuple[str, str, str, str], list[ProvisionVersion]] = {}
        order: list[tuple[str, str, str, str]] = []
        for version in versions:
            key = _rank_key(version)
            bucket = groups.get(key)
            if bucket is None:
                groups[key] = [version]
                order.append(key)
            else:
                bucket.append(version)

        for key in order:
            bucket = groups[key]
            if len(bucket) < 2:
                continue
            collision = _distinct_content_collision(bucket)
            if collision is None:
                # All rows in the group share content -> timeline dedupe collapses
                # them; no co-effective ambiguity to surface.
                continue
            left, right = collision
            if _scopes_admit_overlap(left, right):
                # SCOPE-01 satisfied the disjoint way: distinct scope predicates
                # admit the overlap; the query's scope chooses, not list order.
                continue
            variant_kind, effective, enacted, source_statute = key
            left_hash = _content_hash(left)
            right_hash = _content_hash(right)
            findings.append(
                ScopeOverlapFinding(
                    code=SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE,
                    address=str(address),
                    effective=effective,
                    enacted=enacted,
                    source_statute=source_statute,
                    variant_kind=variant_kind,
                    candidate_count=len(bucket),
                    left_content_hash=left_hash,
                    right_content_hash=right_hash,
                    scope_disjoint=False,
                    detail=(
                        f"co-effective collision at {str(address)!r}: "
                        f"{len(bucket)} versions share precedence-rail rank key "
                        f"(variant={variant_kind}, effective={effective}, "
                        f"enacted={enacted or '<none>'}, "
                        f"source={source_statute or '<none>'}) with distinct content "
                        f"({left_hash[:12]} != {right_hash[:12]}) and NO disjoint "
                        f"scope predicate admits the overlap: the winner would depend "
                        f"on list order, not a proved legal precedence or a scope "
                        f"distinction"
                    ),
                )
            )
    findings.sort(
        key=lambda f: (
            f.address,
            f.effective,
            f.enacted,
            f.source_statute,
            f.variant_kind,
        )
    )
    return tuple(findings)


def _distinct_content_collision(
    versions: list[ProvisionVersion],
) -> Optional[tuple[ProvisionVersion, ProvisionVersion]]:
    """Return two same-rank rows with DISTINCT content, or None if all share content.

    A real co-effective ambiguity needs at least two rows whose legal content
    differs; rows that share a content hash are duplicates the timeline dedupe
    collapses and carry no disambiguation burden. Returns the first such distinct
    pair (deterministic: list order), used as the finding's evidence.
    """
    first = versions[0]
    first_hash = _content_hash(first)
    for other in versions[1:]:
        if _content_hash(other) != first_hash:
            return (first, other)
    return None


__all__ = [
    "SCOPE_OVERLAP_WITHOUT_DISJOINT_SCOPE",
    "ScopeOverlapFinding",
    "sweep_scope_lattice",
]
