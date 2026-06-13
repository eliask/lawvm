"""Typed request/sink boundary for Finland single-statute replay."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
from typing import Any, Callable, Dict, Literal, Optional

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

    Request fields and sink fields have been merged, so the replay executor
    consumes one normalized internal carrier.
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


def _callable_accepts_typed_replay_call(fn: Callable[..., Any]) -> bool:
    try:
        params = signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "request" in params and "sinks" in params


def _filter_legacy_kwargs(fn: Callable[..., Any], kwargs: dict[str, object]) -> dict[str, object]:
    try:
        params = signature(fn).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(param.kind is Parameter.VAR_KEYWORD for param in params.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in params}


def call_replay_xml(
    replay_xml_func: Callable[..., Any],
    *,
    request: ReplayXmlRequest,
    sinks: ReplayXmlSinks | None = None,
) -> Any:
    """Call ``replay_xml`` through the typed surface, with legacy-fake fallback.

    This keeps production callers migrating toward ``ReplayXmlRequest`` /
    ``ReplayXmlSinks`` while test fakes and older adapters are retired in
    separate, reviewable slices. The real ``replay_xml`` function advertises the
    typed parameters and is always called that way.
    """
    if _callable_accepts_typed_replay_call(replay_xml_func):
        return replay_xml_func(request=request, sinks=sinks)

    sinks = sinks or ReplayXmlSinks()
    legacy_kwargs: dict[str, object] = {"mode": request.mode}
    if request.stop_before:
        legacy_kwargs["stop_before"] = request.stop_before
    if request.strict_profile is not None:
        legacy_kwargs["strict_profile"] = request.strict_profile
    if request.corpus is not None:
        legacy_kwargs["corpus"] = request.corpus
    legacy_kwargs["quiet"] = request.quiet
    legacy_kwargs["build_full_products"] = request.build_full_products
    if request.checkpoint_callback is not None:
        legacy_kwargs["checkpoint_callback"] = request.checkpoint_callback
    if request.as_of:
        legacy_kwargs["as_of"] = request.as_of
    if request.strict_johto_temporal:
        legacy_kwargs["strict_johto_temporal"] = request.strict_johto_temporal
    if request.oracle_selector is not None:
        legacy_kwargs["oracle_selector"] = request.oracle_selector
    if sinks.compiled_ops_out is not None:
        legacy_kwargs["compiled_ops_out"] = sinks.compiled_ops_out
    if sinks.replay_meta_out is not None:
        legacy_kwargs["replay_meta_out"] = sinks.replay_meta_out
    if sinks.lo_ops_out is not None:
        legacy_kwargs["lo_ops_out"] = sinks.lo_ops_out
    if sinks.failed_ops_out is not None:
        legacy_kwargs["failed_ops_out"] = sinks.failed_ops_out
    if sinks.temporal_events_out is not None:
        legacy_kwargs["temporal_events_out"] = sinks.temporal_events_out
    if sinks.source_pathologies_out is not None:
        legacy_kwargs["source_pathologies_out"] = sinks.source_pathologies_out

    return replay_xml_func(
        request.parent_id,
        **_filter_legacy_kwargs(replay_xml_func, legacy_kwargs),
    )


def resolve_replay_xml_request(
    *,
    request: ReplayXmlRequest,
    sinks: ReplayXmlSinks | None = None,
) -> ResolvedReplayXmlCall:
    """Resolve the typed replay request/sink boundary into executor inputs."""

    sinks = sinks or ReplayXmlSinks()
    return ResolvedReplayXmlCall(
        parent_id=request.parent_id,
        mode=request.mode,
        stop_before=request.stop_before,
        strict_profile=request.strict_profile,
        corpus=request.corpus,
        quiet=request.quiet,
        build_full_products=request.build_full_products,
        checkpoint_callback=request.checkpoint_callback,
        as_of=request.as_of,
        strict_johto_temporal=request.strict_johto_temporal,
        oracle_selector=request.oracle_selector,
        compiled_ops_out=sinks.compiled_ops_out,
        replay_meta_out=sinks.replay_meta_out,
        lo_ops_out=sinks.lo_ops_out,
        failed_ops_out=sinks.failed_ops_out,
        temporal_events_out=sinks.temporal_events_out,
        source_pathologies_out=sinks.source_pathologies_out,
    )
