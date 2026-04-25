"""Body-driven observed/pairing lane for Finland amendment replay.

This module implements the pairing lane that assigns observed body units
to clause claims, enforcing the invariant that foreign-statute and unmatched
body units must NEVER become fallback inserts for the current statute's
replay.

Pipeline:

    build_observed_body_inventory(muutos_tree)
        → list[ObservedBodyUnit]

    build_clause_claims(ast, target_statute_id)
        → list[ClauseClaim]

    assign_body_units(inventory, claims, target_statute_id)
        → list[PayloadAssignment]

    enforce_pairing_invariants(assignments, statute_id, amendment_id)
        → list[PairingFinding]

    should_use_body_section(label, chapter, assignments)
        → bool

Primary path: ClauseAST (from parse_clause / lower_to_clause_ast).
Legacy compat path: build_clause_claims_from_ops() accepts ParsedOp/AmendmentOp.

Design decisions (from PRO_FI_PEG_VPRI_2026-04-07c.md §2, §5):
- Pairing/coverage owns body assignment, not PEG
- Foreign/unmatched body units must NEVER become fallback inserts
- Every observed body unit is claimed_current, claimed_foreign, or unmatched
- Enforcement is blocking finding, not warning-only
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional, Union

import lxml.etree as etree

from lawvm.core.clause_ast import (
    ClauseAST,
    ClauseNode,
    LabelAmend,
    RefAmend,
    ScopedBlock,
    VerbGroup,
    legal_op_to_clause_node,
)
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind, LabelAction, StructuralAction
from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic
from lawvm.finland.johtolause.surface_model import TargetKind
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.ops import AmendmentOp


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedBodyUnit:
    """An operative unit observed in the amendment body XML."""

    unit_id: str  # e.g. "section:3" or "chapter:2/section:5"
    kind: str  # "section", "chapter", etc.
    label: str
    chapter_label: str = ""
    part_label: str = ""
    xml_element: object = None  # lxml element (opaque for downstream)


@dataclass(frozen=True)
class ClauseClaim:
    """A claim from the parsed johtolause targeting a specific section.

    When target_statute is empty, the claim targets the current replay
    statute (the default single-target case).
    """

    target_statute: str  # which statute this claim targets ("" = current)
    target_address: str  # normalized section label
    claim_kind: str  # "REPLACE", "INSERT", "REPEAL", etc.
    chapter: str = ""
    part: str = ""
    witness: object = None  # ParseWitness | None


@dataclass(frozen=True)
class PayloadAssignment:
    """Assignment result for one body unit."""

    body_unit_id: str
    status: Literal["claimed_current", "claimed_foreign", "unmatched"]
    claim: Optional[ClauseClaim] = None


@dataclass(frozen=True)
class PairingFinding:
    """A finding from pairing invariant enforcement.

    Blocking findings mean that supplemental synthesis must NOT use this
    body unit.  Non-blocking findings are informational.
    """

    body_unit_id: str
    kind: str  # e.g. "foreign_body_unit", "unmatched_body_unit"
    detail: str
    blocking: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _localname(el: etree._Element) -> str:
    """Return the local XML tag name, stripping any namespace prefix."""
    tag = el.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _num_text(el: etree._Element) -> Optional[str]:
    """Return stripped text of the first <num> child, or None."""
    num_el = el.find("{*}num")
    if num_el is None:
        num_el = el.find("num")
    if num_el is None or not num_el.text:
        return None
    return num_el.text.strip()


def _normalize_section_label(raw: str) -> str:
    """Normalize a raw section <num> text to a canonical label."""
    cleaned = re.sub(r"\s*§.*$", "", raw).strip()
    return _norm_num_token(cleaned)


def _normalize_chapter_label(raw: str) -> str:
    """Normalize a chapter <num> text, stripping 'luku' suffix."""
    return _norm_num_token(raw).removesuffix("luku")


def _normalize_part_label(raw: str) -> str:
    """Normalize a part label to the live-tree form used by Finland replay."""
    norm = _norm_num_token(raw).removesuffix("osasto").removesuffix("osa")
    arabic = _roman_label_to_arabic(norm.lower()) if norm else None
    return str(arabic) if arabic is not None else norm


_PART_CROSS_HEADING_RE = re.compile(
    r"^(?P<label>[IVXLCDM]+|\d+[a-z]?)\s+(?:osa|osasto)$",
    flags=re.I,
)


def _direct_text(el: etree._Element) -> str:
    """Return whitespace-normalized direct text content for ``el``."""
    return " ".join("".join(el.itertext()).split())


def _part_label_from_cross_heading(el: etree._Element) -> str:
    """Return a normalized part label when ``el`` is a direct part marker."""
    if _localname(el) != "crossHeading":
        return ""
    match = _PART_CROSS_HEADING_RE.match(_direct_text(el))
    if match is None:
        return ""
    return _normalize_part_label(match.group("label"))


def _is_pseudo_chapter_marker_section(raw_num: str) -> bool:
    """Return True when a malformed <section><num>... luku</num></section> acts as a chapter marker.

    Some Finland sources encode a new chapter heading as a ``section`` with a
    ``<num>`` like ``16 b luku`` instead of a real ``chapter`` element. The
    following sibling sections then belong to that new chapter, not to the
    enclosing previous chapter.
    """
    norm = _norm_num_token(raw_num)
    return norm.endswith("luku")


# ---------------------------------------------------------------------------
# 1. Build observed body inventory
# ---------------------------------------------------------------------------


def build_observed_body_inventory(
    muutos_tree: etree._Element,
) -> List[ObservedBodyUnit]:
    """Extract all operative body units from amendment XML.

    Enumerates all sections and chapters in the amendment body,
    producing a typed inventory.  Similar to extract_body_coverage
    but producing ObservedBodyUnit objects with explicit chapter
    context.
    """
    body = muutos_tree.find(".//{*}body")
    if body is None:
        return []

    units: List[ObservedBodyUnit] = []
    seen_ids: set[str] = set()

    def _append_unit(
        kind: str,
        label: str,
        chapter_label: str,
        part_label: str,
        xml_element: etree._Element,
    ) -> None:
        if chapter_label:
            base_id = f"{kind}:{chapter_label}/{label}"
        else:
            base_id = f"{kind}:{label}"

        unit_id = base_id
        counter = 1
        while unit_id in seen_ids:
            unit_id = f"{base_id}#{counter}"
            counter += 1
        seen_ids.add(unit_id)

        units.append(
            ObservedBodyUnit(
                unit_id=unit_id,
                kind=kind,
                label=label,
                chapter_label=chapter_label,
                part_label=part_label,
                xml_element=xml_element,
            )
        )

    def _walk_children(parent: etree._Element, active_chapter: str = "", active_part: str = "") -> None:
        current_chapter = active_chapter
        current_part = active_part
        for child in parent:
            kind = _localname(child)

            if kind == "crossHeading":
                part_label = _part_label_from_cross_heading(child)
                if part_label:
                    _append_unit("part", part_label, "", part_label, child)
                    current_chapter = ""
                    current_part = part_label
                    continue

            if kind == "part":
                raw_num = _num_text(child)
                if raw_num:
                    part_label = _normalize_part_label(raw_num)
                    _append_unit("part", part_label, "", part_label, child)
                    _walk_children(child, active_chapter="", active_part=part_label)
                    current_chapter = active_chapter
                    current_part = active_part
                    continue

            if kind == "chapter":
                raw_num = _num_text(child)
                if raw_num:
                    chapter_label = _normalize_chapter_label(raw_num)
                    if chapter_label:
                        _append_unit("chapter", chapter_label, "", current_part, child)
                        _walk_children(child, chapter_label, current_part)
                        current_chapter = active_chapter
                        continue

            if kind == "section":
                raw_num = _num_text(child)
                if raw_num:
                    if _is_pseudo_chapter_marker_section(raw_num):
                        pseudo_chapter = _normalize_chapter_label(raw_num)
                        if pseudo_chapter:
                            _append_unit("chapter", pseudo_chapter, "", current_part, child)
                            _walk_children(child, pseudo_chapter, current_part)
                            current_chapter = pseudo_chapter
                            continue

                    section_label = _normalize_section_label(raw_num)
                    if section_label:
                        _append_unit("section", section_label, current_chapter, current_part, child)

            _walk_children(child, current_chapter, current_part)

    _walk_children(body)

    return units


# ---------------------------------------------------------------------------
# 2a. ClauseAST construction from AmendmentOps (bridge for grafter)
# ---------------------------------------------------------------------------


_OP_TYPE_TO_ACTION: dict[str, StructuralAction] = {
    "REPLACE": StructuralAction.REPLACE,
    "REPEAL": StructuralAction.REPEAL,
    "INSERT": StructuralAction.INSERT,
    "RENUMBER": StructuralAction.RENUMBER,
}

_TARGET_UNIT_KIND_TO_ADDR: dict[str, str] = {
    "section": "section",
    "chapter": "chapter",
    "part": "part",
}


def clause_ast_from_amendment_ops(ops: list[AmendmentOp]) -> ClauseAST:
    """Build a ClauseAST from a list of AmendmentOps.

    Uses the LegalOperation stored on each AmendmentOp when available
    (from the PEG path), otherwise constructs RefAmend nodes directly
    from the AmendmentOp target fields.

    This is the bridge that lets grafter.py pass ClauseAST to
    build_clause_claims() without requiring the full parse_clause()
    pipeline.
    """
    if not ops:
        return ClauseAST(source_text="", verb_groups=())

    # Group consecutive ops by action into verb runs
    runs: list[tuple[StructuralAction, list[AmendmentOp]]] = []
    current_action = _OP_TYPE_TO_ACTION.get(ops[0].op_type, StructuralAction.REPLACE)
    current_run: list[AmendmentOp] = [ops[0]]
    for op in ops[1:]:
        action = _OP_TYPE_TO_ACTION.get(op.op_type, StructuralAction.REPLACE)
        if action == current_action:
            current_run.append(op)
        else:
            runs.append((current_action, current_run))
            current_action = action
            current_run = [op]
    runs.append((current_action, current_run))

    verb_groups: list[VerbGroup] = []
    for action, bucket in runs:
        nodes: list[ClauseNode] = []
        for op in bucket:
            # Prefer LegalOperation bridge when available
            if op.lo is not None:
                try:
                    nodes.append(legal_op_to_clause_node(op.lo))
                    continue
                except (ValueError, TypeError):
                    pass  # fall through to direct construction

            # Direct construction from AmendmentOp fields
            addr_kind = _TARGET_UNIT_KIND_TO_ADDR.get(op.target_unit_kind, "section")
            path: list[tuple[str, str]] = []
            if op.target_part:
                path.append(("part", op.target_part))
            if op.target_chapter:
                path.append(("chapter", op.target_chapter))
            if op.target_section:
                path.append((addr_kind, op.target_section))
            if op.target_paragraph:
                path.append(("subsection", str(op.target_paragraph)))
                if op.target_item:
                    path.append(("item", op.target_item))

            if not path:
                continue

            special: Optional[FacetKind] = None
            if op.target_special:
                special = {"otsikko": FacetKind.HEADING, "johd": FacetKind.INTRO}.get(op.target_special)

            target = LegalAddress(path=tuple(path), special=special)
            op_action = _OP_TYPE_TO_ACTION.get(op.op_type, StructuralAction.REPLACE)

            if op.op_type == "RENUMBER" or op.target_special == "otsikko":
                la = LabelAction.RENUMBER if op.op_type == "RENUMBER" else LabelAction.HEADING_REPLACE
                nodes.append(
                    LabelAmend(
                        action=la,
                        target=target,
                        witness_rule_id=op.witness_rule_id,
                    )
                )
            else:
                nodes.append(
                    RefAmend(
                        action=op_action,
                        target=target,
                        witness_rule_id=op.witness_rule_id,
                    )
                )

        verb_groups.append(VerbGroup(verb=action, nodes=tuple(nodes)))

    return ClauseAST(source_text="", verb_groups=tuple(verb_groups))


# ---------------------------------------------------------------------------
# 2b. Build clause claims from ClauseAST (primary) or legacy ops (compat)
# ---------------------------------------------------------------------------

# Action name → claim kind mapping (ClauseAST uses English action names)
_ACTION_TO_CLAIM_KIND: dict[str, str] = {
    StructuralAction.REPLACE.value: "REPLACE",
    StructuralAction.REPEAL.value: "REPEAL",
    StructuralAction.INSERT.value: "INSERT",
    StructuralAction.RENUMBER.value: "RENUMBER",
    LabelAction.HEADING_REPLACE.value: "REPLACE",
}


def _extract_target_fields(node: Union[RefAmend, LabelAmend]) -> tuple[TargetKind | None, str, str, str]:
    """Extract (leaf_kind, label, chapter, part) from a RefAmend or LabelAmend target.

    Returns a tuple of:
      - typed target kind for claim filtering
      - label: normalized target label
      - chapter: normalized chapter label from enclosing path (or "")
      - part: normalized part label from enclosing path (or "")
    """
    target = node.target
    if not target.path:
        return (None, "", "", "")

    leaf_kind = target.leaf_kind()
    leaf_label = target.leaf_label()
    kind: TargetKind | None
    if leaf_kind == "section":
        kind = TargetKind.SECTION
    elif leaf_kind == "chapter":
        kind = TargetKind.CHAPTER
    elif leaf_kind == "part":
        kind = TargetKind.PART
    else:
        kind = None

    label = _norm_num_token(leaf_label) if leaf_label else ""
    if kind is TargetKind.CHAPTER:
        label = label.removesuffix("luku")
    elif kind is TargetKind.PART:
        label = _normalize_part_label(leaf_label)

    # Extract chapter context from the path (enclosing chapter before leaf)
    chapter = ""
    part = ""
    for path_kind, path_label in target.path[:-1]:
        if path_kind == "part":
            part = _normalize_part_label(path_label)
        if path_kind == "chapter":
            chapter = _norm_num_token(path_label).removesuffix("luku")

    return (kind, label, chapter, part)


def _claims_from_node(
    node: Union[RefAmend, LabelAmend, ScopedBlock],
    verb_action: StructuralAction,
    target_statute_id: str,
    scope_chapter: str = "",
) -> List[ClauseClaim]:
    """Recursively extract ClauseClaims from a single ClauseAST node."""
    claims: List[ClauseClaim] = []

    if isinstance(node, ScopedBlock):
        # Extract chapter from scope if present
        block_chapter = scope_chapter
        for path_kind, path_label in node.scope.path:
            if path_kind == "chapter":
                block_chapter = _norm_num_token(path_label).removesuffix("luku")
        for child in node.children:
            if isinstance(child, (RefAmend, LabelAmend, ScopedBlock)):
                claims.extend(_claims_from_node(child, verb_action, target_statute_id, block_chapter))
        return claims

    if not isinstance(node, (RefAmend, LabelAmend)):
        return claims

    kind, label, node_chapter, node_part = _extract_target_fields(node)
    if not label:
        return claims

    # Section-, chapter-, and part-level targets produce claims.  Part INSERT
    # claims are required so body pairing can adopt the full subtree of a new
    # part (for example "lisätään ... V osa seuraavasti") instead of leaving
    # all child chapters/sections unmatched as high_uncovered_body residue.
    if kind not in (TargetKind.SECTION, TargetKind.CHAPTER, TargetKind.PART):
        return claims

    # Determine action: node's own action if available, else verb group's action.
    # RefAmend.action is ActionKind (str Literal); LabelAmend.action is LabelAction enum.
    # _ACTION_TO_CLAIM_KIND uses string keys, so extract .value from enum.
    if isinstance(node, RefAmend):
        action_str = node.action.value  # StructuralAction enum -> str
        witness = node.witness_rule_id
    else:
        action_str = node.action.value  # LabelAction enum -> str
        witness = node.witness_rule_id

    claim_kind = _ACTION_TO_CLAIM_KIND.get(action_str, "REPLACE")

    # Use node-level chapter if present, else scope chapter
    ch = node_chapter if node_chapter else scope_chapter

    claims.append(
        ClauseClaim(
            target_statute=target_statute_id,
            target_address=label,
            claim_kind=claim_kind,
            chapter=ch,
            part=node_part,
            witness=witness,
        )
    )
    return claims


def build_clause_claims(
    ast: ClauseAST,
    target_statute_id: str,
) -> List[ClauseClaim]:
    """Build typed clause claims from a ClauseAST.

    Walks VerbGroups and their nodes (RefAmend, LabelAmend, ScopedBlock)
    to produce ClauseClaim objects.  MetaClause, TextAmend, ItemShiftClause,
    and NamedRowClause nodes are skipped — they don't produce section/chapter
    claims.

    For REPEAL ops, the claim marks the section as "claimed for repeal" —
    body content for that label should NOT be used as payload.
    """
    claims: List[ClauseClaim] = []

    for vg in ast.verb_groups:
        for node in vg.nodes:
            if isinstance(node, (RefAmend, LabelAmend, ScopedBlock)):
                claims.extend(_claims_from_node(node, vg.verb, target_statute_id))

    return claims


def build_clause_claims_from_ops(
    ops: Union[list[ParsedOp], list[AmendmentOp]],
    target_statute_id: str,
) -> List[ClauseClaim]:
    """Legacy compat: build clause claims from ParsedOp or AmendmentOp lists.

    Directly extracts claims from op fields without going through ClauseAST.
    Callers should migrate to passing ClauseAST to build_clause_claims()
    directly.
    """
    _VERB_TO_OP_TYPE: dict[str, str] = {
        "M": "REPLACE",
        "K": "REPEAL",
        "L": "INSERT",
        "S": "RENUMBER",
    }

    claims: List[ClauseClaim] = []

    for op in ops:
        # Determine op_type and target fields based on op shape
        op_type: str
        kind: TargetKind
        number: str
        chapter: str
        part: str
        witness: object
        if isinstance(op, ParsedOp):
            op_type = _VERB_TO_OP_TYPE.get(op.verb, op.verb)
            kind = op.typed_kind
            number = op.number
            chapter = op.chapter
            part = op.part
            witness = op.witness
        elif isinstance(op, AmendmentOp):
            op_type = op.op_type
            kind = TargetKind.for_leaf_kind(op.target_unit_kind)
            number = op.target_section
            chapter = op.target_chapter or ""
            part = op.target_part or ""
            witness = op.witness_rule_id
        else:
            continue

        if not number:
            continue

        # Section-, chapter-, and part-level ops produce claims.
        if kind not in (TargetKind.SECTION, TargetKind.CHAPTER, TargetKind.PART):
            continue

        label = _norm_num_token(number)
        if kind is TargetKind.CHAPTER:
            label = label.removesuffix("luku")
        elif kind is TargetKind.PART:
            label = _normalize_part_label(number)

        ch = _norm_num_token(chapter) if chapter else ""
        if ch:
            ch = ch.removesuffix("luku")
        pt = _normalize_part_label(part) if part else ""

        claims.append(
            ClauseClaim(
                target_statute=target_statute_id,
                target_address=label,
                claim_kind=op_type,
                chapter=ch,
                part=pt,
                witness=witness,
            )
        )

    return claims


# ---------------------------------------------------------------------------
# 3. Pair and assign
# ---------------------------------------------------------------------------


def assign_body_units(
    inventory: List[ObservedBodyUnit],
    claims: List[ClauseClaim],
    target_statute_id: str,
) -> List[PayloadAssignment]:
    """Pair observed body units with clause claims.

    Rules:
    - Body unit with matching claim for current statute → claimed_current
    - Body unit with claim for different statute → claimed_foreign
    - Body unit with no claim → unmatched
    - Body unit matching a REPEAL claim → claimed_current (but payload NOT used)

    Chapter-scoped matching (cross-chapter misrouting prevention):
    - Claims WITH chapter context are indexed under their exact (chapter, label)
      key only — they do NOT spill into the wildcard ("", label) bucket.
    - Claims WITHOUT chapter context (chapter="") are indexed under ("", label)
      and match any body unit regardless of chapter.
    - This prevents an INSERT targeting chapter:5/section:18a from claiming a
      body unit in chapter 2 that also has label "18a".
    """
    # Build lookup: (chapter, label) → list of claims for quick matching.
    # IMPORTANT: claims with chapter context are indexed ONLY under their exact
    # (chapter, label) key.  Claims without chapter context (chapter="") go into
    # the ("", label) wildcard bucket.  This is the fix for cross-chapter
    # misrouting: a chapter-scoped claim must NOT match body units in other
    # chapters via the wildcard bucket.
    claim_index: dict[tuple[str, str, str], List[ClauseClaim]] = {}
    for claim in claims:
        key = (claim.part, claim.chapter, claim.target_address)
        claim_index.setdefault(key, []).append(claim)

    assignments: List[PayloadAssignment] = []

    for unit in inventory:
        if unit.kind == "chapter":
            # Chapter units: match by label directly as chapter claim
            matched_claim = _find_matching_claim(
                unit.label,
                "",
                unit.part_label,
                claim_index,
                target_statute_id,
                kind_filter="chapter",
            )
        else:
            # Section units: match by (chapter, label)
            matched_claim = _find_matching_claim(
                unit.label,
                unit.chapter_label,
                unit.part_label,
                claim_index,
                target_statute_id,
            )

        if matched_claim is not None:
            if matched_claim.target_statute != target_statute_id:
                status: Literal["claimed_current", "claimed_foreign", "unmatched"] = "claimed_foreign"
            else:
                status = "claimed_current"
            assignments.append(
                PayloadAssignment(
                    body_unit_id=unit.unit_id,
                    status=status,
                    claim=matched_claim,
                )
            )
        else:
            assignments.append(
                PayloadAssignment(
                    body_unit_id=unit.unit_id,
                    status="unmatched",
                    claim=None,
                )
            )

    return assignments


def _find_matching_claim(
    label: str,
    chapter: str,
    part: str,
    claim_index: dict[tuple[str, str, str], List[ClauseClaim]],
    target_statute_id: str,
    kind_filter: str = "",
) -> Optional[ClauseClaim]:
    """Find best matching claim for a body unit.

    Preference order:
    1. Exact (chapter, label) match for current statute
    2. Wildcard ("", label) match for current statute (only unscoped claims)
    3. Cross-chapter fallback: if body unit has no chapter context, check
       all chapter-scoped claims for this label
    4. Any match for a different statute

    Key invariant: a claim scoped to chapter X never matches a body unit
    in chapter Y (where X != Y).  This prevents cross-chapter INSERT
    misrouting (e.g. INSERT chapter:5/section:18a claiming body unit in
    chapter 2 that also has label "18a").
    """
    candidates: List[ClauseClaim] = []

    # Priority 1: Exact (chapter, label) match
    if chapter:
        exact = claim_index.get((part, chapter, label), [])
        candidates.extend(exact)

    # Priority 2: Wildcard ("", label) match — only claims with no chapter scope
    wild = claim_index.get((part, "", label), [])
    if not wild and not part:
        wild = claim_index.get(("", "", label), [])
    candidates.extend(wild)

    # Priority 3: If body unit has no chapter context (chapter=""), check all
    # chapter-scoped claims for this label.  This handles the case where the
    # amendment body has a flat section (no enclosing chapter) but the claim
    # targets a specific chapter.
    if not chapter:
        for key, claims_for_key in claim_index.items():
            key_part, key_chapter, key_label = key
            if part and key_part and key_part != part:
                continue
            if key_chapter and key_label == label:
                candidates.extend(claims_for_key)

    if not candidates:
        return None

    # Deduplicate while preserving order
    seen: set[int] = set()
    unique_candidates: List[ClauseClaim] = []
    for c in candidates:
        cid = id(c)
        if cid not in seen:
            seen.add(cid)
            unique_candidates.append(c)

    # Prefer current-statute claims
    for claim in unique_candidates:
        if claim.target_statute == target_statute_id:
            return claim

    # Fall back to foreign-statute claims
    return unique_candidates[0]


# ---------------------------------------------------------------------------
# 3b. Subtree-aware assignment for chapter INSERT ops
# ---------------------------------------------------------------------------


def assign_body_units_subtree_aware(
    inventory: List[ObservedBodyUnit],
    claims: List[ClauseClaim],
    target_statute_id: str,
) -> List[PayloadAssignment]:
    """Pair observed body units with clause claims, with subtree awareness.

    Extends ``assign_body_units`` with two additional rules:

    **Subtree claim rule (chapter INSERT):** when a chapter INSERT claim
    exists for chapter X, all section body units whose ``chapter_label == X``
    are claimed by that chapter INSERT claim as a subtree, even if no explicit
    per-section claim exists for them.  The rationale: the chapter INSERT op
    implicitly adopts all child sections visible in the amendment body for
    that chapter.

    **Parent adoption rule (section-level claims):** when a chapter body unit
    has NO direct chapter-level claim but at least one of its child sections
    IS claimed by a section-level claim for the current statute, the chapter
    body unit is promoted to ``claimed_current``.  The rationale: the chapter
    element in the amendment body is a structural container for the claimed
    sections (e.g. tilalle-range INSERT ops that insert sections into an
    existing chapter).  Without this rule, the parent chapter would be
    "unmatched" even though all its sections are paired, triggering spurious
    HIGH_UNCOVERED_BODY signals.

    Standard individual-claim matching runs first; subtree adoption and
    parent adoption only fire for body units that are still unmatched after
    the individual pass.
    """
    # Step 1: run the normal individual-claim assignment pass
    assignments = assign_body_units(inventory, claims, target_statute_id)

    # Step 2: build chapter/part INSERT claim indices for subtree adoption.
    chapter_insert_claims: dict[tuple[str, str], ClauseClaim] = {}
    part_insert_claims: dict[str, ClauseClaim] = {}

    # Build sets of part/chapter labels in the inventory.
    part_labels_in_body: set[str] = {
        unit.label for unit in inventory if unit.kind == "part"
    }
    chapter_labels_in_body: set[tuple[str, str]] = {
        (unit.part_label, unit.label) for unit in inventory if unit.kind == "chapter"
    }

    for claim in claims:
        if (
            claim.claim_kind == "INSERT"
            and claim.target_statute == target_statute_id
            and claim.target_address in part_labels_in_body
            and claim.chapter == ""
        ):
            part_insert_claims[claim.target_address] = claim

    # Chapter INSERT claims are those where target_address matches a chapter
    # label in the body, and claim_kind == "INSERT", chapter == "" (no parent
    # chapter context for a top-level chapter INSERT).
    for claim in claims:
        if (
            claim.claim_kind == "INSERT"
            and claim.target_statute == target_statute_id
            and (claim.part, claim.target_address) in chapter_labels_in_body
            and claim.chapter == ""
        ):
            chapter_insert_claims[(claim.part, claim.target_address)] = claim

    # Step 3: build a set of chapter labels whose section body units are fully
    # covered by current-statute section claims. This is used for the parent
    # adoption rule (Step 5): a chapter body unit is only adopted when it acts
    # as a pure structural wrapper for already-claimed child sections.
    chapters_with_claimed_sections: dict[tuple[str, str], ClauseClaim] = {}
    chapters_with_noncurrent_sections: set[tuple[str, str]] = set()
    for assignment in assignments:
        # Find the corresponding body unit to get its chapter_label
        for unit in inventory:
            if unit.unit_id == assignment.body_unit_id and unit.kind == "section" and unit.chapter_label:
                chapter_key = (unit.part_label, unit.chapter_label)
                if assignment.status == "claimed_current" and assignment.claim is not None:
                    if chapter_key not in chapters_with_claimed_sections:
                        chapters_with_claimed_sections[chapter_key] = assignment.claim
                else:
                    chapters_with_noncurrent_sections.add(chapter_key)
                break

    for chapter_key in tuple(chapters_with_claimed_sections):
        if chapter_key in chapters_with_noncurrent_sections:
            del chapters_with_claimed_sections[chapter_key]

    if not part_insert_claims and not chapter_insert_claims and not chapters_with_claimed_sections:
        return assignments

    # Step 4: for each unmatched section unit whose chapter_label is in a
    # chapter INSERT claim, promote to claimed_current via the subtree rule.
    # Step 5: for each unmatched chapter unit whose label has only current-
    # statute claimed child sections, promote to claimed_current via the
    # parent adoption rule.
    result: List[PayloadAssignment] = []
    for assignment in assignments:
        if assignment.status != "unmatched":
            result.append(assignment)
            continue

        # Find the corresponding body unit from inventory (by unit_id)
        matching_unit: Optional[ObservedBodyUnit] = None
        for unit in inventory:
            if unit.unit_id == assignment.body_unit_id:
                matching_unit = unit
                break

        if (
            matching_unit is not None
            and matching_unit.kind == "part"
            and matching_unit.label in part_insert_claims
        ):
            result.append(
                PayloadAssignment(
                    body_unit_id=assignment.body_unit_id,
                    status="claimed_current",
                    claim=part_insert_claims[matching_unit.label],
                )
            )
        elif (
            matching_unit is not None
            and matching_unit.kind == "chapter"
            and matching_unit.part_label in part_insert_claims
        ):
            result.append(
                PayloadAssignment(
                    body_unit_id=assignment.body_unit_id,
                    status="claimed_current",
                    claim=part_insert_claims[matching_unit.part_label],
                )
            )
        elif (
            matching_unit is not None
            and matching_unit.kind == "section"
            and matching_unit.part_label in part_insert_claims
        ):
            result.append(
                PayloadAssignment(
                    body_unit_id=assignment.body_unit_id,
                    status="claimed_current",
                    claim=part_insert_claims[matching_unit.part_label],
                )
            )
        elif (
            matching_unit is not None
            and matching_unit.kind == "section"
            and matching_unit.chapter_label
            and (matching_unit.part_label, matching_unit.chapter_label) in chapter_insert_claims
        ):
            # Subtree adoption: promote to claimed_current via chapter INSERT
            chapter_claim = chapter_insert_claims[(matching_unit.part_label, matching_unit.chapter_label)]
            result.append(
                PayloadAssignment(
                    body_unit_id=assignment.body_unit_id,
                    status="claimed_current",
                    claim=chapter_claim,
                )
            )
        elif (
            matching_unit is not None
            and matching_unit.kind == "chapter"
            and (matching_unit.part_label, matching_unit.label) in chapters_with_claimed_sections
        ):
            # Parent adoption: chapter body unit is a container for claimed
            # sections — promote to claimed_current using one of the child
            # section's claims as the representative claim.
            representative_claim = chapters_with_claimed_sections[(matching_unit.part_label, matching_unit.label)]
            result.append(
                PayloadAssignment(
                    body_unit_id=assignment.body_unit_id,
                    status="claimed_current",
                    claim=representative_claim,
                )
            )
        else:
            result.append(assignment)

    return result


def build_chapter_subtree_coverage(
    inventory: List[ObservedBodyUnit],
    claims: List[ClauseClaim],
    target_statute_id: str,
) -> dict[tuple[str, str], list[str]]:
    """Return a map of ``(part_label, chapter_label)`` → [section unit_ids].

    Only includes chapters that have an INSERT claim targeting the current
    statute.  This is used by the restructure-plan builder to populate
    ``payload_claim_ids`` for subtree INSERT ops.
    """
    part_insert_labels: set[str] = set()
    chapter_insert_labels: set[tuple[str, str]] = set()
    part_labels_in_body: set[str] = {
        unit.label for unit in inventory if unit.kind == "part"
    }
    chapter_labels_in_body: set[tuple[str, str]] = {
        (unit.part_label, unit.label) for unit in inventory if unit.kind == "chapter"
    }
    for claim in claims:
        if (
            claim.claim_kind == "INSERT"
            and claim.target_statute == target_statute_id
            and claim.target_address in part_labels_in_body
            and claim.chapter == ""
        ):
            part_insert_labels.add(claim.target_address)
        if (
            claim.claim_kind == "INSERT"
            and claim.target_statute == target_statute_id
            and (claim.part, claim.target_address) in chapter_labels_in_body
            and claim.chapter == ""
        ):
            chapter_insert_labels.add((claim.part, claim.target_address))

    result: dict[tuple[str, str], list[str]] = {}
    for unit in inventory:
        if (
            unit.kind == "section"
            and (
                unit.part_label in part_insert_labels
                or (unit.part_label, unit.chapter_label) in chapter_insert_labels
            )
        ):
            result.setdefault((unit.part_label, unit.chapter_label), []).append(unit.unit_id)

    return result


# ---------------------------------------------------------------------------
# 4. Invariant enforcement
# ---------------------------------------------------------------------------


def enforce_pairing_invariants(
    assignments: List[PayloadAssignment],
    statute_id: str,
    amendment_id: str,
) -> List[PairingFinding]:
    """Check that no foreign/unmatched units would be auto-inserted.

    Returns findings (not exceptions).  These should be surfaced in
    evidence/diagnostics.  All findings are blocking by default per
    Pro decision: enforcement is blocking finding, not warning-only.
    """
    findings: List[PairingFinding] = []

    chapter_statuses: dict[str, List[str]] = {}
    unmatched_chapters: set[str] = set()

    for assignment in assignments:
        unit_chapter, unit_label = _parse_unit_id(assignment.body_unit_id)
        if assignment.body_unit_id.startswith("section:") and unit_chapter:
            chapter_statuses.setdefault(unit_chapter, []).append(assignment.status)
        elif assignment.body_unit_id.startswith("chapter:") and assignment.status == "unmatched":
            unmatched_chapters.add(unit_label)

        if assignment.status == "claimed_foreign":
            claim_detail = ""
            if assignment.claim is not None:
                claim_detail = (
                    f" (targets statute {assignment.claim.target_statute}"
                    f", {assignment.claim.claim_kind} {assignment.claim.target_address})"
                )
            findings.append(
                PairingFinding(
                    body_unit_id=assignment.body_unit_id,
                    kind="foreign_body_unit",
                    detail=(
                        f"Body unit {assignment.body_unit_id} in amendment "
                        f"{amendment_id} targets a different statute"
                        f"{claim_detail}; must not be used as payload for "
                        f"statute {statute_id}"
                    ),
                    blocking=True,
                )
            )
        elif assignment.status == "unmatched":
            findings.append(
                PairingFinding(
                    body_unit_id=assignment.body_unit_id,
                    kind="unmatched_body_unit",
                    detail=(
                        f"Body unit {assignment.body_unit_id} in amendment "
                        f"{amendment_id} has no matching clause claim; "
                        f"must not be auto-inserted into statute {statute_id}"
                    ),
                    blocking=True,
                )
            )

    for chapter_label in sorted(unmatched_chapters):
        child_statuses = chapter_statuses.get(chapter_label, [])
        if "claimed_current" in child_statuses and any(
            status in {"claimed_foreign", "unmatched"} for status in child_statuses
        ):
            findings.append(
                PairingFinding(
                    body_unit_id=f"chapter:{chapter_label}",
                    kind="chapter_parent_adoption_mixed_children",
                    detail=(
                        f"Chapter body unit chapter:{chapter_label} in amendment "
                        f"{amendment_id} contains mixed child ownership; parent adoption "
                        f"must not hide unmatched or foreign child sections for statute {statute_id}"
                    ),
                    blocking=True,
                )
            )

    return findings


# ---------------------------------------------------------------------------
# 5. Integration guard for _recover_uncovered_body_ops
# ---------------------------------------------------------------------------


def should_use_body_section(
    label: str,
    chapter: str,
    assignments: List[PayloadAssignment],
) -> bool:
    """Return True unless the body section is claimed_foreign or a REPEAL claim.

    This function is the integration point: _recover_uncovered_body_ops
    can call it as an additional guard to prevent foreign-statute and
    repeal body sections from being used as fallback payload.  Unmatched
    sections are allowed through — they are exactly the case where
    body-coverage recovery is needed (PEG didn't emit an op for them).

    Args:
        label: Normalized section label (e.g. "3", "5a")
        chapter: Normalized chapter label (e.g. "2", ""), empty if no chapter context
        assignments: Full assignment list from assign_body_units

    Returns:
        True if the section is safe to use as payload for the current statute.
    """
    norm_label = _norm_num_token(label)
    norm_chapter = _norm_num_token(chapter) if chapter else ""

    for assignment in assignments:
        # Match by parsing the unit_id back to (kind, chapter, label)
        unit_chapter, unit_label = _parse_unit_id(assignment.body_unit_id)

        if unit_label != norm_label:
            continue

        # Chapter matching: empty chapter in query matches any unit
        if norm_chapter and unit_chapter and unit_chapter != norm_chapter:
            continue

        # Found a matching assignment
        if assignment.status == "claimed_foreign":
            return False

        # Claimed_current but REPEAL — payload should not be used
        if (
            assignment.status == "claimed_current"
            and assignment.claim is not None
            and assignment.claim.claim_kind == "REPEAL"
        ):
            return False

        # claimed_current or unmatched — allow through.
        # Unmatched sections are exactly the case where body-coverage
        # recovery is needed (PEG didn't emit an op for them).
        return True

    # No matching assignment found — conservative: don't block
    # (the body section may not have been in the inventory, e.g. non-section
    # elements or elements without <num>)
    return True


def _parse_unit_id(unit_id: str) -> tuple[str, str]:
    """Parse a unit_id like 'section:2/5' or 'section:5' into (chapter, label).

    Returns (chapter_label, section_label). Chapter is "" if not present.
    """
    # Strip kind prefix
    _, _, rest = unit_id.partition(":")
    # Strip any disambiguation suffix (#N)
    rest = rest.split("#")[0]

    if "/" in rest:
        chapter, label = rest.split("/", 1)
        return (chapter, label)
    return ("", rest)


# ---------------------------------------------------------------------------
# 6. Corpus-level body pairing audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmendmentPairingResult:
    """Body pairing analysis for one amendment applied to one statute."""

    statute_id: str
    amendment_id: str
    inventory_count: int
    claimed_current: int
    claimed_foreign: int
    unmatched: int
    findings: tuple[PairingFinding, ...]
    repeal_blocked: int = 0

    @property
    def has_anomalies(self) -> bool:
        return bool(self.findings) or self.repeal_blocked > 0

    def to_dict(self) -> dict:
        return {
            "statute_id": self.statute_id,
            "amendment_id": self.amendment_id,
            "inventory_count": self.inventory_count,
            "claimed_current": self.claimed_current,
            "claimed_foreign": self.claimed_foreign,
            "unmatched": self.unmatched,
            "repeal_blocked": self.repeal_blocked,
            "findings": [
                {
                    "body_unit_id": f.body_unit_id,
                    "kind": f.kind,
                    "detail": f.detail,
                    "blocking": f.blocking,
                }
                for f in self.findings
            ],
        }


def analyze_amendment_pairing(
    statute_id: str,
    amendment_id: str,
    amendment_xml_bytes: bytes,
) -> Optional[AmendmentPairingResult]:
    """Run body pairing analysis on a single amendment.

    Parses the johtolause via parse_clause() to obtain ClauseAST, builds
    the body inventory from the amendment XML, pairs them, and returns
    findings.

    Returns None if the amendment has no body content or no parseable ops.
    """
    from lawvm.finland.acquisition import build_amendment_acquisition_result
    from lawvm.finland.johtolause.compat import parse_clause

    muutos_tree = etree.fromstring(amendment_xml_bytes)
    acquisition = build_amendment_acquisition_result(
        xml_bytes=amendment_xml_bytes,
        parent_id=statute_id,
        amendment_id=amendment_id,
        source_title="",
        parent_title="",
    )
    johto_norm = acquisition.decision.chosen_normalized_text
    if not johto_norm:
        return None

    result = parse_clause(johto_norm)
    clause_ast = result.clause_ast

    # Empty AST means no parseable structural ops
    if not clause_ast.verb_groups:
        return None

    inventory = build_observed_body_inventory(muutos_tree)
    if not inventory:
        return None

    claims = build_clause_claims(clause_ast, statute_id)
    assignments = assign_body_units(inventory, claims, statute_id)
    findings = enforce_pairing_invariants(assignments, statute_id, amendment_id)

    # Count statuses
    n_current = sum(1 for a in assignments if a.status == "claimed_current")
    n_foreign = sum(1 for a in assignments if a.status == "claimed_foreign")
    n_unmatched = sum(1 for a in assignments if a.status == "unmatched")

    # Count repeal-blocked sections (claimed_current but REPEAL claim)
    n_repeal_blocked = sum(
        1
        for a in assignments
        if a.status == "claimed_current" and a.claim is not None and a.claim.claim_kind == "REPEAL"
    )

    return AmendmentPairingResult(
        statute_id=statute_id,
        amendment_id=amendment_id,
        inventory_count=len(inventory),
        claimed_current=n_current,
        claimed_foreign=n_foreign,
        unmatched=n_unmatched,
        findings=tuple(findings),
        repeal_blocked=n_repeal_blocked,
    )


def audit_statute_body_pairing(
    statute_id: str,
    *,
    mode: str = "legal_pit",
) -> List[AmendmentPairingResult]:
    """Run body pairing audit across all amendments for one statute.

    Returns a list of AmendmentPairingResult, one per amendment that
    has body content and parseable johtolause ops.
    """
    from lawvm.corpus_store import get_corpus_store
    from lawvm.finland.amendment_index import get_amendment_children

    corpus = get_corpus_store()
    children = get_amendment_children()
    amendment_ids = list(children.get(statute_id, []))

    results: List[AmendmentPairingResult] = []
    for amendment_id in amendment_ids:
        xml_bytes = corpus.read_source(amendment_id)
        if xml_bytes is None:
            continue
        result = analyze_amendment_pairing(statute_id, amendment_id, xml_bytes)
        if result is not None:
            results.append(result)

    return results
