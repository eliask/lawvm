"""Finland amendment source model.

This module is the first IR-frontier boundary for amendment bodies.  It keeps
the parsed XML root as the acquisition witness, but owns the derived body
indexes and payload surfaces so later compiler phases do not independently
walk and reinterpret the same XML tree.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

import lxml.etree as etree

from lawvm.core.coverage import CoverageIgnoredUnit, CoverageUnit
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_witness import DigestWitness
from lawvm.finland.body_coverage import BodyCoveragePayloadRef, extract_body_coverage
from lawvm.finland.body_pairing import (
    ObservedBodyUnit,
    _body_with_orphan_subsections_attached,
    _part_label_from_cross_heading,
    build_observed_body_inventory,
)
from lawvm.finland.helpers import (
    _normalize_source_part_num,
    _normalize_source_section_num,
    _norm_num_token,
)

if TYPE_CHECKING:
    from lawvm.core.compile_result import StrictProfile
    from lawvm.core.phase_result import PhaseResult
    from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
    from lawvm.finland.metadata import SeparateCommencementLawWitness
    from lawvm.finland.amendment_chapter_precreate import (
        PrecreateApplyChaptersResult,
        PrecreatedChaptersResult,
        SourceChapter,
        SourcePseudoChapter,
    )
    from lawvm.finland.frontend_compile import _AmendmentTreeMetadata
    from lawvm.finland.ops import AmendmentOp, ResolvedOp
    from lawvm.finland.statute import ReplayState
    from lawvm.finland.uncovered_recovery_context import UncoveredRecoveryContext
    from lawvm.finland.vts import VtsSkippedTarget


@dataclass(frozen=True, slots=True)
class SourceUnitLookup:
    """A normalized source-body lookup key."""

    unit_kind: str
    label: str
    chapter: Optional[str] = None
    part: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SourceBodyUnitQuery:
    """A typed source-body unit query over the observed source inventory."""

    unit_kind: str
    label: str
    chapter: str | None = None
    part: str | None = None


@dataclass(frozen=True, slots=True)
class SourceBodyLookupResult:
    """Typed verdict for a source-body inventory lookup."""

    status: Literal["unique", "missing", "ambiguous"]
    query: SourceBodyUnitQuery
    candidates: tuple[ObservedBodyUnit, ...]

    @property
    def unique_unit(self) -> ObservedBodyUnit | None:
        if self.status != "unique":
            return None
        return self.candidates[0]


@dataclass(frozen=True, slots=True)
class SourcePayloadLookupResult:
    """Typed payload lookup verdict for a source-body target."""

    status: Literal["unique", "missing", "ambiguous"]
    query: SourceBodyUnitQuery
    body_lookup_status: Literal["unique", "missing", "ambiguous"]
    body_candidates: tuple[ObservedBodyUnit, ...]
    payload_basis: Literal["body_inventory", "coverage_payload_ref", "none"]
    payload_ir: IRNode | None
    cross_heading_ir: IRNode | None


@dataclass(frozen=True, slots=True)
class SourcePayloadTextLookupResult:
    """Typed text lookup verdict for source payload text consumers."""

    status: Literal["unique", "missing", "ambiguous"]
    query: SourceBodyUnitQuery
    payload_lookup_status: Literal["unique", "missing", "ambiguous"]
    payload_basis: Literal["body_inventory", "coverage_payload_ref", "none"]
    text: str


@dataclass(slots=True)
class _SourcePayloadIrEntry:
    """Lazy converted source payload for one source-body unit id."""

    muutos_tree: etree._Element
    kind: str
    label: str
    el: etree._Element
    _payload: tuple[IRNode | None, IRNode | None] | None = None

    def resolve(self) -> tuple[IRNode | None, IRNode | None]:
        if self._payload is None:
            from lawvm.finland.amendment_payload_lookup import (
                _find_muutos_ir,
                _payload_ir_from_muutos_node,
            )

            if self.kind == "chapter" and (
                _xml_localname(self.el) != "chapter" or _chapter_contains_pseudo_markers(self.el)
            ):
                self._payload = _find_muutos_ir(
                    self.muutos_tree,
                    target_unit_kind=self.kind,
                    target_norm=self.label,
                )
                if self._payload[0] is None and _xml_localname(self.el) == "section":
                    marker_ir, cross_ir = _payload_ir_from_muutos_node(
                        self.el,
                        target_unit_kind="section",
                        target_norm=self.label,
                    )
                    marker_children = (
                        tuple(
                            child
                            for child in marker_ir.children
                            if child.kind in {IRNodeKind.NUM, IRNodeKind.HEADING}
                        )
                        if marker_ir is not None
                        else ()
                    )
                    if marker_children:
                        self._payload = (
                            IRNode(
                                kind=IRNodeKind.CHAPTER,
                                label=self.label,
                                children=marker_children,
                            ),
                            cross_ir,
                        )
            else:
                self._payload = _payload_ir_from_muutos_node(
                    self.el,
                    target_unit_kind=self.kind,
                    target_norm=self.label,
                )
        return self._payload


@dataclass(frozen=True, slots=True)
class SourcePayloadIrIndex:
    """Converted source payloads addressable by current transitional unit ids."""

    observed_entries_by_unit_id: dict[str, _SourcePayloadIrEntry]
    coverage_entries_by_unit_id: dict[str, _SourcePayloadIrEntry]

    def observed_payload(self, unit_id: str) -> tuple[IRNode | None, IRNode | None]:
        entry = self.observed_entries_by_unit_id.get(unit_id)
        return entry.resolve() if entry is not None else (None, None)

    def coverage_payload(self, unit_id: str) -> tuple[IRNode | None, IRNode | None]:
        entry = self.coverage_entries_by_unit_id.get(unit_id)
        return entry.resolve() if entry is not None else (None, None)

    @property
    def observed_by_unit_id(self) -> dict[str, tuple[IRNode | None, IRNode | None]]:
        """Compatibility view for callers that still expect eager payload maps."""
        return {
            unit_id: entry.resolve()
            for unit_id, entry in self.observed_entries_by_unit_id.items()
        }

    @property
    def coverage_by_unit_id(self) -> dict[str, tuple[IRNode | None, IRNode | None]]:
        """Compatibility view for callers that still expect eager payload maps."""
        return {
            unit_id: entry.resolve()
            for unit_id, entry in self.coverage_entries_by_unit_id.items()
        }


@dataclass(frozen=True, slots=True)
class SourceMetadataSurface:
    """Model-owned source metadata facts derived during the XML adapter phase."""

    source_issue_date: dt.date | None
    source_title: str
    effective_date: dt.date | None
    effective_date_step: str
    expiry_date: dt.date | None


@dataclass(frozen=True, slots=True)
class SourceBodyInventoryIndex:
    """Normalized indexes over observed source-body units."""

    units_by_lookup_key: dict[
        tuple[str, str, str | None, str | None],
        tuple[ObservedBodyUnit, ...],
    ]
    section_scopes_by_label: dict[str, frozenset[tuple[str | None, str | None]]]
    section_parts_by_label: dict[str, frozenset[str | None]]
    section_chapters_by_label: dict[str, frozenset[str]]
    section_chapters_by_label_part: dict[tuple[str, str | None], frozenset[str]]
    first_section_chapter_by_label: dict[str, str]
    pseudo_chapter_labels: frozenset[str]
    real_chapter_labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class SourceBodySectionWrapperIndex:
    """XML-wrapper section scope facts owned by the source-model adapter."""

    section_scopes_by_label: dict[str, frozenset[tuple[str | None, str | None]]]
    section_parts_by_label: dict[str, frozenset[str | None]]
    section_chapters_by_label: dict[str, frozenset[str]]
    section_chapters_by_label_part: dict[tuple[str, str | None], frozenset[str]]


def _xml_localname(el: etree._Element) -> str:
    tag = el.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _xml_num_text(el: etree._Element) -> str | None:
    num_el = el.find("{*}num")
    if num_el is None:
        num_el = el.find("num")
    if num_el is None or not num_el.text:
        return None
    return num_el.text.strip()


def _is_pseudo_chapter_marker(el: etree._Element) -> bool:
    """Return true for section-shaped source markers such as ``16 b luku``."""
    return _xml_localname(el) == "section" and bool(
        _norm_num_token(_xml_num_text(el) or "").endswith("luku")
    )


def _chapter_contains_pseudo_markers(el: etree._Element) -> bool:
    return any(_is_pseudo_chapter_marker(child) for child in el)


def _source_body_inventory_index(
    inventory: tuple[ObservedBodyUnit, ...],
) -> SourceBodyInventoryIndex:
    """Build normalized lookup indexes without collapsing ambiguous units."""
    by_key_lists: dict[
        tuple[str, str, str | None, str | None],
        list[ObservedBodyUnit],
    ] = {}
    scope_sets: dict[str, set[tuple[str | None, str | None]]] = {}
    part_sets: dict[str, set[str | None]] = {}
    chapter_sets: dict[str, set[str]] = {}
    chapter_sets_by_part: dict[tuple[str, str | None], set[str]] = {}
    first_chapter: dict[str, str] = {}
    pseudo_chapters: set[str] = set()
    real_chapters: set[str] = set()

    for unit in inventory:
        kind = str(unit.kind or "")
        label = _norm_num_token(unit.label)
        chapter = _norm_num_token(unit.chapter_label) if unit.chapter_label else None
        part = _norm_num_token(unit.part_label) if unit.part_label else None
        keys = {
            (kind, label, None, None),
            (kind, label, chapter, None),
            (kind, label, None, part),
            (kind, label, chapter, part),
        }
        for key in keys:
            by_key_lists.setdefault(key, []).append(unit)

        if kind == "section":
            scope_sets.setdefault(label, set()).add((part, chapter))
            part_sets.setdefault(label, set()).add(part)
            if chapter is not None:
                chapter_sets.setdefault(label, set()).add(chapter)
                chapter_sets_by_part.setdefault((label, part), set()).add(chapter)
            if chapter is not None and label not in first_chapter:
                first_chapter[label] = unit.chapter_label
        elif kind == "chapter":
            if unit.source_tag == "section":
                pseudo_chapters.add(label)
            elif unit.source_tag == "chapter":
                real_chapters.add(label)

    return SourceBodyInventoryIndex(
        units_by_lookup_key={key: tuple(units) for key, units in by_key_lists.items()},
        section_scopes_by_label={
            label: frozenset(scopes) for label, scopes in scope_sets.items()
        },
        section_parts_by_label={
            label: frozenset(parts) for label, parts in part_sets.items()
        },
        section_chapters_by_label={
            label: frozenset(chapters) for label, chapters in chapter_sets.items()
        },
        section_chapters_by_label_part={
            key: frozenset(chapters)
            for key, chapters in chapter_sets_by_part.items()
        },
        first_section_chapter_by_label=first_chapter,
        pseudo_chapter_labels=frozenset(pseudo_chapters),
        real_chapter_labels=frozenset(real_chapters),
    )


def _source_body_section_wrapper_index(
    muutos_tree: etree._Element,
) -> SourceBodySectionWrapperIndex:
    """Build source-body section scope facts from XML wrapper ancestry once."""
    body = muutos_tree if _xml_localname(muutos_tree) == "body" else muutos_tree.find(".//{*}body")
    if body is None:
        body = muutos_tree.find(".//body")
    if body is None:
        return SourceBodySectionWrapperIndex(
            section_scopes_by_label={},
            section_parts_by_label={},
            section_chapters_by_label={},
            section_chapters_by_label_part={},
        )

    def _part_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if _xml_localname(parent) == "part":
                part_num = _xml_num_text(parent)
                if not part_num:
                    return None
                return _normalize_source_part_num(part_num) or None
            parent = parent.getparent()
        return None

    def _chapter_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if _xml_localname(parent) == "chapter":
                chapter_num = _xml_num_text(parent)
                if not chapter_num:
                    return None
                return _norm_num_token(chapter_num).removesuffix("luku") or None
            parent = parent.getparent()
        return None

    scope_sets: dict[str, set[tuple[str | None, str | None]]] = {}
    part_sets: dict[str, set[str | None]] = {}
    chapter_sets: dict[str, set[str]] = {}
    chapter_sets_by_part: dict[tuple[str, str | None], set[str]] = {}
    for section in body.findall(".//{*}section"):
        raw_num = _xml_num_text(section)
        if not raw_num:
            continue
        section_label = _normalize_source_section_num(raw_num)
        if not section_label:
            continue
        part = _part_label_for_element(section)
        chapter = _chapter_label_for_element(section)
        scope_sets.setdefault(section_label, set()).add((part, chapter))
        part_sets.setdefault(section_label, set()).add(part)
        if chapter is not None:
            chapter_sets.setdefault(section_label, set()).add(chapter)
            chapter_sets_by_part.setdefault((section_label, part), set()).add(chapter)

    return SourceBodySectionWrapperIndex(
        section_scopes_by_label={
            label: frozenset(scopes) for label, scopes in scope_sets.items()
        },
        section_parts_by_label={
            label: frozenset(parts) for label, parts in part_sets.items()
        },
        section_chapters_by_label={
            label: frozenset(chapters) for label, chapters in chapter_sets.items()
        },
        section_chapters_by_label_part={
            key: frozenset(chapters)
            for key, chapters in chapter_sets_by_part.items()
        },
    )


def _source_payload_ir_index(muutos_tree: etree._Element) -> SourcePayloadIrIndex:
    """Return converted payload IR keyed by observed and coverage unit ids."""
    body = muutos_tree if _xml_localname(muutos_tree) == "body" else muutos_tree.find(".//{*}body")
    if body is None:
        body = muutos_tree.find(".//body")
    if body is None:
        return SourcePayloadIrIndex(observed_entries_by_unit_id={}, coverage_entries_by_unit_id={})
    body = _body_with_orphan_subsections_attached(body)
    observed_payloads: dict[str, _SourcePayloadIrEntry] = {}
    coverage_payloads: dict[str, _SourcePayloadIrEntry] = {}
    seen_observed_ids: set[str] = set()
    seen_coverage_ids: set[str] = set()

    def next_observed_id(kind: str, label: str, chapter_label: str) -> str:
        base_id = f"{kind}:{chapter_label}/{label}" if chapter_label else f"{kind}:{label}"
        unit_id = base_id
        counter = 1
        while unit_id in seen_observed_ids:
            unit_id = f"{base_id}#{counter}"
            counter += 1
        seen_observed_ids.add(unit_id)
        return unit_id

    def next_coverage_id(kind: str, label: str, chapter_label: str | None) -> str:
        base_id = f"{kind}_{label}"
        if chapter_label:
            base_id = f"{kind}_{chapter_label}_{label}"
        unit_id = base_id
        counter = 1
        while unit_id in seen_coverage_ids:
            unit_id = f"{base_id}_{counter}"
            counter += 1
        seen_coverage_ids.add(unit_id)
        return unit_id

    def append_payload(
        kind: str,
        label: str,
        chapter_label: str,
        el: etree._Element,
        *,
        include_observed: bool = True,
        include_coverage: bool = True,
    ) -> None:
        payload = _SourcePayloadIrEntry(
            muutos_tree=muutos_tree,
            kind=kind,
            label=label,
            el=el,
        )
        if include_observed:
            observed_payloads[next_observed_id(kind, label, chapter_label)] = payload
        if include_coverage:
            coverage_payloads[next_coverage_id(kind, label, chapter_label or None)] = payload

    def walk_children(parent: etree._Element, active_chapter: str = "") -> None:
        current_chapter = active_chapter
        for child in parent:
            kind = _xml_localname(child)
            if kind == "crossHeading":
                part_label = _part_label_from_cross_heading(child)
                if part_label:
                    append_payload(
                        "part",
                        part_label,
                        "",
                        child,
                        include_coverage=False,
                    )
                    current_chapter = ""
                    continue
            if kind == "part":
                raw_num = _xml_num_text(child)
                if raw_num:
                    part_label = _normalize_source_part_num(raw_num)
                    if part_label:
                        append_payload("part", part_label, "", child, include_coverage=False)
                        walk_children(child, active_chapter="")
                        current_chapter = active_chapter
                        continue
            if kind == "chapter":
                raw_num = _xml_num_text(child)
                if raw_num:
                    chapter_label = _norm_num_token(raw_num).removesuffix("luku")
                    if chapter_label:
                        append_payload("chapter", chapter_label, "", child)
                        walk_children(child, chapter_label)
                        current_chapter = active_chapter
                        continue
            if kind == "section":
                raw_num = _xml_num_text(child)
                if raw_num:
                    if _norm_num_token(raw_num).endswith("luku"):
                        pseudo_chapter = _norm_num_token(raw_num).removesuffix("luku")
                        if pseudo_chapter:
                            append_payload("chapter", pseudo_chapter, "", child)
                            walk_children(child, pseudo_chapter)
                            current_chapter = pseudo_chapter
                            continue
                    section_label = _normalize_source_section_num(raw_num)
                    if section_label:
                        append_payload("section", section_label, current_chapter, child)
                        walk_children(child, current_chapter)
                        continue
            if kind == "article":
                raw_num = _xml_num_text(child)
                if raw_num:
                    article_label = _norm_num_token(raw_num)
                    if article_label:
                        append_payload(
                            "article",
                            article_label,
                            current_chapter,
                            child,
                            include_observed=False,
                        )
            walk_children(child, current_chapter)

    walk_children(body)
    return SourcePayloadIrIndex(
        observed_entries_by_unit_id=observed_payloads,
        coverage_entries_by_unit_id=coverage_payloads,
    )


def content_digest_witness(source_bytes: bytes) -> DigestWitness:
    """Return the content-addressed sha256 ``DigestWitness`` for source bytes.

    The digest is computed from the actual artifact bytes — never from a name,
    id, or locator — so two acquisitions agree iff their bytes agree.
    """
    if not isinstance(source_bytes, bytes | bytearray):
        raise TypeError(
            "content_digest_witness requires source bytes, "
            f"got {type(source_bytes).__name__}"
        )
    digest = hashlib.sha256(bytes(source_bytes)).hexdigest()
    if not digest:  # pragma: no cover - hashlib always yields a hex digest
        raise ValueError("content digest could not be computed for source bytes")
    return DigestWitness(digest_algorithm="sha256", digest=digest)


@dataclass(slots=True)
class AmendmentSourceModel:
    """Cached read-only projections over one Finland amendment source tree.

    ``source_digest`` is the intrinsic content identity of ``source_bytes``
    (sha256 of the actual artifact bytes, never derived from ``source_ref`` or
    any name/id). It is bound at construction and is present whenever the carried
    bytes are present, so downstream witnesses can prove identity from a content
    hash rather than reconstructing it from a name.

    ``pre_correction_digest`` is the content digest of the bytes *before*
    ``_apply_source_corrections`` ran, set only when a correction actually
    changed the bytes. Together with ``source_digest`` it forms a pre/post pair
    so that a source correction is itself witnessed.
    """

    muutos_tree: etree._Element
    source_ref: str = ""
    source_bytes: bytes | None = None
    source_digest: DigestWitness | None = None
    pre_correction_digest: DigestWitness | None = None

    def __post_init__(self) -> None:
        # Intrinsic content identity: present whenever bytes are present.
        # content_digest_witness fails loud if a digest cannot be computed.
        if self.source_bytes is not None and self.source_digest is None:
            self.source_digest = content_digest_witness(self.source_bytes)
    _observed_body_inventory: tuple[ObservedBodyUnit, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _body_inventory_index_cache: SourceBodyInventoryIndex | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _body_section_wrapper_index_cache: SourceBodySectionWrapperIndex | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _coverage_units: tuple[CoverageUnit, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _coverage_ignored_units: tuple[CoverageIgnoredUnit, ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _coverage_units_by_payload_ref_id_cache: dict[str, tuple[CoverageUnit, ...]] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _source_payload_ir_index_cache: SourcePayloadIrIndex | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _payload_ir_cache: dict[SourceUnitLookup, SourcePayloadLookupResult] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _source_chapters_cache: tuple["SourceChapter", ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _source_pseudo_chapters_cache: tuple["SourcePseudoChapter", ...] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _amendment_metadata_cache: dict[str, "_AmendmentTreeMetadata"] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _metadata_surface_cache: SourceMetadataSurface | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _text_cache: str | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _preamble_text_cache: str | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _text_lower_cache: str | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _source_xml_bytes_cache: bytes | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _operative_body_repeal_candidate_cache: str | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _has_body_cache: bool | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _has_eid_free_body_sections_cache: bool | None = field(
        default=None,
        init=False,
        repr=False,
    )

    @classmethod
    def from_tree(
        cls,
        muutos_tree: etree._Element,
        *,
        source_ref: str = "",
        source_bytes: bytes | None = None,
        pre_correction_bytes: bytes | None = None,
    ) -> "AmendmentSourceModel":
        """Build a source model and bind intrinsic content digests.

        ``source_bytes`` are the post-correction artifact bytes; their sha256
        becomes ``source_digest``. ``pre_correction_bytes`` are the bytes as
        acquired *before* ``_apply_source_corrections``; when they differ from
        ``source_bytes`` their digest is bound as ``pre_correction_digest`` so a
        correction is witnessed as a content change, not just a name.
        """
        pre_correction_digest: DigestWitness | None = None
        if (
            pre_correction_bytes is not None
            and source_bytes is not None
            and pre_correction_bytes != source_bytes
        ):
            pre_correction_digest = content_digest_witness(pre_correction_bytes)
        return cls(
            muutos_tree=muutos_tree,
            source_ref=source_ref,
            source_bytes=source_bytes,
            pre_correction_digest=pre_correction_digest,
        )

    @property
    def has_body(self) -> bool:
        if self._has_body_cache is None:
            self._has_body_cache = self.muutos_tree.find(".//{*}body") is not None
        return self._has_body_cache

    def has_eid_free_body_sections(self) -> bool:
        """Return True when the source body has sections and none carry eIds."""
        if self._has_eid_free_body_sections_cache is None:
            sections = self.muutos_tree.findall(".//{*}section")
            self._has_eid_free_body_sections_cache = bool(sections) and not any(
                section.get("eId") for section in sections
            )
        return self._has_eid_free_body_sections_cache

    def observed_body_inventory(self) -> tuple[ObservedBodyUnit, ...]:
        """Return cached body-pairing inventory units."""
        if self._observed_body_inventory is None:
            self._observed_body_inventory = tuple(
                build_observed_body_inventory(self.muutos_tree)
            )
        return self._observed_body_inventory

    def _body_inventory_index(self) -> SourceBodyInventoryIndex:
        if self._body_inventory_index_cache is None:
            self._body_inventory_index_cache = _source_body_inventory_index(
                self.observed_body_inventory()
            )
        return self._body_inventory_index_cache

    def _body_section_wrapper_index(self) -> SourceBodySectionWrapperIndex:
        if self._body_section_wrapper_index_cache is None:
            self._body_section_wrapper_index_cache = _source_body_section_wrapper_index(
                self.muutos_tree
            )
        return self._body_section_wrapper_index_cache

    def body_coverage_units(
        self,
        *,
        ignored_units_out: list[CoverageIgnoredUnit] | None = None,
    ) -> tuple[CoverageUnit, ...]:
        """Return cached operative body coverage units.

        ``ignored_units_out`` preserves the legacy API's side channel while the
        cached model keeps the canonical ignored-unit tuple.
        """
        if self._coverage_units is None or self._coverage_ignored_units is None:
            ignored_units: list[CoverageIgnoredUnit] = []
            self._coverage_units = tuple(
                extract_body_coverage(
                    self.muutos_tree,
                    ignored_units_out=ignored_units,
                )
            )
            self._coverage_ignored_units = tuple(ignored_units)
        if ignored_units_out is not None:
            ignored_units_out.extend(self._coverage_ignored_units)
        return self._coverage_units

    def _coverage_units_by_payload_ref_id(self) -> dict[str, tuple[CoverageUnit, ...]]:
        if self._coverage_units_by_payload_ref_id_cache is None:
            grouped: dict[str, list[CoverageUnit]] = {}
            for unit in self.body_coverage_units():
                payload_ref = unit.payload_ref
                if isinstance(payload_ref, BodyCoveragePayloadRef):
                    grouped.setdefault(payload_ref.unit_id, []).append(unit)
            self._coverage_units_by_payload_ref_id_cache = {
                unit_id: tuple(units) for unit_id, units in grouped.items()
            }
        return self._coverage_units_by_payload_ref_id_cache

    def body_section_scope(
        self,
        target_norm: str,
    ) -> tuple[str | None, str | None] | None:
        """Return the unique observed body (part, chapter) scope for a section."""
        wanted = _norm_num_token(target_norm)
        scopes = self._body_inventory_index().section_scopes_by_label.get(
            wanted,
            frozenset(),
        )
        if len(scopes) != 1:
            return None
        return next(iter(scopes))

    def body_section_chapter(self, target_norm: str) -> str | None:
        """Return the observed body chapter label for a section, if any."""
        scope = self.body_section_scope(target_norm)
        if scope is None:
            return None
        _part, chapter = scope
        return chapter

    def first_body_section_chapter(self, target_norm: str) -> str | None:
        """Return the first observed chapter containing a source body section."""
        wanted = _norm_num_token(target_norm)
        return self._body_inventory_index().first_section_chapter_by_label.get(wanted)

    def body_has_pseudo_chapter_marker(self, chapter_label: str) -> bool:
        """Return True if the observed body has a section-shaped chapter marker."""
        wanted = _norm_num_token(chapter_label)
        return wanted in self._body_inventory_index().pseudo_chapter_labels

    def body_has_real_chapter_container(self, chapter_label: str) -> bool:
        """Return True if the observed body has a real chapter container."""
        wanted = _norm_num_token(chapter_label)
        return wanted in self._body_inventory_index().real_chapter_labels

    def body_real_chapter_section_labels(self, chapter_label: str) -> tuple[str, ...]:
        """Return section labels observed under one real source chapter wrapper."""
        wanted = _norm_num_token(chapter_label)
        if wanted not in self._body_inventory_index().real_chapter_labels:
            return ()
        labels: list[str] = []
        for unit in self.observed_body_inventory():
            if unit.kind != "section":
                continue
            if _norm_num_token(unit.chapter_label or "") != wanted:
                continue
            labels.append(_norm_num_token(unit.label))
        return tuple(labels)

    def body_chapter_is_single_mixed_wrapper(
        self,
        chapter_label: str,
        master: "ReplayState",
    ) -> bool:
        """Return True when one source chapter wrapper contains multiple live chapters.

        Some historical amendment XML opens one chapter wrapper and then leaves
        unrelated later sections inside it.  The wrapper is then a source
        topology defect, not reliable chapter-scope evidence for every
        contained section.
        """
        body_chapter_norm = _norm_num_token(chapter_label)
        real_chapter_labels = {
            _norm_num_token(unit.label)
            for unit in self.observed_body_inventory()
            if unit.kind == "chapter" and unit.source_tag == "chapter"
        }
        if real_chapter_labels != {body_chapter_norm}:
            return False

        foreign_live_chapters: set[str] = set()
        for unit in self.observed_body_inventory():
            if (
                unit.kind != "section"
                or _norm_num_token(unit.chapter_label) != body_chapter_norm
            ):
                continue
            section_label = _norm_num_token(unit.label)
            section_path = master.find_section_path(
                section_label,
                None,
                unit.part_label or None,
            )
            if section_path is None:
                stem_match = re.fullmatch(r"(\d+)[a-z]+", section_label, re.I)
                if stem_match is not None:
                    section_path = master.find_section_path(
                        stem_match.group(1),
                        None,
                        unit.part_label or None,
                    )
            if section_path is None:
                continue
            live_chapter = next(
                (label for kind, label in section_path if kind == "chapter"),
                "",
            )
            if live_chapter and _norm_num_token(live_chapter) != body_chapter_norm:
                foreign_live_chapters.add(_norm_num_token(live_chapter))
        return len(foreign_live_chapters) >= 2

    def lookup_body_unit(
        self,
        target_unit_kind: str,
        target_norm: str,
        *,
        target_chapter: str | None = None,
        target_part: str | None = None,
    ) -> SourceBodyLookupResult:
        """Return a typed source-body inventory lookup verdict."""
        query = SourceBodyUnitQuery(
            unit_kind=str(target_unit_kind or ""),
            label=_norm_num_token(target_norm),
            chapter=_norm_num_token(target_chapter or "") if target_chapter else None,
            part=_norm_num_token(target_part or "") if target_part else None,
        )
        candidates = self._body_inventory_index().units_by_lookup_key.get(
            (query.unit_kind, query.label, query.chapter, query.part),
            (),
        )
        if not candidates:
            status: Literal["unique", "missing", "ambiguous"] = "missing"
        elif len(candidates) == 1:
            status = "unique"
        else:
            status = "ambiguous"
        return SourceBodyLookupResult(
            status=status,
            query=query,
            candidates=candidates,
        )

    def body_has_section(
        self,
        target_norm: str,
        *,
        target_chapter: str | None = None,
        target_part: str | None = None,
    ) -> bool:
        """Return True if the observed body has a section in the requested scope."""
        return (
            self.lookup_body_unit(
                "section",
                target_norm,
                target_chapter=target_chapter,
                target_part=target_part,
            ).status
            != "missing"
        )

    def body_carries_whole_section(
        self,
        target_norm: str,
        *,
        target_part: str | None = None,
    ) -> bool:
        """Return True when the observed body carries a section in any chapter."""
        wanted = _norm_num_token(target_norm)
        part = _norm_num_token(target_part) if target_part else None
        return part in self._body_section_wrapper_index().section_parts_by_label.get(
            wanted,
            frozenset(),
        )

    def body_section_wrapper_scope(
        self,
        target_norm: str,
    ) -> tuple[str | None, str | None] | None:
        """Return the unique XML-wrapper body scope for a section."""
        wanted = _norm_num_token(target_norm)
        scopes = self._body_section_wrapper_index().section_scopes_by_label.get(
            wanted,
            frozenset(),
        )
        if len(scopes) != 1:
            return None
        return next(iter(scopes))

    def unique_body_section_chapter(
        self,
        target_norm: str,
        *,
        target_part: str | None = None,
    ) -> str | None:
        """Return the unique chapter wrapper for an observed body section."""
        wanted = _norm_num_token(target_norm)
        index = self._body_section_wrapper_index()
        if target_part is None:
            chapters = index.section_chapters_by_label.get(wanted, frozenset())
        else:
            part = _norm_num_token(target_part)
            chapters = index.section_chapters_by_label_part.get(
                (wanted, part),
                frozenset(),
            )
        if len(chapters) != 1:
            return None
        return next(iter(chapters)) or None

    def body_section_lookup(
        self,
        target_norm: str,
        *,
        target_chapter: str | None = None,
        target_part: str | None = None,
    ) -> SourceBodyLookupResult:
        """Return a typed source-body lookup for a section label."""
        return self.lookup_body_unit(
            "section",
            target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )

    def has_single_unlabeled_section_payload(self) -> bool:
        """Return True for the legacy single unlabeled section payload fact.

        This preserves the old source-node fallback as a typed model fact:
        if the body contains exactly one section candidate and that candidate
        has no usable number, constraints should treat the body as having a
        payload rather than rejecting the operation as a cross-reference.
        """
        ignored_units: list[CoverageIgnoredUnit] = []
        coverage_units = self.body_coverage_units(ignored_units_out=ignored_units)
        labeled_section_count = sum(1 for unit in coverage_units if unit.kind == "section")
        ignored_section_units = tuple(
            unit
            for unit in ignored_units
            if unit.unit_kind == "section"
            and unit.reason in {"missing_num", "unusable_num", "pseudo_chapter_marker_unusable"}
        )
        return (
            labeled_section_count == 0
            and len(ignored_section_units) == 1
            and ignored_section_units[0].reason == "missing_num"
        )

    def _source_payload_ir_index(self) -> SourcePayloadIrIndex:
        if self._source_payload_ir_index_cache is None:
            self._source_payload_ir_index_cache = _source_payload_ir_index(self.muutos_tree)
        return self._source_payload_ir_index_cache

    def has_source_node(
        self,
        target_unit_kind: TargetUnitKind | str,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> bool:
        """Return whether the source body contains the normalized target."""
        lookup = self.lookup_body_unit(
            str(target_unit_kind or ""),
            target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )
        if lookup.status != "missing":
            return True
        return str(target_unit_kind or "") == "section" and self.has_single_unlabeled_section_payload()

    def lookup_payload_ir(
        self,
        target_unit_kind: TargetUnitKind | str,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> SourcePayloadLookupResult:
        """Return cached typed payload lookup for a source-body target."""
        key = SourceUnitLookup(
            unit_kind=str(target_unit_kind or ""),
            label=_norm_num_token(target_norm),
            chapter=_norm_num_token(target_chapter or "") if target_chapter else None,
            part=_norm_num_token(target_part or "") if target_part else None,
        )
        if key not in self._payload_ir_cache:
            body_lookup = self.lookup_body_unit(
                key.unit_kind,
                key.label,
                target_chapter=key.chapter,
                target_part=key.part,
            )
            if body_lookup.status != "unique":
                self._payload_ir_cache[key] = SourcePayloadLookupResult(
                    status=body_lookup.status,
                    query=body_lookup.query,
                    body_lookup_status=body_lookup.status,
                    body_candidates=body_lookup.candidates,
                    payload_basis="none",
                    payload_ir=None,
                    cross_heading_ir=None,
                )
                return self._payload_ir_cache[key]

            observed_unit = body_lookup.unique_unit
            payload_ir, cross_heading_ir = (
                self._source_payload_ir_index().observed_payload(observed_unit.unit_id)
                if observed_unit is not None
                else (None, None)
            )
            if payload_ir is not None:
                status = "unique"
                payload_basis: Literal["body_inventory", "none"] = "body_inventory"
            else:
                status = "missing"
                payload_basis = "none"
            self._payload_ir_cache[key] = SourcePayloadLookupResult(
                status=status,
                query=body_lookup.query,
                body_lookup_status=body_lookup.status,
                body_candidates=body_lookup.candidates,
                payload_basis=payload_basis,
                payload_ir=payload_ir,
                cross_heading_ir=cross_heading_ir,
            )
        return self._payload_ir_cache[key]

    def lookup_payload_ir_for_coverage_ref(
        self,
        source_ref: BodyCoveragePayloadRef,
    ) -> SourcePayloadLookupResult:
        """Return payload IR for one concrete body-coverage source unit."""
        query = SourceBodyUnitQuery(
            unit_kind=source_ref.unit_kind,
            label=_norm_num_token(source_ref.label),
            chapter=_norm_num_token(source_ref.chapter or "") if source_ref.chapter else None,
            part=_norm_num_token(source_ref.part or "") if source_ref.part else None,
        )
        matching_units = self._coverage_units_by_payload_ref_id().get(source_ref.unit_id, ())
        body_lookup = self.lookup_body_unit(
            source_ref.unit_kind,
            source_ref.label,
            target_chapter=source_ref.chapter,
            target_part=source_ref.part,
        )
        if len(matching_units) != 1:
            return SourcePayloadLookupResult(
                status="missing",
                query=query,
                body_lookup_status=body_lookup.status,
                body_candidates=body_lookup.candidates,
                payload_basis="none",
                payload_ir=None,
                cross_heading_ir=None,
            )

        payload_ir, cross_heading_ir = self._source_payload_ir_index().coverage_payload(
            source_ref.unit_id
        )
        return SourcePayloadLookupResult(
            status="unique" if payload_ir is not None else "missing",
            query=query,
            body_lookup_status=body_lookup.status,
            body_candidates=body_lookup.candidates,
            payload_basis="coverage_payload_ref" if payload_ir is not None else "none",
            payload_ir=payload_ir,
            cross_heading_ir=cross_heading_ir,
        )

    def find_payload_ir(
        self,
        target_unit_kind: TargetUnitKind | str,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> tuple[IRNode | None, IRNode | None]:
        """Return payload and cross-heading IR for legacy adapter callers."""
        result = self.lookup_payload_ir(
            target_unit_kind,
            target_norm,
            target_chapter,
            target_part,
        )
        return result.payload_ir, result.cross_heading_ir

    def lookup_section_payload_text(
        self,
        section_label: str,
        *,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> SourcePayloadTextLookupResult:
        """Return typed source-body payload text for a section target."""
        payload_lookup = self.lookup_payload_ir(
            "section",
            section_label,
            target_chapter=target_chapter,
            target_part=target_part,
        )
        payload_text = (
            " ".join(irnode_to_text(payload_lookup.payload_ir).split())
            if payload_lookup.payload_ir is not None
            else ""
        )
        return SourcePayloadTextLookupResult(
            status=payload_lookup.status if payload_text else "missing",
            query=payload_lookup.query,
            payload_lookup_status=payload_lookup.status,
            payload_basis=payload_lookup.payload_basis,
            text=payload_text,
        )

    def pre_create_amendment_chapters(
        self,
        state: "ReplayState",
        amendment_id: str,
    ) -> "PrecreatedChaptersResult | None":
        """Pre-create real source-body chapters through the source-model adapter."""
        source_chapters = self.source_chapters()
        if not source_chapters:
            return None

        from lawvm.finland.amendment_chapter_precreate import _pre_create_source_chapters

        return _pre_create_source_chapters(
            state,
            amendment_id,
            source_chapters,
        )

    def source_chapters(self) -> tuple["SourceChapter", ...]:
        """Return cached typed source-body real chapter declarations."""
        if self._source_chapters_cache is None:
            from lawvm.finland.amendment_chapter_precreate import source_chapters_from_tree

            self._source_chapters_cache = source_chapters_from_tree(self.muutos_tree)
        return self._source_chapters_cache

    def source_pseudo_chapters(self) -> tuple["SourcePseudoChapter", ...]:
        """Return cached typed source-body pseudo-chapter marker declarations."""
        if self._source_pseudo_chapters_cache is None:
            from lawvm.finland.amendment_chapter_precreate import source_pseudo_chapters_from_tree

            self._source_pseudo_chapters_cache = source_pseudo_chapters_from_tree(self.muutos_tree)
        return self._source_pseudo_chapters_cache

    def precreate_apply_chapters(
        self,
        *,
        state: "ReplayState",
        resolved: list["ResolvedOp"],
        amendment_id: str,
        vts_ops_enrich_done: bool,
        johto: str,
    ) -> "PrecreateApplyChaptersResult":
        """Pre-create apply-time chapters through the source-model adapter."""
        from lawvm.finland.amendment_chapter_precreate import (
            PrecreateApplyChaptersRequest,
            precreate_apply_chapters,
        )

        return precreate_apply_chapters(
            PrecreateApplyChaptersRequest(
                state=state,
                resolved=resolved,
                amendment_id=amendment_id,
                vts_ops_enrich_done=vts_ops_enrich_done,
                johto=johto,
                source_chapters=self.source_chapters(),
                source_pseudo_chapters=self.source_pseudo_chapters(),
            )
        )

    def preamble_text(self) -> str:
        """Return normalized source preamble text for this amendment."""
        if self._preamble_text_cache is None:
            johto_el = self.muutos_tree.find(".//{*}preamble")
            self._preamble_text_cache = (
                ""
                if johto_el is None
                else etree.tostring(johto_el, method="text", encoding="unicode")
            )
        return self._preamble_text_cache

    def source_text(self) -> str:
        """Return cached plain source text for this amendment."""
        if self._text_cache is None:
            self._text_cache = etree.tostring(
                self.muutos_tree,
                method="text",
                encoding="unicode",
            )
        return self._text_cache

    def source_text_lower(self) -> str:
        """Return cached lowercased plain source text for classifier prefilters."""
        if self._text_lower_cache is None:
            self._text_lower_cache = self.source_text().lower()
        return self._text_lower_cache

    def source_text_contains(self, fragment: str) -> bool:
        """Return whether the plain source text contains ``fragment`` case-insensitively."""
        if not fragment:
            return False
        return fragment.lower() in self.source_text_lower()

    def source_xml_bytes(self) -> bytes:
        """Return corrected source XML bytes for byte-oriented ingest adapters."""
        if self.source_bytes is not None:
            return self.source_bytes
        if self._source_xml_bytes_cache is None:
            self._source_xml_bytes_cache = etree.tostring(self.muutos_tree, encoding="utf-8")
        return self._source_xml_bytes_cache

    def title(self) -> str:
        """Return the source title through the source-model adapter."""
        return self.metadata_surface().source_title

    def issue_date(self) -> dt.date | None:
        """Return the source issue date through the source-model adapter."""
        return self.metadata_surface().source_issue_date

    def effective_date(self) -> dt.date | None:
        """Return the source amendment effective date through the source-model adapter."""
        return self.metadata_surface().effective_date

    def effective_date_with_step(self) -> tuple[dt.date | None, str]:
        """Return source amendment effective date and derivation step."""
        surface = self.metadata_surface()
        return surface.effective_date, surface.effective_date_step

    def expiry_date(self) -> dt.date | None:
        """Return the source amendment expiry date through the source-model adapter."""
        return self.metadata_surface().expiry_date

    def separate_commencement_law_witness(self) -> "SeparateCommencementLawWitness | None":
        """Return the separate-law commencement witness through the source-model adapter."""
        from lawvm.finland.metadata import separate_commencement_law_witness

        return separate_commencement_law_witness(self.source_ref)

    def metadata_surface(self) -> SourceMetadataSurface:
        """Return cached source metadata facts used by compile and temporal phases."""
        if self._metadata_surface_cache is None:
            from lawvm.finland.frontend_compile import _tree_title
            from lawvm.finland.metadata import (
                _amendment_effective_date_with_step,
                _amendment_expiry_date,
                _statute_issue_date,
            )

            effective_date, effective_step = _amendment_effective_date_with_step(
                self.muutos_tree
            )
            self._metadata_surface_cache = SourceMetadataSurface(
                source_issue_date=_statute_issue_date(self.muutos_tree),
                source_title=_tree_title(self.muutos_tree),
                effective_date=effective_date,
                effective_date_step=effective_step,
                expiry_date=_amendment_expiry_date(
                    self.muutos_tree,
                    raw_text=self.source_text(),
                ),
            )
        return self._metadata_surface_cache

    def amendment_tree_metadata(
        self,
        amendment_id: str,
    ) -> "_AmendmentTreeMetadata":
        """Return cached frontend metadata derived from this amendment source."""
        if amendment_id not in self._amendment_metadata_cache:
            from lawvm.finland.frontend_compile import _AmendmentTreeMetadata
            from lawvm.finland.metadata import (
                _temporary_provision_expiry_overrides,
                _temporary_section_expiry_overrides,
            )

            surface = self.metadata_surface()
            raw_text = self.source_text()
            self._amendment_metadata_cache[amendment_id] = _AmendmentTreeMetadata(
                source_issue_date=surface.source_issue_date,
                source_title=surface.source_title,
                effective_date=surface.effective_date,
                expiry_date=surface.expiry_date,
                provision_expiry_overrides=_temporary_provision_expiry_overrides(
                    self.muutos_tree,
                    amendment_id,
                    raw_text=raw_text,
                ),
                section_expiry_overrides=_temporary_section_expiry_overrides(
                    self.muutos_tree,
                    amendment_id,
                    raw_text=raw_text,
                ),
            )
        return self._amendment_metadata_cache[amendment_id]

    def commencement_expiry_override(
        self,
        source_statute_id: str,
        *,
        section_expiry_overrides: tuple[tuple[str, set[str], dt.date], ...] | None = None,
    ) -> tuple[str, set[str] | None, dt.date] | None:
        """Return commencement-clause expiry override metadata."""
        from lawvm.finland.metadata import _commencement_expiry_override

        return _commencement_expiry_override(
            self.muutos_tree,
            source_statute_id,
            section_expiry_overrides=section_expiry_overrides,
        )

    def section_commencement_effective_override(
        self,
        source_statute_id: str,
    ) -> tuple[str, dict[str | None, set[str]], dt.date] | None:
        """Return section-scoped commencement effective override metadata."""
        from lawvm.finland.metadata import _section_commencement_effective_override

        return _section_commencement_effective_override(self.muutos_tree, source_statute_id)

    def chapter_commencement_effective_overrides(
        self,
        source_statute_id: str,
    ) -> tuple[tuple[str, frozenset[str], dt.date], ...]:
        """Return chapter-scoped commencement effective override metadata."""
        from lawvm.finland.metadata import _chapter_commencement_effective_overrides

        return _chapter_commencement_effective_overrides(self.muutos_tree, source_statute_id)

    def section_subsection_commencement_effective_override(
        self,
        source_statute_id: str,
    ) -> tuple[str, tuple[LegalAddress, ...], dt.date] | None:
        """Return subsection-scoped commencement effective override metadata."""
        from lawvm.finland.metadata import _section_subsection_commencement_effective_override

        return _section_subsection_commencement_effective_override(
            self.muutos_tree,
            source_statute_id,
        )

    def section_subsection_application_commencement_effective_override(
        self,
        source_statute_id: str,
    ) -> tuple[str, tuple[LegalAddress, ...], dt.date] | None:
        """Return subsection-scoped application-start effective override metadata."""
        from lawvm.finland.metadata import _section_subsection_application_commencement_effective_override

        return _section_subsection_application_commencement_effective_override(
            self.muutos_tree,
            source_statute_id,
        )

    def operative_body_repeal_candidate(self) -> str:
        """Return body-prose repeal text when no structured operative body exists."""
        from lawvm.finland.metadata import get_operative_body_repeal_candidate

        if self._operative_body_repeal_candidate_cache is None:
            self._operative_body_repeal_candidate_cache = get_operative_body_repeal_candidate(
                self.source_xml_bytes()
            )
        return self._operative_body_repeal_candidate_cache

    def extract_vts_cross_statute_repeals(
        self,
        *,
        parent_id: str,
        parent_title: str,
        strict_profile: "StrictProfile | None",
        skipped_targets_out: list["VtsSkippedTarget"] | None = None,
    ) -> list["AmendmentOp"] | None:
        """Extract cross-statute VTS repeals through the source-model byte adapter.

        Conservation (Audit C): this adapter is the in-set production consumer of
        the :class:`VtsRepealPartition`. It builds the partition, READS its
        ``skipped_targets`` rejected lane and drains it into ``skipped_targets_out``
        (the replay ledger sink), and returns the accepted ops — mirroring the
        ``parent_id``/op-keyword gate the free-function wrappers apply.
        """
        from lawvm.finland.vts import extract_voimaantulo_repeals_partition

        if not parent_id:
            return None
        partition = extract_voimaantulo_repeals_partition(
            self.source_xml_bytes(),
            parent_id,
            parent_title=parent_title,
        )
        if skipped_targets_out is not None:
            skipped_targets_out.extend(partition.skipped_targets)
        return list(partition.accepted)

    def extract_vts_repeals(
        self,
        *,
        extract_vts_repeals: Callable[..., list["AmendmentOp"] | None],
        johto: str,
        parent_id: str,
        parent_title: str,
        strict_profile: "StrictProfile | None",
        skipped_targets_out: list["VtsSkippedTarget"] | None = None,
    ) -> list["AmendmentOp"] | None:
        """Extract VTS repeals through the source-model byte adapter.

        The ``extract_vts_repeals`` callable (default
        ``extract_vts_repeals_fallback``) owns the johto op-keyword gate; it
        drains the :class:`VtsRepealPartition` rejected lane into
        ``skipped_targets_out`` internally, so the dropped targets still reach the
        production replay ledger.
        """
        return extract_vts_repeals(
            johto,
            self.source_xml_bytes(),
            parent_id,
            parent_title,
            strict_profile,
            skipped_targets_out=skipped_targets_out,
        )

    def normalize_and_compile_ops(
        self,
        *,
        compile_ops: Callable[..., "PhaseResult[list[AmendmentOp]]"],
        johto: str,
        master: "ReplayState",
        base_ir: IRNode | None,
        amendment_id: str,
        source_title: str,
        used_preamble_body_fallback: bool,
        parent_id: str,
        strict_profile: "StrictProfile | None",
        parse_result: object | None,
        regex_recognition_coverage_out: list["RegexRecognitionCoverage"] | None,
        amendment_metadata: "_AmendmentTreeMetadata | None",
    ) -> "PhaseResult[list[AmendmentOp]]":
        """Run frontend normalization with XML access owned by the source model."""
        from lawvm.finland.constraints import muutos_node_lookup_cache_scope

        with muutos_node_lookup_cache_scope():
            return compile_ops(
                johto=johto,
                muutos_tree=self.muutos_tree,
                master=master,
                base_ir=base_ir,
                amendment_id=amendment_id,
                source_title=source_title,
                used_preamble_body_fallback=used_preamble_body_fallback,
                parent_id=parent_id,
                strict_profile=strict_profile,
                parse_result=parse_result,
                regex_recognition_coverage_out=regex_recognition_coverage_out,
                amendment_metadata=amendment_metadata,
                source_model=self,
            )

    def enrich_ops_from_amendment_tree(
        self,
        *,
        enrich_ops: Callable[..., list["AmendmentOp"]],
        ops: list["AmendmentOp"],
        amendment_id: str,
        master: "ReplayState | None" = None,
        johto: str = "",
        base_ir: IRNode | None = None,
        parent_id: str = "",
        metadata: "_AmendmentTreeMetadata | None" = None,
    ) -> list["AmendmentOp"]:
        """Stamp source metadata onto ops with XML access owned by the model."""
        return enrich_ops(
            ops,
            amendment_id,
            self.muutos_tree,
            master,
            johto,
            base_ir=base_ir,
            parent_id=parent_id,
            metadata=metadata or self.amendment_tree_metadata(amendment_id),
        )

    def enrich_amendment_ops(
        self,
        *,
        ops: list["AmendmentOp"],
        amendment_id: str,
        master: "ReplayState | None" = None,
        johto: str = "",
        base_ir: IRNode | None = None,
        parent_id: str = "",
        metadata: "_AmendmentTreeMetadata | None" = None,
    ) -> list["AmendmentOp"]:
        """Stamp source metadata onto ops using the default Finland enricher."""
        from lawvm.finland.frontend_compile import _enrich_ops_from_amendment_tree

        return self.enrich_ops_from_amendment_tree(
            enrich_ops=_enrich_ops_from_amendment_tree,
            ops=ops,
            amendment_id=amendment_id,
            master=master,
            johto=johto,
            base_ir=base_ir,
            parent_id=parent_id,
            metadata=metadata,
        )

    def has_uncovered_recovery_content_ops(self, ops: list["AmendmentOp"]) -> bool:
        """Whether section/chapter body recovery is content-authorized."""
        from lawvm.finland.ops import OpType

        if any(
            op.op_type in (OpType.REPLACE, OpType.INSERT)
            and op.target_cols.target_unit_kind == "section"
            and op.target_cols.target_special is None
            for op in ops
        ):
            return True
        if any(
            op.op_type in (OpType.REPLACE, OpType.INSERT) and op.target_cols.target_unit_kind == "chapter"
            for op in ops
        ):
            return True
        return bool(
            # lawvm-regex: prefilter content-authorization boolean gate for body recovery; op-bearing branches above use typed op.op_type/target_unit_kind, this only answers "is recovery content-authorized?"
            re.search(
                r"\bmuutetaan\b|\blisätään\b",
                self.preamble_text(),
                re.IGNORECASE,
            )
        )

    def build_uncovered_recovery_context(
        self,
        *,
        ops: list["AmendmentOp"],
        new_chapter_labels: set[str] | None,
    ) -> "UncoveredRecoveryContext":
        """Build uncovered-recovery context through the source-model adapter."""
        from lawvm.finland.uncovered_recovery_context import build_uncovered_recovery_context

        return build_uncovered_recovery_context(
            preamble_text=self.preamble_text(),
            ops=ops,
            new_chapter_labels=new_chapter_labels,
        )

    def source_body_scope_for_section_target(
        self,
        target_norm: str,
    ) -> tuple[str | None, str | None] | None:
        """Return the unique observed source-body scope for a section."""
        return self.body_section_scope(target_norm)

    def source_body_chapter_for_scoped_section_target(
        self,
        *,
        target_norm: str,
        target_chapter: str,
        target_part: str | None,
    ) -> str | None:
        """Return source-body chapter for a uniquely scoped section target."""
        lookup = self.body_section_lookup(
            target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )
        unit = lookup.unique_unit
        if unit is None:
            return None
        return unit.chapter_label or None

    def retarget_duplicate_body_section_scope_from_close_live_siblings(
        self,
        *,
        section_norm: str,
        body_chapter: str,
        body_part: str | None,
        master: "ReplayState",
    ) -> tuple[str | None, str] | None:
        """Retarget duplicate body scope through the source-model adapter."""
        from lawvm.finland.scope import retarget_duplicate_body_section_scope_from_close_live_siblings

        return retarget_duplicate_body_section_scope_from_close_live_siblings(
            inventory=self.observed_body_inventory(),
            section_norm=section_norm,
            body_chapter=body_chapter,
            body_part=body_part,
            master=master,
        )

    def retarget_heading_insert_body_chapter_from_close_live_sibling(
        self,
        *,
        section_norm: str,
        body_chapter: str,
        master: "ReplayState",
    ) -> str:
        """Retarget heading-only body chapter through the source-model adapter."""
        from lawvm.finland.scope import retarget_heading_insert_body_chapter_from_close_live_sibling

        return retarget_heading_insert_body_chapter_from_close_live_sibling(
            inventory=self.observed_body_inventory(),
            section_norm=section_norm,
            body_chapter=body_chapter,
            master=master,
        )

    def resolve_group_surface_scope(
        self,
        *,
        target_unit_kind: TargetUnitKind,
        target_norm: str,
        target_chapter: str | None,
        target_part: str | None,
        group_ops: Iterable["AmendmentOp"],
    ) -> tuple[str | None, str | None]:
        """Return source-facing group surface scope through the source model."""
        from lawvm.finland.lowering_scope_recovery import resolve_group_surface_scope

        return resolve_group_surface_scope(
            source_model=self,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            group_ops=group_ops,
        )
