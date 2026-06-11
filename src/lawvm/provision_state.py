"""Public provision-state seam API."""

from __future__ import annotations

from typing import Any, TextIO

from lawvm.core.ir import IRStatute
from lawvm.tools.provision_state import (
    build_provision_state_response,
    invalid_provision_selector_payload,
    provision_selector_diagnostic,
    unsupported_jurisdiction_payload,
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

    from lawvm.finland.grafter import replay_xml
    from lawvm.tools.timeline_integrity import (
        attach_effective_dates,
        timeline_breaks_from_findings,
    )

    if status_stream is not None:
        print(f"Replaying {statute_id}...", file=status_stream)
    replay_meta: dict[str, Any] = {}
    master = replay_xml(statute_id, quiet=True, replay_meta_out=replay_meta)
    base_ir = IRStatute(
        statute_id=statute_id,
        title=master.title,
        body=master.ctx.base_ir,
    )
    timeline_breaks = attach_effective_dates(
        timeline_breaks_from_findings(getattr(master, "findings", ()) or ()),
        replay_meta.get("lineage") or (),
    )
    return build_provision_state_response(
        timelines=master.timelines,
        migration_events=tuple(master.migration_events or ()),
        statute_id=statute_id,
        jurisdiction=jurisdiction,
        provision=provision,
        as_of=as_of,
        query_type=query_type,
        territory=territory,
        include_ir=include_ir,
        title=master.title,
        base=base_ir,
        timeline_breaks=timeline_breaks,
    )
