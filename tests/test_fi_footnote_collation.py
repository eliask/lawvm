"""Tests for ``lawvm.finland.footnote_collation`` (doc3 D4).

Covers the SDOC-08 contract scoped-collation algorithm:
* Single scope with marker + body → linked.
* Marker outside scope's label set → UnboundMarker residual.
* Body with no marker citing it → UnreferencedBody residual.
* Scope-change resets the label namespace (label N is legal in BOTH
  scope A and scope B without colliding).
* Duplicate labels inside one scope → DuplicateLabel residual
  (SDOC-08 violation).
* Pure IR pass — SDOC-11 (no PDF re-extraction at runtime).
* D0 fixture (data/finland/attachment_ir/2002_1248/4484.json) loads and
  the collation pass completes without raising (hot-path smoke).

Operating contract: AGENTS.md §2.9 (synthetic + corpus test per
meaningful change).
"""
from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.footnote_collation import (
    FootnoteLinkage,
    collate_footnotes_by_scope,
)


# ---------------------------------------------------------------------------
# Tree builders
# ---------------------------------------------------------------------------


def _scope_with_marker_and_body(scope_label: str, marker: str, body_text: str) -> IRNode:
    """APPENDIX(scope) → CONTENT(text containing marker) + SCHEDULE → SCHEDULE_ENTRY(label, body_text)."""
    return IRNode(
        kind=IRNodeKind.APPENDIX,
        label=scope_label,
        children=(
            IRNode(
                kind=IRNodeKind.HEADING,
                text=f"Heading: scope {scope_label}",
            ),
            IRNode(
                kind=IRNodeKind.P,
                text=f"Body refers to footnote {marker}) here.",
            ),
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="liite_1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label=marker,
                        text=body_text,
                    ),
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Synthetic tests
# ---------------------------------------------------------------------------


def test_single_scope_marker_links_to_body() -> None:
    tree = _scope_with_marker_and_body("osa_I", "1", "First footnote body")
    result = collate_footnotes_by_scope(tree)
    assert len(result.linkages) == 1
    assert result.linkages[0].scope_label == "osa_I"
    assert result.linkages[0].marker_label == "1"
    assert result.linkages[0].body_label == "1"
    assert "First footnote body" in result.linkages[0].body_text_snippet
    assert result.scopes_seen == 1


def test_marker_outside_label_set_is_unbound() -> None:
    """A marker '5)' with only a body labelled '1' → UnboundMarker."""
    appendix = _scope_with_marker_and_body("osa_I", "1", "Body for #1")
    # appendix has children: [HEADING, P (with "1)"), SCHEDULE]
    # Replace the existing P with one that mentions both '5)' and '1)'.
    p_with_extra = IRNode(
        kind=IRNodeKind.P,
        text="Appendix refers to footnote 5) and 1).",
    )
    appendix = IRNode(
        kind=IRNodeKind.APPENDIX,
        label=appendix.label,
        children=(appendix.children[0], p_with_extra, appendix.children[2]),
    )
    tree = IRNode(kind=IRNodeKind.HCONTAINER, children=(appendix,))
    result = collate_footnotes_by_scope(tree)
    # Marker '5' never matched to body → unbound.
    assert any(m.marker_label == "5" for m in result.unbound_markers)
    # Marker '1' matched.
    assert any(l.marker_label == "1" for l in result.linkages)


def test_body_with_no_marker_is_unreferenced() -> None:
    """SCHEDULE_ENTRY body labelled 9 with no marker citing it."""
    tree = IRNode(
        kind=IRNodeKind.APPENDIX,
        label="osa_I",
        children=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="liite_1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE_ENTRY,
                        label="9",
                        text="Orphan footnote body",
                    ),
                ),
            ),
        ),
    )
    result = collate_footnotes_by_scope(tree)
    assert len(result.unreferenced_bodies) == 1
    assert result.unreferenced_bodies[0].body_label == "9"


def test_scope_change_resets_label_namespace() -> None:
    """Label '1' in scope A AND in scope B is legal (SDOC-08)."""
    tree = IRNode(
        kind=IRNodeKind.HCONTAINER,
        children=(
            _scope_with_marker_and_body("osa_I", "1", "Footnote 1 of part I"),
            _scope_with_marker_and_body("osa_II", "1", "Footnote 1 of part II"),
        ),
    )
    result = collate_footnotes_by_scope(tree)
    assert result.scopes_seen == 2
    assert len(result.linkages) == 2
    scope_labels = {lk.scope_label for lk in result.linkages}
    assert scope_labels == {"osa_I", "osa_II"}
    assert result.duplicate_labels == (), (
        "labels in different scopes are NOT duplicates per SDOC-08"
    )


def test_duplicate_labels_in_one_scope_emit_residual() -> None:
    """Two SCHEDULE_ENTRY bodies with label '1' inside one APPENDIX → DuplicateLabel."""
    tree = IRNode(
        kind=IRNodeKind.APPENDIX,
        label="osa_I",
        children=(
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="liite_1",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label="1", text="First 1"),
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label="1", text="Second 1"),
                ),
            ),
        ),
    )
    result = collate_footnotes_by_scope(tree)
    assert len(result.duplicate_labels) == 1
    dup = result.duplicate_labels[0]
    assert dup.scope_label == "osa_I"
    assert dup.label == "1"
    assert len(dup.body_paths) == 2


def test_bracketed_marker_form_bracket_is_captured() -> None:
    """Marker form ``[1]`` is detected alongside ``1)``."""
    tree = IRNode(
        kind=IRNodeKind.APPENDIX,
        label="osa_I",
        children=(
            IRNode(kind=IRNodeKind.P, text="text [3] continues"),
            IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="liite_1",
                children=(
                    IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label="3", text="Body for #3"),
                ),
            ),
        ),
    )
    result = collate_footnotes_by_scope(tree)
    assert any(l.marker_label == "3" for l in result.linkages)


def test_collation_is_pure_ir_pass_no_pdf_extraction() -> None:
    """SDOC-11 — runtime collation must not import pdfplumber."""
    import builtins
    from unittest.mock import patch

    real_import = builtins.__import__

    def _fail_on_pdfplumber(name, *args, **kwargs):
        if name == "pdfplumber" or name.startswith("pdfplumber."):
            raise AssertionError(
                f"SDOC-11 violation: runtime collation imported {name!r} "
                "(no PDF extraction permitted)"
            )
        return real_import(name, *args, **kwargs)

    tree = _scope_with_marker_and_body("osa_I", "1", "Body")
    with patch("builtins.__import__", side_effect=_fail_on_pdfplumber):
        result = collate_footnotes_by_scope(tree)
    assert result.scopes_seen == 1


def test_collation_carriers_are_typed() -> None:
    """FootnoteLinkage / UnboundMarker / UnreferencedBody / DuplicateLabel are frozen typed carriers."""
    import dataclasses as _dc

    link = FootnoteLinkage(
        scope_label="osa_I",
        marker_label="1",
        body_label="1",
        body_text_snippet="x",
        marker_path=("body[root]", "p[0]"),
        body_path=("body[root]", "schedule[2]", "entry[0]"),
    )
    assert link.scope_label == "osa_I"
    with pytest.raises(_dc.FrozenInstanceError):
        link.scope_label = "osa_II"  # type: ignore[misc]
    assert not hasattr(link, "__dict__")


# ---------------------------------------------------------------------------
# D0 fixture smoke
# ---------------------------------------------------------------------------


def test_collation_completes_on_d0_fixture() -> None:
    """The D0 attachment IR fixture walks without raising; produces typed residuals."""
    from lawvm.finland.ir_serialize import load_attachment_ir

    ir = load_attachment_ir("2002/1248", "4484.pdf")
    assert ir is not None, "D0 fixture must load via the canonical store"
    result = collate_footnotes_by_scope(ir)
    # Pure smoke — the pass completes. The D0 fixture has SCHEDULE_ENTRY
    # nodes within APPENDIX scopes; the pass should at minimum count
    # scopes (it does not crash without bodies or markers).
    assert isinstance(result.scopes_seen, int)
    assert result.scopes_seen >= 0
    # The fixture is real Finnish legislation, not a synthetic test, so
    # we don't assert specific residual counts — the contract is that the
    # pass completes (no exception) and returns a typed result.
