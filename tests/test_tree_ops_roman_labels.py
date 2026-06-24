"""Run-aware Roman-numeral label ordering + gap rendering in core tree_ops.

These tests pin the run-aware Roman fix (and guard against the reverted per-label
promotion that broke UK alphabetic-continuation schedules):

  * BITE A: a sibling run ``i, ii, ..., x`` sorts NUMERICALLY (``ix`` after
    ``viii``) and produces NO false ``sort_order`` invariant violation.
  * BITE B: a UK-shaped alphabetic-continuation run ``a, b, c, d, da, ...,
    dm, e, f`` stays in alphabetic family order — ``dc``/``dd``/``di`` (which
    are *also* valid Roman numerals in isolation) are NOT promoted to huge
    Roman ordinals, because the presence of non-Roman members (``a``/``b``/
    ``e``/``f``) disqualifies the whole run.
  * BITE C: a gap inside a run classified as family ``roman`` renders
    ``missing_labels`` as Roman numerals, not decimals.

The single-label disambiguation is fundamentally impossible (``dc`` could be
alpha-continuation or Roman-600); only the RUN tells you. So ``_default_sort_key``
must NOT promote ambiguous labels per-label — it stays at its original
``(-1, s, 0)`` fallback — and promotion happens only through the run-aware path.
"""

from __future__ import annotations

from typing import cast

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import (
    _default_sort_key,
    _render_sequence_label,
    _roman_run_ordinals,
    _run_aware_sort_key_fn,
    check_invariants,
    find_label_sequence_gap_warnings,
    resort_children,
)


def _item(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.ITEM, label=label, text=label)


# ---------------------------------------------------------------------------
# Per-label key stays ambiguity-agnostic (the reverted-commit defect guard)
# ---------------------------------------------------------------------------


def test_default_sort_key_does_not_promote_ambiguous_roman_per_label() -> None:
    # 'dc' (=Roman 600), 'dl' (=550), 'dm' (=1000), 'di' (=501) are valid Roman
    # numerals but ALSO valid alphabetic-continuation item labels. The per-label
    # key must remain agnostic — the original digits-fallback slot — so it cannot
    # scatter an alphabetic family. Disambiguation is the run-aware path's job.
    for ambiguous in ("dc", "dd", "di", "dl", "dm"):
        assert _default_sort_key(ambiguous) == (-1, ambiguous, 0)
    # Single glyphs likewise stay ambiguous per-label.
    for glyph in ("i", "v", "x"):
        assert _default_sort_key(glyph) == (-1, glyph, 0)


# ---------------------------------------------------------------------------
# Run classifier
# ---------------------------------------------------------------------------


def test_roman_run_ordinals_promotes_genuine_roman_run() -> None:
    labels = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
    mapping = _roman_run_ordinals(labels)
    assert mapping is not None
    assert mapping["ix"] == (9, "")
    assert mapping["viii"] == (8, "")
    assert mapping["x"] == (10, "")


def test_roman_run_ordinals_rejects_uk_alpha_continuation_run() -> None:
    # The UK schedule run: a..f plus da..dm. Members like 'a','b','e','f' are not
    # Roman numerals at all, which disqualifies the whole run even though
    # 'dc'/'dd'/'di'/'dl'/'dm' are individually Roman-matching.
    labels = ["a", "b", "c", "d", "da", "db", "dc", "dd", "de", "df",
              "dg", "dh", "di", "dj", "dk", "dl", "dm", "e", "f"]
    assert _roman_run_ordinals(labels) is None


def test_roman_run_ordinals_rejects_single_glyph_only_run() -> None:
    # A lone 'i' or a bare alpha 'a, b, c' is never promoted — no multi-letter
    # Roman token proves the run is Roman.
    assert _roman_run_ordinals(["i"]) is None
    assert _roman_run_ordinals(["a", "b", "c"]) is None


# ---------------------------------------------------------------------------
# BITE A: Roman run sorts numerically, no false sort_order violation
# ---------------------------------------------------------------------------


def test_bite_a_roman_run_sorts_numerically() -> None:
    # A correctly-ordered Roman run must be recognised as in-order: numerically
    # ix(9) > viii(8), so NO sort_order violation, and resort leaves it untouched.
    ordered = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
    parent = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        text="",
        children=tuple(_item(l) for l in ordered),
    )
    tree = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=(parent,))

    # No false sort_order violation on the already-correct Roman run.
    violations = [v for v in check_invariants(tree) if "out of order" in v]
    assert violations == [], violations

    # resort_children must be a no-op (run already numerically ordered).
    resorted = resort_children(tree)
    assert [c.label for c in resorted.children[0].children] == ordered

    # And a scrambled Roman run gets restored to NUMERIC order (ix after viii).
    scrambled = ["i", "ii", "iii", "iv", "ix", "v", "vi", "vii", "viii", "x"]
    scrambled_parent = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="1",
        text="",
        children=tuple(_item(l) for l in scrambled),
    )
    scrambled_tree = IRNode(
        kind=IRNodeKind.BODY, label=None, text="", children=(scrambled_parent,)
    )
    fixed = resort_children(scrambled_tree)
    assert [c.label for c in fixed.children[0].children] == ordered


# ---------------------------------------------------------------------------
# BITE B: UK alpha-continuation family order preserved
# ---------------------------------------------------------------------------


def test_bite_b_uk_alpha_continuation_family_stays_alphabetic() -> None:
    alpha_family = ["a", "b", "c", "d", "da", "db", "dc", "dd", "de", "df",
                    "dg", "dh", "di", "dj", "dk", "dl", "dm", "e", "f"]
    parent = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label="2",
        text="",
        children=tuple(_item(l) for l in alpha_family),
    )
    tree = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=(parent,))

    # The alphabetic family is already in alphabetic order; the run-aware path
    # must NOT reclassify it as Roman and must NOT reorder it.
    resorted = resort_children(tree)
    assert [c.label for c in resorted.children[0].children] == alpha_family

    # And no false sort_order violation (which a Roman-promotion of dc/dl/dm
    # would produce: dm=1000 before e/f would look out of order, or dc=600
    # before dd=alpha would).
    violations = [v for v in check_invariants(tree) if "out of order" in v]
    assert violations == [], violations


# ---------------------------------------------------------------------------
# BITE C: Roman gap renders missing labels as Roman numerals
# ---------------------------------------------------------------------------


def test_bite_c_roman_gap_renders_roman_missing_labels() -> None:
    # A Roman run ii, iii, vi, vii skips iv and v. Every present member is a
    # multi-letter roman token, so the gap walker classifies the family as
    # 'roman' (single glyphs i/v/x are per-label-ambiguous and classify as alpha
    # by design — only the run-aware sort path promotes those). The gap warning
    # must render the missing labels as Roman numerals ['iv', 'v'], not decimals.
    present = ["ii", "iii", "vi", "vii"]
    parent = IRNode(
        kind=IRNodeKind.SUBSECTION,
        label="1",
        text="",
        children=tuple(
            IRNode(kind=IRNodeKind.ITEM, label=l, text=l) for l in present
        ),
    )
    tree = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=(parent,))

    warnings = find_label_sequence_gap_warnings(tree)
    roman_gaps = [w for w in warnings if w.get("family") == "roman"]
    assert roman_gaps, warnings
    internal = [w for w in roman_gaps if w["kind"] == "label_sequence_internal_gap"]
    assert internal, roman_gaps
    missing = cast("list[str]", internal[0]["missing_labels"])
    assert missing == ["iv", "v"], missing
    # Self-consistency: EVERY rendered missing label across all roman gap
    # warnings is a Roman numeral, never a decimal that can never match a real
    # source label (the Bug B self-contradicting witness).
    for warning in roman_gaps:
        for label in cast("list[str]", warning["missing_labels"]):
            assert not label.isdigit(), (warning["kind"], label)


def test_render_sequence_label_roman_branch() -> None:
    assert _render_sequence_label("roman", 4) == "iv"
    assert _render_sequence_label("roman", 9) == "ix"
    assert _render_sequence_label("roman", 14) == "xiv"
    assert _render_sequence_label("roman", 40) == "xl"


# ---------------------------------------------------------------------------
# Run-aware guard: a non-default (jurisdiction) key is left untouched
# ---------------------------------------------------------------------------


def test_run_aware_passthrough_for_non_default_key() -> None:
    def custom_key(label):  # type: ignore[no-untyped-def]
        return (0, label or "", 0)

    labels = ["i", "ii", "iii", "iv"]
    assert _run_aware_sort_key_fn(labels, custom_key) is custom_key
