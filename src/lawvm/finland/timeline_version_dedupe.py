"""Owned Finland timeline version dedupe before PIT materialization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_addresses import STRUCTURAL_RENUMBER_SNAPSHOT_ATTR

if TYPE_CHECKING:
    from lawvm.core.timeline import Timelines

FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID = (
    "fi.timeline.same_source_semantic_version_dedupe"
)
FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID = (
    "fi.timeline.absent_content_shadow_collapse"
)
FI_TIMELINE_RESTRUCTURE_RELABEL_SNAPSHOT_SHADOW_COLLAPSE_RULE_ID = (
    "fi.timeline.restructure_relabel_snapshot_shadow_collapse"
)
FI_TIMELINE_RESTRUCTURE_RELABEL_SHELL_SHADOW_COLLAPSE_RULE_ID = (
    "fi.timeline.restructure_relabel_shell_shadow_collapse"
)
_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_ATTR = "lawvm_restructure_relabel_section_snapshot"
_TIMELINE_SECTION_MARK_SPACING_RE = re.compile(r"^(\d+[a-z]?)\s*§")
SemanticTextKeyCache = dict[tuple[str, str], str]


@dataclass(frozen=True, slots=True)
class TimelineVersionDedupeRecord:
    """Evidence for one collapsed same-source timeline version row."""

    address: str
    source_statute: str
    effective: str
    enacted: str
    variant_kind: str
    witness_rule_id: str
    removed_count: int = 1


def _timeline_version_semantic_text_key(
    node: object | None,
    *,
    cache_key: tuple[str, str] | None = None,
    semantic_text_cache: SemanticTextKeyCache | None = None,
) -> str:
    if node is None:
        return ""
    if cache_key is not None and semantic_text_cache is not None:
        cached = semantic_text_cache.get(cache_key)
        if cached is not None:
            return cached
    if isinstance(node, IRNode):
        text = " ".join(irnode_to_text(node).split())
    else:
        text = str(node)
    normalized = _TIMELINE_SECTION_MARK_SPACING_RE.sub(r"\1 §", text)
    if cache_key is not None and semantic_text_cache is not None:
        semantic_text_cache[cache_key] = normalized
    return normalized


def _semantic_text_cache_key(version: ProvisionVersion) -> tuple[str, str]:
    if version.content_hash:
        return ("content_hash", version.content_hash)
    return ("node_id", str(id(version.content)))


def _collapse_absent_content_shadow_rows(
    address: LegalAddress,
    versions: list[ProvisionVersion],
) -> tuple[list[ProvisionVersion], list[TimelineVersionDedupeRecord]]:
    """Drop absent-content rows competing with substantive same-moment snapshots."""
    grouped: dict[tuple[str, str, str, str], list[ProvisionVersion]] = {}
    for version in versions:
        source_id = version.source.statute_id if version.source is not None else ""
        if not source_id:
            continue
        key = (source_id, version.effective, version.enacted, version.variant_kind)
        grouped.setdefault(key, []).append(version)

    drop_ids: set[int] = set()
    records: list[TimelineVersionDedupeRecord] = []
    for (source_id, effective, enacted, variant_kind), group in grouped.items():
        if len(group) < 2:
            continue
        with_content = [version for version in group if version.content is not None]
        without_content = [version for version in group if version.content is None]
        if not with_content or not without_content:
            continue
        for version in without_content:
            drop_ids.add(id(version))
        records.append(
            TimelineVersionDedupeRecord(
                address=str(address),
                source_statute=source_id,
                effective=effective,
                enacted=enacted,
                variant_kind=variant_kind,
                witness_rule_id=FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID,
                removed_count=len(without_content),
            )
        )

    if not drop_ids:
        return versions, records
    return [version for version in versions if id(version) not in drop_ids], records


def _is_restructure_relabel_snapshot(version: ProvisionVersion) -> bool:
    content = version.content
    return isinstance(content, IRNode) and content.attrs.get(_RESTRUCTURE_RELABEL_SECTION_SNAPSHOT_ATTR) == "1"


def _is_structural_renumber_snapshot(version: ProvisionVersion) -> bool:
    content = version.content
    return isinstance(content, IRNode) and content.attrs.get(STRUCTURAL_RENUMBER_SNAPSHOT_ATTR) == "1"


def _is_section_label_only_shell(version: ProvisionVersion) -> bool:
    """Return True for section snapshots that carry no legal body payload.

    A whole-container replacement may emit a direct section snapshot for each
    child section. If the child is only a ``num``/``omission`` shell while a
    same-source restructure relabel snapshot carries the moved body, the shell
    is not payload authority and must not shadow the moved section text.
    """
    content = version.content
    if not isinstance(content, IRNode) or content.kind is not IRNodeKind.SECTION:
        return False
    if content.text.strip():
        return False
    for child in content.children:
        if child.kind not in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.OMISSION}:
            return False
        if child.kind is IRNodeKind.OMISSION and child.text.strip():
            return False
    return True


def _collapse_restructure_relabel_snapshot_shadow_rows(
    address: LegalAddress,
    versions: list[ProvisionVersion],
) -> tuple[list[ProvisionVersion], list[TimelineVersionDedupeRecord]]:
    """Drop early restructure snapshots shadowed by same-source payload authority."""
    grouped: dict[tuple[str, str, str, str], list[ProvisionVersion]] = {}
    for version in versions:
        source_id = version.source.statute_id if version.source is not None else ""
        if not source_id:
            continue
        key = (source_id, version.effective, version.enacted, version.variant_kind)
        grouped.setdefault(key, []).append(version)

    drop_ids: set[int] = set()
    records: list[TimelineVersionDedupeRecord] = []
    for (source_id, effective, enacted, variant_kind), group in grouped.items():
        if len(group) < 2:
            continue
        restructure_snapshots = [
            version for version in group if _is_restructure_relabel_snapshot(version)
        ]
        non_snapshot_payloads = [
            version
            for version in group
            if version.content is not None
            and not _is_restructure_relabel_snapshot(version)
            and not _is_structural_renumber_snapshot(version)
        ]
        if not restructure_snapshots or not non_snapshot_payloads:
            continue
        substantive_payloads = [
            version
            for version in non_snapshot_payloads
            if not _is_section_label_only_shell(version)
        ]
        if substantive_payloads:
            for version in restructure_snapshots:
                drop_ids.add(id(version))
            records.append(
                TimelineVersionDedupeRecord(
                    address=str(address),
                    source_statute=source_id,
                    effective=effective,
                    enacted=enacted,
                    variant_kind=variant_kind,
                    witness_rule_id=FI_TIMELINE_RESTRUCTURE_RELABEL_SNAPSHOT_SHADOW_COLLAPSE_RULE_ID,
                    removed_count=len(restructure_snapshots),
                )
            )
            continue
        shell_payloads = [
            version
            for version in non_snapshot_payloads
            if _is_section_label_only_shell(version)
        ]
        if not shell_payloads:
            continue
        for version in shell_payloads:
            drop_ids.add(id(version))
        records.append(
            TimelineVersionDedupeRecord(
                address=str(address),
                source_statute=source_id,
                effective=effective,
                enacted=enacted,
                variant_kind=variant_kind,
                witness_rule_id=FI_TIMELINE_RESTRUCTURE_RELABEL_SHELL_SHADOW_COLLAPSE_RULE_ID,
                removed_count=len(shell_payloads),
            )
        )

    if not drop_ids:
        return versions, records
    return [version for version in versions if id(version) not in drop_ids], records


def _dedupe_same_source_semantic_versions(
    address: LegalAddress,
    versions: list[ProvisionVersion],
    *,
    semantic_text_cache: SemanticTextKeyCache | None = None,
) -> tuple[list[ProvisionVersion], list[TimelineVersionDedupeRecord]]:
    """Collapse same-source rows that differ only by non-semantic content shape."""
    deduped: list[ProvisionVersion] = []
    index_by_key: dict[tuple[object, ...], int] = {}
    records: list[TimelineVersionDedupeRecord] = []
    for version in versions:
        source_id = version.source.statute_id if version.source is not None else ""
        if not source_id or version.content is None:
            deduped.append(version)
            continue
        key = (
            source_id,
            version.effective,
            version.enacted,
            version.expires,
            version.variant_kind,
            tuple(version.applicability),
            _timeline_version_semantic_text_key(
                version.content,
                cache_key=_semantic_text_cache_key(version),
                semantic_text_cache=semantic_text_cache,
            ),
        )
        existing_index = index_by_key.get(key)
        if existing_index is None:
            index_by_key[key] = len(deduped)
            deduped.append(version)
            continue
        records.append(
            TimelineVersionDedupeRecord(
                address=str(address),
                source_statute=source_id,
                effective=version.effective,
                enacted=version.enacted,
                variant_kind=version.variant_kind,
                witness_rule_id=FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID,
            )
        )
        deduped[existing_index] = version
    return deduped, records


def dedupe_finland_timelines(
    timelines: Timelines,
    *,
    semantic_text_cache: SemanticTextKeyCache | None = None,
) -> tuple[Timelines, tuple[TimelineVersionDedupeRecord, ...]]:
    """Apply owned Finland timeline dedupe rules to every address bucket."""
    out: Timelines = {}
    records: list[TimelineVersionDedupeRecord] = []
    for address, timeline in timelines.items():
        versions = list(timeline.versions)
        versions, absent_records = _collapse_absent_content_shadow_rows(address, versions)
        versions, restructure_records = _collapse_restructure_relabel_snapshot_shadow_rows(
            address,
            versions,
        )
        versions, semantic_records = _dedupe_same_source_semantic_versions(
            address,
            versions,
            semantic_text_cache=semantic_text_cache,
        )
        records.extend(absent_records)
        records.extend(restructure_records)
        records.extend(semantic_records)
        out[address] = ProvisionTimeline(address=address, versions=versions)
    return out, tuple(records)


__all__ = [
    "FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID",
    "FI_TIMELINE_RESTRUCTURE_RELABEL_SHELL_SHADOW_COLLAPSE_RULE_ID",
    "FI_TIMELINE_RESTRUCTURE_RELABEL_SNAPSHOT_SHADOW_COLLAPSE_RULE_ID",
    "FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID",
    "SemanticTextKeyCache",
    "TimelineVersionDedupeRecord",
    "dedupe_finland_timelines",
]
