"""``lawvm fi-parse-compare`` — full-doc PDF->IR vs authoritative XML.

Hermetic tests pin the reworked tool WITHOUT a vision/LLM server:
  * full-doc text extraction on both witnesses (XML mainBody itertext; IR tree);
  * de-hyphenation applied symmetrically to both sides before comparison;
  * the categorized-diff parsing (MISSING/EXTRA/OCR/NUMERIC/STRUCTURE + verdict);
  * the repetition-guard payload fields + the pathological-loop detection;
  * full-doc-vs-full-doc coverage (not the old few-pages-vs-whole-doc "recall").

One live test runs the real :8080 backend end to end, skipped when unavailable.
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.tools.fi_parse_compare import (
    AdjudicatedDiff,
    XmlPdfDiffAdjudicator,
    _coverage,
    _ir_dict_text,
    _repetition_ratio,
    _source_node_text,
    _words,
    parse_categorized_diff,
    xml_body_text,
)

_DIGEST = "a" * 64


def _anchor(loc: str = "page=1;block=1") -> SourceAnchor:
    return SourceAnchor(artifact_digest=_DIGEST, locator=loc, page_num=1)


# --------------------------------------------------------------------------- #
# Full-doc text extraction                                                     #
# --------------------------------------------------------------------------- #


def test_xml_body_text_extracts_mainbody_itertext() -> None:
    xml = (
        b"<akomaNtoso xmlns='http://x'>"
        b"<preface>SKIP HEADER</preface>"
        b"<mainBody><section><num>1 \xc2\xa7</num><p>Laki muuttamisesta.</p></section>"
        b"<section><p>Toinen pyk\xc3\xa4l\xc3\xa4.</p></section></mainBody>"
        b"</akomaNtoso>"
    )
    txt = xml_body_text(xml)
    assert "Laki muuttamisesta." in txt
    assert "Toinen" in txt
    # Preface (outside mainBody) is not part of the authoritative body text.
    assert "SKIP HEADER" not in txt


def test_xml_body_text_falls_back_to_body_then_root() -> None:
    # No mainBody, but a <body>.
    xml = b"<doc><body><p>Vain runko.</p></body></doc>"
    assert "Vain runko." in xml_body_text(xml)
    # No mainBody and no body: whole document.
    xml2 = b"<doc><p>Koko dokumentti.</p></doc>"
    assert "Koko dokumentti." in xml_body_text(xml2)


def test_ir_dict_text_walks_children_depth_first() -> None:
    ir = {
        "text": "root",
        "children": [
            {"text": "child-a", "children": [{"text": "grandchild"}]},
            {"text": "", "children": [{"text": "child-b"}]},
        ],
    }
    txt = _ir_dict_text(ir)
    assert txt.split("\n") == ["root", "child-a", "grandchild", "child-b"]


def test_source_node_text_matches_ir_dict_text() -> None:
    tree = SourceDocumentNode(
        kind=SourceDocumentNodeKind.BODY,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=_anchor(),
        text="root",
        children=(
            SourceDocumentNode(
                kind=SourceDocumentNodeKind.PARAGRAPH,
                assurance_tier=AssuranceTier.SINGLE_WITNESS,
                anchor=_anchor(),
                text="leaf",
            ),
        ),
    )
    assert _source_node_text(tree) == "root\nleaf"


# --------------------------------------------------------------------------- #
# De-hyphenation applied symmetrically                                         #
# --------------------------------------------------------------------------- #


def test_dehyphenation_applied_symmetrically_both_sides() -> None:
    # Soft hyphen (U+00AD) at a line break — a display artifact, not a word split.
    hyphenated = "kriisinrat­\nkaisusta"
    clean = "kriisinratkaisusta"
    # Both witnesses collapse to the SAME word set regardless of soft-hyphenation.
    assert _words(hyphenated) == _words(clean) == frozenset({"kriisinratkaisusta"})


def test_coverage_is_full_doc_symmetric_not_few_pages_recall() -> None:
    authoritative = _words("Laki tulee voimaan 1 pana tammikuuta 2016")
    # A faithful full-doc reconstruction covers every authoritative word.
    faithful = _words("Laki tulee voimaan 1 pana tammikuuta 2016 (sivu 1)")
    assert _coverage(faithful, authoritative) == 1.0
    # A reconstruction missing half the words scores ~0.5 — honest, not 0.0.
    partial = _words("Laki tulee voimaan")
    assert 0.4 < _coverage(partial, authoritative) < 0.6


# --------------------------------------------------------------------------- #
# Categorized-diff parsing                                                     #
# --------------------------------------------------------------------------- #


def test_parse_categorized_diff_buckets_by_label_and_verdict() -> None:
    reply = (
        "NUMERIC: 2016 in A vs 2015 in B (voimaantulopaiva)\n"
        "NUMERIC: 5 000 euroa in A vs 6 000 euroa in B\n"
        "MISSING: the entire perustelut section is absent from B\n"
        "OCR: 'sellaisiin' read as 'se11aisiin'\n"
        "Some stray commentary line the model added\n"
        "VERDICT: material-issues — a year and a euro amount differ"
    )
    cats, verdict = parse_categorized_diff(reply)
    assert cats["NUMERIC"] == (
        "2016 in A vs 2015 in B (voimaantulopaiva)",
        "5 000 euroa in A vs 6 000 euroa in B",
    )
    assert cats["MISSING"] == ("the entire perustelut section is absent from B",)
    assert cats["OCR"] == ("'sellaisiin' read as 'se11aisiin'",)
    assert "EXTRA" not in cats and "STRUCTURE" not in cats  # empty categories skipped
    assert verdict.startswith("material-issues")


def test_parse_categorized_diff_empty_when_no_labels() -> None:
    cats, verdict = parse_categorized_diff("The documents look identical to me.")
    assert cats == {}
    assert verdict == ""


# --------------------------------------------------------------------------- #
# Repetition guard                                                             #
# --------------------------------------------------------------------------- #


def test_repetition_ratio_flags_pathological_loop() -> None:
    loop = "\n".join(["No, TEXT B says the formula is x = a + b"] * 30)
    assert _repetition_ratio(loop) > 0.9
    healthy = (
        "NUMERIC: 2016 vs 2015\nMISSING: perustelut section\n"
        "OCR: sellaisiin\nVERDICT: minor-issues — one OCR slip"
    )
    assert _repetition_ratio(healthy) == 0.0


class _ScriptedDiffAdjudicator(XmlPdfDiffAdjudicator):
    """XmlPdfDiffAdjudicator with a scripted ``_chat`` — no network."""

    def __init__(self, response: str, *, finish_reason: str = "stop") -> None:
        super().__init__()
        self._response = response
        self._scripted_finish = finish_reason
        self.captured_payload: dict | None = None

    def _chat(self, system: str, user: str) -> str:  # type: ignore[override]
        # Exercise the real payload builder so the guard fields are asserted.
        self.captured_payload = self._payload(system, user)
        self._last_finish_reason = self._scripted_finish
        return self._response


def test_payload_carries_repetition_guard_fields() -> None:
    adj = _ScriptedDiffAdjudicator("VERDICT: faithful — ok")
    adj.adjudicate("A text", "B text")
    payload = adj.captured_payload
    assert payload is not None
    assert payload["repeat_penalty"] == pytest.approx(1.1)
    assert payload["presence_penalty"] == pytest.approx(0.5)
    assert payload["max_tokens"] == 4000
    assert payload["temperature"] == 0.0
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_adjudicate_parses_healthy_diff() -> None:
    reply = (
        "NUMERIC: 2016 vs 2015\n"
        "MISSING: perustelut\n"
        "VERDICT: material-issues — year differs"
    )
    adj = _ScriptedDiffAdjudicator(reply)
    diff = adj.adjudicate("A", "B")
    assert isinstance(diff, AdjudicatedDiff)
    assert not diff.pathological_repetition
    assert diff.categorized["NUMERIC"] == ("2016 vs 2015",)
    assert diff.verdict.startswith("material-issues")
    assert diff.finish_reason == "stop"


def test_adjudicate_withholds_pathological_repetition_loop() -> None:
    loop = "\n".join(["No, TEXT B says the formula is x = a + b"] * 30)
    adj = _ScriptedDiffAdjudicator(loop)
    diff = adj.adjudicate("A", "B")
    assert diff.pathological_repetition
    assert diff.categorized == {}
    assert "repetition" in diff.verdict.lower()
    # The raw garbage is retained for inspection but not presented as a diff.
    assert diff.raw == loop


# --------------------------------------------------------------------------- #
# Live backend (skipped when unavailable, like the vision live tests)          #
# --------------------------------------------------------------------------- #


@pytest.mark.network
def test_live_adjudicator_end_to_end() -> None:
    if "LAWVM_CANONICAL_DATA_ROOT" not in os.environ:
        pytest.skip("no canonical data root")
    adj = XmlPdfDiffAdjudicator()
    if not adj.is_available():
        pytest.skip("LLM backend at :8080 unavailable")
    diff = adj.adjudicate(
        "Laki tulee voimaan 1 paivana tammikuuta 2016.",
        "Laki tulee voimaan 1 paivana tammikuuta 2015.",
    )
    assert isinstance(diff, AdjudicatedDiff)
    # A real model should catch the year discrepancy or at least return a verdict.
    assert diff.verdict or diff.categorized
