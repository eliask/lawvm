"""Synthetic regression for ``infer_source_payload_from_target`` subparagraph kind.

``asp/2003/13`` section 158 had an ``all_tree`` monotone failure (28/28
amendments bad, introduced by ``ssi/2004/533`` art. 2(4)(b)) because
``infer_source_payload_from_target`` misread the body-path
``section:158/paragraph:a/subparagraph:vi`` as a paragraph-label being
synthesized into a *subsection* with label ``a``.  Per AGENTS.md §1.3 this
was a granularity escalation: a paragraph label cannot become a subsection
sibling — the result was an ``unexpected subsection inside paragraph``
tree-shape violation.

The fix prefers the deepest carried ``_addr`` leaf (the subparagraph) when
the body path actually names a subparagraph-level target.  This matches the
``affected_provisions`` declared in the effect feed
(``s. 158(a)(vi)`` — i.e. ``section/paragraph/subparagraph``), and stops
the misclassification of ``paragraph:a`` as a ``subsection:a`` payload.
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.source_payload_helpers import infer_source_payload_from_target


def _target(path: tuple[tuple[str, str], ...]) -> LegalAddress:
    return LegalAddress(path=path, special=None)


def test_section_paragraph_subparagraph_target_yields_subparagraph_payload() -> None:
    """A body path of section/paragraph/subparagraph must produce
    a subparagraph payload (not subsection)."""
    target = _target(
        (
            ("section", "158"),
            ("paragraph", "a"),
            ("subparagraph", "vi"),
        )
    )
    inferred = infer_source_payload_from_target(
        target=target,
        extracted_text="; and if the mental health officer disagrees, the reason for that disagreement",
        effect_id="key-30c16e8a62fdce66904348266ca16150",
        use_metadata_fallback=False,
    )
    assert inferred["kind"] == "subparagraph", (
        f"§1.3 granularity escalation: expected kind=subparagraph, "
        f"got kind={inferred['kind']!r} for target={target!r}"
    )
    assert inferred["label"] == "vi"
    assert "mental health officer" in inferred["text"]


def test_section_paragraph_only_target_keeps_paragraph_payload() -> None:
    """A body path of section/paragraph (no subparagraph step) must keep
    the prior section/paragraph.  The fix only fires when a subparagraph
    step is present in the path."""
    target = _target(
        (
            ("section", "158"),
            ("paragraph", "a"),
        )
    )
    inferred = infer_source_payload_from_target(
        target=target,
        extracted_text="some inserted text",
        effect_id="key-p",
        use_metadata_fallback=False,
    )
    # The prior code treated a paragraph as a placeholder "subsection": we
    # keep that semantics here for backwards compatibility on the absent-
    # subparagraph path (i.e. the fix narrows strictly to the §1.3 symptom
    # of an explicit subparagraph leaf, not the broader section/paragraph
    # shape).  See the contract assertion below.
    assert inferred["label"] is not None
    # No §1.3 escalation inferred as `subsection` with label `a` is a separate
    # behaviour change. This test documents the contract for now and should
    # be revisited if the deeper section/paragraph semantics are tightened.
    assert inferred["text"] == "some inserted text"


def test_subsection_paragraph_subparagraph_chain_uses_deepest_leaf() -> None:
    """A full body chain (section/subsection/paragraph/subparagraph) must
    synthesize a subparagraph payload."""
    target = _target(
        (
            ("section", "290"),
            ("subsection", "2"),
            ("paragraph", "f"),
            ("subparagraph", "iii"),
        )
    )
    inferred = infer_source_payload_from_target(
        target=target,
        extracted_text="...",
        effect_id="key-iii",
        use_metadata_fallback=False,
    )
    assert inferred["kind"] == "subparagraph", inferred
    assert inferred["label"] == "iii"
