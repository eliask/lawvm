"""Route-rejection handling for ``process_muutoslaki``.

Acquisition/citation routing can reject an amendment for the current parent, but
some rejected rows still carry executable side effects: parent expiry overrides
or cross-statute VTS repeals. This module owns that boundary so rejected routing
does not disappear as a plain early return.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Mapping, Optional

from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.finland.citation_routing import (
    _looks_like_fi_meta_repeal,
    _title_looks_like_fi_meta_repeal,
    _title_explicitly_targets_other_statute,
    johtolause_cited_target_ids,
)
from lawvm.finland.effect_lifecycle_signals import (
    EffectLifecycleOverride,
    EffectLifecycleOverrideScope,
    EffectRelationSignal,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.temporal_rewrites import _rewrite_lo_op_source_expiry
from lawvm.finland.vts import VtsSkippedTarget


RecordProcessFinding = Callable[..., Finding]
ReplayPrint = Callable[[str], None]

logger = logging.getLogger(__name__)


FI_ROUTE_REJECTION_NUM_COLLISION_RULE_ID = "fi.route_rejection.num_collision"
FI_ROUTE_REJECTION_PENDING_AMENDMENT_RULE_ID = "fi.route_rejection.pending_amendment_of_parent"
FI_ROUTE_REJECTION_DELEGATED_AUTHORITY_RULE_ID = "fi.route_rejection.delegated_authority_nojalla"
FI_ROUTE_REJECTION_META_REPEAL_RULE_ID = "fi.route_rejection.meta_repeal"
FI_ROUTE_REJECTION_TITLE_OTHER_STATUTE_RULE_ID = "fi.route_rejection.title_targets_other_statute"
FI_ROUTE_REJECTION_CITATION_MISMATCH_RULE_ID = "fi.route_rejection.citation_mismatch"


class RouteRejectionBranch(str, Enum):
    """Stable sub-branch for route rejections that share one finding kind."""

    NUM_COLLISION = "num_collision"
    PENDING_AMENDMENT_OF_PARENT = "pending_amendment_of_parent"
    DELEGATED_AUTHORITY_NOJALLA = "delegated_authority_nojalla"
    META_REPEAL = "meta_repeal"
    TITLE_TARGETS_OTHER_STATUTE = "title_targets_other_statute"
    CITATION_MISMATCH = "citation_mismatch"


@dataclass(frozen=True, slots=True)
class RouteRejectionDisposition:
    rule_id: str
    route_reason: str
    branch: RouteRejectionBranch
    family: str = "source_routing"
    phase: str = "process_muutoslaki.route_rejection"
    strict_disposition: str = "block"
    quirks_disposition: str = "skip_with_finding"

    def as_detail(self, extra: Mapping[str, object] | None = None) -> dict[str, object]:
        detail: dict[str, object] = {
            "route_reason": self.route_reason,
            "rule_id": self.rule_id,
            "family": self.family,
            "phase": self.phase,
            "branch": self.branch.value,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }
        if extra:
            detail.update(extra)
        return detail


def classify_route_rejection(
    *,
    route_reason: str,
    johto: str,
    source_title: str,
    parent_title: str,
) -> RouteRejectionDisposition:
    """Classify a skipped amendment into a stable source-routing sub-branch."""

    if route_reason == "num_collision_skip":
        return RouteRejectionDisposition(
            rule_id=FI_ROUTE_REJECTION_NUM_COLLISION_RULE_ID,
            route_reason="num_collision_skip",
            branch=RouteRejectionBranch.NUM_COLLISION,
        )
    if route_reason == "pending_amendment_of_parent_skip":
        return RouteRejectionDisposition(
            rule_id=FI_ROUTE_REJECTION_PENDING_AMENDMENT_RULE_ID,
            route_reason="pending_amendment_of_parent_skip",
            branch=RouteRejectionBranch.PENDING_AMENDMENT_OF_PARENT,
        )
    if route_reason == "delegated_authority_nojalla_skip":
        return RouteRejectionDisposition(
            rule_id=FI_ROUTE_REJECTION_DELEGATED_AUTHORITY_RULE_ID,
            route_reason="delegated_authority_nojalla_skip",
            branch=RouteRejectionBranch.DELEGATED_AUTHORITY_NOJALLA,
        )
    normalized_reason = str(route_reason or "citation_mismatch_skip")
    if _looks_like_fi_meta_repeal(johto) or _title_looks_like_fi_meta_repeal(source_title):
        return RouteRejectionDisposition(
            rule_id=FI_ROUTE_REJECTION_META_REPEAL_RULE_ID,
            route_reason=normalized_reason,
            branch=RouteRejectionBranch.META_REPEAL,
        )
    if _title_explicitly_targets_other_statute(source_title, parent_title):
        return RouteRejectionDisposition(
            rule_id=FI_ROUTE_REJECTION_TITLE_OTHER_STATUTE_RULE_ID,
            route_reason=normalized_reason,
            branch=RouteRejectionBranch.TITLE_TARGETS_OTHER_STATUTE,
        )
    return RouteRejectionDisposition(
        rule_id=FI_ROUTE_REJECTION_CITATION_MISMATCH_RULE_ID,
        route_reason=normalized_reason,
        branch=RouteRejectionBranch.CITATION_MISMATCH,
    )


@dataclass(frozen=True, slots=True)
class RouteRejectionResult:
    ops: tuple[AmendmentOp, ...]
    vts_ops_enrich_done: bool
    skip_to_compile: bool
    should_return_state: bool


@dataclass(slots=True)
class ProcessRouteRejectionContext:
    amendment_id: str
    parent_id: str
    parent_title: str
    source_title: str
    johto: str
    source_model: AmendmentSourceModel
    route_reason: str
    route_target_amendment_id: str
    strict_profile: Optional[StrictProfile]
    replay_mode: str
    lo_ops_out: Optional[List[_LegalOperation]]
    vts_skipped_targets: list[VtsSkippedTarget]
    commencement_expiry_override_notes: list[EffectLifecycleOverride]
    effect_relation_signals: list[EffectRelationSignal]
    record_finding: RecordProcessFinding
    replay_print: ReplayPrint

    def _cited_statute_phrase(self) -> str:
        """Name the statute(s) the johtolause actually cites, for diagnostics.

        Returns e.g. ``"johtolause cites 1958/70"`` so a reader can see the
        dropped/garbled citation against ``parent_id`` directly, or a generic
        phrase when no statute citation is parseable from the working clause.
        """
        cited = list(self._cited_statute_ids())
        if cited:
            return f"johtolause cites {', '.join(cited)}"
        return "johtolause cites no parseable statute"

    def _cited_statute_ids(self) -> tuple[str, ...]:
        try:
            source_year = int(self.amendment_id.split("/", 1)[0])
        except (ValueError, IndexError):
            return ()
        if not source_year:
            return ()
        cited = list(johtolause_cited_target_ids(self.johto, source_year))
        if not cited and self.source_title:
            cited = list(johtolause_cited_target_ids(self.source_title, source_year))
        return tuple(cited)

    def handle(self) -> RouteRejectionResult:
        disposition = classify_route_rejection(
            route_reason=self.route_reason,
            johto=self.johto,
            source_title=self.source_title,
            parent_title=self.parent_title,
        )
        self._record_source_incomplete(disposition)
        self._apply_skipped_amendment_expiry_override()
        if disposition.branch is RouteRejectionBranch.META_REPEAL:
            return RouteRejectionResult(
                ops=(),
                vts_ops_enrich_done=False,
                skip_to_compile=False,
                should_return_state=True,
            )
        vts_ops = self.source_model.extract_vts_cross_statute_repeals(
            parent_id=self.parent_id,
            parent_title=self.parent_title,
            strict_profile=self.strict_profile,
            skipped_targets_out=self.vts_skipped_targets,
        )
        if not vts_ops:
            return RouteRejectionResult(
                ops=(),
                vts_ops_enrich_done=False,
                skip_to_compile=False,
                should_return_state=True,
            )

        ops = self.source_model.enrich_amendment_ops(
            ops=vts_ops,
            amendment_id=self.amendment_id,
            johto=self.johto,
        )
        self.replay_print(
            f"  [{self.amendment_id}] voimaantulo_repeal (cross-statute): "
            f"{[op.description() for op in ops]}"
        )
        return RouteRejectionResult(
            ops=tuple(ops),
            vts_ops_enrich_done=True,
            skip_to_compile=True,
            should_return_state=False,
        )

    def _record_source_incomplete(self, disposition: RouteRejectionDisposition | None = None) -> None:
        if disposition is None:
            disposition = classify_route_rejection(
                route_reason=self.route_reason,
                johto=self.johto,
                source_title=self.source_title,
                parent_title=self.parent_title,
            )
        if disposition.branch is RouteRejectionBranch.NUM_COLLISION:
            self.replay_print(
                f"  [{self.amendment_id}] SKIPPED — NUM-collision false mapping: "
                f"{self._cited_statute_phrase()} (not {self.parent_id})"
            )
            self.record_finding(
                kind="APPLY.SOURCE_INCOMPLETE",
                message="Amendment skipped: lineage routing rejected by NUM collision.",
                source_statute=self.amendment_id,
                detail=disposition.as_detail(),
                role="obligation",
            )
            return

        if disposition.branch is RouteRejectionBranch.PENDING_AMENDMENT_OF_PARENT:
            target_suffix = f" via pending {self.route_target_amendment_id}" if self.route_target_amendment_id else ""
            self.replay_print(
                f"  [{self.amendment_id}] SKIPPED — pending amendment of parent recognized "
                f"but not yet composed into {self.parent_id}{target_suffix}"
            )
            self.effect_relation_signals.append(
                EffectRelationSignal.pending_amendment(
                    source_statute=self.amendment_id,
                    target_statute=self.route_target_amendment_id,
                    base_parent_id=self.parent_id,
                    message=(
                        "Pending amendment-of-amendment target could not be resolved "
                        "to a prior source-backed effect."
                    ),
                    source_finding="APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
                    target_resolution="target_instrument_unresolved",
                )
            )
            self.record_finding(
                kind="APPLY.SOURCE_INCOMPLETE",
                message="Amendment skipped: pending amendment-of-amendment target not yet composed.",
                source_statute=self.amendment_id,
                detail=disposition.as_detail({"target_amendment_id": self.route_target_amendment_id}),
                role="obligation",
            )
            self.record_finding(
                kind="APPLY.PENDING_AMENDMENT_EFFECT_UNRESOLVED",
                message=(
                    "Pending amendment-of-amendment target could not be resolved "
                    "to a prior source-backed effect."
                ),
                source_statute=self.amendment_id,
                detail=disposition.as_detail({"target_amendment_id": self.route_target_amendment_id}),
                role="obligation",
                blocking=True,
            )
            return

        if disposition.branch is RouteRejectionBranch.DELEGATED_AUTHORITY_NOJALLA:
            self.replay_print(
                f"  [{self.amendment_id}] SKIPPED — delegated-authority nojalla clause: "
                f"{self._cited_statute_phrase()} is enabling authority (not replay target {self.parent_id})"
            )
            self.record_finding(
                kind="APPLY.SOURCE_INCOMPLETE",
                message="Amendment skipped: delegated-authority nojalla lead-in cites enabling statute.",
                source_statute=self.amendment_id,
                detail=disposition.as_detail(),
                role="obligation",
            )
            return

        if disposition.branch is RouteRejectionBranch.META_REPEAL:
            logger.debug("  [%s] SKIPPED — meta-repeal targets prior amendment act, not %s", self.amendment_id, self.parent_id)
            cited_ids = self._cited_statute_ids()
            if cited_ids:
                for cited_id in cited_ids:
                    self.effect_relation_signals.append(
                        EffectRelationSignal.meta_repeal(
                            source_statute=self.amendment_id,
                            target_statute=cited_id,
                            route_reason=disposition.route_reason,
                            message="Meta-repeal of prior amending instrument recorded as lifecycle evidence.",
                            source_finding="APPLY.META_REPEAL_EFFECT_RECORDED",
                            target_resolution="target_instrument_resolved",
                        )
                    )
                    self.record_finding(
                        kind="APPLY.META_REPEAL_EFFECT_RECORDED",
                        message="Meta-repeal of prior amending instrument recorded as lifecycle evidence.",
                        source_statute=self.amendment_id,
                        detail=disposition.as_detail({"target_amendment_id": cited_id}),
                        role="observation",
                        blocking=False,
                    )
            else:
                self.effect_relation_signals.append(
                    EffectRelationSignal.meta_repeal(
                        source_statute=self.amendment_id,
                        target_statute="",
                        route_reason=disposition.route_reason,
                        message="Meta-repeal target could not be resolved to a prior source-backed effect.",
                        source_finding="APPLY.META_REPEAL_EFFECT_UNRESOLVED",
                        target_resolution="target_instrument_unresolved",
                    )
                )
                self.record_finding(
                    kind="APPLY.META_REPEAL_EFFECT_UNRESOLVED",
                    message="Meta-repeal target could not be resolved to a prior source-backed effect.",
                    source_statute=self.amendment_id,
                    detail=disposition.as_detail(),
                    role="obligation",
                    blocking=True,
                )
        elif disposition.branch is RouteRejectionBranch.TITLE_TARGETS_OTHER_STATUTE:
            self.replay_print(f"  [{self.amendment_id}] SKIPPED — title targets different statute (not {self.parent_id})")
        else:
            self.replay_print(
                f"  [{self.amendment_id}] SKIPPED — citation mismatch: "
                f"{self._cited_statute_phrase()} (not {self.parent_id})"
            )
        self.record_finding(
            kind="APPLY.SOURCE_INCOMPLETE",
            message="Amendment skipped: citation routing rejected.",
            source_statute=self.amendment_id,
            detail=disposition.as_detail(),
            role="obligation",
        )

    def _apply_skipped_amendment_expiry_override(self) -> None:
        expiry_override = self.source_model.commencement_expiry_override(self.amendment_id)
        if expiry_override is None:
            return
        target_mid, labels, expiry = expiry_override
        if target_mid == self.amendment_id:
            return
        scope = sorted(labels) if labels else ["*"]
        self.commencement_expiry_override_notes.append(
            EffectLifecycleOverride(
                source_statute=self.amendment_id,
                target_statute=target_mid,
                scope=EffectLifecycleOverrideScope.sections(scope),
                expiry=expiry.isoformat(),
                context="skipped_amendment",
            )
        )
        if not _rewrite_lo_op_source_expiry(
            self.lo_ops_out,
            target_mid,
            labels,
            expiry,
            parent_statute_id=self.parent_id,
            replay_mode=self.replay_mode,
            expiry_convention="inclusive_prose",
        ):
            return
        self.replay_print(
            f"  [{self.amendment_id}] voimaantulo_expiry_override: "
            f"{target_mid} {scope} -> {expiry.isoformat()}"
        )
