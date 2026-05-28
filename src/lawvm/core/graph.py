"""core/graph.py — Unified graph model for LawVM.

Phase 9.1: Converges the three separate data paths (amendments via timelines,
delegations, cross-references) into a single queryable container.

Data model:
  StatuteGraph  — all graph data for a single statute
  CorpusGraph   — all graph data for a set of statutes, with cross-statute queries

Entry points:
  Data/query types live here.
  Build orchestration lives in ``lawvm.graph_build``.

API tier
--------
Stable data/query surface. Build orchestration and jurisdiction-specific graph
construction policy are intentionally outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from lawvm.contracts import ArtifactEnvelope, ProcessingStatus, to_wire_jsonable
from lawvm.core.authority import BranchGraphEdge, LegalBranch
from lawvm.core.timeline import Timelines


class DelegationEdgeLike(Protocol):
    statute_id: str
    section: str
    delegation_type: str
    eid: str
    match_text: str
    quote: str


class CitationEdgeLike(Protocol):
    source_statute_id: str
    source_section: str
    target_statute_id: str
    target_section: str
    edge_type: str
    count: int
    target_stat_hash: str


def _delegation_sort_key(edge: DelegationEdgeLike) -> tuple:
    """Canonical sort key for delegation-like edge rows."""
    return (
        edge.statute_id,
        edge.section,
        edge.delegation_type,
        edge.eid,
        edge.match_text,
        edge.quote,
    )


def _citation_sort_key(edge: CitationEdgeLike) -> tuple:
    """Canonical sort key for citation-like edge rows."""
    return (
        edge.source_statute_id,
        edge.source_section,
        edge.target_statute_id,
        edge.target_section,
        edge.edge_type,
        edge.count,
        edge.target_stat_hash,
    )


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BuildMeta:
    """Reproducibility metadata attached to a CorpusGraph build."""
    built_at: str = ""           # ISO-8601 UTC timestamp
    lawvm_commit: str = ""       # git commit SHA of this lawvm installation
    schema_version: str = "9.5"  # bump when CorpusGraph shape changes
    corpus_size: int = 0         # number of statutes in the input corpus
    failed_statutes: int = 0     # number of statutes that failed to build


@dataclass
class StatuteGraph:
    """All graph data for a single statute.

    timelines       — provision-level amendment history (from compile_timelines)
    delegations     — outgoing delegation clauses (DelegationEdge list)
    citations       — outgoing cross-reference edges (CrossRefEdge list)
    amendment_chain — statute IDs that have amended this statute
    title           — human-readable title (empty if not loaded)
    statute_type    — act / decree / decision / ... (empty if not loaded)
    """
    statute_id: str
    timelines: Timelines = field(default_factory=dict)
    delegations: list = field(default_factory=list)    # List[DelegationEdge]
    citations: list = field(default_factory=list)      # List[CrossRefEdge]
    branches: List[LegalBranch] = field(default_factory=list)
    branch_edges: List[BranchGraphEdge] = field(default_factory=list)
    amendment_chain: List[str] = field(default_factory=list)
    title: str = ""
    statute_type: str = ""


@dataclass
class CorpusGraph:
    """All graph data for a set of statutes, with cross-statute query methods.

    timelines       — {statute_id: Timelines} (empty when built with with_timelines=False)
    delegations     — all DelegationEdges across the corpus
    citations       — all CrossRefEdges across the corpus
    amendment_index — {parent_statute_id: [amending_statute_ids]}
    statute_meta    — {statute_id: {title, statute_type}} for all loaded statutes
    """
    timelines: Dict[str, Timelines] = field(default_factory=dict)
    delegations: list = field(default_factory=list)    # List[DelegationEdge]
    citations: list = field(default_factory=list)      # List[CrossRefEdge]
    branches: List[LegalBranch] = field(default_factory=list)
    branch_edges: List[BranchGraphEdge] = field(default_factory=list)
    amendment_index: Dict[str, List[str]] = field(default_factory=dict)
    statute_meta: Dict[str, dict] = field(default_factory=dict)
    build_failures: List[dict] = field(default_factory=list)
    build_meta: Optional[BuildMeta] = None
    processing_status: ProcessingStatus = field(
        default_factory=lambda: ProcessingStatus(kind="complete")
    )

    def __post_init__(self) -> None:
        """Fail loudly when the build status contradicts the recorded failures."""
        failure_count = len(self.build_failures)
        status_kind = self.processing_status.kind
        blockers = tuple(self.processing_status.blockers or ())

        if self.build_meta is not None and self.build_meta.failed_statutes != failure_count:
            raise ValueError(
                "CorpusGraph.build_meta.failed_statutes must match build_failures "
                f"({self.build_meta.failed_statutes!r} != {failure_count!r})"
            )

        if failure_count:
            if status_kind != "partial" or not blockers:
                raise ValueError(
                    "CorpusGraph with build_failures must have a partial processing_status "
                    "with blockers"
                )
        else:
            if status_kind != "complete" or blockers:
                raise ValueError(
                    "CorpusGraph without build_failures must have a complete processing_status "
                    "with no blockers"
                )

    # ── Query methods ────────────────────────────────────────────────────────

    def get_statute(self, sid: str) -> Optional[StatuteGraph]:
        """Return a StatuteGraph view for one statute, or None if not loaded."""
        if sid not in self.timelines and sid not in self.statute_meta:
            return None
        meta = self.statute_meta.get(sid, {})
        return StatuteGraph(
            statute_id=sid,
            timelines=self.timelines.get(sid, {}),
            delegations=sorted(
                [d for d in self.delegations if d.statute_id == sid],
                key=_delegation_sort_key,
            ),
            citations=sorted(
                [c for c in self.citations if c.source_statute_id == sid],
                key=_citation_sort_key,
            ),
            branches=sorted(
                [
                    branch
                    for branch in self.branches
                    if branch.source_artifact_id == sid
                    or any(edge.branch_id == branch.branch_id for edge in self.branch_edges_for_statute(sid))
                ],
                key=lambda branch: branch.branch_id,
            ),
            branch_edges=self.branch_edges_for_statute(sid),
            amendment_chain=sorted(self.amendment_index.get(sid, [])),
            title=meta.get("title", ""),
            statute_type=meta.get("statute_type", ""),
        )

    @property
    def is_partial(self) -> bool:
        """True when one or more requested statutes failed during graph build."""
        return self.processing_status.kind == "partial"

    def reverse_citations(self, sid: str) -> list:
        """Return all CrossRefEdges whose target is sid (what cites this statute?)."""
        return sorted(
            [c for c in self.citations if c.target_statute_id == sid],
            key=_citation_sort_key,
        )

    def affecting_acts(self, sid: str) -> List[str]:
        """Return statute IDs that have amended sid."""
        return sorted(self.amendment_index.get(sid, []))

    def delegation_chain(self, sid: str, section: str = "") -> list:
        """Return DelegationEdges from sid, optionally filtered to a source section."""
        edges = [d for d in self.delegations if d.statute_id == sid]
        if section:
            edges = [d for d in edges if d.section == section]
        return sorted(edges, key=_delegation_sort_key)

    def branch_edges_for_statute(self, sid: str) -> List[BranchGraphEdge]:
        """Return branch/proposal graph edges touching ``sid``."""

        return sorted(
            [
                edge
                for edge in self.branch_edges
                if edge.source_statute_id == sid or edge.target_statute_id == sid
            ],
            key=lambda edge: (
                edge.branch_id,
                edge.edge_kind,
                edge.source_artifact_id,
                edge.source_statute_id,
                edge.source_unit_id,
                edge.target_statute_id,
                edge.target_address,
                edge.operation_id,
            ),
        )

    def silent_breakage(
        self,
        sid: str,
        target_section: str = "",
        as_of: str = "",
    ) -> List[dict]:
        """Return provisions that cite sid and may have been silently affected.

        Finds all CITES edges targeting sid (optionally filtered to provisions that
        cite a specific section of sid). For each citing provision, reports whether
        it was active at as_of if timelines are loaded for the citing statute.

        Args:
            sid:            Target statute — the one that changed.
            target_section: Filter to edges where target_section contains this string
                            (e.g. "section/3" or just "3"). Empty = all provisions.
            as_of:          ISO date string. If provided and timelines are loaded for
                            the citing statute, checks whether the citing provision
                            was active at that date. Empty = no temporal filter.

        Returns:
            List of dicts with keys:
                citing_statute  — statute that contains the citing provision
                citing_section  — section number of the citing provision
                target_section  — provision path in sid that is cited
                count           — number of cross-reference occurrences
                active_at_date  — True/False if timelines loaded, None otherwise
        """
        from lawvm.core.timeline import select_active_version_ex

        edges = [
            e for e in self.citations
            if e.target_statute_id == sid and e.edge_type == "CITES"
        ]
        if target_section:
            edges = [e for e in edges if target_section in (e.target_section or "")]

        results = []
        for e in edges:
            active_at_date = None
            selection_status = "absent"
            if as_of and e.source_statute_id in self.timelines:
                tl_for_statute = self.timelines[e.source_statute_id]
                candidate_statuses: list[Optional[bool]] = []
                for addr, tl in tl_for_statute.items():
                    if any(k == "section" and v == e.source_section for k, v in addr.path):
                        selection = select_active_version_ex(tl, as_of)
                        if selection.status == "ambiguous_missing_scope":
                            candidate_statuses.append(None)
                            selection_status = "ambiguous_missing_scope"
                        else:
                            version = selection.version
                            candidate_statuses.append(
                                version is not None and version.content is not None
                            )
                            selection_status = "selected"
                if not candidate_statuses:
                    active_at_date = False
                    selection_status = "absent"
                elif any(status is None for status in candidate_statuses):
                    active_at_date = None
                    selection_status = "ambiguous_missing_scope"
                elif len(set(candidate_statuses)) == 1:
                    active_at_date = candidate_statuses[0]
                    selection_status = "selected"
                else:
                    # Section-only citation metadata is not precise enough to
                    # distinguish between multiple same-label provisions.
                    active_at_date = None
                    selection_status = "ambiguous_same_label"
            results.append({
                "citing_statute": e.source_statute_id,
                "citing_section": e.source_section,
                "target_section": e.target_section,
                "count": e.count,
                "active_at_date": active_at_date,
                "selection_status": selection_status,
            })
        return sorted(
            results,
            key=lambda row: (
                str(row.get("citing_statute", "") or ""),
                str(row.get("citing_section", "") or ""),
                str(row.get("target_section", "") or ""),
                int(row.get("count", 0) or 0),
                "" if row.get("active_at_date") is None else str(row.get("active_at_date")),
            ),
        )

    def breakage_report(
        self,
        changed_statutes: List[str],
        as_of: str = "",
    ) -> List[dict]:
        """Push-based breakage detection: given a set of recently-changed statutes,
        return all citation edges that may have been silently invalidated.

        This is the push complement to silent_breakage(): instead of asking
        "who cites X?", the caller provides a batch of X's and gets back a
        unified report of all potentially affected citations.

        Typical use: after a corpus rebuild, pass the set of newly-amended statute
        IDs to get a change-event feed for downstream consumers.

        Args:
            changed_statutes:  Statute IDs that were amended in the latest update.
            as_of:             ISO date string forwarded to silent_breakage() for
                               active-version filtering (empty = no temporal filter).

        Returns:
            List of dicts with keys:
                changed_statute  — the statute that changed (driver of the event)
                citing_statute   — statute that contains the potentially-stale citation
                citing_section   — section in the citing statute
                target_section   — provision path in changed_statute that is cited
                count            — number of cross-reference occurrences
                active_at_date   — True/False if timelines loaded, None otherwise
        """
        results: List[dict] = []
        for sid in changed_statutes:
            for row in self.silent_breakage(sid, as_of=as_of):
                results.append({"changed_statute": sid, **row})
        return sorted(
            results,
            key=lambda row: (
                str(row.get("changed_statute", "") or ""),
                str(row.get("citing_statute", "") or ""),
                str(row.get("citing_section", "") or ""),
                str(row.get("target_section", "") or ""),
                int(row.get("count", 0) or 0),
                "" if row.get("active_at_date") is None else str(row.get("active_at_date")),
            ),
        )

    def wire_projection(self) -> dict[str, object]:
        """Return a stable summary payload for persisted/reporting use."""
        build_meta_payload: Optional[dict[str, object]] = None
        if self.build_meta is not None:
            build_meta_payload = {
                "built_at": self.build_meta.built_at,
                "lawvm_commit": self.build_meta.lawvm_commit,
                "schema_version": self.build_meta.schema_version,
                "corpus_size": self.build_meta.corpus_size,
                "failed_statutes": self.build_meta.failed_statutes,
            }
        return {
            "build_meta": build_meta_payload,
            "processing_status": {
                "kind": self.processing_status.kind,
                "blockers": tuple(self.processing_status.blockers),
            },
            "counts": {
                "timeline_statutes": len(self.timelines),
                "delegations": len(self.delegations),
                "citations": len(self.citations),
                "branches": len(self.branches),
                "branch_edges": len(self.branch_edges),
                "amendment_index": len(self.amendment_index),
                "statute_meta": len(self.statute_meta),
                "build_failures": len(self.build_failures),
            },
            "loaded_statutes": tuple(sorted(self.statute_meta)),
            "amendment_index_statutes": tuple(sorted(self.amendment_index)),
            "branches": tuple(
                to_wire_jsonable(branch.to_dict())
                for branch in sorted(self.branches, key=lambda branch: branch.branch_id)
            ),
            "branch_edges": tuple(
                to_wire_jsonable(edge.to_dict())
                for edge in sorted(
                    self.branch_edges,
                    key=lambda edge: (
                        edge.branch_id,
                        edge.edge_kind,
                        edge.source_artifact_id,
                        edge.source_statute_id,
                        edge.source_unit_id,
                        edge.target_statute_id,
                        edge.target_address,
                        edge.operation_id,
                    ),
                )
            ),
            "build_failures": tuple(
                sorted(
                    (
                        to_wire_jsonable(
                            {
                                "statute_id": str(item.get("statute_id", "") or ""),
                                "error": str(item.get("error", "") or ""),
                            }
                        )
                        for item in self.build_failures
                    ),
                    key=lambda item: (item["statute_id"], item["error"]),
                )
            ),
        }

    def to_wire_artifact(
        self,
        *,
        producer: str = "lawvm.core.graph",
        version: str = "1",
    ) -> ArtifactEnvelope[dict[str, object]]:
        """Wrap the graph wire projection in a versioned artifact envelope."""
        return ArtifactEnvelope(
            schema="lawvm.corpus_graph",
            producer=producer,
            version=version,
            payload=self.wire_projection(),
            status=self.processing_status,
        )
