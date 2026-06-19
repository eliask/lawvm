"""Unit tests for the cross-family UNION token-ownership census harness.

Exercises the SourceSyntaxGraph "ruler" on synthetic provisions — no corpus:

  * a fully-owned provision (a commencement clause the temporal family claims)
    has zero silent-unowned cheap-signal tokens;
  * a provision with a deliberately-unowned cheap-signal span (an ``HE`` /
    preparatory-work reference, a bare ``§``, in plain prose no family parses) is
    FLAGGED into the silent-unowned bucket and the ranked worklist;
  * a benign-prose provision (no cheap signal at all) is NOT flagged;
  * the four token buckets always partition the classified-token total.

These run entirely in-memory via :func:`classify_body` / :func:`union_over_sentence`
(both corpus-free); the corpus driver is exercised separately on the sample.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.union_ownership_census import (
    FAMILY_PARSERS,
    UnownedSignalSpan,
    classify_body,
    union_over_sentence,
)


def _buckets(text: str) -> dict[str, int]:
    bc, _fc, _usc, _ex, _sc = classify_body("test/1", text)
    return dict(bc)


def _shapes(text: str) -> dict[str, int]:
    _bc, _fc, usc, _ex, _sc = classify_body("test/1", text)
    return dict(usc)


def _examples(text: str) -> list[UnownedSignalSpan]:
    _bc, _fc, _usc, ex, _sc = classify_body("test/1", text)
    return ex


def test_roster_has_all_six_families() -> None:
    names = {fid for fid, _fn in FAMILY_PARSERS}
    assert names == {
        "citation",
        "definition",
        "temporal",
        "modal",
        "condition_exception",
        "delegation",
    }


def test_fully_owned_provision_has_no_silent_unowned() -> None:
    # A commencement clause: the temporal family claims the cue + date span, so
    # the cheap temporal signals (``tulee voimaan`` / ``voimaan``) are OWNED and
    # nothing is silent-unowned.
    text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."
    buckets = _buckets(text)
    assert buckets.get("silent", 0) == 0
    assert buckets.get("owned", 0) > 0
    assert _shapes(text) == {}


def test_unowned_cheap_signal_span_is_flagged() -> None:
    # An HE (preparatory-work) reference embedded in plain prose no family parses
    # is a cheap legal signal with NO owning typed construction → silent-unowned,
    # and it appears in the ranked worklist + the self-evidencing examples.
    text = "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti."
    buckets = _buckets(text)
    assert buckets.get("silent", 0) > 0, buckets
    shapes = _shapes(text)
    assert shapes.get("he_ref", 0) == 1, shapes
    examples = _examples(text)
    he = [e for e in examples if e.shape == "he_ref"]
    assert he, examples
    # Self-evidencing: the example carries the VERBATIM offending span text.
    assert "HE 5/2019" in he[0].text
    assert "HE 5/2019" in he[0].context


def test_bare_section_mark_in_prose_is_flagged() -> None:
    # A bare ``§`` in prose no family claims is a silent-unowned structural signal.
    text = "Kyseinen § sisältää tietoa."
    assert _buckets(text).get("silent", 0) > 0
    assert _shapes(text).get("section_mark", 0) == 1


def test_benign_prose_is_not_flagged() -> None:
    # Ordinary prose carrying NO cheap legal signal: every token is benign, none
    # owned, none silent, none residual.
    text = "Tässä pykälässä kuvataan yleisiä periaatteita selkeästi."
    buckets = _buckets(text)
    assert buckets.get("silent", 0) == 0
    assert buckets.get("residual", 0) == 0
    assert buckets.get("owned", 0) == 0
    assert buckets.get("benign", 0) > 0
    assert _shapes(text) == {}


def test_buckets_partition_the_classified_tokens() -> None:
    # The four buckets must sum to the non-whitespace token total for any body.
    from lawvm.finland.legal_surface.tokenize import build_token_tape

    for text in (
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.",
        "Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti.",
        "Tässä pykälässä kuvataan yleisiä periaatteita selkeästi.",
        "Viranomainen voi antaa tarkempia säännöksiä asetuksella.",
    ):
        buckets = _buckets(text)
        partition = sum(buckets.values())
        tape = build_token_tape("t", text)
        nonws = sum(1 for tok in tape.tokens if tok.category != "whitespace")
        assert partition == nonws, (text, buckets, nonws)


def test_coordinated_application_half_is_owned_not_silent() -> None:
    # The L0 ruler's dominant unowned applicability span: in
    # "tulee voimaan X ja sitä sovelletaan Y" the "sitä sovelletaan" half used to
    # be silent-unowned (production keeps only the commencement). The temporal
    # family now owns BOTH halves, so the cheap "sovelletaan" signal is NOT silent.
    text = (
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 1991 "
        "ja sitä sovelletaan ensimmäisen kerran vuodelta 1991 toimitettavassa verotuksessa."
    )
    su = union_over_sentence(text)
    cue_at = text.index("sovelletaan")
    assert "temporal" in su.owners.get(cue_at, frozenset())
    assert _shapes(text).get("sovelletaan", 0) == 0, _shapes(text)


def test_standalone_application_signal_is_owned_not_silent() -> None:
    # "Lakia sovelletaan …" (production never recognized this) is now owned by the
    # temporal family, so its cheap "sovelletaan" signal is not silent-unowned.
    text = "Lakia sovelletaan vakuutusmaksuun, joka on kertynyt vuoden 1991 loppuun."
    assert _shapes(text).get("sovelletaan", 0) == 0, _shapes(text)
    su = union_over_sentence(text)
    assert "temporal" in {f for fams in su.owners.values() for f in fams}


def test_union_over_sentence_records_owning_families() -> None:
    # The owner map records WHICH family claimed each char; a commencement clause
    # is owned by the temporal family.
    su = union_over_sentence("Tämä laki tulee voimaan 1.1.2020.")
    families_seen = {f for fams in su.owners.values() for f in fams}
    assert "temporal" in families_seen
