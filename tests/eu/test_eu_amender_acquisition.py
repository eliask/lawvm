"""Unit tests for the #9 EU amender/corrigendum byte-acquisition lane.

No network: every fetch is a synthetic seam. Covers the wrong-manifestation-item
fix (sibling ``DOC_N`` ACT-body resolution), the truly-missing amender store, the
``CORRECTED_BY`` corrigendum-resource extraction (celex + Cellar UUID pairing),
and the corrigendum ``CORR``-body acquisition.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

from farchive import Farchive

from lawvm.eu.eu_acquire import (
    CorrigendumResourceRef,
    acquire_amender_act,
    acquire_corrigendum,
    celex_locator,
    extract_corrigendum_resources,
    resolve_act_body,
)

FETCHED_AT = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _http_404(url: str) -> HTTPError:
    """A properly-typed 404 (a missing sibling ``DOC_N`` index the probe expects)."""
    return HTTPError(url, 404, "Not Found", Message(), None)


_STEM = "http://publications.europa.eu/resource/cellar/abcd.0006.02/"
_DOC1 = _STEM + "DOC_1"  # a DOC publication envelope
_DOC2 = _STEM + "DOC_2"  # the real ACT body
_DOC3 = _STEM + "DOC_3"  # an ANNEX member

_ENVELOPE = b'<?xml version="1.0"?><DOC><TOC/></DOC>'
_ACT_BODY = b'<?xml version="1.0"?><ACT><TITLE><TI>Amending Reg</TI></TITLE></ACT>'
_ANNEX = b'<?xml version="1.0"?><ANNEX><TI>Annex I</TI></ANNEX>'
_CORR_BODY = b'<?xml version="1.0"?><CORR><TITLE><TI>Corrigendum</TI></TITLE></CORR>'

_MAN_URI = "http://publications.europa.eu/resource/cellar/abcd.0006.02.fmx4"


def _notice_with_item(item_url: str) -> bytes:
    """A tree notice exposing ONE ENG fmx4 manifestation with ``item_url``.

    Mirrors the shape ``cellar.list_manifestation_options`` walks: EXPRESSION
    (with EXPRESSION_USES_LANGUAGE + EXPRESSION_MANIFESTED_BY_MANIFESTATION) and
    a MANIFESTATION whose URI matches the expression link.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NOTICE type="tree">
  <WORK>
    <URI><VALUE>http://publications.europa.eu/resource/celex/X</VALUE>
      <IDENTIFIER>X</IDENTIFIER><TYPE>cellar</TYPE></URI>
  </WORK>
  <EXPRESSION>
    <URI><VALUE>http://publications.europa.eu/resource/cellar/abcd.0006</VALUE>
      <IDENTIFIER>expr</IDENTIFIER><TYPE>cellar</TYPE></URI>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>ENG</IDENTIFIER>
      <OP-CODE>ENG</OP-CODE><PREFLABEL>English</PREFLABEL></EXPRESSION_USES_LANGUAGE>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <URI><VALUE>{_MAN_URI}</VALUE><IDENTIFIER>man</IDENTIFIER>
        <TYPE>cellar</TYPE></URI>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="fmx4">
    <URI><VALUE>{_MAN_URI}</VALUE><IDENTIFIER>man</IDENTIFIER>
      <TYPE>cellar</TYPE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>{item_url}</VALUE><IDENTIFIER>i1</IDENTIFIER>
        <TYPE>cellar</TYPE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
""".encode("utf-8")


# A notice whose fmx4 manifestation lists the ENVELOPE item first (DOC_1) — the
# wrong-manifestation pathology the first acquisition run fell into.
_NOTICE_ENVELOPE_FIRST = _notice_with_item(_DOC1)

_SIBLINGS = {_DOC1: _ENVELOPE, _DOC2: _ACT_BODY, _DOC3: _ANNEX}


def _fetch_notice_envelope(*_a, **_k):
    return _NOTICE_ENVELOPE_FIRST, {}


def _fetch_item_siblings(url, _timeout):
    if url in _SIBLINGS:
        return _SIBLINGS[url], {}
    raise _http_404(url)


# --- resolve_act_body -------------------------------------------------------


def test_resolve_act_body_returns_selected_when_already_act() -> None:
    body, url = resolve_act_body(
        _DOC2, _ACT_BODY, fetch_item=_fetch_item_siblings, timeout_s=5
    )
    assert body == _ACT_BODY
    assert url == _DOC2


def test_resolve_act_body_probes_siblings_from_envelope() -> None:
    # Selected item is the DOC envelope; the ACT body is a sibling (DOC_2).
    body, url = resolve_act_body(
        _DOC1, _ENVELOPE, fetch_item=_fetch_item_siblings, timeout_s=5
    )
    assert body == _ACT_BODY
    assert url == _DOC2


def test_resolve_act_body_probes_siblings_from_annex() -> None:
    body, url = resolve_act_body(
        _DOC3, _ANNEX, fetch_item=_fetch_item_siblings, timeout_s=5
    )
    assert body == _ACT_BODY
    assert url == _DOC2


def test_resolve_act_body_respects_accept_roots() -> None:
    # With CORR-only accept, no sibling qualifies -> None (never a wrong store).
    body, url = resolve_act_body(
        _DOC1,
        _ENVELOPE,
        fetch_item=_fetch_item_siblings,
        timeout_s=5,
        accept_roots=("CORR",),
    )
    assert body is None
    assert url == ""


def test_resolve_act_body_none_when_no_doc_pattern() -> None:
    body, url = resolve_act_body(
        "http://example/notdoc", _ENVELOPE, fetch_item=_fetch_item_siblings, timeout_s=5
    )
    assert body is None


# --- acquire_amender_act ----------------------------------------------------


def test_acquire_amender_act_stores_act_body_from_wrong_item(tmp_path: Path) -> None:
    fa = Farchive(str(tmp_path / "eu.farchive"))
    try:
        rep = acquire_amender_act(
            fa,
            "32016R0646",
            fetched_at=FETCHED_AT,
            _fetch_notice=_fetch_notice_envelope,
            _fetch_item=_fetch_item_siblings,
        )
        assert rep["acquire_status"] == "STORED", rep
        assert rep["root"] == "ACT"
        # The stored bytes are the ACT body, NOT the envelope the notice listed.
        stored = fa.get(celex_locator("32016R0646", "enacted", "eng", "fmx4"))
        assert stored == _ACT_BODY
    finally:
        fa.close()


def test_acquire_amender_act_idempotent(tmp_path: Path) -> None:
    fa = Farchive(str(tmp_path / "eu.farchive"))
    try:
        first = acquire_amender_act(
            fa,
            "32016R0646",
            fetched_at=FETCHED_AT,
            _fetch_notice=_fetch_notice_envelope,
            _fetch_item=_fetch_item_siblings,
        )
        assert first["acquire_status"] == "STORED"
        second = acquire_amender_act(
            fa,
            "32016R0646",
            fetched_at=FETCHED_AT,
            _fetch_notice=_fetch_notice_envelope,
            _fetch_item=_fetch_item_siblings,
        )
        assert second["acquire_status"] == "RE_OBSERVED"
    finally:
        fa.close()


def test_acquire_amender_act_no_act_body_is_typed_gap(tmp_path: Path) -> None:
    # A notice whose only siblings are non-act -> typed NO_ACT_BODY, no store.
    only_annex = {_DOC1: _ENVELOPE, _DOC3: _ANNEX}

    def _fetch_only_annex(url, _t):
        if url in only_annex:
            return only_annex[url], {}
        raise _http_404(url)

    fa = Farchive(str(tmp_path / "eu.farchive"))
    try:
        rep = acquire_amender_act(
            fa,
            "39999R9999",
            fetched_at=FETCHED_AT,
            _fetch_notice=_fetch_notice_envelope,
            _fetch_item=_fetch_only_annex,
        )
        assert rep["acquire_status"].startswith("NO_ACT_BODY"), rep
        assert not fa.history(celex_locator("39999R9999", "enacted", "eng", "fmx4"))
    finally:
        fa.close()


# --- extract_corrigendum_resources ------------------------------------------

_CORR_UUID = "b1669090-76ac-4c99-9d8a-07fd11421783"
_BASE_NOTICE_WITH_CORRIG = f"""<?xml version="1.0" encoding="UTF-8"?>
<NOTICE type="tree">
  <WORK>
    <RESOURCE_LEGAL_CORRECTED_BY_RESOURCE_LEGAL>
      <URI><TYPE>cellar</TYPE><IDENTIFIER>{_CORR_UUID}</IDENTIFIER></URI>
      <URI><TYPE>celex</TYPE><IDENTIFIER>32008R0402R(01)</IDENTIFIER></URI>
    </RESOURCE_LEGAL_CORRECTED_BY_RESOURCE_LEGAL>
  </WORK>
</NOTICE>
""".encode("utf-8")


def test_extract_corrigendum_resources_pairs_celex_and_uuid() -> None:
    resources, looked = extract_corrigendum_resources(_BASE_NOTICE_WITH_CORRIG)
    assert looked is True
    assert resources == (
        CorrigendumResourceRef(celex="32008R0402R(01)", cellar_uuid=_CORR_UUID),
    )


def test_extract_corrigendum_resources_skips_missing_uuid() -> None:
    # A CORRECTED_BY relation with a celex but no resolvable cellar uuid is not
    # actionable for the byte lane -> skipped (no fabricated pairing).
    notice = b"""<?xml version="1.0"?>
<NOTICE><WORK><RESOURCE_LEGAL_CORRECTED_BY_RESOURCE_LEGAL>
  <URI><TYPE>celex</TYPE><IDENTIFIER>32008R0402R(01)</IDENTIFIER></URI>
</RESOURCE_LEGAL_CORRECTED_BY_RESOURCE_LEGAL></WORK></NOTICE>"""
    resources, looked = extract_corrigendum_resources(notice)
    assert looked is True
    assert resources == ()


def test_extract_corrigendum_resources_unparseable_notice() -> None:
    resources, looked = extract_corrigendum_resources(b"not xml <<<")
    assert looked is False
    assert resources == ()


# --- acquire_corrigendum ----------------------------------------------------

_CORR_NOTICE = _notice_with_item(_DOC1)


def test_acquire_corrigendum_stores_corr_body(tmp_path: Path) -> None:
    corr_siblings = {_DOC1: _ENVELOPE, _DOC2: _CORR_BODY}

    def _fetch_corr_item(url, _t):
        if url in corr_siblings:
            return corr_siblings[url], {}
        raise _http_404(url)

    res = CorrigendumResourceRef(celex="32008R0402R(01)", cellar_uuid=_CORR_UUID)
    fa = Farchive(str(tmp_path / "eu.farchive"))
    try:
        rep = acquire_corrigendum(
            fa,
            res,
            fetched_at=FETCHED_AT,
            _fetch_notice=lambda _uuid, _lang, _t: (_CORR_NOTICE, {}),
            _fetch_item=_fetch_corr_item,
        )
        assert rep["acquire_status"] == "STORED", rep
        assert rep["root"] == "CORR"
        stored = fa.get(celex_locator("32008R0402R(01)", "enacted", "eng", "fmx4"))
        assert stored == _CORR_BODY
    finally:
        fa.close()


def test_acquire_corrigendum_rejects_cons_act_sibling(tmp_path: Path) -> None:
    # A corrigendum notice whose only act-shaped sibling is a co-bundled CONS.ACT
    # (a DIFFERENT Work) must NOT be stored under the corrigendum's locator.
    cons = b'<?xml version="1.0"?><CONS.ACT><TITLE><TI>X</TI></TITLE></CONS.ACT>'
    siblings = {_DOC1: _ENVELOPE, _DOC2: cons}

    def _fetch_item(url, _t):
        if url in siblings:
            return siblings[url], {}
        raise _http_404(url)

    res = CorrigendumResourceRef(celex="32008R0692R(03)", cellar_uuid=_CORR_UUID)
    fa = Farchive(str(tmp_path / "eu.farchive"))
    try:
        rep = acquire_corrigendum(
            fa,
            res,
            fetched_at=FETCHED_AT,
            _fetch_notice=lambda _uuid, _lang, _t: (_CORR_NOTICE, {}),
            _fetch_item=_fetch_item,
        )
        assert rep["acquire_status"].startswith("NO_ACT_BODY"), rep
        assert not fa.history(
            celex_locator("32008R0692R(03)", "enacted", "eng", "fmx4")
        )
    finally:
        fa.close()
