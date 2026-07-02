"""Immutable statute context and explicit replay state for the Finnish pipeline.

``StatuteContext`` — immutable, constructed once per statute, never mutated.
``ReplayState``   — the fold accumulator; replaced (not mutated) on each op.
``ReplayResult``  — immutable return type of replay_xml; wraps typed replay
                    products while remaining drop-in compatible with the old
                    XMLStatute-like API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, FrozenSet, Mapping, Optional, Set

import lxml.etree as etree

from lawvm.core.ir import IRNode
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import LabelIndex, Path, build_label_index, normalized_label_key
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.xml_ir import fi_xml_to_ir_node, detect_unnumbered_paragraph_peers, detect_label_eid_divergence
from lawvm.xml_ingest import _IngestSink
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.projection_rows import projection_rows as _projection_rows
from lawvm.finland.scoped_section_resolver import find_scoped_section_path

if TYPE_CHECKING:
    from lawvm.core.compile_facade import CompileFacade
    from lawvm.core.semantic_types import SourceNormalizationFact
    from lawvm.core.timeline import Timelines
    from lawvm.finland.replay_products import ReplayProducts
    from lawvm.replay_adjudication import SourceAdjudication
    from lawvm.finland.payload_normalize import ElaborationObservation

from lawvm.core.compile_views import source_pathology_rows_from_findings
from lawvm.core.phase_result import Finding
from lawvm.core.write_receipt import WriteReceipt


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


def _base_issue_date_iso(tree: etree._Element) -> str:
    for name in ("dateIssued", "datePublished", "dateIssuedGenerated"):
        for el in tree.findall('.//{*}FRBRdate'):
            if el.get("name") != name:
                continue
            raw = el.get("date") or ""
            try:
                return date.fromisoformat(raw).isoformat()
            except ValueError:
                continue
    return ""


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
    issue_date: str = ""
    # Witnessed XML→IR ingest observations + token-partition coverage gathered
    # while parsing the base XML on the production path. Mirrors the metadata
    # channel IRStatute carries for xml_body_to_ir: only populated when the
    # ingest actually dropped a child / guessed a positional label / repaired
    # structure, so a clean parse leaves this an empty mapping.
    ingest_metadata: Mapping[str, Any] = field(default_factory=dict)
    # Parsed attachment-PDF content (each entry an AttachmentIRSupplement
    # carrying the PDF→IRNode tree). Built post-from_xml by
    # ``prepare_replay_plan`` (the corpus-store-aware builder) from
    # ``<a href="media/N.pdf">`` links in the consolidated source XML +
    # ``corpus.read_attachment_media``. Default empty tuple preserves the
    # ``StatuteContext.from_xml`` call sites that do not extract attachments
    # (e.g. tests, amendment-only builders) — the supplements are an
    # opt-in enrichment set, not load-bearing for correctness of the body
    # replay fold. Per SDOC-13 projections include attachments unless scoped
    # out (``lawvm show --no-attachments``).
    attachment_supplements: tuple = field(default_factory=tuple)

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
        # Thread an ingest sink through the production parse so the XML→IR
        # boundary's silent drops/guesses (dropped childless unknown elements,
        # positional-label assignment, structural-repair re-parenting/merges)
        # are witnessed on the real FI path, not only in xml_body_to_ir.
        sink = _IngestSink()
        raw_ir = fi_xml_to_ir_node(body_el, label_postprocessor, sink)
        # Emit base statute observations on the RAW (pre-normalization) IR so
        # that unnumbered paragraph peers are still present in the tree when
        # detect_unnumbered_paragraph_peers runs.  After normalize_source_ir,
        # the sub_clause_with_list reparenting pass (step 8.5) removes those
        # peers from the tree, which would otherwise cause the observation to
        # silently miss them (T1b wiring gap).
        base_observations = _collect_base_observations(raw_ir, sid)
        base_ir, norm_facts = normalize_source_ir(raw_ir, sid)
        # Fold the witnessed ingest observations + coverage into a metadata
        # channel mirroring IRStatute.metadata. Published only when non-empty so
        # a clean parse does not dirty the context for the common case.
        ingest_metadata: dict[str, Any] = {}
        if sink.observations:
            ingest_metadata["xml_ingest_observations"] = tuple(
                obs.as_dict() for obs in sink.observations
            )
        if sink.coverage.dropped:
            ingest_metadata["xml_ingest_coverage"] = sink.coverage.as_dict()
        return cls(
            id=sid,
            title=title,
            base_ir=base_ir,
            base_xml_bytes=xml_bytes,
            base_observations=base_observations,
            source_normalization_facts=tuple(norm_facts),
            issue_date=_base_issue_date_iso(tree),
            ingest_metadata=ingest_metadata,
        )


# ---------------------------------------------------------------------------
# ReplayState — fold accumulator, replaced not mutated
# ---------------------------------------------------------------------------

_PROVISION_INDEXED_KINDS: FrozenSet[str] = frozenset({"section", "chapter", "part"})


def _collect_provision_index_entries(node: IRNode, path: Path) -> LabelIndex:
    index: LabelIndex = {}

    def _walk(current: IRNode, current_path: Path) -> None:
        kind = current.kind.value
        if current.label and kind in _PROVISION_INDEXED_KINDS:
            key = (kind, normalized_label_key(current.label))
            index.setdefault(key, []).append(current_path)
            if kind == "section":
                return
        for child in current.children:
            child_path = current_path + ((child.kind.value, child.label or ""),)
            _walk(child, child_path)

    _walk(node, path)
    return index


def _subtree_insertion_positions_by_key(
    tree: IRNode,
    subtree_path: Path,
    wanted_keys: FrozenSet[tuple[str, str]],
) -> tuple[dict[tuple[str, str], int], bool]:
    counts = {key: 0 for key in wanted_keys}
    insertion_positions: dict[tuple[str, str], int] = {}
    found_subtree = False

    def _walk(current: IRNode, current_path: Path) -> None:
        nonlocal found_subtree
        if current_path == subtree_path:
            found_subtree = True
            for key, count in counts.items():
                insertion_positions.setdefault(key, count)
            return
        kind = current.kind.value
        if current.label and kind in _PROVISION_INDEXED_KINDS:
            key = (kind, normalized_label_key(current.label))
            if key in counts:
                counts[key] += 1
        for child in current.children:
            child_path = current_path + ((child.kind.value, child.label or ""),)
            _walk(child, child_path)

    _walk(tree, ())
    return insertion_positions, found_subtree


def _replace_provision_index_subtree(
    *,
    tree: IRNode,
    new_tree: IRNode,
    provision_index: LabelIndex,
    subtree_path: Path,
    old_subtree: IRNode,
    new_subtree: IRNode,
) -> LabelIndex | None:
    old_entries = _collect_provision_index_entries(old_subtree, subtree_path)
    new_subtree_path = subtree_path[:-1] + ((new_subtree.kind.value, new_subtree.label or ""),)
    new_entries = _collect_provision_index_entries(new_subtree, new_subtree_path)
    changed_keys = frozenset(old_entries.keys() | new_entries.keys())
    if not changed_keys:
        return provision_index

    next_index: LabelIndex = {}
    first_removed_index: dict[tuple[str, str], int] = {}
    for key, paths in provision_index.items():
        if key not in changed_keys:
            next_index[key] = paths
            continue
        old_path_set = set(old_entries.get(key, ()))
        if not old_path_set:
            next_index[key] = list(paths)
            continue
        kept: list[Path] = []
        for path in paths:
            if path in old_path_set:
                first_removed_index.setdefault(key, len(kept))
            else:
                kept.append(path)
        if kept:
            next_index[key] = kept
        elif key in new_entries:
            next_index[key] = []

    new_only_keys = frozenset(
        key
        for key in new_entries
        if key not in first_removed_index and key in provision_index
    )
    insertion_positions: dict[tuple[str, str], int] = {}
    if new_only_keys:
        insertion_positions, found_subtree = _subtree_insertion_positions_by_key(
            tree,
            subtree_path,
            new_only_keys,
        )
        if not found_subtree:
            return None

    for key, new_paths in new_entries.items():
        existing = list(next_index.get(key, ()))
        insert_at = first_removed_index.get(key)
        if insert_at is None:
            insert_at = insertion_positions.get(key, len(existing))
        next_index[key] = existing[:insert_at] + list(new_paths) + existing[insert_at:]

    updated_index = {key: paths for key, paths in next_index.items() if paths}
    if not _changed_provision_index_paths_resolve(
        new_tree,
        updated_index,
        changed_keys,
    ):
        return None
    return updated_index


def _changed_provision_index_paths_resolve(
    tree: IRNode,
    provision_index: LabelIndex,
    changed_keys: FrozenSet[tuple[str, str]],
) -> bool:
    for key in changed_keys:
        expected_kind, expected_norm_label = key
        for path in provision_index.get(key, ()):
            node = _tops.resolve(tree, path)
            if node is None:
                return False
            if node.kind.value != expected_kind:
                return False
            if normalized_label_key(node.label) != expected_norm_label:
                return False
    return True

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
    _section_path_cache: Optional[
        dict[tuple[str, Optional[str], Optional[str]], Optional[Path]]
    ] = field(default=None, repr=False)

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
            _section_path_cache=(
                self._section_path_cache if preserve_provision_index else None
            ),
        )

    def with_replaced_provision_subtree_index(
        self,
        new_ir: IRNode,
        *,
        path: Path,
        old_subtree: IRNode,
        new_subtree: IRNode,
    ) -> "ReplayState":
        """Return a new state with a provision index updated for one subtree replacement.

        This is only for exact same-path subtree replacement. Section-path and
        duplicate-label caches are invalidated because the replacement can add,
        remove, or relabel indexed provisions under that path.
        """
        if self._provision_index is None or not path:
            return self.with_ir(new_ir)
        next_index = _replace_provision_index_subtree(
            tree=self.ir,
            new_tree=new_ir,
            provision_index=self._provision_index,
            subtree_path=path,
            old_subtree=old_subtree,
            new_subtree=new_subtree,
        )
        if next_index is None:
            return self.with_ir(new_ir)
        return ReplayState(
            ir=new_ir,
            revision=self.revision + 1,
            _provision_index=next_index,
            _duplicate_section_labels=None,
            _section_path_cache=None,
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
            self._provision_index = _tops.build_provision_label_index(
                self.ir,
                indexed_kinds=_PROVISION_INDEXED_KINDS,
            )
        return self._provision_index

    def _drop_provision_lookup_caches(self) -> None:
        self._provision_index = None
        self._duplicate_section_labels = None
        self._section_path_cache = None

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
        if path is not None and self.resolve(path) is None and label_index is self._provision_index:
            self._drop_provision_lookup_caches()
            path = _tops.find(
                self.ir,
                kind,
                label,
                scope_kind=scope_kind,
                scope_label=scope_label,
                label_index=self.provision_index,
            )
            if path is not None and self.resolve(path) is None:
                return None
        return path

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
        cache_key = (target_norm, target_chapter, target_part)
        if self._section_path_cache is None:
            self._section_path_cache = {}
        elif cache_key in self._section_path_cache:
            cached_path = self._section_path_cache[cache_key]
            if cached_path is None or self.resolve(cached_path) is not None:
                return cached_path
            self._drop_provision_lookup_caches()
            self._section_path_cache = {}
        path = find_scoped_section_path(
            self.ir,
            target_section=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            find_path=self.find,
            provision_index=self.provision_index,
        )
        if path is not None and self.resolve(path) is None:
            self._drop_provision_lookup_caches()
            path = find_scoped_section_path(
                self.ir,
                target_section=target_norm,
                target_chapter=target_chapter,
                target_part=target_part,
                find_path=self.find,
                provision_index=self.provision_index,
            )
            if path is not None and self.resolve(path) is None:
                path = None
        if self._section_path_cache is None:
            self._section_path_cache = {}
        self._section_path_cache[cache_key] = path
        return path

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
    write_receipts : tuple[WriteReceipt, ...]
        Landed-write receipts accumulated across the replay fold (contract
        §4). Carried up from ``ApplyOpsSinks.write_receipts_out`` so the
        certificate stage can cross-check its covering-state transitions
        against the writes that actually landed. Empty when no receipts were
        captured (e.g. replays that take no apply path).
    """

    ctx: StatuteContext
    products: "ReplayProducts"
    findings: tuple["Finding", ...] = field(default_factory=tuple, repr=False)
    compile_facade: Optional["CompileFacade"] = field(default=None, repr=False)
    oracle_selector_info: Optional[OracleSelectorInfo] = field(default=None, repr=False)
    write_receipts: tuple[WriteReceipt, ...] = field(default_factory=tuple, repr=False)

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
    def timelines(self) -> Optional["Timelines"]:
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
    def identity_ledger(self):
        """Frozen read-only lineage ledger over replay migration events."""
        return self.products.identity_ledger

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
