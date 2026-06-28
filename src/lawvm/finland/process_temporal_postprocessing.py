"""Late temporal and kumotaan replay-product postprocessing for Finland.

This module owns process-level rewrites that happen after normal amendment
compilation but before the process result is materialized: commencement/expiry
metadata rewrites, law-level text patch collection, and pure kumotaan repeal
operation injection. It mutates caller-owned report/op streams deliberately; it
does not apply tree mutations itself.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Set

from lawvm.core.temporal import ActivationRule, TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.johtolause import extract_law_level_text_patch_los as _extract_law_level_patch_los
from lawvm.finland.kumotaan import (
    _extract_kumotaan_chapter_section_map,
    _extract_kumotaan_subsection_refs,
    kumotaan_recycle_guard_result,
)
from lawvm.finland.kumotaan_replay import (
    PureKumotaanInjectedRepeal,
    _inject_pure_kumotaan_repeal_ops,
    _inject_pure_kumotaan_subsection_repeal_ops,
    _live_suffix_section_labels_for_numeric_kumotaan_ranges,
    _rewrite_kumotaan_snapshot_replaces_to_repeal,
)
from lawvm.finland.effect_lifecycle_signals import (
    EffectLifecycleOverride,
    EffectLifecycleOverrideScope,
)
from lawvm.finland.helpers import _parse_iso_date
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.temporal_rewrites import (
    _rewrite_compiled_op_activation_rule_effective,
    _rewrite_compiled_op_activation_rule_effective_for_chapters,
    _rewrite_compiled_op_activation_rule_effective_for_addresses,
    _rewrite_compiled_op_activation_rule_effective_for_address_suffixes,
    _rewrite_later_effective_lo_groups,
    _rewrite_lo_op_group_id,
    _rewrite_lo_op_source_effective,
    _rewrite_lo_op_source_effective_for_chapters,
    _rewrite_lo_op_source_effective_for_address_suffixes,
    _rewrite_lo_op_source_expiry,
    _rewrite_temporal_event_expiry_for_addresses,
)

if TYPE_CHECKING:
    from lawvm.finland.metadata import TemporarySectionExpiryOverride
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
    source_model: AmendmentSourceModel
    base_ir: IRNode
    state: ReplayState
    replay_mode: str
    amendment_issue_date: Optional[dt.date]
    amendment_effective_date: Optional[dt.date]
    lo_ops_out: Optional[List[_LegalOperation]]
    compiled_ops_out: Optional[List[Dict[str, object]]]
    amendment_temporal_events: list[TemporalEvent]
    commencement_expiry_override_notes: list[EffectLifecycleOverride]
    process_findings: list[Finding]
    record_finding: RecordProcessFinding
    replay_print: ReplayPrint
    section_expiry_overrides: tuple[TemporarySectionExpiryOverride, ...] = ()

    def run(self) -> None:
        self.collect_law_level_text_patches()
        self.apply_commencement_expiry_overrides()
        self.apply_chapter_commencement_overrides()
        self.apply_section_commencement_overrides()
        self.apply_later_effective_group_rewrites()
        self.apply_kumotaan_replay_product_rewrites()
        self.reconcile_temporal_occupancy_observations()

    def reconcile_temporal_occupancy_observations(self) -> None:
        """Add bounded-window evidence after source expiry rewrites are known."""
        self.process_findings.extend(
            _temporal_occupancy_reconciliation_findings(
                self.lo_ops_out,
                self.process_findings,
                amendment_id=self.amendment_id,
            )
        )

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
        #
        # Legal-state authority is the typed commencement/expiry surface
        # ``commencement_expiry_override`` (it owns the voimaantulosäännös parse
        # and the foreign-scoped section-expiry decision), never a raw-text
        # substring predicate (AGENTS §1.11/§1.12, leak-ledger rank 15). The
        # former raw-text prefilter on the commencement-clause keyword could not
        # change the result — the typed parser's own ``voimaantulosäänn`` match
        # already implies that keyword, and the foreign-scoped branch is carried
        # by ``section_expiry_overrides`` — so it is dropped and the typed
        # surface is consulted unconditionally.
        accepted = self.source_model.commencement_expiry_override(
            self.amendment_id,
            section_expiry_overrides=self.section_expiry_overrides,
        )
        if accepted is not None:
            target_mid = accepted.target_mid
            labels = accepted.labels
            expiry = accepted.expiry
            fallback_effective = accepted.fallback_effective
            if target_mid != self.amendment_id:
                self.commencement_expiry_override_notes.append(
                    EffectLifecycleOverride(
                        source_statute=self.amendment_id,
                        target_statute=target_mid,
                        scope=_section_override_scope(labels),
                        expiry=expiry.isoformat(),
                        context="accepted_amendment",
                    )
                )
            touched_addresses: list[LegalAddress] = []
            if target_mid != self.amendment_id and _rewrite_lo_op_source_expiry(
                self.lo_ops_out,
                target_mid,
                labels,
                expiry,
                parent_statute_id=self.parent_id,
                replay_mode=self.replay_mode,
                fallback_effective=fallback_effective,
                expiry_convention="inclusive_prose",
                touched_addresses_out=touched_addresses,
            ):
                _rewrite_temporal_event_expiry_for_addresses(
                    self.amendment_temporal_events,
                    target_mid,
                    tuple(touched_addresses),
                    expiry,
                    replay_mode=self.replay_mode,
                    expiry_convention="inclusive_prose",
                )
                scope = sorted(labels) if labels else ["*"]
                self.replay_print(
                    f"  [{self.amendment_id}] voimaantulo_expiry_override (accepted): "
                    f"{target_mid} {scope} -> {expiry.isoformat()}"
                )

        for override in self.section_expiry_overrides:
            target_mid = override.target_mid
            labels = override.labels
            expiry = override.expiry
            if target_mid == self.amendment_id:
                self.commencement_expiry_override_notes.append(
                    EffectLifecycleOverride(
                        source_statute=self.amendment_id,
                        target_statute=target_mid,
                        scope=_section_override_scope(labels),
                        expiry=expiry.isoformat(),
                        context="accepted_section_temporary",
                    )
                )
            touched_addresses = []
            if target_mid == self.amendment_id and _rewrite_lo_op_source_expiry(
                self.lo_ops_out,
                target_mid,
                labels,
                expiry,
                parent_statute_id=self.parent_id,
                replay_mode=self.replay_mode,
                expiry_convention="inclusive_prose",
                touched_addresses_out=touched_addresses,
            ):
                _rewrite_temporal_event_expiry_for_addresses(
                    self.amendment_temporal_events,
                    target_mid,
                    tuple(touched_addresses),
                    expiry,
                    replay_mode=self.replay_mode,
                    expiry_convention="inclusive_prose",
                )
                scope = sorted(labels) if labels else ["*"]
                self.replay_print(
                    f"  [{self.amendment_id}] temporary_section_expiry_override (accepted): "
                    f"{target_mid} {scope} -> {expiry.isoformat()}"
                )

    def apply_chapter_commencement_overrides(self) -> None:
        overrides = self.source_model.chapter_commencement_effective_overrides(self.amendment_id)
        for index, (target_mid, chapter_labels, effective) in enumerate(overrides, start=1):
            scoped_group_id = (
                f"finland-johto:{self.amendment_id}:chapter_commencement:{index}"
            )
            scoped_addresses = _rewrite_lo_op_source_effective_for_chapters(
                self.lo_ops_out,
                target_mid,
                effective,
                chapter_labels=chapter_labels,
                new_group_id=scoped_group_id,
                base_ir=self.base_ir,
            )
            compiled_updated = _rewrite_compiled_op_activation_rule_effective_for_chapters(
                self.compiled_ops_out,
                target_mid,
                effective,
                chapter_labels=chapter_labels,
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
                scope = sorted(chapter_labels)
                self.replay_print(
                    f"  [{self.amendment_id}] chapter_commencement_effective_override "
                    f"(accepted): {target_mid} {scope} -> {effective.isoformat()}"
                )
            if scoped_addresses:
                self.commencement_expiry_override_notes.append(
                    EffectLifecycleOverride(
                        source_statute=self.amendment_id,
                        target_statute=target_mid,
                        scope=EffectLifecycleOverrideScope.exact_addresses(scoped_addresses),
                        effective=effective.isoformat(),
                        context="accepted_chapter_commencement",
                    )
                )

    def apply_section_commencement_overrides(self) -> None:
        override = self.source_model.section_commencement_effective_override(self.amendment_id)
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
                    EffectLifecycleOverride(
                        source_statute=self.amendment_id,
                        target_statute=target_mid,
                        scope=_chapter_section_override_scope(chapter_section_map),
                        effective=effective.isoformat(),
                        context="accepted_section_commencement",
                    )
                )

        subsection_override = self.source_model.section_subsection_commencement_effective_override(
            self.amendment_id
        )
        if subsection_override is not None:
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
                    EffectLifecycleOverride(
                        source_statute=self.amendment_id,
                        target_statute=target_mid,
                        scope=EffectLifecycleOverrideScope.exact_addresses(scoped_addresses),
                        effective=effective.isoformat(),
                        context="accepted_subsection_commencement",
                    )
                )

        application_override = (
            self.source_model.section_subsection_application_commencement_effective_override(
                self.amendment_id
            )
        )
        if application_override is None:
            return
        target_mid, address_suffixes, effective = application_override
        scoped_group_id = f"finland-johto:{self.amendment_id}:subsection_application_commencement"
        scoped_addresses = _rewrite_lo_op_source_effective_for_address_suffixes(
            self.lo_ops_out,
            target_mid,
            effective,
            address_suffixes=address_suffixes,
            new_group_id=scoped_group_id,
            include_payload_carriers=True,
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
                f"  [{self.amendment_id}] subsection_application_commencement_effective_override "
                f"(accepted): {target_mid} {scope} -> {effective.isoformat()}"
            )
            self.commencement_expiry_override_notes.append(
                EffectLifecycleOverride(
                    source_statute=self.amendment_id,
                    target_statute=target_mid,
                    scope=EffectLifecycleOverrideScope.exact_addresses(scoped_addresses),
                    effective=effective.isoformat(),
                    context="accepted_subsection_application_commencement",
                )
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
                kind="PARSE.REPEAL_RECYCLE_GUARD",
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

        kumotaan_expiry_groups = _kumotaan_labels_by_effective_date(
            self.lo_ops_out,
            labels=kumotaan_labels,
            amendment_id=self.amendment_id,
            default_effective_date=self.amendment_effective_date,
        )
        for expiry_date, labels in sorted(kumotaan_expiry_groups.items()):
            self.commencement_expiry_override_notes.append(
                EffectLifecycleOverride(
                    source_statute=self.amendment_id,
                    target_statute=self.amendment_id,
                    scope=_kumotaan_override_scope(
                        labels,
                        _filter_chapter_section_map(chap_map_sets, labels),
                    ),
                    expiry=expiry_date.isoformat(),
                    context="repeal_clause",
                )
            )
        for expiry_date, labels in sorted(kumotaan_expiry_groups.items()):
            label_set = set(labels)
            if not _rewrite_lo_op_source_expiry(
                self.lo_ops_out,
                self.amendment_id,
                label_set,
                expiry_date,
                parent_statute_id=self.parent_id,
                replay_mode=self.replay_mode,
                chapter_section_map=_filter_chapter_section_map(chap_map_sets, labels),
                expiry_convention="exclusive_cutoff",
            ):
                continue
            scope = sorted(label_set)
            self.replay_print(
                f"  [{self.amendment_id}] kumotaan_section_expiry_override: "
                f"{self.amendment_id} {scope} -> {expiry_date.isoformat()}"
            )
        if kumotaan_expiry_groups:
            _rewrite_kumotaan_snapshot_replaces_to_repeal(
                self.lo_ops_out,
                target_source_statute=self.amendment_id,
                section_labels={label.lower() for label in kumotaan_labels},
                chapter_section_map=chap_map_sets,
                source_raw_text=self.johto,
            )

        if kumotaan_labels:
            pure_result = _inject_pure_kumotaan_repeal_ops(
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
            _rewrite_delayed_kumotaan_injected_ops(
                self.lo_ops_out,
                amendment_id=self.amendment_id,
                default_effective_date=self.amendment_effective_date,
                expiry_groups=kumotaan_expiry_groups,
                base_ir=self.base_ir,
                group_id_prefix=f"finland-johto:{self.amendment_id}:kumotaan_commencement",
                chapter_section_map=chap_map_sets,
            )
            self._emit_pure_kumotaan_injection_findings(pure_result.injected)
            pure_count = pure_result.injected_count
            if pure_count:
                self.replay_print(f"  [{self.amendment_id}] pure_kumotaan_repeal_injected: {pure_count} section(s)")

        johto_for_subsection = self.johto
        if not _extract_kumotaan_subsection_refs(self.johto):
            body_repeal = self.source_model.operative_body_repeal_candidate()
            if body_repeal:
                johto_for_subsection = self.johto + " " + body_repeal
        subsection_map = _extract_kumotaan_subsection_refs(johto_for_subsection)
        if not subsection_map:
            return
        pure_subsection_result = _inject_pure_kumotaan_subsection_repeal_ops(
            self.lo_ops_out,
            amendment_id=self.amendment_id,
            source_title=self.source_title,
            kumotaan_subsection_map=subsection_map,
            amendment_effective_date=self.amendment_effective_date,
            amendment_issue_date=self.amendment_issue_date,
            state=self.state,
            source_raw_text=johto_for_subsection,
        )
        for skipped in pure_subsection_result.skipped_targets:
            self.record_finding(
                kind="APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED",
                message="Pure kumotaan subsection injection skipped an unresolved target.",
                source_statute=self.amendment_id,
                detail={
                    "message": "Pure kumotaan subsection injection skipped an unresolved target.",
                    **skipped.finding_detail(),
                },
                role="observation",
                blocking=False,
            )
        self._emit_pure_kumotaan_injection_findings(pure_subsection_result.injected)
        pure_subsection_count = pure_subsection_result.injected_count
        if pure_subsection_count:
            self.replay_print(
                f"  [{self.amendment_id}] pure_kumotaan_subsection_repeal_injected: "
                f"{pure_subsection_count} subsection(s)"
            )

    def _emit_pure_kumotaan_injection_findings(
        self,
        injected: tuple[PureKumotaanInjectedRepeal, ...],
    ) -> None:
        """Record one witnessed finding per repeal reconstructed from raw johtolause.

        The repeal op was minted from raw kumotaan source text because the typed
        pipeline produced no op for the target. Each injected op carries a
        witness_rule_id; this surfaces the matching evidence record so the mint
        is never a silent legal-state move.
        """
        for record in injected:
            self.record_finding(
                kind="PARSE.PURE_REPEAL_CLAUSE_RECONSTRUCTED",
                message=(
                    "Repeal reconstructed from raw kumotaan johtolause; the typed "
                    "pipeline produced no op for this target."
                ),
                source_statute=self.amendment_id,
                detail={
                    "message": (
                        "Repeal reconstructed from raw kumotaan johtolause; the "
                        "typed pipeline produced no op for this target."
                    ),
                    **record.finding_detail(),
                },
                role="observation",
                blocking=False,
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


def _section_override_scope(labels: AbstractSet[str] | None) -> EffectLifecycleOverrideScope:
    return EffectLifecycleOverrideScope.sections(sorted(labels or ()))


def _chapter_section_override_scope(
    chapter_section_map: Dict[Optional[str], Set[str]]
) -> EffectLifecycleOverrideScope:
    addresses: list[LegalAddress] = []
    for chapter, sections in sorted(
        chapter_section_map.items(), key=lambda item: str(item[0] or "")
    ):
        for section in sorted(sections):
            path: list[tuple[str, str]] = []
            if chapter:
                path.append(("chapter", str(chapter)))
            path.append(("section", str(section)))
            addresses.append(LegalAddress(path=tuple(path)))
    return EffectLifecycleOverrideScope.exact_addresses(addresses)


def _kumotaan_override_scope(
    labels: list[str],
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]],
) -> EffectLifecycleOverrideScope:
    if not chapter_section_map:
        return EffectLifecycleOverrideScope.sections(sorted(set(labels)))

    addresses: list[LegalAddress] = []
    uncovered_labels: list[str] = []
    covered: set[str] = set()
    for chapter, sections in sorted(
        chapter_section_map.items(), key=lambda item: str(item[0] or "")
    ):
        for section in sorted(sections):
            label = str(section)
            covered.add(label.lower())
            if chapter is None:
                uncovered_labels.append(label)
                continue
            path: list[tuple[str, str]] = []
            path.append(("chapter", str(chapter)))
            path.append(("section", label))
            addresses.append(LegalAddress(path=tuple(path)))
    for label in sorted(set(labels)):
        if label.lower() in covered:
            continue
        uncovered_labels.append(str(label))
    return EffectLifecycleOverrideScope.mixed(labels=uncovered_labels, addresses=addresses)


def _section_key(address: LegalAddress) -> tuple[tuple[str, str], ...]:
    return tuple((kind, normalized_label_key(label)) for kind, label in address.path)


def _section_label(address: LegalAddress) -> str:
    return next((label for kind, label in reversed(address.path) if kind == "section"), "")


def _chapter_label(address: LegalAddress) -> str:
    return next((label for kind, label in reversed(address.path) if kind == "chapter"), "")


def _chapter_from_ctx_label(ctx_label: str) -> str:
    if " luku " not in ctx_label:
        return ""
    prefix = ctx_label.split(" luku ", 1)[0].strip()
    if not prefix:
        return ""
    return prefix.rsplit(maxsplit=1)[-1].strip()


def _temporary_occupant_for_prior_history(
    lo_ops: list[_LegalOperation],
    *,
    before_index: int,
    target: LegalAddress,
) -> tuple[str, str] | None:
    key = _section_key(target)
    for prior in reversed(lo_ops[:before_index]):
        if prior.target is None or prior.source is None:
            continue
        if prior.action not in {StructuralAction.INSERT, StructuralAction.REPLACE}:
            continue
        if _section_key(prior.target) != key:
            continue
        if not prior.source.effective:
            return None
        return prior.source.effective, prior.source.statute_id
    return None


@dataclass(frozen=True, slots=True)
class _OccupancyViolationSeed:
    section_norm: str
    chapter_norm: str
    target_label: str
    target_chapter: str
    ctx_label: str
    legacy_action: str
    op_id: str


def _occupancy_violation_seeds(
    findings: list[Finding],
    *,
    amendment_id: str,
) -> tuple[_OccupancyViolationSeed, ...]:
    seeds: list[_OccupancyViolationSeed] = []
    for finding in findings:
        if finding.kind != "APPLY.OCCUPANCY_POLICY_VIOLATION":
            continue
        if finding.source_statute != amendment_id:
            continue
        detail = finding.detail
        if detail.get("allowed_non_primary"):
            continue
        target_label = str(detail.get("target_label") or "")
        if not target_label:
            continue
        target_chapter = str(detail.get("target_chapter") or "")
        if not target_chapter:
            target_chapter = _chapter_from_ctx_label(str(detail.get("ctx_label") or ""))
        seeds.append(
            _OccupancyViolationSeed(
                section_norm=normalized_label_key(target_label),
                chapter_norm=normalized_label_key(target_chapter),
                target_label=target_label,
                target_chapter=target_chapter,
                ctx_label=str(detail.get("ctx_label") or ""),
                legacy_action=str(detail.get("legacy_action") or ""),
                op_id=str(detail.get("op_id") or ""),
            )
        )
    return tuple(seeds)


def _matching_occupancy_violation_seed(
    seeds: tuple[_OccupancyViolationSeed, ...],
    *,
    target_label: str,
    target_chapter: str,
) -> _OccupancyViolationSeed | None:
    target_norm = normalized_label_key(target_label)
    chapter_norm = normalized_label_key(target_chapter)
    for seed in seeds:
        if seed.section_norm != target_norm:
            continue
        if seed.chapter_norm and seed.chapter_norm != chapter_norm:
            continue
        return seed
    return None


def _temporal_occupancy_reconciliation_findings(
    lo_ops_out: Optional[List[_LegalOperation]],
    process_findings: list[Finding],
    *,
    amendment_id: str,
) -> tuple[Finding, ...]:
    """Reconcile early occupancy observations after per-section expiry rewrites.

    Apply-time occupancy runs before Finland commencement/expiry postprocessing
    stamps section-specific temporary windows onto LegalOperation sources. When
    a same-slot insert was recorded as an occupancy concern and the final
    operation stream proves a finite temporary window with a prior occupant,
    emit the same typed temporal-window observation the apply lane would have
    emitted had those bounds already been available.
    """
    if not lo_ops_out:
        return ()
    violation_seeds = _occupancy_violation_seeds(
        process_findings,
        amendment_id=amendment_id,
    )
    if not violation_seeds:
        return ()

    already_recorded: set[tuple[str, str, str, str]] = set()
    for finding in process_findings:
        if finding.kind != "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT":
            continue
        detail = finding.detail
        already_recorded.add(
            (
                finding.source_statute,
                normalized_label_key(str(detail.get("target_label") or "")),
                str(detail.get("incoming_effective") or ""),
                str(detail.get("incoming_expires") or ""),
            )
        )

    notes: list[Finding] = []
    for index, lo in enumerate(lo_ops_out):
        source = lo.source
        if source is None or source.statute_id != amendment_id:
            continue
        if lo.target is None or lo.target.special is not None:
            continue
        if lo.action not in {StructuralAction.INSERT, StructuralAction.REPLACE}:
            continue
        target_label = _section_label(lo.target)
        target_chapter = _chapter_label(lo.target)
        violation_seed = _matching_occupancy_violation_seed(
            violation_seeds,
            target_label=target_label,
            target_chapter=target_chapter,
        )
        if violation_seed is None:
            continue
        if not source.effective or not source.expires or source.expires <= source.effective:
            continue
        occupant = _temporary_occupant_for_prior_history(
            lo_ops_out,
            before_index=index,
            target=lo.target,
        )
        if occupant is None:
            continue
        occupant_effective, occupant_statute = occupant
        if not (
            (source.effective < occupant_effective and source.expires <= occupant_effective)
            or source.effective == occupant_effective
        ):
            continue
        key = (amendment_id, violation_seed.section_norm, source.effective, source.expires)
        if key in already_recorded:
            continue
        already_recorded.add(key)
        rule_id = (
            "temporally_bounded_overlay_insert"
            if source.effective == occupant_effective
            else "temporally_disjoint_twin_insert"
        )
        notes.append(
            Finding(
                kind="APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT",
                role="observation",
                stage="process_muutoslaki",
                source_statute=amendment_id,
                detail={
                    "message": (
                        "Post-temporal expiry rewrite proved a bounded same-slot "
                        "temporary window for an earlier occupancy observation."
                    ),
                    "ctx_label": violation_seed.ctx_label,
                    "op_id": violation_seed.op_id,
                    "legacy_action": violation_seed.legacy_action,
                    "target_label": violation_seed.target_label,
                    "target_chapter": violation_seed.target_chapter,
                    "incoming_effective": source.effective,
                    "incoming_expires": source.expires,
                    "occupant_effective": occupant_effective,
                    "occupant_source_statute": occupant_statute,
                    "rule_id": rule_id,
                    "reconciles_finding": "APPLY.OCCUPANCY_POLICY_VIOLATION",
                },
                blocking=False,
            )
        )
    return tuple(notes)


def _kumotaan_labels_by_effective_date(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    labels: list[str],
    amendment_id: str,
    default_effective_date: dt.date,
) -> dict[dt.date, list[str]]:
    groups: dict[dt.date, list[str]] = {}
    for label in sorted(set(labels)):
        expiry_date = _kumotaan_label_effective_date(
            lo_ops_out,
            label=label,
            amendment_id=amendment_id,
            default_effective_date=default_effective_date,
        )
        groups.setdefault(expiry_date, []).append(label)
    return groups


def _kumotaan_label_effective_date(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    label: str,
    amendment_id: str,
    default_effective_date: dt.date,
) -> dt.date:
    if lo_ops_out is None:
        return default_effective_date
    label_norm = label.lower()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        section = next((value for kind, value in lo.target.path if kind == "section"), "")
        if section.lower() != label_norm or not src.effective:
            continue
        effective_date = _parse_iso_date(src.effective)
        if effective_date is None:
            continue
        if effective_date > default_effective_date:
            return effective_date
    return default_effective_date


def _filter_chapter_section_map(
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]],
    labels: list[str],
) -> Optional[Dict[Optional[str], Set[str]]]:
    if not chapter_section_map:
        return None
    label_set = {label.lower() for label in labels}
    filtered: Dict[Optional[str], Set[str]] = {}
    for chapter, sections in chapter_section_map.items():
        scoped = {section for section in sections if section.lower() in label_set}
        if scoped:
            filtered[chapter] = scoped
    return filtered or None


def _rewrite_delayed_kumotaan_injected_ops(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    amendment_id: str,
    default_effective_date: dt.date,
    expiry_groups: dict[dt.date, list[str]],
    base_ir: Optional[IRNode],
    group_id_prefix: str,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]],
) -> None:
    for effective_date, labels in expiry_groups.items():
        if effective_date <= default_effective_date:
            continue
        scoped_map = _filter_chapter_section_map(chapter_section_map, labels)
        if scoped_map is None:
            scoped_map = {None: {label.lower() for label in labels}}
        _rewrite_lo_op_source_effective(
            lo_ops_out,
            amendment_id,
            effective_date,
            chapter_section_map=scoped_map,
            base_ir=base_ir,
        )
        _rewrite_lo_op_group_id(
            lo_ops_out,
            amendment_id,
            f"{group_id_prefix}:{effective_date.isoformat()}",
            chapter_section_map=scoped_map,
        )
