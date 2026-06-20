"""Owned Finland timeline version dedupe before PIT materialization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text

if TYPE_CHECKING:
    from lawvm.core.timeline import Timelines

FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID = (
    "fi.timeline.same_source_semantic_version_dedupe"
)
FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID = (
    "fi.timeline.absent_content_shadow_collapse"
)
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
        versions, semantic_records = _dedupe_same_source_semantic_versions(
            address,
            versions,
            semantic_text_cache=semantic_text_cache,
        )
        records.extend(absent_records)
        records.extend(semantic_records)
        out[address] = ProvisionTimeline(address=address, versions=versions)
    return out, tuple(records)


__all__ = [
    "FI_TIMELINE_ABSENT_CONTENT_SHADOW_COLLAPSE_RULE_ID",
    "FI_TIMELINE_SAME_SOURCE_SEMANTIC_DEDUPE_RULE_ID",
    "SemanticTextKeyCache",
    "TimelineVersionDedupeRecord",
    "dedupe_finland_timelines",
]
