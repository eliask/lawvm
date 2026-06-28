"""lawvm uk-cross-statute-graph -- read-only UK cross-statute reference/delegation graph.

§23. A read-only, additive instrumentation surface that extracts and exposes the
graph of **cross-statute references and delegations** in the UK corpus: edges
where one UK provision references / applies / confers-power-under / amends /
commences another statute's provision.

Thesis discipline (AGENTS.md): this surface RECONSTRUCTS the spec from
observations. It observes compiled output (the effects feed plus the
already-built UK source-adjudication classifiers); it NEVER replays, lowers,
grafts, or mutates any op. Code here is disposable; the auditable edge artifact
is the value.

Edge model
----------
Each effect in a statute's effects feed is a directed edge::

    (source_statute, source_provision) --relation--> (target_statute, target_provision)

The effects feed of an *affected* statute lists effects whose *affecting* act is
the SOURCE of the edge (the act making the change / conferring the power) and
whose *affected* statute is the TARGET (the act being changed / under which a
power is exercised). This orientation matches the citation graph: the affecting
instrument cites into the affected statute.

Relation taxonomy (``relation`` ∈)::

    amends                  inserted / substituted / added (text/tree mutation)
    repeals                 repealed / omitted / revoked
    commences               coming into force / appointed day / commencement order
    applies_by_reference    "applied by …" — N4/deixis frontier family
    confers_power           transfer of functions / power conferred / delegation
    modifies                modified / modification / extension / restriction
    references              residual cross-statute reference (unclassified)

The ``applies_by_reference`` family is the N4/deixis frontier
(``deixis_application_claim`` just landed): when the applying provision is
identified deictically ("(as inserted)"), the edge's ``deictic_target_unresolved``
flag is set and ``target_provision`` is kept as the unresolved surface — never
silently resolved.

Reused extraction surfaces (NOT reinvented)
-------------------------------------------
- ``lawvm.uk_legislation.effects.load_effects_for_statute_from_archive`` — the
  parsed ``UKEffectRecord`` feed (effect_id, effect_type, affecting/affected
  URIs and provisions, applied/structural flags).
- ``lawvm.uk_legislation.effects.STRUCTURAL_EFFECT_TYPES`` and the
  ``UKEffectRecord`` structural/renumber/repealed-by predicates.
- ``lawvm.uk_legislation.source_adjudication._looks_like_application_by_reference_deixis_source``
  — the N4 deixis sensor, for the ``deictic_target_unresolved`` flag.
- ``lawvm.core.evidence_surface_report.EvidenceSurfaceReport`` — the deterministic
  diffable JSON envelope used by every other read-only UK report tool.

New here
--------
- The ``relation`` taxonomy and the ``effect_type → relation`` classifier.
- Edge/node canonicalization and deterministic ordering.
- Dangling-target detection (targets whose statute is absent from the base
  corpus — the pre-1994 acquisition gap) and delegation-depth summary.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, NamedTuple, Optional

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.uk_legislation.effects import (
    STRUCTURAL_EFFECT_TYPES,
    _is_uk_renumber_effect_type,
    _is_uk_repealed_by_effect_type,
)
from lawvm.uk_legislation.source_adjudication import (
    _looks_like_application_by_reference_deixis_source,
)

if TYPE_CHECKING:
    import argparse
    from farchive import Farchive
    from lawvm.uk_legislation.effects import UKEffectRecord

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_DEFAULT_APPLICABILITY_MODE = "effective_date_plus_feed_applied"

# ── Relation taxonomy ─────────────────────────────────────────────────────────
RELATION_AMENDS = "amends"
RELATION_REPEALS = "repeals"
RELATION_COMMENCES = "commences"
RELATION_APPLIES_BY_REFERENCE = "applies_by_reference"
RELATION_CONFERS_POWER = "confers_power"
RELATION_MODIFIES = "modifies"
RELATION_REFERENCES = "references"

#: Canonical, stable ordering for relation buckets in every summary block.
RELATION_ORDER: tuple[str, ...] = (
    RELATION_AMENDS,
    RELATION_REPEALS,
    RELATION_COMMENCES,
    RELATION_APPLIES_BY_REFERENCE,
    RELATION_CONFERS_POWER,
    RELATION_MODIFIES,
    RELATION_REFERENCES,
)

_REPEAL_EFFECT_TYPES = frozenset(
    {
        "repealed",
        "entry repealed",
        "repealed in part",
        "words repealed",
        "word repealed",
        "omitted",
        "entry omitted",
        "words omitted",
        "word omitted",
        "revoked",
        "revoked in part",
    }
)

_COMMENCEMENT_EFFECT_TYPES = frozenset(
    {
        "appointed day(s)",
        "appointed day",
        "coming into force",
        "commencement order",
    }
)

# Substrings that mark a delegation / power-conferral / function-transfer edge.
# These are the cross-statute "confers power under" / delegation surfaces: the
# affecting instrument relocates or confers an executive power rather than
# mutating the affected text.
_CONFERS_POWER_SUBSTRINGS: tuple[str, ...] = (
    "transfer of functions",
    "functions transferred",
    "functions made exercisable",
    "power to make",
    "power conferred",
    "power exercised",
    "delegated",
    "delegation",
)

# Substrings that mark a non-mutating modification / extension / restriction:
# the affected provision is qualified in scope/extent without a tree edit.
_MODIFIES_SUBSTRINGS: tuple[str, ...] = (
    "modified",
    "modification",
    "extended",
    "extension",
    "restricted",
    "restriction",
    "excluded",
    "exclusion",
    "disapplied",
    "applied with modifications",
)


def _normalize_effect_type(effect_type: str) -> str:
    return " ".join(str(effect_type or "").strip().lower().split())


def classify_uk_cross_statute_relation(effect_type: str) -> str:
    """Map an effects-feed ``effect_type`` to a cross-statute graph relation.

    Deterministic and total: every effect type resolves to exactly one relation,
    falling back to ``references`` for unclassified cross-statute citations. The
    structural amend/repeal partition reuses ``effects.STRUCTURAL_EFFECT_TYPES``
    and the renumber/repealed-by predicates rather than re-listing them.
    """
    norm = _normalize_effect_type(effect_type)
    if not norm:
        # Empty type is the structural "applied" shell in the feed; treat as a
        # generic amendment edge (it is structural-for-replay when applied).
        return RELATION_AMENDS

    if norm.startswith("applied by "):
        return RELATION_APPLIES_BY_REFERENCE

    if norm in _COMMENCEMENT_EFFECT_TYPES or "commencement" in norm or "appointed day" in norm:
        return RELATION_COMMENCES

    if norm in _REPEAL_EFFECT_TYPES or _is_uk_repealed_by_effect_type(norm) or norm.startswith("revoked"):
        return RELATION_REPEALS

    if any(token in norm for token in _CONFERS_POWER_SUBSTRINGS):
        return RELATION_CONFERS_POWER

    if norm in STRUCTURAL_EFFECT_TYPES or _is_uk_renumber_effect_type(norm):
        return RELATION_AMENDS

    if any(token in norm for token in _MODIFIES_SUBSTRINGS):
        return RELATION_MODIFIES

    # Recognized amendment verbs that are not in the exact structural set but
    # are unambiguously tree/text edits.
    if re.search(r"\b(?:inserted|substituted|added|renumbered)\b", norm):
        return RELATION_AMENDS

    return RELATION_REFERENCES


# ── Edge model ────────────────────────────────────────────────────────────────


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())


class CrossStatuteEdge(NamedTuple):
    """One directed cross-statute reference/delegation edge.

    ``source_*`` is the affecting instrument/provision (the citing side); the
    ``target_*`` is the affected statute/provision (the cited side).
    """

    source_statute: str
    source_provision: str
    target_statute: str
    target_provision: str
    relation: str
    effect_id: str
    effect_type: str
    applied: bool
    deictic_target_unresolved: bool
    target_in_base: bool

    @property
    def sort_key(self) -> tuple[str, ...]:
        """Canonical ordering key — fully deterministic, no insertion-order leak."""
        return (
            self.relation,
            self.source_statute,
            self.source_provision,
            self.target_statute,
            self.target_provision,
            self.effect_type,
            self.effect_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "source_statute": self.source_statute,
            "source_provision": self.source_provision,
            "target_statute": self.target_statute,
            "target_provision": self.target_provision,
            "effect_id": self.effect_id,
            "effect_type": self.effect_type,
            "applied": self.applied,
            "deictic_target_unresolved": self.deictic_target_unresolved,
            "target_in_base": self.target_in_base,
        }


def _affected_statute_id(
    effect: "UKEffectRecord",
    *,
    fallback: str,
    unmapped_diagnostics_out: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Resolve the affected statute id (edge target) from one effect record.

    Prefers the explicit affected slug carried on the effect URI; the feed is
    loaded per affected statute, so ``fallback`` (the queried statute id) is the
    safe default when the record carries no usable affected slug.

    For an effect with a non-empty ``affected_class`` that does not map to a
    real document-type slug — the same anti-example flagged in AGENTS.md §1.10
    — the former behaviour fell back to ``cls.lower()`` (e.g.
    ``northernirelandact/2016/10``), producing an edge target that 404s at any
    downstream archive lookup. The shared ``affecting_class_slug`` helper now
    raises ``UnmappedAffectingClass`` instead; here we catch that raise and
    fall through to ``fallback`` (the queried statute id, a defensible
    self-edge) while emitting a typed ``uk_affected_act_class_unmapped_rejected``
    finding via ``unmapped_diagnostics_out`` so the residual is owned rather
    than silent. A soft fall-through is preferred at this site because the
    cross-statute-graph is a read-only observation surface: a hard crash on one
    unmapped record would lose every other edge in the same feed.
    """
    from lawvm.core.diagnostic_records import diagnostic_detail
    from lawvm.uk_legislation.affecting_class import (
        UnmappedAffectingClass,
        _UK_AFFECTING_URI_SLUG_RE,
        affecting_class_slug,
    )

    uri_match = _UK_AFFECTING_URI_SLUG_RE.search(_clean(getattr(effect, "affected_uri", "")))
    if uri_match:
        return f"{uri_match.group(1)}/{uri_match.group(2)}/{uri_match.group(3)}"
    cls = _clean(getattr(effect, "affected_class", ""))
    year = _clean(getattr(effect, "affected_year", ""))
    number = _clean(getattr(effect, "affected_number", ""))
    if year and number and cls:
        try:
            slug = affecting_class_slug(cls, year=year, number=number)
        except UnmappedAffectingClass as exc:
            if unmapped_diagnostics_out is not None:
                unmapped_diagnostics_out.append(
                    diagnostic_detail(
                        rule_id="uk_affected_act_class_unmapped_rejected",
                        family="source_pathology",
                        phase="acquisition",
                        reason=(
                            "UK affected act class has no document-type slug mapping and "
                            "the effect carried no resolvable affected URI, so the edge "
                            "target id was emited as the source statute id (self-edge "
                            "fallback). Add a class-to-slug mapping (or a usable "
                            "AffectingURI) rather than treating this edge as authoritative."
                        ),
                        blocking=False,
                        detail={
                            "effect_id": _clean(getattr(effect, "effect_id", "")),
                            "affected_class": exc.cls,
                            "affected_year": exc.year,
                            "affected_number": exc.number,
                            "hint": exc.hint,
                            "fallback_statute_id": fallback,
                        },
                    )
                )
            return fallback
        return f"{slug}/{year}/{number}"
    return fallback


def edge_from_effect(
    effect: "UKEffectRecord",
    *,
    affected_statute_id: str,
    base_statute_ids: Optional[set[str]] = None,
    unmapped_diagnostics_out: Optional[list[dict[str, Any]]] = None,
) -> CrossStatuteEdge:
    """Build one cross-statute edge from a single effects-feed record.

    Pure: no archive, no replay. ``base_statute_ids`` (when given) is the set of
    statute ids present in the base corpus, used only to flag dangling targets.

    ``unmapped_diagnostics_out`` (when given) receives a typed finding per
    effect whose affected class fails to resolve to a real document-type slug;
    the edge is still emitted against the queried statute id (a self-edge
    fallback) so the cross-statute-graph never silently drops records.
    """
    relation = classify_uk_cross_statute_relation(effect.effect_type)
    target_statute = _affected_statute_id(
        effect,
        fallback=affected_statute_id,
        unmapped_diagnostics_out=unmapped_diagnostics_out,
    )
    deictic = (
        relation == RELATION_APPLIES_BY_REFERENCE
        and _looks_like_application_by_reference_deixis_source(effect.effect_type)
    )
    target_in_base = (
        target_statute in base_statute_ids if base_statute_ids is not None else True
    )
    # ``effect.affecting_act_id`` raises ``UnmappedAffectingClass`` when the
    # affecting class is unmapped AND no usable AffectingURI is available
    # (AGENTS.md §1.10); the cross-statute-graph flags that case from the
    # AFFECTED side above. For the affecting side, we mirror that handling at
    # the caller (``edges_for_statute``) via ``affecting_class_is_recognized``,
    # so a single unmapped-affecting record never crashes the whole feed.
    return CrossStatuteEdge(
        source_statute=_clean(effect.affecting_act_id),
        source_provision=_clean(effect.affecting_provisions),
        target_statute=_clean(target_statute),
        target_provision=_clean(effect.affected_provisions),
        relation=relation,
        effect_id=_clean(effect.effect_id),
        effect_type=_clean(effect.effect_type),
        applied=bool(effect.applied or getattr(effect, "metadata_only", False)),
        deictic_target_unresolved=deictic,
        target_in_base=target_in_base,
    )


def edges_for_statute(
    statute_id: str,
    *,
    archive: "Farchive",
    base_statute_ids: Optional[set[str]] = None,
    parse_rejections_out: Optional[list[dict[str, Any]]] = None,
) -> tuple[CrossStatuteEdge, ...]:
    """Extract all cross-statute edges from one statute's effects feed.

    Reuses ``load_effects_for_statute_from_archive`` for parsing; emits one edge
    per effect, canonically sorted. READ-ONLY.

    An effect whose ``affecting_class`` is unmapped AND has no usable
    AffectingURI cannot resolve a real source statute id — ``effect.
    affecting_act_id`` raises ``UnmappedAffectingClass`` (AGENTS.md §1.10). Rather
    than crashing the whole feed, the predicate is checked first, a typed
    ``uk_affecting_act_class_unmapped_rejected`` finding is emitted via
    ``parse_rejections_out``, and that one record is skipped; the remaining
    edges are still produced. The AFFECTED class unmapped case is handled inside
    ``_affected_statute_id`` (falling back to a self-edge) so the affected side
    never silent-drops.
    """
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.uk_legislation.source_state import (
        uk_affecting_act_class_unmapped_rejection,
    )

    effects = load_effects_for_statute_from_archive(
        statute_id,
        archive,
        parse_rejections_out=parse_rejections_out,
    )
    edges: list[CrossStatuteEdge] = []
    for effect in effects:
        if not effect.affecting_class_is_recognized:
            # Affecting class has no slug mapping AND no usable AffectingURI;
            # the source-statute side of the edge cannot be resolved to a real
            # document-type slug. ``effect.affecting_act_id`` would raise
            # ``UnmappedAffectingClass`` (AGENTS.md §1.10). Surface the residual
            # loudly and skip just this record so the rest of the feed survives.
            if parse_rejections_out is not None:
                parse_rejections_out.append(
                    uk_affecting_act_class_unmapped_rejection(
                        effect_id=str(effect.effect_id or ""),
                        affecting_act_id="",
                        locator=str(effect.affecting_uri or ""),
                        affecting_class=str(effect.affecting_class or ""),
                    )
                )
            continue
        edges.append(
            edge_from_effect(
                effect,
                affected_statute_id=statute_id,
                base_statute_ids=base_statute_ids,
                unmapped_diagnostics_out=parse_rejections_out,
            )
        )
    return tuple(sorted(edges, key=lambda edge: edge.sort_key))


# ── Summary statistics ────────────────────────────────────────────────────────


def _relation_count_block(edges: Iterable[CrossStatuteEdge]) -> dict[str, int]:
    counts = {relation: 0 for relation in RELATION_ORDER}
    for edge in edges:
        counts[edge.relation] = counts.get(edge.relation, 0) + 1
    return counts


def _delegation_depth(edges: Iterable[CrossStatuteEdge]) -> dict[str, Any]:
    """Longest delegation/application chain across statutes (citation depth).

    A delegation edge (``confers_power`` or ``applies_by_reference``) links a
    source statute to a target statute. The depth is the longest path in the
    DAG-projection of those edges (cycles are broken by a visited stack). This
    is a coarse instrumentation figure, not a legal claim.
    """
    adjacency: dict[str, set[str]] = {}
    nodes: set[str] = set()
    for edge in edges:
        if edge.relation not in (RELATION_CONFERS_POWER, RELATION_APPLIES_BY_REFERENCE):
            continue
        if not edge.source_statute or not edge.target_statute:
            continue
        if edge.source_statute == edge.target_statute:
            continue
        adjacency.setdefault(edge.source_statute, set()).add(edge.target_statute)
        nodes.add(edge.source_statute)
        nodes.add(edge.target_statute)

    memo: dict[str, int] = {}

    def depth(node: str, stack: frozenset[str]) -> int:
        if node in memo:
            return memo[node]
        best = 0
        for succ in sorted(adjacency.get(node, ())):
            if succ in stack:
                continue
            best = max(best, 1 + depth(succ, stack | {node}))
        memo[node] = best
        return best

    max_depth = 0
    for node in sorted(nodes):
        max_depth = max(max_depth, depth(node, frozenset()))
    return {
        "delegation_edge_count": sum(len(v) for v in adjacency.values()),
        "delegation_nodes": len(nodes),
        "max_delegation_depth": max_depth,
    }


def cross_statute_graph_summary(edges: tuple[CrossStatuteEdge, ...]) -> dict[str, Any]:
    """Deterministic summary stats over a collection of canonical edges."""
    source_nodes = sorted({(edge.source_statute, edge.source_provision) for edge in edges})
    target_nodes = sorted({(edge.target_statute, edge.target_provision) for edge in edges})
    source_statutes = sorted({edge.source_statute for edge in edges if edge.source_statute})
    target_statutes = sorted({edge.target_statute for edge in edges if edge.target_statute})
    dangling_edges = tuple(edge for edge in edges if not edge.target_in_base)
    dangling_target_statutes = sorted(
        {edge.target_statute for edge in dangling_edges if edge.target_statute}
    )
    deictic_unresolved = tuple(edge for edge in edges if edge.deictic_target_unresolved)
    return {
        "edge_count": len(edges),
        "source_node_count": len(source_nodes),
        "target_node_count": len(target_nodes),
        "source_statute_count": len(source_statutes),
        "target_statute_count": len(target_statutes),
        "edge_counts_by_relation": _relation_count_block(edges),
        "applied_edge_count": sum(1 for edge in edges if edge.applied),
        "dangling_target": {
            "edge_count": len(dangling_edges),
            "statute_count": len(dangling_target_statutes),
            "statutes": dangling_target_statutes,
        },
        "deictic_unresolved": {
            "edge_count": len(deictic_unresolved),
        },
        "delegation_depth": _delegation_depth(edges),
    }


def cross_statute_graph_report_jsonable(
    *,
    statute_ids: tuple[str, ...],
    edges: tuple[CrossStatuteEdge, ...],
    archive_path: str,
    applicability_mode: str = _DEFAULT_APPLICABILITY_MODE,
    base_statute_ids: Optional[set[str]] = None,
    parse_rejections: tuple[dict[str, Any], ...] = (),
    summary_only: bool = False,
) -> dict[str, Any]:
    """Build the deterministic, diffable evidence-surface report.

    Canonical and order-invariant: edges are sorted by ``sort_key`` and the
    ``statute_ids`` header is sorted/deduped, so the JSON is byte-identical
    regardless of the order statutes were supplied in.
    """
    sorted_edges = tuple(sorted(edges, key=lambda edge: edge.sort_key))
    canonical_statute_ids = sorted({str(sid) for sid in statute_ids if str(sid)})
    summary = cross_statute_graph_summary(sorted_edges)
    rows = () if summary_only else tuple(edge.to_dict() for edge in sorted_edges)
    detail: dict[str, Any] = {
        "statute_ids": canonical_statute_ids,
        "archive_path": archive_path,
        "applicability_mode": applicability_mode,
        "relation_taxonomy": list(RELATION_ORDER),
        "base_corpus_known": base_statute_ids is not None,
        "base_statute_count": (
            len(base_statute_ids) if base_statute_ids is not None else 0
        ),
        "effect_feed_parse_observation_count": len(parse_rejections),
        "safe_default": "observe_citation_graph_without_authorizing_replay",
        "forbidden_shortcuts": (
            "edge_as_replay_authorization",
            "deictic_target_as_resolved_target",
            "dangling_target_as_no_op",
            "delegation_depth_as_legal_authority_claim",
        ),
        "next_promotion_requires": (
            "affecting_source_identity",
            "target_identity",
            "deixis_resolution_proof",
        ),
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_cross_statute_graph",
        schema="lawvm.uk_cross_statute_graph.v1",
        truth_claim="uk_cross_statute_reference_and_delegation_graph_observation_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_ids": canonical_statute_ids,
            "applicability_mode": applicability_mode,
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        detail=detail,
    ).to_dict()


# ── CLI ───────────────────────────────────────────────────────────────────────


def _collect_base_statute_ids(archive: "Farchive") -> set[str]:
    """Enumerate statute ids present in the base corpus (enacted/current XML).

    Used to flag dangling targets: a target statute whose enacted/current XML is
    absent from the archive (the pre-1994 acquisition gap). Read-only locator
    scan; no parse.
    """
    base: set[str] = set()
    pattern = re.compile(r"/([a-z]{1,16})/(\d{1,9})/(\d{1,9})/(?:enacted/)?data\.xml$")
    for locator in archive.locators("%/data.xml"):
        match = pattern.search(locator)
        if match:
            base.add(f"{match.group(1)}/{match.group(2)}/{match.group(3)}")
    return base


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive

    statute_ids: tuple[str, ...] = tuple(getattr(args, "statute_id", []) or [])
    if not statute_ids:
        print("error: at least one statute_id is required", file=sys.stderr)
        sys.exit(1)
    json_output: bool = bool(getattr(args, "json", False))
    summary_only: bool = bool(getattr(args, "summary_only", False))
    relation_filter: Optional[str] = getattr(args, "relation", None)
    db_arg: Optional[str] = getattr(args, "db", None)
    applicability_mode: str = (
        getattr(args, "uk_applicability_mode", None) or _DEFAULT_APPLICABILITY_MODE
    )
    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    with Farchive(db_path) as archive:
        base_statute_ids = _collect_base_statute_ids(archive)
        all_edges: list[CrossStatuteEdge] = []
        parse_rejections: list[dict[str, Any]] = []
        for statute_id in statute_ids:
            all_edges.extend(
                edges_for_statute(
                    statute_id,
                    archive=archive,
                    base_statute_ids=base_statute_ids,
                    parse_rejections_out=parse_rejections,
                )
            )

    if relation_filter:
        all_edges = [edge for edge in all_edges if edge.relation == relation_filter]

    edges = tuple(sorted(all_edges, key=lambda edge: edge.sort_key))

    if json_output:
        print(
            json.dumps(
                cross_statute_graph_report_jsonable(
                    statute_ids=statute_ids,
                    edges=edges,
                    archive_path=str(db_path),
                    applicability_mode=applicability_mode,
                    base_statute_ids=base_statute_ids,
                    parse_rejections=tuple(parse_rejections),
                    summary_only=summary_only,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return

    summary = cross_statute_graph_summary(edges)
    print(f"Statutes:           {', '.join(statute_ids)}")
    print(f"Archive:            {db_path}")
    print(f"Base corpus size:   {len(base_statute_ids)} statutes")
    print(f"Edges:              {summary['edge_count']}")
    print(f"Source nodes:       {summary['source_node_count']}")
    print(f"Target nodes:       {summary['target_node_count']}")
    print("Edge counts by relation:")
    for relation in RELATION_ORDER:
        print(f"  {relation:<22} {summary['edge_counts_by_relation'][relation]}")
    dangling = summary["dangling_target"]
    print(
        f"Dangling targets:   {dangling['edge_count']} edges "
        f"into {dangling['statute_count']} absent statute(s)"
    )
    deictic = summary["deictic_unresolved"]
    print(f"Deictic unresolved: {deictic['edge_count']} edges")
    depth = summary["delegation_depth"]
    print(
        f"Delegation depth:   max={depth['max_delegation_depth']} "
        f"over {depth['delegation_nodes']} nodes / {depth['delegation_edge_count']} edges"
    )
    if not summary_only:
        print()
        for edge in edges:
            flags = []
            if edge.deictic_target_unresolved:
                flags.append("deictic?")
            if not edge.target_in_base:
                flags.append("dangling")
            if not edge.applied:
                flags.append("unapplied")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            print(
                f"  {edge.relation:<22} "
                f"{edge.source_statute} {edge.source_provision} "
                f"--> {edge.target_statute} {edge.target_provision}"
                f"{flag_str}"
            )


__all__ = [
    "RELATION_ORDER",
    "RELATION_AMENDS",
    "RELATION_REPEALS",
    "RELATION_COMMENCES",
    "RELATION_APPLIES_BY_REFERENCE",
    "RELATION_CONFERS_POWER",
    "RELATION_MODIFIES",
    "RELATION_REFERENCES",
    "CrossStatuteEdge",
    "classify_uk_cross_statute_relation",
    "edge_from_effect",
    "edges_for_statute",
    "cross_statute_graph_summary",
    "cross_statute_graph_report_jsonable",
    "main",
]
