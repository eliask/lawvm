"""Landed-write receipts for the semantic apply plane.

A WriteReceipt is the apply helper's record of what ACTUALLY landed — not
what it intended. It is produced at the write boundary, from landed reality,
and is the single producer that mutation events and certificate transition
leaves project from (contract: notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md
§4). The normative replay principle it serves:

    The address you bind is the address you write, and the address you
    write is the address you declare.

A divergence between ``bound_target_path`` (from the ResolverBinding) and
``landed_primary_path`` MUST be explained by a named rule in
``recovery_rule_ids`` / ``migration_rule_ids`` / ``fallback_rule_ids``.
Unexplained divergence is a mutation-boundary failure for strict mode to
block on — it is deliberately NOT a constructor error, because the receipt
must be able to record a bad write truthfully so the audit can see it.

A receipt alone is not a proof: the independent observed before/after diff
(ObservedWriteAudit, contract §5) is what catches lying or incomplete
receipts. This module carries only the producer-side record.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Mapping, Optional

from lawvm.core.mutation_boundary import RenumberedTreePaths, TreePath, TreePaths

if TYPE_CHECKING:
    from lawvm.core.provenance import SourceAnchor


def receipt_address_string(path: TreePath) -> str:
    """Slash-joined ``kind:label`` address grammar for receipt hash keys.

    Mirrors the certified-transition address grammar
    (CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.3).
    """
    return "/".join(f"{kind}:{label}" for kind, label in path)


class DivergenceKind(str, Enum):
    """Classification of a receipt's bound→landed divergence (PR2).

    A receipt's ``divergence_kind`` is the typed witness for HOW its
    bound→landed relation was resolved by the receipt-boundary arm of
    :func:`finland.apply_replay_authorization._receipt_boundary_authorized`.
    It is computed at the receipt-construction site
    (``finland.apply_resolved_op._collect_op_write_receipt``) once the
    canonical-form (wrapper-strip + kind-alias) bound/landed paths are
    available, then carried on the receipt so the receipt-boundary arm
    and certificate consumers can read the classification without
    re-deriving it (per §1.12 — no semantic reach-back from a lossier
    representation once a typed owner exists).

    ``None`` (the default) signals "not computed" (legacy / pre-PR2
    receipts); the receipt arm falls back to recomputing
    :func:`_paths_consistent_under_prefix` defensively. A typed value
    is the audit witness the §0 prime directive demands for any
    authority-bearing relation: ``PREFIX_OF_LANDED`` carries a paired
    ``RECEIPT_BOUND_PREFIX_OF_LANDED`` observation row so the prefix
    authorization is OWNED, not silent.
    """

    #: bound and landed paths reconcile as the same canonical address.
    EXACT_MATCH = "exact_match"
    #: bound != landed but a named recovery/migration/fallback rule explains it.
    EXPLAINED_BY_RULE = "explained_by_rule"
    #: bound is a strict prefix of landed (or vice versa); benign per the
    #: receipt-prefix-equivalence rule (``receipt_prefix_equivalence``,
    #: family ``presentation_cleanup``), witnessed by an emitted
    #: ``APPLY.RECEIPT_BOUND_PREFIX_OF_LANDED`` observation row.
    PREFIX_OF_LANDED = "prefix_of_landed"
    #: bound and landed diverge in a non-prefix way; the receipt's
    #: mutation-boundary divergence is unexplained (strict mode blocks).
    UNEXPLAINED_DIVERGENCE = "unexplained_divergence"


def _paths_consistent_under_prefix(
    bound: TreePath,
    landed: TreePath,
    *,
    normalize_fn: Optional[Callable[[TreePath], TreePath]] = None,
) -> bool:
    """Return True when one path is a strict prefix of the other (either direction).

    PR2 receipt-prefix-equivalence rule (``receipt_prefix_equivalence``,
    family ``presentation_cleanup`` — a structural-diff-level surface
    normalization, not a legal-state mutation). The 71+15 Pattern-A/B
    false-positives catalogued in
    ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` §1.1 / §1.2 have one path
    nested under the other after canonical-form normalization
    (wrapper-strip + kind-alias), so the receipt's tuple-equality
    ``divergence_explained`` check needs a prefix-or-equal tolerance to
    authorize them. The helper lives alongside
    :func:`WriteReceipt.divergence_explained`'s owner module so the receipt
    boundary check has its own typed sibling of
    ``core.mutation_boundary.observed_paths_explained_by_declared`` and
    ``tools.certificate_bundle._address_explains`` — NOT a new predicate
    family (§2.6 rule-of-three crystallization: those callers + this arm
    all want the same prefix-tolerance fact, so the receipt-side host is
    the typed sibling).

    The relation is BENIGN-by-relation-shape: a prefix mismatch alone does
    NOT silently authorize replay. ``_receipt_boundary_authorized`` emits a
    ``RECEIPT_BOUND_PREFIX_OF_LANDED`` observation row carrying the
    bound/landed pair as the witness, AND defers to the undeclared-touch
    cross-check (``no_boundary_violation`` in
    :func:`aggregate_replay_authority`) — if the cross-check is dirty the
    aggregate refuses authorization regardless of the prefix relation. The
    observation is the visible audit witness; the cross-check is the
    load-bearing independent witness.

    ``normalize_fn`` is OPTIONAL: when the caller has already pushed both
    paths through ``finland._receipt_path_norm._normalize_receipt_path_for_comparison``
    (the receipt-construction site in ``apply_resolved_op`` does this), the
    helper accepts the pre-normalized form. When the caller passes raw
    paths and a ``normalize_fn`` callable, the helper normalizes both sides
    BEFORE the prefix check. This keeps PR2's helper reusable from unit
    tests that construct raw bound/landed paths without duplicating the
    canonicalization pipeline (per AGENTS.md §1.12 — no semantic reach-back
    from a raw form once a typed owner exists; the helper either trusts the
    already-typed input or applies the typed normalization itself).

    Returns True when either path is a strict prefix of the other. Returns
    False when they diverge in a non-prefix way (e.g.,
    ``(("section","5"),)`` vs ``(("chapter","3"), ("section","1"),
    ("subsection","1"), ("paragraph","7"))`` — neither is a prefix of the
    other). Equal paths return False (the EXACT_MATCH case is the receipt-
    boundary arm's existing ``bound == landed`` short-circuit; this helper
    classifies STRICT prefix-of only).
    """
    a = normalize_fn(bound) if normalize_fn is not None else bound
    b = normalize_fn(landed) if normalize_fn is not None else landed
    if a == b:
        # Equal paths fall under EXACT_MATCH, not the prefix relation.
        return False
    if len(a) < len(b) and b[: len(a)] == a:
        return True  # bound is a strict prefix of landed (Pattern B)
    if len(b) < len(a) and a[: len(b)] == b:
        return True  # landed is a strict prefix of bound (Pattern A)
    return False


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    """Record of one landed semantic write (contract §4).

    ``pre_hashes`` / ``post_hashes`` are canonical structural subtree hashes
    (``lawvm.core.ir_helpers.structural_subtree_hash``) of every touched
    covering unit, keyed by ``receipt_address_string`` — computed AT the
    write, never reconstructed later. ``""`` is the hash of an absent
    subtree.
    """

    op_id: str
    helper: str
    action: str
    bound_target_path: TreePath | None
    landed_primary_path: TreePath | None

    created_paths: TreePaths = ()
    replaced_paths: TreePaths = ()
    removed_paths: TreePaths = ()
    consumed_paths: TreePaths = ()
    renumbered_paths: RenumberedTreePaths = ()

    placeholder_created_paths: TreePaths = ()
    placeholder_consumed_paths: TreePaths = ()

    recovery_rule_ids: tuple[str, ...] = ()
    migration_rule_ids: tuple[str, ...] = ()
    fallback_rule_ids: tuple[str, ...] = ()

    pre_hashes: Mapping[str, str] = field(default_factory=dict)
    post_hashes: Mapping[str, str] = field(default_factory=dict)

    # PR2 receipt-prefix-equivalence (BOUND_TARGET_PATH_NORMALIZATION_DESIGN §3):
    # typed witness for HOW the bound→landed relation was resolved. Set at the
    # receipt-construction site (``apply_resolved_op._collect_op_write_receipt``)
    # once the canonical-form paths are compared; consumed by
    # ``_receipt_boundary_authorized`` and the certificate consumers. ``None``
    # signals "not computed" (legacy / pre-PR2 receipts) and the receipt arm
    # falls back to recomputing ``_paths_consistent_under_prefix`` defensively.
    divergence_kind: Optional[DivergenceKind] = None

    # Byte-level anchor of the source clause (johtolause) that drove this write,
    # in the raw amendment source bytes. Present only when the parse captured a
    # genuine verbatim contiguous span; None (fail-loud) otherwise. Carries the
    # source provenance the certificate's source_anchor needs (trace spec §7).
    source_anchor: "SourceAnchor | None" = None

    @property
    def declared_footprint(self) -> TreePaths:
        """Union of every path this receipt declares as touched."""
        renumber_legs: list[TreePath] = []
        for from_path, to_path in self.renumbered_paths:
            renumber_legs.append(from_path)
            renumber_legs.append(to_path)
        return tuple(
            dict.fromkeys(
                (
                    *self.created_paths,
                    *self.replaced_paths,
                    *self.removed_paths,
                    *self.consumed_paths,
                    *renumber_legs,
                    *self.placeholder_created_paths,
                    *self.placeholder_consumed_paths,
                )
            )
        )

    @property
    def named_rule_ids(self) -> tuple[str, ...]:
        return (
            *self.recovery_rule_ids,
            *self.migration_rule_ids,
            *self.fallback_rule_ids,
        )

    @property
    def divergence_explained(self) -> bool:
        """True when bound==landed, or the divergence carries a named rule.

        ``False`` means an unexplained mutation-boundary divergence: strict
        mode must turn this into a blocking residual (contract §4); it is
        never silently acceptable.
        """
        if self.bound_target_path == self.landed_primary_path:
            return True
        return bool(self.named_rule_ids)
