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


@dataclass(frozen=True, slots=True)
class ResolvedReplayXmlCall:
    """Fully resolved replay_xml call boundary.

    This is the compatibility adapter output: request fields and sink fields
    have been merged, and the replay executor no longer needs to know whether
    callers used the typed or legacy surface.
    """

    parent_id: str
    mode: Literal["official_consolidation", "legal_pit"]
    stop_before: str
    strict_profile: Optional[StrictProfile]
    corpus: Optional[CorpusStore]
    quiet: bool
    build_full_products: bool
    checkpoint_callback: Optional[ReplayCheckpointCallback]
    as_of: str
    strict_johto_temporal: bool
    oracle_selector: ConsolidatedArtifactSelector | None
    compiled_ops_out: Optional[list[dict[str, object]]]
    replay_meta_out: Optional[Dict[str, object]]
    lo_ops_out: Optional[list[LegalOperation]]
    failed_ops_out: Optional[list[FailedOp]]
    temporal_events_out: Optional[list[Any]]
    source_pathologies_out: Optional[list[SourcePathology]]


def resolve_replay_xml_call(
    *,
    parent_id: Optional[str],
    mode: Literal["official_consolidation", "legal_pit"],
    compiled_ops_out: Optional[list[dict[str, object]]],
    replay_meta_out: Optional[Dict[str, object]],
    lo_ops_out: Optional[list[LegalOperation]],
    stop_before: str,
    failed_ops_out: Optional[list[FailedOp]],
    strict_profile: Optional[StrictProfile],
    corpus: Optional[CorpusStore],
    quiet: bool,
    build_full_products: bool,
    temporal_events_out: Optional[list[Any]],
    checkpoint_callback: Optional[ReplayCheckpointCallback],
    as_of: str,
    strict_johto_temporal: bool,
    oracle_selector: ConsolidatedArtifactSelector | None,
    source_pathologies_out: Optional[list[SourcePathology]],
    request: Optional[ReplayXmlRequest],
    sinks: Optional[ReplayXmlSinks],
) -> ResolvedReplayXmlCall:
    """Merge typed and legacy replay_xml inputs into one typed call object."""

    if request is not None:
        parent_id = request.parent_id
        mode = request.mode
        stop_before = request.stop_before
        strict_profile = request.strict_profile
        corpus = request.corpus
        quiet = request.quiet
        build_full_products = request.build_full_products
        checkpoint_callback = request.checkpoint_callback
        as_of = request.as_of
        strict_johto_temporal = request.strict_johto_temporal
        oracle_selector = request.oracle_selector
    if parent_id is None:
        raise TypeError("replay_xml requires either parent_id or request=")

    if sinks is not None:
        compiled_ops_out = compiled_ops_out if compiled_ops_out is not None else sinks.compiled_ops_out
        replay_meta_out = replay_meta_out if replay_meta_out is not None else sinks.replay_meta_out
        lo_ops_out = lo_ops_out if lo_ops_out is not None else sinks.lo_ops_out
        failed_ops_out = failed_ops_out if failed_ops_out is not None else sinks.failed_ops_out
        temporal_events_out = (
            temporal_events_out if temporal_events_out is not None else sinks.temporal_events_out
        )
        source_pathologies_out = (
            source_pathologies_out
            if source_pathologies_out is not None
            else sinks.source_pathologies_out
        )

    return ResolvedReplayXmlCall(
        parent_id=parent_id,
        mode=mode,
        stop_before=stop_before,
        strict_profile=strict_profile,
        corpus=corpus,
        quiet=quiet,
        build_full_products=build_full_products,
        checkpoint_callback=checkpoint_callback,
        as_of=as_of,
        strict_johto_temporal=strict_johto_temporal,
        oracle_selector=oracle_selector,
        compiled_ops_out=compiled_ops_out,
        replay_meta_out=replay_meta_out,
        lo_ops_out=lo_ops_out,
        failed_ops_out=failed_ops_out,
        temporal_events_out=temporal_events_out,
        source_pathologies_out=source_pathologies_out,
    )
