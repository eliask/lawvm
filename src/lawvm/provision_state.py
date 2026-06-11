"""Public provision-state seam API."""

from __future__ import annotations

from typing import Any, TextIO

from lawvm.core.ir import IRStatute
from lawvm.tools.provision_state import (
    build_provision_state_response,
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

    from lawvm.finland.grafter import replay_xml

    if status_stream is not None:
        print(f"Replaying {statute_id}...", file=status_stream)
    master = replay_xml(statute_id, quiet=True)
    base_ir = IRStatute(
        statute_id=statute_id,
        title=master.title,
        body=master.ctx.base_ir,
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
    )
