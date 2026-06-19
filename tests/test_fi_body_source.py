"""Reference-extraction body selection: contentAbsent oracle falls back to source.

Finlex serves an empty consolidated body (``<hcontainer name="contentAbsent"/>``)
for repealed/expired statutes. The reference-extraction read path must detect that
stub and fall back to the enacted source XML so the statute's references survive;
an active statute (oracle has real content) must be returned unchanged.
"""

from __future__ import annotations

from lawvm.finland.legal_surface.body_source import (
    has_consolidated_text_state,
    is_content_absent_body,
    read_reference_body,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _content_absent_oracle() -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}"><act>'
        "<preface><p><docTitle>Stub</docTitle></p></preface>"
        '<body><hcontainer name="contentAbsent"/></body>'
        "</act></akomaNtoso>"
    ).encode("utf-8")


def _real_body(marker: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}"><act><body>'
        f"<section><num>1 §</num><p>{marker}</p></section>"
        "</body></act></akomaNtoso>"
    ).encode("utf-8")


class _FakeStore:
    def __init__(
        self,
        oracle: dict[str, bytes],
        source: dict[str, bytes],
        amendment: dict[str, bytes] | None = None,
    ) -> None:
        self._oracle = oracle
        self._source = source
        self._amendment = amendment or {}

    def read_oracle(self, sid: str) -> bytes | None:
        return self._oracle.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return self._source.get(sid)

    def read_amendment(self, sid: str) -> bytes | None:
        return self._amendment.get(sid)


def test_is_content_absent_body_detects_stub() -> None:
    assert is_content_absent_body(_content_absent_oracle()) is True


def test_is_content_absent_body_rejects_real_body() -> None:
    assert is_content_absent_body(_real_body("x")) is False


def test_is_content_absent_body_empty_bytes() -> None:
    assert is_content_absent_body(b"") is True


def test_is_content_absent_body_componentref_pdf_stub() -> None:
    pdf_stub = (
        f'<akomaNtoso xmlns="{_AKN}"><act><body>'
        '<componentRef src="x.pdf"/>'
        "</body></act></akomaNtoso>"
    ).encode("utf-8")
    assert is_content_absent_body(pdf_stub) is True


def test_is_content_absent_body_unparseable_is_not_absent() -> None:
    # Malformed bytes are left for the caller's parse path to handle, not
    # silently discarded as "absent".
    assert is_content_absent_body(b"<not valid xml") is False


def test_contentabsent_oracle_falls_back_to_source() -> None:
    sid = "2013/872"
    store = _FakeStore(
        oracle={sid: _content_absent_oracle()},
        source={sid: _real_body("SOURCE")},
    )
    body = read_reference_body(store, sid)
    assert body == _real_body("SOURCE")


def test_active_oracle_is_returned_unchanged() -> None:
    sid = "731/1999"
    oracle = _real_body("ORACLE")
    store = _FakeStore(
        oracle={sid: oracle},
        # A different source must NOT be read when the oracle is real.
        source={sid: _real_body("SOURCE")},
    )
    assert read_reference_body(store, sid) == oracle


def test_absent_oracle_no_source_falls_through_to_amendment() -> None:
    sid = "1/2000"
    store = _FakeStore(
        oracle={sid: _content_absent_oracle()},
        source={},
        amendment={sid: _real_body("AMEND")},
    )
    assert read_reference_body(store, sid) == _real_body("AMEND")


# ---------------------------------------------------------------------------
# has_consolidated_text_state — the broken-refs citer-scope predicate
# ---------------------------------------------------------------------------


def test_consolidated_text_state_true_for_real_oracle() -> None:
    sid = "731/1999"
    store = _FakeStore(oracle={sid: _real_body("ORACLE")}, source={})
    assert has_consolidated_text_state(store, sid) is True


def test_consolidated_text_state_false_for_contentabsent_oracle() -> None:
    # A repealed/expired statute serving a contentAbsent stub has no in-force
    # text-state to scope the broken-refs check to.
    sid = "2013/872"
    store = _FakeStore(
        oracle={sid: _content_absent_oracle()},
        source={sid: _real_body("SOURCE")},
    )
    assert has_consolidated_text_state(store, sid) is False


def test_consolidated_text_state_false_for_amendment_act_no_oracle() -> None:
    # An amendment act ("Laki ... muuttamisesta") has no consolidated oracle of
    # its own; its only body is the amended-law-relative payload (source). Out of
    # scope — this is the characterized false-positive class.
    sid = "1953/427"
    store = _FakeStore(oracle={}, source={sid: _real_body("AMEND-PAYLOAD")})
    assert has_consolidated_text_state(store, sid) is False
