"""Finland-local receipt-side path canonicalization (Wave N3a PR1).

Single source of truth for two pure-form canonicalizations the op-level
``WriteReceipt.bound_target_path`` needs to reconcile with the IR-diff
``landed_primary_path`` under the receipt's tuple-equality divergence check
(``WriteReceipt.divergence_explained``):

1. Wrapper-root strip — drop a single leading ``("hcontainer", "")`` body-root
   wrapper step that the FI replay IR carries (constructed by
   ``replay_products._ensure_body_hcontainer``) but op-nominal
   ``LegalAddress`` target paths do not. Mirrors
   ``core.mutation_accounting._WRAPPER_ROOT_STEP`` / ``_strip_wrapper_root``;
   the receipt-side host keeps its own copy so the receipt-boundary arm
   does not couple to the undeclared-touch cross-check module.
2. Kind-alias reconciliation — rewrite legal-address vocabulary kinds
   (``item`` / ``subitem`` for kohta / alakohta) to the IR-kind vocabulary
   the diff emits (``paragraph`` / ``subparagraph``). A kohta is stored as
   :data:`IRNodeKind.PARAGRAPH` in the FI IR (witness:
   ``apply_ir_ops._relabel_item_ir``). A subitem is
   :data:`IRNodeKind.SUBPARAGRAPH`. The legal-address path uses ``item`` /
   ``subitem``; the IR diff path uses ``paragraph`` / ``subparagraph`` for
   the same nodes — Pattern C per ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN``
   §1.3.

Both canonicalizations are pure-form aliasing — no new authority relation is
minted, no information is lost (the label of every path step is preserved;
only the kind label is rewritten to the IR vocabulary). They exist so
``bound_target_path`` threaded from a rop's ``resolved_target_address`` (per
``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` §3 PR1) clears the 29 Pattern-C
kind-label-mismatch false-positives on the green corpus ``1997/1339`` (count
115 → 86). The 71+15 Pattern-A/B prefix-count cases (86) remain surfaced as
false-positives pending PR2's prefix-equivalence rule.

Cross-jurisdiction note (§2.3): the alias map is Finland-local — ``item`` ↔
``IRNodeKind.PARAGRAPH`` is a finland IR convention; a globally-canonical
kind vocabulary is explicitly deferred per AGENTS.md §2.3. This module is the
receipt-side host; ``payload_realization_audit._TARGET_NODE_KINDS`` re-imports
the shared map so the fact is owned in one place (rule-of-three per §2.6:
payload-realization audit, receipt-construction, and the future PR2
prefix-equivalence helper all want the same alias fact).
"""

from __future__ import annotations

from collections.abc import Mapping

from lawvm.core.mutation_boundary import TreePath
from lawvm.core.semantic_types import IRNodeKind

# The FI replay IR is rooted under an unlabeled ``("hcontainer", "")`` wrapper
# (constructed by ``replay_products._ensure_body_hcontainer``). Tree-diff paths
# carry this leading step while op-nominal ``LegalAddress`` target paths do
# not. Mirrors ``core.mutation_accounting._WRAPPER_ROOT_STEP``; the receipt
# host keeps its own copy so the receipt-boundary arm stays decoupled from
# the undeclared-touch cross-check module (both must agree on the wrapper shape).
_FI_RECEIPT_WRAPPER_ROOT_STEP: TreePath = (("hcontainer", ""),)


# Finland-local legal-address-vocabulary → IR-kind-vocabulary equivalence map.
# A kohta (legal-address ``item``) is stored in the FI IR as EITHER
# :data:`IRNodeKind.ITEM` (the generic name) OR — the production convention —
# :data:`IRNodeKind.PARAGRAPH` (the FI kohta carrier, per
# ``apply_ir_ops._relabel_item_ir``). The receipt-side canonicalization picks
# the canonical IR form via :data:`_FI_KIND_ALIAS_CANONICAL_IR_STR` below; this
# map is the equivalence relation (used by :func:`_reconcile_kind_alias`).
#
# Single source of truth — ``payload_realization_audit._TARGET_NODE_KINDS``
# imports from this constant rather than keeping its own copy, so the alias
# fact lives in exactly one place (rule-of-three per §2.6).
_FI_KIND_ALIAS_TO_IR: Mapping[str, frozenset[IRNodeKind]] = {
    "part": frozenset({IRNodeKind.PART}),
    "chapter": frozenset({IRNodeKind.CHAPTER}),
    "section": frozenset({IRNodeKind.SECTION}),
    "subsection": frozenset({IRNodeKind.SUBSECTION}),
    "item": frozenset({IRNodeKind.ITEM, IRNodeKind.PARAGRAPH}),
    "subitem": frozenset({IRNodeKind.SUBPARAGRAPH}),
}


# Canonical IR-kind string for each legal-address-vocabulary kind. The FI IR
# convention (witness: ``apply_ir_ops._relabel_item_ir`` documents "An ``item``
# (kohta) is a :data:`IRNodeKind.PARAGRAPH` node carrying a visible ``N)``
# marker") picks :data:`IRNodeKind.PARAGRAPH` for kohta and
# :data:`IRNodeKind.SUBPARAGRAPH` for alakohta. Used by
# :func:`_normalize_receipt_path_for_comparison` to rewrite the bound path
# (from the rop's legal-address vocabulary) into the IR-kind vocabulary the
# diff's ``landed_primary_path`` already uses, so the tuple-equality
# divergence check compares canonical-form paths. Used symmetrically on the
# landed side (a no-op there — the diff already emits IR-kind vocabulary).
_FI_KIND_ALIAS_CANONICAL_IR_STR: Mapping[str, str] = {
    "part": IRNodeKind.PART.value,
    "chapter": IRNodeKind.CHAPTER.value,
    "section": IRNodeKind.SECTION.value,
    "subsection": IRNodeKind.SUBSECTION.value,
    "item": IRNodeKind.PARAGRAPH.value,
    "subitem": IRNodeKind.SUBPARAGRAPH.value,
}


def _strip_wrapper_root(path: TreePath) -> TreePath:
    """Drop a single leading unlabeled ``("hcontainer", "")`` wrapper step.

    Mirrors ``core.mutation_accounting._strip_wrapper_root``. The receipt-side
    host keeps its own copy so the receipt-boundary arm does not couple to the
    undeclared-touch cross-check module; both must agree on the wrapper shape
    (a single leading unlabeled ``hcontainer`` step is dropped; a NAMED
    ``("hcontainer", "<label>")`` step used by some provisions-wrapper
    subtrees is preserved — only the body-root wrapper is stripped).
    """
    if path[: len(_FI_RECEIPT_WRAPPER_ROOT_STEP)] == _FI_RECEIPT_WRAPPER_ROOT_STEP:
        return path[len(_FI_RECEIPT_WRAPPER_ROOT_STEP):]
    return path


def _reconcile_kind_alias(kind: str) -> frozenset[IRNodeKind]:
    """Return the IR-kind equivalence set for a legal-address-vocabulary kind.

    A single-element frozenset for kinds whose legal-address name matches the
    IR kind name exactly (``part`` / ``chapter`` / ``section`` /
    ``subsection``). For ``item`` (kohta) the set is ``{ITEM, PARAGRAPH}``
    because the legal-address vocabulary uses ``item`` for both, while the FI
    IR concretely stores the kohta as :data:`IRNodeKind.PARAGRAPH`. An empty
    frozenset for kinds not in the alias map (``hcontainer`` / ``heading`` /
    ``content`` / etc., which are already in IR-kind vocabulary).

    Used by the receipt-side comparison to recognize that two path steps with
    kind labels in the same equivalence set identify the same node (the
    canonical-direction rewrite picks one canonical form via
    :data:`_FI_KIND_ALIAS_CANONICAL_IR_STR`).
    """
    return _FI_KIND_ALIAS_TO_IR.get(kind, frozenset())


def _reconcile_path_step_kind(kind: str) -> str:
    """Return the canonical IR-kind string for a path step's kind label.

    No-op for kinds already in IR-kind vocabulary (``paragraph`` / ``section``
    / ``hcontainer`` etc., which are not keys in the legal-address alias map).
    For ``item`` / ``subitem`` returns ``paragraph`` / ``subparagraph``
    respectively (the FI IR's actual storage kind for kohta / alakohta).

    This is the canonical-direction step (the rewrite that aligns rop-bound
    paths with the IR-kind-vocabulary paths the diff emits); paired with
    :func:`_reconcile_kind_alias` which exposes the equivalence relation.
    """
    return _FI_KIND_ALIAS_CANONICAL_IR_STR.get(kind, kind)


def _normalize_receipt_path_for_comparison(path: TreePath) -> TreePath:
    """Apply the receipt-side canonical form: wrapper-strip + kind-alias rewrite.

    Used by ``apply_resolved_op._collect_op_write_receipt`` on BOTH the
    bound-side path (rop's ``resolved_target_address.path``) AND the
    landed-side path (the IR-diff's first observed path) so the receipt's
    tuple-equality ``divergence_explained`` check compares canonical-form
    paths. Both transformations are pure-form aliasing — no information is
    lost (the label of every step is preserved; only the kind label is
    rewritten to the IR vocabulary and a single leading unlabeled body-root
    wrapper step is stripped).

    Order (per ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` §2): wrapper-strip
    first (the leading ``("hcontainer", "")`` step's kind is not in the
    alias map, so the kind-alias rewrite is a no-op for it either way — the
    two transformations commute, but the design doc names wrapper-strip
    first).
    """
    stripped = _strip_wrapper_root(path)
    return tuple(
        (_reconcile_path_step_kind(kind), label) for kind, label in stripped
    )
