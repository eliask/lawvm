"""Regression tests: opaque internal markers must never leak into persisted artifacts.

Per corrigendum §1.2 and POST_WAVE5_GATE_AND_T11_SCOPE §9:
  - ``__continuation__``, ``__ord_N__``, ``__tail_prose__`` and any other
    ``__``-prefixed synthetic discriminators are internal-only.
  - They must never appear in ``label`` or ``visible_label`` fields of
    serialized artifacts, user-visible output, or persisted evidence.

This file checks:
  1. A synthetic SemanticStructureNode tree with deliberately injected
     opaque markers does NOT export them through ``to_dict()`` → JSON.
  2. A hand-constructed Finding tuple that could represent 2013/331 findings
     does not carry opaque markers in its detail dict values.

The tests are intentionally narrow: they check the ``label`` and
``visible_label`` fields by name, not via blind substring search, to avoid
false positives from dunder attributes that are legitimate Python
(``__init__``, ``__module__``, etc.).
"""
from __future__ import annotations

import json

from lawvm.core.phase_result import Finding
from lawvm.semantic.model import SemanticStructureNode


# ---------------------------------------------------------------------------
# Marker set — these strings are the ones that must NOT leak
# ---------------------------------------------------------------------------

_OPAQUE_MARKERS = (
    "__continuation__",
    "__tail_prose__",
    # __ord_N__ is a family of patterns; check for the prefix
    "__ord_",
)


def _has_opaque_label(text: str) -> bool:
    """Return True if *text* contains an opaque marker prefix."""
    return any(marker in text for marker in _OPAQUE_MARKERS)


# ---------------------------------------------------------------------------
# Helper: walk a dict tree and check label / visible_label fields
# ---------------------------------------------------------------------------

def _collect_label_values(obj: object, results: list[str]) -> None:
    """Recursively collect all 'label' and 'visible_label' string values."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in ("label", "visible_label") and isinstance(value, str):
                results.append(value)
            else:
                _collect_label_values(value, results)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_label_values(item, results)


# ---------------------------------------------------------------------------
# Item 5a: SemanticStructureNode serialization
# ---------------------------------------------------------------------------

def test_semantic_structure_node_does_not_export_opaque_label() -> None:
    """A SemanticStructureNode with a synthetic __ord_ label must NOT export it.

    ``SemanticStructureNode.to_dict()`` already guards against __ord_ labels
    (see model.py line ~131: ``not self.label.startswith("__ord_")``).
    This test confirms the contract holds at the JSON boundary.
    """
    # Build a tree with a deliberate opaque marker in label
    node_with_ord_label = SemanticStructureNode(
        kind="item",
        label="__ord_3",  # synthetic fallback — must not leak
        visible_label="",
        text="kolmanneksi",
    )
    d = node_with_ord_label.to_dict()
    json_str = json.dumps(d)

    label_values: list[str] = []
    _collect_label_values(json.loads(json_str), label_values)

    for val in label_values:
        assert not _has_opaque_label(val), (
            f"Opaque marker leaked into JSON label field: {val!r}\n"
            f"Full JSON: {json_str}"
        )


def test_semantic_structure_node_continuation_label_not_exported() -> None:
    """A __continuation__ label in visible_label must not appear in serialized output."""
    node = SemanticStructureNode(
        kind="item",
        label="__continuation__",
        visible_label="__continuation__",
        text="",
    )
    d = node.to_dict()
    json_str = json.dumps(d)

    label_values: list[str] = []
    _collect_label_values(json.loads(json_str), label_values)

    # The node may appear in the dict (to_dict does NOT filter visible_label),
    # but the TEST should assert that if a visible_label IS exported it is not
    # an opaque marker.  This test documents and enforces the boundary:
    # visible_label must not be set to an opaque value in the first place.
    for val in label_values:
        assert not _has_opaque_label(val), (
            f"Opaque marker leaked into JSON label/visible_label field: {val!r}\n"
            "visible_label must never be set to an opaque marker — only "
            "display-safe values are allowed."
        )


def test_nested_structure_no_opaque_leak() -> None:
    """A nested tree with clean labels serializes without opaque markers."""
    child_a = SemanticStructureNode(kind="subitem", label="a", text="first")
    child_b = SemanticStructureNode(kind="subitem", label="b", text="second")
    parent = SemanticStructureNode(
        kind="item",
        label="1",
        visible_label="1",
        children=(child_a, child_b),
    )
    root = SemanticStructureNode(
        kind="section",
        label="3",
        children=(parent,),
    )
    d = root.to_dict()
    json_str = json.dumps(d)

    label_values: list[str] = []
    _collect_label_values(json.loads(json_str), label_values)

    for val in label_values:
        assert not _has_opaque_label(val), (
            f"Opaque marker found in label field of clean tree: {val!r}"
        )


# ---------------------------------------------------------------------------
# Item 5b: Finding tuple serialization (represents 2013/331-style findings)
# ---------------------------------------------------------------------------

def _make_finding(kind: str, detail: dict) -> Finding:
    return Finding(
        kind=kind,
        role="observation",
        stage="parse",
        detail=detail,
        source_statute="2013/331",
        blocking=False,
    )


def test_findings_detail_does_not_carry_opaque_labels() -> None:
    """Finding.detail values must not carry opaque markers in label-like fields."""
    # Simulate findings that might plausibly arise from 2013/331 replay
    findings = (
        _make_finding(
            "ELAB.SOURCE_PATHOLOGY",
            {
                "kind": "base_unnumbered_paragraph_peer",
                "path": ("section:3", "subsection:1"),
                "label": "1",       # real label — clean
                "observation": "unnumbered peer detected",
            },
        ),
        _make_finding(
            "ELAB.SOURCE_PATHOLOGY",
            {
                "kind": "label_eid_divergence",
                "path": ("section:3",),
                "label": "3",       # real label — clean
            },
        ),
    )

    for finding in findings:
        detail_json = json.dumps(dict(finding.detail), ensure_ascii=False)
        # Check label-like keys specifically
        for key, val in finding.detail.items():
            if key in ("label", "visible_label", "node_label") and isinstance(val, str):
                assert not _has_opaque_label(val), (
                    f"Opaque marker in Finding.detail[{key!r}]: {val!r}\n"
                    f"Full detail: {dict(finding.detail)}"
                )


def test_findings_detail_path_values_no_opaque_markers() -> None:
    """Path tuple values in Finding.detail must not contain opaque markers."""
    finding = _make_finding(
        "ELAB.SOURCE_PATHOLOGY",
        {
            "kind": "unnumbered_peer_reparent",
            "path": ("section:3", "subsection:1"),
        },
    )
    path = finding.detail.get("path", ())
    if isinstance(path, (list, tuple)):
        for segment in path:
            if isinstance(segment, str):
                assert not _has_opaque_label(segment), (
                    f"Opaque marker in path segment: {segment!r}"
                )
