"""Roman-numeral label ordering and rendering in core tree_ops.

Covers two related bugs:

* Bug A — ``_default_sort_key`` ordered pure-Roman labels by their string
  (lexicographic: i, ii, iii, iv, ix, v, ...) instead of numerically, so
  ``resort_children`` reordered correct Roman lists and ``sort_order``
  invariants fired falsely.
* Bug B — ``_render_sequence_label`` had no ``'roman'`` branch, so a gap in a
  Roman-numbered sibling run was reported as decimal ``missing_labels`` that
  never match real source labels.
"""

from __future__ import annotations

from lawvm.core.ir import IRNode, IRNodeKind
from lawvm.core.tree_ops import (
    _default_sort_key,
    _render_sequence_label,
    _roman_run_ordinals,
    _run_aware_sort_key_fn,
    find_label_sequence_gap_warnings,
    iter_tree_invariant_violations,
    resort_children,
)


# ---------------------------------------------------------------------------
# Bug A — _default_sort_key numeric Roman ordering
# ---------------------------------------------------------------------------


def test_multichar_roman_sort_key_is_numeric_not_lexicographic():
    # The unambiguous per-label case: multi-char Roman tokens get numeric
    # ordinals, so ix(9) outranks viii(8) (lexicographically 'ix' < 'viii').
    assert _default_sort_key("ix") > _default_sort_key("viii")
    assert _default_sort_key("iv") > _default_sort_key("iii")
    assert "ix" < "viii"  # guard: lexicographic order really is the wrong one


def test_run_aware_roman_sequence_sorts_numerically():
    # A full sibling run i..x — including ambiguous single glyphs — must key
    # monotonically once the *run* is recognised as Roman (run-aware key).
    labels = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]
    key = _run_aware_sort_key_fn(labels, _default_sort_key)
    keys = [key(label) for label in labels]
    assert keys == [(n, "", 0) for n in range(1, 11)], keys
    assert keys == sorted(keys)


def test_roman_run_ordinals_classification():
    assert _roman_run_ordinals(["i", "ii", "iii", "iv"]) == {
        "i": (1, ""),
        "ii": (2, ""),
        "iii": (3, ""),
        "iv": (4, ""),
    }
    # Letter-suffixed roman amendment inserts key by (ordinal, suffix).
    assert _roman_run_ordinals(["ii", "iia", "iib", "iii"]) == {
        "ii": (2, ""),
        "iia": (2, "a"),
        "iib": (2, "b"),
        "iii": (3, ""),
    }
    # No multi-char roman signal -> ambiguous, refuse (fall back to alpha).
    assert _roman_run_ordinals(["i", "v", "x"]) is None
    # Pure-alpha list with i/v/x glyphs is not a Roman run.
    assert _roman_run_ordinals(["a", "b", "c", "i"]) is None
    # Alpha continuation tokens are not valid roman -> refuse.
    assert _roman_run_ordinals(["aa", "ab", "ii"]) is None


def test_multichar_roman_gets_numeric_ordinal():
    assert _default_sort_key("iv") == (4, "", 0)
    assert _default_sort_key("ix") == (9, "", 0)
    assert _default_sort_key("viii") == (8, "", 0)
    assert _default_sort_key("xiv") == (14, "", 0)


def test_single_char_ambiguous_glyphs_stay_alpha():
    # Single glyphs that are valid both as Roman and as alphabetic item labels
    # must NOT be promoted to Roman ordinals, so pure-alpha lists are not
    # regressed.
    for glyph in ["i", "v", "x", "c", "l", "d", "m"]:
        assert _default_sort_key(glyph) == (-1, glyph, 0)


def test_pure_alpha_list_with_ivx_letters_still_sorts_alphabetically():
    labels = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    keys = [_default_sort_key(label) for label in labels]
    assert keys == sorted(keys)
    assert keys == [(-1, label, 0) for label in labels]


def test_alpha_continuation_tokens_not_promoted():
    # 'aa'/'ab' look multi-char but are alphabetic continuations after 'z'.
    assert _default_sort_key("aa") == (-1, "aa", 0)
    assert _default_sort_key("ab") == (-1, "ab", 0)


def _roman_item_list(labels: list[str]) -> IRNode:
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        children=tuple(
            IRNode(kind=IRNodeKind.ITEM, label=label) for label in labels
        ),
    )


def test_resort_leaves_correct_roman_item_list_unchanged():
    # A correctly ordered Roman list must be a no-op for resort_children;
    # before the fix the lexicographic key reordered ix/v/...
    tree = _roman_item_list(["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"])
    resorted = resort_children(tree)
    assert [c.label for c in resorted.children] == [c.label for c in tree.children]


def test_resort_fixes_out_of_order_roman_item_list():
    tree = _roman_item_list(["iii", "i", "viii", "ix", "ii"])
    resorted = resort_children(tree)
    # Numeric order: i(1), ii(2), iii(3), viii(8), ix(9)
    assert [c.label for c in resorted.children] == ["i", "ii", "iii", "viii", "ix"]


def test_resort_roman_list_with_letter_suffix_inserts():
    # Roman list with amendment-inserted letter suffixes must sort each suffix
    # right after its base roman, not lexicographically scattered.
    tree = _roman_item_list(["i", "ii", "iia", "iib", "iii", "iiia", "iv", "v", "vi"])
    resorted = resort_children(tree)
    assert [c.label for c in resorted.children] == [c.label for c in tree.children]
    # And a scrambled version restores the same canonical order.
    scrambled = _roman_item_list(["iv", "ii", "i", "iiia", "iib", "iii", "vi", "iia", "v"])
    assert [c.label for c in resort_children(scrambled).children] == [
        "i", "ii", "iia", "iib", "iii", "iiia", "iv", "v", "vi",
    ]


def test_resort_does_not_promote_pure_alpha_list():
    # A pure-alpha item list a..j (containing i) must keep alphabetic order and
    # never be treated as roman.
    tree = _roman_item_list(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"])
    resorted = resort_children(tree)
    assert [c.label for c in resorted.children] == [c.label for c in tree.children]


def test_correct_roman_list_emits_no_false_sort_order_violation():
    tree = _roman_item_list(["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"])
    violations = [
        v
        for v in iter_tree_invariant_violations(tree, families={"sort_order"})
        if v.kind == "sort_order"
    ]
    assert violations == [], violations


# ---------------------------------------------------------------------------
# Bug B — _render_sequence_label roman branch
# ---------------------------------------------------------------------------


def test_render_sequence_label_roman():
    assert _render_sequence_label("roman", 4) == "iv"
    assert _render_sequence_label("roman", 9) == "ix"
    assert _render_sequence_label("roman", 8) == "viii"
    assert _render_sequence_label("roman", 14) == "xiv"
    assert _render_sequence_label("roman", 40) == "xl"


def test_roman_list_gap_reports_roman_missing_labels():
    # Uniformly multi-char Roman sibling run ii, iii, vi, vii with a gap at
    # iv, v.  This is the exact shape that drives _label_sequence_family_and
    # _ordinal -> family 'roman' -> _render_sequence_label.
    tree = _roman_item_list(["ii", "iii", "vi", "vii"])
    warnings = find_label_sequence_gap_warnings(tree)
    internal = [w for w in warnings if w["kind"] == "label_sequence_internal_gap"]
    assert internal, warnings
    # The gap must be rendered as Roman numerals (iv, v), never decimals like
    # '4'/'5' that can never match a real source label.
    assert internal[0]["missing_labels"] == ["iv", "v"], warnings
