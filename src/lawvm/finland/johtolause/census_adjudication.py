"""Content-based adjudication predicates for the FI johtolause parity census.

The census (:mod:`census_accounting`) bins every amendment johtolause into five
closed buckets. The genuine-delta frontier (NEW != OLD, not adjudicated) was
historically cleared by FOUR hardcoded ``frozenset[str]`` of statute-ids — one
sid per human-judged case. Those lists don't scale: every new amendment whose
NEW parse carries a cosmetic provenance delta, or recovers an insertion the OLD
parser flattened, re-breaks the ``genuine_delta_unclassified <= 0`` baseline.

This module replaces the *mechanical* part of that work with two CONTENT-BASED
predicates, applied to the OLD/NEW :class:`SurfaceClause` pair directly (not to
the sid). They are deliberately conservative: the one hard invariant is
FAIL-LOUD — a real regression (NEW worse than OLD) must NEVER be adjudicated
benign. When in doubt, a predicate returns ``None`` and the clause stays
``genuine_delta_unclassified``.

  P1 — provenance-only delta (``provenance_only_delta``)
     Every diff line between the two canonical models is a ``witness.rule_id`` or
     ``witness.source_span`` difference. All semantic node content is identical;
     two parsers merely credit different (equivalent) recognizers / span
     boundaries for the same node. Replay-neutral by construction.

  P2 — source-witnessed additive recovery (``new_better_recovery``)
     NEW preserves ALL of OLD's real content AND every NEW-only addition is
     source-witnessed under a named recovery rule. Concretely:

       * Verb groups are matched OLD<->NEW by content signature (verb + the
         multiset of preserved real-node signatures), NOT by index — an inserted
         leading group must not shift indices into a false match or mask a drop
         (Codex Q2). Every OLD real group must match exactly one NEW group that
         preserves ALL of its real nodes.
       * Within a matched group, every OLD real node is either preserved
         identically OR upgraded bare-ref -> insertion at the SAME
         (kind, label, chapter) — never relabelled, dropped, or narrowed onto a
         different target.
       * Every NEW-only node (extra insertion, bare->insertion upgrade) and every
         NEW-only verb group must carry a ``witness.rule_id`` in the allowed
         recovery-rule set AND a ``witness.source_span`` that lies within the
         johtolause token bounds. A NEW-only node with no source span (e.g. a
         ``fi.jolloin_renumber`` move group materialised from renumber pairs, not
         from the johtolause text) is NOT source-witnessed -> P2 FAILS.

     Any relabel, drop, fewer-nodes, merge/split of groups, non-source-witnessed
     addition, or unknown-rule addition fails P2 (stays unclassified). This is
     the strengthened P2 from the Codex ruling: structural superset is necessary
     but NOT sufficient; every addition must be source-explicit.

Category-C corrections (NEW *replaces* a wrong OLD node, e.g. OLD target =
CHAPTER, NEW = SECTION + momentti) are deliberately NOT auto-detected: a node
replacement is a regression-or-correction ambiguity only source-text reading
resolves. They fail P2 (the OLD node is not preserved) and remain on a
human-judged sid-list in :mod:`census_accounting`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from enum import Enum
from typing import Any, Optional

from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceClause,
    SurfaceInsertion,
    SurfaceScopeBlock,
    SurfaceTargetRef,
    SurfaceVerbGroup,
)

# ---------------------------------------------------------------------------
# Adjudication sub-reasons. The census keeps the closed-FIVE top-level bucket
# contract intact; an adjudicated clause additionally records WHY it was
# adjudicated benign, so "cosmetic provenance drift" and "OLD parsed the wrong
# legal target" are not collapsed into one undifferentiated count (Codex Q4).
# ---------------------------------------------------------------------------
PROVENANCE_ONLY_DELTA = "provenance_only_delta"  # P1
NEW_BETTER_RECOVERY = "new_better_recovery"  # P2
HUMAN_CORRECTED_OLD_WRONG = "human_corrected_old_wrong"  # correction sid-list
SPAN_NORMALIZED = "span_normalized"  # witness-span-only sid-list
LEGACY_ADJUDICATED = "legacy_adjudicated"  # legacy redundant sid-lists (transition guards)

ADJUDICATION_SUB_REASONS: tuple[str, ...] = (
    PROVENANCE_ONLY_DELTA,
    NEW_BETTER_RECOVERY,
    HUMAN_CORRECTED_OLD_WRONG,
    SPAN_NORMALIZED,
    LEGACY_ADJUDICATED,
)


# ---------------------------------------------------------------------------
# Allowed recovery-rule set for P2 additions.
#
# Derived from rule_registry (the ``fi.insertion_*`` insertion families + the
# anaphoric insertion recognizers) AND confirmed against the actual rule_ids on
# the NEW-only / upgraded nodes of the additive-recovery corpus cases. Every
# rule here EMITS a SurfaceInsertion (the "lisätään ... uusi X" recovery the OLD
# reference parse flattened). Deliberately EXCLUDED:
#   * ``fi.jolloin_renumber`` — emits a SIIRTAA move/renumber group from the
#     jolloin renumber pairs, with source_span=None. A NEW-only move group is a
#     CORRECTION (NEW recovers a whole verb group OLD missed), not a benign
#     additive insertion, so it must reach the human sid-list, not P2.
#   * any plain ``fi.section_ref`` / ``fi.chapter_ref`` reference rule — a
#     NEW-only bare reference is not a source-explicit insertion recovery.
# ---------------------------------------------------------------------------
P2_ALLOWED_RECOVERY_RULE_IDS: frozenset[str] = frozenset(
    {
        "fi.insertion_sub_target",
        "fi.insertion_alakohta_into_item",
        "fi.insertion_section",
        "fi.insertion_law_level_bare_section",
        "fi.insertion_momentti_ill",
        "fi.insertion_section_ill",
        "fi.anaphoric_bare_uusi",
        "fi.anaphoric_determiner_insert",
    }
)


def _enum_name(v: Any) -> Any:
    return v.name if isinstance(v, Enum) else v


def _node_witness(node: Any) -> tuple[Optional[str], Optional[tuple[int, int]]]:
    w = getattr(node, "witness", None)
    if w is None:
        return (None, None)
    span = w.source_span
    if span is not None:
        span = (span[0], span[1])
    return (w.rule_id, span)


def _node_content_signature(node: Any) -> tuple[Any, ...]:
    """An identity-free, provenance-free signature of a node's semantic content.

    Built from ``dataclasses.asdict`` with the ``witness`` key dropped and enums
    rendered by name, so two nodes compare equal iff every NON-provenance field
    is equal. Used both to decide "OLD node preserved identically" and to build
    the verb-group matching signature.
    """

    def strip(obj: Any) -> Any:
        if isinstance(obj, Enum):
            return _enum_name(obj)
        if isinstance(obj, dict):
            return tuple(
                (k, strip(v)) for k, v in sorted(obj.items()) if k != "witness"
            )
        if isinstance(obj, (list, tuple)):
            return tuple(strip(x) for x in obj)
        return obj

    return ("__type__", type(node).__name__, strip(asdict(node)))


def _is_real_node(node: Any) -> bool:
    """A 'real' content node — excludes None-kind placeholder nodes.

    OLD-side ``SurfaceTargetRef`` / coordination nodes whose ``kind`` is ``None``
    are structural placeholders (e.g. a DescendantCoordination carrier), not
    targets to be preserved 1:1. They are kept in the signature for matching but
    are not load-bearing for the preservation check.
    """
    kind = getattr(node, "kind", "__no_kind__")
    if kind == "__no_kind__":
        # Non-target node types (coordination, scope blocks, tails) — treat as
        # real content for preservation; they have no ``kind`` field.
        return True
    return kind is not None


# A refinement target is the provenance-free address an OLD reference and the
# NEW insertion that recovers it both live at:
#   (kind, label, chapter, momentti, item, facet_name, special)
# A bare ref has momentti=0/item=''/facet=None; a partial ref carries whatever
# sub-ref OLD captured; the recovering NEW insertion carries the same target with
# a refined (item-suffix-extended) sub-target. ``None`` means the node is not a
# single-target ref/insertion (coordination, scope wrapper, tail, backref).
RefinementTarget = tuple[Any, str, str, int, str, Optional[str], str]


def _subref_fields(sub: Any) -> tuple[int, str, Optional[str], str]:
    if sub is None:
        return (0, "", None, "")
    return (
        sub.momentti,
        sub.item,
        _enum_name(sub.facet) if sub.facet is not None else None,
        sub.special,
    )


def _ref_refinement_target(node: Any) -> Optional[RefinementTarget]:
    """The RefinementTarget for an OLD ``SurfaceTargetRef`` (<=1 sub_ref)."""
    if not isinstance(node, SurfaceTargetRef):
        return None
    if len(node.sub_refs) > 1:
        return None
    sub = node.sub_refs[0] if node.sub_refs else None
    m, item, facet, special = _subref_fields(sub)
    return (_enum_name(node.kind), node.label, node.chapter, m, item, facet, special)


def _insertion_refinement_target(node: Any) -> Optional[RefinementTarget]:
    """The RefinementTarget for a NEW ``SurfaceInsertion``."""
    if not isinstance(node, SurfaceInsertion):
        return None
    m, item, facet, special = _subref_fields(node.sub_target)
    return (_enum_name(node.kind), node.label, node.chapter, m, item, facet, special)


def _new_insertion_satisfies_old_ref(
    old_t: RefinementTarget, new_t: RefinementTarget
) -> bool:
    """True iff a NEW insertion recovers (refines, never narrows) an OLD ref.

    Sound refinement (Codex Q1 strengthening): the inserted sub-unit must be the
    SAME target the OLD reference named, with the sub-reference only EXTENDED at
    the alakohta tail — never a different momentti or a conflicting item.
      * same (kind, label, chapter)  [chapter may be folded in from an OLD scope]
      * momentti consistent: OLD momentti == NEW momentti, OR OLD momentti == 0
        (OLD named no momentti, NEW makes it explicit from the source)
      * item-prefix: OLD item == '' (bare) OR NEW item == OLD item OR NEW item
        starts with OLD item (e.g. '5' -> '5c', '14b' -> '14bc'); NEW item must
        NOT be a *different* item.
      * facet / special unchanged.
    """
    ok, ol, och, om, oi, of, osp = old_t
    nk, nl, nch, nm, ni, nf, nsp = new_t
    if (ok, ol, och) != (nk, nl, nch):
        return False
    if of != nf or osp != nsp:
        return False
    if not (om == nm or om == 0):
        return False
    if oi != "" and not ni.startswith(oi):
        return False
    return True


def _span_within_bounds(
    span: Optional[tuple[int, int]], consumed_count: int
) -> bool:
    if span is None:
        return False
    start, end = span
    return 0 <= start <= end <= consumed_count


# ---------------------------------------------------------------------------
# P1 — provenance-only delta.
# ---------------------------------------------------------------------------
def is_provenance_only_delta(deltas: list[str]) -> bool:
    """True iff EVERY diff line is a witness.rule_id / witness.source_span delta.

    ``deltas`` are the ``path: a != b`` strings from
    :func:`compare_surface_models`. A clause is provenance-only when there is at
    least one delta and every one of them touches only a witness field — i.e.
    all semantic node content (kind/label/chapter/sub_target/is_exception/…) is
    byte-identical and the parsers merely disagree on the recognizer credited or
    the per-node span boundary.
    """
    if not deltas:
        return False
    return all(
        (".witness.rule_id" in d or ".witness.source_span" in d) for d in deltas
    )


# ---------------------------------------------------------------------------
# P2 — source-witnessed additive recovery.
# ---------------------------------------------------------------------------
def _unwrap_scope_block(block: SurfaceScopeBlock) -> list[Any]:
    """Unwrap a scope block into its inner targets with scope chapter/part folded.

    So an OLD chapter-scoped section ref (``1 luvun 4 §``) can be matched against a
    NEW insertion that carries the chapter in its own ``chapter`` field.
    """
    from dataclasses import replace

    out: list[Any] = []
    for inner in block.targets:
        if isinstance(inner, SurfaceTargetRef):
            folded = inner
            if block.scope_kind in (ScopeKind.CHAPTER, "chapter") and not inner.chapter:
                folded = replace(inner, chapter=block.scope_label)
            elif block.scope_kind in (ScopeKind.PART, "part") and not inner.part:
                folded = replace(inner, part=block.scope_label)
            out.append(folded)
        else:
            out.append(inner)
    return out


def _old_group_units(group: SurfaceVerbGroup) -> list[Any]:
    """An OLD verb group's nodes as the units P2 must account for.

    Scope blocks are kept INTACT here (so a preserved scope block matches its
    byte-identical NEW counterpart). They are only unwrapped lazily during the
    recovery step, when no identical NEW node preserves them. None-kind
    placeholder targets are dropped.
    """
    return [n for n in group.nodes if _is_real_node(n)]


def _group_match_signature(group: SurfaceVerbGroup) -> tuple[Any, ...]:
    """Signature used to match OLD<->NEW verb groups by content, not index.

    The verb plus the multiset of the group's target *labels* (the (kind,label)
    of each ref/insertion/scoped-target) — the part stable under additive
    recovery. A drop or relabel of a target changes this multiset and so defeats
    the match, which is the fail-loud property we want; an inserted leading NEW
    group has a different verb/label set and so cannot steal an OLD group's match.
    """
    verb = _enum_name(group.verb)
    keys: list[Any] = []
    units: list[Any] = []
    for n in _old_group_units(group):
        if isinstance(n, SurfaceScopeBlock):
            units.extend(_unwrap_scope_block(n))
        else:
            units.append(n)
    for n in units:
        rt = _ref_refinement_target(n) or _insertion_refinement_target(n)
        if rt is not None:
            keys.append(("tgt", rt[0], rt[1]))  # (kind, label)
        else:
            keys.append(("node", _node_content_signature(n)))
    return (verb, tuple(sorted(keys, key=repr)))


def _new_group_preserves_old(
    old_group: SurfaceVerbGroup,
    new_group: SurfaceVerbGroup,
    new_consumed: int,
) -> bool:
    """True iff ``new_group`` preserves every OLD unit and only ADDS
    source-witnessed insertion material on top.

    Returns False (fail-loud) on any drop, relabel, narrowing, or
    non-source-witnessed addition.
    """
    if _enum_name(old_group.verb) != _enum_name(new_group.verb):
        return False

    old_units = _old_group_units(old_group)
    new_nodes = list(new_group.nodes)
    new_used = [False] * len(new_nodes)
    new_sigs = [_node_content_signature(n) for n in new_nodes]

    # Process scope blocks last: try to preserve them intact first, and only
    # unwrap (for refinement matching) the ones with no identical NEW counterpart.
    pending: list[Any] = []
    for old_node in old_units:
        old_sig = _node_content_signature(old_node)
        matched = False
        for i, used in enumerate(new_used):
            if not used and new_sigs[i] == old_sig:
                new_used[i] = True
                matched = True
                break
        if matched:
            continue
        if isinstance(old_node, SurfaceScopeBlock):
            # No identical NEW scope block — unwrap to inner targets and try to
            # recover each below.
            pending.extend(_unwrap_scope_block(old_node))
        else:
            pending.append(old_node)

    for old_node in pending:
        # (1) exact preservation: an identical NEW node exists.
        old_sig = _node_content_signature(old_node)
        matched = False
        for i, used in enumerate(new_used):
            if not used and new_sigs[i] == old_sig:
                new_used[i] = True
                matched = True
                break
        if matched:
            continue

        # (2) ref -> insertion recovery: a NEW insertion refines this OLD ref at
        # the same target (sound refinement only; never a narrowing/relabel).
        old_rt = _ref_refinement_target(old_node)
        if old_rt is not None:
            recovered = False
            for i, used in enumerate(new_used):
                if used:
                    continue
                cand = new_nodes[i]
                cand_rt = _insertion_refinement_target(cand)
                if cand_rt is None:
                    continue
                if not _new_insertion_satisfies_old_ref(old_rt, cand_rt):
                    continue
                rid, span = _node_witness(cand)
                if (
                    rid in P2_ALLOWED_RECOVERY_RULE_IDS
                    and _span_within_bounds(span, new_consumed)
                ):
                    new_used[i] = True
                    recovered = True
                    break
            if recovered:
                continue

        # OLD unit neither preserved nor source-witnessed-recovered -> a drop,
        # relabel, or narrowing. FAIL LOUD.
        return False

    # Every remaining (unused) NEW node is a NEW-only addition: it must be a
    # source-witnessed insertion under an allowed recovery rule.
    for i, used in enumerate(new_used):
        if used:
            continue
        cand = new_nodes[i]
        if not isinstance(cand, SurfaceInsertion):
            return False
        rid, span = _node_witness(cand)
        if rid not in P2_ALLOWED_RECOVERY_RULE_IDS:
            return False
        if not _span_within_bounds(span, new_consumed):
            return False

    return True


def is_source_witnessed_additive_recovery(
    old: SurfaceClause, new: SurfaceClause
) -> bool:
    """True iff NEW is a source-witnessed additive recovery of OLD (strengthened P2).

    Preconditions: the two models already differ (caller has a non-empty delta).
    Returns False whenever the relationship is anything other than "NEW preserves
    all OLD content and every addition is source-explicit under a named recovery
    rule" — including any drop, relabel, narrowing, group merge/split, or a
    NEW-only group/node that is not a source-witnessed insertion.
    """
    old_groups = list(old.verb_groups)
    new_groups = list(new.verb_groups)
    new_consumed = new.consumed_count

    # Bipartite OLD->NEW group matching by content signature (Codex Q2). Every
    # OLD group must match exactly one NEW group that preserves all its real
    # nodes; an inserted leading NEW group must not shift indices into a false
    # match or mask a drop.
    new_taken = [False] * len(new_groups)
    for og in old_groups:
        og_sig = _group_match_signature(og)
        candidates = [
            i
            for i, ng in enumerate(new_groups)
            if not new_taken[i] and _enum_name(ng.verb) == _enum_name(og.verb)
        ]
        # Prefer an exact-signature match (a byte-identical group); else any verb
        # match that genuinely preserves all OLD real nodes.
        chosen: Optional[int] = None
        for i in candidates:
            if _group_match_signature(new_groups[i]) == og_sig:
                chosen = i
                break
        if chosen is None:
            for i in candidates:
                if _new_group_preserves_old(og, new_groups[i], new_consumed):
                    chosen = i
                    break
        if chosen is None:
            return False
        if not _new_group_preserves_old(og, new_groups[chosen], new_consumed):
            return False
        new_taken[chosen] = True

    # Any unmatched NEW group is a NEW-only verb group. Allow it ONLY if every one
    # of its nodes is a source-witnessed insertion under an allowed recovery rule.
    # (A NEW-only SIIRTAA move group from fi.jolloin_renumber has span=None nodes
    # and so fails here — correctly routing the move-recovery corrections to the
    # human sid-list rather than P2.)
    for i, taken in enumerate(new_taken):
        if taken:
            continue
        ng = new_groups[i]
        if not ng.nodes:
            return False
        for node in ng.nodes:
            if not isinstance(node, SurfaceInsertion):
                return False
            rid, span = _node_witness(node)
            if rid not in P2_ALLOWED_RECOVERY_RULE_IDS:
                return False
            if not _span_within_bounds(span, new_consumed):
                return False

    return True


# ---------------------------------------------------------------------------
# Sub-reason histogram helper.
# ---------------------------------------------------------------------------
def empty_sub_reason_counts() -> dict[str, int]:
    """Materialize every sub-reason at zero, so the histogram is the closed set."""
    return {r: 0 for r in ADJUDICATION_SUB_REASONS}


def sub_reason_histogram(counter: Counter[str]) -> dict[str, int]:
    out = empty_sub_reason_counts()
    for k, v in counter.items():
        out[k] = v
    return out
