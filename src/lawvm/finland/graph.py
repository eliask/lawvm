"""Finnish-specific StatuteGraph builders.

These functions build StatuteGraph objects for Finnish statutes by calling
into the Finland frontend (grafter, amendment_index, cross_refs, delegation).
They are separated from core/graph.py so that core/ remains
jurisdiction-agnostic.

Entry points used by core/graph.py dispatch:
    build_statute_graph_fi(sid)             -> StatuteGraph  (with timelines)
    build_statute_graph_fi_lightweight(sid) -> StatuteGraph  (no timelines)

Entry points used by tools/build.py:
    build_statute_graph_fi            (same as above)
"""
from __future__ import annotations

import re

from lawvm.core.graph import StatuteGraph


async def build_statute_graph_fi(sid: str) -> StatuteGraph:
    """Build a StatuteGraph for one Finnish statute, including provision timelines.

    Replays all amendments via grafter, compiles timelines, and extracts
    delegation + citation edges from the consolidated corpus ZIP.
    """
    from lxml import etree

    from lawvm.core.ir import IRStatute
    from lawvm.xml_ingest import xml_to_ir_node
    from lawvm.core.timeline import compile_timelines
    from lawvm.finland.amendment_index import get_amendment_children
    from lawvm.finland.cross_refs import extract_cross_refs
    from lawvm.finland.delegation import extract_delegations
    from lawvm.finland.grafter import get_corpus, replay_xml, _fi_label_postprocessor

    # 1. Replay amendments, collect LegalOperations
    lo_ops_out: list = []
    master = replay_xml(sid, lo_ops_out=lo_ops_out)

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
    delegations: list = []
    citations: list = []
    con_xml = cs.read_oracle(sid)
    if con_xml is not None:
        try:
            delegations = extract_delegations(con_xml, sid)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        try:
            citations = extract_cross_refs(con_xml, sid)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

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
    from lawvm.finland.cross_refs import extract_cross_refs, extract_eu_refs
    from lawvm.finland.delegation import extract_delegations
    from lawvm.finland.grafter import get_corpus

    # Read base statute XML bytes (KeyError propagates to caller for skip)
    cs = get_corpus()
    base_xml = cs.read_source(sid)
    if base_xml is None:
        raise KeyError(f"statute {sid} not found in corpus")

    # Extract title via regex
    title = ""
    m = re.search(rb'<docTitle[^>]*>(.*?)</docTitle>', base_xml, re.DOTALL)
    if m:
        title = re.sub(r'<[^>]+>', '', m.group(1).decode("utf-8", errors="replace")).strip()
        title = re.sub(r'\s+', ' ', title)

    # Extract statute_type via regex
    statute_type = "statute"
    m2 = re.search(rb'typeStatute[^>]+refersTo="#([^"]+)"', base_xml)
    if m2:
        statute_type = m2.group(1).decode("utf-8", errors="replace")

    # Delegation + citation edges from consolidated ZIP
    delegations: list = []
    citations: list = []
    con_xml = cs.read_oracle(sid)
    if con_xml is not None:
        try:
            delegations = extract_delegations(con_xml, sid)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        try:
            citations = extract_cross_refs(con_xml, sid)
            citations += extract_eu_refs(base_xml, sid)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass

    # Phase 8.4: section-level ISSUED_UNDER — parse preamble for "N §:n nojalla" patterns.
    # Populates target_section on existing ISSUED_UNDER edges and adds edges absent from
    # finlex:issuedUnderActs metadata (which is sometimes incomplete).
    try:
        from lawvm.finland.delegation import extract_asetus_authority
        auth_edges = extract_asetus_authority(base_xml, sid)
        if auth_edges:
            # Build map: parent_statute_id → list of parent_section values
            from collections import defaultdict
            auth_map: dict = defaultdict(list)
            for ae in auth_edges:
                if ae.parent_section:
                    auth_map[ae.parent_statute_id].append(ae.parent_section)

            # Update existing ISSUED_UNDER edges with section info
            existing_targets = set()
            for edge in citations:
                if edge.edge_type == "ISSUED_UNDER":
                    existing_targets.add(edge.target_statute_id)
                    if edge.target_statute_id in auth_map:
                        secs = auth_map[edge.target_statute_id]
                        edge.target_section = ",".join(dict.fromkeys(secs))  # dedup, preserve order

            # Add ISSUED_UNDER edges found in preamble but absent from metadata
            for parent_id, secs in auth_map.items():
                if parent_id not in existing_targets:
                    from lawvm.finland.cross_refs import CrossRefEdge
                    citations.append(CrossRefEdge(
                        source_statute_id=sid,
                        target_statute_id=parent_id,
                        edge_type="ISSUED_UNDER",
                        target_section=",".join(dict.fromkeys(secs)),
                    ))
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass

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
