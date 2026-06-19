"""Finland amendment source model.

This module is the first IR-frontier boundary for amendment bodies.  It keeps
the parsed XML root as the acquisition witness, but owns the derived body
indexes and payload surfaces so later compiler phases do not independently
walk and reinterpret the same XML tree.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal, Optional, cast

import lxml.etree as etree

from lawvm.core.coverage import CoverageIgnoredUnit, CoverageUnit
from lawvm.core.ir import IRNode
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.body_coverage import extract_body_coverage
from lawvm.finland.body_pairing import ObservedBodyUnit, build_observed_body_inventory
from lawvm.finland.constraints import _find_muutos_node_uncached
from lawvm.finland.helpers import _norm_num_token

if TYPE_CHECKING:
    from lawvm.finland.amendment_chapter_precreate import (
        PrecreateApplyChaptersResult,
        PrecreatedChaptersResult,
    )
    from lawvm.finland.ops import AmendmentOp, ResolvedOp
    from lawvm.finland.statute import ReplayState
    from lawvm.finland.uncovered_recovery_context import UncoveredRecoveryContext


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


@dataclass(slots=True)
class AmendmentSourceModel:
    """Cached read-only projections over one Finland amendment source tree."""

    muutos_tree: etree._Element
    source_ref: str = ""
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
    _node_cache: dict[SourceUnitLookup, etree._Element | None] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _payload_ir_cache: dict[SourceUnitLookup, tuple[IRNode | None, IRNode | None]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    @classmethod
    def from_tree(
        cls,
        muutos_tree: etree._Element,
        *,
        source_ref: str = "",
    ) -> "AmendmentSourceModel":
        return cls(muutos_tree=muutos_tree, source_ref=source_ref)

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

    def find_xml_node(
        self,
        target_unit_kind: TargetUnitKind | str,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> etree._Element | None:
        """Return the source XML node for a normalized source-body target."""
        key = SourceUnitLookup(
            unit_kind=str(target_unit_kind or ""),
            label=target_norm,
            chapter=target_chapter,
            part=target_part,
        )
        if key not in self._node_cache:
            self._node_cache[key] = _find_muutos_node_uncached(
                self.muutos_tree,
                cast(TargetUnitKind, key.unit_kind),
                key.label,
                key.chapter,
                key.part,
            )
        return self._node_cache[key]

    def has_source_node(
        self,
        target_unit_kind: TargetUnitKind | str,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> bool:
        """Return whether the source body contains the normalized target."""
        return (
            self.find_xml_node(
                target_unit_kind,
                target_norm,
                target_chapter,
                target_part,
            )
            is not None
        )

    def find_payload_ir(
        self,
        target_unit_kind: TargetUnitKind | str,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> tuple[IRNode | None, IRNode | None]:
        """Return cached payload and cross-heading IR for a source-body target."""
        key = SourceUnitLookup(
            unit_kind=str(target_unit_kind or ""),
            label=target_norm,
            chapter=target_chapter,
            part=target_part,
        )
        if key not in self._payload_ir_cache:
            from lawvm.finland.amendment_payload_lookup import _payload_ir_from_muutos_node

            source_node = self.find_xml_node(
                key.unit_kind,
                key.label,
                key.chapter,
                key.part,
            )
            self._payload_ir_cache[key] = (
                _payload_ir_from_muutos_node(
                    source_node,
                    target_unit_kind=key.unit_kind,
                    target_norm=key.label,
                )
                if source_node is not None
                else (None, None)
            )
        return self._payload_ir_cache[key]

    def pre_create_amendment_chapters(
        self,
        state: "ReplayState",
        amendment_id: str,
    ) -> "PrecreatedChaptersResult | None":
        """Pre-create real source-body chapters through the source-model adapter."""
        muutos_body = self.muutos_tree.find(".//{*}body")
        if muutos_body is None:
            return None

        from lawvm.finland.amendment_chapter_precreate import _pre_create_amendment_chapters

        return _pre_create_amendment_chapters(
            state,
            muutos_body,
            amendment_id,
        )

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
                muutos_tree=self.muutos_tree,
                amendment_id=amendment_id,
                vts_ops_enrich_done=vts_ops_enrich_done,
                johto=johto,
            )
        )

    def preamble_text(self) -> str:
        """Return normalized source preamble text for this amendment."""
        johto_el = self.muutos_tree.find(".//{*}preamble")
        if johto_el is None:
            return ""
        return etree.tostring(johto_el, method="text", encoding="unicode")

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
            muutos_tree=self.muutos_tree,
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
            muutos_tree=self.muutos_tree,
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
            muutos_tree=self.muutos_tree,
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
            muutos_tree=self.muutos_tree,
            source_model=self,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            group_ops=group_ops,
        )
