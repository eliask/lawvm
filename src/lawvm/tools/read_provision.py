"""lawvm read — the clean L1 analyst reading surface.

``read`` is a thin façade over the existing engines. It does NOT introduce a new
replay or reconstruction kernel:

  - Provision scope (a §-selector given) → replay-L1 via ``provision-state``
    (``lawvm.provision_state.resolve_provision_state``), rendered cleanly by
    default; ``--json`` returns the byte-identical ``lawvm.provision_state.v1``
    pin that MeVM depends on.
  - Statute scope (no selector) → whole-statute replay (the same engine the
    ``replay --as-of --show-text`` command uses).
  - ``--raw`` / ``--raw --temporal-labels`` → drill down to L0 consolidated
    prose at the SAME selector (delegates to ``oracle-text``).
  - ``--xml`` → drill down to the raw archived source XML at the same selector
    (delegates to ``source-dump``).

The shared ``§a:b.c.d`` selector (``core.selector``) is lowered to the legacy
locator form before being handed to any engine, so the analyst never has to
retype a different grammar across the drill-down ladder.
"""
from __future__ import annotations

import datetime
import json
import sys
from typing import Any

from lawvm.core.selector import to_locator_string


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _provenance_footer(payload: dict[str, Any]) -> str:
    """One-line provenance footer: eff <date> · src <amendment> · query <type>."""
    version = payload.get("version") or {}
    source = payload.get("source") or {}
    query = payload.get("query") or {}
    eff = version.get("effective") or "—"
    if eff in ("0000-00-00", ""):
        eff = "(base)"
    src = source.get("statute_id") or "(base statute)"
    qt = query.get("query_type") or "in_force"
    state = version.get("content_state") or ""
    bits = [f"eff {eff}", f"src {src}", f"query {qt}"]
    if state and state != "live":
        bits.append(state)
    return "── " + " · ".join(bits)


def _render_provision_human(payload: dict[str, Any]) -> str:
    """Clean human render of a resolved replay-L1 provision-state payload."""
    lines: list[str] = []
    statute = payload.get("statute_id", "")
    query = payload.get("query") or {}
    # Prefer the human-typed selector (e.g. "§3:1") over the lowered locator.
    provision = payload.get("display_selector") or query.get("provision", "")
    as_of = query.get("as_of", "")
    title = payload.get("title", "")

    header = f"{statute} {provision}".rstrip()
    if as_of:
        header += f"  (in force @ {as_of})"
    lines.append(header)
    if title:
        lines.append(f"  {title}")

    status = payload.get("provision_status", "")
    if status != "selected":
        # Surface non-resolution honestly rather than printing empty text.
        lines.append("")
        lines.append(f"  [{status}]")
        candidates = payload.get("address_candidates") or []
        if candidates:
            lines.append("  candidate addresses:")
            for c in candidates:
                lines.append(f"    - {c.get('text', '')}")
        return "\n".join(lines)

    text = (payload.get("text") or {})
    rendered = text.get("rendered", "")
    available = text.get("available", False)
    lines.append("")
    if available and rendered:
        lines.append(f"  {rendered}")
    elif (payload.get("version") or {}).get("content_state") == "tombstone":
        lines.append("  [tombstone — provision repealed / no live text at this date]")
    else:
        lines.append("  [no text available]")

    lines.append("")
    lines.append("  " + _provenance_footer(payload))
    return "\n".join(lines)


def _resolve_replay_l1(args: Any, locator: str) -> dict[str, Any]:
    from lawvm.provision_state import resolve_provision_state

    return resolve_provision_state(
        statute_id=args.statute_id,
        jurisdiction=getattr(args, "jurisdiction", "fi"),
        provision=locator,
        as_of=getattr(args, "as_of", "") or _today_iso(),
        query_type=getattr(args, "query_type", "in_force"),
        territory=getattr(args, "territory", None),
        include_ir=getattr(args, "include_ir", False),
        status_stream=sys.stderr,
    )


def _provision_state_is_invalid(payload: dict[str, Any]) -> bool:
    return payload.get("provision_status") in {"invalid_address", "invalid_query"}


def _emit_invalid_provision_state_diagnostic(payload: dict[str, Any]) -> None:
    from lawvm.tools.provision_state import _emit_cli_diagnostic

    _emit_cli_diagnostic(payload, stream=sys.stderr)


def _run_raw(args: Any, selector: str) -> None:
    """Drill down to L0 consolidated prose (oracle-text) at the same selector."""
    from lawvm.tools.oracle_text import build_oracle_text_bundle, _format_text

    # oracle-text takes the legacy section_filter (eId or chapter:/section:).
    section_filter = to_locator_string(selector) if selector else ""
    bundle = build_oracle_text_bundle(
        args.statute_id,
        section_filter=section_filter,
        at_amendment=getattr(args, "at_amendment", "") or "",
        show_subsections=getattr(args, "subsections", False),
        temporal_labels=getattr(args, "temporal_labels", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
    else:
        print(_format_text(bundle))


def _run_xml(args: Any, selector: str) -> None:
    """Drill down to the raw archived source XML (source-dump) at the same selector."""
    from lawvm.tools.source_dump import main as source_dump_main

    # source-dump consumes args.address (a locator) + args.statute_id + args.json.
    address = to_locator_string(selector) if selector else None
    # Build a shim args object carrying exactly what source-dump reads.
    shim = _Shim(
        statute_id=args.statute_id,
        address=address,
        json=getattr(args, "json", False),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
        db=getattr(args, "db", None),
    )
    source_dump_main(shim)


def _run_statute_scope(args: Any) -> None:
    """No selector → whole-statute replay (== replay --as-of --show-text)."""
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, call_replay_xml
    from lawvm.core.ir_helpers import irnode_to_text

    as_of = getattr(args, "as_of", "") or _today_iso()
    result = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id=args.statute_id,
            mode="legal_pit",
            as_of=as_of,
            quiet=True,
        ),
    )

    if getattr(args, "json", False):
        # Reuse the statute-level replay JSON shape the replay command emits.
        out = {
            "statute_id": args.statute_id,
            "title": result.title,
            "as_of": as_of,
            "sections": [],
        }
        for addr, timeline in result.timelines.items():
            from lawvm.core.timeline_selection import select_active_version_ex

            sel = select_active_version_ex(timeline, as_of=as_of, query_type="in_force")
            ver = sel.version
            if ver is None or ver.content is None:
                continue
            out["sections"].append(
                {
                    "address": str(addr),
                    "effective": ver.effective,
                    "text": irnode_to_text(ver.content),
                }
            )
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    print(f"{args.statute_id} — {result.title}  (in force @ {as_of})")
    print()
    from lawvm.core.timeline_selection import select_active_version_ex

    rows = []
    for addr, timeline in result.timelines.items():
        sel = select_active_version_ex(timeline, as_of=as_of, query_type="in_force")
        ver = sel.version
        if ver is None or ver.content is None:
            continue
        rows.append((str(addr), ver.effective, irnode_to_text(ver.content)))
    for addr, eff, text in rows:
        print(f"§ {addr}   (eff {eff or '—'})")
        print(f"  {text}")
        print()


class _Shim:
    """Minimal attribute carrier for delegating to engines that read args.*"""

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:  # default for unread attrs
        return None


def main(args: Any) -> None:
    selector = getattr(args, "selector", "") or ""

    # Drill-down modes route the SAME selector into L0 engines.
    if getattr(args, "xml", False):
        _run_xml(args, selector)
        return
    if getattr(args, "raw", False):
        _run_raw(args, selector)
        return

    # No selector → whole-statute replay scope.
    if not selector:
        _run_statute_scope(args)
        return

    # Provision scope → replay-L1 via provision-state.
    locator = to_locator_string(selector)
    payload = _resolve_replay_l1(args, locator)
    invalid = _provision_state_is_invalid(payload)
    if invalid:
        _emit_invalid_provision_state_diagnostic(payload)
    if getattr(args, "json", False):
        # Byte-identical to provision-state --json (the MeVM contract).
        # NOTE: display_selector is NOT injected here — JSON stays the pin.
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
        if invalid:
            raise SystemExit(2)
        return
    # Human render only: carry the typed selector for the header, without
    # touching the JSON pin shape.
    payload_for_render = dict(payload)
    payload_for_render["display_selector"] = selector
    print(_render_provision_human(payload_for_render))
    if invalid:
        raise SystemExit(2)
