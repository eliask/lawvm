"""``lawvm show`` — pretty human-readable statute tree (FI frontend).

Surface contract: pretty-print the statute tree as a juridical reader
expects — ``1 luku Yleiset säännökset`` / ``5 § Soveltamisala`` /
``a) kohta``. Body and attachments render as one continuous tree (SDOC-13:
a projection must include attachments/schedules unless explicitly scoped
out via ``--no-attachments``).

OSC 8 terminal hyperlinks wrap ``Y/N`` statute-id tokens that appear in
the rendered text. The visible character cells are unchanged; the URL is
attached invisibly (§ SDOC — attachment-supplements render as part of the
same tree). Hyperlink emission is gated through :func:`should_hyperlink`
(``--hyperlinks auto|always|never``, default ``auto``).

This is the *pretty* counterpart to ``lawvm dump`` (technical IR labels:
CHAPTER/SECTION/CONTENT). Today they share the FI replay path through
``call_replay_xml``. The ``--format`` selector carries future html/json
projections without touching the printer surface.

Operating contract: AGENTS.md §2.10 projection plane (a projection is never
the source of truth; it must be re-derivable from a committed dossier).
"""
from __future__ import annotations

import re
import sys

from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core import tree_ops as _tops
from lawvm.core.regex_safety import compile_classifier_regex
from lawvm.finland.ir_tree_dump import (
    format_ir_pretty,
    format_statute_with_attachments,
)
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml
from lawvm.tools.hyperlinks import (
    consolidated_url_from_id,
    hyperlink,
    should_hyperlink,
)


# ``Y/N`` statute-id tokens, e.g. ``578/1995`` — four-digit year minimum
# to avoid catching bare fractions. Routed through ``compile_classifier_regex``
# per AGENTS.md §2.4 (classifier patterns must go through the safety lint +
# sound required-literal prefilter, never raw ``re.compile``).
_STATUTE_TOKEN_RE = compile_classifier_regex(
    r"\b(\d{2,4}/\d{1,5})\b",
    classifier_id="lawvm.tools.show.statute_token",
)


def _hyperlink_statute_tokens(text: str, *, enabled: bool) -> str:
    """Wrap Y/N statute-id tokens in OSC 8 hyperlinks (or pass through).

    ``enabled`` is the caller's :func:`should_hyperlink` decision — the
    function is the only place that emits an OSC sequence for statute ids
    in the rendered tree (single chokepoint, single policy).
    """
    if not enabled:
        return text

    def _wrap(m: "re.Match[str]") -> str:
        sid = m.group(1)
        url = consolidated_url_from_id(sid)
        if url is None:
            return sid
        return hyperlink(sid, url)

    return _STATUTE_TOKEN_RE.sub(_wrap, text)


def _render_text(body_ir, att_supps, *, max_text: int, include_attachments: bool) -> str:
    if include_attachments:
        return format_statute_with_attachments(
            body_ir, att_supps, max_text=max_text, max_table_rows=5
        )
    return format_ir_pretty(body_ir, max_text=max_text, max_table_rows=5)


def main(args) -> None:
    """``lawvm show`` entry point.

    Reuses the FI replay path (the same one ``dump`` uses) so the rendered
    tree is the same materialised PIT the rest of the pipeline produces.
    """
    sid = args.statute_id
    as_of = getattr(args, "as_of", "") or ""
    address = getattr(args, "address", "") or ""
    max_text = int(getattr(args, "max_text", 200))
    include_attachments = not getattr(args, "no_attachments", False)
    use_json = bool(getattr(args, "json", False))

    hyperlinks_mode = getattr(args, "hyperlinks", "auto") or "auto"
    link_enabled = should_hyperlink(hyperlinks_mode, sys.stdout, is_json=use_json)

    replay_meta: dict[str, object] = {}
    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(
            parent_id=sid,
            mode="legal_pit",
            as_of=as_of,
            quiet=True,
        ),
        sinks=ReplayXmlSinks(replay_meta_out=replay_meta),
    )

    if master is None or master.ir is None:
        print(f"No replay result for {sid}", file=sys.stderr)
        sys.exit(1)

    body_ir = master.ir
    att_supps = tuple(getattr(getattr(master, "ctx", None), "attachment_supplements", ()))

    if address:
        # Scoped view: resolve the address against the materialised tree
        # and print just that subtree's pretty form.
        addr = _parse_address(address)
        if addr is None:
            print(f"ERROR: unrecognised address {address!r}", file=sys.stderr)
            sys.exit(2)
        kind, num = addr
        path = _tops.find(body_ir, kind, num)
        if not path:
            print(f"(provision {address} not found in replay output)", file=sys.stderr)
            sys.exit(1)
        node = _tops.resolve(body_ir, path)
        if node is None:
            print(f"(provision {address} not resolvable in replay output)", file=sys.stderr)
            sys.exit(1)
        if use_json:
            import json as _json

            print(_json.dumps(
                {"statute_id": sid, "as_of": as_of, "address": address, "text": irnode_to_text(node)},
                ensure_ascii=False,
                indent=2,
            ))
        else:
            print(f"Statute: {sid}  Address: {address}")
            render = format_ir_pretty(node, max_text=max_text, max_table_rows=5)
            print(_hyperlink_statute_tokens(render, enabled=link_enabled))
        return

    if use_json:
        import json as _json

        from lawvm.finland.ir_serialize import ir_to_json

        doc: dict[str, object] = {
            "statute_id": sid,
            "as_of": as_of,
            "title": getattr(master, "title", ""),
            "body": ir_to_json(body_ir),
        }
        if include_attachments and att_supps:
            doc["attachments"] = [
                {"pdf_name": getattr(s, "pdf_name", ""), "pdf_text_length": getattr(s, "pdf_text_length", 0), "ir": ir_to_json(s.ir)}
                for s in att_supps
            ]
        print(_json.dumps(doc, ensure_ascii=False, indent=2))
        return

    # Default: pretty human-readable with OSC 8 on Y/N statute-id tokens.
    print(f"Statute: {sid}")
    if as_of:
        print(f"As-of  : {as_of}")
    if att_supps and include_attachments:
        print(f"Attachments: {len(att_supps)}")
    print()
    render = _render_text(
        body_ir,
        att_supps,
        max_text=max_text,
        include_attachments=include_attachments,
    )
    print(_hyperlink_statute_tokens(render, enabled=link_enabled))


# ---------------------------------------------------------------------------
# Address parsing — local copy of dump's contract (avoid coupling to dump's
# internal helper exports; the parse is intentionally tiny and string-based
# — no regex required for "kind:label" / "kind:label/kind:label" shapes).
# ---------------------------------------------------------------------------

_ADDR_KINDS = frozenset(
    ("chapter", "section", "subsection", "paragraph", "item", "part", "appendix", "schedule")
)


def _parse_address(addr: str) -> tuple[str, str] | None:
    """Parse ``kind:label`` / ``kind:label/kind:label`` addresses.

    The dump command's address filter accepts the same characters; this
    helper is the surface the show command exposes. Returns ``(kind, num)``
    for the LAST segment so :func:`tree_ops.find` resolves the leaf.
    """
    if not addr:
        return None
    parts = addr.split("/")
    last = parts[-1].strip()
    if ":" not in last:
        return None
    kind, _, label = last.partition(":")
    kind = kind.strip()
    label = label.strip()
    if kind not in _ADDR_KINDS or not label:
        return None
    return kind, label


__all__ = ["main"]
