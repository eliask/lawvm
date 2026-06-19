"""Late temporal and kumotaan replay-product postprocessing for Finland.

This module owns process-level rewrites that happen after normal amendment
compilation but before the process result is materialized: commencement/expiry
metadata rewrites, law-level text patch collection, and pure kumotaan repeal
operation injection. It mutates caller-owned report/op streams deliberately; it
does not apply tree mutations itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set

from lxml import etree

from lawvm.core.compile_result import ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.finland.johtolause import extract_law_level_text_patch_los as _extract_law_level_patch_los
from lawvm.finland.kumotaan import (
    _extract_kumotaan_chapter_section_map,
    _extract_kumotaan_subsection_refs,
    kumotaan_recycle_guard_result,
)
from lawvm.finland.kumotaan_replay import (
    _inject_pure_kumotaan_repeal_ops,
    _inject_pure_kumotaan_subsection_repeal_ops,
    _live_suffix_section_labels_for_numeric_kumotaan_ranges,
    _rewrite_kumotaan_snapshot_replaces_to_repeal,
)
from lawvm.finland.metadata import (
    _commencement_expiry_override,
    _section_commencement_effective_override,
    _section_subsection_commencement_effective_override,
    get_operative_body_repeal_candidate,
)
from lawvm.finland.temporal_rewrites import (
    _rewrite_compiled_op_activation_rule_effective,
    _rewrite_compiled_op_activation_rule_effective_for_addresses,
    _rewrite_compiled_op_activation_rule_effective_for_address_suffixes,
    _rewrite_later_effective_lo_groups,
    _rewrite_lo_op_group_id,
    _rewrite_lo_op_source_effective,
    _rewrite_lo_op_source_effective_for_address_suffixes,
    _rewrite_lo_op_source_expiry,
)

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


RecordProcessFinding = Callable[..., Finding]
ReplayPrint = Callable[[str], None]


@dataclass(slots=True)
class ProcessTemporalPostprocessContext:
    amendment_id: str
    parent_id: str
    ctx_id: str
    source_title: str
    johto: str
    xml_bytes: bytes
    muutos_tree: etree._Element
    base_ir: IRNode
    state: ReplayState
    replay_mode: str
    amendment_issue_date: Optional[dt.date]
    amendment_effective_date: Optional[dt.date]
    lo_ops_out: Optional[List[_LegalOperation]]
    compiled_ops_out: Optional[List[Dict[str, object]]]
    amendment_temporal_events: list[TemporalEvent]
    commencement_expiry_override_notes: list[dict[str, object]]
    record_finding: RecordProcessFinding
    replay_print: ReplayPrint
    section_expiry_overrides: tuple[tuple[str, Set[str], dt.date], ...] = ()

    def run(self) -> None:
        self.collect_law_level_text_patches()
        self.apply_commencement_expiry_overrides()
        self.apply_section_commencement_overrides()
        self.apply_later_effective_group_rewrites()
        self.apply_kumotaan_replay_product_rewrites()

    def collect_law_level_text_patches(self) -> None:
        if self.lo_ops_out is None:
            return
        effective_iso = self.amendment_effective_date.isoformat() if self.amendment_effective_date else ""
        patches = _extract_law_level_patch_los(
            self.johto,
            amendment_id=self.amendment_id,
            effective=effective_iso,
        )
        if not patches:
            return
        self.replay_print(f"  [{self.amendment_id}] {len(patches)} law-level text patch(es) collected")
        self.lo_ops_out.extend(patches)

    def apply_commencement_expiry_overrides(self) -> None:
        # Accepted voimaantulosäännös-only amendments may extend or expire prior
        # ops even when this amendment emitted no section-level replacement ops.
        has_foreign_scoped_expiry = any(
            target_mid != self.amendment_id
            for target_mid, _labels, _expiry in self.section_expiry_overrides
        )
        accepted = None
        if has_foreign_scoped_expiry or b"voimaantulos" in self.xml_bytes.lower():
            accepted = _commencement_expiry_override(
                self.muutos_tree,
                self.amendment_id,
                section_expiry_overrides=self.section_expiry_overrides,
            )
        if accepted is not None:
            target_mid, labels, expiry = accepted
            if target_mid != self.amendment_id and _rewrite_lo_op_source_expiry(
                self.lo_ops_out,
                target_mid,
                labels,
                expiry,
                parent_statute_id=self.parent_id,
                replay_mode=self.replay_mode,
                expiry_convention="inclusive_prose",
            ):
                scope = sorted(labels) if labels else ["*"]
                self.replay_print(
                    f"  [{self.amendment_id}] voimaantulo_expiry_override (accepted): "
                    f"{target_mid} {scope} -> {expiry.isoformat()}"
                )
                self.commencement_expiry_override_notes.append(
                    {
                        "source_statute": self.amendment_id,
                        "target_statute": target_mid,
                        "labels": scope,
                        "expiry": expiry.isoformat(),
                        "context": "accepted_amendment",
                    }
                )

        for target_mid, labels, expiry in self.section_expiry_overrides:
            if target_mid == self.amendment_id and _rewrite_lo_op_source_expiry(
                self.lo_ops_out,
                target_mid,
                labels,
                expiry,
                parent_statute_id=self.parent_id,
                replay_mode=self.replay_mode,
                expiry_convention="inclusive_prose",
            ):
                scope = sorted(labels) if labels else ["*"]
                self.replay_print(
                    f"  [{self.amendment_id}] temporary_section_expiry_override (accepted): "
                    f"{target_mid} {scope} -> {expiry.isoformat()}"
                )
                self.commencement_expiry_override_notes.append(
                    {
                        "source_statute": self.amendment_id,
                        "target_statute": target_mid,
                        "labels": scope,
                        "expiry": expiry.isoformat(),
                        "context": "accepted_section_temporary",
                    }
                )

    def apply_section_commencement_overrides(self) -> None:
        override = _section_commencement_effective_override(
            self.muutos_tree,
            self.amendment_id,
        )
        if override is not None:
            target_mid, chapter_section_map, effective = override
            lo_updated = _rewrite_lo_op_source_effective(
                self.lo_ops_out,
                target_mid,
                effective,
                chapter_section_map=chapter_section_map,
                base_ir=self.base_ir,
            )
            scoped_group_id = f"finland-johto:{self.amendment_id}:section_commencement"
            scoped_addresses = _rewrite_lo_op_group_id(
                self.lo_ops_out,
                target_mid,
                scoped_group_id,
                chapter_section_map=chapter_section_map,
            )
            compiled_updated = _rewrite_compiled_op_activation_rule_effective(
                self.compiled_ops_out,
                target_mid,
                effective,
                chapter_section_map=chapter_section_map,
            )
            if scoped_addresses:
                self.amendment_temporal_events.append(
                    self._commence_event(
                        event_group_id=scoped_group_id,
                        exact_addresses=scoped_addresses,
                        effective=effective,
                    )
                )
            if lo_updated or compiled_updated:
                scope = sorted(
                    f"{chap + '/' if chap else ''}{sec}"
                    for chap, sections in chapter_section_map.items()
                    for sec in sections
                )
                self.replay_print(
                    f"  [{self.amendment_id}] section_commencement_effective_override (accepted): "
                    f"{target_mid} {scope} -> {effective.isoformat()}"
                )
                self.commencement_expiry_override_notes.append(
                    {
                        "source_statute": self.amendment_id,
                        "target_statute": target_mid,
                        "labels": scope,
                        "effective": effective.isoformat(),
                        "context": "accepted_section_commencement",
                    }
                )

        subsection_override = _section_subsection_commencement_effective_override(
            self.muutos_tree,
            self.amendment_id,
        )
        if subsection_override is None:
            return
        target_mid, address_suffixes, effective = subsection_override
        scoped_group_id = f"finland-johto:{self.amendment_id}:subsection_commencement"
        scoped_addresses = _rewrite_lo_op_source_effective_for_address_suffixes(
            self.lo_ops_out,
            target_mid,
            effective,
            address_suffixes=address_suffixes,
            new_group_id=scoped_group_id,
        )
        compiled_updated = _rewrite_compiled_op_activation_rule_effective_for_address_suffixes(
            self.compiled_ops_out,
            target_mid,
            effective,
            address_suffixes=address_suffixes,
        )
        if scoped_addresses:
            self.amendment_temporal_events.append(
                self._commence_event(
                    event_group_id=scoped_group_id,
                    exact_addresses=scoped_addresses,
                    effective=effective,
                )
            )
        if scoped_addresses or compiled_updated:
            scope = sorted(str(address) for address in scoped_addresses)
            self.replay_print(
                f"  [{self.amendment_id}] subsection_commencement_effective_override (accepted): "
                f"{target_mid} {scope} -> {effective.isoformat()}"
            )
            self.commencement_expiry_override_notes.append(
                {
                    "source_statute": self.amendment_id,
                    "target_statute": target_mid,
                    "labels": scope,
                    "effective": effective.isoformat(),
                    "context": "accepted_subsection_commencement",
                }
            )

    def apply_later_effective_group_rewrites(self) -> None:
        if self.lo_ops_out is None or self.amendment_effective_date is None:
            return
        later_effective_groups = _rewrite_later_effective_lo_groups(
            self.lo_ops_out,
            target_source_statute=self.amendment_id,
            amendment_effective_date=self.amendment_effective_date,
        )
        for effective_iso, exact_addresses in sorted(later_effective_groups.items()):
            effective_date = dt.date.fromisoformat(effective_iso)
            _rewrite_compiled_op_activation_rule_effective_for_addresses(
                self.compiled_ops_out,
                target_source_statute=self.amendment_id,
                effective_date=effective_date,
                exact_addresses=exact_addresses,
            )
            self.amendment_temporal_events.append(
                self._commence_event(
                    event_group_id=f"finland-johto:{self.amendment_id}:effective:{effective_iso}",
                    exact_addresses=exact_addresses,
                    effective=effective_date,
                )
            )

    def apply_kumotaan_replay_product_rewrites(self) -> None:
        if self.lo_ops_out is None or self.amendment_effective_date is None:
            return

        recycle_guard = kumotaan_recycle_guard_result(self.johto)
        kumotaan_labels = list(recycle_guard.filtered_labels)
        kumotaan_chap_map = _extract_kumotaan_chapter_section_map(self.johto)
        if recycle_guard.fired:
            self.replay_print(
                f"  [{self.amendment_id}] kumotaan_muutetaan_recycle_guard: "
                f"excluding {list(recycle_guard.recycled_labels)} "
                "(appear in both kumotaan+muutetaan)"
            )
            self.record_finding(
                kind="PARSE.KUMOTAAN_RECYCLE_GUARD",
                message=(
                    "Kumotaan repeal candidates were excluded because the "
                    "same source also replaces those targets."
                ),
                source_statute=self.amendment_id,
                detail=recycle_guard.finding_detail(),
                role="observation",
                blocking=False,
            )

        chap_map_sets: Optional[Dict[Optional[str], Set[str]]] = None
        if kumotaan_chap_map and any(k is not None for k in kumotaan_chap_map):
            chap_map_sets = {k: {s.lower() for s in sections} for k, sections in kumotaan_chap_map.items()}

        range_suffix_sections = _live_suffix_section_labels_for_numeric_kumotaan_ranges(
            self.johto,
            state=self.state,
        )
        if range_suffix_sections:
            known_labels = {label.lower() for label in kumotaan_labels}
            for chapter, labels in range_suffix_sections.items():
                for label in sorted(labels):
                    if label not in known_labels:
                        kumotaan_labels.append(label)
                        known_labels.add(label)
                if chap_map_sets is not None:
                    chap_map_sets.setdefault(chapter, set()).update(labels)

        if kumotaan_labels and _rewrite_lo_op_source_expiry(
            self.lo_ops_out,
            self.amendment_id,
            set(kumotaan_labels),
            self.amendment_effective_date,
            parent_statute_id=self.parent_id,
            replay_mode=self.replay_mode,
            chapter_section_map=chap_map_sets,
            expiry_convention="exclusive_cutoff",
        ):
            scope = sorted(set(kumotaan_labels))
            self.replay_print(
                f"  [{self.amendment_id}] kumotaan_section_expiry_override: "
                f"{self.amendment_id} {scope} -> {self.amendment_effective_date.isoformat()}"
            )
            self.commencement_expiry_override_notes.append(
                {
                    "source_statute": self.amendment_id,
                    "target_statute": self.amendment_id,
                    "labels": scope,
                    "expiry": self.amendment_effective_date.isoformat(),
                    "context": "repeal_clause",
                }
            )
            _rewrite_kumotaan_snapshot_replaces_to_repeal(
                self.lo_ops_out,
                target_source_statute=self.amendment_id,
                section_labels={label.lower() for label in kumotaan_labels},
                chapter_section_map=chap_map_sets,
                source_raw_text=self.johto,
            )

        if kumotaan_labels:
            pure_count = _inject_pure_kumotaan_repeal_ops(
                self.lo_ops_out,
                amendment_id=self.amendment_id,
                source_title=self.source_title,
                amendment_issue_date=self.amendment_issue_date,
                kumotaan_labels=kumotaan_labels,
                chap_map_sets=chap_map_sets,
                amendment_effective_date=self.amendment_effective_date,
                state=self.state,
                source_raw_text=self.johto,
            )
            if pure_count:
                self.replay_print(f"  [{self.amendment_id}] pure_kumotaan_repeal_injected: {pure_count} section(s)")

        johto_for_subsection = self.johto
        if not _extract_kumotaan_subsection_refs(self.johto):
            body_repeal = get_operative_body_repeal_candidate(self.xml_bytes)
            if body_repeal:
                johto_for_subsection = self.johto + " " + body_repeal
        subsection_map = _extract_kumotaan_subsection_refs(johto_for_subsection)
        if not subsection_map:
            return
        pure_subsection_count = _inject_pure_kumotaan_subsection_repeal_ops(
            self.lo_ops_out,
            amendment_id=self.amendment_id,
            source_title=self.source_title,
            kumotaan_subsection_map=subsection_map,
            amendment_effective_date=self.amendment_effective_date,
            amendment_issue_date=self.amendment_issue_date,
            state=self.state,
            source_raw_text=johto_for_subsection,
        )
        if pure_subsection_count:
            self.replay_print(
                f"  [{self.amendment_id}] pure_kumotaan_subsection_repeal_injected: "
                f"{pure_subsection_count} subsection(s)"
            )

    def _commence_event(
        self,
        *,
        event_group_id: str,
        exact_addresses: tuple[LegalAddress, ...],
        effective: dt.date,
    ) -> TemporalEvent:
        effective_iso = effective.isoformat()
        return TemporalEvent(
            event_id=f"fi-temporal:{event_group_id}",
            kind="commence",
            scope=TemporalScope(
                target_statute=self.parent_id or self.ctx_id,
                exact_addresses=exact_addresses,
            ),
            effective=effective_iso,
            source=OperationSource(
                statute_id=self.amendment_id,
                title=self.source_title,
                enacted=self.amendment_issue_date.isoformat() if self.amendment_issue_date else "",
                effective=effective_iso,
            ),
            activation_rule=ActivationRule(
                kind="fixed_date",
                effective_date=effective_iso,
            ),
            group_id=event_group_id,
        )
