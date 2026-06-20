"""Source correction and acquisition projection for ``process_muutoslaki``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import lxml.etree as etree

from lawvm.core.compile_result import StrictProfile
from lawvm.core.phase_result import Finding
from lawvm.finland.acquisition import (
    AmendmentAcquisitionResult,
    build_amendment_acquisition_result,
)
from lawvm.finland.corrigendum import extract_inline_corrections, get_patch_table
from lawvm.finland.effect_lifecycle_signals import EffectRelationSignal
from lawvm.finland.process_findings import ProcessFindingRecorder
from lawvm.finland.source_model import AmendmentSourceModel

RecordProcessFinding = Callable[..., Finding]
ReplayPrint = Callable[[str], None]
TreeTitle = Callable[[etree._Element], str]
OperativeStructureCheck = Callable[[etree._Element], tuple[bool, list[str]]]


@dataclass(frozen=True, slots=True)
class ProcessAcquisitionResult:
    source_model: AmendmentSourceModel
    lacks_operative_structure: bool
    operative_tags: tuple[str, ...]
    johto: str
    source_title: str
    acquisition: AmendmentAcquisitionResult
    used_sec1_fallback: bool
    sec1_text: str


@dataclass(slots=True)
class ProcessAcquisitionContext:
    amendment_id: str
    parent_id: str
    parent_title: str
    parent_issue_date: str
    xml_bytes: bytes
    strict_profile: StrictProfile | None
    processed_amendment_titles: dict[str, str]
    effect_relation_signals: list[EffectRelationSignal]
    finding_recorder: ProcessFindingRecorder
    record_finding: RecordProcessFinding
    replay_print: ReplayPrint
    tree_title: TreeTitle
    amendment_lacks_operative_structure: OperativeStructureCheck

    def acquire(self) -> ProcessAcquisitionResult:
        xml_bytes = self._apply_source_corrections(self.xml_bytes)
        muutos_tree = etree.fromstring(xml_bytes)
        source_model = AmendmentSourceModel.from_tree(
            muutos_tree,
            source_ref=self.amendment_id,
            source_bytes=xml_bytes,
        )
        lacks_operative_structure, operative_tags = self.amendment_lacks_operative_structure(muutos_tree)
        source_title = self.tree_title(muutos_tree)
        acquisition = self._build_acquisition(
            xml_bytes=xml_bytes,
            muutos_tree=muutos_tree,
            parent_id=self.parent_id,
            source_title=source_title,
            lacks_operative_structure=lacks_operative_structure,
            operative_tags=operative_tags,
        )
        acquisition = self._compose_pending_target_if_available(
            acquisition=acquisition,
            xml_bytes=xml_bytes,
            muutos_tree=muutos_tree,
            source_title=source_title,
            lacks_operative_structure=lacks_operative_structure,
            operative_tags=operative_tags,
        )
        sec1_text = acquisition.sec1_text
        if acquisition.decision.pre_routing_sec1_requested and sec1_text:
            self.finding_recorder.record_sec1_fallback(
                amendment_id=self.amendment_id,
                stage="pre_routing",
                previous_johto=acquisition.preamble_text or "",
                sec1_fallback_text=sec1_text,
                applied=acquisition.decision.pre_routing_sec1_applied,
            )

        return ProcessAcquisitionResult(
            source_model=source_model,
            lacks_operative_structure=lacks_operative_structure,
            operative_tags=tuple(operative_tags),
            johto=acquisition.decision.chosen_normalized_text,
            source_title=source_title,
            acquisition=acquisition,
            used_sec1_fallback=(
                acquisition.decision.pre_routing_sec1_applied
                or acquisition.decision.post_routing_sec1_applied
            ),
            sec1_text=sec1_text,
        )

    def _apply_source_corrections(self, xml_bytes: bytes) -> bytes:
        # Corrigendum patches (Population B): apply johtolause corrections in
        # both modes. The oracle already has the corrected result; applying the
        # corrigendum to the source johtolause makes PEG target the right provisions.
        # Heuristic #35: gated by strict_profile.allows_source_correction_rules.
        correction_allowed = (
            self.strict_profile is None
            or self.strict_profile.allows_source_correction_rules
        )
        if correction_allowed:
            _, corrected = extract_inline_corrections(xml_bytes, self.amendment_id)
            patch_table = get_patch_table()
            corrected, johtolause_patch_ids = patch_table.patch_source_xml(
                corrected, self.amendment_id
            )
            corrected, body_patch_ids = patch_table.patch_source_body_xml(
                corrected, self.amendment_id
            )
            for op_id in (*johtolause_patch_ids, *body_patch_ids):
                self.finding_recorder.record(
                    kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
                    message="A corrigendum patch corrected amendment source XML before parsing.",
                    source_statute=self.amendment_id,
                    detail={
                        "op_id": op_id,
                        "source_role": "amendment_source_xml",
                        "corrected_by": "corrigendum_patch_table",
                    },
                    role="obligation",
                    blocking=False,
                )
            return corrected

        self.record_finding(
            kind="APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH",
            message="Corrigendum Population B patch rejected by strict profile",
            source_statute=self.amendment_id,
        )
        return xml_bytes

    def _build_acquisition(
        self,
        *,
        xml_bytes: bytes,
        muutos_tree: etree._Element,
        parent_id: str,
        source_title: str,
        parent_issue_date: str | None = None,
        lacks_operative_structure: bool,
        operative_tags: Sequence[str],
    ) -> AmendmentAcquisitionResult:
        return build_amendment_acquisition_result(
            xml_bytes=xml_bytes,
            muutos_tree=muutos_tree,
            parent_id=parent_id,
            amendment_id=self.amendment_id,
            source_title=source_title,
            parent_title=self.parent_title,
            parent_issue_date=self.parent_issue_date if parent_issue_date is None else parent_issue_date,
            strict_profile=self.strict_profile,
            lacks_operative_structure=lacks_operative_structure,
            operative_structure_tags=operative_tags,
        )

    def _compose_pending_target_if_available(
        self,
        *,
        acquisition: AmendmentAcquisitionResult,
        xml_bytes: bytes,
        muutos_tree: etree._Element,
        source_title: str,
        lacks_operative_structure: bool,
        operative_tags: Sequence[str],
    ) -> AmendmentAcquisitionResult:
        if acquisition.decision.route_reason != "pending_amendment_of_parent_skip":
            return acquisition
        pending_target_mid = str(acquisition.decision.route_target_amendment_id or "")
        pending_target_title = str(self.processed_amendment_titles.get(pending_target_mid) or "")
        if not pending_target_mid or not pending_target_title:
            return acquisition

        composed = self._build_acquisition(
            xml_bytes=xml_bytes,
            muutos_tree=muutos_tree,
            parent_id=pending_target_mid,
            source_title=source_title,
            parent_issue_date="",
            lacks_operative_structure=lacks_operative_structure,
            operative_tags=operative_tags,
        )
        self.effect_relation_signals.append(
            EffectRelationSignal.pending_amendment(
                source_statute=self.amendment_id,
                target_statute=pending_target_mid,
                target_title=pending_target_title,
                base_parent_id=self.parent_id,
                message="Pending amendment-of-amendment composed onto already-processed target amendment.",
                source_finding="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
                target_resolution="target_instrument_resolved",
            )
        )
        self.record_finding(
            kind="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
            message="Pending amendment-of-amendment composed onto already-processed target amendment.",
            source_statute=self.amendment_id,
            detail={
                "target_amendment_id": pending_target_mid,
                "target_amendment_title": pending_target_title,
                "base_parent_id": self.parent_id,
                "effect_relation_id": (
                    f"fi-effect-relation:{self.amendment_id}:pending_amendment:{pending_target_mid}"
                ),
                "effect_relation_kind": "modifies_effect",
            },
            role="observation",
            blocking=False,
        )
        self.replay_print(
            f"  [{self.amendment_id}] COMPOSED — pending amendment retargeted "
            f"from {self.parent_id} onto processed target {pending_target_mid}"
        )
        return composed
