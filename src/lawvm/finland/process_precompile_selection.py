"""Pre-compile operation selection for ``process_muutoslaki``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.finland.acquisition import AmendmentAcquisitionResult
from lawvm.finland.citation_routing import OP_KEYWORDS
from lawvm.finland.frontend_compile import _enrich_ops_from_amendment_tree, _is_body_only_amendment_surface
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.process_findings import ProcessFindingRecorder
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.source_pathology import build_empty_operative_body_pathology
from lawvm.finland.vts import VtsSkippedTarget, extract_vts_repeals_fallback

ReplayPrint = Callable[[str], None]
OpsEnricher = Callable[..., list[AmendmentOp]]


class VtsExtractor(Protocol):
    def __call__(
        self,
        johto: str,
        xml_bytes: bytes,
        parent_id: str,
        parent_title: str,
        strict_profile: Optional[StrictProfile],
        skipped_targets_out: Optional[list[VtsSkippedTarget]] = None,
    ) -> Optional[list[AmendmentOp]]: ...


@dataclass(frozen=True, slots=True)
class ProcessPrecompileSelectionResult:
    ops: tuple[AmendmentOp, ...]
    vts_ops_enrich_done: bool
    should_return_state: bool = False


@dataclass(slots=True)
class ProcessPrecompileSelectionContext:
    amendment_id: str
    parent_id: str
    parent_title: str
    source_title: str
    johto: str
    xml_bytes: bytes
    source_model: AmendmentSourceModel
    strict_profile: Optional[StrictProfile]
    acquisition: AmendmentAcquisitionResult
    skip_to_compile: bool
    ops: Sequence[AmendmentOp]
    vts_ops_enrich_done: bool
    lacks_operative_structure: bool
    operative_tags: Sequence[str]
    source_pathologies: list[SourcePathology]
    vts_skipped_targets: list[VtsSkippedTarget]
    finding_recorder: ProcessFindingRecorder
    replay_print: ReplayPrint
    amendment_metadata: object | None = None
    extract_vts_repeals: VtsExtractor = extract_vts_repeals_fallback
    enrich_ops_from_amendment_tree: OpsEnricher = _enrich_ops_from_amendment_tree

    def select(self) -> ProcessPrecompileSelectionResult:
        if self.skip_to_compile:
            return ProcessPrecompileSelectionResult(
                ops=tuple(self.ops),
                vts_ops_enrich_done=self.vts_ops_enrich_done,
            )

        self._record_post_routing_sec1_fallback()
        vts_ops = self.extract_vts_repeals(
            self.johto,
            self.xml_bytes,
            self.parent_id,
            self.parent_title,
            self.strict_profile,
            skipped_targets_out=self.vts_skipped_targets,
        )
        if vts_ops:
            ops = self.source_model.enrich_ops_from_amendment_tree(
                enrich_ops=self.enrich_ops_from_amendment_tree,
                ops=vts_ops,
                amendment_id=self.amendment_id,
                master=None,
                johto=self.johto,
                metadata=self.amendment_metadata,
            )
            self.replay_print(
                f"  [{self.amendment_id}] voimaantulo_repeal: "
                f"{[op.description() for op in ops]}"
            )
            return ProcessPrecompileSelectionResult(
                ops=tuple(ops),
                vts_ops_enrich_done=True,
            )

        if self._has_operation_keyword():
            return ProcessPrecompileSelectionResult(
                ops=tuple(self.ops),
                vts_ops_enrich_done=False,
            )

        if self._can_fall_through_to_eid_free_enacting_formula():
            return ProcessPrecompileSelectionResult(
                ops=tuple(self.ops),
                vts_ops_enrich_done=False,
            )

        self._record_empty_body_pathology_if_needed()
        return ProcessPrecompileSelectionResult(
            ops=tuple(self.ops),
            vts_ops_enrich_done=False,
            should_return_state=True,
        )

    def _record_post_routing_sec1_fallback(self) -> None:
        sec1_text = self.acquisition.sec1_text
        if not self.acquisition.decision.post_routing_sec1_applied or not sec1_text:
            return
        self.finding_recorder.record_sec1_fallback(
            amendment_id=self.amendment_id,
            stage="post_routing",
            previous_johto=self.acquisition.decision.citation_guard_johto,
            sec1_fallback_text=sec1_text,
            applied=True,
        )

    def _has_operation_keyword(self) -> bool:
        johto_lower = self.johto.lower()
        return any(keyword in johto_lower for keyword in OP_KEYWORDS)

    def _can_fall_through_to_eid_free_enacting_formula(self) -> bool:
        normalized_johto = " ".join(self.johto.split()).lower()
        is_enacting_formula = normalized_johto == "eduskunnan päätöksen mukaisesti"
        is_body_only_amendment = _is_body_only_amendment_surface(self.johto, self.source_title)
        return bool(
            (is_enacting_formula or is_body_only_amendment)
            and self.source_model.has_eid_free_body_sections()
        )

    def _record_empty_body_pathology_if_needed(self) -> None:
        if not self.lacks_operative_structure or self.acquisition.sec1_text.strip():
            return
        self.source_pathologies.append(
            build_empty_operative_body_pathology(
                source_statute=self.amendment_id,
                source_title=self.source_title,
                has_sec1_fallback_text=False,
                operative_tags_detected=list(self.operative_tags),
            )
        )
