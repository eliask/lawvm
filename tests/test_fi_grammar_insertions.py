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
    TargetKind,
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
    spans = {_as_insertion(n).witness.source_span for n in vg.nodes if n.witness}
    assert len(spans) == 1, f"expected one shared batch span, got {spans}"


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
    spans = {_as_insertion(n).witness.source_span for n in vg.nodes if n.witness}
    assert len(spans) == 1, f"expected one shared batch span, got {spans}"


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


def test_gen_momentti_sub_target_after_uusi_is_zero_delta() -> None:
    # ``uusi N §:GEN M momentti`` — a §:GEN sub-target insert reached via the
    # whole-target dispatch (the genitive §:n carries a momentti/kohta sub-target).
    report = compare_surface_parsers(
        "lisätään lakiin uusi 5 §:n 2 momentti seuraavasti:",
        surface_parse.parse,
        new_parser.parse,
    )
    assert report.equal


def test_gen_plain_whole_section_is_declined() -> None:
    # ``uuden N §:n seuraavasti`` with no momentti/kohta — the plain genitive
    # whole-section stylistic variant. The old parser threads its witness span in
    # a way this context-free recogniser cannot reproduce without risking a
    # divergent per-batch span (and exposing latent deltas in preceding verb
    # groups), so it stays out of scope: the driver must DECLINE (fail-loud).
    text = "lisätään päätökseen uuden 6 a §:n seuraavasti:"
    tokens, _ = _tokenize(text)
    with pytest.raises(OutOfScope):
        new_parser.parse(tokens)


def test_section_ref_kohta_without_uusi_is_not_an_insertion() -> None:
    # "12 §:n 2 momentin 3 kohta" (no "uusi") is a plain section reference, not an
    # insertion — the new parser must reproduce the old parser's SurfaceTargetRef.
    report = compare_surface_parsers(
        "muutetaan 12 §:n 2 momentin 3 kohta", surface_parse.parse, new_parser.parse
    )
    assert report.equal


def _tokenize(text: str):
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    raw = tokenize(text)
    return apply_annotations_with_jolloin_pairs(raw)
