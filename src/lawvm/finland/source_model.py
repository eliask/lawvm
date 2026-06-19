"""Finland amendment source model.

This module is the first IR-frontier boundary for amendment bodies.  It keeps
the parsed XML root as the acquisition witness, but owns the derived body
indexes and payload surfaces so later compiler phases do not independently
walk and reinterpret the same XML tree.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

import lxml.etree as etree

from lawvm.core.coverage import CoverageIgnoredUnit, CoverageUnit
from lawvm.core.ir import IRNode
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.body_coverage import BodyCoveragePayloadRef, extract_body_coverage
from lawvm.finland.body_pairing import ObservedBodyUnit, build_observed_body_inventory
from lawvm.finland.helpers import (
    _normalize_source_part_num,
    _normalize_source_section_num,
    _norm_num_token,
)

if TYPE_CHECKING:
    from lawvm.core.compile_result import StrictProfile
    from lawvm.core.phase_result import PhaseResult
    from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage
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


def _coverage_payload_ir_by_unit_id(
    muutos_tree: etree._Element,
) -> dict[str, tuple[IRNode | None, IRNode | None]]:
    """Return converted payload IR keyed by the same unit ids as body coverage."""
    from lawvm.finland.amendment_payload_lookup import _payload_ir_from_muutos_node

    body = muutos_tree if _xml_localname(muutos_tree) == "body" else muutos_tree.find(".//{*}body")
    if body is None:
        body = muutos_tree.find(".//body")
    if body is None:
        return {}
    payloads: dict[str, tuple[IRNode | None, IRNode | None]] = {}
    seen_ids: set[str] = set()

    def append_node(
        kind: str,
        observed_label: str,
        parent_label: str | None,
        el: etree._Element,
    ) -> None:
        base_id = f"{kind}_{observed_label}"
        if parent_label:
            base_id = f"{kind}_{parent_label}_{observed_label}"
        unit_id = base_id
        counter = 1
        while unit_id in seen_ids:
            unit_id = f"{base_id}_{counter}"
            counter += 1
        seen_ids.add(unit_id)
        payloads[unit_id] = _payload_ir_from_muutos_node(
            el,
            target_unit_kind=kind,
            target_norm=observed_label,
        )

    def walk_children(parent: etree._Element, active_chapter: str | None = None) -> None:
        current_chapter = active_chapter
        for child in parent:
            kind = _xml_localname(child)
            if kind == "crossHeading":
                raw_part = " ".join("".join(str(part) for part in child.itertext()).split())
                if _normalize_source_part_num(raw_part):
                    current_chapter = None
                    continue
            if kind == "part":
                raw_num = _xml_num_text(child)
                if raw_num and _normalize_source_part_num(raw_num):
                    walk_children(child, active_chapter=None)
                    current_chapter = active_chapter
                    continue
            if kind == "chapter":
                raw_num = _xml_num_text(child)
                if raw_num:
                    chapter_label = _norm_num_token(raw_num).removesuffix("luku")
                    if chapter_label:
                        append_node("chapter", chapter_label, None, child)
                        walk_children(child, chapter_label)
                        current_chapter = active_chapter
                        continue
            if kind == "section":
                raw_num = _xml_num_text(child)
                if raw_num:
                    if _norm_num_token(raw_num).endswith("luku"):
                        pseudo_chapter = _norm_num_token(raw_num).removesuffix("luku")
                        if pseudo_chapter:
                            append_node("chapter", pseudo_chapter, None, child)
                            walk_children(child, pseudo_chapter)
                            current_chapter = pseudo_chapter
                            continue
                    observed_label = _normalize_source_section_num(raw_num)
                    if observed_label:
                        append_node("section", observed_label, current_chapter, child)
                        walk_children(child, current_chapter)
                        continue
            if kind == "article":
                raw_num = _xml_num_text(child)
                if raw_num:
                    observed_label = _norm_num_token(raw_num)
                    if observed_label:
                        append_node("article", observed_label, current_chapter, child)
            walk_children(child, current_chapter)

    walk_children(body)
    return payloads


def _observed_payload_ir_by_unit_id(
    muutos_tree: etree._Element,
) -> dict[str, tuple[IRNode | None, IRNode | None]]:
    """Return converted payload IR keyed by observed body-inventory unit id."""
    from lawvm.finland.amendment_payload_lookup import _payload_ir_from_muutos_node

    body = muutos_tree if _xml_localname(muutos_tree) == "body" else muutos_tree.find(".//{*}body")
    if body is None:
        body = muutos_tree.find(".//body")
    if body is None:
        return {}
    payloads: dict[str, tuple[IRNode | None, IRNode | None]] = {}
    seen_ids: set[str] = set()

    def append_payload(
        kind: str,
        label: str,
        chapter_label: str,
        el: etree._Element,
    ) -> None:
        base_id = f"{kind}:{chapter_label}/{label}" if chapter_label else f"{kind}:{label}"
        unit_id = base_id
        counter = 1
        while unit_id in seen_ids:
            unit_id = f"{base_id}#{counter}"
            counter += 1
        seen_ids.add(unit_id)
        payloads[unit_id] = _payload_ir_from_muutos_node(
            el,
            target_unit_kind=kind,
            target_norm=label,
        )

    def walk_children(parent: etree._Element, active_chapter: str = "") -> None:
        current_chapter = active_chapter
        for child in parent:
            kind = _xml_localname(child)
            if kind == "crossHeading":
                raw_part = " ".join("".join(str(part) for part in child.itertext()).split())
                if _normalize_source_part_num(raw_part):
                    append_payload("part", _normalize_source_part_num(raw_part), "", child)
                    current_chapter = ""
                    continue
            if kind == "part":
                raw_num = _xml_num_text(child)
                if raw_num:
                    part_label = _normalize_source_part_num(raw_num)
                    if part_label:
                        append_payload("part", part_label, "", child)
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
            walk_children(child, current_chapter)

    walk_children(body)
    return payloads


@dataclass(slots=True)
class AmendmentSourceModel:
    """Cached read-only projections over one Finland amendment source tree."""

    muutos_tree: etree._Element
    source_ref: str = ""
    source_bytes: bytes | None = None
    _observed_body_inventory: tuple[ObservedBodyUnit, ...] | None = field(
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
    _coverage_payload_ir_cache: dict[str, tuple[IRNode | None, IRNode | None]] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _observed_payload_ir_cache: dict[str, tuple[IRNode | None, IRNode | None]] | None = field(
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
    _text_cache: str | None = field(
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
    ) -> "AmendmentSourceModel":
        return cls(
            muutos_tree=muutos_tree,
            source_ref=source_ref,
            source_bytes=source_bytes,
        )

    @property
    def has_body(self) -> bool:
        return self.muutos_tree.find(".//{*}body") is not None

    def has_eid_free_body_sections(self) -> bool:
        """Return True when the source body has sections and none carry eIds."""
        sections = self.muutos_tree.findall(".//{*}section")
        return bool(sections) and not any(section.get("eId") for section in sections)

    def observed_body_inventory(self) -> tuple[ObservedBodyUnit, ...]:
        """Return cached body-pairing inventory units."""
        if self._observed_body_inventory is None:
            self._observed_body_inventory = tuple(
                build_observed_body_inventory(self.muutos_tree)
            )
        return self._observed_body_inventory

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

    def body_section_scope(
        self,
        target_norm: str,
    ) -> tuple[str | None, str | None] | None:
        """Return the unique observed body (part, chapter) scope for a section."""
        wanted = _norm_num_token(target_norm)
        scopes = {
            (unit.part_label or None, unit.chapter_label or None)
            for unit in self.observed_body_inventory()
            if unit.kind == "section" and _norm_num_token(unit.label) == wanted
        }
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
        return next(
            (
                unit.chapter_label
                for unit in self.observed_body_inventory()
                if unit.kind == "section"
                and _norm_num_token(unit.label) == wanted
                and unit.chapter_label
            ),
            None,
        )

    def body_has_pseudo_chapter_marker(self, chapter_label: str) -> bool:
        """Return True if the observed body has a section-shaped chapter marker."""
        wanted = _norm_num_token(chapter_label)
        return any(
            unit.kind == "chapter"
            and _norm_num_token(unit.label) == wanted
            and unit.source_tag == "section"
            for unit in self.observed_body_inventory()
        )

    def body_has_real_chapter_container(self, chapter_label: str) -> bool:
        """Return True if the observed body has a real chapter container."""
        wanted = _norm_num_token(chapter_label)
        return any(
            unit.kind == "chapter"
            and _norm_num_token(unit.label) == wanted
            and unit.source_tag == "chapter"
            for unit in self.observed_body_inventory()
        )

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
        candidates = tuple(
            unit
            for unit in self.observed_body_inventory()
            if unit.kind == query.unit_kind
            and _norm_num_token(unit.label) == query.label
            and (
                query.chapter is None
                or _norm_num_token(unit.chapter_label) == query.chapter
            )
            and (
                query.part is None
                or _norm_num_token(unit.part_label) == query.part
            )
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

    def _coverage_payload_ir_by_unit_id(self) -> dict[str, tuple[IRNode | None, IRNode | None]]:
        if self._coverage_payload_ir_cache is None:
            self._coverage_payload_ir_cache = _coverage_payload_ir_by_unit_id(self.muutos_tree)
        return self._coverage_payload_ir_cache

    def _observed_payload_ir_by_unit_id(self) -> dict[str, tuple[IRNode | None, IRNode | None]]:
        if self._observed_payload_ir_cache is None:
            self._observed_payload_ir_cache = _observed_payload_ir_by_unit_id(self.muutos_tree)
        return self._observed_payload_ir_cache

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
            label=target_norm,
            chapter=target_chapter,
            part=target_part,
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
                self._observed_payload_ir_by_unit_id().get(observed_unit.unit_id, (None, None))
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
        matching_units = tuple(
            unit
            for unit in self.body_coverage_units()
            if isinstance(unit.payload_ref, BodyCoveragePayloadRef)
            and unit.payload_ref.unit_id == source_ref.unit_id
        )
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

        payload_ir, cross_heading_ir = self._coverage_payload_ir_by_unit_id().get(
            source_ref.unit_id,
            (None, None),
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
        johto_el = self.muutos_tree.find(".//{*}preamble")
        if johto_el is None:
            return ""
        return etree.tostring(johto_el, method="text", encoding="unicode")

    def source_text(self) -> str:
        """Return cached plain source text for this amendment."""
        if self._text_cache is None:
            self._text_cache = etree.tostring(
                self.muutos_tree,
                method="text",
                encoding="unicode",
            )
        return self._text_cache

    def source_text_contains(self, fragment: str) -> bool:
        """Return whether the plain source text contains ``fragment`` case-insensitively."""
        if not fragment:
            return False
        return fragment.lower() in self.source_text().lower()

    def source_xml_bytes(self) -> bytes:
        """Return corrected source XML bytes for byte-oriented ingest adapters."""
        if self.source_bytes is not None:
            return self.source_bytes
        return etree.tostring(self.muutos_tree, encoding="utf-8")

    def title(self) -> str:
        """Return the source title through the source-model adapter."""
        from lawvm.finland.frontend_compile import _tree_title

        return _tree_title(self.muutos_tree)

    def issue_date(self) -> dt.date | None:
        """Return the source issue date through the source-model adapter."""
        from lawvm.finland.metadata import _statute_issue_date

        return _statute_issue_date(self.muutos_tree)

    def effective_date(self) -> dt.date | None:
        """Return the source amendment effective date through the source-model adapter."""
        from lawvm.finland.metadata import _amendment_effective_date

        return _amendment_effective_date(self.muutos_tree)

    def effective_date_with_step(self) -> tuple[dt.date | None, str]:
        """Return source amendment effective date and derivation step."""
        from lawvm.finland.metadata import _amendment_effective_date_with_step

        return _amendment_effective_date_with_step(self.muutos_tree)

    def expiry_date(self) -> dt.date | None:
        """Return the source amendment expiry date through the source-model adapter."""
        from lawvm.finland.metadata import _amendment_expiry_date

        return _amendment_expiry_date(self.muutos_tree)

    def amendment_tree_metadata(
        self,
        amendment_id: str,
    ) -> "_AmendmentTreeMetadata":
        """Return cached frontend metadata derived from this amendment source."""
        if amendment_id not in self._amendment_metadata_cache:
            from lawvm.finland.frontend_compile import _amendment_tree_metadata

            self._amendment_metadata_cache[amendment_id] = _amendment_tree_metadata(
                amendment_id=amendment_id,
                muutos_tree=self.muutos_tree,
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
    ) -> tuple[str, dict[str, set[str]], dt.date] | None:
        """Return section-scoped commencement effective override metadata."""
        from lawvm.finland.metadata import _section_commencement_effective_override

        return _section_commencement_effective_override(self.muutos_tree, source_statute_id)

    def section_subsection_commencement_effective_override(
        self,
        source_statute_id: str,
    ) -> tuple[str, set[str], dt.date] | None:
        """Return subsection-scoped commencement effective override metadata."""
        from lawvm.finland.metadata import _section_subsection_commencement_effective_override

        return _section_subsection_commencement_effective_override(
            self.muutos_tree,
            source_statute_id,
        )

    def operative_body_repeal_candidate(self) -> str:
        """Return body-prose repeal text when no structured operative body exists."""
        from lawvm.finland.metadata import get_operative_body_repeal_candidate

        return get_operative_body_repeal_candidate(self.source_xml_bytes())

    def extract_vts_cross_statute_repeals(
        self,
        *,
        parent_id: str,
        parent_title: str,
        strict_profile: "StrictProfile | None",
        skipped_targets_out: list["VtsSkippedTarget"] | None = None,
    ) -> list["AmendmentOp"] | None:
        """Extract cross-statute VTS repeals through the source-model byte adapter."""
        from lawvm.finland.vts import extract_vts_cross_statute_repeals

        return extract_vts_cross_statute_repeals(
            self.source_xml_bytes(),
            parent_id,
            parent_title,
            strict_profile,
            skipped_targets_out=skipped_targets_out,
        )

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
        """Extract VTS repeals through the source-model byte adapter."""
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
        used_sec1_fallback: bool,
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
                used_sec1_fallback=used_sec1_fallback,
                parent_id=parent_id,
                strict_profile=strict_profile,
                parse_result=parse_result,
                regex_recognition_coverage_out=regex_recognition_coverage_out,
                amendment_metadata=amendment_metadata,
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
        if any(
            op.op_type in ("REPLACE", "INSERT")
            and op.target_unit_kind == "section"
            and op.target_special is None
            for op in ops
        ):
            return True
        if any(
            op.op_type in ("REPLACE", "INSERT") and op.target_unit_kind == "chapter"
            for op in ops
        ):
            return True
        return bool(
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
