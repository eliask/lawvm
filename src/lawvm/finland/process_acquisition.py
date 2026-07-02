"""Source correction and acquisition projection for ``process_muutoslaki``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import lxml.etree as etree

from lawvm.core.compile_result import StrictProfile
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import SourceAnchor
from lawvm.finland.acquisition import (
    AmendmentAcquisitionResult,
    build_amendment_acquisition_result,
)
from lawvm.finland.citation_routing import (
    johtolause_cited_target_ids,
    title_targets_pending_amendment_title,
)
from lawvm.finland.corrigendum import extract_inline_corrections, get_patch_table
from lawvm.finland.effect_lifecycle_signals import EffectRelationSignal
from lawvm.finland.process_findings import ProcessFindingRecorder
from lawvm.finland.source_model import AmendmentSourceModel, SourceMetadataSeed

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
    used_preamble_body_fallback: bool
    sec1_text: str
    source_anchor: "SourceAnchor | None" = None


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
    selection_metadata: SourceMetadataSeed | None = None

    def acquire(self) -> ProcessAcquisitionResult:
        pre_correction_bytes = self.xml_bytes
        xml_bytes, source_patch_op_ids = self._apply_source_corrections(pre_correction_bytes)
        muutos_tree = etree.fromstring(xml_bytes)
        metadata_seed = self.selection_metadata if not source_patch_op_ids else None
        source_model = AmendmentSourceModel.from_tree(
            muutos_tree,
            source_ref=self.amendment_id,
            source_bytes=xml_bytes,
            pre_correction_bytes=pre_correction_bytes,
            metadata_seed=metadata_seed,
        )
        self._record_source_correction_findings(source_model, source_patch_op_ids)
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
            used_preamble_body_fallback=(
                acquisition.decision.pre_routing_sec1_applied
                or acquisition.decision.post_routing_sec1_applied
                or acquisition.decision.preamble_body_lead_combine_applied
            ),
            sec1_text=sec1_text,
            source_anchor=acquisition.source_anchor,
        )

    def _apply_source_corrections(self, xml_bytes: bytes) -> tuple[bytes, tuple[str, ...]]:
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
            return corrected, (*johtolause_patch_ids, *body_patch_ids)

        self.record_finding(
            kind="APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH",
            message="Corrigendum Population B patch rejected by strict profile",
            source_statute=self.amendment_id,
        )
        return xml_bytes, ()

    def _record_source_correction_findings(
        self,
        source_model: AmendmentSourceModel,
        source_patch_op_ids: tuple[str, ...],
    ) -> None:
        """Witness corrigendum source corrections by the model's content digests.

        Each ``APPLY.SOURCE_CORRECTED_BY_PATCH`` finding asserts that a patch
        changed the amendment source bytes before parsing. That assertion is now
        anchored to ``AmendmentSourceModel.source_digest`` /
        ``pre_correction_digest`` — the sha256 of the actual post- and
        pre-correction bytes — so the claimed content change travels as a content
        hash pair rather than only an ``op_id`` name. The model is the single
        owner of those digests; this site consumes them.
        """
        if not source_patch_op_ids:
            # No patch claimed a change: the model must agree that the bytes did
            # not change under correction. A bound pre/post pair here would mean
            # content drifted with no patch owning it — surface it, don't hide it.
            self._cross_check_no_silent_source_correction(source_model)
            return

        post_digest = source_model.source_digest
        pre_digest = source_model.pre_correction_digest
        digest_detail: dict[str, object] = {}
        if post_digest is not None:
            digest_detail["source_digest"] = post_digest.to_dict()
        if pre_digest is not None:
            digest_detail["pre_correction_digest"] = pre_digest.to_dict()

        # Drift cross-check: a patch claims a content change, so the model must
        # carry distinct pre/post digests. If the digests are absent or equal,
        # the patch's name-based claim diverged from the actual content.
        content_change_witnessed = (
            pre_digest is not None
            and post_digest is not None
            and pre_digest.digest != post_digest.digest
        )
        digest_detail["content_change_witnessed"] = content_change_witnessed
        if not content_change_witnessed:
            digest_detail["digest_drift"] = "patch_claimed_change_without_distinct_digests"

        for op_id in source_patch_op_ids:
            self.finding_recorder.record(
                kind="APPLY.SOURCE_CORRECTED_BY_PATCH",
                message="A corrigendum patch corrected amendment source XML before parsing.",
                source_statute=self.amendment_id,
                detail={
                    "op_id": op_id,
                    "source_role": "amendment_source_xml",
                    "corrected_by": "corrigendum_patch_table",
                    **digest_detail,
                },
                role="obligation",
                blocking=False,
            )

    def _cross_check_no_silent_source_correction(
        self,
        source_model: AmendmentSourceModel,
    ) -> None:
        """Flag a pre/post digest pair that no patch op owns (name-vs-content drift)."""
        pre_digest = source_model.pre_correction_digest
        post_digest = source_model.source_digest
        if (
            pre_digest is not None
            and post_digest is not None
            and pre_digest.digest != post_digest.digest
        ):
            self.finding_recorder.record(
                kind="APPLY.SOURCE_CORRECTION_DIGEST_DRIFT",
                message=(
                    "Amendment source bytes changed under correction but no "
                    "corrigendum patch op owns the change."
                ),
                source_statute=self.amendment_id,
                detail={
                    "source_role": "amendment_source_xml",
                    "source_digest": post_digest.to_dict(),
                    "pre_correction_digest": pre_digest.to_dict(),
                    "digest_drift": "content_changed_without_owning_patch",
                },
                role="observation",
                blocking=False,
            )

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
        pending_target_mid = ""
        if acquisition.decision.route_reason == "pending_amendment_of_parent_skip":
            pending_target_mid = str(acquisition.decision.route_target_amendment_id or "")
        elif acquisition.decision.route_reason == "citation_mismatch_skip":
            pending_target_mid = self._processed_pending_target_from_citation(
                acquisition=acquisition,
                source_title=source_title,
            )
        else:
            return acquisition
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

    def _processed_pending_target_from_citation(
        self,
        *,
        acquisition: AmendmentAcquisitionResult,
        source_title: str,
    ) -> str:
        """Resolve a cited already-processed pending amendment target.

        The normal pending-amendment route uses the base statute title to prove
        that a citation mismatch is actually an amendment of a pending amendment
        of this parent. That misses renamed statutes: the replay context may
        still carry the base title, while the later source title names the
        pending amending act by its own title. Here the guard is stricter:
        compose only when the johtolause cites exactly one already-processed
        amendment and the source title targets that amendment's own title.
        """
        source_year_text, separator, _source_num_text = self.amendment_id.partition("/")
        if separator != "/" or not source_year_text.isdigit():
            return ""
        source_year = int(source_year_text)
        cited_ids = [
            target_id
            for target_id in johtolause_cited_target_ids(
                acquisition.decision.citation_guard_johto
                or acquisition.decision.chosen_normalized_text,
                source_year,
            )
            if target_id in self.processed_amendment_titles
        ]
        matches = [
            target_id
            for target_id in cited_ids
            if title_targets_pending_amendment_title(
                source_title,
                self.processed_amendment_titles[target_id],
            )
        ]
        if len(matches) != 1:
            return ""
        return matches[0]
