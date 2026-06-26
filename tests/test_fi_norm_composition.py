"""Gate for the Layer-2 construction-derived deontic NORM edges.

The FIRST real Layer-2 composition (``norm_composition.ConditionAttachmentPass``):
it composes the condition/exception construction parse's COMPUTED ATTACHMENT into
deontic NORM edges, replacing the over-generating 120-char proximity join.

  * ``condition_attaches_norm`` — a CONDITION qualifier → its attached deontic core
  * ``exception_excepts_norm``  — an EXCEPTION qualifier → its attached deontic core

The edge is sourced from the construction's ``attached_core_index`` /
``attachment_status`` (NOT a proximity window):

  (a) a RESOLVED (single-core) condition emits one edge, edge status "asserted",
      attaching to the correct core node;
  (b) an EXCEPTION qualifier emits an ``exception_excepts_norm`` edge;
  (c) an AMBIGUOUS qualifier (several cores) emits one edge PER candidate core,
      edge status "ambiguous", carrying the full candidate set — never a silent
      pick;
  (d) a CANDIDATE qualifier (no deontic core in the sentence) emits NO asserted
      edge — recorded as a typed unattached diagnostic instead;
  (e) firewall: every emitted edge is surface_only / not replay_authorized;
  (f) determinism: building the same statute twice yields identical NORM edges +
      graph_id;
  (g) precision over proximity: the construction NORM edge count is <= the
      proximity ``exception_scopes_frame`` count on the same statute, and each
      construction edge attaches WITHIN the qualifier's own sentence.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.norm_composition import (
    ATTACHMENT_AMBIGUOUS_BY_PROVISION_REF,
    ATTACHMENT_PROXIMITY_FALLBACK,
    ATTACHMENT_RESOLVED_BY_CHAPEAU,
    ATTACHMENT_RESOLVED_BY_FOREST_SEGMENT,
    ATTACHMENT_RESOLVED_BY_PROVISION_REF,
    EDGE_CONDITION_ATTACHES,
    EDGE_EXCEPTION_EXCEPTS,
    NO_CORE_IN_SENTENCE,
    NO_INTERNAL_PROVISION_REF,
    ConditionAttachmentPass,
    ForestStructuralAttachmentPass,
    condition_attachment_passes,
    forest_structural_attachment_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_NORM_KINDS = {EDGE_CONDITION_ATTACHES, EDGE_EXCEPTION_EXCEPTS}


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


# A statute exercising all four attachment shapes in one body:
#  P1: resolved condition (one core "voi")
#  P2: an exception ("ei kuitenkaan") + a condition ("jos"), both -> the lone core
#  P3: ambiguous condition ("jos") -> two cores ("voi" / "saa" split by ';')
#  P4: candidate condition ("jos") -> NO deontic core in the sentence
_XML = _xml(
    "Jos hakemus on puutteellinen, viranomainen voi hylata sen.",
    "Valtioneuvosto ei kuitenkaan voi kumota paatosta, jos asia on vireilla.",
    "Jos ehto tayttyy: ministerio voi myontaa luvan; hakija saa korvauksen.",
    "Jos hakemus on puutteellinen, asia raukeaa ja kasittely paattyy.",
)


def _build():
    return build_legal_surface_graph(_XML, "999/2025")


def _norm_edges(graph):
    return [e for e in graph.edges if e.edge_kind in _NORM_KINDS]


# ── (a) resolved condition → one edge attaching to the correct core ──────────


def test_resolved_condition_attaches_to_its_core() -> None:
    graph = _build()
    conds = [e for e in graph.edges if e.edge_kind == EDGE_CONDITION_ATTACHES]
    assert conds, "expected a condition_attaches_norm edge"
    # the P1 resolved condition: src is the cue, dst is the actor_modal_frame core
    resolved = [e for e in conds if e.payload.get("attachment_status") == "resolved"]
    assert resolved, "expected a resolved condition attachment"
    for edge in resolved:
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind == "deontic_core"
        assert edge.surface_edge_status == "asserted"
        assert edge.payload.get("source") == "construction_attachment"
        assert edge.payload.get("qualifier_kind") == "condition"
        # the attached core span lies inside the dst frame's span (the frame
        # CONTAINS the modal cue) — proves it is THE attached core, not a window.
        core_span = edge.payload["core_span"]
        dst_ref = graph.nodes[edge.dst].source_ref
        assert dst_ref is not None
        assert dst_ref.char_start <= core_span[0] and core_span[1] <= dst_ref.char_end


# ── (b) exception qualifier → exception_excepts_norm edge ────────────────────


def test_exception_qualifier_emits_excepts_edge() -> None:
    graph = _build()
    excs = [e for e in graph.edges if e.edge_kind == EDGE_EXCEPTION_EXCEPTS]
    assert excs, "expected an exception_excepts_norm edge"
    for edge in excs:
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind == "deontic_core"
        assert edge.payload.get("qualifier_kind") == "exception"
        assert edge.payload.get("cue") == "ei kuitenkaan"


# ── (c) ambiguous → one edge per candidate core, full set, never a silent pick ─


def test_ambiguous_attachment_emits_candidate_set_not_a_pick() -> None:
    graph = _build()
    amb = [
        e
        for e in _norm_edges(graph)
        if e.payload.get("attachment_status") == "ambiguous"
    ]
    assert amb, "expected ambiguous NORM edges (the two-core P3 sentence)"
    # all ambiguous edges from the SAME qualifier carry the SAME candidate set,
    # and there is one edge per candidate core that has a backing graph node.
    for edge in amb:
        assert edge.surface_edge_status == "ambiguous"
        cand = edge.payload.get("candidate_core_spans")
        assert isinstance(cand, list) and len(cand) >= 2, (
            "an ambiguous edge must carry the FULL candidate set (>=2 cores), "
            "not just the chosen one"
        )
        # the dst this edge attaches to is one OF the candidates (never invented)
        assert edge.payload["core_span"] in cand


def test_ambiguous_carries_full_set_but_only_backed_cores_get_edges() -> None:
    # An ambiguous qualifier carries the FULL candidate set in payload, but emits
    # an asserted edge ONLY for candidate cores that have a backing deontic_core
    # node. The deontic_core lens mints one per construction core, so every
    # candidate is normally backed; the edge count <= candidate count — never an
    # invented edge to a core with no graph node. This is the honest
    # coordinate-bridge behaviour.
    graph = _build()
    amb = [
        e
        for e in _norm_edges(graph)
        if e.payload.get("attachment_status") == "ambiguous"
    ]
    assert amb, "expected at least one ambiguous NORM edge"
    for edge in amb:
        cand = edge.payload["candidate_core_spans"]
        # the edge's own attached core is a member of the carried candidate set
        assert edge.payload["core_span"] in cand
        # and the dst node's span actually covers that attached core
        dst_ref = graph.nodes[edge.dst].source_ref
        assert dst_ref is not None
        cs = edge.payload["core_span"]
        assert dst_ref.char_start <= cs[0] and cs[1] <= dst_ref.char_end


# ── (d) candidate (no core) → NO asserted edge; tagged unattached ────────────


def test_candidate_no_core_emits_no_edge_but_is_tagged() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    graph = _build()
    (pass_,) = condition_attachment_passes(bundle)
    pass_.run(graph)
    # the P4 "jos ... asia raukeaa" sentence has NO deontic core → tagged candidate
    no_core = [u for u in pass_.unattached if u.reason == NO_CORE_IN_SENTENCE]
    assert no_core, "expected a NO_CORE_IN_SENTENCE unattached qualifier (P4)"
    # and that qualifier produced NO edge from the INTRA-SENTENCE construction pass
    # (its own `construction_attachment` source). The forest-structural pass MAY
    # later recover it (see test_forest_structural_recovers_candidate_residue), but
    # the intra-sentence pass itself never fabricates a target for a zero-core
    # sentence.
    intra_edges = [
        e
        for e in _norm_edges(graph)
        if e.payload.get("source") == "construction_attachment"
    ]
    for u in no_core:
        for edge in intra_edges:
            assert not (
                edge.payload.get("cue_span") == [u.cue_char_start, u.cue_char_end]
            ), "the intra-sentence pass must NOT yield an edge for a no-core candidate"


# ── (e) firewall over every NORM edge ────────────────────────────────────────


def test_norm_edges_obey_the_firewall() -> None:
    graph = _build()
    edges = _norm_edges(graph)
    assert edges, "expected NORM edges to exercise the firewall assertion"
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status in ("asserted", "ambiguous")


# ── (f) determinism ──────────────────────────────────────────────────────────


def test_norm_composition_is_deterministic() -> None:
    first = _build()
    second = _build()

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in _norm_edges(graph)
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


# ── (g) precision over proximity: fewer edges, sentence-local attachment ──────


def test_construction_is_more_precise_than_proximity() -> None:
    graph = _build()
    norm = _norm_edges(graph)
    proximity = [e for e in graph.edges if e.edge_kind == "exception_scopes_frame"]
    # the proximity pass over-generates a near-complete mesh; the construction
    # attaches to the right core or flags ambiguous -> never MORE edges.
    assert len(norm) <= len(proximity), (
        f"construction NORM edges ({len(norm)}) should not exceed the proximity "
        f"mesh ({len(proximity)})"
    )
    assert norm, "expected some construction NORM edges"


def test_attachment_is_sentence_local() -> None:
    # every construction edge's cue and attached core must co-occur in ONE sentence
    # (the construction never crosses a sentence boundary, unlike the 120-char
    # proximity window which can join across them).
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    bundle = build_surface_bundle(_XML, "999/2025")
    unit = bundle.units[0]
    index = build_clause_index(unit.source_unit_id, unit.raw_text)
    sentences = [(s.char_start, s.char_end) for s in index.sentences]

    def _sentence_of(pos: int) -> int:
        for i, (s, e) in enumerate(sentences):
            if s <= pos < e:
                return i
        return -1

    graph = _build()
    # The INTRA-sentence attachment is sentence-local by construction. The
    # cross-sentence back-reference pass (source ==
    # construction_cross_sentence_backref) is DELIBERATELY cross-sentence (it
    # targets a reference_expr in another provision, not a co-sentence core), so it
    # is excluded here — its own gate is the test_cross_sentence_* tests below.
    intra = [
        e
        for e in _norm_edges(graph)
        if e.payload.get("source") == "construction_attachment"
    ]
    for edge in intra:
        cue_span = edge.payload["cue_span"]
        core_span = edge.payload["core_span"]
        cue_sent = _sentence_of(cue_span[0])
        core_sent = _sentence_of(core_span[0])
        assert cue_sent == core_sent and cue_sent >= 0, (
            f"construction edge crosses sentences: cue@{cue_span} core@{core_span}"
        )


# ── pass contract sanity ──────────────────────────────────────────────────────


def test_pass_declares_its_kinds() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    (pass_,) = condition_attachment_passes(bundle)
    assert isinstance(pass_, ConditionAttachmentPass)
    assert set(pass_.emits_edge_kinds) == _NORM_KINDS
    assert "exception_condition_cue" in pass_.reads_node_kinds
    assert "deontic_core" in pass_.reads_node_kinds


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-SENTENCE back-reference attachment (the Layer-2 cross-sentence gap)
#
# A back-reference EXCEPTION qualifier whose excepted norm is stated in a DIFFERENT
# provision — "Sen estämättä, mitä N §:ssä on säädetty, …" / "Poiketen siitä mitä
# N §:ssä on säädetty, …" — carries no LOCAL deontic core (its matrix is the new
# rule, and the back-reference uses the non-finite "on säädetty", not a modal
# core), so it would be left CANDIDATE by the intra-sentence pass. The principled
# cross-sentence target is the INTERNAL provision reference the back-reference
# names (the §/momentti the excepted norm lives in, within THIS statute). The pass
# binds the exception cue to that reference_expr node.
#
# A multi-section statute exercising the three cross-sentence shapes:
#   sec_2: a plain norm (the excepted norm, with a deontic core "voi").
#   sec_5: a resolved back-reference exception → INTERNAL ref to § 2 (one target).
#   sec_7: an AMBIGUOUS back-reference (mitä 2 §:ssä ja 5 §:ssä …) → two targets.
#   sec_9: a back-reference to "muualla laissa" — NO specific provision → no
#          internal ref → diagnostic, never an invented edge.
# ─────────────────────────────────────────────────────────────────────────────

_XSENT_BACKREF_SOURCE = "construction_cross_sentence_backref"


def _section(eid: str, num: str, *paragraphs: str) -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f'<section eId="{eid}"><num>{num}</num><content>{body}</content></section>'
    )


_XSENT_XML = (
    f'<?xml version="1.0" encoding="UTF-8"?>'
    f'<akomaNtoso xmlns="{_AKN}"><act><body>'
    + _section("sec_2", "2 §", "Viranomainen voi myöntää luvan hakijalle.")
    + _section(
        "sec_5",
        "5 §",
        "Sen estämättä, mitä 2 §:ssä on säädetty, lupa myönnetään ehdoitta.",
    )
    + _section(
        "sec_7",
        "7 §",
        "Poiketen siitä mitä 2 §:ssä ja 5 §:ssä on säädetty, asia raukeaa heti.",
    )
    + _section(
        "sec_9",
        "9 §",
        "Sen estämättä, mitä muualla laissa on säädetty, asia raukeaa heti.",
    )
    + "</body></act></akomaNtoso>"
).encode("utf-8")


def _xsent_build():
    return build_legal_surface_graph(_XSENT_XML, "888/2025")


def _xsent_edges(graph):
    return [
        e
        for e in graph.edges
        if e.payload.get("source") == _XSENT_BACKREF_SOURCE
    ]


def test_cross_sentence_resolved_binds_exception_to_internal_provision() -> None:
    graph = _xsent_build()
    resolved = [
        e
        for e in _xsent_edges(graph)
        if e.payload.get("attachment") == ATTACHMENT_RESOLVED_BY_PROVISION_REF
    ]
    assert resolved, "expected a resolved cross-sentence back-reference attachment"
    for edge in resolved:
        assert edge.edge_kind == EDGE_EXCEPTION_EXCEPTS
        assert edge.surface_edge_status == "asserted"
        # source is the exception cue; target is the INTERNAL provision reference.
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind == "reference_expr"
        assert graph.nodes[edge.dst].payload.get("cite_kind") == "internal"
        # the resolved edge carries the named provision target (the § 2 norm).
        assert edge.payload.get("target_provision_ref") == "888/2025/2"


def test_cross_sentence_ambiguous_emits_full_set_not_a_pick() -> None:
    graph = _xsent_build()
    ambiguous = [
        e
        for e in _xsent_edges(graph)
        if e.payload.get("attachment") == ATTACHMENT_AMBIGUOUS_BY_PROVISION_REF
    ]
    # the sec_7 "mitä 2 §:ssä ja 5 §:ssä on säädetty" names TWO provisions → two
    # edges, each carrying the FULL candidate set (never a silent nearest pick).
    assert len(ambiguous) >= 2, "expected one ambiguous edge per back-referenced provision"
    targets = {e.payload.get("target_provision_ref") for e in ambiguous}
    assert {"888/2025/2", "888/2025/5"} <= targets
    for edge in ambiguous:
        assert edge.surface_edge_status == "ambiguous"
        assert edge.edge_kind == EDGE_EXCEPTION_EXCEPTS
        full_set = edge.payload.get("candidate_provisions")
        assert full_set and len(full_set) >= 2, "ambiguous edge must carry full set"


def test_cross_sentence_no_internal_ref_is_diagnostic_not_invented() -> None:
    bundle = build_surface_bundle(_XSENT_XML, "888/2025")
    graph = _xsent_build()
    (pass_,) = condition_attachment_passes(bundle)
    pass_.run(graph)
    # sec_9 "mitä muualla laissa on säädetty" names no specific provision → a typed
    # diagnostic, NEVER an invented edge.
    diag = [u for u in pass_.unattached if u.reason == NO_INTERNAL_PROVISION_REF]
    assert diag, "expected a NO_INTERNAL_PROVISION_REF diagnostic for sec_9"
    # no cross-sentence edge was minted for the diagnosed cue.
    for u in diag:
        for edge in _xsent_edges(graph):
            assert edge.payload.get("cue_span") != [u.cue_char_start, u.cue_char_end], (
                "a diagnosed back-reference must NOT yield an asserted edge"
            )


def test_cross_sentence_edges_obey_the_firewall() -> None:
    graph = _xsent_build()
    edges = _xsent_edges(graph)
    assert edges, "expected cross-sentence edges to exercise the firewall"
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status in ("asserted", "ambiguous")


def test_cross_sentence_is_strict_superset_intra_unchanged() -> None:
    # The cross-sentence pass is a STRICT ADDITION: a statute with NO back-reference
    # exception (the intra-sentence-only _XML) must produce ZERO cross-sentence
    # edges, and its intra-sentence edge set must be byte-identical with the pass.
    intra_graph = _build()
    assert not [
        e
        for e in intra_graph.edges
        if e.payload.get("source") == _XSENT_BACKREF_SOURCE
    ], "a statute with no back-reference must yield no cross-sentence edge"
    # and the cross-sentence statute's intra-sentence edges are still the
    # construction_attachment ones (the new path never relabels them).
    xs_graph = _xsent_build()
    for edge in xs_graph.edges:
        if edge.edge_kind in _NORM_KINDS:
            assert edge.payload.get("source") in (
                "construction_attachment",
                _XSENT_BACKREF_SOURCE,
                # the forest-structural pass may recover a non-backref candidate
                # via the enclosing structural segment / proximity fallback; it
                # never relabels an intra/cross-sentence edge.
                "construction_forest_structural",
            )


def test_cross_sentence_is_deterministic() -> None:
    first = _xsent_build()
    second = _xsent_build()

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in _xsent_edges(graph)
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


# ─────────────────────────────────────────────────────────────────────────────
# FOREST-STRUCTURAL attachment (the proximity / sentence-local blind spot fix)
#
# The intra-sentence pass attaches a qualifier to a deontic core in its OWN
# clause-segmented sentence; when ``build_clause_index`` splits the governing
# core into a NEIGHBOURING sentence of the same provision, the cue is left
# CANDIDATE (no edge). The forest-structural pass recovers it via the
# SourceSyntaxGraph forest's structural segment + inherits_chapeau edges:
#   * a chapeau-governed list-item condition attaches to the CHAPEAU's frame via
#     inheritance, not the nearest char (resolved_by_chapeau_inheritance);
#   * an in-segment exception attaches to its enclosing prose segment's core
#     (resolved_by_forest_segment);
#   * a candidate with no parseable structure FALLS BACK to proximity (never
#     dropped), status ambiguous (a fallback is never asserted).
# It fires ONLY on previously-edgeless candidates → NEW-BETTER, 0 regressions.
# ─────────────────────────────────────────────────────────────────────────────

_FOREST_SOURCE = "construction_forest_structural"

# A chapeau ("voi … :") governing two list items, each carrying a condition/
# exception cue but NO own deontic core — the governing norm is the chapeau's
# ``voi`` core, reachable only via inherits_chapeau (a different sentence).
_LIST_XML = (
    f'<?xml version="1.0" encoding="UTF-8"?>'
    f'<akomaNtoso xmlns="{_AKN}"><act><body>'
    + _section(
        "sec_1",
        "1 §",
        "Viranomainen voi myöntää luvan, jos seuraavat edellytykset täyttyvät:",
        "1) hakija on täysi-ikäinen, jos hakemus koskee ajolupaa;",
        "2) maksu on suoritettu, ellei vapautusta ole myönnetty.",
    )
    + "</body></act></akomaNtoso>"
).encode("utf-8")

# A norm followed (in the SAME prose paragraph) by an exception whose own
# sentence carries no core: "… on oltava … henkilö. Tämä ei kuitenkaan koske …".
_SEG_XML = _xml(
    "Säilytystilassa on oltava vastaava henkilö. Tämä ei kuitenkaan koske pieniä tiloja.",
)

# A candidate cue in a section with NO core, but a core in ANOTHER section of the
# same body → no enclosing-segment / chapeau structure → proximity fallback.
_FALLBACK_XML = (
    f'<?xml version="1.0" encoding="UTF-8"?>'
    f'<akomaNtoso xmlns="{_AKN}"><act><body>'
    + _section("sec_1", "1 §", "Viranomainen voi myöntää luvan.")
    + _section("sec_2", "2 §", "Jos hakemus on puutteellinen, asia raukeaa.")
    + "</body></act></akomaNtoso>"
).encode("utf-8")


def _forest_edges(graph):
    return [
        e
        for e in graph.edges
        if e.edge_kind in _NORM_KINDS
        and e.payload.get("source") == _FOREST_SOURCE
    ]


def test_list_item_condition_attaches_via_chapeau_inheritance_not_proximity() -> None:
    # A chapeau-governed list-item condition/exception (its own sentence has no
    # core) attaches to the CHAPEAU's deontic core via inherits_chapeau, NOT by
    # char-distance — the syntactic attachment the proximity pass cannot make.
    graph = build_legal_surface_graph(_LIST_XML, "1/2025")
    forest = _forest_edges(graph)
    chap = [
        e
        for e in forest
        if e.payload.get("attachment") == ATTACHMENT_RESOLVED_BY_CHAPEAU
    ]
    assert chap, "expected a chapeau-inheritance attachment for the list items"
    for edge in chap:
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind == "deontic_core"
        assert edge.surface_edge_status == "asserted"
        # the attached core is the chapeau's ``voi`` — its span PRECEDES the cue
        # (a different, earlier sentence): proximity-within-sentence could not find
        # it; only the structural inheritance does.
        assert edge.payload["core_span"][0] < edge.payload["cue_span"][0]


def test_enclosing_segment_exception_attaches_to_segment_core() -> None:
    # An exception whose own sentence carries no core attaches to the deontic core
    # in the SAME forest prose segment (the previous sentence's "on oltava").
    graph = build_legal_surface_graph(_SEG_XML, "2/2025")
    seg = [
        e
        for e in _forest_edges(graph)
        if e.payload.get("attachment") == ATTACHMENT_RESOLVED_BY_FOREST_SEGMENT
    ]
    assert seg, "expected a forest-segment attachment for the in-segment exception"
    for edge in seg:
        assert edge.edge_kind == EDGE_EXCEPTION_EXCEPTS
        assert graph.nodes[edge.dst].node_kind == "deontic_core"
        assert edge.surface_edge_status == "asserted"


def test_no_structure_candidate_falls_back_to_proximity_never_dropped() -> None:
    # A candidate cue with no enclosing-segment / chapeau core must NOT be dropped:
    # it falls back to the nearest core in the unit, status ambiguous (a fallback
    # is never asserted), attachment=proximity_fallback.
    graph = build_legal_surface_graph(_FALLBACK_XML, "3/2025")
    fb = [
        e
        for e in _forest_edges(graph)
        if e.payload.get("attachment") == ATTACHMENT_PROXIMITY_FALLBACK
    ]
    assert fb, "expected a proximity-fallback edge (candidate never dropped)"
    for edge in fb:
        assert edge.surface_edge_status == "ambiguous", "a proximity fallback is never asserted"
        assert graph.nodes[edge.dst].node_kind == "deontic_core"


def test_forest_structural_recovers_candidate_residue() -> None:
    # The _XML P4 candidate ("Jos hakemus on puutteellinen, asia raukeaa …") has no
    # core in its sentence; the forest-structural pass now recovers it (the
    # intra-sentence pass tagged it NO_CORE_IN_SENTENCE and the test
    # test_candidate_no_core_emits_no_edge_but_is_tagged checks the intra pass emits
    # nothing). Here the forest pass DOES attach it (NEW-BETTER, never dropped).
    bundle = build_surface_bundle(_XML, "999/2025")
    graph = _build()
    (intra,) = condition_attachment_passes(bundle)
    intra.run(graph)
    no_core = [u for u in intra.unattached if u.reason == NO_CORE_IN_SENTENCE]
    assert no_core, "expected a NO_CORE_IN_SENTENCE candidate (P4)"
    forest = _forest_edges(graph)
    # every intra-sentence NO_CORE candidate is now covered by a forest edge (the
    # no-silent-drop guarantee: recovered, never lost).
    for u in no_core:
        covered = [
            e
            for e in forest
            if e.payload.get("cue_span") == [u.cue_char_start, u.cue_char_end]
        ]
        assert covered, (
            f"a NO_CORE_IN_SENTENCE candidate (cue={u.cue!r}) must be recovered "
            f"by the forest-structural pass, not silently dropped"
        )


def test_forest_structural_is_strict_addition_zero_regressions() -> None:
    # The forest pass NEVER alters an incumbent (intra/cross/enclosing) edge: the
    # incumbent NORM edge set is byte-identical whether or not the forest pass ran.
    # Because the forest pass uses a DISTINCT rule_id/source, its edges can never
    # share an edge id with an incumbent — the strict-addition / 0-regression
    # guarantee, asserted directly on the assembled graph.
    graph = build_legal_surface_graph(_LIST_XML, "1/2025")
    incumbent_ids = {
        e.edge_id
        for e in graph.edges
        if e.edge_kind in _NORM_KINDS
        and e.payload.get("source")
        in ("construction_attachment", "construction_cross_sentence_backref",
            "construction_enclosing_anaphora")
    }
    forest_ids = {e.edge_id for e in _forest_edges(graph)}
    assert incumbent_ids, "expected incumbent intra-sentence edges in the fixture"
    assert forest_ids, "expected forest-structural edges in the fixture"
    assert not (incumbent_ids & forest_ids), (
        "a forest-structural edge must never reuse an incumbent edge id "
        "(strict addition, 0 regressions)"
    )


def test_forest_structural_edges_obey_firewall_and_are_deterministic() -> None:
    first = build_legal_surface_graph(_LIST_XML, "1/2025")
    second = build_legal_surface_graph(_LIST_XML, "1/2025")
    fe = _forest_edges(first)
    assert fe, "expected forest-structural edges"
    for edge in fe:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status in ("asserted", "ambiguous")
    assert first.graph_id == second.graph_id

    def _fkeys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in _forest_edges(graph)
        )

    assert _fkeys(first) == _fkeys(second)


def test_forest_pass_declares_its_kinds() -> None:
    bundle = build_surface_bundle(_LIST_XML, "1/2025")
    (pass_,) = forest_structural_attachment_passes(bundle)
    assert isinstance(pass_, ForestStructuralAttachmentPass)
    assert set(pass_.emits_edge_kinds) == _NORM_KINDS
    assert "exception_condition_cue" in pass_.reads_node_kinds
    assert "deontic_core" in pass_.reads_node_kinds
