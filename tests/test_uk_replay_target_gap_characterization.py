"""Characterization tests pinning the numeral-mode behavior of the two
extreme-complexity gap functions in ``replay_target_diagnostics``:

  * ``_missing_sibling_range_gap`` (roman/arabic/alpha sibling-range gap)
  * ``_malformed_target_gap``      (malformed-target shape gap)

These tests drive the REAL ``UKReplayExecutor`` so ``_find_node_by_target`` is
the production lookup, and assert the exact boolean each function returns for a
broad matrix of constructed (statute, target) shapes covering every numeral
mode (numeric / alpha / alpha-suffix / roman / alnum-suffix / alnum-multi-suffix
/ alpha-num-suffix / part) and every malformed-target branch.

The embedded ``GOLDEN`` dict is the byte-identity guard for the table-driven
refactor: any change to a recorded boolean is a behavior change and fails here.
Generated/curated, then frozen.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.replay_executor import UKReplayExecutor

_KIND_MAP: dict[str, IRNodeKind] = {k.value.lower(): k for k in IRNodeKind}


def _kind(k: str) -> IRNodeKind:
    # Real frontend kinds include strings without enum members ("point",
    # "article", "pblock", ...). IRNode lowercases ``str(kind)`` everywhere the
    # gap functions read it, so a raw string is behaviorally equivalent; cast to
    # satisfy the IRNodeKind annotation.
    enum_kind = _KIND_MAP.get(k.lower())
    return enum_kind if enum_kind is not None else cast(IRNodeKind, k)


def N(
    kind: str,
    label: str | None = None,
    children: Sequence[IRNode] = (),
    text: str = "",
) -> IRNode:
    return IRNode(kind=_kind(kind), label=label, text=text, children=tuple(children))


def statute(
    body_children: Sequence[IRNode] = (),
    supplements: Sequence[IRNode] = (),
) -> IRStatute:
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(body_children)),
        supplements=tuple(supplements),
    )


def addr(*path: tuple[str, str]) -> LegalAddress:
    return LegalAddress(path=tuple(path), special=None)


# (case_id, statute, target) tuples — appended by the builder block below.
CASES: list[tuple[str, IRStatute, LegalAddress]] = []


def add(case_id: str, st: IRStatute, target: LegalAddress) -> None:
    CASES.append((case_id, st, target))


def sec(children: Sequence[IRNode]) -> IRNode:
    return N("section", "1", children)


def addm(case_id: str, st: IRStatute, target: LegalAddress) -> None:
    CASES.append(("MAL_" + case_id, st, target))



# ========== _missing_sibling_range_gap matrix ==========
# numeric mode: sibling numeric labels around / outside want
for want, labels in [
    ("2", ["1", "3"]),       # gap between
    ("5", ["1", "2"]),       # above max
    ("1", ["2", "3"]),       # below min
    ("2", ["2"]),            # present
    ("4", ["1", "2", "3"]),  # just above
    ("2", []),               # empty
]:
    add(f"sib_numeric_w{want}_{'_'.join(labels) or 'none'}",
        statute([N("section", "1", [N("subsection", l) for l in labels])]),
        addr(("section", "1"), ("subsection", want)))

# numeric with blank-same-kind present
add("sib_numeric_blank_present",
    statute([N("section", "1", [N("subsection", ""), N("subsection", "3")])]),
    addr(("section", "1"), ("subsection", "2")))

# numeric vs numeric_suffix siblings (no plain numeric siblings)
add("sib_numeric_suffix_siblings",
    statute([N("section", "1", [N("subsection", "1a"), N("subsection", "3a")])]),
    addr(("section", "1"), ("subsection", "2")))

# alpha mode
for want, labels in [
    ("b", ["a", "c"]),
    ("a", ["b", "c"]),
    ("c", ["a", "b"]),
    ("b", ["b"]),
]:
    add(f"sib_alpha_w{want}_{'_'.join(labels)}",
        statute([N("section", "1", [N("subsection", l) for l in labels])]),
        addr(("section", "1"), ("subsection", want)))

# alpha with doubled-letter siblings (repeated)
add("sib_alpha_repeated_siblings",
    statute([N("section", "1", [N("subsection", "aa"), N("subsection", "cc")])]),
    addr(("section", "1"), ("subsection", "bb")))
add("sib_alpha_prefix_sibling",
    statute([N("section", "1", [N("subsection", "bb")])]),
    addr(("section", "1"), ("subsection", "b")))

# alpha_suffix mode (multi-letter want)
add("sib_alpha_suffix_startswith",
    statute([N("section", "1", [N("subsection", "abc")])]),
    addr(("section", "1"), ("subsection", "ab")))
add("sib_alpha_suffix_first_in_alpha_raw",
    statute([N("section", "1", [N("subsection", "a")])]),
    addr(("section", "1"), ("subsection", "ab")))
add("sib_alpha_suffix_gap",
    statute([N("section", "1", [N("subsection", "a"), N("subsection", "c")])]),
    addr(("section", "1"), ("subsection", "bb")))

# roman mode
for want, labels in [
    ("ii", ["i", "iii"]),
    ("iv", ["i", "ii"]),
    ("i", ["ii", "iii"]),
    ("ii", ["ii"]),
    ("iiii", ["i", "v"]),   # non-canonical roman -> rejected
]:
    add(f"sib_roman_w{want}_{'_'.join(labels)}",
        statute([N("section", "1", [N("paragraph", l) for l in labels])]),
        addr(("section", "1"), ("paragraph", want)))

# alnum_suffix mode (digit+letter)
add("sib_alnum_gap_between",
    statute([N("section", "1", [N("subsection", "6a"), N("subsection", "6c")])]),
    addr(("section", "1"), ("subsection", "6b")))
add("sib_alnum_base_present",
    statute([N("section", "1", [N("subsection", "6")])]),
    addr(("section", "1"), ("subsection", "6a")))
add("sib_alnum_no_pairs_numeric_suffix",
    statute([N("section", "1", [N("subsection", "5"), N("subsection", "7")])]),
    addr(("section", "1"), ("subsection", "6a")))
add("sib_alnum_same_num_extension",
    statute([N("section", "1", [N("subsection", "6a"), N("subsection", "6z")])]),
    addr(("section", "1"), ("subsection", "6m")))

# alnum_multi_suffix mode (digit + 2+ letters)
add("sib_alnum_multi_pairs",
    statute([N("section", "1", [N("subsection", "6aa"), N("subsection", "6cc")])]),
    addr(("section", "1"), ("subsection", "6bb")))
add("sib_alnum_multi_base_present",
    statute([N("section", "1", [N("subsection", "6")])]),
    addr(("section", "1"), ("subsection", "6aa")))
add("sib_alnum_multi_local_suffix_sibling",
    statute([N("section", "1", [N("subsection", "6a")])]),
    addr(("section", "1"), ("subsection", "6bb")))

# alpha_num_suffix mode (letter+digit)
add("sib_alpha_num_pairs",
    statute([N("section", "1", [N("paragraph", "a1"), N("paragraph", "a3")])]),
    addr(("section", "1"), ("paragraph", "a2")))
add("sib_alpha_num_raw_prefix",
    statute([N("section", "1", [N("paragraph", "a")])]),
    addr(("section", "1"), ("paragraph", "a2")))

# part mode (numeric)
add("sib_part_numeric_gap",
    statute([N("part", "1"), N("part", "3")]),
    addr(("part", "2")))
add("sib_part_numeric_roman_label",
    statute([N("part", "I"), N("part", "III")]),
    addr(("part", "2")))
add("sib_part_alnum",
    statute([N("part", "2"), N("part", "3")]),
    addr(("part", "2a")))

# nested part mode (depth >= 2 so the part blocks are actually reachable)
add("sib_nested_part_numeric_between",
    statute([], [N("schedule", "1", [N("part", "1"), N("part", "3")])]),
    addr(("schedule", "1"), ("part", "2")))
add("sib_nested_part_numeric_below_all",
    statute([], [N("schedule", "1", [N("part", "3"), N("part", "4")])]),
    addr(("schedule", "1"), ("part", "2")))
add("sib_nested_part_numeric_above_all",
    statute([], [N("schedule", "1", [N("part", "1"), N("part", "2")])]),
    addr(("schedule", "1"), ("part", "5")))
# alnum part: bracketed base -> gap; below-all / above-all base -> NOT a gap
add("sib_nested_part_alnum_between",
    statute([], [N("schedule", "1", [N("part", "1"), N("part", "3")])]),
    addr(("schedule", "1"), ("part", "2a")))
add("sib_nested_part_alnum_exact_base",
    statute([], [N("schedule", "1", [N("part", "2"), N("part", "4")])]),
    addr(("schedule", "1"), ("part", "2a")))
add("sib_nested_part_alnum_below_all",
    statute([], [N("schedule", "1", [N("part", "3"), N("part", "4")])]),
    addr(("schedule", "1"), ("part", "2a")))
add("sib_nested_part_alnum_above_all",
    statute([], [N("schedule", "1", [N("part", "1"), N("part", "2")])]),
    addr(("schedule", "1"), ("part", "5a")))

# transparent wrapper descent
add("sib_numeric_via_wrapper",
    statute([N("section", "1", [N("pblock", None, [N("subsection", "1"), N("subsection", "3")])])]),
    addr(("section", "1"), ("subsection", "2")))

# len(path)==1 -> parent is body
add("sib_path1_numeric",
    statute([N("section", "1"), N("section", "3")]),
    addr(("section", "2")))

# malformed text -> not a recognized mode
add("sib_unrecognized",
    statute([N("section", "1", [N("subsection", "x.y")])]),
    addr(("section", "1"), ("subsection", "x.y")))

# parent missing
add("sib_parent_missing",
    statute([N("section", "9")]),
    addr(("section", "1"), ("subsection", "2")))


# ========== _malformed_target_gap matrix ==========



# placeholder bracket label
addm("placeholder_bracket",
     statute([N("section", "1")]),
     addr(("section", "1"), ("paragraph", "[a]")))
# "note" label
addm("note_label",
     statute([N("section", "1")]),
     addr(("section", "1"), ("paragraph", "note")))
# crossheading label
addm("crossheading_label",
     statute([N("section", "1")]),
     addr(("section", "1"), ("paragraph", "cross-heading")))
# schedule root blank label
addm("schedule_blank_root",
     statute([], [N("schedule", "")]),
     addr(("schedule", ""), ("paragraph", "1")))
# subsection-alpha parent + roman paragraph leaf
addm("subsec_alpha_roman_para",
     statute([N("section", "1", [N("subsection", "a", [N("paragraph", "i")])])]),
     addr(("section", "1"), ("subsection", "a"), ("paragraph", "i")))
# paragraph roman under subsection w/ grandchild match
addm("para_roman_grandchild_match",
     statute([N("section", "1", [N("subsection", "1", [N("paragraph", "a", [N("subparagraph", "i")])])])]),
     addr(("section", "1"), ("subsection", "1"), ("paragraph", "i")))
# subparagraph alpha under paragraph w/ roman children
addm("subpara_alpha_roman_children",
     statute([N("section", "1", [N("paragraph", "1", [N("subparagraph", "i"), N("subparagraph", "ii")])])]),
     addr(("section", "1"), ("paragraph", "1"), ("subparagraph", "a")))
# subparagraph alpha under paragraph w/ digit children
addm("subpara_alpha_digit_children",
     statute([N("section", "1", [N("paragraph", "1", [N("subparagraph", "1"), N("subparagraph", "2")])])]),
     addr(("section", "1"), ("paragraph", "1"), ("subparagraph", "a")))
# subparagraph digit under paragraph w/ item/point children
addm("subpara_digit_item_children",
     statute([N("section", "1", [N("paragraph", "1", [N("item", "i"), N("item", "ii")])])]),
     addr(("section", "1"), ("paragraph", "1"), ("subparagraph", "1")))
# item digit under item/point/subpara parent w/ roman item children
addm("item_digit_roman_children",
     statute([N("section", "1", [N("subparagraph", "1", [N("item", "i"), N("item", "ii")])])]),
     addr(("section", "1"), ("subparagraph", "1"), ("item", "1")))
# item alpha(>1) under subparagraph w/ single-alpha item children
addm("item_alpha_single_alpha_children",
     statute([N("section", "1", [N("subparagraph", "1", [N("item", "a"), N("item", "b")])])]),
     addr(("section", "1"), ("subparagraph", "1"), ("item", "ab")))
# item alpha under paragraph w/ digit-suffix subparagraph children
addm("item_alpha_under_para_subpara_children",
     statute([N("section", "1", [N("paragraph", "1", [N("subparagraph", "1"), N("subparagraph", "2a")])])]),
     addr(("section", "1"), ("paragraph", "1"), ("item", "a")))
# paragraph digit under subsection w/ alpha children
addm("para_digit_alpha_children",
     statute([N("section", "1", [N("subsection", "1", [N("paragraph", "a"), N("paragraph", "b")])])]),
     addr(("section", "1"), ("subsection", "1"), ("paragraph", "1")))
# paragraph alpha(>1) under subsection w/ single-alpha children
addm("para_alpha_single_alpha_children",
     statute([N("section", "1", [N("subsection", "1", [N("paragraph", "a"), N("paragraph", "b")])])]),
     addr(("section", "1"), ("subsection", "1"), ("paragraph", "ab")))
# paragraph alpha(>1): first in child + rest in descendant
addm("para_alpha_first_rest_descendant",
     statute([N("section", "1", [N("subsection", "1", [N("paragraph", "a", [N("subparagraph", "b")])])])]),
     addr(("section", "1"), ("subsection", "1"), ("paragraph", "ab")))
# subsection digit under section w/ blank subsection child
addm("subsec_digit_blank_child",
     statute([N("section", "1", [N("subsection", "")])]),
     addr(("section", "1"), ("subsection", "2")))
# subsection digit w/ numeric+alpha extension child
addm("subsec_digit_extension_child",
     statute([N("section", "1", [N("subsection", "2abc")])]),
     addr(("section", "1"), ("subsection", "2")))
# schedule len2 paragraph under schedule w/ part children
addm("sched_para_part_children",
     statute([], [N("schedule", "1", [N("part", "1")])]),
     addr(("schedule", "1"), ("paragraph", "1")))
# schedule len2 part under schedule w/ crossheading/pblock children
addm("sched_part_crossheading_children",
     statute([], [N("schedule", "1", [N("crossheading", None)])]),
     addr(("schedule", "1"), ("part", "1")))
# schedule paragraph under part w/ crossheading children
addm("sched_para_under_part_crossheading",
     statute([], [N("schedule", "1", [N("part", "1", [N("crossheading", None)])])]),
     addr(("schedule", "1"), ("part", "1"), ("paragraph", "1")))
# subsection digit under section: no subsection, has paragraph
addm("subsec_digit_no_subsec_has_para",
     statute([N("section", "1", [N("paragraph", "a")])]),
     addr(("section", "1"), ("subsection", "1")))
# subsection alpha under section: no subsection has paragraph
addm("subsec_alpha_no_subsec_has_para",
     statute([N("section", "1", [N("paragraph", "a")])]),
     addr(("section", "1"), ("subsection", "a")))
# subsection alpha with digit-suffix subsection children
addm("subsec_alpha_digitsuffix_children",
     statute([N("section", "1", [N("subsection", "1a"), N("subsection", "2")])]),
     addr(("section", "1"), ("subsection", "a")))
# subsection digit+2letters with digit-suffix children
addm("subsec_alnummulti_children",
     statute([N("section", "1", [N("subsection", "1a"), N("subsection", "2")])]),
     addr(("section", "1"), ("subsection", "1ab")))
# schedule sectionlike under schedule w/ part children
addm("sched_sectionlike_part_children",
     statute([], [N("schedule", "1", [N("part", "1")])]),
     addr(("schedule", "1"), ("section", "1")))
# "and" anywhere in path
addm("and_in_path",
     statute([N("section", "1")]),
     addr(("section", "1"), ("paragraph", "and")))
# negative: plain present subsection
addm("negative_plain",
     statute([N("section", "1", [N("subsection", "1")])]),
     addr(("section", "1"), ("subsection", "1")))
# negative: empty path
addm("negative_empty",
     statute([N("section", "1")]),
     addr())


# Frozen golden snapshot: case_id -> (malformed_target_gap, missing_sibling_range_gap).
GOLDEN: dict[str, tuple[bool, bool]] = {
    'sib_numeric_w2_1_3': (False, True),
    'sib_numeric_w5_1_2': (False, True),
    'sib_numeric_w1_2_3': (False, True),
    'sib_numeric_w2_2': (False, False),
    'sib_numeric_w4_1_2_3': (False, True),
    'sib_numeric_w2_none': (False, False),
    'sib_numeric_blank_present': (True, True),
    'sib_numeric_suffix_siblings': (False, True),
    'sib_alpha_wb_a_c': (False, True),
    'sib_alpha_wa_b_c': (False, True),
    'sib_alpha_wc_a_b': (False, True),
    'sib_alpha_wb_b': (False, False),
    'sib_alpha_repeated_siblings': (False, False),
    'sib_alpha_prefix_sibling': (False, True),
    'sib_alpha_suffix_startswith': (False, True),
    'sib_alpha_suffix_first_in_alpha_raw': (False, False),
    'sib_alpha_suffix_gap': (False, True),
    'sib_roman_wii_i_iii': (False, True),
    'sib_roman_wiv_i_ii': (False, True),
    'sib_roman_wi_ii_iii': (False, True),
    'sib_roman_wii_ii': (False, True),
    'sib_roman_wiiii_i_v': (False, False),
    'sib_alnum_gap_between': (False, True),
    'sib_alnum_base_present': (False, True),
    'sib_alnum_no_pairs_numeric_suffix': (False, True),
    'sib_alnum_same_num_extension': (False, True),
    'sib_alnum_multi_pairs': (False, True),
    'sib_alnum_multi_base_present': (True, True),
    'sib_alnum_multi_local_suffix_sibling': (True, True),
    'sib_alpha_num_pairs': (False, True),
    'sib_alpha_num_raw_prefix': (False, True),
    'sib_part_numeric_gap': (False, False),
    'sib_part_numeric_roman_label': (False, False),
    'sib_part_alnum': (False, False),
    'sib_nested_part_numeric_between': (False, True),
    'sib_nested_part_numeric_below_all': (False, True),
    'sib_nested_part_numeric_above_all': (False, True),
    'sib_nested_part_alnum_between': (False, True),
    'sib_nested_part_alnum_exact_base': (False, True),
    'sib_nested_part_alnum_below_all': (False, True),
    'sib_nested_part_alnum_above_all': (False, True),
    'sib_numeric_via_wrapper': (False, True),
    'sib_path1_numeric': (False, False),
    'sib_unrecognized': (False, False),
    'sib_parent_missing': (False, False),
    'MAL_placeholder_bracket': (True, False),
    'MAL_note_label': (True, False),
    'MAL_crossheading_label': (True, False),
    'MAL_schedule_blank_root': (True, False),
    'MAL_subsec_alpha_roman_para': (True, False),
    'MAL_para_roman_grandchild_match': (True, True),
    'MAL_subpara_alpha_roman_children': (True, True),
    'MAL_subpara_alpha_digit_children': (True, False),
    'MAL_subpara_digit_item_children': (True, False),
    'MAL_item_digit_roman_children': (False, False),
    'MAL_item_alpha_single_alpha_children': (False, False),
    'MAL_item_alpha_under_para_subpara_children': (True, False),
    'MAL_para_digit_alpha_children': (True, False),
    'MAL_para_alpha_single_alpha_children': (True, False),
    'MAL_para_alpha_first_rest_descendant': (True, False),
    'MAL_subsec_digit_blank_child': (True, False),
    'MAL_subsec_digit_extension_child': (True, False),
    'MAL_sched_para_part_children': (True, False),
    'MAL_sched_part_crossheading_children': (True, False),
    'MAL_sched_para_under_part_crossheading': (True, False),
    'MAL_subsec_digit_no_subsec_has_para': (True, False),
    'MAL_subsec_alpha_no_subsec_has_para': (True, False),
    'MAL_subsec_alpha_digitsuffix_children': (True, False),
    'MAL_subsec_alnummulti_children': (True, True),
    'MAL_sched_sectionlike_part_children': (True, False),
    'MAL_and_in_path': (True, False),
    'MAL_negative_plain': (False, False),
    'MAL_negative_empty': (False, False),
}


@pytest.mark.parametrize("case_id,st,target", CASES, ids=[c[0] for c in CASES])
def test_gap_function_characterization(case_id: str, st: IRStatute, target: LegalAddress) -> None:
    """Both gap functions must return their frozen booleans for every shape."""
    ex = UKReplayExecutor(st)
    expected_mal, expected_sib = GOLDEN[case_id]
    assert ex._malformed_target_gap(target) is expected_mal, (
        f"_malformed_target_gap drifted for {case_id}"
    )
    assert ex._missing_sibling_range_gap(target) is expected_sib, (
        f"_missing_sibling_range_gap drifted for {case_id}"
    )


def test_characterization_matrix_is_complete() -> None:
    """Guard: every CASES entry has a golden row and ids are unique."""
    ids = [c[0] for c in CASES]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(GOLDEN)
