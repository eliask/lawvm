"""Route-rejection handling for ``process_muutoslaki``.

Acquisition/citation routing can reject an amendment for the current parent, but
some rejected rows still carry executable side effects: parent expiry overrides
or cross-statute VTS repeals. This module owns that boundary so rejected routing
does not disappear as a plain early return.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from lxml import etree

from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.phase_result import Finding
from lawvm.finland.citation_routing import (
    _looks_like_fi_meta_repeal,
    _title_explicitly_targets_other_statute,
    johtolause_cited_target_ids,
)
from lawvm.finland.frontend_compile import _enrich_ops_from_amendment_tree
from lawvm.finland.metadata import _commencement_expiry_override
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.temporal_rewrites import _rewrite_lo_op_source_expiry
from lawvm.finland.vts import VtsSkippedTarget, extract_vts_cross_statute_repeals


RecordProcessFinding = Callable[..., Finding]
ReplayPrint = Callable[[str], None]

logger = logging.getLogger(__name__)


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
    xml_bytes: bytes
    muutos_tree: etree._Element
    route_reason: str
    route_target_amendment_id: str
    strict_profile: Optional[StrictProfile]
    replay_mode: str
    lo_ops_out: Optional[List[_LegalOperation]]
    vts_skipped_targets: list[VtsSkippedTarget]
    commencement_expiry_override_notes: list[dict[str, object]]
    record_finding: RecordProcessFinding
    replay_print: ReplayPrint

    def _cited_statute_phrase(self) -> str:
        """Name the statute(s) the johtolause actually cites, for diagnostics.

        Returns e.g. ``"johtolause cites 1958/70"`` so a reader can see the
        dropped/garbled citation against ``parent_id`` directly, or a generic
        phrase when no statute citation is parseable from the working clause.
        """
        try:
            source_year = int(self.amendment_id.split("/", 1)[0])
        except (ValueError, IndexError):
            source_year = 0
        cited = johtolause_cited_target_ids(self.johto, source_year) if source_year else []
        if cited:
            return f"johtolause cites {', '.join(cited)}"
        return "johtolause cites no parseable statute"

    def handle(self) -> RouteRejectionResult:
        self._record_source_incomplete()
        self._apply_skipped_amendment_expiry_override()
        vts_ops = extract_vts_cross_statute_repeals(
            self.xml_bytes,
            self.parent_id,
            self.parent_title,
            self.strict_profile,
            skipped_targets_out=self.vts_skipped_targets,
        )
        if not vts_ops:
            return RouteRejectionResult(
                ops=(),
                vts_ops_enrich_done=False,
                skip_to_compile=False,
                should_return_state=True,
            )

        ops = _enrich_ops_from_amendment_tree(
            vts_ops,
            self.amendment_id,
            self.muutos_tree,
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

    def _record_source_incomplete(self) -> None:
        if self.route_reason == "num_collision_skip":
            self.replay_print(
                f"  [{self.amendment_id}] SKIPPED — NUM-collision false mapping: "
                f"{self._cited_statute_phrase()} (not {self.parent_id})"
            )
            self.record_finding(
                kind="APPLY.SOURCE_INCOMPLETE",
                message="Amendment skipped: lineage routing rejected by NUM collision.",
                source_statute=self.amendment_id,
                detail={"route_reason": "num_collision_skip"},
                role="obligation",
            )
            return

        if self.route_reason == "pending_amendment_of_parent_skip":
            target_suffix = f" via pending {self.route_target_amendment_id}" if self.route_target_amendment_id else ""
            self.replay_print(
                f"  [{self.amendment_id}] SKIPPED — pending amendment of parent recognized "
                f"but not yet composed into {self.parent_id}{target_suffix}"
            )
            self.record_finding(
                kind="APPLY.SOURCE_INCOMPLETE",
                message="Amendment skipped: pending amendment-of-amendment target not yet composed.",
                source_statute=self.amendment_id,
                detail={
                    "route_reason": "pending_amendment_of_parent_skip",
                    "target_amendment_id": self.route_target_amendment_id,
                },
                role="obligation",
            )
            return

        if _looks_like_fi_meta_repeal(self.johto):
            logger.debug("  [%s] SKIPPED — meta-repeal targets prior amendment act, not %s", self.amendment_id, self.parent_id)
        elif _title_explicitly_targets_other_statute(self.source_title, self.parent_title):
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
            detail={"route_reason": str(self.route_reason or "citation_mismatch_skip")},
            role="obligation",
        )

    def _apply_skipped_amendment_expiry_override(self) -> None:
        expiry_override = _commencement_expiry_override(self.muutos_tree, self.amendment_id)
        if expiry_override is None:
            return
        target_mid, labels, expiry = expiry_override
        if target_mid == self.amendment_id:
            return
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
        scope = sorted(labels) if labels else ["*"]
        self.replay_print(
            f"  [{self.amendment_id}] voimaantulo_expiry_override: "
            f"{target_mid} {scope} -> {expiry.isoformat()}"
        )
        self.commencement_expiry_override_notes.append(
            {
                "source_statute": self.amendment_id,
                "target_statute": target_mid,
                "labels": scope,
                "expiry": expiry.isoformat(),
                "context": "skipped_amendment",
            }
        )
