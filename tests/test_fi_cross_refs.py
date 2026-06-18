from __future__ import annotations

from lawvm.finland.cross_refs import (
    CrossRefDiagnostic,
    extract_affected_document_refs,
    extract_cross_refs,
)


_AMENDING_PREAMBLE = (
    '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    "<act><preamble>"
    '<formula name="enactingClause">'
    "<p>Valtioneuvoston päätöksen mukaisesti</p>"
    "<blockContainer>"
    '<block name="substitutions"><i>muutetaan</i>'
    " ammattikorkeakouluista annetun valtioneuvoston asetuksen ("
    '<affectedDocument href="/akn/fi/act/statute/2014/1129">1129/2014</affectedDocument>'
    ") 3 §, sellaisena kuin se on asetuksessa 1438/2014, seuraavasti:</block>"
    "</blockContainer></formula></preamble>"
    "<body><section><num>3 §</num><paragraph><content><p>Uusi teksti.</p>"
    "</content></paragraph></section></body></act></akomaNtoso>"
).encode("utf-8")


def test_extract_affected_document_refs_emits_amends_edge() -> None:
    edges = extract_affected_document_refs(_AMENDING_PREAMBLE, "2019/1294")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.edge_type == "AMENDS"
    assert edge.source_statute_id == "2019/1294"
    assert edge.target_statute_id == "2014/1129"
    assert edge.count == 1
    # The byte span slices exactly the displayed citation phrase.
    assert edge.source_byte_offset is not None
    sliced = _AMENDING_PREAMBLE[
        edge.source_byte_offset : edge.source_byte_offset + edge.source_byte_len
    ]
    assert sliced == b"1129/2014"


def test_affected_document_target_is_absent_from_body_cites() -> None:
    # The amendment target lives ONLY in the preamble enacting clause, so the
    # inline-<ref> body lane (extract_cross_refs) never sees it — the gap this
    # AMENDS lane closes.
    body_edges = extract_cross_refs(_AMENDING_PREAMBLE, "2019/1294")
    assert all(e.target_statute_id != "2014/1129" for e in body_edges)


def test_extract_affected_document_refs_dedups_repeated_target() -> None:
    # A statute may name the SAME affectedDocument in several enacting-clause
    # blocks (kumotaan / muutetaan) — one amendment relation → one edge.
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><preamble>"
        '<formula name="enactingClause">'
        "<blockContainer>"
        '<block name="repeals"><i>kumotaan</i> lain ('
        '<affectedDocument href="/akn/fi/act/statute/2012/916">916/2012</affectedDocument>'
        ") 11 luvun 4 §,</block></blockContainer>"
        "<blockContainer>"
        '<block name="substitutions"><i>muutetaan</i> '
        '<affectedDocument href="/akn/fi/act/statute/2012/916">916/2012</affectedDocument>'
        " 1 luvun 3 §,</block></blockContainer>"
        "</formula></preamble><body/></act></akomaNtoso>"
    ).encode("utf-8")
    edges = extract_affected_document_refs(xml, "2023/371")
    assert len(edges) == 1
    assert edges[0].target_statute_id == "2012/916"
    assert edges[0].count == 2


def test_extract_affected_document_refs_skips_self_reference() -> None:
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><preamble>"
        '<formula name="enactingClause"><blockContainer>'
        '<block name="substitutions"><i>muutetaan</i> ('
        '<affectedDocument href="/akn/fi/act/statute/2000/1">1/2000</affectedDocument>'
        ")</block></blockContainer></formula></preamble><body/></act></akomaNtoso>"
    ).encode("utf-8")
    diagnostics: list[CrossRefDiagnostic] = []
    edges = extract_affected_document_refs(xml, "2000/1", diagnostics_out=diagnostics)
    assert edges == []
    assert [d.rule_id for d in diagnostics] == ["fi_cross_ref_self_reference_skipped"]
    assert diagnostics[0].edge_type == "AMENDS"


def test_extract_cross_refs_records_xml_parse_failure_when_diagnostics_requested() -> None:
    diagnostics: list[CrossRefDiagnostic] = []

    edges = extract_cross_refs(b"<akomaNtoso>", "2000/1", diagnostics_out=diagnostics)

    assert edges == []
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["fi_cross_ref_xml_parse_failed"]
    assert diagnostics[0].family == "source_pathology"
    assert diagnostics[0].blocking is True
    assert diagnostics[0].strict_disposition == "block"


def test_extract_cross_refs_records_skipped_inline_self_reference() -> None:
    xml = b"""
    <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act>
        <body>
          <section>
            <num>5 \xc2\xa7</num>
            <paragraph>
              <content>
                <p>
                  <ref href="/akn/fi/act/statute/2000/1#sec_5">same act</ref>
                  <ref href="/akn/fi/act/statute/2001/2#sec_9">other act</ref>
                </p>
              </content>
            </paragraph>
          </section>
        </body>
      </act>
    </akomaNtoso>
    """
    diagnostics: list[CrossRefDiagnostic] = []

    edges = extract_cross_refs(xml, "2000/1", diagnostics_out=diagnostics)

    assert [(edge.target_statute_id, edge.target_section) for edge in edges] == [("2001/2", "sec_9")]
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["fi_cross_ref_self_reference_skipped"]
    assert diagnostics[0].edge_type == "CITES"
    assert diagnostics[0].source_section == "5"
    assert diagnostics[0].target_section == "sec_5"
    assert diagnostics[0].blocking is False


def test_extract_cross_refs_records_skipped_metadata_self_reference() -> None:
    xml = b"""
    <akomaNtoso
      xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
      xmlns:finlex="http://data.finlex.fi/schema/finlex">
      <act>
        <meta>
          <finlex:repeals>
            <finlex:ref href="/akn/fi/act/statute/2000/1"/>
            <finlex:ref href="/akn/fi/act/statute/2001/2"/>
          </finlex:repeals>
        </meta>
        <body/>
      </act>
    </akomaNtoso>
    """
    diagnostics: list[CrossRefDiagnostic] = []

    edges = extract_cross_refs(xml, "2000/1", diagnostics_out=diagnostics)

    assert [(edge.edge_type, edge.target_statute_id) for edge in edges] == [("REPEALS", "2001/2")]
    assert [diagnostic.rule_id for diagnostic in diagnostics] == ["fi_cross_ref_self_reference_skipped"]
    assert diagnostics[0].edge_type == "REPEALS"
    assert diagnostics[0].target_statute_id == "2000/1"


def test_inline_ref_byte_span_is_inner_phrase_only() -> None:
    """The CITES byte span must slice exactly the citation phrase.

    Before the fix the span covered the whole ``<ref href="…">…</ref>`` markup
    envelope, so the slice contained ``<ref``/``href=`` markup instead of the
    surface phrase.
    """
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body><section><num>5 §</num><paragraph><content><p>"
        'Katso <ref href="/akn/fi/act/statute/2001/2#sec_9">toinen laki</ref> tarkemmin.'
        "</p></content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    edges = extract_cross_refs(xml, "2000/1")
    cites = [e for e in edges if e.edge_type == "CITES"]
    assert len(cites) == 1
    edge = cites[0]
    assert edge.source_byte_offset is not None
    sliced = xml[edge.source_byte_offset : edge.source_byte_offset + edge.source_byte_len]
    assert sliced == b"toinen laki"
    assert b"<ref" not in sliced and b"href=" not in sliced
    assert " ".join(sliced.decode("utf-8").split()) == edge.surface_text


# ── Authority-basis (nojalla) merge centralized into extract_cross_refs ──────


def _issued_under_doc(preamble_text: str) -> bytes:
    """An AKN doc with a finlex:issuedUnderActs metadata edge + matching preamble.

    The metadata names the authority act (no section, no kind); the preamble
    carries the "N §:n nojalla" clause that supplies both. extract_cross_refs
    must merge them so the edge gains its section + drafting kind.
    """
    return (
        '<akomaNtoso '
        'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
        'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
        "<act><meta><finlex:issuedUnderActs>"
        '<finlex:ref href="/akn/fi/act/statute/2014/1301"/>'
        "</finlex:issuedUnderActs></meta>"
        f"<preamble><p>{preamble_text}</p></preamble>"
        "<body><section><num>1 §</num></section></body></act></akomaNtoso>"
    ).encode("utf-8")


def test_extract_cross_refs_merges_nojalla_section_and_act_kind() -> None:
    # The centralized merge must populate target_section (with the "60a" letter
    # suffix preserved) and target_kind="act" on the ISSUED_UNDER edge, so cite +
    # the surface lens + the builder all see the typed, sectioned edge.
    xml = _issued_under_doc("Säädetään eräiden lain (1301/2014) 60 a §:n nojalla:")

    issued = [e for e in extract_cross_refs(xml, "2024/348") if e.edge_type == "ISSUED_UNDER"]

    assert [(e.target_statute_id, e.target_section, e.target_kind) for e in issued] == [
        ("2014/1301", "60a", "act")
    ]


def test_extract_cross_refs_nojalla_decree_basis_stays_untyped() -> None:
    # A genuine decree authority basis ("…asetuksen (…) … nojalla") must NOT gain
    # target_kind="act" — it stays a non-statutory instrument downstream.
    xml = (
        '<akomaNtoso '
        'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
        'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
        "<act><meta><finlex:issuedUnderActs>"
        '<finlex:ref href="/akn/fi/act/statute/2005/1248"/>'
        "</finlex:issuedUnderActs></meta>"
        "<preamble><p>Säädetään esimerkkiasetuksen (1248/2005) 3 §:n nojalla:</p></preamble>"
        "<body><section><num>1 §</num></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    issued = [e for e in extract_cross_refs(xml, "2099/1") if e.edge_type == "ISSUED_UNDER"]

    assert [(e.target_statute_id, e.target_section, e.target_kind) for e in issued] == [
        ("2005/1248", "3", "decree")
    ]


def test_nojalla_act_typing_agrees_across_projections() -> None:
    # Projection-consistency guard: extract_cross_refs, the StatuteGraph builder,
    # and the surface-graph reference lens (via ref_mention_extractor) MUST agree
    # on the same nojalla-act case — the bug being fixed was that the merge reached
    # only the builder, leaving cite + the lens on the old untyped, sectionless edge.
    import asyncio
    from unittest import mock

    from lawvm.core.reference_mention import CiteKind
    from lawvm.finland.references.ref_mention_extractor import extract_reference_mentions

    sid = "2024/348"
    xml = _issued_under_doc("Säädetään eräiden lain (1301/2014) 60 a §:n nojalla:")

    # 1. extract_cross_refs (the single source of truth)
    xr = [e for e in extract_cross_refs(xml, sid) if e.edge_type == "ISSUED_UNDER"]
    assert [(e.target_statute_id, e.target_section, e.target_kind) for e in xr] == [
        ("2014/1301", "60a", "act")
    ]

    # 2. StatuteGraph lightweight builder — same xml as both base and oracle so the
    #    builder reads it identically; no per-builder merge remains, it inherits the
    #    centralized one. amendment chain stubbed out (no corpus dependency).
    from lawvm.finland import graph as graph_mod

    class _FakeCorpus:
        def read_source(self, _sid: str) -> bytes:
            return xml

        def read_oracle(self, _sid: str) -> bytes:
            return xml

    with (
        mock.patch.object(graph_mod, "_build_statute_graph_fi_lightweight"),
        mock.patch("lawvm.finland.corpus.get_corpus", return_value=_FakeCorpus()),
        mock.patch("lawvm.finland.amendment_index.get_amendment_children", return_value={}),
    ):
        g = asyncio.run(graph_mod.build_statute_graph_fi_lightweight(sid))
    builder_issued = [e for e in g.citations if e.edge_type == "ISSUED_UNDER"]
    assert [(e.target_statute_id, e.target_section, e.target_kind) for e in builder_issued] == [
        ("2014/1301", "60a", "act")
    ]

    # 3. Surface-graph reference lens path (ref_mention_extractor wraps extract_cross_refs):
    #    the act basis must lift to a CROSS_STATUTE mention with the 60a section label.
    res = extract_reference_mentions(xml, sid)
    lens_issued = [
        m
        for m in res.mentions
        if m.phrase_lemma == "ISSUED_UNDER" and m.target_provision_ref.statute_id == "2014/1301"
    ]
    assert len(lens_issued) == 1
    assert lens_issued[0].cite_kind is CiteKind.CROSS_STATUTE
    assert lens_issued[0].target_provision_ref.section_label == "60a"


def test_inline_ref_byte_span_ignores_metadata_duplicate_href() -> None:
    """The locator must not latch onto a duplicate href in the metadata block.

    The same citation href appears first inside a leading ``<references>``
    block; a non-body-scoped search latched onto it and then ``<ref`` (a prefix
    of ``<references``) ran the close-tag forward to a body ``</ref>``, yielding
    a multi-KB span.
    """
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><meta><references>"
        '<TLCReference href="/akn/fi/act/statute/2001/2#sec_9" showAs="x"/>'
        "</references></meta><body><section><num>5 §</num>"
        "<paragraph><content><p>"
        'Katso <ref href="/akn/fi/act/statute/2001/2#sec_9">toinen laki</ref> tarkemmin.'
        "</p></content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    edges = extract_cross_refs(xml, "2000/1")
    cites = [e for e in edges if e.edge_type == "CITES"]
    assert len(cites) == 1
    edge = cites[0]
    assert edge.source_byte_offset is not None
    sliced = xml[edge.source_byte_offset : edge.source_byte_offset + edge.source_byte_len]
    assert sliced == b"toinen laki"
    # Span must be the short phrase, never the multi-KB catastrophe.
    assert edge.source_byte_len < 50
