"""Adversarial security tests for the hardened lxml parser config.

Three test groups:

Group A — billion-laughs / quadratic-entity-expansion rejection:
    Synthetic internal-entity-expansion payloads that, under default lxml
    (``resolve_entities=True``), would expand to gigabytes of text.  The
    hardened config (``resolve_entities=False``) must reject the entity
    definitions rather than expand them: parse either raises a typed lxml
    error (``XMLSyntaxError`` raised by lxml's amplification-factor detector
    before expansion reaches catastrophic size) or completes within a tight
    memory budget (no >10MB growth under ``tracemalloc``).

Group B — external DTD / network safety:
    Synthetic DOCTYPE referencing an external DTD at a sentinel URL.  The
    parse must not perform any network fetch (``no_network=True``);
    ``load_dtd=False`` ensures the external DTD is not loaded even if a
    fetch were possible.  A socket-level monkeypatch guards against any
    accidental future regression.

Group C — positive / regressions:
    A small valid XML document parses to the expected tree, and the
    ``recover=True`` mode tolerates a truncated-but-recoverable document
    without losing element content.

Reference: AGENTS.md §1.10 (fail loud, never silent-fallback); §2.6 (rule of
three — 30 sites shared the same hardened-config shape before this helper).
"""
from __future__ import annotations

import tracemalloc
from typing import Any

import pytest
from lxml import etree

from lawvm.core.xml_parse import parse_corpus_xml


# ---------------------------------------------------------------------------
# Group A — billion-laughs / quadratic entity expansion rejection
# ---------------------------------------------------------------------------


# Classic billion-laughs payload.  Under default lxml (``resolve_entities=True``
# without an amplification cap), ``&lol9;`` expands to 9^9 = ~387M chars.
# lxml ships its own amplification-factor cap (≈10MB) that fires before
# expansion reaches catastrophic sizes, but layers matter: the hardened
# ``resolve_entities=False`` config is the SECOND layer rather than the first.
# Both layers together provide defence in depth — either lxml's amplification
# detector raises ``XMLSyntaxError`` first, or ``resolve_entities=False`` keeps
# the entity reference unresolved.  The invariant tested below is: NO expansion
# of any magnitude ever reaches the text plane through ``parse_corpus_xml``.
_BILLION_LAUGHS_XML = b"""\
<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
 <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
 <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
 <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
 <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
<lolz>&lol9;</lolz>
"""


# Acceptable exception classes when an entity-based payload is rejected.
# ``XMLSyntaxError`` is the primary raise (subclass of ``ParseError``);
# ``ParseError`` / ``SerialisationError`` are defensive covers for cross-version
# lxml behaviour on entity-reference payloads.
_ENTITY_REJECTION_EXCS = (
    etree.XMLSyntaxError,
    etree.ParseError,
    etree.SerialisationError,
)


class TestBillionLaughsRejection:
    """The hardened config must not expand internal entities."""

    def test_parse_corpus_xml_rejects_billion_laughs_under_memory_cap(self) -> None:
        """The billion-laughs payload must not expand to >10MB of memory.

        Under ``resolve_entities=False`` lxml never substitutes the entity
        references; an ``XMLSyntaxError`` is raised by the amplification-cap
        detector before expansion (or by the unresolved-reference check).
        Either outcome is acceptable — the invariant is that no expansion
        ever runs.

        The ``tracemalloc`` binding is the secondary guard: even if a future
        lxml version returned a partial tree instead of raising, the
        memory-budget ceiling would catch the expansion directly.
        """
        tracemalloc.start()
        try:
            with pytest.raises(_ENTITY_REJECTION_EXCS):
                parse_corpus_xml(_BILLION_LAUGHS_XML)
        finally:
            snapshot_before = tracemalloc.take_snapshot()
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        # Even though lxml raised (the expected path), verify the traced
        # memory budget was respected — this catches a future partial-
        # expansion regression where lxml expands before erroring.
        # 10MB ceiling — the payload would expand to ~387MB under default lxml
        # without the amplification factor cap.
        assert peak < 10 * 1024 * 1024, (
            f"parse_corpus_xml expanded internal entities past the 10MB memory "
            f"budget: peak={peak} bytes, current={current}; the hardened config "
            "must set resolve_entities=False so the expansion step never runs."
        )

    def test_billion_laughs_does_not_produce_lol9_text_in_output(self) -> None:
        """If parse_corpus_xml ever returns a tree instead of raising, the
        entity-expanded text must not appear in the serialized output.

        Defence in depth against a future lxml regression that silently
        swallows the entity-ref error and returns a partial tree — the
        payload's whole point is to never reach the text plane.
        """
        raised = False
        tree = None
        try:
            tree = parse_corpus_xml(_BILLION_LAUGHS_XML)
        except _ENTITY_REJECTION_EXCS:
            raised = True

        if not raised and tree is not None:
            serialized = etree.tostring(tree, encoding="unicode")
            # Even one expanded ``lol2`` (9 ``lol`` concatenations) would be
            # suspicious — the hardened config forbids ALL expansion.
            assert "lol" * 9 not in serialized, (
                "parse_corpus_xml returned a tree containing expanded billion-"
                "laughs entities; resolve_entities=False must reject the "
                "entity definitions rather than expand them."
            )


# ---------------------------------------------------------------------------
# Group B — external DTD / network safety
# ---------------------------------------------------------------------------


# A DOCTYPE that references an external DTD at a sentinel URL.  ``no_network=True``
# already prevents the fetch, and ``load_dtd=False`` ensures the external DTD
# is not loaded even if a fetch were possible.  The hardened config combines
# both defences so a regression in either layer does not expose the system.
_EXTERNAL_DTD_XML = (
    b'<?xml version="1.0"?>'
    b'<!DOCTYPE root SYSTEM "http://lawvm-test-sentinel.invalid/example.dtd">'
    b"<root><child>text</child></root>"
)


class _NetworkCallSentinel(Exception):
    """Raised if any socket-level network call is attempted during a parse."""


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Block every socket-level network call for the duration of the test.

    lxml's ``no_network=True`` already disables its own fetches; this fixture
    is the secondary guard that catches a future regression where lxml (or a
    transitive dependency) routes a fetch through the stdlib socket layer.
    The sentinel domain uses the ``.invalid`` TLD (RFC 6761) so even a
    real fetch would fail rather than hit a live host.
    """

    def _fail(*_args: Any, **_kwargs: Any) -> Any:
        raise _NetworkCallSentinel(
            "parse_corpus_xml triggered a socket-level network call despite "
            "the hardened no_network=True / load_dtd=False config."
        )

    # Patch the high-level APIs first (these are the most likely vectors).
    monkeypatch.setattr("urllib.request.urlopen", _fail, raising=False)
    monkeypatch.setattr("http.client.HTTPConnection", _fail, raising=False)
    monkeypatch.setattr("http.client.HTTPSConnection", _fail, raising=False)
    # Patch the low-level socket constructors too — the belt-and-braces layer.
    monkeypatch.setattr("socket.socket", _fail, raising=False)
    monkeypatch.setattr("socket.create_connection", _fail, raising=False)
    return _fail


class TestExternalDtdNetworkSafety:
    """``parse_corpus_xml`` must never fetch an external DTD or otherwise hit the network."""

    def test_parse_corpus_xml_rejects_external_dtd_network(self, block_network: Any) -> None:
        """The external-DTD payload must parse without any network fetch.

        With ``load_dtd=False`` + ``dtd_validation=False`` + ``no_network=True``,
        lxml parses the document tree without resolving the SYSTEM DTD URL —
        the parse completes (or fails on a separate well-formedness issue) but
        never makes a network call.  This test fails loudly if a networking
        sentinel is hit.
        """
        # The parse may complete or raise, but it must not trigger the
        # network sentinel.  Wrap in a softer try/except so the assertion
        # about the sentinel is the load-bearing check (not the parse outcome).
        try:
            tree = parse_corpus_xml(_EXTERNAL_DTD_XML)
        except _NetworkCallSentinel as exc:
            pytest.fail(str(exc))
        except etree.XMLSyntaxError:
            # Acceptable: the parse may fail because the external DTD is
            # referenced but not loaded — the import-success path is also
            # valid, since load_dtd=False means the DTD is ignored.
            tree = None

        if tree is not None:
            # If the parse succeeded, the document must be the expected tree —
            # the DOCTYPE must not have injected any element or attribute.
            assert tree.tag == "root"
            child = tree.find("child")
            assert child is not None
            assert (child.text or "") == "text"

    def test_parse_corpus_xml_does_not_load_external_dtd_entities(self, block_network: Any) -> None:
        """A document that defines an entity via an external DTD must not
        resolve it — ``resolve_entities=False`` closes the external-entity
        exfiltration vector (the classic XXE read-probe)."""
        xxe_xml = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE root ['
            b'  <!ENTITY xxe SYSTEM "http://lawvm-test-sentinel.invalid/secret.txt">'
            b']>'
            b'<root>&xxe;</root>'
        )
        raised = False
        try:
            tree = parse_corpus_xml(xxe_xml)
        except _NetworkCallSentinel as exc:
            pytest.fail(str(exc))
        except _ENTITY_REJECTION_EXCS:
            raised = True
            tree = None

        if not raised and tree is not None:
            # The entity reference must not have been resolved to file
            # contents — the &xxe; reference should either have errored or
            # been left as an unresolved entity-reference node.
            serialized = etree.tostring(tree, encoding="unicode")
            assert "secret.txt" not in serialized, (
                "parse_corpus_xml resolved an external entity despite "
                "resolve_entities=False; the XXE exfiltration vector is open."
            )


# ---------------------------------------------------------------------------
# Group C — positive / regressions
# ---------------------------------------------------------------------------


class TestValidXmlParsesCorrectly:
    """Positive: the hardened config must still parse good XML correctly."""

    def test_small_valid_xml_snippet_parses_to_expected_tree(self) -> None:
        xml = b'<?xml version="1.0"?><root><child a="1">text</child></root>'
        tree = parse_corpus_xml(xml)
        assert tree.tag == "root"
        child = tree.find("child")
        assert child is not None
        assert child.get("a") == "1"
        assert (child.text or "") == "text"

    def test_unicode_content_round_trips(self) -> None:
        # AKN/Sami/Finnish content lives in corpus XML — verify the hardened
        # config does not mangle non-ASCII characters.
        xml = "<root><teksti>åÄön\N{LATIN SMALL LETTER A WITH MACRON}</teksti></root>".encode(
            "utf-8"
        )
        tree = parse_corpus_xml(xml)
        assert tree.tag == "root"
        text_el = tree.find("teksti")
        assert text_el is not None
        assert (text_el.text or "") == "åÄön\N{LATIN SMALL LETTER A WITH MACRON}"

    def test_recover_true_tolerates_truncated_xml(self) -> None:
        # ``recover=True`` is the Norway Lovdata / corrigendum-fragment path
        # for known-broken sources.  Verify it tolerates a truncated tail and
        # keeps the well-formed prefix.
        xml = b'<root><a>1</a><b>2</b><c>3'  # truncated — no closing </c> or </root>
        tree = parse_corpus_xml(xml, recover=True)
        assert tree.tag == "root"
        a = tree.find("a")
        assert a is not None
        assert (a.text or "") == "1"
        b = tree.find("b")
        assert b is not None
        assert (b.text or "") == "2"

    def test_default_recover_false_raises_on_malformed_xml(self) -> None:
        # AGENTS §1.10 — fail loud.  The default (``recover=False``) must
        # raise ``XMLSyntaxError`` on malformed input rather than silently
        # returning a partial tree.
        xml = b'<root><a>1</a>'  # unclosed <root>
        with pytest.raises(etree.XMLSyntaxError):
            parse_corpus_xml(xml)

    def test_does_not_resolve_internal_entity_in_default_mode(self) -> None:
        # An inline internal entity declaration should not be expanded under
        # the hardened config.  lxml either raises or leaves the reference
        # visible — it must NOT substitute the entity's replacement text.
        xml = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE root [ <!ENTITY greeting "Hello world"> ]>'
            b'<root>&greeting;</root>'
        )
        raised = False
        try:
            tree = parse_corpus_xml(xml)
        except _ENTITY_REJECTION_EXCS:
            raised = True
            tree = None

        if not raised and tree is not None:
            serialized = etree.tostring(tree, encoding="unicode")
            assert "Hello world" not in serialized, (
                "parse_corpus_xml expanded an internal entity; the hardened "
                "config must set resolve_entities=False so entity expansion "
                "never runs."
            )
