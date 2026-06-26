"""Gate for the Layer-2 enclosing-section anaphora composition.

The enclosing-anaphora pass (``norm_composition.EnclosingAnaphoraPass``, fed by the
``EnclosingAnaphoraLens``) resolves ``Tätä pykälää / Tätä momenttia ei sovelleta …``
against the unit's ``provision_index``: the anaphor's enclosing §/momentti IS the
referent, so the qualifier attaches (via the existing condition/exception edge
kinds, reason ``resolved_by_enclosing_provision``) to that provision's deontic
cores — and ONLY that provision's (a section anaphor never reaches a sibling
section; a momentti anaphor never reaches a sibling momentti).

  (a) a SECTION anaphor (``Tätä pykälää``) with one core in its section → one
      "asserted" edge attaching to that section's core (and NOT a sibling
      section's core);
  (b) a MOMENTTI anaphor (``Tätä momenttia``) → attaches to its OWN subsection's
      core(s) only;
  (c) a SECTION anaphor whose section has several cores → one edge per core,
      status "ambiguous", full candidate-core set in payload (never a silent pick);
  (d) a WHOLE-LAW anaphor (``Tätä lakia``) → NO edge, a typed
      ``ENCLOSING_SCOPE_WHOLE_LAW`` diagnostic (too broad to be one target);
  (e) an anaphor whose enclosing provision has NO deontic core → NO edge, a typed
      ``NO_CORE_IN_ENCLOSING_PROVISION`` diagnostic;
  (f) firewall: every emitted edge is surface_only / not replay_authorized;
  (g) determinism: building the same statute twice yields identical edges +
      graph_id;
  (h) strict superset: the pre-existing intra-sentence condition/exception edges
      are UNCHANGED by adding the enclosing pass (it only ADDS enclosing edges).
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import SourceUnitRef, SurfaceLensRun
from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
from lawvm.core.legal_surface_tokens import ProvisionIndex, ProvisionSpan
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import (
    DEFAULT_EDGE_PASSES,
    DEFAULT_LENSES,
    build_legal_surface_graph,
)
from lawvm.finland.legal_surface.norm_composition import (
    ATTACHMENT_RESOLVED_BY_ENCLOSING,
    EDGE_CONDITION_ATTACHES,
    EDGE_EXCEPTION_EXCEPTS,
    ENCLOSING_CUE_NODE_KIND,
    ENCLOSING_SCOPE_WHOLE_LAW,
    NO_CORE_IN_ENCLOSING_PROVISION,
    EnclosingAnaphoraPass,
    condition_attachment_passes,
    enclosing_anaphora_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_NORM_KINDS = {EDGE_CONDITION_ATTACHES, EDGE_EXCEPTION_EXCEPTS}
_ENCLOSING_SOURCE = "construction_enclosing_anaphora"


def _section(eid: str, num: str, *paragraphs: str) -> str:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'  <section eId="{eid}"><num>{num}</num><content>\n{body}\n'
        f"  </content></section>"
    )


def _section_with_subsecs(eid: str, num: str, *subsecs: tuple[str, str]) -> str:
    inner = "\n".join(
        f'    <subsection eId="{seid}"><content><p>{txt}</p></content></subsection>'
        for seid, txt in subsecs
    )
    return f'  <section eId="{eid}"><num>{num}</num>\n{inner}\n  </section>'


def _doc(*sections: str) -> bytes:
    body = "\n".join(sections)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n{body}\n</body></act></akomaNtoso>\n'
    ).encode("utf-8")


def _enclosing_edges(graph):
    return [e for e in graph.edges if e.payload.get("source") == _ENCLOSING_SOURCE]


def _provision_of_core(xml: bytes, statute_id: str, dst) -> ProvisionSpan:
    """The enclosing ProvisionSpan of a deontic-core node (narrowed for ty)."""
    pidx = next(
        u.metadata["provision_index"]
        for u in build_surface_bundle(xml, statute_id).units
    )
    assert isinstance(pidx, ProvisionIndex)
    ref = dst.source_ref
    assert ref is not None
    res = pidx.provision_at(ref.char_start, ref.char_end)
    assert isinstance(res, ProvisionSpan)
    return res


def _diagnostics(xml: bytes, statute_id: str) -> list:
    """Run the pass standalone over a built graph to read its typed diagnostics."""
    graph = build_legal_surface_graph(xml, statute_id)
    bundle = build_surface_bundle(xml, statute_id)
    ap = EnclosingAnaphoraPass(units=bundle.units)
    ap.run(graph)
    return ap.unattached


# ── (a) resolved SECTION anaphor → its own section's core, not a sibling's ────


def test_section_anaphor_attaches_to_own_section_core_only() -> None:
    # §4 has one core (voidaan) + the anaphor; §5 has its own core (voidaan).
    # "Tätä pykälää ei sovelleta" in §4 must attach to §4's core, NOT §5's.
    xml = _doc(
        _section(
            "sec_4",
            "4 §",
            "Viranomainen voidaan velvoittaa korvaukseen.",
            "Tätä pykälää ei sovelleta, jos hakemus on myöhässä.",
        ),
        _section("sec_5", "5 §", "Ministeriö voidaan oikeuttaa luvan myöntämiseen."),
    )
    graph = build_legal_surface_graph(xml, "111/2025")
    enc = _enclosing_edges(graph)
    assert enc, "expected an enclosing-anaphora edge"
    assert all(e.edge_kind == EDGE_EXCEPTION_EXCEPTS for e in enc)
    # exactly one core in §4 → asserted, attaching to §4's core
    assert len(enc) == 1
    e = enc[0]
    assert e.surface_edge_status == "asserted"
    assert e.payload["attachment"] == ATTACHMENT_RESOLVED_BY_ENCLOSING
    assert e.payload["enclosing_provision"] == "4"
    # the dst core sits in §4 (its enclosing provision), never §5
    dst = graph.nodes[e.dst]
    assert dst.node_kind == "deontic_core"
    res = _provision_of_core(xml, "111/2025", dst)
    assert res.section_label == "4"
    # the source is the enclosing_anaphor_cue node
    assert graph.nodes[e.src].node_kind == ENCLOSING_CUE_NODE_KIND


# ── (b) resolved MOMENTTI anaphor → its own subsection's core only ───────────


def test_momentti_anaphor_attaches_to_own_subsection_only() -> None:
    # §7 subsec 1 (a core) and subsec 2 (a core + the momentti anaphor). The
    # "Tätä momenttia ei sovelleta" in subsec 2 must attach to subsec 2's core,
    # NOT subsec 1's.
    xml = _doc(
        _section_with_subsecs(
            "sec_7",
            "7 §",
            ("sec_7__subsec_1", "Hakija voidaan velvoittaa toimittamaan selvitys."),
            (
                "sec_7__subsec_2",
                "Viranomainen voidaan oikeuttaa perimään maksu. "
                "Tätä momenttia ei sovelleta valtion virastoihin.",
            ),
        ),
    )
    graph = build_legal_surface_graph(xml, "222/2025")
    enc = _enclosing_edges(graph)
    assert enc, "expected a momentti enclosing-anaphora edge"
    for e in enc:
        assert e.payload["anaphor_scope"] == "subsection"
        # attaches within subsec 2 (provision path 7/2), never 7/1
        assert e.payload["enclosing_provision"] == "7/2"
        dst = graph.nodes[e.dst]
        res = _provision_of_core(xml, "222/2025", dst)
        assert res.section_label == "7"
        assert res.subsection_num == 2


# ── (c) ambiguous: a section with several cores → one edge per core ──────────


def test_section_anaphor_with_several_cores_is_ambiguous() -> None:
    xml = _doc(
        _section(
            "sec_9",
            "9 §",
            "Viranomainen voidaan velvoittaa. Hakija voidaan oikeuttaa.",
            "Tätä pykälää ei sovelleta sähköiseen asiointiin.",
        ),
    )
    graph = build_legal_surface_graph(xml, "333/2025")
    enc = _enclosing_edges(graph)
    assert len(enc) >= 2, "several cores in the section → one edge per core"
    for e in enc:
        assert e.surface_edge_status == "ambiguous"
        assert e.payload["enclosing_provision"] == "9"
        # the full candidate-core set rides in every ambiguous edge
        assert "candidate_core_spans" in e.payload
        assert len(e.payload["candidate_core_spans"]) == len(enc)


# ── (d) whole-law anaphor → typed diagnostic, no edge ────────────────────────


def test_whole_law_anaphor_is_diagnostic_not_attached() -> None:
    xml = _doc(
        _section(
            "sec_1",
            "1 §",
            "Viranomainen voidaan velvoittaa korvaukseen.",
            "Tätä lakia ei sovelleta puolustusvoimien hankintoihin.",
        ),
    )
    graph = build_legal_surface_graph(xml, "444/2025")
    assert not _enclosing_edges(graph), "whole-law anaphor must not attach"
    diags = _diagnostics(xml, "444/2025")
    assert any(
        d.reason == ENCLOSING_SCOPE_WHOLE_LAW and d.scope == "whole_law" for d in diags
    ), "expected a whole-law-too-broad diagnostic"


# ── (e) enclosing provision with no core → typed diagnostic ──────────────────


def test_anaphor_with_no_core_in_provision_is_diagnostic() -> None:
    # §3 has the anaphor but NO modal core (no voi/voidaan/on velvollinen …).
    xml = _doc(
        _section(
            "sec_3",
            "3 §",
            "Soveltamisala koskee viranomaisten asiakirjoja.",
            "Tätä pykälää ei sovelleta salassa pidettäviin tietoihin.",
        ),
    )
    graph = build_legal_surface_graph(xml, "555/2025")
    assert not _enclosing_edges(graph), "no core in the section → no edge"
    diags = _diagnostics(xml, "555/2025")
    assert any(
        d.reason == NO_CORE_IN_ENCLOSING_PROVISION for d in diags
    ), "expected a no-core-in-enclosing-provision diagnostic"


# ── (f) firewall: every enclosing edge is surface_only / not replay_authorized ─


def test_enclosing_edges_are_surface_only() -> None:
    xml = _doc(
        _section(
            "sec_4",
            "4 §",
            "Viranomainen voidaan velvoittaa korvaukseen.",
            "Tätä pykälää ei sovelleta, jos hakemus on myöhässä.",
        ),
    )
    graph = build_legal_surface_graph(xml, "666/2025")
    enc = _enclosing_edges(graph)
    assert enc
    for e in enc:
        assert e.surface_only is True
        assert e.replay_authorized is False
        assert e.edge_kind in _NORM_KINDS


# ── (g) determinism ──────────────────────────────────────────────────────────


def test_enclosing_anaphora_is_deterministic() -> None:
    xml = _doc(
        _section(
            "sec_4",
            "4 §",
            "Viranomainen voidaan velvoittaa korvaukseen.",
            "Tätä pykälää ei sovelleta, jos hakemus on myöhässä.",
        ),
    )
    g1 = build_legal_surface_graph(xml, "777/2025")
    g2 = build_legal_surface_graph(xml, "777/2025")
    assert g1.graph_id == g2.graph_id
    assert _enclosing_edges(g1)


# ── (h) strict superset: intra-sentence condition/exception edges unchanged ───


def _build(xml: bytes, statute_id: str, *, with_enclosing: bool):
    bundle = build_surface_bundle(xml, statute_id)
    ctx = SurfaceAnalysisContext(
        surface_time=None, options={"statute_registry": None, "eu_registry": None}
    )
    results = []
    runs = []
    for lens in DEFAULT_LENSES:
        r = lens.analyze(bundle, context=ctx)
        results.append(r)
        runs.append(
            SurfaceLensRun(
                lens_id=lens.lens_id,
                schema_version=lens.schema_version,
                jurisdiction=lens.jurisdiction,
                produced_node_kinds=lens.produces_node_kinds,
                produced_edge_kinds=lens.produces_edge_kinds,
                coverage=r.coverage,
            )
        )
    passes = DEFAULT_EDGE_PASSES + condition_attachment_passes(bundle)
    if with_enclosing:
        passes = passes + enclosing_anaphora_passes(bundle)
    units = tuple(
        SourceUnitRef(
            source_unit_id=u.source_unit_id,
            work_id=u.work_id,
            address=u.address,
            source_hash=u.source_hash,
        )
        for u in bundle.units
    )
    return assemble_surface_graph(
        subject=bundle.subject,
        source_units=units,
        lens_results=tuple(results),
        lens_runs=tuple(runs),
        edge_passes=passes,
    )


def test_intra_sentence_edges_unchanged_by_enclosing_pass() -> None:
    # §4 has BOTH an intra-sentence condition ("jos … voidaan …") AND the enclosing
    # anaphor. The intra-sentence condition_attaches_norm edge must be byte-
    # identical with vs without the enclosing pass (strict superset).
    xml = _doc(
        _section(
            "sec_4",
            "4 §",
            "Jos hakemus on puutteellinen, viranomainen voidaan hylätä se.",
            "Tätä pykälää ei sovelleta sähköiseen asiointiin.",
        ),
    )

    def cond_exc(graph):
        return sorted(
            (e.src, e.dst, e.edge_kind, e.payload_hash)
            for e in graph.edges
            if e.edge_kind in _NORM_KINDS
            and e.payload.get("source") != _ENCLOSING_SOURCE
        )

    g_off = _build(xml, "888/2025", with_enclosing=False)
    g_on = _build(xml, "888/2025", with_enclosing=True)
    assert cond_exc(g_off) == cond_exc(g_on), (
        "the enclosing pass must not alter pre-existing condition/exception edges"
    )
    # and it DID add enclosing edges
    assert _enclosing_edges(g_on)
    assert not _enclosing_edges(g_off)
