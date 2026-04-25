"""Immutable statute context and explicit replay state for the Finnish pipeline.

``StatuteContext`` — immutable, constructed once per statute, never mutated.
``ReplayState``   — the fold accumulator; replaced (not mutated) on each op.
``ReplayResult``  — immutable return type of replay_xml; wraps typed replay
                    products while remaining drop-in compatible with the old
                    XMLStatute-like API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, FrozenSet, Optional, Set

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import LabelIndex, Path, build_label_index
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.xml_ir import fi_xml_to_ir_node, detect_unnumbered_paragraph_peers, detect_label_eid_divergence
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.helpers import _norm_num_token as _fi_norm_label
from lawvm.finland.projection_rows import projection_rows as _projection_rows

if TYPE_CHECKING:
    from lawvm.core.compile_facade import CompileFacade
    from lawvm.core.semantic_types import SourceNormalizationFact
    from lawvm.finland.replay_products import ReplayProducts
    from lawvm.replay_adjudication import SourceAdjudication
    from lawvm.finland.payload_normalize import ElaborationObservation

from lawvm.core.compile_views import source_pathology_rows_from_findings
from lawvm.core.phase_result import Finding


# ---------------------------------------------------------------------------
# Base observation collection helpers
# ---------------------------------------------------------------------------

def _collect_base_observations(ir: IRNode, statute_id: str) -> tuple["ElaborationObservation", ...]:
    """Walk base IR and collect observations from detection helpers.

    Detects:
    - BASE_UNNUMBERED_PARAGRAPH_PEER: unnumbered paragraphs with numbered siblings
    - LABEL_EID_DIVERGENCE: label/eId mismatches in paragraphs
    """
    from lawvm.finland.payload_normalize import ElaborationObservation

    observations: list["ElaborationObservation"] = []

    def _walk_sections(node: IRNode, section_path: str = "") -> None:
        """Recursively walk IR tree looking for sections and their subsections."""
        if node.kind == IRNodeKind.SECTION:
            section_label = str(node.label) if node.label is not None else "?"
            new_path = f"section:{section_label}"
            # Look for subsections in this section
            for subsec in node.children:
                if subsec.kind == IRNodeKind.SUBSECTION:
                    subsec_label = str(subsec.label) if subsec.label is not None else "?"
                    subsec_path = f"{new_path}/subsection:{subsec_label}"
                    _check_subsection(subsec, subsec_path)

        # Recurse into children
        for child in node.children:
            _walk_sections(child, section_path)

    def _check_subsection(subsec: IRNode, subsec_path: str) -> None:
        """Check a subsection for unnumbered peer and label/eId divergences."""
        # Check for unnumbered paragraph peers
        violations = detect_unnumbered_paragraph_peers(subsec, subsec_path)
        for eId, intro_text, preceding, following in violations:
            observations.append(
                ElaborationObservation(
                    kind="BASE_UNNUMBERED_PARAGRAPH_PEER",
                    stage="base_source_analysis",
                    detail={
                        "section_address": subsec_path,
                        "eId": eId,
                        "intro_excerpt": intro_text,
                        "preceding_numbered_eIds": preceding,
                        "following_numbered_eIds": following,
                    }
                )
            )

        # Check for label/eId divergences
        divergences = detect_label_eid_divergence(subsec, subsec_path)
        for label, eId in divergences:
            observations.append(
                ElaborationObservation(
                    kind="LABEL_EID_DIVERGENCE",
                    stage="base_source_analysis",
                    detail={
                        "section_address": subsec_path,
                        "label": str(label),
                        "eId": eId,
                    }
                )
            )

    _walk_sections(ir)
    return tuple(observations)


# ---------------------------------------------------------------------------
# StatuteContext — immutable context bag, built once from base XML
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StatuteContext:
    """Immutable context for a statute being replayed.

    Constructed once from the base XML bytes.  Never changes during replay.
    Passed as read-only context to every pipeline function that needs to know
    "what did the original statute look like?" (e.g. kumotaan placeholder
    decisions that compare against the original section list).

    Fields
    ------
    id : str
        Statute identifier, e.g. "2002/738".
    title : str
        Human-readable title from docTitle element.
    base_ir : IRNode
        Original body IR before any amendments.  Immutable — no function
        should mutate this.  Replay code should rebuild a new tree when it
        needs to branch, not copy this baseline defensively.
    base_xml_bytes : bytes
        Raw source XML bytes.  Used by functions that still need lxml for
        amendment-body inspection against the base statute structure.
    """
    id: str
    title: str
    base_ir: IRNode
    base_xml_bytes: bytes
    base_observations: tuple["ElaborationObservation", ...] = field(default_factory=tuple)
    source_normalization_facts: tuple["SourceNormalizationFact", ...] = field(default_factory=tuple)

    @classmethod
    def from_xml(cls, xml_bytes: bytes, label_postprocessor=None) -> "StatuteContext":
        """Construct a StatuteContext by parsing base XML bytes.

        Parameters
        ----------
        xml_bytes:
            Raw AKN XML for the base statute.
        label_postprocessor:
            Optional callable passed to ``xml_to_ir_node`` to normalise
            Finnish section labels (trailing periods, section signs, etc.).
            If None, labels are used as-is.
        """
        tree = etree.fromstring(xml_bytes)
        # Extract id
        num_el = tree.find(".//{*}docNumber")
        sid = num_el.text.strip() if num_el is not None else "0/0"
        # Extract title
        title_el = tree.find(".//{*}docTitle")
        title = (
            etree.tostring(title_el, method="text", encoding="unicode").strip()
            if title_el is not None
            else "Unknown"
        )
        # Build base IR: raw parse then explicit source normalization phase.
        body_el = tree.find(".//{*}body")
        if body_el is None:
            body_el = tree
        raw_ir = fi_xml_to_ir_node(body_el, label_postprocessor)
        # Emit base statute observations on the RAW (pre-normalization) IR so
        # that unnumbered paragraph peers are still present in the tree when
        # detect_unnumbered_paragraph_peers runs.  After normalize_source_ir,
        # the sub_clause_with_list reparenting pass (step 8.5) removes those
        # peers from the tree, which would otherwise cause the observation to
        # silently miss them (T1b wiring gap).
        base_observations = _collect_base_observations(raw_ir, sid)
        base_ir, norm_facts = normalize_source_ir(raw_ir, sid)
        return cls(
            id=sid,
            title=title,
            base_ir=base_ir,
            base_xml_bytes=xml_bytes,
            base_observations=base_observations,
            source_normalization_facts=tuple(norm_facts),
        )


# ---------------------------------------------------------------------------
# ReplayState — fold accumulator, replaced not mutated
# ---------------------------------------------------------------------------

_PROVISION_INDEXED_KINDS: FrozenSet[str] = frozenset({"section", "chapter", "part"})

@dataclass
class ReplayState:
    """Current state of the replay tree.

    ``with_ir(new_ir)`` returns a new ``ReplayState`` with the updated IR and
    a cleared index (recomputed lazily on the next lookup).  The old state
    remains valid — this enables checkpointing and diffing.

    Convention: functions that change the tree return a new ``ReplayState``
    via ``with_ir``.  Direct assignment ``state.ir = x`` is disallowed by
    convention (not enforced by the type system — see spec Non-goals).
    """
    ir: IRNode
    revision: int = 0
    _index: Optional[LabelIndex] = field(default=None, repr=False)
    _provision_index: Optional[LabelIndex] = field(default=None, repr=False)
    _duplicate_section_labels: Optional[Set[str]] = field(default=None, repr=False)

    def with_ir(
        self,
        new_ir: IRNode,
        *,
        preserve_provision_index: bool = False,
    ) -> "ReplayState":
        """Return a new ReplayState with updated IR.

        `preserve_provision_index=True` is only safe when the update cannot
        change section/chapter/part labels or their paths.
        """
        return ReplayState(
            ir=new_ir,
            revision=self.revision + 1,
            _provision_index=self._provision_index if preserve_provision_index else None,
            _duplicate_section_labels=(
                self._duplicate_section_labels if preserve_provision_index else None
            ),
        )

    @property
    def snapshot_rev(self) -> int:
        """Compatibility alias for elaboration snapshot freshness."""
        return self.revision

    @property
    def index(self) -> LabelIndex:
        """Lazy label index.  Built on first access, invalidated by with_ir."""
        if self._index is None:
            self._index = build_label_index(self.ir)
        return self._index

    @property
    def provision_index(self) -> LabelIndex:
        """Lazy sparse index for section/chapter/part lookups only."""
        if self._provision_index is None:
            self._provision_index = build_label_index(
                self.ir,
                indexed_kinds=_PROVISION_INDEXED_KINDS,
            )
        return self._provision_index

    @property
    def duplicate_section_labels(self) -> Set[str]:
        """Section labels that appear under more than one labeled chapter."""
        if self._duplicate_section_labels is None:
            counts: dict[str, set[str]] = {}

            def _collect(node: IRNode) -> None:
                if node.kind == IRNodeKind.CHAPTER and node.label:
                    for child in node.children:
                        if child.kind == IRNodeKind.SECTION and child.label:
                            counts.setdefault(child.label, set()).add(node.label)
                for child in node.children:
                    _collect(child)

            _collect(self.ir)
            self._duplicate_section_labels = {
                label for label, chapters in counts.items() if len(chapters) > 1
            }
        return self._duplicate_section_labels

    # ------------------------------------------------------------------
    # Lookup helpers — mirror XMLStatute.find_section / find_chapter etc.
    # ------------------------------------------------------------------

    def find(
        self,
        kind: str,
        label: str,
        scope_kind: Optional[str] = None,
        scope_label: Optional[str] = None,
    ) -> Optional[Path]:
        """Return path to the first node matching (kind, label), or None."""
        if kind in _PROVISION_INDEXED_KINDS and (
            scope_kind is None or scope_kind in _PROVISION_INDEXED_KINDS
        ):
            label_index = self.provision_index
        else:
            label_index = self.index
        path = _tops.find(
            self.ir,
            kind,
            label,
            scope_kind=scope_kind,
            scope_label=scope_label,
            label_index=label_index,
        )
        if path is not None:
            return path
        return _tops.find(
            self.ir,
            kind,
            label,
            scope_kind=scope_kind,
            scope_label=scope_label,
        )

    def resolve(self, path: Path) -> Optional[IRNode]:
        """Resolve a path to an IRNode, or None if not found."""
        return _tops.resolve(self.ir, path)

    def find_node(
        self,
        kind: str,
        label: str,
        scope_kind: Optional[str] = None,
        scope_label: Optional[str] = None,
    ) -> Optional[IRNode]:
        """Return the IRNode at (kind, label), or None."""
        path = self.find(kind, label, scope_kind=scope_kind, scope_label=scope_label)
        return self.resolve(path) if path is not None else None

    def find_section(
        self,
        sec_num: str,
        chapter_num: Optional[str] = None,
        part_num: Optional[str] = None,
    ) -> Optional[IRNode]:
        """Convenience: find a section node by number, optionally scoped to chapter/part."""
        path = self.find_section_path(sec_num, chapter_num, part_num)
        return self.resolve(path) if path is not None else None

    def find_section_path(
        self,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> Optional[Path]:
        """Convenience: find path to a section by number, optionally scoped to chapter/part."""
        if target_part:
            # Normalize Roman numeral part references (e.g. "II" → "2") so that
            # johtolause-derived addresses match the Arabic labels stored in IR.
            expected_part = _fi_norm_label(target_part)
            part_path = self.find("part", target_part)
            if part_path is None:
                part_path = self.find("part", expected_part)
            if part_path is None or _fi_norm_label(part_path[-1][1]) != expected_part:
                return None
            part_node = self.resolve(part_path) if part_path is not None else None
            if part_path is not None and part_node is not None:
                if target_chapter:
                    chapter_path = _tops.find(part_node, "chapter", target_chapter)
                    chapter_node = _tops.resolve(part_node, chapter_path) if chapter_path is not None else None
                    if chapter_path is not None and chapter_node is not None:
                        section_path = _tops.find(chapter_node, "section", target_norm)
                        if section_path is not None:
                            return part_path + chapter_path + section_path
                    return None
                section_path = _tops.find(part_node, "section", target_norm)
                if section_path is not None:
                    return part_path + section_path
            return None
        return self.find(
            "section",
            target_norm,
            scope_kind="chapter" if target_chapter else None,
            scope_label=target_chapter,
        )

    def find_chapter(self, chap_num: str) -> Optional[IRNode]:
        """Convenience: find a chapter node by number."""
        return self.find_node("chapter", chap_num)

    def find_part(self, part_num: str) -> Optional[IRNode]:
        """Convenience: find a part node by number."""
        return self.find_node("part", part_num)


# ---------------------------------------------------------------------------
# ReplayResult — immutable return type of replay_xml
# ---------------------------------------------------------------------------

_SKIP_NAMES = frozenset({'signatures', 'attachments', 'conclusions', 'omission'})


def _serialize_text_node(node: IRNode) -> str:
    """Recursive operative-body text extractor (no XMLStatute needed).

    For mixed-content nodes (nodes that have both .text and structured children,
    e.g. a content node with table children), both the own text and the children
    text are emitted.  This matches ``irnode_to_text`` semantics.
    """
    if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") in _SKIP_NAMES:
        return ""
    if node.text and node.children:
        # Mixed-content node: emit own text AND children text
        parts = [node.text]
        parts.extend(_serialize_text_node(c) for c in node.children)
        return " ".join(p for p in parts if p)
    if node.text:
        return node.text
    return " ".join(p for p in (_serialize_text_node(c) for c in node.children) if p)


@dataclass(frozen=True)
class OracleSelectorInfo:
    """Provenance for the oracle selection decision on one replay call.

    Populated in ``replay_xml`` when an explicit ``oracle_selector`` was
    active and a cached consolidated artifact was used.  ``None`` on
    ``ReplayResult`` means no selector decision was made (default path,
    e.g. when corpus provides no cached artifacts or the default
    ``latest_cached_editorial`` was used without interesting candidates).

    Fields
    ------
    selector_mode:
        The ConsolidatedSelectionMode value string that was requested by the
        caller, e.g. ``"bench_comparable"`` or ``"latest_cached_editorial"``.
    chosen_artifact_version:
        The embedded version tag (``YYYYMMNN`` string) of the selected
        artifact, e.g. ``"20211030"``.  Empty string if no artifact was
        chosen.
    tolerance_applied:
        True when the chosen artifact was accepted under the 180-day
        Finlex-ahead tolerance (Option Z) — i.e. the amendment's
        ordering_date was slightly after the artifact's ``dateConsolidated``
        but within 180 days.  The ``ORACLE_METADATA_COLLAPSED_DATES``
        warning in consolidated_store.py fires together with this flag.
    rejected_candidates:
        Version tags of artifacts that were screened out by the
        comparability filter (only relevant for BENCH_COMPARABLE mode).
        Empty tuple when all candidates passed or mode does not filter.
    """

    selector_mode: str = ""
    chosen_artifact_version: str = ""
    tolerance_applied: bool = False
    rejected_candidates: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ReplayResult:
    """Immutable return type of replay_xml.

    Presents the same surface as the old ``_MasterAdapter`` / ``XMLStatute``
    that 30+ tools access, so they work without modification after Commit 3
    changes ``replay_xml`` to return this instead of ``_MasterAdapter``.

    Fields
    ------
    ctx : StatuteContext
        Frozen context (id, title, base_ir, base_xml_bytes).
    products : ReplayProducts
        Typed replay/materialization artifacts bundle.
    findings : tuple[Finding, ...]
        Replay-owned finding ledger for replay/process/materialization evidence.
    compile_facade : Optional[CompileFacade]
        Attached by ``compile_fi_facade`` after replay; None when callers use
        ``replay_xml`` directly.
    oracle_selector_info : Optional[OracleSelectorInfo]
        Provenance for the oracle selection decision, if an explicit
        ``oracle_selector`` was provided to ``replay_xml``.  ``None`` means
        the default selection path was used or no cached artifact was
        available.
    """

    ctx: StatuteContext
    products: "ReplayProducts"
    findings: tuple["Finding", ...] = field(default_factory=tuple, repr=False)
    compile_facade: Optional["CompileFacade"] = field(default=None, repr=False)
    oracle_selector_info: Optional[OracleSelectorInfo] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Convenience accessors — mirror old XMLStatute / _MasterAdapter API
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Statute identifier, e.g. '2002/738'."""
        return self.ctx.id

    @property
    def title(self) -> str:
        """Human-readable statute title."""
        return self.ctx.title

    @property
    def ir(self) -> IRNode:
        """Final IR tree (PIT body after all amendments)."""
        return self.state.ir

    @property
    def replay_fold_state(self) -> ReplayState:
        """Replay state immediately after amendment folding."""
        return self.products.replay_fold_state

    @property
    def state(self) -> ReplayState:
        """Final PIT-materialized state."""
        return self.products.materialized_state

    def projection_rows(self) -> tuple[dict[str, object], ...]:
        """Preferred compatibility read model for replay-facing tooling/tests."""
        return _projection_rows(self.findings)

    def source_pathology_rows(self) -> tuple[dict[str, object], ...]:
        """Return source-pathology summary rows from replay-owned findings."""
        return source_pathology_rows_from_findings(self.findings)

    @property
    def timelines(self) -> Optional[dict]:
        """Compiled provision timelines."""
        return self.products.timelines

    @property
    def temporal_events(self):
        """Explicit temporal authority carried by replay products."""
        return self.products.temporal_events

    @property
    def migration_events(self):
        """Address migration events emitted during replay."""
        return self.products.migration_events

    @property
    def tree(self):
        """Lazy lxml parse of base_xml_bytes.

        The returned tree reflects the *original* base XML, not the amended
        state.  Only used by dump.py as a last-resort fallback when IRNode
        search fails.  Parses once and caches the result.
        """
        # Use object.__getattribute__ to avoid triggering dataclass machinery
        try:
            return object.__getattribute__(self, '_tree_cache')
        except AttributeError:
            tree = etree.fromstring(self.ctx.base_xml_bytes)
            object.__setattr__(self, '_tree_cache', tree)
            return tree

    def serialize_text(self) -> str:
        """Serialize operative body text from the final IR, excluding appendices."""
        return _serialize_text_node(self.state.ir)

    @property
    def materialized_state(self) -> ReplayState:
        """Explicit alias for the final PIT-materialized state."""
        return self.products.materialized_state

    @property
    def materialization_spec(self):
        """Typed PIT materialization spec."""
        return self.products.materialization_spec

    @property
    def source_adjudication(self) -> Optional["SourceAdjudication"]:
        """Typed source/oracle comparability state."""
        return self.products.source_adjudication

    def find_section(
        self,
        sec_num: str,
        chapter_num: Optional[str] = None,
        part_num: Optional[str] = None,
    ) -> Optional[IRNode]:
        """Find a section node by number, optionally scoped to chapter/part."""
        return self.state.find_section(sec_num, chapter_num, part_num)
