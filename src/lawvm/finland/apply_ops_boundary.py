"""Typed boundary for applying one Finland amendment's resolved operations."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set

import lxml.etree as etree

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import LegalOperation
from lawvm.core.mutation_accounting import MutationAccountingResult
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.finland.apply_events import ApplyMutationEvent
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.migration_ledger import MigrationLedger
from lawvm.finland.ops import AmendmentOp, FailedOp, ResolvedOp
from lawvm.finland.restructure_plan import StructuralTransformPlan
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState, StatuteContext


@dataclass(frozen=True, slots=True)
class ApplyOpsRequest:
    """Semantic inputs for the resolved-op apply fold.

    Mutable diagnostics and replay artifacts belong in ``ApplyOpsSinks``.
    """

    state: ReplayState
    ctx: StatuteContext
    resolved: List[ResolvedOp]
    ops: List[AmendmentOp]
    muutos_tree: etree._Element
    johto: str
    amendment_id: str
    source_title: str
    amendment_issue_date: Optional[dt.date]
    amendment_effective_date: Optional[dt.date]
    amendment_expiry_date: Optional[dt.date]
    replay_mode: Literal["official_consolidation", "legal_pit"]
    strict_profile: Optional[StrictProfile]
    vts_ops_enrich_done: bool
    future_repeals: Optional[Set[RepealTargetRef]] = None
    source_model: Optional[AmendmentSourceModel] = None


@dataclass(frozen=True, slots=True)
class ApplyOpsSinks:
    """Mutable evidence/artifact channels for the resolved-op apply fold."""

    compiled_ops_out: Optional[List[Dict[str, object]]] = None
    lo_ops_out: Optional[List[LegalOperation]] = None
    failed_ops_out: Optional[List[FailedOp]] = None
    source_pathologies_out: Optional[List[SourcePathology]] = None
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None
    migration_ledger: Optional[MigrationLedger] = None
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None
    observations_out: Optional[List[Dict[str, object]]] = None
    findings_out: Optional[List[Finding]] = None
    observed_touch_results_out: Optional[List[MutationAccountingResult]] = None
    write_audits_out: Optional[List[ObservedWriteAudit]] = None
