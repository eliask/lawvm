"""Increment 1 goal 2: real consolidation-PIT oracle (NEVER repair).

Acquire the sector-0 CONSOLIDATION of the degree-57 base ``32016R0044`` at the
PIT ``2016-04-01`` (the date-of-application of amender ``32016R0466``, which
replaced Annex III), and run the existing non-repairing comparator
(``eu_oracle_divergence``) end-to-end:

  native replay (lower → order → apply) → PIT body
    vs sector-0 consolidation 02016R0044-20160401
    → per-article classification (agreement / text_divergence / present-in-one)
    → NEVER repaired toward the consolidation (it is editorial, "no legal value")

Core tests are OFFLINE: a pinned consolidated FMX4 excerpt fixture (the real
consolidated bytes are NOT committed). The pinned consolidation is deliberately
made to DIVERGE from the native replay on Article 6 so the comparator
demonstrates a classified, non-repaired divergence, and the consolidation-CELEX
construction + the typed REST-failure witness are exercised.

A networked smoke (opt-in via ``LAWVM_EU_NETWORK_SMOKE=1``) acquires the REAL
consolidation; if the CELLAR REST byte lane is 5xx (the observed intermittent
``502`` / ``Unable to acquire JDBC Connection``), it is recorded as a typed
``ConsolidationAcquisitionFailure`` and the test skips — never a silent zero.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind

_ANNEX_KIND = cast(IRNodeKind, "annex")  # grafter vocabulary (see grafter.py)
from lawvm.eu.eu_consolidation_oracle import (
    ConsolidationAcquisitionFailure,
    build_consolidation_oracle,
    consolidated_celex,
    enumerate_consolidation_series,
    fetch_consolidation_bytes,
    parse_consolidation_date,
)
from lawvm.eu.eu_ordering import order_eu_ops
from lawvm.eu.fmx4_amendment_grammar import lower_amending_act
from lawvm.eu.pipeline import apply_eu_ops_conserved

FIXTURES = Path(__file__).parent / "eu" / "fixtures"
BASE_CELEX = "32016R0044"
AS_OF = "2016-04-01"


# --------------------------------------------------------------------------- #
# Consolidated-CELEX construction                                             #
# --------------------------------------------------------------------------- #


def test_consolidated_celex_construction() -> None:
    assert consolidated_celex(BASE_CELEX, AS_OF) == "02016R0044-20160401"
    assert consolidated_celex(BASE_CELEX, "20160401") == "02016R0044-20160401"
    assert parse_consolidation_date("02016R0044-20160401") == "20160401"


def test_consolidated_celex_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        consolidated_celex("not-a-celex", AS_OF)
    with pytest.raises(ValueError):
        consolidated_celex(BASE_CELEX, "2016")
    with pytest.raises(ValueError):
        parse_consolidation_date("32016R0044")  # not a sector-0 consolidation


# --------------------------------------------------------------------------- #
# Replay → consolidation oracle, offline, NEVER repaired                       #
# --------------------------------------------------------------------------- #


def _replayed_pit() -> IRStatute:
    """Native replay of the real ANNEX-root amender against a base carrying the
    targeted Annex III and Article 6."""
    base = IRStatute(
        statute_id=BASE_CELEX,
        title="base",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="6", text="Article 6 (native replay)."),
                IRNode(kind=_ANNEX_KIND, label="III", text="OLD Annex III listing."),
            ),
        ),
    )
    lowered = lower_amending_act(
        (FIXTURES / "amending_annex_root_excerpt.fmx4.xml").read_bytes(),
        "32016R0466",
        base_celex=BASE_CELEX,
        effective=AS_OF,
    )
    ordered = order_eu_ops(lowered.ops)
    return apply_eu_ops_conserved(base, list(ordered.ops)).statute


def _fetch_pinned_consolidation(_celex: str) -> bytes:
    return (FIXTURES / "consolidated_excerpt.fmx4.xml").read_bytes()


def _fetch_pinned_cons_act(_celex: str) -> bytes:
    """The REAL consolidated manifestation SHAPE (CONS.ACT/CONS.DOC), fetched live
    in Increment 2 once the CELLAR REST byte lane recovered, pinned offline."""
    return (FIXTURES / "consolidated_cons_act_excerpt.fmx4.xml").read_bytes()


def test_oracle_diffs_real_cons_act_manifestation_increment2() -> None:
    """Increment 2 (REST recovered): the live consolidation byte lane returns a
    CONS.ACT/CONS.DOC manifestation (NOT an ACT root). The grafter now parses
    CONS.DOC as the ACT-equivalent root and its ALINEA/PARAG>ALINEA article text,
    so the replay-vs-consolidation oracle diff runs end-to-end on the real shape —
    classified, never repaired. (Increment 1 could not reach this: REST was 5xx.)"""
    replayed = _replayed_pit()
    before6 = _section_text(replayed, "6")

    cmp = build_consolidation_oracle(
        replayed,
        base_celex=BASE_CELEX,
        as_of=AS_OF,
        fetch_consolidation=_fetch_pinned_cons_act,
    )

    # The CONS.DOC carries Articles 1 and 6; the comparator builds a per-article
    # ledger (the consolidated Article 6 EDITORIAL text diverges from the native
    # replay's Article 6).
    assert cmp.article_count >= 2
    labels = {d.article_label for d in cmp.divergences}
    assert {"1", "6"} <= labels
    kinds = cmp.divergences_by_kind()
    assert kinds.get("text_divergence", 0) >= 1

    # NEVER repaired: the native replay's Article 6 is byte-identical afterward,
    # and the consolidated EDITORIAL text did not leak into it.
    assert _section_text(replayed, "6") == before6
    assert "native replay" in before6
    assert "CONSOLIDATED EDITORIAL" not in before6


def test_cons_act_article_text_recovered_from_alinea() -> None:
    """The Increment-2 grafter ALINEA fix: consolidated article text lives in
    <ALINEA> (Article 1, direct) and <PARAG><ALINEA> (Article 6). Both are
    recovered — the Increment-1 grafter (P/LIST only) dropped them, which would
    have made every consolidated article look text-empty in the oracle diff."""
    import tempfile
    from pathlib import Path as _P

    from lawvm.eu.grafter import parse_eu_regulation_ir

    raw = _fetch_pinned_cons_act("02016R0044-20160401")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tf:
        tf.write(raw)
        tf.flush()
        st = parse_eu_regulation_ir(_P(tf.name), celex="02016R0044-20160401")

    def _node_text(node: IRNode) -> str:
        parts = [node.text] if node.text else []
        for c in node.children:
            parts.append(_node_text(c))
        return " ".join(p for p in parts if p)

    arts = {
        node.label: _node_text(node)
        for node in _iter(st.body)
        if str(node.kind) == "section" and node.label
    }
    assert "definitions apply" in arts["1"]  # ALINEA-direct
    assert "freezing of funds" in arts["6"]  # PARAG>ALINEA


def _iter(node: IRNode):
    yield node
    for c in node.children:
        yield from _iter(c)


def test_oracle_classifies_divergence_and_never_repairs() -> None:
    replayed = _replayed_pit()
    before = _section_text(replayed, "6")

    cmp = build_consolidation_oracle(
        replayed,
        base_celex=BASE_CELEX,
        as_of=AS_OF,
        fetch_consolidation=_fetch_pinned_consolidation,
    )

    # The consolidation diverges from the native replay on Article 6 (editorial
    # re-rendering). Classified, not repaired.
    kinds = cmp.divergences_by_kind()
    assert kinds.get("text_divergence", 0) >= 1
    assert cmp.divergence_count >= 1
    # Honest agreement/divergence ledger is populated.
    assert cmp.article_count >= 1
    assert 0.0 <= cmp.agreement_fraction <= 1.0

    # NEVER repaired: the replayed Article 6 body is byte-identical after compare.
    assert _section_text(replayed, "6") == before
    assert "native replay" in before
    assert "EDITORIAL" not in before


def test_oracle_rest_failure_is_typed_witness_not_silent_zero() -> None:
    """A REST byte-lane failure (the observed CELLAR 502) becomes a typed
    ConsolidationAcquisitionFailure — never a fabricated empty/agreeing oracle."""
    replayed = _replayed_pit()

    def _boom(_celex: str) -> bytes:
        raise OSError("HTTP Error 502: Bad Gateway")

    with pytest.raises(ConsolidationAcquisitionFailure) as ei:
        build_consolidation_oracle(
            replayed,
            base_celex=BASE_CELEX,
            as_of=AS_OF,
            fetch_consolidation=_boom,
        )
    assert "02016R0044-20160401" in str(ei.value)


def test_oracle_empty_bytes_is_typed_witness() -> None:
    replayed = _replayed_pit()
    with pytest.raises(ConsolidationAcquisitionFailure):
        build_consolidation_oracle(
            replayed,
            base_celex=BASE_CELEX,
            as_of=AS_OF,
            fetch_consolidation=lambda _c: b"",
        )


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _section_text(statute: IRStatute, label: str) -> str:
    out = [""]

    def _walk(node: IRNode) -> None:
        if node.label == label and str(node.kind) == "section":
            out[0] = node.text or ""
        for child in node.children:
            _walk(child)

    _walk(statute.body)
    return out[0]


# --------------------------------------------------------------------------- #
# Networked smoke (opt-in): acquire the REAL consolidation; tolerate 5xx        #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    os.environ.get("LAWVM_EU_NETWORK_SMOKE") != "1",
    reason="networked CELLAR consolidation smoke; set LAWVM_EU_NETWORK_SMOKE=1",
)
def test_live_consolidation_acquire_smoke() -> None:
    from lawvm.eu.cellar import NoticeRequest, _request_notice, select_manifestation_option

    cons_celex = consolidated_celex(BASE_CELEX, AS_OF)

    def _fetch_real(celex: str) -> bytes:
        # Resolve the tree notice → EN FMX4 manifestation item → fetch its bytes.
        # Increment 2: the REST byte lane recovered. The consolidated FMX4 item
        # negotiates ONLY under a permissive Accept (a strict
        # ``application/xml;notice=branch`` 406s); ``Accept: */*`` returns the
        # ``application/xml;type=fmx4`` CONS.ACT manifestation bytes.
        import tempfile
        import urllib.request
        from pathlib import Path as _P

        from lawvm.eu.cellar import USER_AGENT

        req = NoticeRequest(
            celex=celex, notice_format="xml", notice_type="tree", decode_language="eng"
        )
        notice_bytes, _meta = _request_notice(req, timeout_s=45)
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=True) as tf:
            tf.write(notice_bytes)
            tf.flush()
            option = select_manifestation_option(_P(tf.name), "eng", "fmx4")
        items = option.get("items") or []
        if not items:
            raise OSError("consolidated notice exposed no FMX4 item")
        first = items[0]
        item_uri = first if isinstance(first, str) else (
            first.get("uri", {}).get("value", "")
            if isinstance(first.get("uri"), dict)
            else first.get("uri", "")
        )
        if not item_uri:
            raise OSError("consolidated FMX4 item exposed no resolvable URI")
        item_req = urllib.request.Request(
            item_uri, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
        )
        with urllib.request.urlopen(item_req, timeout=45) as resp:
            return resp.read()

    replayed = _replayed_pit()
    try:
        cmp = build_consolidation_oracle(
            replayed, base_celex=BASE_CELEX, as_of=AS_OF, fetch_consolidation=_fetch_real
        )
    except ConsolidationAcquisitionFailure as exc:
        # The CELLAR REST byte lane is intermittently 5xx — recorded honestly.
        pytest.skip(f"CELLAR consolidation byte lane unavailable: {exc}")

    # If acquired, the comparison is a real per-article ledger; never repaired.
    assert cmp.base_celex == BASE_CELEX
    assert cmp.article_count >= 1
    assert 0.0 <= cmp.agreement_fraction <= 1.0


# ---------------------------------------------------------------------------
# Consolidation-series enumeration + the offline consolidated byte lane (#221).
# These are the two pieces the eu_anchor_manifest _doc said were "not in the
# archive": Cellar exposes a DATED sector-0 consolidation series per base, and
# the dated consolidated CELEX's tree notice resolves a graftable FMX4 item.
# Both are exercised OFFLINE here via test seams (no network in CI).
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402


def _sparql_series_json(conscelexes: list[str]) -> bytes:
    """A synthetic Cellar SPARQL results doc for the consolidation-series query."""
    return _json.dumps(
        {
            "head": {"vars": ["conscelex"]},
            "results": {
                "bindings": [
                    {"conscelex": {"type": "literal", "value": c}}
                    for c in conscelexes
                ]
            },
        }
    ).encode("utf-8")


def test_enumerate_consolidation_series_sorted_and_base_filtered() -> None:
    """The series is sorted, de-duplicated, and drops rows whose numeric root is
    not THIS base (a co-consolidated sibling bundled in the same result)."""
    rows = [
        "02008R0692-20140101",
        "02008R0692-20080731",
        "02007R0715-20080731",  # sibling base — must be filtered out
        "02008R0692-20140101",  # duplicate
        "not-a-consolidated-celex",  # malformed — dropped
    ]

    def _fetch(_q: str, _ep: str, _t: int) -> bytes:
        return _sparql_series_json(rows)

    series = enumerate_consolidation_series("32008R0692", _fetch=_fetch)
    assert series == ("02008R0692-20080731", "02008R0692-20140101")


def test_enumerate_consolidation_series_empty_is_honest_empty() -> None:
    """A base with zero published consolidations returns () (no oracle exists —
    the conservation-invariant lane is the correct fallback), not an error."""

    def _fetch(_q: str, _ep: str, _t: int) -> bytes:
        return _sparql_series_json([])

    assert enumerate_consolidation_series("39999R9999", _fetch=_fetch) == ()


def test_enumerate_consolidation_series_fails_loud_on_cellar_500() -> None:
    """A CELLAR JDBC-500 body raises, never a silent empty series."""
    from lawvm.eu.eu_amendment_graph import AmendmentGraphError

    def _fetch(_q: str, _ep: str, _t: int) -> bytes:
        return b"Unable to acquire JDBC Connection"

    with pytest.raises(AmendmentGraphError):
        enumerate_consolidation_series("32008R0692", _fetch=_fetch)


# A synthetic DATED consolidated tree notice exposing one ENG fmx4 manifestation
# whose single item carries a resolvable /DOC_N URL (mirrors the real shape).
_CONS_NOTICE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<NOTICE type="tree">
  <EXPRESSION>
    <EXPRESSION_USES_LANGUAGE><IDENTIFIER>ENG</IDENTIFIER></EXPRESSION_USES_LANGUAGE>
    <URI><VALUE>http://x/expr</VALUE></URI>
    <EXPRESSION_MANIFESTED_BY_MANIFESTATION>
      <URI><VALUE>http://x/man.fmx4</VALUE></URI>
    </EXPRESSION_MANIFESTED_BY_MANIFESTATION>
  </EXPRESSION>
  <MANIFESTATION manifestation-type="fmx4">
    <URI><VALUE>http://x/man.fmx4</VALUE></URI>
    <MANIFESTATION_HAS_ITEM>
      <URI><VALUE>http://x/cellar/uuid.0006.01/DOC_1</VALUE></URI>
    </MANIFESTATION_HAS_ITEM>
  </MANIFESTATION>
</NOTICE>
"""

# A consolidated Formex act (root CONS.ACT, one article) — graftable bytes.
_CONS_ACT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<CONS.ACT><TITLE><TI>Consolidated proof act</TI></TITLE>
<ENACTING.TERMS><ARTICLE><TI.ART>Article 1</TI.ART></ARTICLE></ENACTING.TERMS>
</CONS.ACT>
"""


def test_fetch_consolidation_bytes_resolves_and_unwraps() -> None:
    """The offline consolidated byte lane fetches the dated notice, selects the
    DOC-item URL, and returns bare Formex bytes (here a non-zip item)."""

    def _notice(cons_celex: str, lang: str, _t: int) -> tuple[bytes, dict]:
        assert cons_celex == "02008R0692-20140101"  # dated form, not bare base
        assert lang == "eng"
        return _CONS_NOTICE_XML, {}

    def _item(url: str, _t: int) -> tuple[bytes, dict]:
        assert url.endswith("/DOC_1")
        return _CONS_ACT_XML, {}

    raw = fetch_consolidation_bytes(
        "32008R0692", "2014-01-01", _fetch_notice=_notice, _fetch_item=_item
    )
    assert raw == _CONS_ACT_XML


def test_fetch_consolidation_bytes_no_item_is_typed_failure() -> None:
    """A notice with no resolvable fmx4 item raises a typed acquisition failure
    (never an empty-oracle masquerade)."""
    empty_notice = b'<?xml version="1.0"?><NOTICE type="tree"></NOTICE>'

    def _notice(_c: str, _l: str, _t: int) -> tuple[bytes, dict]:
        return empty_notice, {}

    with pytest.raises(ConsolidationAcquisitionFailure):
        fetch_consolidation_bytes(
            "32008R0692", "2014-01-01", _fetch_notice=_notice
        )
