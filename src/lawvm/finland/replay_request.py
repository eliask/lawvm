"""Typed request/sink boundary for Finland single-statute replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from lawvm.corpus_store import CorpusStore
from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.ir import LegalOperation
from lawvm.core.replay_contracts import ReplayCheckpointCallback
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.ops import FailedOp


@dataclass(frozen=True, slots=True)
class ReplayXmlRequest:
    """Semantic/control inputs for replaying one Finnish statute."""

    parent_id: str
    mode: Literal["official_consolidation", "legal_pit"] = "official_consolidation"
    stop_before: str = ""
    strict_profile: Optional[StrictProfile] = None
    corpus: Optional[CorpusStore] = None
    quiet: bool = False
    build_full_products: bool = True
    checkpoint_callback: Optional[ReplayCheckpointCallback] = None
    as_of: str = ""
    strict_johto_temporal: bool = False
    oracle_selector: ConsolidatedArtifactSelector | None = None


@dataclass(frozen=True, slots=True)
class ReplayXmlSinks:
    """External artifact/evidence destinations for ``replay_xml``."""

    compiled_ops_out: Optional[list[dict[str, object]]] = None
    replay_meta_out: Optional[Dict[str, object]] = None
    lo_ops_out: Optional[list[LegalOperation]] = None
    failed_ops_out: Optional[list[FailedOp]] = None
    temporal_events_out: Optional[list[Any]] = None
    source_pathologies_out: Optional[list[SourcePathology]] = None
