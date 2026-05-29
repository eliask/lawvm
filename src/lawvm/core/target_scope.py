"""Shared target-scope normalization for proof/reporting surfaces.

Internal FI/core code should carry neutral unit kinds only. Legacy
codes belong at narrow boundary ingress, not in the shared
normalization contract itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

TargetUnitKind = Literal["section", "chapter", "part"]
NeutralTargetUnitKind = Literal["", "section", "chapter", "part"]


def _scope_text(value: object) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ResolvedTargetScope:
    """Normalized target scope extracted from compiled/adjudication rows."""

    target_unit_kind: NeutralTargetUnitKind = ""
    target_norm: str = ""
    target_chapter: str = ""
    target_part: str = ""
    target_section: str = ""

    def __post_init__(self) -> None:
        if self.target_unit_kind not in {"", "section", "chapter", "part"}:
            raise ValueError("ResolvedTargetScope.target_unit_kind is not supported")
        for field_name, value in (
            ("target_norm", self.target_norm),
            ("target_chapter", self.target_chapter),
            ("target_part", self.target_part),
            ("target_section", self.target_section),
        ):
            if not isinstance(value, str):
                raise TypeError(f"ResolvedTargetScope.{field_name} must be a string")


def normalize_target_unit_kind(value: object) -> NeutralTargetUnitKind:
    normalized = _scope_text(value).lower()
    value_to_kind: dict[str, NeutralTargetUnitKind] = {
        "section": "section",
        "chapter": "chapter",
        "part": "part",
    }
    return value_to_kind.get(normalized, "")


def _scope_kind_value(kind: NeutralTargetUnitKind) -> str:
    return str(kind)


def _default_target_norm(
    *,
    target_unit_kind: NeutralTargetUnitKind,
    target_norm: str,
    target_chapter: str,
    target_part: str,
    target_section: str,
) -> str:
    canonical_kind = _scope_kind_value(target_unit_kind)
    if target_norm:
        return target_norm
    if canonical_kind == "section":
        return target_section
    if canonical_kind == "chapter":
        return target_chapter
    if canonical_kind == "part":
        return target_part
    return ""


def _infer_target_unit_kind_from_scope(scope: ResolvedTargetScope) -> NeutralTargetUnitKind:
    if scope.target_unit_kind:
        return scope.target_unit_kind
    if scope.target_part:
        return "part"
    if scope.target_chapter and not scope.target_section and not scope.target_norm:
        return "chapter"
    if scope.target_norm and scope.target_chapter:
        return "section"
    if scope.target_section:
        return "section"
    return ""


def resolve_internal_target_scope(mapping: Mapping[str, object]) -> ResolvedTargetScope:
    """Resolve strict-neutral scope from internal reporting inputs."""
    target_unit_kind = normalize_target_unit_kind(mapping.get("target_unit_kind"))
    target_norm = _scope_text(mapping.get("target_norm"))
    target_chapter = _scope_text(mapping.get("target_chapter"))
    target_part = _scope_text(mapping.get("target_part"))
    target_section = _scope_text(mapping.get("target_section"))

    resolved_scope = ResolvedTargetScope(
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
        target_section=target_section,
    )
    inferred_target_unit_kind = _infer_target_unit_kind_from_scope(resolved_scope)
    return ResolvedTargetScope(
        target_unit_kind=inferred_target_unit_kind,
        target_norm=_default_target_norm(
            target_unit_kind=inferred_target_unit_kind,
            target_norm=resolved_scope.target_norm,
            target_chapter=resolved_scope.target_chapter,
            target_part=resolved_scope.target_part,
            target_section=resolved_scope.target_section,
        ),
        target_chapter=resolved_scope.target_chapter,
        target_part=resolved_scope.target_part,
        target_section=resolved_scope.target_section,
    )


def section_scope_parts(section_label: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for token in str(section_label or "").split("/"):
        kind, sep, value = token.partition(":")
        if sep and kind and value:
            parts[kind] = value
    return parts


def matching_sections_for_scope(
    *,
    scope: ResolvedTargetScope,
    section_labels: list[str],
) -> list[str]:
    target_unit_kind = _infer_target_unit_kind_from_scope(scope)
    scope_kind = _scope_kind_value(target_unit_kind)

    matched: list[str] = []

    if scope_kind == "chapter":
        chapter_ref = scope.target_norm or scope.target_chapter
        if not chapter_ref:
            return matched
        for label in section_labels:
            parts = section_scope_parts(label)
            if parts.get("chapter") != chapter_ref:
                continue
            if scope.target_part and parts.get("part") != scope.target_part:
                continue
            matched.append(label)
        return matched

    if scope_kind == "part":
        part_ref = scope.target_norm or scope.target_part
        if not part_ref:
            return matched
        return [
            label
            for label in section_labels
            if section_scope_parts(label).get("part") == part_ref
        ]

    section_ref = scope.target_norm or scope.target_section
    if not section_ref:
        return matched

    if section_ref in section_labels:
        matched.append(section_ref)

    for label in section_labels:
        parts = section_scope_parts(label)
        if parts.get("section") != section_ref:
            continue
        if scope.target_chapter and parts.get("chapter") != scope.target_chapter:
            continue
        if scope.target_part and parts.get("part") != scope.target_part:
            continue
        if label not in matched:
            matched.append(label)

    return matched
