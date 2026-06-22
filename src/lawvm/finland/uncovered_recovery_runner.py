"""Stateful candidate runner for Finnish uncovered-body recovery."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.apply_ir_ops import _relabel_section_ir
from lawvm.finland.constraints import DEBUG
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.merge import merge_section_with_omission_invariants
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.uncovered_dispose import (
    ExistingDisposition,
    classify_existing_disposition,
    compute_replace_decision,
    evaluate_omission_merge,
    evaluate_past_repeal_guard,
)
from lawvm.finland.uncovered_recovery_state import (
    RecoveryState,
    UncoveredRecoveryGuards,
)
from lawvm.finland.uncovered_recovery_support import (
    ChapterPayloadOutcome,
    ChapterPayloadOwnershipRequest,
    ExistingSectionCandidate,
    NewSectionCandidate,
    PreGuardRequest,
    UncoveredRopDraft,
    _build_uncovered_rop,
    _evaluate_chapter_payload_ownership,
    _evaluate_pre_guards,
    _next_letter_label,
    _part_label_from_path,
    _section_heading_text,
    _uncovered_disposition_for_op_id,
    merge_group_ops_for_section,
)
from lawvm.finland.uncovered_recovery_iteration import UncoveredSectionCandidate
from lawvm.finland.uncovered_target_resolve import (
    TargetVerdict,
    resolve_insert_chapter,
    resolve_target,
)
from lawvm.finland.table_target_merge import merge_numbered_table_targets_into_live_section

if TYPE_CHECKING:
    from lawvm.finland.source_model import AmendmentSourceModel
    from lawvm.finland.source_model import SourcePayloadLookupResult
    from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UncoveredRecoveryRun:
    """Sequential uncovered-body recovery candidate runner.

    The candidate loop is intentionally stateful: each recovered or covered
    section affects the guards seen by later body sections. Keeping that loop in
    one object makes the live state, johto gates, pairing verdicts, and recovery
    ledger explicit instead of relying on closure capture.
    """

    state: "ReplayState"
    source_model: "AmendmentSourceModel"
    ops: List[AmendmentOp]
    amendment_id: str
    future_repeals: Optional[Set["RepealTargetRef"]]
    new_chapter_labels: Optional[Set[str]]
    has_content_ops: bool
    rstate: RecoveryState
    recovery_guards: UncoveredRecoveryGuards
    bp_assignments: object
    johto_mentioned_labels: Set[str]
    johto_moment_targets: Dict[str, frozenset[int]]
    johto_numbered_table_targets: Dict[str, frozenset[str]]
    johto_mentioned_replaced_chapters: Set[str]
    moved_section_destinations: Dict[str, str]
    owned_chapter_labels: Set[str]
    source_owned_insert_chapter_labels: Set[str]
    part_insert_labels: Set[str]
    johto_whole_section_targets: Set[str]
    johto_insert_section_targets: Set[str]
    johto_named_subprovision_section_targets: Set[str]
    johto_insert_subsection_section_targets: Set[str]

    def record_skip(
        self,
        reason: str,
        label: str,
        amend_chapter_label: Optional[str],
        amend_part_label: Optional[str] = None,
    ) -> None:
        self.rstate.record_skip(reason, label, amend_chapter_label, amend_part_label)

    def is_future_repealed(self, label: str, chapter: Optional[str]) -> bool:
        """Whether a later amendment explicitly repeals this section."""
        if self.future_repeals is None:
            return False
        if RepealTargetRef.section(label) in self.future_repeals:
            return True
        if chapter and RepealTargetRef.section(label, chapter) in self.future_repeals:
            return True
        return False

    def label_allowed_by_johto(
        self,
        label: str,
        chapter: Optional[str] = None,
        amend_part_label: Optional[str] = None,
    ) -> bool:
        if not self.johto_mentioned_labels:
            return True
        if amend_part_label and amend_part_label in self.part_insert_labels:
            return True
        if chapter and chapter in self.owned_chapter_labels:
            return True
        if chapter and chapter in self.johto_mentioned_replaced_chapters:
            return True
        if label in self.johto_mentioned_labels:
            return True
        # lawvm-regex: prefilter numeric base-label extraction from a section label for the johto-allowlist check; pure label-token lex, no source text
        base_label = re.match(r"^(\d+)", label)
        return bool(base_label and base_label.group(1) in self.johto_mentioned_labels)

    def label_has_whole_section_johto_target(self, label: str) -> bool:
        """Whether the preamble parses this label as a whole-section target."""
        return _norm_num_token(label) in self.johto_whole_section_targets

    def label_has_section_insert_johto_target(self, label: str) -> bool:
        """Whether the preamble declares ``lisätään ... uusi N §`` for this label."""
        return _norm_num_token(label) in self.johto_insert_section_targets

    def label_has_subsection_insert_johto_target(self, label: str) -> bool:
        """Whether the preamble inserts subsection(s) into this section."""
        return _norm_num_token(label) in self.johto_insert_subsection_section_targets

    def is_declared_move_destination(self, label: str, chapter: Optional[str]) -> bool:
        """Whether the source preamble declares this section moved to chapter."""
        if chapter is None:
            return False
        destination = self.moved_section_destinations.get(_norm_num_token(label))
        return destination is not None and _norm_num_token(destination) == _norm_num_token(chapter)

    def make_uncovered_rop(self, draft: UncoveredRopDraft) -> ResolvedOp:
        return _build_uncovered_rop(
            draft,
            amendment_id=self.amendment_id,
            op_source=self.rstate.op_source,
        )

    def append_recovered_rop(self, rop: ResolvedOp) -> None:
        disposition, reason = _uncovered_disposition_for_op_id(rop.op_id or "")
        self.rstate.append_recovered_rop(rop, disposition=disposition, reason=reason)

    def section_payload_ir(self, candidate: UncoveredSectionCandidate) -> IRNode | None:
        """Resolve one candidate's section payload through the source model."""
        return self.section_payload_lookup(candidate).payload_ir

    def section_payload_lookup(
        self,
        candidate: UncoveredSectionCandidate,
    ) -> "SourcePayloadLookupResult":
        """Resolve one candidate's section payload and neighboring heading witness."""
        payload_lookup = self.source_model.lookup_payload_ir_for_coverage_ref(candidate.source_ref)
        return payload_lookup

    def process_section_candidate(self, candidate: UncoveredSectionCandidate) -> None:
        """Process one uncovered section candidate and commit a typed disposition."""
        label = candidate.label
        amend_chapter_label = candidate.amend_chapter_label
        amend_part_label = candidate.amend_part_label
        debug_recovery = os.environ.get("LAWVM_DEBUG_RECOVERY") == "1"
        if debug_recovery:
            print(
                f"  [DBG] _process_section_candidate: "
                f"label={label!r}, chapter={amend_chapter_label!r}"
            )

        pre = _evaluate_pre_guards(
            PreGuardRequest(
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
                guards=self.recovery_guards,
                already_recovered=self.rstate.already_recovered(
                    section=label, chapter=amend_chapter_label
                ),
                moved_section_destinations=self.moved_section_destinations,
                bp_assignments=self.bp_assignments,
            )
        )
        if not pre.proceed:
            assert pre.skip_reason is not None
            if debug_recovery:
                print(
                    f"  [DBG]  -> SKIP ({pre.skip_reason}): "
                    f"{label!r} in chapter {amend_chapter_label!r}"
                )
            if pre.skip_reason == "body_pairing_guard":
                logger.debug(
                    "  [%s] uncovered SKIP %s § — body-pairing guard (foreign/unmatched/repeal)",
                    self.amendment_id,
                    label,
                )
            self.record_skip(
                pre.skip_reason,
                label,
                amend_chapter_label,
                amend_part_label if pre.with_part else None,
            )
            return

        payload = _evaluate_chapter_payload_ownership(
            ChapterPayloadOwnershipRequest(
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
                guards=self.recovery_guards,
                section_present_in_chapter=(
                    bool(amend_chapter_label)
                    and self.state.find_section_path(label, amend_chapter_label, amend_part_label)
                    is not None
                ),
                future_repealed=self.is_future_repealed(label, amend_chapter_label),
            )
        )
        if payload.outcome is not ChapterPayloadOutcome.NOT_APPLICABLE:
            assert amend_chapter_label is not None
            if debug_recovery:
                print(
                    f"  [DBG]  -> chapter-payload {payload.outcome.value}: section "
                    f"{label!r} in chapter {amend_chapter_label!r}"
                )
            if payload.outcome is ChapterPayloadOutcome.ADOPT:
                adopt_payload = self.section_payload_lookup(candidate)
                adopt_sec_ir = adopt_payload.payload_ir
                if adopt_sec_ir is None:
                    self.record_skip(
                        "source_payload_missing",
                        label,
                        amend_chapter_label,
                        amend_part_label,
                    )
                    return
                self.rstate.mark_covered(
                    part=amend_part_label,
                    chapter=amend_chapter_label,
                    section=label,
                )
                declared_move_destination = self.is_declared_move_destination(
                    label,
                    amend_chapter_label,
                )
                self.append_recovered_rop(
                    self.make_uncovered_rop(
                        UncoveredRopDraft(
                            op_type="REPLACE" if declared_move_destination else "INSERT",
                            target_label=label,
                            target_chapter=amend_chapter_label,
                            target_part=amend_part_label,
                            muutos_ir=adopt_sec_ir,
                            cross_ir=adopt_payload.cross_heading_ir,
                            op_id=(
                                f"uncovered_move_replace_{label}"
                                if declared_move_destination
                                else f"uncov_chapter_adopt_{label}"
                            ),
                            move_clause_target_unit_kind=(
                                "chapter" if declared_move_destination else None
                            ),
                        )
                    )
                )
                self.rstate.note_chapter_disposition(amend_chapter_label, "adopted")
            elif payload.outcome is ChapterPayloadOutcome.OWNED:
                self.rstate.mark_covered(
                    part=amend_part_label,
                    chapter=amend_chapter_label,
                    section=label,
                )
                self.rstate.note_chapter_disposition(amend_chapter_label, "owned")
                self.record_skip("chapter_payload_owned", label, amend_chapter_label)
            else:
                self.record_skip("future_repeal", label, amend_chapter_label)
            return

        resolved = resolve_target(
            label,
            amend_chapter_label,
            amend_part_label,
            self.state,
            self.owned_chapter_labels,
        )
        if resolved.verdict is TargetVerdict.AMBIGUOUS:
            self.record_skip("ambiguous_duplicate_label_no_chapter", label, amend_chapter_label)
            return

        payload_lookup = self.section_payload_lookup(candidate)
        sec_ir = payload_lookup.payload_ir
        if sec_ir is None:
            self.record_skip(
                "source_payload_missing",
                label,
                amend_chapter_label,
                amend_part_label,
            )
            return
        if resolved.existing_path is not None:
            existing = _tops.resolve(self.state.ir, resolved.existing_path)
            if existing is not None:
                self.process_existing_section(
                    ExistingSectionCandidate(
                        existing=existing,
                        existing_path=resolved.existing_path,
                        sec_ir=sec_ir,
                        cross_ir=payload_lookup.cross_heading_ir,
                        label=label,
                        amend_chapter_label=amend_chapter_label,
                        amend_part_label=amend_part_label,
                        cross_chapter=resolved.cross_chapter,
                    )
                )
                return

        self.process_new_section(
            NewSectionCandidate(
                sec_ir=sec_ir,
                cross_ir=payload_lookup.cross_heading_ir,
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
            )
        )

    def process_existing_section(self, candidate: ExistingSectionCandidate) -> None:
        """Commit the disposition for a candidate with a resolvable live section."""
        existing = candidate.existing
        existing_path = candidate.existing_path
        sec_ir = candidate.sec_ir
        cross_ir = candidate.cross_ir
        label = candidate.label
        amend_chapter_label = candidate.amend_chapter_label
        amend_part_label = candidate.amend_part_label
        cross_chapter = candidate.cross_chapter
        existing_heading = _section_heading_text(existing)
        amend_heading = _section_heading_text(sec_ir)
        if (
            existing_heading.startswith("voimaantulo")
            and amend_heading
            and not amend_heading.startswith("voimaantulo")
            and not self.label_has_section_insert_johto_target(label)
        ):
            parent_path = existing_path[:-1]
            parent = _tops.resolve(self.state.ir, parent_path) if parent_path else self.state.ir
            section_siblings = (
                [c for c in parent.children if c.kind is IRNodeKind.SECTION]
                if parent is not None
                else []
            )
            insert_label: Optional[str] = None
            if existing in section_siblings:
                existing_idx = section_siblings.index(existing)
                if existing_idx > 0:
                    insert_label = _next_letter_label(section_siblings[existing_idx - 1].label or "")
            if (
                insert_label
                and self.state.find_section_path(insert_label, amend_chapter_label) is None
            ):
                inserted_sec = _relabel_section_ir(sec_ir, insert_label)
                self.recovery_guards.mark_covered(
                    part=amend_part_label,
                    chapter=amend_chapter_label,
                    section=label,
                )
                self.append_recovered_rop(
                    self.make_uncovered_rop(
                        UncoveredRopDraft(
                            op_type="INSERT",
                            target_label=insert_label,
                            target_chapter=amend_chapter_label,
                            target_part=amend_part_label or _part_label_from_path(existing_path),
                            muutos_ir=inserted_sec,
                            cross_ir=cross_ir,
                            op_id=f"uncovered_insert_{insert_label}",
                        )
                    )
                )
                return

        if not self.label_allowed_by_johto(
            label, amend_chapter_label, amend_part_label=amend_part_label
        ):
            self.record_skip("johto_guard", label, amend_chapter_label)
            return

        whole_ch_replace = bool(
            amend_chapter_label
            and amend_chapter_label in self.johto_mentioned_replaced_chapters
        )
        prv = evaluate_past_repeal_guard(
            existing.attrs,
            self.ops,
            label,
            amend_chapter_label,
            whole_ch_replace,
            amend_part=amend_part_label,
            part_insert_labels=self.part_insert_labels,
        )
        if prv.applies and not prv.bypass:
            self.recovery_guards.mark_covered(
                part=amend_part_label,
                chapter=amend_chapter_label,
                section=label,
            )
            self.record_skip("past_repeal_placeholder_guard", label, amend_chapter_label)
            return
        if prv.applies:
            logger.debug(
                "  [%s] uncovered: bypassing past-repeal guard for %s § (%s)",
                self.amendment_id,
                label,
                prv.bypass_reason,
            )

        rdec = compute_replace_decision(
            sec_ir, existing, self.has_content_ops, cross_chapter, whole_ch_replace
        )
        edisp = classify_existing_disposition(
            sec_ir, rdec, self.has_content_ops, cross_chapter
        )
        if os.environ.get("LAWVM_DEBUG_RECOVERY") == "1":
            print(
                f"  [DBG]  existing disposition={edisp.outcome.value}, "
                f"has_content_ops={self.has_content_ops}, has_omissions={rdec.has_omissions}, "
                f"cross_chapter={cross_chapter}, would_lose={rdec.would_lose_subsections}, "
                f"whole_ch_replace={whole_ch_replace}, amend_ss={rdec.amend_subsec_count}, "
                f"master_ss={rdec.master_subsec_count}"
            )

        self.recovery_guards.mark_covered(
            part=amend_part_label,
            chapter=amend_chapter_label,
            section=label,
        )
        table_labels = self.johto_numbered_table_targets.get(_norm_num_token(label), frozenset())
        if table_labels and not self.johto_moment_targets.get(_norm_num_token(label)):
            table_merge = merge_numbered_table_targets_into_live_section(
                existing,
                sec_ir,
                table_labels,
            )
            if table_merge.rewritten and table_merge.node is not None:
                self.append_recovered_rop(
                    self.make_uncovered_rop(
                        UncoveredRopDraft(
                            op_type="REPLACE",
                            target_label=label,
                            target_chapter=amend_chapter_label,
                            target_part=amend_part_label or _part_label_from_path(existing_path),
                            muutos_ir=table_merge.node,
                            cross_ir=cross_ir,
                            op_id=f"uncovered_table_merge_{label}",
                        )
                    )
                )
                return
        if edisp.outcome is ExistingDisposition.REPLACE:
            self.append_recovered_rop(
                self.make_uncovered_rop(
                    UncoveredRopDraft(
                        op_type="REPLACE",
                        target_label=label,
                        target_chapter=amend_chapter_label,
                        target_part=amend_part_label or _part_label_from_path(existing_path),
                        muutos_ir=sec_ir,
                        cross_ir=cross_ir,
                        op_id=f"uncovered_replace_{label}",
                    )
                )
            )
        elif edisp.outcome is ExistingDisposition.MERGE_CANDIDATE:
            group_ops = merge_group_ops_for_section(
                self.ops,
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
                johto_moment_targets=self.johto_moment_targets,
            )
            if (
                not group_ops
                and not self.label_has_whole_section_johto_target(label)
                and not self.label_has_subsection_insert_johto_target(label)
            ):
                if _norm_num_token(label) in self.johto_named_subprovision_section_targets:
                    self.record_skip(
                        "omission_merge_special_subprovision_scope",
                        label,
                        amend_chapter_label,
                    )
                    return
                self.record_skip("omission_merge_missing_scope", label, amend_chapter_label)
                return
            merge_result = merge_section_with_omission_invariants(
                existing,
                sec_ir,
                group_ops=group_ops or None,
                source_statute=self.amendment_id,
                op_id=f"uncovered_merge_{label}",
                findings_out=self.rstate.findings_out,
            )
            if merge_result is not None:
                merged = merge_result.node
                mdec = evaluate_omission_merge(merged, existing)
                if mdec.accept:
                    self.append_recovered_rop(
                        self.make_uncovered_rop(
                            UncoveredRopDraft(
                                op_type="REPLACE",
                                target_label=label,
                                target_chapter=amend_chapter_label,
                                target_part=amend_part_label or _part_label_from_path(existing_path),
                                muutos_ir=merged,
                                cross_ir=cross_ir,
                                op_id=f"uncovered_merge_{label}",
                            )
                        )
                    )
                elif mdec.skip_reason is not None:
                    self.record_skip(f"omission_merge_{mdec.skip_reason}", label, amend_chapter_label)
            else:
                self.record_skip("omission_merge_failed", label, amend_chapter_label)
        elif (
            edisp.skip_reason is not None
            and edisp.outcome is not ExistingDisposition.SKIP_BLOCKED
        ):
            self.record_skip(edisp.skip_reason, label, amend_chapter_label)

    def process_new_section(self, candidate: NewSectionCandidate) -> None:
        """Commit the disposition for a candidate without a live target."""
        sec_ir = candidate.sec_ir
        cross_ir = candidate.cross_ir
        label = candidate.label
        amend_chapter_label = candidate.amend_chapter_label
        amend_part_label = candidate.amend_part_label
        if not self.label_allowed_by_johto(
            label, amend_chapter_label, amend_part_label=amend_part_label
        ):
            self.record_skip("johto_guard", label, amend_chapter_label)
            return

        if self.is_future_repealed(label, amend_chapter_label):
            if DEBUG:
                _replay_print(
                    f"  [{self.amendment_id}] uncovered SKIP INSERT {label} § — future repeal"
                )
            self.recovery_guards.mark_covered(
                part=amend_part_label,
                chapter=amend_chapter_label,
                section=label,
            )
            self.record_skip("future_repeal", label, amend_chapter_label)
            return

        insert_ch = resolve_insert_chapter(
            label,
            amend_chapter_label,
            amend_part_label,
            self.state,
            self.ops,
            self.new_chapter_labels,
            self.owned_chapter_labels,
            self.source_owned_insert_chapter_labels,
            self.part_insert_labels,
        )
        effective_chapter = insert_ch.effective_chapter
        effective_part = insert_ch.effective_part
        if insert_ch.reason == "family_base_override":
            logger.debug(
                "  [%s] uncovered INSERT %s: overriding chapter %s->%s"
                " (family base in unrelated existing chapter)",
                self.amendment_id,
                label,
                amend_chapter_label,
                effective_chapter,
            )

        self.recovery_guards.mark_covered(
            part=amend_part_label,
            chapter=effective_chapter,
            section=label,
        )
        self.append_recovered_rop(
            self.make_uncovered_rop(
                UncoveredRopDraft(
                    op_type="INSERT",
                    target_label=label,
                    target_chapter=effective_chapter,
                    target_part=effective_part,
                    muutos_ir=sec_ir,
                    cross_ir=cross_ir,
                    op_id=f"uncovered_insert_{label}",
                )
            )
        )


_UncoveredRecoveryRun = UncoveredRecoveryRun
