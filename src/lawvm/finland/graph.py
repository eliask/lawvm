"""Finnish-specific StatuteGraph builders.

These functions build StatuteGraph objects for Finnish statutes by calling
into the Finland frontend (grafter, amendment_index, cross_refs, and the
canonical forward-grant delegation parser). They are separated from
core/graph.py so that core/ remains jurisdiction-agnostic.

Delegation forward-grant source: the canonical token-native parser via
``legal_surface.delegation_edge_adapter.extract_delegations_canonical``. This is
the SHARED source for every offline delegation producer — the StatuteGraph paths
here AND the default lightweight ``lawvm build`` worker
(:func:`lawvm.tools.build._worker_fn`) — so both build paths emit identical
delegation sets for the same corpus. The legacy nine-regex
``delegation.extract_delegations`` is retained as a typed residue / cross-check
oracle (see its docstring) but is no longer the source on any path.

Entry points used by core/graph.py dispatch:
    build_statute_graph_fi(sid)             -> StatuteGraph  (with timelines)
    build_statute_graph_fi_lightweight(sid) -> StatuteGraph  (no timelines)

Entry points used by tools/build.py:
    build_statute_graph_fi            (--with-timelines path)
    delegation_edge_adapter.extract_delegations_canonical  (default lightweight
        _worker_fn delegation source — same canonical source as above)
"""
from __future__ import annotations

import re

from lawvm.core.graph import CitationEdgeLike, DelegationEdgeLike, StatuteGraph
from lawvm.core.named_swallow import log_emitter, named_swallow


async def build_statute_graph_fi(sid: str) -> StatuteGraph:
    """Build a StatuteGraph for one Finnish statute, including provision timelines.

    Replays all amendments via grafter, compiles timelines, and extracts
    delegation + citation edges from the consolidated corpus ZIP.
    """
    from lxml import etree

    from lawvm.core.ir import IRStatute, LegalOperation
    from lawvm.xml_ingest import xml_to_ir_node
    from lawvm.core.timeline import compile_timelines
    from lawvm.finland.amendment_index import get_amendment_children
    from lawvm.finland.references.cross_refs import extract_cross_refs
    from lawvm.finland.legal_surface.delegation_edge_adapter import (
        extract_delegations_canonical,
    )
    from lawvm.finland.corpus import get_corpus
    from lawvm.finland.helpers import _fi_label_postprocessor
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml
    from lawvm.finland.statute_id import engine_statute_id

    # Normalize the säädös id to engine 'year/num' form at this boundary so the
    # corpus read, the amendment-index lookup, and replay all key off the same
    # id. A canonical 'num/year' id would otherwise read no base or, worse,
    # silently resolve to an empty amendment chain (base-only degradation).
    sid = engine_statute_id(sid)

    # 1. Replay amendments, collect LegalOperations
    lo_ops_out: list[LegalOperation] = []
    master = call_replay_xml(
        replay_xml,
        request=ReplayXmlRequest(parent_id=sid),
        sinks=ReplayXmlSinks(lo_ops_out=lo_ops_out),
    )

    # 2. Build base IRStatute from original (unamended) XML
    cs = get_corpus()
    base_xml = cs.read_source(sid)
    if base_xml is None:
        raise KeyError(f"statute {sid} not found in corpus")
    base_el = etree.fromstring(base_xml)
    body_el = base_el.find(".//{*}body")
    body_ir = xml_to_ir_node(
        body_el if body_el is not None else base_el,
        _fi_label_postprocessor,
    )
    base_ir = IRStatute(statute_id=sid, title=master.title, body=body_ir)

    # Extract statute_type from base XML bytes
    statute_type = "statute"
    # lawvm-regex: owning_parser source-plane byte-attribute scrape of statute_type from the base XML the graph builder owns, not legal prose
    m2 = re.search(rb'typeStatute[^>]+refersTo="#([^"]+)"', base_xml)
    if m2:
        statute_type = m2.group(1).decode("utf-8", errors="replace")

    # 3. Compile provision timelines
    # Prefer replay-owned timelines so this graph path does not silently drop
    # Finland temporal_events by rebuilding from bare structural ops.
    timelines = master.timelines
    if timelines is None:
        timelines = compile_timelines(
            base_ir,
            lo_ops_out,
            temporal_events=master.temporal_events,
        )

    # 4. Delegation + citation edges from consolidated ZIP
    delegations: list[DelegationEdgeLike] = []
    citations: list[CitationEdgeLike] = []
    con_xml = cs.read_oracle(sid)
    if con_xml is not None:
        # ``extract_delegations_canonical`` parses the consolidated XML which
        # can raise ValueError / etree.QueryError on malformed oracle bytes.
        # Previously ``except Exception: pass`` silently swallowed to [];
        # now ``named_swallow`` evidence-witnesses the swallow (AGENTS.md §1.10)
        # — the typed Finding is logged at WARNING visibility so a stale/
        # malformed oracle is observable, while still returning [] so a graph
        # build for one broken corpus does not abort a batch run.
        with named_swallow(
            rule_id="fi_graph_build_delegations_extract",
            default=delegations,
            jurisdiction="fi",
            source_artifact=sid,
            clause_text=f"delegations canonical parse: con_xml size={len(con_xml)}",
            emit=log_emitter(),
        ):
            delegations = list(extract_delegations_canonical(con_xml, sid))
        with named_swallow(
            rule_id="fi_graph_build_citations_extract",
            default=citations,
            jurisdiction="fi",
            source_artifact=sid,
            clause_text=f"cross-refs parse: con_xml size={len(con_xml)}",
            emit=log_emitter(),
        ):
            # Parse the preamble "nojalla" authority basis from the BASE XML:
            # Finlex drops the preamble from the consolidated form of older
            # statutes, so the nojalla clause (which supplies the ISSUED_UNDER
            # section + drafting kind) survives only in the base statute XML.
            citations = list(
                extract_cross_refs(con_xml, sid, authority_xml_bytes=base_xml)
            )

    # 5. Amendment chain (statutes that amend sid)
    amendment_chain = get_amendment_children().get(sid, [])

    return StatuteGraph(
        statute_id=sid,
        timelines=timelines,
        delegations=delegations,
        citations=citations,
        amendment_chain=amendment_chain,
        title=master.title,
        statute_type=statute_type,
    )


async def build_statute_graph_fi_lightweight(sid: str) -> StatuteGraph:
    """Build a StatuteGraph with metadata + edges but NO provision timelines.

    Reads title/statute_type from the base statute ZIP via regex (fast, no XML parse).
    Loads delegations and citations from the consolidated ZIP.
    Does NOT call replay_xml — suitable for bulk export use cases.
    """
    from lawvm.finland.amendment_index import get_amendment_children
    from lawvm.finland.references.cross_refs import extract_cross_refs, extract_eu_refs
    from lawvm.finland.legal_surface.delegation_edge_adapter import (
        extract_delegations_canonical,
    )
    from lxml import etree

    from lawvm.finland.corpus import get_corpus
    from lawvm.finland.statute_id import engine_statute_id

    # Normalize to engine 'year/num' form so corpus read and amendment-index
    # lookup agree (see build_statute_graph_fi).
    sid = engine_statute_id(sid)

    # Read base statute XML bytes (KeyError propagates to caller for skip)
    cs = get_corpus()
    base_xml = cs.read_source(sid)
    if base_xml is None:
        raise KeyError(f"statute {sid} not found in corpus")

    # Extract title from the docTitle element as plain text. Parse with lxml
    # rather than regex-scraping markup: the prior regex captured inner markup
    # and stripped tags afterwards, which is exactly what itertext() yields,
    # without mishandling entities or nested tags.
    title = ""
    try:
        _base_el = etree.fromstring(base_xml)
    except etree.XMLSyntaxError:
        _base_el = None
    if _base_el is not None:
        _title_el = _base_el.find(".//{*}docTitle")
        if _title_el is not None:
            title = re.sub(r"\s+", " ", "".join(str(t) for t in _title_el.itertext())).strip()

    # Extract statute_type via regex
    statute_type = "statute"
    # lawvm-regex: owning_parser source-plane byte-attribute scrape of statute_type from the base XML the graph builder owns, not legal prose
    m2 = re.search(rb'typeStatute[^>]+refersTo="#([^"]+)"', base_xml)
    if m2:
        statute_type = m2.group(1).decode("utf-8", errors="replace")

    # Delegation + citation edges from consolidated ZIP
    delegations: list[DelegationEdgeLike] = []
    citations: list[CitationEdgeLike] = []
    con_xml = cs.read_oracle(sid)
    if con_xml is not None:
        # Mirror build_statute_graph_fi: swallow-and-witness via named_swallow
        # (AGENTS.md §1.10). Visible WARNING path keeps [] default so a single
        # broken oracle does not abort a bulk export.
        with named_swallow(
            rule_id="fi_graph_lightweight_delegations_extract",
            default=delegations,
            jurisdiction="fi",
            source_artifact=sid,
            clause_text=f"delegations canonical parse: con_xml size={len(con_xml)}",
            emit=log_emitter(),
        ):
            delegations = list(extract_delegations_canonical(con_xml, sid))
        with named_swallow(
            rule_id="fi_graph_lightweight_citations_extract",
            default=citations,
            jurisdiction="fi",
            source_artifact=sid,
            clause_text=f"lightweight cross-refs parse: con_xml size={len(con_xml)}",
            emit=log_emitter(),
        ):
            # Parse the preamble "nojalla" authority basis from the BASE XML:
            # Finlex drops the preamble from the consolidated form of older
            # statutes, so the nojalla clause (which supplies the ISSUED_UNDER
            # section + drafting kind) survives only in the base statute XML.
            citations = list(
                extract_cross_refs(con_xml, sid, authority_xml_bytes=base_xml)
            )
            citations.extend(extract_eu_refs(base_xml, sid))

    # The ISSUED_UNDER section + drafting-kind enrichment from the preamble
    # "N §:n nojalla" clause is now applied centrally inside extract_cross_refs
    # (above), so cite + the surface-graph lens + this builder share one source
    # of truth for the authority-basis typing. No per-builder merge here.

    amendment_chain = get_amendment_children().get(sid, [])

    return StatuteGraph(
        statute_id=sid,
        timelines={},
        delegations=delegations,
        citations=citations,
        amendment_chain=amendment_chain,
        title=title,
        statute_type=statute_type,
    )


# Keep old private names as aliases so any direct importers still work.
# These were previously defined in core/graph.py; the canonical location
# is now this module.
_build_statute_graph_fi = build_statute_graph_fi
_build_statute_graph_fi_lightweight = build_statute_graph_fi_lightweight
