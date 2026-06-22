"""Differential tests for the slice-2 insertion recognizer family.

Each test asserts the NEW parser (``grammar.parser.parse``) produces a
``SurfaceClause`` byte-identical to the OLD authority (``surface_parse.parse``)
via the differential harness — objective, not self-referential. The worked
examples from the slice-2 briefing are the checklist; the corpus validation
script proves coverage at scale.

The headline example is the kohta-into-momentti insert
(``N §:n M momenttiin uusi K kohta``): the insertion recogniser must match it
FIRST so the host section is not mis-read as a section reference, including the
variants that carry a TRAILING provenance / citation span and the reinstatement
preamble the old parser threads before ``uusi``.
"""

from __future__ import annotations

import pytest

from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar import parser as new_parser
from lawvm.finland.johtolause.grammar.diff import (
    compare_surface_parsers,
    parse_text_with,
)
from lawvm.finland.johtolause.grammar.parser import OutOfScope
from lawvm.finland.johtolause.surface_model import (
    SurfaceInsertion,
    SurfaceNode,
    SurfaceRenumberTail,
    SurfaceTargetRef,
    TargetKind,
    VerbKind,
)


def _as_insertion(node: SurfaceNode) -> SurfaceInsertion:
    assert isinstance(node, SurfaceInsertion)
    return node


# Worked examples from the briefing: each must be byte-identical to the old
# parser. Every clause here is one the OLD parser emits as a SurfaceInsertion
# (or a list of them), so it is in the slice-2 insertion subset.
IN_SCOPE_EXAMPLES = [
    # Whole-section inserts (Pattern C, DOC:ILL).
    "lisätään lakiin uusi 5 a § seuraavasti:",
    "lisätään lakiin uusi 5 ja 6 § seuraavasti:",
    # Whole-chapter insert.
    "lisätään lakiin uusi 4 a luku seuraavasti:",
    # Chapter-scoped section insert (Pattern F, LUKU:ILL).
    "lisätään 3 lukuun uusi 12 § seuraavasti:",
    # Momentti sub-target insert into a section (Pattern A, §:ILL).
    "lisätään 5 §:ään uusi 3 momentti seuraavasti:",
    # NOTE: heading-plus-subsection inserts are intentionally NOT zero-delta examples:
    # the incumbent surface_parse insertion arm leaves ``sub_target.special`` empty
    # for heading facets, but the grammar emitter preserves the legacy "otsikko" bridge
    # used by downstream FI replay/payload preservation. They are covered by dedicated
    # operational-shape tests below instead of byte parity.
    # Nominative momentti sub-target insert (Pattern B3, §:GEN uusi).
    "lisätään 4 §:n uusi 2 momentti seuraavasti:",
    # The headline kohta-into-momentti insert (Pattern B2,
    # §:GEN M MOMENTTI:ILL uusi K kohta) — numeric and letter-suffixed.
    "lisätään 27 §:n 2 momenttiin uusi 5 kohta seuraavasti:",
    "lisätään 27 §:n 2 momenttiin uusi 4 a kohta seuraavasti:",
    # Genitive-momentti container kohta insert (§:ILL uusi N momentin K kohta).
    "lisätään 5 §:ään uusi 1 momentin 4 kohta seuraavasti:",
]


@pytest.mark.parametrize("text", IN_SCOPE_EXAMPLES)
def test_insertion_examples_are_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_kohta_into_momentti_emits_sub_target_insertion() -> None:
    # The headline shape: a new kohta into an existing momentti. The host
    # section must be a SurfaceInsertion carrying the (momentti, item) sub-target,
    # NOT a SurfaceTargetRef for the host section.
    model = parse_text_with(
        "lisätään 27 §:n 2 momenttiin uusi 4 a kohta seuraavasti:", new_parser.parse
    )
    (vg,) = model.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "27"
    assert node.sub_target is not None
    assert node.sub_target.momentti == 2
    assert node.sub_target.item == "4a"
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_sub_target"


def test_heading_and_momentti_insert_continuation_emits_both_sub_targets() -> None:
    model = parse_text_with(
        "lisätään 8 §:ään uusi otsikko ja uusi 4 momentti seuraavasti:",
        new_parser.parse,
    )
    (vg,) = model.verb_groups
    insertions = [_as_insertion(node) for node in vg.nodes]
    assert len(insertions) == 2
    heading, momentti = insertions
    assert heading.label == "8"
    assert heading.sub_target is not None
    assert heading.sub_target.facet is FacetKind.HEADING
    assert heading.sub_target.special == "otsikko"
    assert momentti.label == "8"
    assert momentti.sub_target is not None
    assert momentti.sub_target.momentti == 4


def test_heading_insert_without_repeated_uusi_is_lisata_only() -> None:
    model = parse_text_with(
        "lisätään 8 §:ään otsikko ja uusi 4 momentti seuraavasti:",
        new_parser.parse,
    )
    (vg,) = model.verb_groups
    insertions = [_as_insertion(node) for node in vg.nodes]
    assert len(insertions) == 2
    assert insertions[0].sub_target is not None
    assert insertions[0].sub_target.facet is FacetKind.HEADING
    assert insertions[0].sub_target.special == "otsikko"

    with pytest.raises(OutOfScope):
        parse_text_with(
            "muutetaan 8 §:ään otsikko ja uusi 4 momentti seuraavasti:",
            new_parser.parse,
        )


def test_doc_insert_then_section_heading_continuation_stays_reachable() -> None:
    model = parse_text_with(
        "lisätään lakiin uusi 8 a §, 9 §:ään otsikko ja uusi 3 momentti seuraavasti:",
        new_parser.parse,
    )
    (vg,) = model.verb_groups
    insertions = [_as_insertion(node) for node in vg.nodes]
    assert [node.label for node in insertions] == ["8a", "9", "9"]
    assert insertions[1].sub_target is not None
    assert insertions[1].sub_target.facet is FacetKind.HEADING
    assert insertions[2].sub_target is not None
    assert insertions[2].sub_target.momentti == 3


def test_conj_before_uusi_after_citation_is_owned() -> None:
    # ``N §:ään, [citation], ja uusi M momentti`` — a coordinating ``ja`` sits
    # between the §:ään target's citation provenance and ``uusi``. The old parser
    # absorbed the ``[citation] , ja`` cluster as a separator and dropped the arm
    # when it opened a clause; the new parser owns it natively so the insert is not
    # lost (this shape was previously only recovered by the retired
    # subsection/regex fallback). Single-arm and chained forms both recover.
    single = parse_text_with(
        "lisätään 18 a §:ään, sellaisena kuin se on mainituissa laeissa 744/2004 ja "
        "636/2006, ja uusi 4 momentti seuraavasti:",
        new_parser.parse,
    )
    (vg,) = single.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "18a"
    assert node.sub_target is not None
    assert node.sub_target.momentti == 4

    chained = parse_text_with(
        "lisätään 10 §:ään, sellaisena kuin se on viimeksi mainitussa laissa, uusi 3 "
        "momentti ja 18 a §:ään, sellaisena kuin se on mainituissa laeissa 744/2004 "
        "ja 636/2006, ja uusi 4 momentti seuraavasti:",
        new_parser.parse,
    )
    (vg2,) = chained.verb_groups
    labels = set()
    for n in vg2.nodes:
        insertion = _as_insertion(n)
        assert insertion.sub_target is not None
        labels.add((insertion.label, insertion.sub_target.momentti))
    assert labels == {("10", 3), ("18a", 4)}


@pytest.mark.parametrize(
    "text",
    [
        # ``uusi N § ja sen edelle uusi väliotsikko`` — a section insert trailed by
        # an anaphoric heading-placement the old parser consumes but mints no node
        # for. The new parser owns it natively (one SECTION node), byte-identical.
        "lisätään lakiin uusi 29 a § ja sen edelle uusi väliotsikko seuraavasti:",
        "lisätään asetukseen uusi 9 a § ja sen edelle uusi alaotsikko seuraavasti:",
    ],
)
def test_terminal_anaphoric_heading_co_insert_is_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, report.summary()


def test_anaphoric_heading_before_jolloin_renumber_keeps_tail() -> None:
    # 1999/1001: an anaphoric heading residue may be followed by a typed
    # JOLLOIN_MOVE consequence. The heading residue itself still mints no node,
    # but the jolloin pair must survive as a prepended renumber group and the
    # following insertion arm must remain reachable.
    text = (
        "muutetaan lain 9 b § sekä lisätään lakiin uusi 9 c § ja sen edelle uusi "
        "väliotsikko, jolloin nykyinen 9 c § siirtyy 9 d §:ksi, ja 23 §:ään uusi "
        "2 momentti seuraavasti:"
    )
    model = parse_text_with(text, new_parser.parse)

    assert [vg.verb for vg in model.verb_groups] == [
        VerbKind.SIIRTAA,
        VerbKind.MUUTTAA,
        VerbKind.LISATA,
    ]

    renumber_group = model.verb_groups[0]
    target = renumber_group.nodes[0]
    tail = renumber_group.nodes[1]
    assert isinstance(target, SurfaceTargetRef)
    assert target.label == "9c"
    assert target.witness is not None
    assert target.witness.rule_id == "fi.jolloin_renumber"
    assert isinstance(tail, SurfaceRenumberTail)
    assert tail.new_label == "9d"

    insertions = [_as_insertion(node) for node in model.verb_groups[2].nodes]
    assert [node.label for node in insertions] == ["9c", "23"]
    assert insertions[1].sub_target is not None
    assert insertions[1].sub_target.momentti == 2


def test_anaphoric_heading_between_doc_level_inserts_keeps_later_insert() -> None:
    text = (
        "lisätään asetukseen uusi 9 b § ja sen edelle uusi 2 a luvun otsikko "
        "sekä asetukseen uusi 118 b § seuraavasti:"
    )
    model = parse_text_with(text, new_parser.parse)
    (vg,) = model.verb_groups
    labels = [node.label for node in vg.nodes if isinstance(node, SurfaceInsertion)]
    assert labels == ["9b", "118b"]


def test_whole_section_insert_carries_section_witness() -> None:
    model = parse_text_with("lisätään lakiin uusi 5 a § seuraavasti:", new_parser.parse)
    (vg,) = model.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "5a"
    assert node.sub_target is None
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_section"


def test_chapter_insert_carries_chapter_witness() -> None:
    model = parse_text_with("lisätään lakiin uusi 4 a luku seuraavasti:", new_parser.parse)
    (vg,) = model.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.CHAPTER
    assert node.label == "4a"
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_chapter"


# Corpus regressions: real johtolauses whose kohta-into-momentti insert the old
# parser keeps as a single SurfaceInsertion despite a TRAILING provenance span
# (2003/768) or a reinstatement preamble (2023/315), and a §:GEN momentti insert
# behind a provenance citation that is NOT an authority lead-in (1988/771). Each
# must round-trip 0-delta — the regression these slice-2 fixes target.
CORPUS_REGRESSIONS = [
    # 2003/768 — clean kohta insert with trailing "sellaisena kuin se on" span.
    "lisätään raskaan polttoöljyn ja kevyen polttoöljyn rikkipitoisuudesta 24 "
    "päivänä elokuuta 2000 annetun valtioneuvoston asetuksen ( 766/2000 ) 1 §:n "
    "2 momenttiin uusi 4 a kohta sellaisena kuin se on osaksi valtioneuvoston "
    "asetuksessa (1263/2002) seuraavasti:",
    # 2023/315 — kohta insert behind a "siitä lailla N kumotun … tilalle"
    # reinstatement preamble.
    "lisätään valmisteverotuslain ( 182/2010 ) 1 §:n 2 momenttiin siitä lailla "
    "1265/2022 kumotun 8 kohdan tilalle uusi 8 kohta seuraavasti:",
    # 1988/771 — "5 §:n 1 momentin [CITE] uuden 2 momentin": a provenance span
    # before "uusi", NOT a nojalla authority lead-in.
    "lisännyt ammattikurssikeskusten korkotukiluotosta annetun lain "
    "täytäntöönpanosta ja soveltamisesta 17 päivänä syyskuuta 1970 annetun "
    "valtioneuvoston päätöksen 5 §:n 1 momentin voimaantulosäännökseen, "
    "sellaisena kuin se on 16 päivänä kesäkuuta 1988 annetussa valtioneuvoston "
    "päätöksessä ( 558/88 ), uuden 2 momentin seuraavasti:",
]


@pytest.mark.parametrize("text", CORPUS_REGRESSIONS)
def test_corpus_kohta_into_momentti_regressions_are_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


# Multi-arm chained insertion clauses the old parser threads across separators:
# a DOC:ILL whole-section batch folds further ``[, | sekä/ja] [DOC:ILL] [uusi]
# <nums> § (NOM)`` whole-section arms into ONE shared witness span, while a
# following §:ILL / §:GEN sub-target arm starts a fresh ``_target`` batch (its
# own span). A §:ILL sub-target arm in turn folds a trailing ``sekä uusi N §``
# whole-section continuation into its OWN batch. Each must round-trip 0-delta.
CHAINED_INSERTION_EXAMPLES = [
    # Pre-``§`` ``uusi … sekä uusi …`` chain folded into one batch.
    "lisätään lakiin uusi 5 ja 6 sekä uusi 7 ja 8 § seuraavasti:",
    "lisätään lakiin uusi 5 ja 6 § sekä uusi 35 c § seuraavasti:",
    # Post-``§`` bare NOM-section continuation folded into the same batch.
    "lisätään lakiin uusi 5 §, 6 § ja 7 § seuraavasti:",
    "lisätään lakiin uusi 5 § ja uusi 6 § seuraavasti:",
    # DOC:ILL whole-section arm + a fresh §:ILL sub-target arm (separate batches).
    "lisätään lakiin uusi 100 a §, lain 124 §:ään uusi 3 momentti seuraavasti:",
    # §:ILL sub-target arm with a trailing ``sekä uusi N §`` whole-section fold.
    "lisätään 130 §:ään uusi 2 ja 3 momentti sekä uusi 145 a § seuraavasti:",
    # A DOC:ILL whole-section arm followed by a §:ILL whole-section sekä-fold:
    "lisätään lakiin uusi 5 § sekä 6 §:ään uusi 2 momentti seuraavasti:",
    # The full multi-section / multi-momentti chain (1975/674 shape).
    "lisätään lakiin uusi 42 a ja 100 a §, lain 124 §:ään uusi 3 momentti ja "
    "130 §:ään uusi 2 ja 3 momentti sekä uusi 145 a § seuraavasti:",
]


@pytest.mark.parametrize("text", CHAINED_INSERTION_EXAMPLES)
def test_chained_insertion_arms_are_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_doc_section_chain_shares_one_batch_witness_span() -> None:
    # ``lakiin uusi 5 §, 6 § ja 7 §`` — the post-``§`` NOM-section continuation is
    # folded into the SAME batch, so all three inserts share one witness span (the
    # old ``_target`` stamps one span per batch).
    model = parse_text_with(
        "lisätään lakiin uusi 5 §, 6 § ja 7 § seuraavasti:", new_parser.parse
    )
    (vg,) = model.verb_groups
    labels = [_as_insertion(n).label for n in vg.nodes]
    assert labels == ["5", "6", "7"]
    spans = set()
    for n in vg.nodes:
        insertion = _as_insertion(n)
        assert insertion.witness is not None
        spans.add(insertion.witness.source_span)
    assert len(spans) == 1, f"expected one shared batch span, got {spans}"


def test_doc_chapter_insert_skips_heading_anchor_and_keeps_later_section_inserts() -> None:
    text = (
        "lisätään lakiin uusi 5 a luku ja luvun otsikko 44 §:n edelle "
        "sekä lakiin uusi 47 a, 49 a ja 49 b § seuraavasti:"
    )
    model = parse_text_with(text, new_parser.parse)
    (vg,) = model.verb_groups

    assert [(_as_insertion(node).kind, _as_insertion(node).label) for node in vg.nodes] == [
        (TargetKind.CHAPTER, "5a"),
        (TargetKind.SECTION, "47a"),
        (TargetKind.SECTION, "49a"),
        (TargetKind.SECTION, "49b"),
    ]


def test_section_ill_sub_target_folds_trailing_whole_section() -> None:
    # ``130 §:ään uusi 2 ja 3 momentti sekä uusi 145 a §`` — the trailing whole
    # section is folded into the §:ILL sub-target arm's batch (old Pattern A's
    # continuation reaching ``_insertion_sub_target``'s PYKALA arm).
    model = parse_text_with(
        "lisätään 130 §:ään uusi 2 ja 3 momentti sekä uusi 145 a § seuraavasti:",
        new_parser.parse,
    )
    (vg,) = model.verb_groups
    assert [_as_insertion(n).label for n in vg.nodes] == ["130", "130", "145a"]
    spans = set()
    for n in vg.nodes:
        insertion = _as_insertion(n)
        assert insertion.witness is not None
        spans.add(insertion.witness.source_span)
    assert len(spans) == 1, f"expected one shared batch span, got {spans}"


@pytest.mark.parametrize(
    "text",
    [
        # ``uusi 22 a §`` (an insertion node) folded with a plain ``88 §:n 3 ja 4
        # momentti`` arm: under ``lisätään`` the old parser keeps the second arm
        # as a plain SECTION node (a momentti add recognised by ``_section_ref``),
        # NOT a SurfaceInsertion — both stay in one LISATA group.
        "lisätään lakiin uusi 22 a § ja 88 §:n 3 ja 4 momentti seuraavasti:",
        "lisätään lakiin uusi 5 a § ja 7 §:n 2 momentti seuraavasti:",
        # The same fold appearing in the SECOND verb group of a clause that opens
        # with a ``muutetaan`` section group (the corpus's dominant shape).
        "muutetaan 6 § sekä lisätään lakiin uusi 22 a § ja 88 §:n 3 momentti "
        "seuraavasti:",
    ],
)
def test_insertion_then_plain_section_arm_folds_in_one_group(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


@pytest.mark.parametrize(
    "text",
    [
        # A continuation arm opening with an inline document/statute-name WORD
        # (``työjärjestykseen`` / ``ohjesääntöön``) that the lexer leaves
        # un-annotated mid-list (only the FIRST target of a group folds it into a
        # STATUTE_NAME_SPAN). The old parser skips the WORD run and recovers the
        # ``uusi N §`` whole-section insert; the witness anchors at ``uusi``.
        "muutetaan 33 §:n 3 momentti, 37 § sekä lisätään työjärjestykseen "
        "uusi 52 d § seuraavasti:",
        "muutetaan 5 § ja 6 § sekä lisätään ohjesääntöön uusi 14 a § seuraavasti:",
    ],
)
def test_inline_statute_name_word_before_insert_arm_is_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_scoped_section_arm_after_insert_still_declines() -> None:
    # A chapter-SCOPED continuation arm (``3 luvun 5 §:n 2 momentti``) after an
    # insertion batch is NOT folded: the new per-separator split establishes the
    # inherited chapter scope at a different point than the old whole-list pass,
    # so the driver declines rather than risk a divergent grouping.
    text = "lisätään lakiin uusi 22 a § ja 3 luvun 5 §:n 2 momentti seuraavasti:"
    tokens, _ = _tokenize(text)
    with pytest.raises(OutOfScope):
        new_parser.parse(tokens)


def test_momentin_tilalle_uusi_momentti_stays_section_ref() -> None:
    # A §:GEN momentti GENITIVE reinstatement ("N §:n M momentin tilalle uusi M
    # momentti") is NOT a Pattern B2 insertion (the old parser does not skip the
    # bare TILALLE token here): the §:GEN arm must decline so the driver keeps
    # the old parser's section-reference reading. Zero-delta is the assertion.
    text = (
        "muutetaan Suomen ulkomaanedustustojen sijainnista 31 päivänä toukokuuta "
        "1990 annetun asetuksen ( 486/90 ) 1 §:n 1 momentti, 2 § ja 3 §:n 1 "
        "momentti, lisätään 3 päivänä toukokuuta 1991 annetulla asetuksella "
        "(779/91) kumotun 1 §:n 2 momentin tilalle uusi 2 momentti, seuraavasti:"
    )
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_bare_number_after_nojalla_lead_in_is_declined() -> None:
    # "… 34 §:n 2 momentin ja 35 §:n 1 momentin nojalla, … uusi 8 b": a nojalla
    # authority lead-in followed by a bare-number insert with no structural noun.
    # The old parser's historical bare-section insert is out of scope; the driver
    # must DECLINE (raise OutOfScope) rather than mis-read the authority §:GEN
    # list as a plain section reference.
    text = (
        "lisätään 5 päivänä kesäkuuta 2002 annetun tonnistoverolain ( 476/2002 ) "
        "34 §:n 2 momentin ja 35 §:n 1 momentin nojalla, ilmoittamisvelvollisuudesta "
        "28 päivänä joulukuuta 1995 annettuun valtiovarainministeriön päätökseen "
        "(1760/1995) uusi 8 b seuraavasti:"
    )
    tokens, _ = _tokenize(text)
    with pytest.raises(OutOfScope):
        new_parser.parse(tokens)


# Archaic ``näin kuuluva`` lead-ins (and the glued ``näin. kuuluva`` variant)
# between the insertion anchor and the structural target. The old parser skips
# them at every arm; the recogniser reproduces that skip so each clause is the
# same clean insertion as its non-archaic counterpart. Each must round-trip
# 0-delta against the old parser.
NAIN_KUULUVA_EXAMPLES = [
    # Whole-section insert, DOC:ILL anchor.
    "lisätään valtiopäiväjärjestykseen uusi näin kuuluva 37 a §:",
    # Whole-section insert, chapter-scoped (LUKU:ILL anchor).
    "lisätään oikeudenkäymiskaaren 15 lukuun uusi näin kuuluva 4 a §:",
    # Momentti sub-target insert behind a §:ILL host (skip sits before uusi).
    "lisätään 27 §:ään uusi näin kuuluva 3 momentti:",
]


@pytest.mark.parametrize("text", NAIN_KUULUVA_EXAMPLES)
def test_nain_kuuluva_lead_in_is_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_nain_kuuluva_whole_section_label_and_witness() -> None:
    model = parse_text_with(
        "lisätään valtiopäiväjärjestykseen uusi näin kuuluva 37 a §:", new_parser.parse
    )
    (vg,) = model.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "37a"
    assert node.sub_target is None
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_section"


def test_nain_kuuluva_comma_variant_emits_45a_insert() -> None:
    model = parse_text_with(
        "lisätään sotilasvammalakiin (404/48) uusi, näin kuuluva 45 a §:",
        new_parser.parse,
    )
    (vg,) = model.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "45a"
    assert node.sub_target is None
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_section"


def test_gen_momentti_sub_target_after_uusi_is_zero_delta() -> None:
    # ``uusi N §:GEN M momentti`` — a §:GEN sub-target insert reached via the
    # whole-target dispatch (the genitive §:n carries a momentti/kohta sub-target).
    report = compare_surface_parsers(
        "lisätään lakiin uusi 5 §:n 2 momentti seuraavasti:",
        surface_parse.parse,
        new_parser.parse,
    )
    assert report.equal


@pytest.mark.parametrize(
    "text",
    [
        # ``uuden N §:n`` with no momentti/kohta — the plain genitive whole-section
        # stylistic variant. DOC-anchored and bare (citation-stripped) forms; the
        # old parser consumes the §:GEN and emits a plain whole-section insert.
        "lisätään päätökseen uuden 6 a §:n seuraavasti:",
        "lisätään uuden 3 a §:n seuraavasti:",
        "lisätään lakiin uuden 11 §:n seuraavasti:",
    ],
)
def test_gen_plain_whole_section_is_recovered(text: str) -> None:
    # The genitive whole-section variant is now reproduced byte-identically to the
    # old parser (it was previously declined as out of scope).
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal


@pytest.mark.parametrize(
    "text",
    [
        # ``N lukuun [reinstatement] uusi M §`` — chapter-scoped section insert
        # whose reinstatement preamble (``siitä lailla X kumotun K §:n tilalle``)
        # sits between ``lukuun`` and ``uusi``. The new arm consumes the preamble
        # and scopes the inserted section to the chapter, byte-identical to old.
        "lisätään 15 lukuun 16 §:n tilalle uusi 16 § seuraavasti:",
        "lisätään 4 lukuun 6 §:n tilalle uusi 6 § seuraavasti:",
        "lisätään 10 lukuun 4 ja 5 §:n tilalle uusi 4 ja 5 § seuraavasti:",
    ],
)
def test_luku_scoped_reinstatement_preamble_is_recovered(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal


def test_luku_scoped_citation_provenance_does_not_misscope() -> None:
    # ``DOC … N lukuun, sellaisena kuin se on laissa X, uusi M §`` — a CITATION_SPAN
    # provenance attribution between ``lukuun`` and ``uusi``. The old parser captures
    # a chapter-LESS whole-section insert here (the leading reinstatement/citation
    # preamble is skipped before the bare ``uusi 96 a §`` arm), NOT a chapter-scoped
    # node. The new parser must reproduce that exactly: a single SurfaceInsertion for
    # §96 a with ``chapter == ''`` (the inserted section is NOT mis-scoped to the
    # chapter), byte-identical to the old authority.
    text = (
        "lisätään lain (610/2014) 3 lukuun, sellaisena kuin se on laissa 679/2003, "
        "uusi 96 a § seuraavasti:"
    )
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta:\n{report.summary()}"
    model = parse_text_with(text, new_parser.parse)
    node = _as_insertion(model.verb_groups[0].nodes[0])
    assert node.label == "96a"
    assert node.chapter == ""


def test_section_ref_kohta_without_uusi_is_not_an_insertion() -> None:
    # "12 §:n 2 momentin 3 kohta" (no "uusi") is a plain section reference, not an
    # insertion — the new parser must reproduce the old parser's SurfaceTargetRef.
    report = compare_surface_parsers(
        "muutetaan 12 §:n 2 momentin 3 kohta", surface_parse.parse, new_parser.parse
    )
    assert report.equal


def test_repeated_section_ill_targets_share_inserted_subsection() -> None:
    model = parse_text_with(
        "lisätään lain 20 §:ään ja 37 §:ään uusi 2 momentti seuraavasti:",
        new_parser.parse,
    )
    insertions = [_as_insertion(node) for node in model.verb_groups[0].nodes]
    assert [node.label for node in insertions] == ["20", "37"]
    assert [node.sub_target.momentti for node in insertions if node.sub_target] == [2, 2]


# Chained-insertion continuation arms where a LATER batch carries a trailing
# reinstatement / citation / provenance span. The arm-level out-of-scope guard
# must inspect only the CURRENT insertion arm, not scan to the next verb — a
# downstream batch's own provenance must not force the clean leading arm out of
# scope. Each clause used to DECLINE (the over-broad whole-phrase scan caught the
# downstream span); now each is folded byte-identically to the old parser.
CHAINED_CONTINUATION_WITH_DOWNSTREAM_PROV_EXAMPLES = [
    # A §:ILL momentti insert, then a fresh DOC-reanchored §:ILL insert whose own
    # ``sellaisena kuin …`` provenance span closes the trailing batch.
    "lisätään lakiin uusi 5 § sekä 7 §:ään, sellaisena kuin se on laissa 12/1990, "
    "uusi 2 momentti seuraavasti:",
    # Two whole-section DOC inserts, the trailing one carrying a provenance span.
    "lisätään lakiin uusi 5 §, asetukseen uusi 8 §, sellaisena kuin se on laissa "
    "12/1990, seuraavasti:",
]


@pytest.mark.parametrize("text", CHAINED_CONTINUATION_WITH_DOWNSTREAM_PROV_EXAMPLES)
def test_chained_insertion_continuation_with_downstream_prov_is_zero_delta(
    text: str,
) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_multi_section_edelle_heading_after_insertion_is_declined() -> None:
    # After an insertion batch the old parser folds only a SINGLE-section heading
    # placement (``…, 20 §:n edelle uusi väliotsikko``) and swallows a placement
    # scoped to MULTIPLE sections (``…, 41 c ja 54 a §:n edelle uusi väliotsikko``)
    # as residue. The driver must NOT fold the multi-section form (which would
    # over-produce extra SurfaceHeadingPlacement nodes) — it declines loudly.
    text = (
        "lisätään 27 §:ään uusi 3 ja 4 momentti, 41 c ja 54 a §:n edelle uusi "
        "väliotsikko ja asetukseen uusi 72 d § seuraavasti:"
    )
    tokens, _ = _tokenize(text)
    with pytest.raises(OutOfScope):
        new_parser.parse(tokens)


def test_single_section_edelle_heading_after_insertion_is_zero_delta() -> None:
    # The single-section counterpart the old parser DOES fold after an insertion
    # batch — the driver must reproduce it byte-identically (not over-decline it).
    text = (
        "lisätään 16 §:ään uusi 11 kohta, 20 §:n edelle uusi väliotsikko "
        "seuraavasti:"
    )
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_doc_ill_reanchor_resets_inherited_chapter_scope() -> None:
    # ``3 lukuun uusi 21 a §`` scopes chapter 3; a following ``lakiin uusi 36 a §``
    # DOC re-anchors at the statute root and RESETS the chapter, so the trailing
    # ``56 §:ään uusi 3 momentti`` must NOT inherit chapter 3 (it stays statute-
    # level). The driver mirrors the old parser's per-batch DOC:ILL chapter reset.
    text = (
        "lisätään 3 lukuun uusi 21 a §, lakiin uusi 36 a §, "
        "56 §:ään uusi 3 momentti seuraavasti:"
    )
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


@pytest.mark.parametrize(
    "text",
    [
        # ``DOC:ILL [reinstatement] uusi N §`` — the whole ``siitä lailla X kumotun
        # K §:n tilalle`` reinstatement clause collapses to a single REINST_SPAN
        # between the ``lakiin`` / ``asetukseen`` anchor and ``uusi``. The DOC:ILL
        # arm consumes that preamble and scopes the inserted section to the statute
        # (chapter=''), byte-identical to the old Pattern C.
        "lisätään lakiin siitä lailla 694/1985 kumotun 32 §:n tilalle uusi 32 § "
        "seuraavasti:",
        "lisätään asetukseen siitä lailla 123/1990 kumotun 5 §:n tilalle uusi 5 § "
        "seuraavasti:",
        # With a trailing ``sekä uusi …`` whole-section continuation (the chained
        # arms share one batch span; only the leading slot is reinstated).
        "lisätään lakiin siitä lailla 739/1966 kumotun 21 §:n tilalle uusi 21 § "
        "sekä uusi 26 a ja 28 a § seuraavasti:",
    ],
)
def test_doc_ill_reinstatement_preamble_is_recovered(text: str) -> None:
    # Previously declined ("out-of-scope insertion shape (uusi anchor present)"):
    # the broad provenance guard caught the leading REINST_SPAN before the DOC:ILL
    # arm. Now the arm is dispatched ahead of the guard and consumes the preamble.
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_doc_ill_reinstatement_insert_is_statute_scoped_section() -> None:
    # The reinstated slot's consumed ``K §:n tilalle`` preamble does NOT propagate
    # onto the inserted node: it is a plain whole-section insert at statute scope
    # (chapter=''), not a chapter_ref. (The old miscompile this also fixes.)
    model = parse_text_with(
        "lisätään lakiin siitä lailla 694/1985 kumotun 32 §:n tilalle uusi 32 § "
        "seuraavasti:",
        new_parser.parse,
    )
    (vg,) = model.verb_groups
    node = _as_insertion(vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "32"
    assert node.chapter == ""
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_section"


def test_trailing_whole_part_carries_label_as_scope() -> None:
    # ``III ja V osa`` ends a batch with whole-PART target refs; the last part's
    # LABEL (``V``) is the carried part scope onto the following bare section list
    # (``86 § ja 97 §``), exactly as a whole-chapter target's label carries the
    # chapter scope. The new ``extract_part`` was missing this branch.
    text = "kumotaan III ja V osa sekä 86 § ja 97 §"
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"
    model = parse_text_with(text, new_parser.parse)
    (vg,) = model.verb_groups
    by_label = {n.label: n for n in vg.nodes if isinstance(n, SurfaceTargetRef)}
    assert by_label["86"].part == "V"
    assert by_label["97"].part == "V"


def test_naista_second_arm_with_glued_provenance_word_is_declined() -> None:
    # ``näistä N § [CITE], M § sellaisenakuin se on … laissa`` — the first arm is
    # closed by a collapsed provenance span (the old anaphor-skip consumes it), but
    # the second arm's ``§`` is closed by an UNCOLLAPSED glued ``sellaisenakuin``
    # provenance word run. The old target loop re-parses that second ``§`` as a
    # fresh duplicate node before stopping at the word run, so the new section path
    # would silently drop it — the driver must DECLINE instead.
    text = (
        "muutetaan 15 a, 15 b ja 16 §, näistä 15 a § sellaisena kuin se on laissa "
        "303/1961, 15 b § sellaisenakuin se on 9 päivänä kesäkuuta 1961 annetussa "
        "laissa"
    )
    tokens, _ = _tokenize(text)
    with pytest.raises(OutOfScope):
        new_parser.parse(tokens)


def test_naista_single_closed_arm_is_not_over_declined() -> None:
    # Control for the leak detector: a single ``näistä N § sellaisena kuin …``
    # arm closed by a collapsed provenance span is amendment-history the old
    # section path DROPS (byte-identical to the new), so it must NOT be declined.
    text = "muutetaan 15 a, 15 b ja 16 §, näistä 15 a § sellaisena kuin se on laissa 303/1961"
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


# Appendix (liite) inserts: the old parser's ``DOC:ILL uusi liite [numlist]`` arm
# emits a whole-appendix SurfaceInsertion (APPENDIX target, ``fi.insertion_other``
# witness). Each form must round-trip 0-delta. An unlabelled ``uusi liite`` is the
# whole appendix (label ""); a trailing number list gives one node per appendix.
LIITE_INSERT_EXAMPLES = [
    "muutetaan lain 10 § ja lisätään lakiin uusi liite seuraavasti:",
    "muutetaan asetuksen 6 § ja lisätään asetukseen uusi liite 9 seuraavasti:",
    "muutetaan asetuksen liitteet 3 ja 6, sekä lisätään asetukseen uusi liite 9 "
    "seuraavasti:",
]


@pytest.mark.parametrize("text", LIITE_INSERT_EXAMPLES)
def test_appendix_insert_is_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_unlabelled_appendix_insert_carries_other_witness() -> None:
    # ``lakiin uusi liite`` (no number) — the whole appendix, label "".
    model = parse_text_with(
        "muutetaan lain 10 § ja lisätään lakiin uusi liite seuraavasti:",
        new_parser.parse,
    )
    lisata = model.verb_groups[-1]
    node = _as_insertion(lisata.nodes[0])
    assert node.kind == TargetKind.APPENDIX
    assert node.label == ""
    assert node.sub_target is None
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_other"


def test_numbered_appendix_insert_carries_label() -> None:
    # ``asetukseen uusi liite 9`` — a numbered appendix insert.
    model = parse_text_with(
        "muutetaan asetuksen 6 § ja lisätään asetukseen uusi liite 9 seuraavasti:",
        new_parser.parse,
    )
    lisata = model.verb_groups[-1]
    node = _as_insertion(lisata.nodes[0])
    assert node.kind == TargetKind.APPENDIX
    assert node.label == "9"
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_other"


# Real corpus johtolauses whose DOC:ILL appendix insert the old parser keeps as a
# whole-appendix SurfaceInsertion. Each must round-trip 0-delta.
LIITE_CORPUS_REGRESSIONS = [
    # 2007/1311 — ``lisätään lakiin uusi liite`` (unlabelled whole appendix).
    "muutetaan 30 päivänä joulukuuta 2003 annetun ajoneuvoverolain ( 1281/2003 ) "
    "10 § ja lisätään lakiin uusi liite seuraavasti:",
    # 2011/278 — appendix modify list + numbered ``uusi liite 9`` insert.
    "muutetaan ulkomaanedustuksen korvauksista annetun asetuksen ( 1048/2010 ) "
    "liitteet 3 ja 6, sekä lisätään asetukseen uusi liite 9 seuraavasti:",
    # 2016/767 — ``lisätään asetukseen uusi liite 3``.
    "muutetaan (1015/2013) 1 §, sekä lisätään asetukseen uusi liite 3 seuraavasti:",
]


@pytest.mark.parametrize("text", LIITE_CORPUS_REGRESSIONS)
def test_appendix_insert_corpus_regressions_are_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_appendix_reinstatement_insert_still_declines() -> None:
    # ``kumotun C liitteen tilalle uusi C liite`` — a reinstatement appendix
    # insert needs the old parser's residue handling; it stays out of scope so the
    # driver declines rather than mis-compile it as a clean appendix insert.
    text = (
        "muutetaan asetuksen 1 §, sekä korvataan asetuksella kumotun C liitteen "
        "tilalle uusi C liite seuraavasti:"
    )
    with pytest.raises(OutOfScope):
        parse_text_with(text, new_parser.parse)


def _tokenize(text: str):
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    raw = tokenize(text)
    return apply_annotations_with_jolloin_pairs(raw)


# Cross-verb-group anaphoric inserts (#29 sub-problem 1): a LISATA group whose
# host section is established in a PRECEDING verb group. The determiner that names
# it (``sanottuun/mainittuun pykälään``) is lexed into a sentinel span, so the
# group opens on a bare ``uusi N momentti``; the host section is the cross-group
# anchor (``ctx.last_section``), not a node in this group. The old parser resolves
# it via its ``_verb_group`` anaphoric fallback (``fi.cross_verb_bare_uusi`` /
# ``fi.cross_verb_momentti``). Each must now round-trip byte-identical.
CROSS_VERB_ANAPHORIC = [
    # The briefing exemplar.
    "muutetaan lain 5 §:n 1 momentti sekä lisätään sanottuun pykälään uusi 3 "
    "momentti seuraavasti:",
    # 1948/954 — "viimeksi mainittuun pykälään" anchors §67 (the SECOND muutetaan
    # target), the WORD "viimeksi" preceding the determiner.
    "Eduskunnan päätöksen mukaisesti muutetaan elokuun 20 päivänä 1948 annetun "
    "tapaturmavakuutuslain (608/48) 11 § sekä 67 §:n 2 momentti sekä lisätään "
    "viimeksi mainittuun pykälään uusi 3 momentti seuraavasti:",
    # 1965/590 — "mainittuun pykälään" anchors §7 (a descendant-coordination base).
    "Sosiaaliministeriön toimialaan kuuluvia asioita käsittelemään määrätyn "
    "ministerin esittelystä muutetaan 17 päivänä heinäkuuta 1959 annetun "
    "liikennevakuutusasetuksen (324/ 59) 7 §:n 2 ja 3 momentti sekä lisätään "
    "mainittuun pykälään uusi 5 momentti seuraavasti:",
    # 1967/591 — "sanottuun pykälään" anchors §59 across a trailing provenance span.
    "Eduskunnan päätöksen mukaisesti muutetaan 4 päivänä heinäkuuta 1963 annetun "
    "sairausvakuutuslain 59 §:n 1 momentti, sellaisena kuin se on 16 päivänä "
    "joulukuuta 1966 annetussa laissa (646/66), sekä lisätään sanottuun pykälään "
    "uusi 3 momentti seuraavasti:",
]


@pytest.mark.parametrize("text", CROSS_VERB_ANAPHORIC)
def test_cross_verb_anaphoric_insert_is_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_cross_verb_anaphoric_insert_resolves_prior_section() -> None:
    # The recovered LISATA group inserts §5's new momentti 3 with the cross-verb
    # witness — the host section comes from the *prior* muutetaan group, not this
    # group's (empty) own targets.
    model = parse_text_with(
        "muutetaan lain 5 §:n 1 momentti sekä lisätään sanottuun pykälään uusi 3 "
        "momentti seuraavasti:",
        new_parser.parse,
    )
    lisata_vg = model.verb_groups[1]
    node = _as_insertion(lisata_vg.nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "5"
    assert node.sub_target is not None
    assert node.sub_target.momentti == 3
    assert node.witness is not None
    assert node.witness.rule_id == "fi.cross_verb_bare_uusi"


def test_cross_verb_whole_section_insert_is_not_claimed_as_anaphora() -> None:
    # Control: ``… sekä lisätään … uusi 50 a §`` is a WHOLE-section insert, NOT a
    # cross-group sub-target anaphora. The cross-verb fallback must NOT claim it
    # (which would stamp the wrong ``fi.cross_verb_*`` witness in place of the old
    # parser's ``fi.insertion_section``); the clause stays declined (2017/290).
    text = (
        "muutetaan maa- ja metsätalousministeriön työjärjestyksestä annetun maa- "
        "ja metsätalousministeriön asetuksen (658/2016) 15 ja 52 §, sekä lisätään "
        "työjärjestykseen uusi 50 a § ja sen edelle uusi väliotsikko seuraavasti:"
    )
    with pytest.raises(OutOfScope):
        parse_text_with(text, new_parser.parse)


def test_cross_verb_anaphora_without_prior_section_declines() -> None:
    # Control: the same bare ``uusi N momentti`` arm with NO resolvable prior
    # section (the first and only verb group is the LISATA group) has no cross-group
    # anchor, so the fallback does not fire and the clause declines.
    text = "lisätään sanottuun pykälään uusi 3 momentti seuraavasti:"
    with pytest.raises(OutOfScope):
        parse_text_with(text, new_parser.parse)


# Citation-stripped bare-``uusi`` whole-target inserts (old Pattern D). These are
# the single_verb_bare_number_insert subset the grammar now owns natively: a LISATA
# group whose document / authority lead-in is stripped to a sentinel span, opening
# directly on ``uusi <numlist> [§]``. Each must be byte-identical to the old parser.
BARE_UUSI_WHOLE_TARGET_EXAMPLES = [
    # Verbatim corpus johtolauses (the real shapes the recovery owns), each proven
    # byte-identical to the old parser by the differential harness.
    # END-terminated bare section, NO structural ``§`` (1970/675 ``uusi 19 b``).
    "lisätään 30 päivänä huhtikuuta 1964 annettuun kunnallisten viranhaltijain ja "
    "työntekijäin eläkelain (202/64) uusi 19 b seuraavasti:",
    # Chained ``uusi … ja uusi … §`` whole-section inserts (2009/595).
    "lisätään 10 päivänä huhtikuuta 1987 annettuun lääkelakiin (395/1987) uusi 76 a "
    "ja uusi 84 b § seuraavasti:",
    # Trailing reinstatement span AFTER the closing ``§`` (the old parser leaves it
    # for the outer loop, so the arm stops at the target) — 2004/729.
    "lisätään 12 päivänä heinäkuuta 1940 annettuun perintö- ja lahjaverolakiin "
    "(378/1940) uusi 21 a § siitä lailla 540/1996 kumotun 21 a §:n tilalle "
    "seuraavasti:",
    # Bare pure-number section (no letter, no ``§``) after a ``kumotun … tilalle``
    # reinstatement lead-in — 2024/132 ``uusi 127``.
    "lisätään ajoneuvolain (82/2021) siitä lailla 493/2023 kumotun 127 §:n tilalle "
    "uusi 127 seuraavasti:",
]


@pytest.mark.parametrize("text", BARE_UUSI_WHOLE_TARGET_EXAMPLES)
def test_bare_uusi_whole_target_examples_are_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_bare_uusi_end_terminated_section_emits_section_insertion() -> None:
    # ``uusi 19 b`` with no ``§`` before END is a whole-section insert labelled
    # ``19b``, witnessed ``fi.insertion_section`` (not a section reference).
    model = parse_text_with(
        "lisätään 30 päivänä huhtikuuta 1964 annettuun kunnallisten viranhaltijain "
        "ja työntekijäin eläkelain (202/64) uusi 19 b seuraavasti:",
        new_parser.parse,
    )
    node = _as_insertion(model.verb_groups[0].nodes[0])
    assert node.kind == TargetKind.SECTION
    assert node.label == "19b"
    assert node.sub_target is None
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_section"


def test_bare_uusi_recovery_declines_on_downstream_heading_fold() -> None:
    # Self-guard control: a leading ``uusi N §`` followed by a ``… edelle uusi
    # väliotsikko`` heading-placement fold in the SAME batch must NOT be owned as a
    # bare-section insert (that would silently DROP the heading arm the old parser
    # folds into the batch). The clause stays declined (2017/290 shape).
    text = (
        "muutetaan asetuksen (658/2016) 15 ja 52 §, sekä lisätään "
        "työjärjestykseen uusi 50 a § ja sen edelle uusi väliotsikko seuraavasti:"
    )
    with pytest.raises(OutOfScope):
        parse_text_with(text, new_parser.parse)


# Scope-anchor-before-``uusi`` insertion collapse (task #41 parity bugs): a
# CHAPTER or CITATION authority/reinstatement preamble between the scope anchor
# and ``uusi`` made the new parser collapse the whole insert group to a bare
# chapter/section ref, DROPPING the inserted entity the old parser captures. The
# three shapes (chapter-scoped reinstatement ``N luvun [REINST] uusi M §``,
# bare ``lukuun uusi …`` continuation, and ``§:n nojalla uusi …`` citation-stamped
# authority lead-in) all reduce to that one root cause.
SCOPE_ANCHOR_BEFORE_UUSI = [
    # 1991/1055 — single ``4 luvun [REINST] uusi 22 §`` reinstatement insert. The
    # chapter pre-parse + leading REINST_SPAN must not collapse to a bare CHAPTER 4.
    "kumotaan 3 päivänä joulukuuta 1895 annetun ulosottolain 2 luku siihen "
    "myöhemmin tehtyine muutoksineen, muutetaan 3 luvun 23 § sekä 4 luvun 23 §:n 1 "
    "momentti, 24 §:n 2 momentti, 28 §:n 4 momentti ja 30 §:n 2 momentti, lisätään "
    "mainitulla 14 päivänä joulukuuta 1984 annetulla lailla kumotun 4 luvun 22 §:n "
    "tilalle uusi 22 § seuraavasti:",
    # 1995/551 — 12-insertion batch: ``4 luvun [REINST] uusi 27 §`` then §:ILL
    # sub-targets, a bare ``lukuun uusi 31 a … §`` inheriting chapter 5, and trailing
    # §:ILL momentti inserts. The whole batch must reproduce, not collapse to CHAPTER 4.
    "kumotaan 3 päivänä joulukuuta 1895 annetun ulosottolain 5 luvun 54 § ja 7 "
    "luvun 8 §:n 4 momentti, muutetaan 4 luvun 26 §, 30 §:n 1 ja 2 momentti ja 32 §, "
    "5 luvun 7 §:n 2 momentti, 21 ja 26 §, 27 §:n 2 momentti, 29, 31, 36, 37, 42, "
    "43, 50 ja 51 §, 6 luvun 12 ja 13 § sekä 7 luvun 3 §:n 2 momentti, lisätään "
    "laista mainitulla 14 päivänä joulukuuta 1984 annetulla lailla kumotun 4 luvun "
    "27 §:n tilalle uusi 27 § sekä 5 luvun 8 §:ään, sellaisena kuin se on mainitussa "
    "18 päivänä toukokuuta 1973 annetussa laissa, uusi 2 momentti, 17 §:ään, "
    "sellaisena kuin se on mainitussa 14 päivänä joulukuuta 1984 annetussa laissa, "
    "uusi 2 momentti, lukuun uusi 31 a, 32 a, 36 a ja 37 a―37 d §, 41 §:ään uusi 3 "
    "momentti ja 48 §:ään, sellaisena kuin se on 27 päivänä lokakuuta 1933 annetussa "
    "laissa ( 267/33 ), uusi 3 momentti seuraavasti:",
    # 1989/117 — ``[CITE] 4§:n ja [CITE] 4§:n nojalla uusi 3 a§`` citation-stamped
    # authority lead-in: the two ``4 §`` authority sections are the basis, not targets;
    # only ``3 a §`` is inserted. The leading citation must not be mis-read as a target.
    "muutetaan eräistä valtion omistamille alueille perustetuista "
    "kansallispuistoista ja luonnonpuistoista 18 päivänä joulukuuta 1981 annetun "
    "asetuksen ( 932/81 ) 4§:n 2 momentti sekä lisätään asetukseen Oulangan "
    "kansallispuiston laajentamisesta 3 päivänä helmikuuta 1989 annetun lain "
    "(115/89) 4§:n ja Seitsemisen kansallispuiston laajentamisesta 3 päivänä "
    "helmikuuta 1989 annetun lain (116/89) 4§:n nojalla uusi 3 a§ seuraavasti:",
]


@pytest.mark.parametrize("text", SCOPE_ANCHOR_BEFORE_UUSI)
def test_scope_anchor_before_uusi_is_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_chapter_scoped_reinstatement_insert_captures_section() -> None:
    # 1991/1055: the LISATA group is a SurfaceInsertion for §22 (chapter-less, as the
    # old Pattern D whole-section insert leaves it), NOT a bare CHAPTER 4 ref.
    model = parse_text_with(SCOPE_ANCHOR_BEFORE_UUSI[0], new_parser.parse)
    lisata = model.verb_groups[2]
    assert len(lisata.nodes) == 1
    node = _as_insertion(lisata.nodes[0])
    assert node.label == "22"
    assert node.kind == TargetKind.SECTION
    assert node.chapter == ""


def test_multi_arm_chapter_reinstatement_batch_keeps_all_insertions() -> None:
    # 1995/551: all 12 insertions are captured, including the bare ``lukuun uusi …``
    # arm that inherits chapter 5 from the preceding ``5 luvun 8 §:ään`` arm.
    model = parse_text_with(SCOPE_ANCHOR_BEFORE_UUSI[1], new_parser.parse)
    lisata = model.verb_groups[2]
    labels = [_as_insertion(n).label for n in lisata.nodes]
    assert labels == [
        "27", "8", "17", "31a", "32a", "36a", "37a", "37b", "37c", "37d", "41", "48"
    ]
    # The bare ``lukuun uusi 31 a … §`` arm inherits chapter 5.
    by_label = {_as_insertion(n).label: _as_insertion(n) for n in lisata.nodes}
    assert by_label["31a"].chapter == "5"
    assert by_label["27"].chapter == ""  # the ``4 luvun … uusi 27 §`` whole-section insert is chapter-less


def test_nojalla_authority_lead_in_inserts_only_the_new_section() -> None:
    # 1989/117: only ``3 a §`` is inserted; the two ``4 §`` authority sections behind
    # the ``nojalla`` citation lead-in are NOT operative targets.
    model = parse_text_with(SCOPE_ANCHOR_BEFORE_UUSI[2], new_parser.parse)
    lisata = model.verb_groups[1]
    assert len(lisata.nodes) == 1
    node = _as_insertion(lisata.nodes[0])
    assert node.label == "3a"
    assert node.kind == TargetKind.SECTION


def test_bare_nojalla_abutting_uusi_recovers_real_insert() -> None:
    # 1987/1046 shape: a ``14 §:n 2 momentin nojalla uusi 4 a §`` lead-in. The OLD
    # parser mis-read the enabling-statute section ``14 §`` as the insert target
    # (dropping the real ``uusi 4 a §`` insertion — a genuine silent-drop the
    # totality predicate flagged). The new parser now SKIPS the leading-``nojalla``
    # authority basis to the ``uusi`` anchor and recovers the real insertion of
    # §4 a; the mis-read ``14 §`` authority is correctly discarded. NEW is better
    # than legacy here (expected divergence — the recovered insert clears the drop).
    text = (
        "muutetaan sosiaalipalveluista perittävistä maksuista 2 päivänä joulukuuta "
        "1983 annetun asetuksen ( 887/83 ) 2 §:n 1 momentti, 4§:n 2 momentti sekä 5§, "
        "lisätään asetukseen vammaisuuden perusteella järjestettävistä palveluista ja "
        "tukitoimista 3 päivänä huhtikuuta 1987 annetun lain (380/87) 14§:n 2 "
        "momentin nojalla uusi 4 a§, seuraavasti:"
    )
    model = parse_text_with(text, new_parser.parse)
    assert len(model.verb_groups) == 2
    lisata_vg = model.verb_groups[1]
    insertions = [n for n in lisata_vg.nodes if isinstance(n, SurfaceInsertion)]
    assert [n.label for n in insertions] == ["4a"]
    # The mis-read authority section ``14`` must NOT appear as a target.
    labels = [getattr(n, "label", None) for vg in model.verb_groups for n in vg.nodes]
    assert "14" not in labels
