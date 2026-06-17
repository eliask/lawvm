"""Tests for local defined-term / alias binding resolution in the H1 REFERENCES
lens.

A statute introduces a short local name for a cited act (``… asetuksessa (EY)
N:o 1069/2009, jäljempänä sivutuoteasetus …``) and then USES that name inflected
later (``sivutuoteasetuksen 3 artiklassa``). The lens builds a per-statute
defined-term table (re-anchored into the unit's ``xml_bytes`` byte space) and
hands it to ``resolve_mentions`` so the later inflected use resolves to the
locally-bound act EVEN WHEN the statute-name registry would miss it.

This proves the binding's byte-space alignment is correct: the binding site
(re-anchored onto the term token in ``xml_bytes``) precedes the use (re-anchored
onto its surface in the SAME ``xml_bytes``), so the resolver's "binding precedes
use" ordering admits the resolution and the graph carries a ``refers_to`` edge to
the bound act entity.
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import SourceUnitRef
from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.references import ReferenceLens

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A statute that binds the alias ``sivutuoteasetus`` to the EU act (EY) N:o
# 1069/2009 via ``jäljempänä``, then uses it inflected (``sivutuoteasetuksen``).
_XML = (
    f'<akomaNtoso xmlns="{_AKN}" '
    'xmlns:finlex="http://data.finlex.fi/schema/finlex"><act><body>'
    "<section><num>1 §</num><paragraph><content>"
    "<p>Eläimistä saatavista sivutuotteista annetussa asetuksessa "
    "(EY) N:o 1069/2009, jäljempänä sivutuoteasetus, säädetään asioista.</p>"
    "<p>Tämän lain nojalla sivutuoteasetuksen 3 artiklassa tarkoitettua "
    "toimintaa valvotaan.</p>"
    "</content></paragraph></section></body></act></akomaNtoso>"
).encode("utf-8")

_STATUTE_ID = "2099/1"


class _MissingRegistry:
    """A statute-name registry that resolves NOTHING.

    Forces the by-name placeholder (``fi-name:sivutuoteasetus``) to fall through
    to ``statute_only`` UNLESS the local defined-term table resolves it — so the
    test isolates the local-binding contribution from any registry recall.
    """

    def lookup(self, name: str, as_of: object = None) -> object:
        class _Result:
            status = "none"
            candidates: tuple[object, ...] = ()

        return _Result()


def _run(options: dict[str, object]):
    bundle = build_surface_bundle(_XML, _STATUTE_ID, surface_time="2020-06-01")
    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(surface_time="2020-06-01", options=options)
    return bundle, lens.analyze(bundle, context=ctx)


def _assemble(bundle, result):
    unit = bundle.units[0]
    source_units = (
        SourceUnitRef(
            source_unit_id=unit.source_unit_id,
            work_id=unit.work_id,
            address=unit.address,
            source_hash=unit.source_hash,
        ),
    )
    return assemble_surface_graph(
        subject=bundle.subject,
        source_units=source_units,
        lens_results=(result,),
    )


def test_local_alias_use_resolves_via_defined_term_binding() -> None:
    # WITHOUT the registry resolving anything, the local binding alone must make
    # the inflected use resolve to the bound act (1069/2009).
    _bundle, result = _run({"statute_registry": _MissingRegistry()})
    assert result.coverage["resolution_enabled"] is True

    # The bound act becomes a legal_work_entity + an asserted refers_to edge.
    entity_ids = {
        s.payload.get("work_id")
        for s in result.node_seeds
        if s.node_kind == "legal_work_entity"
    }
    assert "1069/2009" in entity_ids, (
        "the locally-bound act must be minted as an entity via the alias use"
    )
    refers_to = [e for e in result.edge_seeds if e.edge_kind == "refers_to"]
    assert any(e.payload.get("work_id") == "1069/2009" for e in refers_to)


def test_local_binding_resolution_round_trips_to_graph() -> None:
    bundle, result = _run({"statute_registry": _MissingRegistry()})
    graph = _assemble(bundle, result)

    entity_ids = {
        nid
        for nid, n in graph.nodes.items()
        if n.node_kind == "legal_work_entity" and n.payload.get("work_id") == "1069/2009"
    }
    assert entity_ids, "bound act entity must exist in the assembled graph"

    g_refers = [e for e in graph.edges if e.edge_kind == "refers_to"]
    to_bound = [e for e in g_refers if e.dst in entity_ids]
    assert to_bound, "a refers_to edge must point at the locally-bound act"
    for edge in to_bound:
        assert edge.src in graph.nodes
        assert graph.nodes[edge.src].node_kind == "reference_resolution"
        # firewall holds for the locally-resolved edge too
        assert edge.surface_only is True
        assert edge.replay_authorized is False


def test_use_before_binding_does_not_resolve_locally() -> None:
    # If the alias is USED (inflected) before it is bound, the local table must
    # NOT resolve that earlier use (binding-precedes-use ordering). Here the use
    # is in §1 and the jäljempänä binding only in §2.
    xml = (
        f'<akomaNtoso xmlns="{_AKN}"><act><body>'
        "<section><num>1 §</num><paragraph><content>"
        "<p>Tämän lain nojalla sivutuoteasetuksen 3 artiklassa tarkoitettua "
        "toimintaa valvotaan.</p>"
        "</content></paragraph></section>"
        "<section><num>2 §</num><paragraph><content>"
        "<p>Eläimistä saatavista sivutuotteista annetussa asetuksessa "
        "(EY) N:o 1069/2009, jäljempänä sivutuoteasetus, säädetään.</p>"
        "</content></paragraph></section>"
        "</body></act></akomaNtoso>"
    ).encode("utf-8")
    bundle = build_surface_bundle(xml, "2099/2", surface_time="2020-06-01")
    lens = ReferenceLens()
    ctx = SurfaceAnalysisContext(
        surface_time="2020-06-01", options={"statute_registry": _MissingRegistry()}
    )
    result = lens.analyze(bundle, context=ctx)

    # The earlier inflected use must NOT have resolved to the bound act via the
    # local binding (it precedes the binding site). No refers_to to 1069/2009
    # may originate from the local binding for that use.
    local_refers = [
        e
        for e in result.edge_seeds
        if e.edge_kind == "refers_to" and e.payload.get("work_id") == "1069/2009"
    ]
    assert not local_refers, (
        "a use preceding its binding site must not resolve via the local table"
    )
