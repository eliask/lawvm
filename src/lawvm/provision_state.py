"""Public provision-state seam API."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TextIO

from lawvm.core.ir import IRStatute, LegalAddress, ProvisionTimeline
from lawvm.core.phase_result import Finding
from lawvm.core.provenance import MigrationEvent
from lawvm.core.temporal_scheduler import TemporalScheduleDelta
from lawvm.tools.provision_state import (
    build_provision_state_response,
    invalid_query_payload,
    invalid_provision_selector_payload,
    provision_query_diagnostic,
    provision_selector_diagnostic,
    unsupported_jurisdiction_payload,
)
from lawvm.tools.timeline_integrity import TimelineBreak


@dataclass(frozen=True, slots=True)
class ProvisionStateRuntime:
    """Replay-backed provision-state read model for repeated queries."""

    statute_id: str
    title: str
    timelines: Mapping[LegalAddress, ProvisionTimeline]
    migration_events: tuple[MigrationEvent, ...]
    base: IRStatute
    timeline_breaks: tuple[TimelineBreak, ...]
    temporal_schedule_deltas: tuple[TemporalScheduleDelta, ...]
    findings: tuple[Finding, ...]
    source_xml_provider: Callable[[str], bytes | None]

    def resolve(
        self,
        *,
        provision: str,
        as_of: str,
        jurisdiction: str = "fi",
        query_type: str = "governing",
        territory: str | None = None,
        include_ir: bool = False,
    ) -> dict[str, Any]:
        """Resolve one provision query from this precompiled statute runtime."""

        if jurisdiction != "fi":
            return unsupported_jurisdiction_payload(
                jurisdiction=jurisdiction,
                statute_id=self.statute_id,
                provision=provision,
                as_of=as_of,
                query_type=query_type,
            )
        return build_provision_state_response(
            timelines=self.timelines,
            migration_events=self.migration_events,
            statute_id=self.statute_id,
            jurisdiction=jurisdiction,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            include_ir=include_ir,
            title=self.title,
            base=self.base,
            timeline_breaks=self.timeline_breaks,
            temporal_schedule_deltas=self.temporal_schedule_deltas,
            findings=self.findings,
            source_xml_provider=self.source_xml_provider,
        )


def resolve_provision_state(
    *,
    statute_id: str,
    provision: str,
    as_of: str,
    jurisdiction: str = "fi",
    query_type: str = "governing",
    territory: str | None = None,
    include_ir: bool = False,
    status_stream: TextIO | None = None,
) -> dict[str, Any]:
    """Resolve one PIT provision state into the stable seam JSON shape."""

    if jurisdiction != "fi":
        return unsupported_jurisdiction_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
        )
    query_diagnostic = provision_query_diagnostic(as_of=as_of, query_type=query_type)
    if query_diagnostic is not None:
        return invalid_query_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            diagnostic=query_diagnostic,
        )
    selector_diagnostic = provision_selector_diagnostic(
        jurisdiction=jurisdiction,
        provision=provision,
    )
    if selector_diagnostic is not None:
        return invalid_provision_selector_payload(
            jurisdiction=jurisdiction,
            statute_id=statute_id,
            provision=provision,
            as_of=as_of,
            query_type=query_type,
            territory=territory,
            diagnostic=selector_diagnostic,
        )

    runtime = compile_provision_state_runtime(
        statute_id=statute_id,
        status_stream=status_stream,
    )
    return runtime.resolve(
        provision=provision,
        as_of=as_of,
        jurisdiction=jurisdiction,
        query_type=query_type,
        territory=territory,
        include_ir=include_ir,
    )


def compile_provision_state_runtime(
    *,
    statute_id: str,
    status_stream: TextIO | None = None,
) -> ProvisionStateRuntime:
    """Replay one FI statute into a reusable provision-state read model."""

    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml
    from lawvm.tools.timeline_integrity import (
        attach_effective_dates,
        timeline_breaks_from_findings,
    )

    if status_stream is not None:
        print(f"Replaying {statute_id}...", file=status_stream)
    replay_meta: dict[str, Any] = {}
    lo_ops: list[Any] = []
    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=statute_id, quiet=True),
        sinks=ReplayXmlSinks(
            replay_meta_out=replay_meta,
            lo_ops_out=lo_ops,
        ),
    )
    source_xml_provider = _source_xml_provider()
    base_ir = IRStatute(
        statute_id=statute_id,
        title=master.title,
        body=master.ctx.base_ir,
    )
    timeline_breaks = attach_effective_dates(
        timeline_breaks_from_findings(getattr(master, "findings", ()) or ()),
        replay_meta.get("lineage") or (),
    )
    from lawvm.core.temporal_scheduler import materialize_temporal_write_windows

    scheduled = materialize_temporal_write_windows(
        master.timelines,
        lo_ops,
        timeline_breaks,
    )
    return ProvisionStateRuntime(
        statute_id=statute_id,
        title=master.title,
        timelines=scheduled.timelines,
        migration_events=tuple(master.migration_events or ()),
        base=base_ir,
        timeline_breaks=scheduled.unresolved_breaks,
        temporal_schedule_deltas=scheduled.deltas,
        findings=tuple(getattr(master, "findings", ()) or ()),
        source_xml_provider=source_xml_provider,
    )


def _source_xml_provider() -> Callable[[str], bytes | None]:
    from lawvm.corpus_store import get_corpus_store

    corpus = get_corpus_store(readonly=True)
    return corpus.read_source
