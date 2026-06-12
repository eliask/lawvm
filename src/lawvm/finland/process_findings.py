"""Finding recorder for ``process_muutoslaki`` phase-owned signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional

from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding


@dataclass(slots=True)
class ProcessFindingRecorder:
    process_findings: list[Finding]

    def record(
        self,
        *,
        kind: str,
        message: str,
        source_statute: str = "",
        detail: Optional[Dict[str, object]] = None,
        role: Literal["observation", "obligation", "violation"] = "obligation",
        blocking: bool = True,
    ) -> Finding:
        spec = get_finding_spec(kind)
        finding_kind = kind
        finding_role = role
        if spec is not None and spec.role == "barrier":
            finding_kind = "RUNTIME.VIOLATION"
            finding_role = "violation"
        finding = Finding(
            kind=finding_kind,
            role=finding_role,
            stage="process_muutoslaki",
            detail={
                "message": message,
                **(detail or {}),
                **({"barrier_code": kind} if finding_kind == "RUNTIME.VIOLATION" else {}),
            },
            source_statute=source_statute,
            blocking=blocking,
        )
        self.process_findings.append(finding)
        return finding

    def record_sec1_fallback(
        self,
        *,
        amendment_id: str,
        stage: Literal["pre_routing", "post_routing"],
        previous_johto: str,
        sec1_fallback_text: str,
        applied: bool,
    ) -> None:
        kind = (
            "ELAB.SEC1_PRE_ROUTING_FALLBACK"
            if stage == "pre_routing"
            else "ELAB.SEC1_POST_ROUTING_FALLBACK"
        )
        message = (
            "Section 1 body text replaced the parsed johtolause before routing."
            if stage == "pre_routing"
            else "Section 1 body text replaced the parsed johtolause after routing."
        )
        self.record(
            kind=kind,
            message=message,
            source_statute=amendment_id,
            detail={
                "fallback_stage": stage,
                "fallback_applied": applied,
                "original_johtolause": previous_johto,
                "sec1_fallback_text": sec1_fallback_text,
            },
            role="obligation" if stage == "pre_routing" else "observation",
            blocking=(stage == "pre_routing"),
        )
