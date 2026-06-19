"""Finland amendment source model.

This module is the first IR-frontier boundary for amendment bodies.  It keeps
the parsed XML root as the acquisition witness, but owns the derived body
indexes and payload surfaces so later compiler phases do not independently
walk and reinterpret the same XML tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, cast

import lxml.etree as etree

from lawvm.core.coverage import CoverageIgnoredUnit, CoverageUnit
from lawvm.core.ir import IRNode
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.finland.body_coverage import extract_body_coverage
from lawvm.finland.body_pairing import ObservedBodyUnit, build_observed_body_inventory
from lawvm.finland.constraints import _find_muutos_node_uncached

if TYPE_CHECKING:
    from lawvm.finland.amendment_chapter_precreate import PrecreatedChaptersResult
    from lawvm.finland.statute import ReplayState


@dataclass(frozen=True, slots=True)
class SourceUnitLookup:
    """A normalized source-body lookup key."""

    unit_kind: str
    label: str
    chapter: Optional[str] = None
    part: Optional[str] = None


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
            from lawvm.finland.amendment_payload_lookup import _find_muutos_ir

            self._payload_ir_cache[key] = _find_muutos_ir(
                self.muutos_tree,
                key.unit_kind,
                key.label,
                key.chapter,
                key.part,
                source_model=self,
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
