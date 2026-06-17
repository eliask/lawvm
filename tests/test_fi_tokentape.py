"""Phase 7 TokenTape substrate: span exactness + ExceptionConditionLens parity.

Two gates (Pro r5 §D4):

  (a) Tokenizer span exactness + totality on synthetic + ≥3 real statutes:
      ``raw_text[t.char_start:t.char_end] == t.text`` for every token, and the
      tape contiguously covers ``raw_text`` (totality).

  (b) The token-consuming ExceptionConditionLens produces node seeds IDENTICAL
      (cue_kind / marker_text / scope_hint payload, source_ref spans, and the
      local_discriminator set) to what the PRE-migration regex recognizer
      (``recognize_exception_condition_cues``, UNCHANGED, the oracle) would
      produce — on synthetic fixtures AND ≥5 real statutes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
)
from lawvm.core.legal_surface_tokens import TOKEN_CATEGORIES, Token, TokenTape
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.exception_condition import (
    ExceptionConditionLens,
)
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.references.exception_condition import (
    recognize_exception_condition_cues,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures (mirror the recognizer test clauses + token edge cases)
# ---------------------------------------------------------------------------

_SYNTHETIC_TEXTS: tuple[str, ...] = (
    "Säännöstä ei kuitenkaan sovelleta alle 15-vuotiaisiin.",
    "Poiketen siitä mitä 5 §:ssä säädetään, lupa voidaan myöntää.",
    "Laki koskee kaikkia, lukuun ottamatta valtion virastoja.",
    "Sen estämättä mitä edellä säädetään, asia ratkaistaan heti.",
    "Lupa myönnetään, jollei estettä ole. Kielto on voimassa, ellei toisin määrätä.",
    "Kaikki asiakirjat ovat julkisia, paitsi salassa pidettävät.",
    "Lupa peruutetaan, jos edellytykset eivät enää täyty.",
    "Maksu peritään, kun päätös on annettu.",
    "Mikäli hakemus on puutteellinen, hakijaa kehotetaan täydentämään sitä.",
    "Säännöksiä sovelletaan siltä osin kuin muualla ei toisin säädetä.",
    "Tuki myönnetään edellyttäen että ehdot täyttyvät. "
    "Lupa annetaan sillä edellytyksellä että maksu suoritetaan.",
    "Lupa peruutetaan, jos edellytykset puuttuvat; muutoin se pysyy voimassa.",
    "Sovelletaan, paitsi.",
    "Asiassa, jossa on useita osapuolia, sovelletaan erityissäännöksiä.",
    "Kunta vastaa palveluista kunnes toisin päätetään.",
    "Tämä tarkoittaa sitä jos asiaa tarkastellaan tarkemmin.",
    "Päätös tehdään. Kun määräaika on kulunut, asia raukeaa.",
    "Lupa annetaan, jos ehto täyttyy, mutta ei kuitenkaan poikkeustapauksissa.",
    "Tämä on tavallinen virke ilman ehtoja tai poikkeuksia.",
    # token edge cases for the word-boundary guard (digit adjacency):
    "Sovelletaan 5 §:n nojalla, jos2 ei lasketa ehdoksi.",
    "ei\nkuitenkaan rivinvaihdolla erotettuna.",
    "ei   kuitenkaan   monella   välilyönnillä.",
)


# ---------------------------------------------------------------------------
# (a) span exactness + totality
# ---------------------------------------------------------------------------


def _assert_tape_exact_and_total(raw_text: str, tape: TokenTape) -> None:
    assert tape.text_hash  # hash populated
    rebuilt: list[str] = []
    prev_end = 0
    for tok in tape.tokens:
        # span exactness
        assert raw_text[tok.char_start : tok.char_end] == tok.text
        assert tok.normalized == tok.text.casefold()
        assert tok.category in TOKEN_CATEGORIES
        # contiguity / totality
        assert tok.char_start == prev_end, (
            f"gap/overlap before token {tok!r}: prev_end={prev_end}"
        )
        prev_end = tok.char_end
        rebuilt.append(tok.text)
    assert prev_end == len(raw_text), "tape does not cover the whole text"
    assert "".join(rebuilt) == raw_text  # lossless round-trip


def test_tokenizer_span_exactness_synthetic() -> None:
    for text in _SYNTHETIC_TEXTS:
        tape = build_token_tape("synthetic#body", text)
        _assert_tape_exact_and_total(text, tape)


def test_tokenizer_categories_section_and_colon_suffix() -> None:
    tape = build_token_tape("u", "5 §:ssä ja 6 § sekä §-merkki.")
    cats = {t.text: t.category for t in tape.tokens}
    assert cats["§:ssä"] == "colon_suffix"
    assert cats["§"] == "section_mark"
    # number tokens
    assert any(t.category == "number" and t.text == "5" for t in tape.tokens)


def test_tokenizer_dotted_number_and_dash() -> None:
    tape = build_token_tape("u", "Voimaan 1.1.2020 – ks. 12.5 prosenttia.")
    texts = {(t.text, t.category) for t in tape.tokens}
    assert ("1.1.2020", "number") in texts
    assert ("12.5", "number") in texts
    assert ("–", "dash") in texts
    # sentence period after "ks" is punct, not swallowed into a number
    assert ("ks", "word") in texts


def test_tokenizer_empty_text() -> None:
    tape = build_token_tape("u", "")
    assert tape.tokens == ()


def test_token_rejects_bad_span() -> None:
    with pytest.raises(ValueError):
        Token(text="ab", char_start=0, char_end=1, normalized="ab", category="word")
    with pytest.raises(ValueError):
        Token(text="A", char_start=0, char_end=1, normalized="A", category="word")
    with pytest.raises(ValueError):
        Token(text="a", char_start=0, char_end=1, normalized="a", category="nope")


def test_tape_rejects_overlap() -> None:
    t1 = Token(text="ab", char_start=0, char_end=2, normalized="ab", category="word")
    t2 = Token(text="cd", char_start=1, char_end=3, normalized="cd", category="word")
    with pytest.raises(ValueError):
        TokenTape(source_unit_id="u", text_hash="h", tokens=(t1, t2))


# ---------------------------------------------------------------------------
# (b) ExceptionConditionLens token/regex parity
# ---------------------------------------------------------------------------


def _oracle_seeds(raw_text: str) -> list[tuple]:
    """Expected node-seed shapes from the UNCHANGED regex recognizer.

    Returns a list of comparable tuples mirroring exactly what the pre-migration
    lens emitted: (cue_kind, marker_text, scope_payload, char_start, char_end).
    """
    cues = recognize_exception_condition_cues(raw_text)
    out: list[tuple] = []
    for cue in cues:
        s = cue.source_span
        start = s.byte_offset
        end = start + s.byte_len
        sh = cue.scope_hint
        scope = (
            [sh.byte_offset, sh.byte_offset + sh.byte_len] if sh is not None else None
        )
        out.append((cue.cue_kind, cue.marker_text, scope, start, end))
    return out


def _lens_seeds(raw_text: str) -> list[tuple]:
    unit = SourceSurfaceUnit(
        source_unit_id="u#body",
        work_id="u",
        address=None,
        raw_text=raw_text,
        source_hash="h",
        source_ref=_dummy_ref(raw_text),
        token_tape=build_token_tape("u#body", raw_text),
    )
    bundle = _bundle(unit)
    res = ExceptionConditionLens().analyze(
        bundle, context=SurfaceAnalysisContext()
    )
    out: list[tuple] = []
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        out.append(
            (
                seed.payload["cue_kind"],
                seed.payload["marker_text"],
                seed.payload["scope_hint"],
                ref.char_start,
                ref.char_end,
            )
        )
    return out


def _dummy_ref(raw_text: str):
    from lawvm.core.legal_surface_graph import SourceSpanRef

    return SourceSpanRef(
        source_unit_id="u#body",
        source_hash="h",
        work_id="u",
        address=None,
        char_start=0,
        char_end=len(raw_text),
        text_hash="t",
    )


def _bundle(unit: SourceSurfaceUnit) -> SourceSurfaceBundle:
    from lawvm.core.legal_surface_graph import SurfaceGraphSubject

    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="u",
        scope={"kind": "whole_work"},
        surface_time=None,
        source_bundle_hash="h",
        language="fi",
    )
    return SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))


def test_lens_parity_synthetic() -> None:
    for text in _SYNTHETIC_TEXTS:
        oracle = _oracle_seeds(text)
        lens = _lens_seeds(text)
        assert lens == oracle, f"divergence on {text!r}:\n oracle={oracle}\n lens={lens}"


def test_lens_local_discriminator_set_matches_payload() -> None:
    # The discriminator embeds kind|marker|start; assert it stays consistent
    # with the payload (the assembler keys on it; it must remain stable).
    text = "Lupa annetaan, jos ehto täyttyy, mutta ei kuitenkaan poikkeustapauksissa."
    unit = SourceSurfaceUnit(
        source_unit_id="u#body",
        work_id="u",
        address=None,
        raw_text=text,
        source_hash="h",
        source_ref=_dummy_ref(text),
        token_tape=build_token_tape("u#body", text),
    )
    res = ExceptionConditionLens().analyze(_bundle(unit), context=SurfaceAnalysisContext())
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        prefix = f"{seed.payload['cue_kind']}|{seed.payload['marker_text']}|{ref.char_start}|"
        assert seed.local_discriminator.startswith(prefix)


# ---------------------------------------------------------------------------
# Real-statute gates (archive-gated)
# ---------------------------------------------------------------------------


def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "finlex.farchive").exists()


_REAL_SIDS: tuple[str, ...] = (
    "2002/723",  # hallintolaki
    "1999/731",  # perustuslaki
    "2003/434",  # hallintolaki (HL)
    "1734/3",    # old
    "2009/916",
    "1889/39",   # rikoslaki
    "2011/379",  # pelastuslaki
)


def _real_xml(sid: str) -> bytes | None:
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore

    root = os.environ["LAWVM_CANONICAL_DATA_ROOT"]
    store = TransparentCorpusStore(
        Farchive(str(Path(root) / "data" / "finlex.farchive"), readonly=True),
        cache_only=True,
    )
    try:
        return store.read_source(sid) or store.read_amendment(sid)
    finally:
        store.close()


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_tokenizer_span_exactness_real_statutes() -> None:
    checked = 0
    for sid in _REAL_SIDS:
        xb = _real_xml(sid)
        if not xb:
            continue
        bundle = build_surface_bundle(xb, sid)
        for unit in bundle.units:
            tape = unit.token_tape
            assert isinstance(tape, TokenTape)
            _assert_tape_exact_and_total(unit.raw_text, tape)
        checked += 1
        if checked >= 3:
            break
    assert checked >= 3, f"needed ≥3 real statutes with body text, got {checked}"


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_lens_parity_real_statutes() -> None:
    checked = 0
    for sid in _REAL_SIDS:
        xb = _real_xml(sid)
        if not xb:
            continue
        bundle = build_surface_bundle(xb, sid)
        unit = bundle.units[0]
        raw = unit.raw_text
        oracle = _oracle_seeds(raw)
        lens = _lens_seeds_for_unit(unit)
        assert lens == oracle, (
            f"parity divergence on real statute {sid}:\n"
            f" #oracle={len(oracle)} #lens={len(lens)}\n"
            f" first_diff={_first_diff(oracle, lens)}"
        )
        checked += 1
        if checked >= 5:
            break
    assert checked >= 5, f"needed ≥5 real statutes, got {checked}"


def _lens_seeds_for_unit(unit: SourceSurfaceUnit) -> list[tuple]:
    res = ExceptionConditionLens().analyze(
        _bundle(unit), context=SurfaceAnalysisContext()
    )
    out: list[tuple] = []
    for seed in res.node_seeds:
        ref = seed.source_ref
        assert ref is not None
        out.append(
            (
                seed.payload["cue_kind"],
                seed.payload["marker_text"],
                seed.payload["scope_hint"],
                ref.char_start,
                ref.char_end,
            )
        )
    return out


def _first_diff(a: list[tuple], b: list[tuple]) -> object:
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return (i, x, y)
    if len(a) != len(b):
        return ("len", len(a), len(b))
    return None
