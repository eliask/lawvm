"""Finland ParsedOp -> ClauseAST compatibility bridge.

Shared core owns ClauseAST itself. Finland owns the legacy ParsedOp carrier and
the compatibility lowering from that carrier into ClauseAST.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from lawvm.core.clause_ast import ClauseAST, ClauseNode, LabelAmend, RefAmend, ScopedBlock, VerbGroup
from lawvm.core.ir import LegalAddress
from lawvm.core.parse_witness import ParseWitness
from lawvm.core.semantic_types import FacetKind, LabelAction, StructuralAction
from lawvm.finland.johtolause.types import ParsedOp

_VERB_TO_ACTION: dict[str, StructuralAction] = {
    "M": StructuralAction.REPLACE,
    "K": StructuralAction.REPEAL,
    "L": StructuralAction.INSERT,
    "S": StructuralAction.RENUMBER,
}

_KNOWN_PARSEDOP_KINDS = {"P", "L", "O", "N", "A"}
_KNOWN_PARSEDOP_VERBS = set(_VERB_TO_ACTION.keys())


def _facet_from_legacy(op: ParsedOp) -> Optional[FacetKind]:
    if op.facet is FacetKind.HEADING:
        return FacetKind.HEADING
    if op.facet is FacetKind.INTRO:
        return FacetKind.INTRO
    if op.special == "otsikko":
        return FacetKind.HEADING
    if op.special == "johd":
        return FacetKind.INTRO
    return op.facet


def _build_renumber_destination_path(
    op: ParsedOp,
    target: LegalAddress,
    dest_leaf_kind: str,
    dest_leaf_label: Optional[str],
) -> Optional[LegalAddress]:
    if not dest_leaf_label:
        return None
    destination_path: list[tuple[str, str]] = list(target.path[:-1])
    if op.renumber_dest_part:
        destination_path = [step for step in destination_path if step[0] != "part"]
        destination_path.insert(0, ("part", op.renumber_dest_part))
    if op.renumber_dest_chapter:
        destination_path = [step for step in destination_path if step[0] != "chapter"]
        insert_at = 1 if destination_path and destination_path[0][0] == "part" else 0
        destination_path.insert(insert_at, ("chapter", op.renumber_dest_chapter))
    destination_path.append((dest_leaf_kind, dest_leaf_label))
    return LegalAddress(path=tuple(destination_path))

def parsed_op_to_clause_node(op: ParsedOp) -> ClauseNode:
    """Convert one Finland ParsedOp into a shared ClauseAST node."""
    if op.kind not in _KNOWN_PARSEDOP_KINDS:
        raise ValueError(f"Unsupported ParsedOp kind for Finland ClauseAST conversion: {op.kind!r}")
    if op.verb not in _KNOWN_PARSEDOP_VERBS:
        raise ValueError(f"Unsupported ParsedOp verb for Finland ClauseAST conversion: {op.verb!r}")

    path: list[tuple[str, str]] = []
    if op.part:
        path.append(("part", op.part))
    if op.chapter:
        path.append(("chapter", op.chapter))
    if op.kind == "P":
        path.append(("section", op.number))
        if op.momentti:
            path.append(("subsection", str(op.momentti)))
            if op.item:
                path.append(("item", op.item))
    elif op.kind == "L":
        path.append(("chapter", op.number))
    elif op.kind == "O":
        path.append(("part", op.number))
    elif op.kind == "N":
        path.append(("nimike", op.number))
    elif op.kind == "A":
        path.append(("appendix", op.number))

    parsed_facet = _facet_from_legacy(op)
    target = LegalAddress(path=tuple(path), special=parsed_facet)
    notes: Tuple[str, ...] = tuple(op.notes) if op.notes else ()

    source_tokens = op.source_tokens
    witness = op.witness if isinstance(op.witness, ParseWitness) else None
    witness_rule_id: Optional[str] = witness.rule_id if witness is not None else None
    destination: Optional[LegalAddress] = None
    dest_leaf_kind = target.leaf_kind() if target.path else "chapter"
    dest_leaf_label: Optional[str] = None
    if op.renumber_dest:
        dest_leaf_label = op.renumber_dest
    elif op.verb == "S":
        if op.kind == "O" and op.renumber_dest_part:
            dest_leaf_label = op.renumber_dest_part
        elif target.path:
            dest_leaf_label = target.leaf_label()
    if dest_leaf_label:
        destination = _build_renumber_destination_path(
            op=op,
            target=target,
            dest_leaf_kind=dest_leaf_kind,
            dest_leaf_label=dest_leaf_label,
        )

    if op.verb == "S":
        return LabelAmend(
            action=LabelAction.RENUMBER,
            target=target,
            new_label=op.renumber_dest if op.renumber_dest else None,
            destination=destination,
            notes=notes,
            source_tokens=source_tokens,
            witness_rule_id=witness_rule_id,
        )

    if parsed_facet is FacetKind.HEADING:
        return LabelAmend(
            action=LabelAction.HEADING_REPLACE,
            target=target,
            new_label=op.renumber_dest if op.renumber_dest else None,
            destination=destination,
            notes=notes,
            source_tokens=source_tokens,
            witness_rule_id=witness_rule_id,
        )

    if op.renumber_dest and not op.momentti and not op.item:
        return LabelAmend(
            action=LabelAction.RENUMBER,
            target=target,
            new_label=op.renumber_dest,
            destination=destination,
            notes=notes,
            source_tokens=source_tokens,
            witness_rule_id=witness_rule_id,
        )

    node = RefAmend(
        action=_VERB_TO_ACTION[op.verb],
        target=target,
        notes=notes,
        source_tokens=source_tokens,
        witness_rule_id=witness_rule_id,
    )
    return node


def _group_by_scope(ops: List[ParsedOp]) -> List[ClauseNode]:
    result: List[ClauseNode] = []

    def scope_path(op: ParsedOp) -> Tuple[Tuple[str, str], ...]:
        path: list[tuple[str, str]] = []
        if op.part:
            path.append(("part", op.part))
        if op.chapter:
            path.append(("chapter", op.chapter))
        return tuple(path)

    i = 0
    while i < len(ops):
        current_scope = scope_path(ops[i])
        if not current_scope:
            result.append(parsed_op_to_clause_node(ops[i]))
            i += 1
            continue

        j = i
        while j < len(ops) and scope_path(ops[j]) == current_scope:
            j += 1

        children = [parsed_op_to_clause_node(ops[k]) for k in range(i, j)]
        result.append(ScopedBlock(scope=LegalAddress(path=current_scope), children=tuple(children)))
        i = j

    return result


def build_clause_ast(ops: List[ParsedOp], source_text: str) -> ClauseAST:
    """Build a ClauseAST from Finland ParsedOps."""
    if not ops:
        return ClauseAST(source_text=source_text, verb_groups=())

    def verb_runs() -> List[Tuple[str, List[ParsedOp]]]:
        runs: List[Tuple[str, List[ParsedOp]]] = []
        current_verb = ops[0].verb
        current_run: List[ParsedOp] = [ops[0]]
        for op in ops[1:]:
            if op.verb == current_verb:
                current_run.append(op)
            else:
                runs.append((current_verb, current_run))
                current_verb = op.verb
                current_run = [op]
        runs.append((current_verb, current_run))
        return runs

    verb_groups: List[VerbGroup] = []
    for verb, bucket in verb_runs():
        verb_groups.append(
            VerbGroup(
        verb=_VERB_TO_ACTION.get(verb, StructuralAction.REPLACE),
        nodes=tuple(_group_by_scope(bucket)),
    )
        )
    return ClauseAST(source_text=source_text, verb_groups=tuple(verb_groups))
