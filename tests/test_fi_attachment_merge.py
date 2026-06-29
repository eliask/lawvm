"""Tests for ``merge_attachments_into_root`` — SDOC-13 unified tree.

Tests:
* Empty supplements → body IR unchanged (no extra wrapping layer).
* One supplement → HCONTAINER root with BODY + APPENDIX children.
* Multiple supplements → ordered APPENDIX siblings, each carrying the
  supplement's IR children.
* The merged tree's pretty-print yields body + attachments in ONE walk
  (single ``format_ir_pretty`` call) — architectural target per
  ``notes_internal/REMAINING_WORK.md`` (the architectural goal of
  attachments-as-siblings-of-BODY under one HCONTAINER root).

Operating contract: AGENTS.md §2.10 (projection plane — a projection is
never the source of truth; it must be re-derivable from a committed
dossier) + §2.9 (synthetic test per meaningful change).
"""
from __future__ import annotations

from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ir_tree_dump import (
    format_unified_statute,
    merge_attachments_into_root,
)


def _body_ir() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                attrs={"eId": "chp_1"},
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Yleiset säännökset"),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={"eId": "sec_1"},
                        children=(
                            IRNode(kind=IRNodeKind.HEADING, text="Soveltamisala"),
                            IRNode(kind=IRNodeKind.CONTENT, text="Tätä asetusta sovelletaan..."),
                        ),
                    ),
                ),
            ),
        ),
    )


def _attachment_ir(label: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.HCONTAINER,
        children=(
            IRNode(
                kind=IRNodeKind.APPENDIX,
                label=label,
                children=(
                    IRNode(kind=IRNodeKind.HEADING, text="Liitteen otsikko"),
                    IRNode(kind=IRNodeKind.CONTENT, text="Liitteen teksti..."),
                ),
            ),
        ),
    )


def _supplement(pdf_name: str, ir: IRNode):
    """Minimal stand-in for AttachmentIRSupplement — duck-typed by name."""
    return SimpleNamespace(
        pdf_name=pdf_name,
        ir=ir,
        pdf_text_length=123,
        source_ref=f"finlex://sd-cons/{pdf_name}",
    )


def test_empty_supplements_returns_body_unchanged() -> None:
    body = _body_ir()
    merged = merge_attachments_into_root(body, ())
    assert merged is body, "empty supplements must not wrap the body"


def test_single_supplement_wraps_under_hcontainer_root() -> None:
    body = _body_ir()
    supp = _supplement("liite_1.pdf", _attachment_ir("liite_1"))
    merged = merge_attachments_into_root(body, (supp,))

    assert merged.kind is IRNodeKind.HCONTAINER
    assert len(merged.children) == 2
    assert merged.children[0] is body or merged.children[0].kind is IRNodeKind.BODY
    appendix = merged.children[1]
    assert appendix.kind is IRNodeKind.APPENDIX
    assert appendix.label == "liite_1"  # .pdf suffix stripped


def test_multiple_supplements_become_ordered_appendix_siblings() -> None:
    body = _body_ir()
    supps = [
        _supplement("liite_1.pdf", _attachment_ir("liite_1")),
        _supplement("liite_2.pdf", _attachment_ir("liite_2")),
    ]
    merged = merge_attachments_into_root(body, supps)

    assert merged.kind is IRNodeKind.HCONTAINER
    # BODY + 2 APPENDIX children
    assert len(merged.children) == 3
    assert merged.children[0].kind is IRNodeKind.BODY
    assert [c.kind for c in merged.children[1:]] == [
        IRNodeKind.APPENDIX,
        IRNodeKind.APPENDIX,
    ]
    labels = [c.label for c in merged.children[1:]]
    assert labels == ["liite_1", "liite_2"]


def test_unified_format_iff_walks_attachments_in_single_pass() -> None:
    """``format_unified_statute`` yields body + attachment content in ONE call.

    This is the architectural goal (SDOC-13 SDOC-13-ready counterpart to
    the body-then-attachments sidecar projection): one tree, one walk.
    """
    body = _body_ir()
    supps = [_supplement("liite_1.pdf", _attachment_ir("liite_1"))]
    out = format_unified_statute(body, supps)

    # Body content is present from the unified walk.
    assert "1 luku" in out
    assert "Soveltamisala" in out
    # Attachment content is present from the SAME walk.
    assert "Liitteen otsikko" in out
    assert "Liitteen teksti" in out


def test_unified_format_vs_separate_format_yield_same_content() -> None:
    """Both projections include body + attachment text — single-walk vs separate."""
    from lawvm.finland.ir_tree_dump import format_statute_with_attachments

    body = _body_ir()
    supps = [_supplement("liite_1.pdf", _attachment_ir("liite_1"))]
    unified = format_unified_statute(body, supps)
    sidecar = format_statute_with_attachments(body, supps)

    # Both projections agree on the body section.
    assert "1 luku" in unified
    assert "1 luku" in sidecar
    # Both include attachment content.
    assert "Liitteen teksti" in unified
    assert "Liitteen teksti" in sidecar


def test_appendix_label_falls_back_to_positional_when_pdf_name_missing() -> None:
    """Supplements without pdf_name get a positional appendix label."""
    body = _body_ir()
    # Supplement without a pdf_name attribute (duck-typed expectations hold via getattr).
    supp = SimpleNamespace(ir=_attachment_ir("x"), pdf_text_length=1, source_ref="x")
    merged = merge_attachments_into_root(body, (supp,))

    appendix = merged.children[1]
    assert appendix.kind is IRNodeKind.APPENDIX
    assert appendix.label == "attachment_1"


def test_merge_passes_node_kind_validator() -> None:
    """The merged HCONTAINER → [BODY, APPENDIX_1, ...] tree obeys the D1 spec.

    SDOC-13 + D1 compliance: the projection plane tree must be
    well-formed under the governed node-kind registry.
    """
    from lawvm.core.node_kind_registry import validate_node

    body = _body_ir()
    supps = [_supplement("liite_1.pdf", _attachment_ir("liite_1"))]
    merged = merge_attachments_into_root(body, supps)
    violations = validate_node(merged)
    assert not [v for v in violations if v.kind == "unknown_kind"], (
        f"unknown IRNodeKind in merged tree: {violations}"
    )
