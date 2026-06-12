"""Replay-product helpers for Finnish kumotaan repeal clauses.

Pure kumotaan extraction lives in ``kumotaan.py``. This module owns the later
replay-product boundary: converting or injecting typed LegalOperations once a
kumotaan target has been established.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


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
) -> int:
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

    Returns the number of ops injected.
    """
    if not kumotaan_labels:
        return 0

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

    injected = 0
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

            if chap is not None:
                target_path: Tuple[Tuple[str, str], ...] = (
                    ("chapter", chap),
                    ("section", label),
                )
            else:
                target_path = (("section", label),)

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
                )
            )
            injected += 1

    return injected


def _live_suffix_section_labels_for_numeric_kumotaan_ranges(
    johto: str,
    *,
    state: ReplayState,
) -> Dict[Optional[str], Set[str]]:
    """Return live letter-suffix sections covered by explicit numeric repeal ranges."""
    text = johto.lower()
    kumotaan_match = re.search(
        r"kumotaan\b(.*?)(?:muutetaan|lisätään|seuraavasti|sekä\s+muutetaan|sekä\s+lisätään|$)",
        text,
        re.DOTALL,
    )
    if not kumotaan_match:
        return {}

    kumotaan_text = kumotaan_match.group(1)
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

    additions: Dict[Optional[str], Set[str]] = {}
    for chapter, block in blocks:
        live_root = state.find_chapter(chapter) if chapter is not None else state.ir
        if live_root is None:
            continue
        live_labels = _section_labels(live_root)
        ranges = [
            (int(match.group(1)), int(match.group(2)))
            for match in re.finditer(r"\b(\d+)\s*[–—―\-]\s*(\d+)\s*§(?!:)", block)
            if int(match.group(1)) <= int(match.group(2))
        ]
        if not ranges:
            continue
        for label in live_labels:
            label_match = re.fullmatch(r"(\d+)[a-zäöå]+", label, flags=re.I)
            if label_match is None:
                continue
            base = int(label_match.group(1))
            if any(start <= base <= end for start, end in ranges):
                additions.setdefault(chapter, set()).add(label)
    return additions


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
) -> int:
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

    Returns the number of ops injected.
    """
    if not kumotaan_subsection_map:
        return 0

    # Build set of (section_lower, subsection_lower) pairs already covered by
    # any non-snapshot REPLACE op from this amendment targeting a subsection.
    # Snapshot REPEAL ops are deliberately excluded: if a snapshot created a
    # REPEAL for the same subsection, we still want to inject a REPLACE+placeholder
    # so that the subsection appears as a repeal marker rather than being absent
    # entirely. The REPLACE+placeholder will have a higher list index than the
    # snapshot REPEAL and will win via pick_latest's same_source_late_placeholder
    # tie-break (placeholder with higher index wins over non-placeholder).
    covered: set[tuple[str, str]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        # Skip snapshot-created ops (op_id starts with "snapshot_")
        if lo.op_id.startswith("snapshot_"):
            continue
        if lo.action not in (StructuralAction.REPEAL, StructuralAction.REPLACE):
            continue
        sec_label = next((v for k, v in reversed(lo.target.path) if k == "section"), "")
        sub_label = next((v for k, v in reversed(lo.target.path) if k == "subsection"), "")
        if sec_label and sub_label:
            covered.add((sec_label.lower(), sub_label.lower()))

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
    for sec_label, sub_labels in kumotaan_subsection_map.items():
        # Find the section path (with chapter context if needed).
        sec_path = state.find_section_path(sec_label, None)
        if sec_path is None:
            continue
        # Build prefix path from resolved section path. Strip empty-label
        # components (e.g. hcontainer wrappers with no label) so that the
        # resulting LegalAddress matches the timeline address space, which
        # only includes nodes whose label is non-empty.
        resolved_sec_path: tuple[tuple[str, str], ...] = tuple(
            (k, v) for k, v in sec_path if v
        )

        for sub_label in sub_labels:
            if (sec_label.lower(), sub_label.lower()) in covered:
                continue
            # Check that the subsection exists in the current IR.
            sec_node = state.find_section(sec_label)
            if sec_node is None:
                break
            sub_exists = any(
                c.kind is IRNodeKind.SUBSECTION and c.label == sub_label
                for c in sec_node.children
            )
            if not sub_exists:
                continue

            target_path = resolved_sec_path + (("subsection", sub_label),)
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
                )
            )
            injected += 1

    return injected
