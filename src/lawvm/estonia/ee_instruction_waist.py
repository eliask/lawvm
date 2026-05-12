"""Experimental Estonia instruction waist definitions.

This module defines a lightweight, explicit surface shape between parsing and
lowering into `LegalOperation`. It is intentionally small and local to Estonia
for now:

- explicit instruction families (structural / text-replace / wrapper-quoted)
- explicit text-replace mode and scope metadata
- explicit witness about wrapper-quoted nested payloads

No parser behavior is changed in this packet. The module is read-mostly and
designed to be migrated into the main lowering boundary without semantic
changes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Sequence

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.estonia.peg import extract_ee_ops


class EEInstructionFamily(str, Enum):
    structural = "structural"
    text_replace = "text_replace"
    wrapper_quoted_payload = "wrapper_quoted_payload"
    other = "other"


class EETextReplaceMode(str, Enum):
    replace = "replace"
    delete = "delete"
    insert_before = "insert_before"
    insert_after = "insert_after"
    unknown = "unknown"


@dataclass(frozen=True)
class EETextRewrite:
    old_surface: str = ""
    new_surface: str = ""
    mode: EETextReplaceMode = EETextReplaceMode.replace
    case_inflected: bool = False
    scope_chapters: tuple[str, ...] = ()
    exclude_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    generic_minister_plural: bool = False
    old_titles: tuple[str, ...] = ()
    source_family: str = ""
    appendix_table_update: bool = False
    appendix_marker: str = ""
    appendix_table_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class EETextRewriteWitness:
    source_text: str
    rewrite: EETextRewrite


@dataclass(frozen=True)
class EESentenceTargetMeta:
    sentence_indexes: tuple[int, ...] = ()
    mode: str = ""


@dataclass(frozen=True)
class EESubsectionSelectionMeta:
    explicit_labels: tuple[str, ...] = ()
    plain_numeric_ranges: tuple[tuple[str, str], ...] = ()
    label_ranges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EEItemSelectionMeta:
    explicit_labels: tuple[str, ...] = ()
    plain_numeric_ranges: tuple[tuple[str, str], ...] = ()
    label_ranges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EESubsectionTextScopeMeta:
    intro_only: bool = False


@dataclass(frozen=True)
class EESectionSelectionMeta:
    explicit_labels: tuple[str, ...] = ()
    plain_numeric_ranges: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EEPayloadRewriteMeta:
    """Typed payload-level rewrite metadata parsed from ``IRNode.attrs``."""

    rewrite: Optional[EETextRewrite] = None
    rewrite_witness: Optional[EETextRewriteWitness] = None
    persistent_postpass: bool = False


@dataclass(frozen=True)
class EEParsedInstruction:
    family: EEInstructionFamily
    action: StructuralAction
    target: LegalAddress
    source_statute_id: str
    source_title: str
    source_raw_text: str
    source_rule: str
    payload_text: Optional[str] = None
    rewrite: Optional[EETextRewrite] = None
    rewrite_witness: Optional[EETextRewriteWitness] = None
    sentence_target_meta: Optional[EESentenceTargetMeta] = None
    section_selection_meta: Optional[EESectionSelectionMeta] = None
    subsection_selection_meta: Optional[EESubsectionSelectionMeta] = None
    item_selection_meta: Optional[EEItemSelectionMeta] = None
    is_wrapper_payload: bool = False
    wrapper_source_text: Optional[str] = None
    provenance_tags: tuple[str, ...] = ()


def _normalize_paths(raw_paths: object) -> tuple[tuple[tuple[str, str], ...], ...]:
    if not raw_paths or not isinstance(raw_paths, (list, tuple)):
        return ()
    normalized: list[tuple[tuple[str, str], ...]] = []
    for item in raw_paths:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        path: list[tuple[str, str]] = []
        for p in item:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                break
            kind, label = p
            path.append((str(kind), str(label)))
        if path:
            normalized.append(tuple(path))
    return tuple(normalized)


def _to_text_replace(payload: IRNode | None) -> Optional[EETextRewrite]:
    if payload is None:
        return None
    old_surface = str(payload.attrs.get("old_text") or "").strip()
    new_surface = (payload.text or "").strip()
    if not old_surface and not new_surface:
        return None

    mode = EETextReplaceMode.replace
    if old_surface and not new_surface:
        mode = EETextReplaceMode.delete
    else:
        raw_mode = payload.attrs.get("rewrite_mode", payload.attrs.get("mode"))
        if raw_mode is not None:
            try:
                mode = EETextReplaceMode(str(raw_mode))
            except ValueError:
                mode = EETextReplaceMode.unknown
    if mode is EETextReplaceMode.replace and "mode" in payload.attrs:
        try:
            mode = EETextReplaceMode(str(payload.attrs["mode"]))
        except ValueError:
            mode = EETextReplaceMode.unknown

    return EETextRewrite(
        old_surface=old_surface,
        new_surface=new_surface,
        mode=mode,
        case_inflected=bool(payload.attrs.get("case_inflected")),
        scope_chapters=tuple(str(c) for c in payload.attrs.get("scope_chapters", ()) if c),
        exclude_paths=_normalize_paths(payload.attrs.get("exclude_paths")),
        generic_minister_plural=bool(payload.attrs.get("generic_minister_plural")),
        old_titles=tuple(str(title) for title in payload.attrs.get("old_titles", ()) if title),
        source_family=str(payload.attrs.get("source_family") or ""),
    )


def read_payload_rewrite_meta(payload: IRNode | None) -> EEPayloadRewriteMeta:
    """Return typed rewrite metadata for an IR payload."""
    if payload is None:
        return EEPayloadRewriteMeta()
    rewrite = _to_text_replace(payload)
    appendix_table_update = bool(payload.attrs.get("appendix_table_update"))
    appendix_marker = str(payload.attrs.get("appendix_marker") or "")
    appendix_table_categories = tuple(
        str(category) for category in payload.attrs.get("appendix_table_categories", ()) if category
    )
    if appendix_table_update:
        if rewrite is None:
            rewrite = EETextRewrite(
                old_surface=str(payload.attrs.get("old_text") or "").strip(),
                new_surface=str(payload.text or "").strip(),
                mode=EETextReplaceMode.replace,
                source_family="appendix_table_update",
                appendix_table_update=True,
                appendix_marker=appendix_marker,
                appendix_table_categories=appendix_table_categories,
            )
        else:
            rewrite = replace(
                rewrite,
                appendix_table_update=True,
                appendix_marker=appendix_marker,
                appendix_table_categories=appendix_table_categories,
            )
    payload_witness = payload.attrs.get("rewrite_witness")
    rewrite_witness = (
        payload_witness
        if isinstance(payload_witness, EETextRewriteWitness)
        else None
    )
    if rewrite_witness is not None and rewrite is not None:
        witness_rewrite = rewrite_witness.rewrite
        rewrite_witness = replace(
            rewrite_witness,
            rewrite=replace(
                witness_rewrite,
                old_surface=rewrite.old_surface or witness_rewrite.old_surface,
                new_surface=rewrite.new_surface if rewrite.old_surface else witness_rewrite.new_surface,
                mode=rewrite.mode if rewrite.mode is not EETextReplaceMode.unknown else witness_rewrite.mode,
                case_inflected=rewrite.case_inflected or witness_rewrite.case_inflected,
                scope_chapters=rewrite.scope_chapters or witness_rewrite.scope_chapters,
                exclude_paths=rewrite.exclude_paths or witness_rewrite.exclude_paths,
                generic_minister_plural=rewrite.generic_minister_plural or witness_rewrite.generic_minister_plural,
                old_titles=rewrite.old_titles or witness_rewrite.old_titles,
                source_family=rewrite.source_family or witness_rewrite.source_family,
            ),
        )
    return EEPayloadRewriteMeta(
        rewrite=rewrite,
        rewrite_witness=rewrite_witness,
        persistent_postpass=bool(payload.attrs.get("persistent_postpass")),
    )


def make_sentence_target_meta(
    *,
    sentence_indexes: Sequence[int],
    mode: str = "",
) -> EESentenceTargetMeta:
    normalized: list[int] = []
    for index in sentence_indexes:
        try:
            parsed = int(index)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            normalized.append(parsed)
    return EESentenceTargetMeta(sentence_indexes=tuple(normalized), mode=str(mode or ""))


def read_sentence_target_meta(payload: IRNode | None) -> Optional[EESentenceTargetMeta]:
    if payload is None:
        return None
    raw_meta = payload.attrs.get("sentence_target_meta")
    if isinstance(raw_meta, EESentenceTargetMeta):
        return raw_meta
    raw_indexes = payload.attrs.get("sentence_indexes")
    if isinstance(raw_indexes, (list, tuple)):
        return make_sentence_target_meta(
            sentence_indexes=raw_indexes,
            mode=str(payload.attrs.get("sentence_target_mode") or ""),
        )
    return None


def make_section_selection_meta(
    *,
    explicit_labels: Sequence[str],
    plain_numeric_ranges: Sequence[Sequence[str]] = (),
) -> EESectionSelectionMeta:
    normalized_ranges: list[tuple[str, str]] = []
    for raw_range in plain_numeric_ranges:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        start, end = str(raw_range[0]), str(raw_range[1])
        if start and end:
            normalized_ranges.append((start, end))
    return EESectionSelectionMeta(
        explicit_labels=tuple(str(label) for label in explicit_labels if str(label)),
        plain_numeric_ranges=tuple(normalized_ranges),
    )


def read_section_selection_meta(payload: IRNode | None) -> Optional[EESectionSelectionMeta]:
    if payload is None:
        return None
    raw_meta = payload.attrs.get("section_selection_meta")
    if isinstance(raw_meta, EESectionSelectionMeta):
        return raw_meta
    raw_labels = payload.attrs.get("section_explicit_labels")
    raw_ranges = payload.attrs.get("section_plain_numeric_ranges")
    if isinstance(raw_labels, (list, tuple)) or isinstance(raw_ranges, (list, tuple)):
        return make_section_selection_meta(
            explicit_labels=raw_labels if isinstance(raw_labels, (list, tuple)) else (),
            plain_numeric_ranges=raw_ranges if isinstance(raw_ranges, (list, tuple)) else (),
        )
    return None


def make_subsection_selection_meta(
    *,
    explicit_labels: Sequence[str],
    plain_numeric_ranges: Sequence[Sequence[str]] = (),
    label_ranges: Sequence[Sequence[str]] = (),
) -> EESubsectionSelectionMeta:
    normalized_ranges: list[tuple[str, str]] = []
    for raw_range in plain_numeric_ranges:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        start, end = str(raw_range[0]), str(raw_range[1])
        if start and end:
            normalized_ranges.append((start, end))
    normalized_label_ranges: list[tuple[str, str]] = []
    for raw_range in label_ranges:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        start, end = str(raw_range[0]), str(raw_range[1])
        if start and end:
            normalized_label_ranges.append((start, end))
    return EESubsectionSelectionMeta(
        explicit_labels=tuple(str(label) for label in explicit_labels if str(label)),
        plain_numeric_ranges=tuple(normalized_ranges),
        label_ranges=tuple(normalized_label_ranges),
    )


def read_subsection_selection_meta(payload: IRNode | None) -> Optional[EESubsectionSelectionMeta]:
    if payload is None:
        return None
    raw_meta = payload.attrs.get("subsection_selection_meta")
    if isinstance(raw_meta, EESubsectionSelectionMeta):
        return raw_meta
    raw_labels = payload.attrs.get("subsection_explicit_labels")
    raw_ranges = payload.attrs.get("subsection_plain_numeric_ranges")
    raw_label_ranges = payload.attrs.get("subsection_label_ranges")
    if (
        isinstance(raw_labels, (list, tuple))
        or isinstance(raw_ranges, (list, tuple))
        or isinstance(raw_label_ranges, (list, tuple))
    ):
        return make_subsection_selection_meta(
            explicit_labels=raw_labels if isinstance(raw_labels, (list, tuple)) else (),
            plain_numeric_ranges=raw_ranges if isinstance(raw_ranges, (list, tuple)) else (),
            label_ranges=raw_label_ranges if isinstance(raw_label_ranges, (list, tuple)) else (),
        )
    return None


def make_item_selection_meta(
    *,
    explicit_labels: Sequence[str],
    plain_numeric_ranges: Sequence[Sequence[str]] = (),
    label_ranges: Sequence[Sequence[str]] = (),
) -> EEItemSelectionMeta:
    normalized_ranges: list[tuple[str, str]] = []
    for raw_range in plain_numeric_ranges:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        start, end = str(raw_range[0]), str(raw_range[1])
        if start and end:
            normalized_ranges.append((start, end))
    normalized_label_ranges: list[tuple[str, str]] = []
    for raw_range in label_ranges:
        if not isinstance(raw_range, (list, tuple)) or len(raw_range) != 2:
            continue
        start, end = str(raw_range[0]), str(raw_range[1])
        if start and end:
            normalized_label_ranges.append((start, end))
    return EEItemSelectionMeta(
        explicit_labels=tuple(str(label) for label in explicit_labels if str(label)),
        plain_numeric_ranges=tuple(normalized_ranges),
        label_ranges=tuple(normalized_label_ranges),
    )


def read_item_selection_meta(payload: IRNode | None) -> Optional[EEItemSelectionMeta]:
    if payload is None:
        return None
    raw_meta = payload.attrs.get("item_selection_meta")
    if isinstance(raw_meta, EEItemSelectionMeta):
        return raw_meta
    raw_labels = payload.attrs.get("item_explicit_labels")
    raw_ranges = payload.attrs.get("item_plain_numeric_ranges")
    raw_label_ranges = payload.attrs.get("item_label_ranges")
    if (
        isinstance(raw_labels, (list, tuple))
        or isinstance(raw_ranges, (list, tuple))
        or isinstance(raw_label_ranges, (list, tuple))
    ):
        return make_item_selection_meta(
            explicit_labels=raw_labels if isinstance(raw_labels, (list, tuple)) else (),
            plain_numeric_ranges=raw_ranges if isinstance(raw_ranges, (list, tuple)) else (),
            label_ranges=raw_label_ranges if isinstance(raw_label_ranges, (list, tuple)) else (),
        )
    return None


def make_subsection_text_scope_meta(*, intro_only: bool = False) -> EESubsectionTextScopeMeta:
    return EESubsectionTextScopeMeta(intro_only=bool(intro_only))


def read_subsection_text_scope_meta(payload: IRNode | None) -> Optional[EESubsectionTextScopeMeta]:
    if payload is None:
        return None
    raw_meta = payload.attrs.get("subsection_text_scope_meta")
    if isinstance(raw_meta, EESubsectionTextScopeMeta):
        return raw_meta
    if "subsection_intro_only" in payload.attrs:
        return make_subsection_text_scope_meta(intro_only=bool(payload.attrs.get("subsection_intro_only")))
    return None


def make_text_rewrite_witness(
    source_text: str,
    *,
    old_surface: str = "",
    new_surface: str = "",
    mode: str | EETextReplaceMode = EETextReplaceMode.replace,
    case_inflected: bool = False,
    scope_chapters: Sequence[str] = (),
    exclude_paths: Sequence[Sequence[tuple[str, str]]] = (),
    generic_minister_plural: bool = False,
    old_titles: Sequence[str] = (),
    source_family: str = "",
    appendix_table_update: bool = False,
    appendix_marker: str = "",
    appendix_table_categories: Sequence[str] = (),
) -> EETextRewriteWitness:
    if isinstance(mode, EETextReplaceMode):
        rewrite_mode = mode
    else:
        try:
            rewrite_mode = EETextReplaceMode(str(mode))
        except ValueError:
            rewrite_mode = EETextReplaceMode.unknown
    rewrite = EETextRewrite(
        old_surface=old_surface,
        new_surface=new_surface,
        mode=rewrite_mode,
        case_inflected=case_inflected,
        scope_chapters=tuple(str(c) for c in scope_chapters if c),
        exclude_paths=tuple(
            tuple((str(kind), str(label)) for kind, label in path)
            for path in exclude_paths
            if path
        ),
        generic_minister_plural=generic_minister_plural,
        old_titles=tuple(str(title) for title in old_titles if title),
        source_family=source_family,
        appendix_table_update=appendix_table_update,
        appendix_marker=appendix_marker,
        appendix_table_categories=tuple(str(category) for category in appendix_table_categories if category),
    )
    return EETextRewriteWitness(source_text=source_text, rewrite=rewrite)


def _instruction_family(action: StructuralAction, wrapper: bool) -> EEInstructionFamily:
    if wrapper:
        return EEInstructionFamily.wrapper_quoted_payload
    if action.value in ("text_replace", "text_repeal"):
        return EEInstructionFamily.text_replace
    if action in (StructuralAction.REPLACE, StructuralAction.INSERT, StructuralAction.REPEAL, StructuralAction.RENUMBER):
        return EEInstructionFamily.structural
    return EEInstructionFamily.other


def to_ee_parsed_instructions(
    ops: Sequence[LegalOperation],
    *,
    source_rule: str = "estonia/peg:extract_ee_ops",
    wrapper_source_text: Optional[str] = None,
) -> list[EEParsedInstruction]:
    """Convert parser-local `LegalOperation` into typed parsed instructions."""
    instructions: list[EEParsedInstruction] = []
    for op in ops:
        source = op.source or OperationSource(statute_id="ee/unknown", title="", raw_text="")
        is_wrapper = wrapper_source_text is not None
        payload_meta = read_payload_rewrite_meta(op.payload)
        text_rewrite = payload_meta.rewrite if op.action.value in ("text_replace", "text_repeal") else None
        rewrite_witness = payload_meta.rewrite_witness
        sentence_target_meta = read_sentence_target_meta(op.payload)
        section_selection_meta = read_section_selection_meta(op.payload)
        subsection_selection_meta = read_subsection_selection_meta(op.payload)
        item_selection_meta = read_item_selection_meta(op.payload)
        instructions.append(
            EEParsedInstruction(
                family=_instruction_family(op.action, is_wrapper),
                action=op.action,
                target=op.target,
                source_statute_id=source.statute_id,
                source_title=source.title,
                source_raw_text=source.raw_text,
                source_rule=source_rule,
                payload_text=op.payload.text if op.payload is not None else None,
                rewrite=text_rewrite,
                rewrite_witness=rewrite_witness,
                sentence_target_meta=sentence_target_meta,
                section_selection_meta=section_selection_meta,
                subsection_selection_meta=subsection_selection_meta,
                item_selection_meta=item_selection_meta,
                is_wrapper_payload=is_wrapper,
                wrapper_source_text=wrapper_source_text,
                provenance_tags=tuple(op.provenance_tags),
            )
        )
    return instructions


def parse_wrapper_quoted_clause(
    clause_text: str,
    source: OperationSource,
    *,
    source_rule: str = "estonia/peg:wrapper-quoted-clause",
) -> list[EEParsedInstruction]:
    """Parse a quoted inner clause as nested instructions with wrapper evidence."""
    if not clause_text:
        return []
    nested_ops = extract_ee_ops(clause_text.strip(), source)
    return to_ee_parsed_instructions(
        nested_ops,
        source_rule=source_rule,
        wrapper_source_text=clause_text.strip(),
    )


__all__ = [
    "EEInstructionFamily",
    "EEItemSelectionMeta",
    "EEParsedInstruction",
    "EEPayloadRewriteMeta",
    "EESectionSelectionMeta",
    "EESubsectionSelectionMeta",
    "EESentenceTargetMeta",
    "EETextRewrite",
    "EETextRewriteWitness",
    "EETextReplaceMode",
    "make_item_selection_meta",
    "make_section_selection_meta",
    "make_subsection_selection_meta",
    "make_subsection_text_scope_meta",
    "make_sentence_target_meta",
    "make_text_rewrite_witness",
    "read_item_selection_meta",
    "read_payload_rewrite_meta",
    "read_section_selection_meta",
    "read_subsection_selection_meta",
    "read_subsection_text_scope_meta",
    "read_sentence_target_meta",
    "to_ee_parsed_instructions",
    "parse_wrapper_quoted_clause",
]
