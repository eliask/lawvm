"""Unit tests for the EU CELEX acquisition lane (eu_acquire).

No network: SAVED fixture bytes only. A small synthetic tree notice exposes a
FIN/fmx4 manifestation item; a synthetic Formex item stands in for the witness.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from farchive import Farchive

from lawvm.eu.eu_acquire import (
    CelexAcquisitionFailure,
    CelexAcquisitionMetadata,
    acquire_celex,
    celex_locator,
    default_universe,
    extract_corrigendum_celexes,
    verify_xml_witness,
)

GDPR = "32016R0679"
CONSOLIDATION = "20160504"
LANG = "fin"
FETCHED_AT = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)

ITEM_URL = "http://publications.europa.eu/resource/cellar/abc.0001.01/DOC_1"

# A synthetic tree notice exposing one FIN expression manifested by an fmx4
# manifestation that has one item (the URL we'd fetch). Shape mirrors what
# cellar.list_manifestation_options walks: NOTICE > WORK, EXPRESSION (with
# EXPRESSION_USES_LANGUAGE), MANIFESTATION (manifestation-type, MANIFESTATION_HAS_ITEM).
NOTICE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<NOTICE type="tree">
  <WORK>
    <URI><VALUE>http://publications.europa.eu/resource/celex/{GDPR}</VALUE>
      <IDENTIFIER>{GDPR}</IDENTIFIER><TYPE>cellar</TYPE></URI>
    <SAMEAS><URI><VALUE>{GDPR}</VALUE><IDENTIFIER>{GDPR}</IDENTIFIER>
      <TYPE>celex</TYPE></URI></SAMEAS>
    <SAMEAS><URI><VALUE>http://data.europa.eu/eli/reg/2016/679/oj</VALUE>
      <IDENTIFIER>eli</IDENTIFIER><TYPE>eli</TYPE></URI></SAMEAS>
    <WORK_HAS_CORRIGENDUM_WORK>
      <SAMEAS><URI><VALUE>32016R0679R(01)</VALUE>
        <IDENTIFIER>32018R0679R(02)</IDENTIFIER><TYPE>other</TYPE></URI></SAMEAS>
      <SAMEAS><URI><VALUE>32016R0680</VALUE>
        <IDENTIFIER>32016R0680</IDENTIFIER><TYPE>celex</TYPE></URI></SAMEAS>
    </WORK_HAS_CORRIGENDUM_WORK>
  </WORK>
  <EXPRESSION>
    <URI><VALUE>http://publications.europa.eu/resource/cellar/abc.0001</VALUE>
      <IDENTIFIER>expr</IDENTIFIER><TYPE>cellar</TYPE></URI>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>FIN</IDENTIFIER>
      <OP-CODE>FIN</OP-CODE><PREFLABEL>Finnish</PREFLABEL></EXPRESSION_USES_LANGUAGE>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <URI><VALUE>http://publications.europa.eu/resource/cellar/abc.0001.01.fmx4</VALUE>
        <IDENTIFIER>man</IDENTIFIER><TYPE>cellar</TYPE></URI>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="fmx4">
    <URI><VALUE>http://publications.europa.eu/resource/cellar/abc.0001.01.fmx4</VALUE>
      <IDENTIFIER>man</IDENTIFIER><TYPE>cellar</TYPE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>{ITEM_URL}</VALUE><IDENTIFIER>item</IDENTIFIER>
        <TYPE>cellar</TYPE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
""".encode("utf-8")

# A synthetic Formex consolidated act (root CONS.ACT, mirrors real fmx4).
FORMEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONS.ACT><TITLE><TI>Synthetic GDPR consolidated</TI></TITLE>
<ENACTING.TERMS><ARTICLE><TI.ART>Article 1</TI.ART></ARTICLE></ENACTING.TERMS>
</CONS.ACT>
"""

HTML_ERROR_PAGE = b"""<!DOCTYPE html><html><head><title>403 Forbidden</title></head>
<body>Bot block.</body></html>"""


def _fake_fetch_notice(*_a, **_k):
    return NOTICE_XML, {"sha256": hashlib.sha256(NOTICE_XML).hexdigest()}


def _fake_fetch_item(*_a, **_k):
    return FORMEX_XML, {"sha256": hashlib.sha256(FORMEX_XML).hexdigest()}


def _archive(tmp_path: Path) -> Farchive:
    return Farchive(str(tmp_path / "eu_cellar.farchive"))


# --------------------------------------------------------------------------- #
# Locator construction                                                        #
# --------------------------------------------------------------------------- #


def test_locator_construction() -> None:
    assert (
        celex_locator(GDPR, CONSOLIDATION, LANG, "fmx4")
        == "cellar://celex/32016R0679/20160504/fin/fmx4"
    )
    assert (
        celex_locator(GDPR, "enacted", "eng", "xhtml")
        == "cellar://celex/32016R0679/enacted/eng/xhtml"
    )


# --------------------------------------------------------------------------- #
# verify-before-store                                                          #
# --------------------------------------------------------------------------- #


def test_verify_rejects_html_error_page() -> None:
    ok, why = verify_xml_witness(HTML_ERROR_PAGE)
    assert ok is False
    assert "html" in why.lower()


def test_verify_rejects_empty() -> None:
    ok, _ = verify_xml_witness(b"")
    assert ok is False


def test_verify_accepts_real_xml() -> None:
    ok, why = verify_xml_witness(FORMEX_XML)
    assert ok is True
    assert why == ""


# --------------------------------------------------------------------------- #
# Store-then-resolve round trip + Work id + universe                          #
# --------------------------------------------------------------------------- #


def test_acquire_stores_witnesses(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    run = acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language=LANG,
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_fake_fetch_item,
    )
    item_loc = celex_locator(GDPR, CONSOLIDATION, LANG, "fmx4")
    notice_loc = celex_locator(GDPR, CONSOLIDATION, LANG, "notice")

    assert run.failed == 0
    assert run.failures == []
    assert run.added == 2  # notice + item
    assert set(run.stored_locators) == {item_loc, notice_loc}

    # The item witness resolves to the exact Formex bytes (content-addressed).
    assert archive.get(item_loc) == FORMEX_XML
    assert archive.get(notice_loc) == NOTICE_XML

    # Work id is the language-neutral celex id, no FI-specific work id.
    assert run.metadata is not None
    assert run.metadata.work_canonical_id == "celex:32016R0679"
    assert run.metadata.expression_language == LANG
    assert run.metadata.consolidation_date == CONSOLIDATION
    assert run.metadata.source_sha256 == hashlib.sha256(FORMEX_XML).hexdigest()

    # Universe is the honest demand-mode claim.
    assert run.universe is not None
    assert run.universe.closed_world_claim is False
    archive.close()


def test_provenance_metadata_round_trips(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language=LANG,
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_fake_fetch_item,
    )
    item_loc = celex_locator(GDPR, CONSOLIDATION, LANG, "fmx4")
    span = archive.resolve(item_loc)
    assert span is not None
    md = span.last_metadata
    assert md is not None
    assert md["celex"] == GDPR
    assert md["work_canonical_id"] == "celex:32016R0679"
    assert md["expression_language"] == LANG
    assert md["consolidation_date"] == CONSOLIDATION
    assert md["format"] == "fmx4"
    assert md["source_sha256"] == hashlib.sha256(FORMEX_XML).hexdigest()
    assert md["item_uri"] == ITEM_URL
    archive.close()


# --------------------------------------------------------------------------- #
# Idempotent dedup                                                             #
# --------------------------------------------------------------------------- #


def test_idempotent_dedup(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    item_loc = celex_locator(GDPR, CONSOLIDATION, LANG, "fmx4")

    run1 = acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language=LANG,
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_fake_fetch_item,
    )
    assert run1.added == 2
    hist1 = archive.history(item_loc)
    assert len(hist1) == 1
    digest1 = hist1[-1].digest

    # Second call: identical bytes -> observe, not re-store.
    run2 = acquire_celex(
        GDPR,
        fetched_at=datetime(2026, 6, 22, 13, 0, 0, tzinfo=timezone.utc),
        language=LANG,
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_fake_fetch_item,
    )
    assert run2.added == 0
    assert run2.skipped == 2
    hist2 = archive.history(item_loc)
    assert len(hist2) == 1  # history length unchanged
    assert hist2[-1].digest == digest1  # digest stable
    archive.close()


def test_two_languages_same_fetched_at_do_not_collide(tmp_path: Path) -> None:
    """Two expression-languages of one CELEX, acquired under a SINGLE
    second-granularity ``fetched_at`` (exactly how the corpus loop drives it),
    must each store their own ingest-run snapshot rather than colliding.

    Regression: ``_ingest_run_locator`` previously omitted the language, so the
    second language's run snapshot hit the first's locator at the same timestamp
    with a different digest and the farchive raised
    ``ValueError: Same-timestamp digest change ...`` — caught upstream and logged
    as a spurious ``GAP (ValueError)``, masking the honest per-language
    accounting AND losing any real second-language manifestation.
    """
    from lawvm.eu.eu_acquire import _ingest_run_locator

    archive = _archive(tmp_path)
    # Same celex, same fetched_at, two languages — fin then eng (the loop reuses
    # one fetched_at across languages, so this is the real collision shape).
    run_fin = acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language="fin",
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_fake_fetch_item,
    )
    # The second language MUST NOT raise (pre-fix this raised ValueError).
    run_eng = acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language="eng",
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_fake_fetch_item,
    )
    # Each language got its OWN, language-distinct ingest-run snapshot.
    fin_loc = _ingest_run_locator(GDPR, "fin", FETCHED_AT)
    eng_loc = _ingest_run_locator(GDPR, "eng", FETCHED_AT)
    assert fin_loc != eng_loc
    assert archive.get(fin_loc) is not None
    assert archive.get(eng_loc) is not None
    assert run_fin.expression_language.lower() == "fin"
    assert run_eng.expression_language.lower() == "eng"
    archive.close()


# --------------------------------------------------------------------------- #
# Verify-before-store rejects an HTML error page (typed failure, no store)     #
# --------------------------------------------------------------------------- #


def test_html_item_rejected_no_store(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    item_loc = celex_locator(GDPR, CONSOLIDATION, LANG, "fmx4")

    def _bot_block_item(*_a, **_k):
        return HTML_ERROR_PAGE, {}

    run = acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language=LANG,
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_fake_fetch_notice,
        _fetch_item=_bot_block_item,
    )
    # Notice stored, but the bad item was NOT stored.
    assert archive.resolve(item_loc) is None
    assert run.failed == 1
    assert len(run.failures) == 1
    failure = run.failures[0]
    assert isinstance(failure, CelexAcquisitionFailure)
    assert failure.rule_id == "EU_ACQ.ITEM_NOT_XML"
    assert failure.locator == item_loc
    archive.close()


def test_html_notice_rejected_no_store(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    notice_loc = celex_locator(GDPR, CONSOLIDATION, LANG, "notice")

    def _bot_block_notice(*_a, **_k):
        return HTML_ERROR_PAGE, {}

    run = acquire_celex(
        GDPR,
        fetched_at=FETCHED_AT,
        language=LANG,
        consolidation=CONSOLIDATION,
        farchive=archive,
        _fetch_notice=_bot_block_notice,
        _fetch_item=_fake_fetch_item,
    )
    assert archive.resolve(notice_loc) is None
    assert run.failed == 1
    assert run.failures[0].rule_id == "EU_ACQ.NOTICE_NOT_XML"
    archive.close()


# --------------------------------------------------------------------------- #
# Malformed CELEX refused                                                      #
# --------------------------------------------------------------------------- #


def test_malformed_celex_refused(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    with pytest.raises(ValueError, match="well-formed CELEX"):
        acquire_celex(
            "not-a-celex",
            fetched_at=FETCHED_AT,
            farchive=archive,
            _fetch_notice=_fake_fetch_notice,
            _fetch_item=_fake_fetch_item,
        )
    archive.close()


# --------------------------------------------------------------------------- #
# Universe + corrigenda extraction                                            #
# --------------------------------------------------------------------------- #


def test_default_universe_is_open_world() -> None:
    u = default_universe()
    assert u.closed_world_claim is False
    assert u.universe_kind == "curated_slice"


def test_corrigenda_extraction() -> None:
    celexes, looked = extract_corrigendum_celexes(NOTICE_XML)
    assert looked is True
    # The well-formed celex SAMEAS under the corrigendum relation is harvested.
    assert "32016R0680" in celexes


def test_corrigenda_extraction_honesty_on_garbage() -> None:
    celexes, looked = extract_corrigendum_celexes(b"not xml")
    assert looked is False
    assert celexes == ()


def test_metadata_dict_serializes_corrigenda() -> None:
    md = CelexAcquisitionMetadata(
        celex=GDPR,
        work_canonical_id="celex:32016R0679",
        work_uri="",
        expression_language=LANG,
        manifestation_uri="",
        item_uri="",
        fmt="fmx4",
        consolidation_date=CONSOLIDATION,
        fetched_at=FETCHED_AT,
        source_sha256="deadbeef",
        corrigendum_celexes=("32016R0680",),
        corrigenda_extracted=True,
    )
    d = md.to_metadata_dict()
    assert d["corrigendum_celexes"] == "32016R0680"
    assert d["corrigenda_extracted"] == "true"
    assert d["work_canonical_id"] == "celex:32016R0679"
