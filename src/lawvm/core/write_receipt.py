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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

from lawvm.core.mutation_boundary import RenumberedTreePaths, TreePath, TreePaths

if TYPE_CHECKING:
    from lawvm.core.provenance import SourceAnchor


def receipt_address_string(path: TreePath) -> str:
    """Slash-joined ``kind:label`` address grammar for receipt hash keys.

    Mirrors the certified-transition address grammar
    (CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.3).
    """
    return "/".join(f"{kind}:{label}" for kind, label in path)


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
