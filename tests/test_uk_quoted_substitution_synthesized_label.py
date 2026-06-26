"""Synthetic regression for the consolidated-XML quoted-substitution sibling family.

A consolidated UK affecting-act XML (``/data.xml`` rather than
``/enacted/data.xml``) sometimes unwraps the ``<BlockAmendment>`` wrappers
around quoted substitution bodies, lifting the quoted ``<P2>`` as a direct
sibling of the real numbered subsections inside the inserted section payload.
The quoted P2 carries a curly-quote ``<Pnumber>`` (no alphanumerics), so
``_parse_p2`` records it as a structural subsection with an empty/quote-only
label.  The duplicate-label disambiguator was then renaming it ``\u201c-1`` /
``\u201c-2`` which the shared core label normalizer (``_norm``) strips to
``1`` / ``2`` -- colliding with the real numbered subsections of the same
parent and firing a monotone ``label_normalization_collision`` violation
(e.g. ``ukpga/1990/8`` section 322B introduced by ``ukpga/1999/29`` s.345).

The fix in ``_disambiguate_duplicate_labels`` synthesizes an ``n{N}`` suffix
(matching the consolidated oracle EID convention ``section-322B-n1``) when the
canonical sibling label has no alphanumerics, preventing the strip-to-``1``
collision and recovering the oracle EIDs.  This test exercises the same
structural-payload parser path (``_parse_structural_payload_element``) that
effect lowering uses; the corpus regression is
``test_uk_misses.py::test_uk_misses_ukpga_1990_8_section_322b_no_duplicate_label``.
"""
from __future__ import annotations

from lxml import etree as ET

from lawvm.uk_legislation.uk_grafter import _LEG_NS
from lawvm.uk_legislation.effect_payload_normalization import (
    _parse_structural_payload_element,
)


def _build_payload_xml() -> str:
    """Build just the inserted-section P1 payload (matches actual_el shape)."""
    quote = "\u201c"  # left double quotation mark
    dash = "\u2014"  # em dash
    return (
        f'<P1 xmlns="{_LEG_NS}" id="section-322b">'
        "<Pnumber>322B</Pnumber><P1para>"
        f'<P2 id="section-322b-1"><Pnumber>1</Pnumber><P2para><Text>This section applies where{dash}</Text></P2para></P2>'
        f'<P2 id="section-322b-5"><Pnumber>5</Pnumber><P2para><Text>The subsection referred to in subsection (2)(a) above is as follows{dash}</Text></P2para></P2>'
        f'<P2 id="section-322b-n1"><Pnumber PuncBefore="" PuncAfter="( )">{quote}</Pnumber>'
        f'<P2para><Text>Where this subsection applies to an inquiry, the costs incurred shall be paid{dash}</Text>'
        '<P3><Pnumber>a</Pnumber><P3para><Text>by the Mayor;</Text></P3para></P3>'
        '<P3><Pnumber>b</Pnumber><P3para><Text>by such local authority.</Text></P3para></P3>'
        "</P2para></P2>"
        f'<P2 id="section-322b-6"><Pnumber>6</Pnumber><P2para><Text>The subsection referred to in subsection (2)(b) above is as follows{dash}</Text></P2para></P2>'
        f'<P2 id="section-322b-n2"><Pnumber PuncBefore="" PuncAfter="( )">{quote}</Pnumber>'
        f'<P2para><Text>Where this subsection applies to an inquiry, the Secretary of State may make orders{dash}</Text>'
        '<P3><Pnumber>a</Pnumber><P3para><Text>by the Mayor;</Text></P3para></P3>'
        '<P3><Pnumber>b</Pnumber><P3para><Text>by the local authority.</Text></P3para></P3>'
        "</P2para></P2>"
        '<P2 id="section-322b-7"><Pnumber>7</Pnumber><P2para><Text>In this section the 1972 Act means the Local Government Act 1972.</Text></P2para></P2>'
        "</P1para></P1>"
    )


def _parse_section_322b() -> dict:
    """Parse the inserted-section payload via the same path effect lowering uses."""
    el = ET.fromstring(_build_payload_xml())
    content_ir = _parse_structural_payload_element(el, parse_context="")
    assert content_ir is not None, "structural payload parser returned None"
    return content_ir


def test_quoted_substitution_sibling_synthesizes_n_label() -> None:
    """Two quote-only P2 siblings must be labelled ``n1`` / ``n2``."""
    section = _parse_section_322b()
    subsections = [c for c in section.get("children", []) if c.get("kind", "").endswith("subsection")]
    labels = [c.get("label") for c in subsections]
    # Real numbered subsections 1, 5, 6, 7 must survive unchanged.
    assert "1" in labels
    assert "5" in labels
    assert "6" in labels
    assert "7" in labels
    # The two quote-only P2 siblings must NOT carry a curly-quote-prefixed
    # ``-1`` / ``-2`` style label (which the shared core normalizer would
    # strip to ``1`` / ``2``).
    assert "\u201c-1" not in labels
    assert "\u201c-2" not in labels
    assert "n1" in labels, f"expected synthesized 'n1' label, got: {labels}"
    assert "n2" in labels, f"expected synthesized 'n2' label, got: {labels}"


def test_quoted_substitution_sibling_carries_source_rule_id() -> None:
    """The synthesized ``n{N}`` sibling must carry an owned rule id."""
    section = _parse_section_322b()
    n_siblings = [
        c for c in section.get("children", []) if c.get("label") in ("n1", "n2")
    ]
    assert len(n_siblings) == 2, [c.get("label") for c in section.get("children", [])]
    for sibling in n_siblings:
        attrs = sibling.get("attrs", {}) or {}
        rule_ids = [v for k, v in attrs.items() if k == "source_rule_id"]
        assert any(
            r == "uk_quoted_substitution_payload_sibling_synthesized_label"
            for r in rule_ids
        ), attrs


def test_quoted_substitution_sibling_no_normalized_collision() -> None:
    """The synthesized labels must NOT normalize to a label shared with the
    real numbered subsections of the same parent."""
    from lawvm.core.tree_ops import normalized_label_key

    section = _parse_section_322b()
    subsections = [c for c in section.get("children", []) if c.get("kind", "").endswith("subsection")]
    normalized = [normalized_label_key(c.get("label") or "") for c in subsections]
    # ``n1`` / ``n2`` must not collide with ``1`` / ``5`` / ``6`` / ``7``.
    assert normalized.count("1") <= 1, normalized
    assert normalized.count("2") <= 1, normalized
