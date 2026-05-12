"""Shared ClauseAST bridge surface for legal compiler core.

This module defines the shared clause-surface contract used by core analysis and
jurisdiction-agnostic adapters.

- ClauseAST container nodes (`ClauseAST`, `VerbGroup`, `ScopedBlock`)
- structural/label/text/effect nodes used by shared legal-intent conversion
  (`RefAmend`, `LabelAmend`, `TextAmend`, `MetaClause`)

Cross-jurisdiction interoperability is handled by explicit adapter functions at
the bottom of this module:

- `legal_op_to_clause_node` and `clause_ast_to_legal_ops` (shared typed bridge)

These adapters are intentionally narrow. They are not where shared semantics are
defined; they provide explicit conversion where shared semantics exist and make
lossy/unmapped fields explicit instead of silently smuggling them through
bridge payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

from lawvm.core.ir import (
    LegalAddress,
    LegalOperation,
    TextPatchSpec,
)
from lawvm.core.semantic_types import FacetKind, LabelAction, MetaClauseKind, StructuralAction


# ============================================================================
# Shared Clause Kernel + Bridge Inputs
# ============================================================================


@dataclass(frozen=True)
class ScopedBlock:
    """Wraps child ops that share a containing scope (chapter, part, etc.).

    Represents clauses like "2 luvun 3 ja 4 §" as:
        ScopedBlock(scope=LegalAddress([("chapter", "2")]),
                    children=(RefAmend("replace", section:3),
                               RefAmend("replace", section:4)))

    The scope is the address of the containing element (not the target
    elements themselves). Children are the ops performed within that scope.
    """

    # ParsedOp mapping: chapter/part fields that establish a shared context
    # for multiple ops — currently implicit in ParsedOp, explicit here.
    scope: LegalAddress
    children: Tuple["ClauseNode", ...]

    def __post_init__(self) -> None:
        if not self.scope.path:
            raise ValueError("ScopedBlock.scope must have a non-empty path")
        if self.scope.special is not None:
            raise ValueError("ScopedBlock.scope must not target a facet")
        if not self.children:
            raise ValueError("ScopedBlock.children must be non-empty")
        if any(isinstance(child, ScopedBlock) for child in self.children):
            raise ValueError("ScopedBlock.children must not contain nested ScopedBlock nodes")


@dataclass(frozen=True)
class RefAmend:
    """Structural reference amendment — the most common clause node type.

    Covers kumotaan, muutetaan, lisätään targeting whole provisions
    (sections, chapters, subsections, items, appendices).

    ParsedOp mapping:
      verb=K → action=StructuralAction.REPEAL
      verb=M → action=StructuralAction.REPLACE
      verb=L → action=StructuralAction.INSERT
      kind=P/L/O/N/A + number → target (address built from ParsedOp fields)
      anchor: insertion point for insert ops (not in ParsedOp; added later)
    """

    action: StructuralAction  # StructuralAction.REPLACE | REPEAL | INSERT
    target: LegalAddress
    anchor: Optional[LegalAddress] = None  # insertion anchor (for insert ops)
    notes: Tuple[str, ...] = ()  # annotation notes (e.g. renumber_clause)
    is_exception: bool = False  # True when target is a "lukuun ottamatta" exclusion from a broader range
    source_tokens: Optional[Tuple[int, int]] = None  # (start, end) in filtered stream
    witness_rule_id: Optional[str] = None  # construction rule that produced this op (diagnostic)
    resolution_kind: Optional[str] = None  # how the target was resolved (e.g. "backref_singular", "pass_through")
    resolution_detail: Optional[str] = None  # antecedent label/chapter for backref/valiotsikko resolutions

    def __post_init__(self) -> None:
        if self.action not in {
            StructuralAction.REPLACE,
            StructuralAction.REPEAL,
            StructuralAction.INSERT,
        }:
            raise ValueError(f"RefAmend requires structural replace/repeal/insert action, got {self.action!r}")
        if not self.target.path:
            raise ValueError("RefAmend.target must have a non-empty path")
        if self.anchor is not None and self.action is not StructuralAction.INSERT:
            raise ValueError("RefAmend.anchor is only valid for insert actions")


@dataclass(frozen=True)
class TextAmend:
    """Word-level text patch within a provision.

    Used for word-level replacement clauses. Future use; one adapter already
    emits these.

    ParsedOp mapping:
      No direct equivalent in current ParsedOp (text ops are not yet parsed
      by the current parse layers). This node type exists for text-level
      operations and future text patch support.
    """

    action: StructuralAction  # StructuralAction.TEXT_REPLACE or TEXT_REPEAL
    target: LegalAddress  # provision containing the text; empty path means statute-wide/root text scope
    text_patch: TextPatchSpec  # authoritative structured text patch

    def __post_init__(self) -> None:
        if self.action not in {
            StructuralAction.TEXT_REPLACE,
            StructuralAction.TEXT_REPEAL,
        }:
            raise ValueError(
                f"TextAmend requires text_replace/text_repeal action, got {self.action!r}"
            )


@dataclass(frozen=True)
class LabelAmend:
    """Label, heading, or renumber/move change.

    Covers verb=S (renumber/move) and special="otsikko" (heading replace).

    Move semantics (from → to):
      target:      the SOURCE address — where the provision currently lives.
      destination: the DESTINATION address — where it moves to.

    Both addresses are preserved through the parser-facing bridge and
    lowering path:
      LabelAmend.target/destination → LegalOperation.target/destination
        → CanonicalIntent Relabel.source/destination (or Move.source/destination_parent)

    This ensures that move operations are explicit from→to pairs, not
    address mutations that lose the source address.

    ParsedOp mapping:
      verb=S                    → action="renumber"
      special="otsikko"         → action="heading_replace"
      number/chapter/etc.       → target (source address from ParsedOp fields)
      renumber_dest/etc.        → destination (built from dest fields)
      new_label: the leaf label of the destination
    """

    action: LabelAction  # LabelAction.RENUMBER | LabelAction.HEADING_REPLACE
    target: LegalAddress
    new_label: Optional[str] = None  # the new label/number/heading text
    destination: Optional[LegalAddress] = None  # full destination address (the "to" in from→to)
    notes: Tuple[str, ...] = ()  # annotation notes (e.g. renumber_clause)
    source_tokens: Optional[Tuple[int, int]] = None
    witness_rule_id: Optional[str] = None  # construction rule that produced this op (diagnostic)
    resolution_kind: Optional[str] = None  # how the target was resolved (e.g. "backref_singular", "pass_through")
    resolution_detail: Optional[str] = None  # antecedent label/chapter for backref/valiotsikko resolutions

    def __post_init__(self) -> None:
        if not self.target.path:
            raise ValueError("LabelAmend.target must have a non-empty path")

        if self.action is LabelAction.HEADING_REPLACE:
            if self.target.special is not FacetKind.HEADING:
                raise ValueError("LabelAmend heading_replace requires a heading target")
            if self.destination is not None and not self.destination.path:
                raise ValueError("LabelAmend.destination must have a non-empty path when provided")
            if self.new_label is not None and self.destination is not None:
                dest_label = self.destination.path[-1][1]
                if self.new_label != dest_label:
                    raise ValueError(
                        "LabelAmend heading_replace new_label must match destination leaf label"
                    )
            return

        if self.action is not LabelAction.RENUMBER:
            raise ValueError(f"Unsupported LabelAmend action {self.action!r}")

        if self.destination is None and not self.new_label:
            raise ValueError("LabelAmend renumber requires destination or new_label")
        if self.destination is not None:
            if not self.destination.path:
                raise ValueError("LabelAmend.destination must have a non-empty path when provided")
            if self.target.leaf_kind() != self.destination.leaf_kind():
                raise ValueError("LabelAmend destination must preserve target leaf kind")
            if self.new_label is not None:
                dest_label = self.destination.path[-1][1]
                if self.new_label != dest_label:
                    raise ValueError("LabelAmend new_label must match destination leaf label")


@dataclass(frozen=True)
class MetaClause:
    """Escape hatch for non-structural clause content.

    Captures entry-into-force, transition, delegation, and any other clause
    that is not a direct structural amendment op.
    These clauses have legal significance but do not map to tree_ops.

    ParsedOp mapping:
      No direct equivalent — MetaClause captures content that the current
      Some parse stages either ignore or emit as a non-structural note annotation. It is the explicit
      representation of "something important that is not a structural op."
    """

    kind: MetaClauseKind  # MetaClauseKind.COMMENCEMENT | .TRANSITION | .DELEGATION | .OTHER etc.
    raw_text: str

    def __post_init__(self) -> None:
        if not self.raw_text:
            raise ValueError("MetaClause.raw_text must be non-empty")


@dataclass(frozen=True)
class ItemShiftClause:
    """Bridge node for coordinated item renumbering after a repeal.

    This remains in core as an explicit bridge contract while frontend lowering
    owns the bridge policy (including how to expand source/target ranges).

    Captures clauses like ``jolloin kohdat e–h muuttuvat kohdiksi d–g``
    (``when items e–h become items d–g``). This is a structural amendment
    that renumbers existing items after one or more items have been repealed.

    The semantic meaning is: after the repeal(s) in the same verb group,
    shift the lettered items in the given range down by the appropriate
    offset.

    Current lowering shape:
      Parsed in clause-surface handling and lowered onto the typed
      ``post_repeal_item_shift_label`` carrier. This family no longer relies
      on ``resolution_hint`` as its semantic transport.
    """

    source_items: Tuple[str, ...]  # items being shifted (e.g. ("e", "f", "g", "h"))
    target_items: Tuple[str, ...]  # target labels (e.g. ("d", "e", "f", "g"))
    target_paragraph: Optional[int] = None  # which moment/paragraph
    target_section: Optional[str] = None  # which section (if not current)

    def __post_init__(self) -> None:
        if not self.source_items:
            raise ValueError("ItemShiftClause.source_items must be non-empty")
        if not self.target_items:
            raise ValueError("ItemShiftClause.target_items must be non-empty")
        if len(self.source_items) != len(self.target_items):
            raise ValueError("ItemShiftClause source_items and target_items must have equal length")


@dataclass(frozen=True)
class NamedRowClause:
    """Bridge node for named-entity table row amendments.

    This remains in core as an explicit bridge contract while frontend lowering
    owns the bridge policy for named-row matching and row-level effects.

    Captures clauses like:
    ``kumotaan päätöksen 1 §:n Iitin ja Juvan käräjäoikeuksia koskevat kohdat
    sekä muutetaan Kouvolan ja Mikkelin käräjäoikeuksia koskevat kohdat``

    The targets are named entities (court names, municipality names, etc.)
    that must be matched against ``row_anchor`` attributes in the live
    statute's table rows, not numeric section/subsection/item addresses.

    Current lowering shape:
      Parsed in clause-surface handling and lowered through the typed
      ``named_row_targets`` carrier. Any remaining supplements are
      bridge adapters, not the semantic owner of this family.
    """

    action: StructuralAction  # "repeal" | "replace"
    named_targets: Tuple[str, ...]  # normalized entity names (e.g. ("iitin", "juvan"))
    target_section: Optional[str] = None  # section containing the table
    target_paragraph: Optional[int] = None  # moment/paragraph within section

    def __post_init__(self) -> None:
        if self.action not in {StructuralAction.REPLACE, StructuralAction.REPEAL}:
            raise ValueError(
                f"NamedRowClause requires structural replace/repeal action, got {self.action!r}"
            )
        if not self.named_targets:
            raise ValueError("NamedRowClause.named_targets must be non-empty")
        if any(not target for target in self.named_targets):
            raise ValueError("NamedRowClause.named_targets must not contain empty names")


# ============================================================================
# Union type alias
# ============================================================================

ClauseNode = Union[ScopedBlock, RefAmend, TextAmend, LabelAmend, MetaClause, ItemShiftClause, NamedRowClause]


CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_KIND = "LOWER.CLAUSE_AST_NODE_UNSUPPORTED_GENERIC_LOWERING"
CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_RULE_ID = "core.clause_ast.unsupported_generic_lowering.v1"


@dataclass(frozen=True)
class ClauseAstLoweringDiagnostic:
    """Diagnostic emitted when generic ClauseAST lowering cannot own a node."""

    kind: str
    rule_id: str
    phase: str
    family: str
    node_kind: str
    sequence: int
    reason: str
    blocking: bool
    strict_disposition: str
    quirks_disposition: str
    scope: Optional[LegalAddress] = None
    detail: Optional[str] = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "kind": self.kind,
            "rule_id": self.rule_id,
            "phase": self.phase,
            "family": self.family,
            "node_kind": self.node_kind,
            "sequence": self.sequence,
            "reason": self.reason,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }
        if self.scope is not None:
            result["scope"] = str(self.scope)
        if self.detail is not None:
            result["detail"] = self.detail
        return result


# ============================================================================
# Grouping and top-level container
# ============================================================================


@dataclass(frozen=True)
class VerbGroup:
    """All clause nodes governed by one verb in the johtolause.

    A single johtolause may contain multiple verb sections:
      "muutetaan 3 §, kumotaan 5 §, lisätään 7 §"
    Each verb clause becomes one VerbGroup.

    verb uses the neutral structural action vocabulary:
      "repeal" | "replace" | "insert" | "renumber" | "meta"
    """

    verb: StructuralAction
    nodes: Tuple[ClauseNode, ...]


@dataclass(frozen=True)
class ClauseAST:
    """Top-level container for a parsed johtolause.

    A johtolause is the leading clause of an amending act that lists all
    structural amendments the act makes. ClauseAST preserves the full
    nested structure of that clause: verb groups, scoped blocks, and
    individual operation nodes.

    source_text is the original johtolause string (for debugging and
    round-trip verification). verb_groups is ordered as they appear in
    the source.
    """

    source_text: str
    verb_groups: Tuple[VerbGroup, ...]


def legal_op_to_clause_node(op: LegalOperation) -> ClauseNode:
    """Convert a LegalOperation to a ClauseNode.

    This is the reverse of clause_node_to_legal_operation() and enables
    jurisdiction-agnostic semantic round-tripping through ClauseAST. Any
    jurisdiction that produces LegalOperations can convert them to
    ClauseAST nodes for common analysis, display, or transformation.

    The bridge is intentionally lossy. Shared ClauseNode carriers do not own legal-op
    provenance, so fields like ``source``, ``applicability``, ``group_id``, and
    ``op_id`` are intentionally unsupported in this conversion.

    Mapping:
        action in {"text_replace", "text_repeal"} → TextAmend
        action="renumber"     → LabelAmend(action="renumber")
        action="heading_replace" → LabelAmend(action="heading_replace")
        special="heading"     → LabelAmend(action="heading_replace")
        otherwise             → RefAmend
    """
    target = op.target

    # Text-level amendments (EE/other: textual replace/repeal).
    if op.action in (StructuralAction.TEXT_REPLACE, StructuralAction.TEXT_REPEAL):
        patch = op.text_patch
        if patch is None:
            raise ValueError(
                "legal_op_to_clause_node requires explicit text_patch for text patch operations"
            )
        return TextAmend(
            action=op.action,
            target=target,
            text_patch=patch,
        )

    # Renumber
    if op.action is StructuralAction.RENUMBER:
        dest_label = None
        if op.destination and op.destination.path:
            dest_label = op.destination.path[-1][1]
        return LabelAmend(
            action=LabelAction.RENUMBER,
            target=target,
            new_label=dest_label,
            destination=op.destination,
        )

    # Heading replace (may carry destination from NUMERO-based renumber context)
    if op.action is StructuralAction.HEADING_REPLACE or (target.special is FacetKind.HEADING):
        dest_label_h = None
        if op.destination and op.destination.path:
            dest_label_h = op.destination.path[-1][1]
        return LabelAmend(
            action=LabelAction.HEADING_REPLACE,
            new_label=dest_label_h,
            target=target,
            destination=op.destination,
        )

    # Default structural amendment
    node = RefAmend(
        action=op.action,
        target=target,
        anchor=op.anchor,
    )
    return node


def clause_node_to_legal_operation(
    node: ClauseNode,
    sequence: int = 0,
) -> Optional[LegalOperation]:
    """Convert a single ClauseNode to a LegalOperation.

    This bridge is explicit about unsupported cases and lossy fields. It always
    requires a generic ClauseNode to legal-operation mapping and intentionally sets
    ``op_id`` to ``\"\"`` because ClauseNode has no stable operation-id surface.
    Core-provenance fields that are not modeled in the current nodes (``source``,
    ``applicability``, ``group_id``) are therefore unsupported at this seam.

    Args:
        node:     The ClauseNode to convert.
        sequence: Sequence number for the resulting LegalOperation.

    Returns:
        A LegalOperation, or None for MetaClause nodes (which have no
        corresponding tree operation).

    Node-type mapping:
        RefAmend        → LegalOperation(action=node.action, target=node.target,
                                         anchor=node.anchor)
        LabelAmend      → LegalOperation(action=node.action, target=node.target)
        TextAmend       → LegalOperation(action=node.action, target=node.target,
                                         text_patch=node.text_patch)
        MetaClause      → None  (no tree-op equivalent)
        ItemShiftClause → None  (dedicated frontend lowering, not generic yet)
        NamedRowClause  → None  (dedicated frontend lowering, not generic yet)
        ScopedBlock     → NOT handled here; walk children via clause_ast_to_legal_ops
    """
    if isinstance(node, MetaClause):
        return None

    # ItemShiftClause and NamedRowClause are explicit bridge-only nodes.
    # Shared core does not pretend to lower them generically.
    if isinstance(node, (ItemShiftClause, NamedRowClause)):
        return None

    if isinstance(node, RefAmend):
        lo = LegalOperation(
            op_id="",
            sequence=sequence,
            action=node.action,
            target=node.target,
            anchor=node.anchor,
            provenance_tags=tuple(node.notes),
            witness_rule_id=node.witness_rule_id,
        )
        return lo

    if isinstance(node, LabelAmend):
        # Typed destination from new_label (Phase 3: replaces note reparsing).
        # For verb=S (siirtää) the action is "renumber"; for NUMERO-based
        # renumber ("muutetaan §:n numero N:ksi") the action is "renumber".
        # Heading-replace nodes carry LabelAction.HEADING_REPLACE.
        notes = list(node.notes)
        if node.action == LabelAction.HEADING_REPLACE:
            lo = LegalOperation(
                op_id="",
                sequence=sequence,
                action=StructuralAction.HEADING_REPLACE,
                target=node.target,
                provenance_tags=tuple(notes),
                witness_rule_id=node.witness_rule_id,
            )
            return lo
        _dest: Optional[LegalAddress] = None
        if node.action == LabelAction.RENUMBER and node.destination is not None:
            _dest = node.destination
        elif node.action == LabelAction.RENUMBER and node.new_label:
            src_kind = node.target.leaf_kind() if node.target.path else "chapter"
            destination_path = list(node.target.path[:-1])
            destination_path.append((src_kind, node.new_label))
            _dest = LegalAddress(path=tuple(destination_path))
        lo = LegalOperation(
            op_id="",
            sequence=sequence,
            action=StructuralAction.RENUMBER,
            target=node.target,
            destination=_dest,
            provenance_tags=tuple(notes),
            witness_rule_id=node.witness_rule_id,
        )
        return lo

    if isinstance(node, TextAmend):
        text_patch = node.text_patch
        lo = LegalOperation(
            op_id="",
            sequence=sequence,
            action=node.action,
            target=node.target,
            text_patch=text_patch,
        )
        return lo

    # ScopedBlock: callers should recurse through children directly.
    # Reaching here means the caller passed a ScopedBlock — guard with
    # a clear error rather than silently skipping.
    raise TypeError(
        f"clause_node_to_legal_operation does not handle ScopedBlock directly; "
        f"use clause_ast_to_legal_ops() to flatten ScopedBlocks. Got: {node!r}"
    )


def _address_with_scope(addr: LegalAddress, scope: LegalAddress) -> LegalAddress:
    """Apply missing container scope components to an address."""
    if not addr.path:
        return addr
    result = list(addr.path)
    ranks = {
        "part": 0,
        "chapter": 1,
        "section": 2,
        "subsection": 3,
        "item": 4,
    }
    changed = False
    for component in scope.path:
        kind = component[0]
        if any(existing_kind == kind for existing_kind, _label in result):
            continue
        component_rank = ranks.get(kind, -1)
        insert_at = len(result)
        for index, existing in enumerate(result):
            if ranks.get(existing[0], 99) > component_rank:
                insert_at = index
                break
        result.insert(insert_at, component)
        changed = True
    if not changed:
        return addr
    return LegalAddress(path=tuple(result), special=addr.special)


def _op_with_scope(op: LegalOperation, scope: LegalAddress | None) -> LegalOperation:
    """Return ``op`` with ``scope`` applied to target/anchor when needed."""
    if scope is None:
        return op
    target = _address_with_scope(op.target, scope)
    anchor = _address_with_scope(op.anchor, scope) if op.anchor is not None else None
    if target == op.target and anchor == op.anchor:
        return op
    return LegalOperation(
        op_id=op.op_id,
        sequence=op.sequence,
        action=op.action,
        target=target,
        payload=op.payload,
        source=op.source,
        text_patch=op.text_patch,
        applicability=op.applicability,
        destination=op.destination,
        anchor=anchor,
        group_id=op.group_id,
        provenance_tags=op.provenance_tags,
        witness_rule_id=op.witness_rule_id,
    )


def _unsupported_generic_lowering_detail(node: ClauseNode) -> str:
    if isinstance(node, MetaClause):
        return f"meta_kind={node.kind.value}"
    if isinstance(node, ItemShiftClause):
        return (
            "item_shift "
            f"source_items={node.source_items!r} target_items={node.target_items!r} "
            f"target_section={node.target_section!r} target_paragraph={node.target_paragraph!r}"
        )
    if isinstance(node, NamedRowClause):
        return (
            "named_row "
            f"action={node.action.value!r} named_targets={node.named_targets!r} "
            f"target_section={node.target_section!r} target_paragraph={node.target_paragraph!r}"
        )
    raise TypeError(f"Expected unsupported generic lowering node, got {node!r}")


def _unsupported_generic_lowering_diagnostic(
    node: ClauseNode,
    sequence: int,
    scope: LegalAddress | None,
) -> ClauseAstLoweringDiagnostic | None:
    if not isinstance(node, (MetaClause, ItemShiftClause, NamedRowClause)):
        return None
    return ClauseAstLoweringDiagnostic(
        kind=CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_KIND,
        rule_id=CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_RULE_ID,
        phase="lowering",
        family="lowering_filter",
        node_kind=type(node).__name__,
        sequence=sequence,
        reason="Clause AST node has no generic LegalOperation lowering at this seam",
        blocking=True,
        strict_disposition="block",
        quirks_disposition="record",
        scope=scope,
        detail=_unsupported_generic_lowering_detail(node),
    )


def _flatten_clause_node(
    node: ClauseNode,
    ops: List[LegalOperation],
    counter: List[int],
    scope: LegalAddress | None = None,
    diagnostics: List[ClauseAstLoweringDiagnostic] | None = None,
) -> None:
    """Recursively flatten a ClauseNode into ops list, incrementing counter."""
    if isinstance(node, ScopedBlock):
        for child in node.children:
            _flatten_clause_node(child, ops, counter, node.scope, diagnostics)
        return

    lo = clause_node_to_legal_operation(node, sequence=counter[0])
    if lo is not None:
        ops.append(_op_with_scope(lo, scope))
        counter[0] += 1
        return

    if diagnostics is not None:
        diagnostic = _unsupported_generic_lowering_diagnostic(node, counter[0], scope)
        if diagnostic is not None:
            diagnostics.append(diagnostic)


def clause_ast_to_legal_ops(ast: ClauseAST) -> List[LegalOperation]:
    """Convert a ClauseAST to a flat list of LegalOperations.

    Walks all VerbGroups in order, flattening ScopedBlocks by recursion and
    dropping MetaClause, ItemShiftClause, and NamedRowClause nodes (they have
    no generic tree-op equivalent; the latter two use dedicated frontend
    lowering paths).

    Sequence numbers are assigned starting at 0 and incrementing for each
    non-MetaClause node encountered in source order.

    This is the inverse of the shared typed bridge in terms of operation
    semantics: the resulting LegalOperation list corresponds one-to-one with
    the ClauseAST nodes, so the round-trip invariant holds for
    action/target structure. Provenance fields such as ``op_id``, ``source``,
    ``applicability``, and ``group_id`` are not preserved yet.
    """
    result: List[LegalOperation] = []
    counter = [0]  # mutable counter shared across recursive calls
    for vg in ast.verb_groups:
        for node in vg.nodes:
            _flatten_clause_node(node, result, counter)
    return result


def clause_ast_to_legal_ops_with_diagnostics(
    ast: ClauseAST,
) -> tuple[List[LegalOperation], tuple[ClauseAstLoweringDiagnostic, ...]]:
    """Convert ClauseAST to LegalOperations and report unsupported skipped nodes.

    The operation list is intentionally identical to ``clause_ast_to_legal_ops``.
    The second return value owns the otherwise-silent unsupported bridge nodes
    that generic core lowering cannot convert to ``LegalOperation``.
    """
    result: List[LegalOperation] = []
    diagnostics: List[ClauseAstLoweringDiagnostic] = []
    counter = [0]
    for vg in ast.verb_groups:
        for node in vg.nodes:
            _flatten_clause_node(node, result, counter, diagnostics=diagnostics)
    return result, tuple(diagnostics)
