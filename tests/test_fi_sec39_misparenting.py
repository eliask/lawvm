"""Tests for § Voimaantulo-section re-homing through
``_reconcile_materialized_fold_hcontainer_sections`` — the 2002/1248 §39
mutation-boundary violation.

Background (diagnosis at ``notes_internal/sec39_misparenting_bisect.md``
+ subagent report at ``notes_internal/reconcile_spurious_split_run_
diagnosis.md``):

When the FI replay fold raw source XML has §N (e.g. §39
"Voimaantulo- ja siirtymäsäännökset" on 2002/1248) as a direct
sibling of CHAPTER children under ``statuteProvisionsWrapper``, the
materialized IR briefly misplaced §39 at body root. The misplaced-
paths branch of ``_reconcile_materialized_fold_hcontainer_sections``
re-homed it via ``_ensure_body_hcontainer`` — which returned the FIRST
body-root HCONTAINER (typically the attachments hcontainer).

§39 ended up nested INTO attachments as a sibling of the appendix
attachment pages (the `<a href="media/...pdf">` link text "Liitteet"
appears there from source XML — that part is editorial legitimate).
But a §N "Voimaantulo" being railed under attachments is a Mutation
Boundary violation per AGENTS.md §1.0 + §1.6 unstated migration.

The fix: gate the helper choice on ``fold_has_hierarchical_roots``
(already computed at ``replay_products.py:1048``). When the fold
wrapper has hierarchical roots (chapters), use the new
``_ensure_body_provisions_hcontainer`` helper that prefers/creates a
``statuteProvisionsWrapper`` hcontainer — never picks attachments.
When the fold wrapper has NO hierarchical roots (just operative
sections), keep the original helper so the assembly-time split +
``MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT`` finding still fire for the
2009/1182 case (no chapters, §§-only).

Operating contract: AGENTS.md §1.0 + §1.6 (mutation boundary + unstated
migration), §2.9 (synthetic test per meaningful change).
"""
from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.replay_products import _reconcile_materialized_fold_hcontainer_sections


# ---------------------------------------------------------------------------
# Helpers — build a 2002/1248-shaped materialized tree.
# ---------------------------------------------------------------------------


def _chapters_only_body() -> IRNode:
    """A materialized body whose top-level children are 5 chapters under
    statuteProvisionsWrapper and a non-empty attachments hcontainer."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="1"),
            IRNode(kind=IRNodeKind.CHAPTER, label="2"),
            IRNode(kind=IRNodeKind.CHAPTER, label="3"),
            IRNode(kind=IRNodeKind.CHAPTER, label="4"),
            IRNode(kind=IRNodeKind.CHAPTER, label="5"),
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "attachments"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "attachment"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.CONTENT,
                                text="Liitteet",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _fold_with_direct_section_39_alongside_chapters() -> IRNode:
    """A fold IR where §39 is a direct sibling of chapter 1-5 under
    statuteProvisionsWrapper — the 2002/1248 source shape."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(
                    IRNode(kind=IRNodeKind.CHAPTER, label="1"),
                    IRNode(kind=IRNodeKind.CHAPTER, label="2"),
                    IRNode(kind=IRNodeKind.CHAPTER, label="3"),
                    IRNode(kind=IRNodeKind.CHAPTER, label="4"),
                    IRNode(kind=IRNodeKind.CHAPTER, label="5"),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="39",
                        attrs={"eId": "sec_39"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.CONTENT,
                                        text="Tämä asetus tulee voimaan 1 päivänä tammikuuta 2003.",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# 2002/1248 §39 misparenting regression
# ---------------------------------------------------------------------------


def _build_materialized_with_39_misplaced_at_body_root() -> IRNode:
    """A body where §39 lives at body root (misplaced by core materialization
    when the wrapper got unwrapped) + an empty attachments hcontainer
    alongside — the shape entering ``_reconcile_materialized_fold`` for §39."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="1"),
            IRNode(kind=IRNodeKind.CHAPTER, label="2"),
            IRNode(kind=IRNodeKind.CHAPTER, label="3"),
            IRNode(kind=IRNodeKind.CHAPTER, label="4"),
            IRNode(kind=IRNodeKind.CHAPTER, label="5"),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="39",
                attrs={"eId": "sec_39"},
                children=(
                    IRNode(
                        kind=IRNodeKind.CONTENT,
                        text="Tämä asetus tulee voimaan 1 päivänä tammikuuta 2003.",
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "attachments"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "attachment"},
                        children=(
                            IRNode(
                                kind=IRNodeKind.CONTENT,
                                text="Liitteet",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_misplaced_section_39_with_hierarchical_roots_lands_in_provisions_wrapper() -> None:
    """§39 misplaced at body root, alongside 5 chapters → re-homed into a
    provisions wrapper, NOT into attachments hcontainer. (2002/1248 case.)
    """
    materialized = _build_materialized_with_39_misplaced_at_body_root()
    fold = _fold_with_direct_section_39_alongside_chapters()

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, fold)

    # §39 should be inside a statuteProvisionsWrapper hcontainer now.
    wrapper = next(
        (c for c in reconciled.children
         if c.kind is IRNodeKind.HCONTAINER
         and c.attrs.get("name") == "statuteProvisionsWrapper"),
        None,
    )
    assert wrapper is not None, (
        "Expected a statuteProvisionsWrapper hcontainer in reconciled body "
        "after §39 misplaced-section re-home; found: "
        f"{[(c.kind.value, c.attrs.get('name')) for c in reconciled.children]}"
    )
    sec39 = next(
        (c for c in wrapper.children
         if c.kind is IRNodeKind.SECTION and c.label == "39"),
        None,
    )
    assert sec39 is not None, (
        "Expected §39 as a direct child of statuteProvisionsWrapper; "
        f"wrapper children found: {[(c.kind.value, c.label) for c in wrapper.children]}"
    )


def test_misplaced_section_39_does_not_end_up_under_attachments() -> None:
    """§39 must not be railed INTO the attachments hcontainer."""
    materialized = _build_materialized_with_39_misplaced_at_body_root()
    fold = _fold_with_direct_section_39_alongside_chapters()

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, fold)

    attachments = next(
        (c for c in reconciled.children
         if c.kind is IRNodeKind.HCONTAINER
         and c.attrs.get("name") == "attachments"),
        None,
    )
    assert attachments is not None, "attachments hcontainer should survive reconcile"
    sec39_in_attachments = any(
        c.kind is IRNodeKind.SECTION and c.label == "39"
        for c in attachments.children
    )
    assert not sec39_in_attachments, (
        "§39 was railed INTO attachments hcontainer; the §39 misparenting "
        "regression has recurred — see notes_internal/sec39_misparenting_bisect.md"
    )


# ---------------------------------------------------------------------------
# 2009/1182 — no hierarchical roots — original rails-into-attachments behaviour
# preserved so assembly-split + finding emission still fire.
# ---------------------------------------------------------------------------


def _fold_with_no_hierarchical_roots_direct_section_1() -> IRNode:
    """A fold where wrapper has only direct §§ (no chapters)."""
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Section 1 text"),),
                    ),
                ),
            ),
        ),
    )


def test_misplaced_section_with_no_hierarchical_roots_keeps_original_assembly_split_path() -> None:
    """When the fold wrapper has NO hierarchical roots (no Part/Chapter
    siblings), the misplaced-section branch uses the original
    ``_ensure_body_hcontainer`` helper — so the section railed into a body-
    level hcontainer (typically the attachments) intentionally, leaving
    the assembly-time split to fire its ``MATERIALIZED_ATTACHMENTS_WRAPPER_
    SPLIT`` finding later in the pipeline."""
    misplaced_section_1 = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="Section 1 text"),),
    )
    materialized = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            misplaced_section_1,
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "attachments"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "attachment"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="attachment text"),),
                    ),
                ),
            ),
        ),
    )
    fold = _fold_with_no_hierarchical_roots_direct_section_1()

    reconciled = _reconcile_materialized_fold_hcontainer_sections(materialized, fold)

    # The original behaviour re-homes §1 into the FIRST body hcontainer — the
    # attachments hcontainer — so the assembly-time split finds §1 there and
    # emits the MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT finding later.
    attachments = next(
        (c for c in reconciled.children
         if c.kind is IRNodeKind.HCONTAINER
         and c.attrs.get("name") == "attachments"),
        None,
    )
    assert attachments is not None, "attachments hcontainer should survive"
    sec1_in_attachments = any(
        c.kind is IRNodeKind.SECTION and c.label == "1"
        for c in attachments.children
    )
    # The original behaviour intentionally re-homes §1 into the body-root
    # hcontainer (attachments) so the assembly split + finding fire.
    assert sec1_in_attachments, (
        "Expected §1 to be re-homed into the body hcontainer (attachments) "
        "so assembly-split + MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT finding "
        "still fire — the uncategorized fold wrapper case (no chapters)."
    )
