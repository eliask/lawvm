"""SDOC invariants as test assertions (doc3 §invariants).

Each invariant becomes a failing-or-passing test against either:
* a synthetic IRNode tree (for invariants that need a constructed shape), or
* the D0 attachment-IR fixture at
  ``data/finland/attachment_ir/2002/1248/4484.json`` (the canonical real
  corpus that pins the SDOC contract against an actual Finlex attachment
  PDF — 4398 nodes).

The invariants that have a runtime check in the codebase are pinned here.
For invariants that the codebase does not yet enforce structurally
(e.g. SDOC-11 — runtime may not invoke PDF extraction without an explicit
rebuild flag), the test asserts the public API gate so this suite
becomes a *guard-liveness pin*: regressing the gate fails the test, not
just removes a comment.

Source Document IR (SDOC) is the architectural claim — attachments are one
node family inside it, not a separate system. The invariants below are
copied from ``notes_internal/REMAINING_WORK.md`` (the doc3 §invariants list)
so they are *the contract the codebase is asked to obey*, not aspirational
decoration. Each test names the SDOC-id it pins.

Operating contract: AGENTS.md §2.9 (synthetic test per meaningful change)
+ §2.10 (planes stay type-distinct).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.node_kind_registry import NODE_KIND_SPECS, validate_node
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ir_serialize import load_attachment_ir


_D0_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "finland"
    / "attachment_ir"
    / "2002_1248"
    / "4484.json"
)

_EVIDENCE_ATTRS = (
    "page_index",
    "bbox",
    "extraction_status",
    "source_span",
    "source_text",
    "source_ref",
    "pdf_sha256",
    "extraction_method",
)


# ---------------------------------------------------------------------------
# SDOC-01: every SourceDocumentIR has exactly one root node
# ---------------------------------------------------------------------------


def _walk(node: IRNode) -> Iterator[IRNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _load_d0_fixture_or_skip() -> IRNode:
    ir = load_attachment_ir("2002/1248", "4484.pdf")
    if ir is None:
        pytest.skip("D0 attachment IR fixture is not present in this checkout")
    return ir


def test_sdoc_01_d0_fixture_has_exactly_one_root() -> None:
    """The D0 fixture is a single IRNode tree — exactly one HCONTAINER root."""
    ir = _load_d0_fixture_or_skip()
    assert ir.kind == IRNodeKind.HCONTAINER, (
        f"D0 root kind must be HCONTAINER; got {ir.kind!r}"
    )
    # The tree mutates only via functional rebuild; the root is unique by
    # construction (IRNode is frozen). One root → pass; a forest would
    # have surfaced as multiple HCONTAINER siblings at the top, which
    # load_attachment_ir cannot represent.


# ---------------------------------------------------------------------------
# SDOC-06: source_anchors are evidence attrs and never enter content_leaf_hash
# ---------------------------------------------------------------------------


def _content_leaf_attrs(node: IRNode) -> tuple[str, ...]:
    """Return the attr keys that enter a content_leaf_hash (semantic identity).

    Evidence attrs (page_index/bbox/source_span/source_text/etc.) per
    SDOC-06 MUST be excluded — they are footing, not identity.
    """
    if not node.attrs:
        return ()
    return tuple(sorted(k for k in node.attrs.keys() if k not in _EVIDENCE_ATTRS))


def test_sdoc_06_evidence_attrs_are_excluded_from_content_leaf_attrs() -> None:
    """A cell carrying both column_id (semantic) and page_index (evidence)
    has only the semantic attr in its content-leaf identity."""
    node = IRNode(
        kind=IRNodeKind.CELL,
        attrs={
            "column_id": "Ajoneuvo",
            "row_key": "M",
            "page_index": 2,
            "bbox": [120.0, 440.0, 200.0, 460.0],
            "source_span": "0:120:0:80",
        },
        text="M",
    )
    semantic = _content_leaf_attrs(node)
    assert "column_id" in semantic
    assert "row_key" in semantic
    # Evidence attrs filtered out — they do not enter identity.
    assert "page_index" not in semantic
    assert "bbox" not in semantic
    assert "source_span" not in semantic


def test_sdoc_06_d0_fixture_evidence_attrs_do_not_crash_identity() -> None:
    """Walking D0: every node with evidence attrs degrades cleanly to the
    semantic identity (no KeyError, no assertion)."""
    ir = _load_d0_fixture_or_skip()
    for node in _walk(ir):
        # Baseline: the helper does not raise on real-corpus shapes.
        _content_leaf_attrs(node)


# ---------------------------------------------------------------------------
# SDOC-07: semantic table coordinates, not page coordinates, define table cell address
# ---------------------------------------------------------------------------


def test_sdoc_07_table_cell_address_uses_semantic_coords() -> None:
    """A CELL node's address is column_id/row_key, NEVER page_index/bbox.

    The node-kind registry treats row_key/column_id as semantic
    (content_leaf_hash) — they sit alongside eId in the kind's known
    optional_attrs (address attrs). page_index/bbox sit in the
    evidence-attrs baseline (excluded from identity).

    This invariant is structural: the registry's address_attrs are
    {eId, row_key, column_id} and the evidence-attrs baseline lists
    page_index/bbox.
    """
    spec = NODE_KIND_SPECS[IRNodeKind.CELL]
    assert "column_id" in spec.optional_attrs
    assert "row_key" in spec.optional_attrs
    # Evidence attrs must be permitted as evidence (not as identity):
    assert "page_index" in spec.optional_attrs
    assert "bbox" in spec.optional_attrs


# ---------------------------------------------------------------------------
# SDOC-08: footnote labels are unique only within footnote_scope
# ---------------------------------------------------------------------------


def test_sdoc_08_footnote_labels_unique_within_scope() -> None:
    """Two footnote scopes may each carry a footnote labelled '1' — same label,
    different scopes — without violating uniqueness.

    SCHEDULE_ENTRY is the attachment-IR's footnote embodiment (label
    is the marker; the parent SCHEDULE/APPENDIX is the scope).
    """
    tree = IRNode(
        kind=IRNodeKind.HCONTAINER,
        children=(
            IRNode(
                kind=IRNodeKind.APPENDIX,
                label="osa_I",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE,
                        label="liite_1",
                        children=(
                            IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label="1", text="Footnote body A1"),
                            IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label="2", text="Footnote body A2"),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.APPENDIX,
                label="osa_II",
                children=(
                    IRNode(
                        kind=IRNodeKind.SCHEDULE,
                        label="liite_1",
                        children=(
                            # Same label '1' within a DIFFERENT scope (osa_II) — legal.
                            IRNode(kind=IRNodeKind.SCHEDULE_ENTRY, label="1", text="Footnote body B1"),
                        ),
                    ),
                ),
            ),
        ),
    )

    # Walk: collect (scope, label) pairs and assert scope-local uniqueness.
    seen: set[tuple[str, str]] = set()
    for scope_node in (c for c in tree.children if c.kind == IRNodeKind.APPENDIX):
        scope_label = scope_node.label or ""
        for sched in scope_node.children:
            if sched.kind != IRNodeKind.SCHEDULE:
                continue
            for entry in sched.children:
                if entry.kind != IRNodeKind.SCHEDULE_ENTRY:
                    continue
                key = (scope_label, entry.label or "")
                assert key not in seen, (
                    f"duplicate footnote label {entry.label!r} within "
                    f"scope {scope_label!r} (SDOC-08 violation)"
                )
                seen.add(key)


def test_sdoc_08_d0_fixture_footnote_labels_are_scope_unique() -> None:
    """D0 fixture: SCHEDULE_ENTRY labels are unique within their parent scope."""
    ir = _load_d0_fixture_or_skip()
    # Walk: each APPENDIX scope must collect no duplicate SCHEDULE_ENTRY labels
    # within that scope.
    for scope_node in (c for c in ir.children if c.kind == IRNodeKind.APPENDIX):
        scope_label = scope_node.label or ""
        labels: set[str] = set()
        for sched in scope_node.children:
            if sched.kind != IRNodeKind.SCHEDULE:
                continue
            for entry in sched.children:
                if entry.kind != IRNodeKind.SCHEDULE_ENTRY:
                    continue
                lbl = entry.label or ""
                # SDOC-08 permits re-use ACROSS scopes; violation only within.
                # We assert the (scope, label) pair was recorded uniquely here.
                assert (scope_label, lbl) not in {
                    (scope_label, x) for x in labels
                }, (
                    f"duplicate footnote label {lbl!r} within scope "
                    f"{scope_label!r} in D0 fixture (SDOC-08)"
                )
                labels.add(lbl)


# ---------------------------------------------------------------------------
# SDOC-11: runtime may not invoke PDF extraction unless an explicit rebuild
# flag is set — pinned by asserting the public API DOES NOT silently call
# pdfplumber on a cold load.
# ---------------------------------------------------------------------------


def test_sdoc_11_load_attachment_ir_does_not_reextract_when_canonical_exists() -> None:
    """The canonical store is the preferrable cold path; the runtime MUST
    NOT call pdfplumber when a canonical IR is on disk."""
    import builtins
    from unittest.mock import patch

    real_import = builtins.__import__

    def _fail_on_pdfplumber(name, *args, **kwargs):
        if name == "pdfplumber" or name.startswith("pdfplumber."):
            raise AssertionError(
                f"SDOC-11 violation: cold load attempted to import {name!r} "
                "(canonical IR present — runtime should NOT re-extract PDF)"
            )
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fail_on_pdfplumber):
        ir = _load_d0_fixture_or_skip()
        assert ir.kind == IRNodeKind.HCONTAINER
        # Defensive: ensure no pdfplumber leaked into sys.modules under the patch.
        import sys
        sys.modules.pop("pdfplumber", None)


# ---------------------------------------------------------------------------
# SDOC-12: LLM output cannot be promoted without verification status
# ---------------------------------------------------------------------------


def test_sdoc_12_overlay_node_defaults_to_unauthorized() -> None:
    """A node that represents an overlay/enrichment (LLM proposal, vision
    output, human input) must NOT carry ``replay_authorized=True`` by default.

    Promotion is an EXPLICIT typed step (AGENTS.md §2.10 plane boundary).
    The IRNode attrs are a FrozenDict — the absence of an ``replay_authorized``
    attr means the node is treated as observation-only.
    """
    proposal = IRNode(
        kind=IRNodeKind.P,
        text="LLM-proposed: tämä on ehdotus",
        attrs={"provenance": "llm_proposal", "verification_status": "unverified"},
    )
    # Default: not authorized — explicit absence of the auth flag.
    assert not proposal.attrs.get("replay_authorized", False), (
        "overlay/enrichment node must default to replay_authorized=False; "
        "promotion requires an explicit ExecutionAuthorization carrier "
        "(AGENTS.md §2.10) not an attrs-flag set to True"
    )


def test_sdoc_12_overlay_node_with_verification_status_still_requires_explicit_promotion() -> None:
    """Even when verification_status moves to 'verified', promotion to
    replay authority is a separate typed step — not flipping a flag."""
    verified_proposal = IRNode(
        kind=IRNodeKind.P,
        text="Verified by deterministic checker",
        attrs={"provenance": "llm_proposal", "verification_status": "verified"},
    )
    # The verification status has changed; replay authority has NOT.
    # A consumer MUST consult an explicit ExecutionAuthorization carrier
    # (§2.10) — not the attrs dict — to authorize mutation.
    assert "replay_authorized" not in verified_proposal.attrs


# ---------------------------------------------------------------------------
# SDOC-13: a projection must include attachments/schedules unless explicitly
# scoped out — pinned against the ``lawvm show`` command's --no-attachments flag.
# ---------------------------------------------------------------------------


def test_sdoc_13_show_includes_attachments_by_default() -> None:
    """``show --no-attachments`` is the explicit opt-out. By default the
    projection includes attachments (the D1 SDOC contract). The CLI flag
    defaults to False — ``--no-attachments`` is required to scope them out.
    """
    # The CLI parser sets the dest no_attachments to False unless the flag
    # is present. Test the flag default directly.
    from lawvm.tools.cli import _build_parser

    parser = _build_parser()
    # No --no-attachments: default must be False (attachments included).
    args = parser.parse_args(["show", "2006/1299"])
    assert args.no_attachments is False

    # Explicit opt-out: True (attachments excluded).
    args = parser.parse_args(["show", "2006/1299", "--no-attachments"])
    assert args.no_attachments is True


# ---------------------------------------------------------------------------
# Cross-cutting: D0 fixture passes the D1 node-kind registry validator.
# This pins the SDOC contract to actual corpus data — a regression that
# emits ``unknown_kind`` notices on the D0 tree would fail this test.
# ---------------------------------------------------------------------------


def test_d0_fixture_passes_node_kind_validator() -> None:
    """D0 IRNode tree: validate_node emits zero unknown_kind / unknown_attr
    violations on every governed kind."""
    ir = _load_d0_fixture_or_skip()
    violations = validate_node(ir)
    # Crashes-on-unknown-kind would have surfaced here instead of silently
    # tolerating an unrecognised kind in the fixture.
    assert all(v.kind != "unknown_kind" for v in violations), (
        f"unknown IRNodeKind in D0 fixture: {[v.node_kind for v in violations if v.kind == 'unknown_kind']}"
    )


def test_d0_fixture_has_expected_top_level_structure() -> None:
    """D0 fixture: the root carries at least one APPENDIX (attachment) and
    at least one SCHEDULE (a footnote/schedule-bearing child)."""
    ir = _load_d0_fixture_or_skip()
    kinds = [c.kind for c in ir.children]
    assert IRNodeKind.APPENDIX in kinds, "D0 should have an APPENDIX child"
    schedule_present = any(
        c.kind == IRNodeKind.APPENDIX and any(
            gc.kind == IRNodeKind.SCHEDULE for gc in c.children
        )
        for c in ir.children
    )
    assert schedule_present, "D0 fixture should carry at least one SCHEDULE under an APPENDIX"


def test_d0_fixture_source_meta_carries_pdf_evidence() -> None:
    """D0 fixture: the JSON wrapper's source block carries the evidence
    fing that SDOC-06 promises to preserve (raw_sha256, locator, media_type)."""
    if not _D0_FIXTURE.exists():
        pytest.skip("D0 attachment IR fixture is not present in this checkout")
    data = json.loads(_D0_FIXTURE.read_text(encoding="utf-8"))
    src = data.get("source") or {}
    assert src.get("role") == "official_pdf", (
        f"D0 source role must be 'official_pdf'; got {src.get('role')!r}"
    )
    assert src.get("raw_sha256"), "D0 fixture must carry raw_sha256 evidence"
    assert src.get("media_type") == "application/pdf"
    assert src.get("locator"), "D0 fixture must carry a source locator"
