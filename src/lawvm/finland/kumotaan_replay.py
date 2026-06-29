"""Replay-product helpers for Finnish kumotaan repeal clauses.

Pure kumotaan extraction lives in ``kumotaan.py``. This module owns the later
replay-product boundary: converting or injecting typed LegalOperations once a
kumotaan target has been established.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace as dc_replace
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.kumotaan import KumotaanItemTarget
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.scoped_section_resolver import section_paths_for_label

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID = "fi.recovery.pure_kumotaan_repeal"
FI_RECOVERY_PURE_KUMOTAAN_SUBSECTION_REPEAL_RULE_ID = (
    "fi.recovery.pure_kumotaan_subsection_repeal"
)
FI_RECOVERY_PURE_KUMOTAAN_ITEM_REPEAL_RULE_ID = "fi.recovery.pure_kumotaan_item_repeal"


@dataclass(frozen=True, slots=True)
class PureKumotaanInjectedRepeal:
    """Witness for one REPEAL op reconstructed from a raw kumotaan johtolause.

    Emitted when the typed pipeline produced no op for a kumotaan target and the
    repeal was reconstructed from raw source text. Carries the witness rule id
    and enough address evidence for the driver to emit a structured finding.
    """

    rule_id: str
    op_id: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str = ""

    def finding_detail(self) -> Mapping[str, object]:
        detail: dict[str, object] = {
            "rule_id": self.rule_id,
            "op_id": self.op_id,
            "target_unit_kind": self.target_unit_kind,
            "target_norm": self.target_norm,
        }
        if self.target_chapter:
            detail["target_chapter"] = self.target_chapter
        return detail


@dataclass(frozen=True, slots=True)
class PureKumotaanInjectionResult:
    """Typed result for pure-kumotaan whole-section/container repeal injection."""

    injected: tuple[PureKumotaanInjectedRepeal, ...] = ()

    @property
    def injected_count(self) -> int:
        return len(self.injected)


@dataclass(frozen=True, slots=True)
class PureKumotaanSubsectionSkippedTarget:
    """Visible witness for a pure-kumotaan subsection injection that did not run."""

    rule_id: str
    reason: str
    section_label: str
    subsection_labels: tuple[str, ...]
    candidate_paths: tuple[LegalAddress, ...] = ()

    def finding_detail(self) -> Mapping[str, object]:
        return {
            "rule_id": self.rule_id,
            "reason": self.reason,
            "target_section": self.section_label,
            "target_subsections": self.subsection_labels,
            "candidate_paths": tuple(str(path) for path in self.candidate_paths),
        }


@dataclass(frozen=True, slots=True)
class PureKumotaanItemSkippedTarget:
    """Visible witness for a pure-kumotaan item injection that did not run."""

    rule_id: str
    reason: str
    section_label: str
    item_label: str
    subsection_label: str | None = None
    candidate_paths: tuple[LegalAddress, ...] = ()

    def finding_detail(self) -> Mapping[str, object]:
        detail: dict[str, object] = {
            "rule_id": self.rule_id,
            "reason": self.reason,
            "target_section": self.section_label,
            "target_item": self.item_label,
            "candidate_paths": tuple(str(path) for path in self.candidate_paths),
        }
        if self.subsection_label:
            detail["target_subsection"] = self.subsection_label
        return detail


@dataclass(frozen=True, slots=True)
class PureKumotaanSubsectionInjectionResult:
    """Typed result for pure-kumotaan subsection replay-product injection."""

    injected_count: int
    skipped_targets: tuple[PureKumotaanSubsectionSkippedTarget, ...] = ()
    injected: tuple[PureKumotaanInjectedRepeal, ...] = ()


@dataclass(frozen=True, slots=True)
class PureKumotaanItemInjectionResult:
    """Typed result for pure-kumotaan item replay-product injection."""

    injected_count: int
    skipped_targets: tuple[PureKumotaanItemSkippedTarget, ...] = ()
    injected: tuple[PureKumotaanInjectedRepeal, ...] = ()


@dataclass(frozen=True, slots=True)
class _ResolvedKumotaanSubsectionSection:
    """Resolved full-address section target for pure-kumotaan subsection injection."""

    section_path: tuple[tuple[str, str], ...]
    section_node: IRNode
    source_scoped: bool


@dataclass(frozen=True, slots=True)
class _ResolvedKumotaanItemTarget:
    """Resolved full-address item target for pure-kumotaan item injection."""

    item_path: tuple[tuple[str, str], ...]
    section_path: tuple[tuple[str, str], ...]
    subsection_label: str


def _rewrite_kumotaan_snapshot_replaces_to_repeal(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    target_source_statute: str,
    section_labels: Set[str],
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
    source_raw_text: str = "",
) -> bool:
    """Turn zero-day section snapshots from whole-section kumotaan clauses into repeals.

    Some Finland amendments rewrite a section snapshot and also state in the
    same johtolause that the whole section is repealed on the amendment's
    effective date. If we leave only the expiring snapshot ``REPLACE`` op in
    the replay product stream, timeline materialization can revive an older
    permanent background version after the zero-day expiry. The honest fix is
    to emit a permanent repeal/tombstone at the replay-products boundary.

    This rewrite is intentionally narrow:
    - only section-root ``snapshot_section_*`` ops are considered
    - the op must already be a zero-day snapshot (effective == expires)
    - the same amendment must not have any non-snapshot ops under that section,
      otherwise we may be looking at a partial repeal / renumber family rather
      than a whole-section repeal
    """
    if lo_ops_out is None or not section_labels:
        return False

    eligible_indices: list[int] = []
    blocked_labels: set[str] = set()

    def _section_label(lo: _LegalOperation) -> str:
        return next((v for k, v in reversed(lo.target.path) if k == "section"), "").lower()

    def _unique_chapter_for_section(section_label: str) -> Optional[str]:
        if chapter_section_map is None:
            return None
        owners = [
            chapter_label
            for chapter_label, sections in chapter_section_map.items()
            if chapter_label is not None and section_label in sections
        ]
        if len(owners) != 1:
            return None
        if section_label in chapter_section_map.get(None, set()):
            return None
        return owners[0]

    def _scoped_target(lo: _LegalOperation) -> LegalAddress:
        if chapter_section_map is None or any(kind == "chapter" for kind, _ in lo.target.path):
            return lo.target
        sec_label = _section_label(lo)
        if not sec_label:
            return lo.target
        chapter_label = _unique_chapter_for_section(sec_label)
        if chapter_label is None:
            return lo.target
        path = list(lo.target.path)
        insert_at = next((i for i, (kind, _label) in enumerate(path) if kind == "section"), len(path))
        return LegalAddress(path=tuple(path[:insert_at] + [("chapter", chapter_label)] + path[insert_at:]))

    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        sec_label = _section_label(lo)
        if not sec_label or sec_label not in section_labels:
            continue
        is_snapshot = lo.op_id.startswith("snapshot_")
        if is_snapshot and lo.target.path:
            # Derived child snapshots are part of the same whole-section
            # snapshot family and must not block conversion of the root
            # section snapshot into a durable repeal/tombstone.
            continue
        if not lo.op_id.startswith("snapshot_section_"):
            blocked_labels.add(sec_label)
            continue
        if not lo.target.path or lo.target.path[-1][0] != "section":
            blocked_labels.add(sec_label)
            continue

    updated = False
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        sec_label = _section_label(lo)
        if not sec_label or sec_label not in section_labels or sec_label in blocked_labels:
            continue
        if not lo.op_id.startswith("snapshot_section_"):
            continue
        if lo.action not in (StructuralAction.REPLACE, StructuralAction.INSERT):
            continue
        # The snapshot is eligible for REPEAL conversion if it is a zero-day
        # snapshot (effective == expires) *or* if its expires was set by
        # _rewrite_lo_op_source_expiry for a kumotaan clause (section already
        # confirmed to be in section_labels above). We accept any snapshot for
        # a section in the kumotaan set regardless of expiry status; the only
        # disallowed case is a non-snapshot op appearing under a snapshot_section_
        # op_id prefix - but those were blocked by the not-startswith guard above.
        #
        # Original guard (zero-day only) was:
        #   if not src.effective or src.effective != src.expires:
        #       if src.expires: continue
        # That guard rejected expiry-rewritten snapshots where the kumotaan
        # effective date differs from the amendment's issue/publication date,
        # causing the repealed section to survive in the oracle surface.
        # Chapter-scoped guard: when chapter_section_map is provided, only convert
        # ops whose (chapter, section) pair is covered by the map.
        if chapter_section_map is not None:
            scoped_target = _scoped_target(lo)
            chap_label = next((v for k, v in reversed(scoped_target.path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label not in (global_secs | chap_secs):
                continue
        eligible_indices.append(i)

    for i in eligible_indices:
        lo = lo_ops_out[i]
        assert lo.source is not None
        target = _scoped_target(lo)
        lo_ops_out[i] = dc_replace(
            lo,
            action=StructuralAction.REPEAL,
            target=target,
            payload=None,
            source=dc_replace(
                lo.source,
                expires="",
                raw_text=lo.source.raw_text or source_raw_text.strip(),
            ),
        )
        updated = True

    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        sec_label = _section_label(lo)
        if not sec_label or sec_label not in section_labels:
            continue
        if lo.action is not StructuralAction.REPEAL:
            continue
        if not src.expires:
            continue
        scoped_target = _scoped_target(lo)
        if chapter_section_map is not None:
            chap_label = next((v for k, v in reversed(scoped_target.path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label not in (global_secs | chap_secs):
                continue
        lo_ops_out[i] = dc_replace(lo, target=scoped_target, source=dc_replace(src, expires=""))
        updated = True

    return updated


def _inject_pure_kumotaan_repeal_ops(
    lo_ops_out: List[_LegalOperation],
    *,
    amendment_id: str,
    source_title: str,
    amendment_issue_date: Optional[dt.date],
    kumotaan_labels: List[str],
    chap_map_sets: Optional[Dict[Optional[str], Set[str]]],
    amendment_effective_date: dt.date,
    state: ReplayState,
    source_raw_text: str,
) -> PureKumotaanInjectionResult:
    """Inject REPEAL lo_ops for pure-kumotaan sections that have no existing lo_ops.

    When an amendment repeals a section purely via the kumotaan clause (no body
    text for that section), the normal path emits no lo_ops for it.
    ``_rewrite_lo_op_source_expiry`` returns False (nothing to rewrite), so no
    REPEAL tombstone is ever injected.

    This function closes that gap: for each kumotaan section that has zero ops
    from ``amendment_id`` in ``lo_ops_out`` AT THE TARGETED (chapter, section)
    address, and whose address exists in the parent ``state`` IR, a permanent
    REPEAL lo_op is appended.

    The coverage check is chapter-aware when ``chap_map_sets`` is provided:
    an op for section "9" in chapter "10" does NOT cover a kumotaan for section
    "9" in chapter "5".

    Returns the typed witness records for every injected REPEAL op so the
    driver can emit a structured finding per reconstructed repeal.
    """
    if not kumotaan_labels:
        return PureKumotaanInjectionResult()

    # Build chapter-aware coverage: set of (chapter_lower_or_None, section_lower)
    # pairs for which this amendment already has a REPEAL op.
    #
    # We only count REPEAL ops as "coverage" for kumotaan injection: if the
    # amendment has a REPLACE/INSERT op for a section that is also in the
    # kumotaan clause (e.g. an amendment that both rewrites and declares a
    # repeal), the REPLACE/INSERT does NOT suppress injection of the kumotaan
    # REPEAL tombstone - the repeal wins.
    #
    # The REPEAL op may have been created earlier in the same pipeline step
    # by ``_rewrite_kumotaan_snapshot_replaces_to_repeal``; sections for which
    # that conversion succeeded are already REPEAL-covered here.
    covered_chap_secs: Set[Tuple[Optional[str], str]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        if lo.action is not StructuralAction.REPEAL:
            continue
        sec_label = next((v for k, v in reversed(lo.target.path) if k == "section"), "")
        if not sec_label:
            continue
        chap_label = next((v for k, v in reversed(lo.target.path) if k == "chapter"), None)
        covered_chap_secs.add((chap_label.lower() if chap_label else None, sec_label.lower()))

    effective_iso = amendment_effective_date.isoformat()
    enacted_iso = amendment_issue_date.isoformat() if amendment_issue_date else effective_iso
    repeal_src = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=enacted_iso,
        effective=effective_iso,
        raw_text=source_raw_text.strip(),
    )

    injected: list[PureKumotaanInjectedRepeal] = []
    for label in kumotaan_labels:
        label_lower = label.lower()

        # Determine chapter(s) for this label.
        if chap_map_sets is not None:
            # Chapter-scoped: find which chapters list this section.
            target_chapters: List[Optional[str]] = [
                chap
                for chap, secs in chap_map_sets.items()
                if chap is not None and label_lower in secs
            ]
            if not target_chapters:
                # Possibly listed under global (None) key only.
                if label_lower in chap_map_sets.get(None, set()):
                    target_chapters = [None]
                else:
                    target_chapters = [None]
        else:
            target_chapters = [None]

        for chap in target_chapters:
            # Chapter-aware coverage check: skip only if there's already an op
            # from this amendment targeting THIS (chapter, section) address.
            chap_key = chap.lower() if chap is not None else None
            if (chap_key, label_lower) in covered_chap_secs:
                continue

            # Constraint: section must exist in the parent state.
            sec_path = state.find_section_path(label, chap)
            if sec_path is None:
                continue

            # Target the resolved section path verbatim.  ``sec_path`` already
            # carries any enclosing part scope (e.g. ``part:4/chapter:15/
            # section:26``); rebuilding a bare ``(chapter, section)`` address
            # would lose that part scope, leaving the REPEAL unresolvable when
            # the chapter is nested under a part — and ambiguous when several
            # parts contain the same chapter number.  Strip empty-label steps
            # (synthetic ``body``/``hcontainer`` wrappers carry no label) so the
            # address matches the timeline address space, which only keys nodes
            # with a non-empty label — the same convention the sibling
            # subsection-repeal injection uses.
            target_path: Tuple[Tuple[str, str], ...] = tuple(
                (str(step_kind), str(step_label))
                for step_kind, step_label in sec_path
                if str(step_label)
            )

            op_id = (
                f"pure_repeal_ch{chap}_{label}_{amendment_id}"
                if chap is not None
                else f"pure_repeal_{label}_{amendment_id}"
            )
            lo_ops_out.append(
                _LegalOperation(
                    op_id=op_id,
                    sequence=0,
                    action=StructuralAction.REPEAL,
                    target=LegalAddress(path=target_path),
                    source=repeal_src,
                    group_id=f"finland-johto:{amendment_id}",
                    witness_rule_id=FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID,
                )
            )
            injected.append(
                PureKumotaanInjectedRepeal(
                    rule_id=FI_RECOVERY_PURE_KUMOTAAN_REPEAL_RULE_ID,
                    op_id=op_id,
                    target_unit_kind="section",
                    target_norm=label_lower,
                    target_chapter=(chap.lower() if chap is not None else ""),
                )
            )

    return PureKumotaanInjectionResult(injected=tuple(injected))


def _live_suffix_section_labels_for_numeric_kumotaan_ranges(
    johto: str,
    *,
    state: ReplayState,
) -> Dict[Optional[str], Set[str]]:
    """Return live letter-suffix sections covered by scoped numeric repeal ranges.

    A bare numeric range may only absorb letter-suffix sections inside the same
    resolved structural scope.  Without this guard, an unqualified ``67-70 §``
    range can expire unrelated ``70a``/``70f`` sections in later chapters.
    """
    text = johto.lower()
    # lawvm-regex: owning_parser clause-boundary segmenter (same family as kumotaan.py); op injection itself is witnessed (PureKumotaanInjected* + witness_rule_id)
    kumotaan_match = re.search(
        r"kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)",
        text,
        re.DOTALL,
    )
    if not kumotaan_match:
        return {}

    kumotaan_text = kumotaan_match.group(1)
    # lawvm-regex: owning_parser chapter-marker lexer for scoped numeric-range live-suffix expansion
    markers = list(re.finditer(r"(\d+(?:\s*[a-z])?)\s+luvun\b", kumotaan_text))
    blocks: list[tuple[Optional[str], str]] = []
    if markers and markers[0].start() > 0:
        blocks.append((None, kumotaan_text[:markers[0].start()]))
    if markers:
        for idx, marker in enumerate(markers):
            chapter = re.sub(r"\s+", "", marker.group(1).strip())
            start = marker.end()
            end = markers[idx + 1].start() if idx + 1 < len(markers) else len(kumotaan_text)
            blocks.append((chapter, kumotaan_text[start:end]))
    else:
        blocks.append((None, kumotaan_text))

    def _section_labels(node: IRNode) -> list[str]:
        labels: list[str] = []

        def _walk(cur: IRNode) -> None:
            if cur.kind is IRNodeKind.SECTION and cur.label:
                labels.append(str(cur.label).lower())
                return
            for child in cur.children:
                _walk(child)

        _walk(node)
        return labels

    def _chapter_roots(root: IRNode) -> list[tuple[str, IRNode]]:
        roots: list[tuple[str, IRNode]] = []

        def _walk(cur: IRNode) -> None:
            if cur.kind is IRNodeKind.CHAPTER and cur.label:
                roots.append((str(cur.label).lower(), cur))
                return
            for child in cur.children:
                _walk(child)

        _walk(root)
        return roots

    chapter_roots = _chapter_roots(state.ir)

    def _scope_for_range(chapter: Optional[str], start: int, end: int) -> tuple[Optional[str], Optional[IRNode]]:
        if chapter is not None:
            return chapter, state.find_chapter(chapter)
        numeric_labels = {str(label) for label in range(start, end + 1)}
        containing_chapters: list[tuple[str, IRNode]] = []
        for chapter_label, chapter_root in chapter_roots:
            labels = set(_section_labels(chapter_root))
            if labels.intersection(numeric_labels):
                containing_chapters.append((chapter_label, chapter_root))
        if not containing_chapters:
            return None, state.ir
        if len(containing_chapters) == 1:
            return containing_chapters[0]
        return None, None

    additions: Dict[Optional[str], Set[str]] = {}
    for chapter, block in blocks:
        ranges = [
            (int(match.group(1)), int(match.group(2)))
            # lawvm-regex: owning_parser whole-section numeric-range site for the live letter-suffix absorption guard
            for match in re.finditer(r"\b(\d+)\s*[–—―\-]\s*(\d+)\s*§(?!:)", block)
            if int(match.group(1)) <= int(match.group(2))
        ]
        for start, end in ranges:
            scoped_chapter, live_root = _scope_for_range(chapter, start, end)
            if live_root is None:
                continue
            for label in _section_labels(live_root):
                label_match = re.fullmatch(r"(\d+)[a-zäöå]+", label, flags=re.I)
                if label_match is None:
                    continue
                base = int(label_match.group(1))
                if start <= base <= end:
                    additions.setdefault(scoped_chapter, set()).add(label)
    return additions


def _non_empty_path(path: Tuple[Tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple((str(kind), str(label)) for kind, label in path if str(label))


def _section_path_from_target(
    target: LegalAddress,
    section_label: str,
) -> tuple[tuple[str, str], ...] | None:
    path: list[tuple[str, str]] = []
    section_norm = section_label.lower()
    for kind, label in target.path:
        kind_text = str(kind)
        label_text = str(label)
        if not label_text:
            continue
        path.append((kind_text, label_text))
        if kind_text == "section":
            if label_text.lower() == section_norm:
                return tuple(path)
            return None
    return None


def _subsection_path_from_target(target: LegalAddress) -> tuple[tuple[str, str], ...] | None:
    path: list[tuple[str, str]] = []
    saw_section = False
    for kind, label in target.path:
        kind_text = str(kind)
        label_text = str(label)
        if not label_text:
            continue
        path.append((kind_text, label_text))
        if kind_text == "section":
            saw_section = True
        if kind_text == "subsection":
            return tuple(path) if saw_section else None
    return None


def _unique_source_scoped_section_path(
    lo_ops_out: list[_LegalOperation],
    *,
    amendment_id: str,
    section_label: str,
    state: ReplayState,
) -> tuple[tuple[str, str], ...] | None:
    paths: list[tuple[tuple[str, str], ...]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        section_path = _section_path_from_target(lo.target, section_label)
        if section_path is None:
            continue
        chapter_label = next(
            (label for kind, label in reversed(section_path) if kind == "chapter"),
            None,
        )
        part_label = next(
            (label for kind, label in reversed(section_path) if kind == "part"),
            None,
        )
        if state.find_section(section_label, chapter_label, part_label) is None:
            continue
        if section_path in seen:
            continue
        seen.add(section_path)
        paths.append(section_path)
    if len(paths) == 1:
        return paths[0]
    return None


def _resolve_pure_kumotaan_subsection_section(
    *,
    lo_ops_out: list[_LegalOperation],
    amendment_id: str,
    section_label: str,
    sub_labels: list[str],
    state: ReplayState,
) -> tuple[_ResolvedKumotaanSubsectionSection | None, PureKumotaanSubsectionSkippedTarget | None]:
    source_scoped_path = _unique_source_scoped_section_path(
        lo_ops_out,
        amendment_id=amendment_id,
        section_label=section_label,
        state=state,
    )
    if source_scoped_path is not None:
        chapter_label = next(
            (label for kind, label in reversed(source_scoped_path) if kind == "chapter"),
            None,
        )
        part_label = next(
            (label for kind, label in reversed(source_scoped_path) if kind == "part"),
            None,
        )
        section_node = state.find_section(section_label, chapter_label, part_label)
        if section_node is not None:
            return (
                _ResolvedKumotaanSubsectionSection(
                    section_path=source_scoped_path,
                    section_node=section_node,
                    source_scoped=True,
                ),
                None,
            )

    live_paths = tuple(
        (_non_empty_path(path), path)
        for path in section_paths_for_label(state.provision_index, section_label)
        if state.resolve(path) is not None
    )
    unique_live_paths = tuple(
        dict.fromkeys(address_path for address_path, _node_path in live_paths)
    )
    if len(unique_live_paths) > 1:
        return (
            None,
            PureKumotaanSubsectionSkippedTarget(
                rule_id="fi_pure_kumotaan_subsection_requires_unambiguous_section_scope",
                reason="ambiguous_duplicate_section_label_without_source_scope",
                section_label=section_label,
                subsection_labels=tuple(sub_labels),
                candidate_paths=tuple(LegalAddress(path=path) for path in unique_live_paths),
            ),
        )
    if len(unique_live_paths) == 1:
        address_path = unique_live_paths[0]
        node_path = next(
            node_path for candidate, node_path in live_paths if candidate == address_path
        )
        section_node = state.resolve(node_path)
        if section_node is not None:
            return (
                _ResolvedKumotaanSubsectionSection(
                    section_path=address_path,
                    section_node=section_node,
                    source_scoped=False,
                ),
                None,
            )
    return None, None


def _inject_pure_kumotaan_subsection_repeal_ops(
    lo_ops_out: List[_LegalOperation],
    *,
    amendment_id: str,
    source_title: str,
    kumotaan_subsection_map: dict[str, list[str]],
    amendment_effective_date: dt.date,
    amendment_issue_date: Optional[dt.date] = None,
    state: ReplayState,
    source_raw_text: str = "",
) -> PureKumotaanSubsectionInjectionResult:
    """Inject REPLACE (repeal-placeholder) lo_ops for pure-kumotaan subsection ranges.

    Handles "N §:n M-P momentti" kumotaan clauses where the amendment contains no
    body text for those subsections (no REPLACE or INSERT op was produced for them).
    Injects explicit REPLACE ops carrying a repeal-placeholder IRNode so that
    compile_timelines creates a non-None version and materialize_pit renders the
    subsection as a tombstone node with lawvm_repeal_placeholder="1" rather than
    omitting it entirely.

    Only injects for subsections that:
    - are NOT already covered by a REPLACE or REPEAL op from this amendment, AND
    - exist in the current parent state (section exists, subsection exists).

    Returns typed injection/skipped-target evidence.
    """
    if not kumotaan_subsection_map:
        return PureKumotaanSubsectionInjectionResult(injected_count=0)

    # Build exact subsection addresses already covered by any non-snapshot
    # REPLACE/REPEAL op from this amendment targeting a subsection.
    # Snapshot REPEAL ops are deliberately excluded: if a snapshot created a
    # REPEAL for the same subsection, we still want to inject a REPLACE+placeholder
    # so that the subsection appears as a repeal marker rather than being absent
    # entirely. The REPLACE+placeholder will have a higher list index than the
    # snapshot REPEAL and will win via pick_latest's same_source_late_placeholder
    # tie-break (placeholder with higher index wins over non-placeholder).
    covered: set[tuple[tuple[str, str], ...]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        # Skip snapshot-created ops (op_id starts with "snapshot_")
        if lo.op_id.startswith("snapshot_"):
            continue
        if lo.action not in (StructuralAction.REPEAL, StructuralAction.REPLACE):
            continue
        sub_path = _subsection_path_from_target(lo.target)
        if sub_path is not None:
            covered.add(sub_path)

    def _same_group_snapshot_carries_subsection(
        section_path: tuple[tuple[str, str], ...],
        sub_label: str,
    ) -> bool:
        sub_norm = _norm_num_token(sub_label)
        if not sub_norm:
            return False
        for lo in lo_ops_out:
            src = lo.source
            if src is None or src.statute_id != amendment_id:
                continue
            if not lo.op_id.startswith("snapshot_section_"):
                continue
            if lo.target.path != section_path:
                continue
            payload = lo.payload
            if payload is None or payload.kind is not IRNodeKind.SECTION:
                continue
            for child in payload.children:
                if child.kind is not IRNodeKind.SUBSECTION or not child.label:
                    continue
                if _norm_num_token(child.label) != sub_norm:
                    continue
                if child.attrs.get("lawvm_repeal_placeholder") == "1":
                    continue
                return True
        return False

    effective_iso = amendment_effective_date.isoformat()
    enacted_iso = amendment_issue_date.isoformat() if amendment_issue_date else effective_iso
    repeal_src = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=enacted_iso,
        effective=effective_iso,
        raw_text=source_raw_text.strip(),
    )
    injected = 0
    injected_records: list[PureKumotaanInjectedRepeal] = []
    skipped: list[PureKumotaanSubsectionSkippedTarget] = []
    for sec_label, sub_labels in kumotaan_subsection_map.items():
        resolved, skip = _resolve_pure_kumotaan_subsection_section(
            lo_ops_out=lo_ops_out,
            amendment_id=amendment_id,
            section_label=sec_label,
            sub_labels=sub_labels,
            state=state,
        )
        if skip is not None:
            skipped.append(skip)
            continue
        if resolved is None:
            continue

        for sub_label in sub_labels:
            target_path = resolved.section_path + (("subsection", sub_label),)
            if target_path in covered:
                continue
            if _same_group_snapshot_carries_subsection(resolved.section_path, sub_label):
                continue
            # Check that the subsection exists in the current IR.
            sub_exists = any(
                c.kind is IRNodeKind.SUBSECTION and c.label == sub_label
                for c in resolved.section_node.children
            )
            if not sub_exists and not resolved.source_scoped:
                continue

            op_id = f"pure_subsec_repeal_{sec_label}_{sub_label}_{amendment_id}"
            # Use REPLACE with a repeal-placeholder payload so that
            # compile_timelines creates a version with non-None content and
            # materialize_pit renders the subsection as a tombstone node rather
            # than omitting it entirely.
            sub_placeholder = IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=sub_label,
                attrs={"lawvm_repeal_placeholder": "1"},
                children=(),
            )
            lo_ops_out.append(
                _LegalOperation(
                    op_id=op_id,
                    sequence=0,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=target_path),
                    payload=sub_placeholder,
                    source=repeal_src,
                    group_id=f"finland-johto:{amendment_id}",
                    witness_rule_id=FI_RECOVERY_PURE_KUMOTAAN_SUBSECTION_REPEAL_RULE_ID,
                )
            )
            injected += 1
            chapter_label = next(
                (label for kind, label in reversed(resolved.section_path) if kind == "chapter"),
                "",
            )
            injected_records.append(
                PureKumotaanInjectedRepeal(
                    rule_id=FI_RECOVERY_PURE_KUMOTAAN_SUBSECTION_REPEAL_RULE_ID,
                    op_id=op_id,
                    target_unit_kind="subsection",
                    target_norm=f"{sec_label}:{sub_label}",
                    target_chapter=(chapter_label or "").lower(),
                )
            )

    return PureKumotaanSubsectionInjectionResult(
        injected_count=injected,
        skipped_targets=tuple(skipped),
        injected=tuple(injected_records),
    )


def _item_path_from_target(target: LegalAddress) -> tuple[tuple[str, str], ...] | None:
    path: list[tuple[str, str]] = []
    saw_section = False
    saw_subsection = False
    for kind, label in target.path:
        kind_text = str(kind)
        label_text = str(label)
        if not label_text:
            continue
        path.append((kind_text, label_text))
        if kind_text == "section":
            saw_section = True
        if kind_text == "subsection":
            saw_subsection = True
        if kind_text == "item":
            return tuple(path) if saw_section and saw_subsection else None
    return None


def _resolve_pure_kumotaan_item_target(
    *,
    lo_ops_out: list[_LegalOperation],
    amendment_id: str,
    target: KumotaanItemTarget,
    state: ReplayState,
) -> tuple[_ResolvedKumotaanItemTarget | None, PureKumotaanItemSkippedTarget | None]:
    if target.chapter_label:
        raw_path = state.find_section_path(target.section_label, target.chapter_label)
        section_node = state.find_section(target.section_label, target.chapter_label)
        if raw_path is None or section_node is None:
            return (
                None,
                PureKumotaanItemSkippedTarget(
                    rule_id="fi_pure_kumotaan_item_requires_resolved_section_scope",
                    reason="section_not_found_in_explicit_chapter_scope",
                    section_label=target.section_label,
                    subsection_label=target.subsection_label,
                    item_label=target.item_label,
                ),
            )
        section_path = _non_empty_path(raw_path)
    else:
        resolved, skipped_subsection = _resolve_pure_kumotaan_subsection_section(
            lo_ops_out=lo_ops_out,
            amendment_id=amendment_id,
            section_label=target.section_label,
            sub_labels=[target.subsection_label or target.item_label],
            state=state,
        )
        if skipped_subsection is not None:
            return (
                None,
                PureKumotaanItemSkippedTarget(
                    rule_id="fi_pure_kumotaan_item_requires_unambiguous_section_scope",
                    reason=skipped_subsection.reason,
                    section_label=target.section_label,
                    subsection_label=target.subsection_label,
                    item_label=target.item_label,
                    candidate_paths=skipped_subsection.candidate_paths,
                ),
            )
        if resolved is None:
            return None, None
        section_path = resolved.section_path
        section_node = resolved.section_node

    item_norm = _norm_num_token(target.item_label)
    if not item_norm:
        return None, None

    if target.subsection_label is not None:
        sub_norm = _norm_num_token(target.subsection_label)
        subsection = next(
            (
                child
                for child in section_node.children
                if child.kind is IRNodeKind.SUBSECTION
                and child.label
                and _norm_num_token(child.label) == sub_norm
            ),
            None,
        )
        if subsection is None:
            return (
                None,
                PureKumotaanItemSkippedTarget(
                    rule_id="fi_pure_kumotaan_item_requires_existing_subsection",
                    reason="explicit_subsection_not_found",
                    section_label=target.section_label,
                    subsection_label=target.subsection_label,
                    item_label=target.item_label,
                ),
            )
        if not any(
            child.kind is IRNodeKind.PARAGRAPH
            and child.label
            and _norm_num_token(child.label) == item_norm
            for child in subsection.children
        ):
            return (
                None,
                PureKumotaanItemSkippedTarget(
                    rule_id="fi_pure_kumotaan_item_requires_existing_item",
                    reason="explicit_subsection_item_not_found",
                    section_label=target.section_label,
                    subsection_label=target.subsection_label,
                    item_label=target.item_label,
                ),
            )
        item_path = section_path + (("subsection", sub_norm), ("item", item_norm))
        return (
            _ResolvedKumotaanItemTarget(
                item_path=item_path,
                section_path=section_path,
                subsection_label=sub_norm,
            ),
            None,
        )

    candidates: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for subsection in section_node.children:
        if subsection.kind is not IRNodeKind.SUBSECTION or not subsection.label:
            continue
        sub_norm = _norm_num_token(subsection.label)
        if not sub_norm:
            continue
        if any(
            child.kind is IRNodeKind.PARAGRAPH
            and child.label
            and _norm_num_token(child.label) == item_norm
            for child in subsection.children
        ):
            candidates.append((sub_norm, section_path + (("subsection", sub_norm), ("item", item_norm))))

    if len(candidates) == 1:
        sub_norm, item_path = candidates[0]
        return (
            _ResolvedKumotaanItemTarget(
                item_path=item_path,
                section_path=section_path,
                subsection_label=sub_norm,
            ),
            None,
        )
    if len(candidates) > 1:
        return (
            None,
            PureKumotaanItemSkippedTarget(
                rule_id="fi_pure_kumotaan_item_requires_unambiguous_subsection_scope",
                reason="ambiguous_item_label_without_subsection_scope",
                section_label=target.section_label,
                item_label=target.item_label,
                candidate_paths=tuple(LegalAddress(path=path) for _sub, path in candidates),
            ),
        )
    return (
        None,
        PureKumotaanItemSkippedTarget(
            rule_id="fi_pure_kumotaan_item_requires_existing_item",
            reason="item_not_found_in_resolved_section",
            section_label=target.section_label,
            item_label=target.item_label,
        ),
    )


def _inject_pure_kumotaan_item_repeal_ops(
    lo_ops_out: List[_LegalOperation],
    *,
    amendment_id: str,
    source_title: str,
    kumotaan_item_targets: tuple[KumotaanItemTarget, ...],
    amendment_effective_date: dt.date,
    amendment_issue_date: Optional[dt.date] = None,
    state: ReplayState,
    source_raw_text: str = "",
) -> PureKumotaanItemInjectionResult:
    """Inject REPLACE repeal-placeholder lo_ops for pure item-level kumotaan clauses."""
    if not kumotaan_item_targets:
        return PureKumotaanItemInjectionResult(injected_count=0)

    covered: set[tuple[tuple[str, str], ...]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        if lo.action not in (StructuralAction.REPEAL, StructuralAction.REPLACE):
            continue
        item_path = _item_path_from_target(lo.target)
        if item_path is not None:
            covered.add(item_path)

    effective_iso = amendment_effective_date.isoformat()
    enacted_iso = amendment_issue_date.isoformat() if amendment_issue_date else effective_iso
    repeal_src = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=enacted_iso,
        effective=effective_iso,
        raw_text=source_raw_text.strip(),
    )

    injected = 0
    injected_records: list[PureKumotaanInjectedRepeal] = []
    skipped: list[PureKumotaanItemSkippedTarget] = []
    for target in kumotaan_item_targets:
        resolved, skip = _resolve_pure_kumotaan_item_target(
            lo_ops_out=lo_ops_out,
            amendment_id=amendment_id,
            target=target,
            state=state,
        )
        if skip is not None:
            skipped.append(skip)
            continue
        if resolved is None or resolved.item_path in covered:
            continue

        op_id = (
            f"pure_item_repeal_{target.section_label}_"
            f"{resolved.subsection_label}_{target.item_label}_{amendment_id}"
        )
        item_placeholder = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=_norm_num_token(target.item_label),
            attrs={"lawvm_repeal_placeholder": "1"},
            children=(),
        )
        lo_ops_out.append(
            _LegalOperation(
                op_id=op_id,
                sequence=0,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=resolved.item_path),
                payload=item_placeholder,
                source=repeal_src,
                group_id=f"finland-johto:{amendment_id}",
                witness_rule_id=FI_RECOVERY_PURE_KUMOTAAN_ITEM_REPEAL_RULE_ID,
            )
        )
        covered.add(resolved.item_path)
        injected += 1
        chapter_label = next(
            (label for kind, label in reversed(resolved.section_path) if kind == "chapter"),
            "",
        )
        injected_records.append(
            PureKumotaanInjectedRepeal(
                rule_id=FI_RECOVERY_PURE_KUMOTAAN_ITEM_REPEAL_RULE_ID,
                op_id=op_id,
                target_unit_kind="item",
                target_norm=f"{target.section_label}:{resolved.subsection_label}:{target.item_label}",
                target_chapter=(chapter_label or "").lower(),
            )
        )

    return PureKumotaanItemInjectionResult(
        injected_count=injected,
        skipped_targets=tuple(skipped),
        injected=tuple(injected_records),
    )
