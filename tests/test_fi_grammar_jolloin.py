"""Byte-identity tests for the driver-level ``jolloin`` emission builder.

``jolloin`` is driver-level: there is no recognizer to drive through the new
``parser.parse`` (which still rejects ``jolloin_renumber_pairs``). So these tests
validate the BUILDER directly against the old parser's prepended SIIRTAA group:

  * feed a single jolloin-bearing clause through the OLD ``surface_parse.parse``,
  * isolate the synthetic ``fi.jolloin_renumber`` group it prepends (index 0),
  * drive ``build_jolloin_group`` with the EXACT consumed positions + per-position
    contexts the old parser computed (captured via a Stream subclass that stashes
    the live accumulator lists),
  * assert the two groups are byte-identical via ``grammar.diff`` canonicalization.

Plus unit-level tests of the builder's construction rules on synthetic inputs
(P/L kinds, M-kind with and without anchor), and a negative test (all-M with no
anchor section → no group).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar.diff import _jsonify
from lawvm.finland.johtolause.grammar.jolloin import (
    build_jolloin_group,
    build_jolloin_nodes,
)
from lawvm.finland.johtolause.jolloin_pair import JolloinRenumberPair
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs
from lawvm.finland.johtolause.surface_model import (
    SurfaceRenumberTail,
    SurfaceSubRef,
    SurfaceTargetRef,
    SurfaceVerbGroup,
    TargetKind,
    VerbKind,
)


# ---------------------------------------------------------------------------
# Old-parser parity harness (capture the live jolloin accumulators).
# ---------------------------------------------------------------------------

_OrigStream = surface_parse.Stream


class _CapturingStream(_OrigStream):  # type: ignore[misc, valid-type]
    """Stream subclass that records the real parse's jolloin accumulator lists."""

    _last_positions: list[int] | None = None
    _last_contexts: list[tuple[int, str, str]] | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Only the real parse Stream is built with these lists (the throwaway
        # lead-in probe Stream gets None); capture only that one.
        if self.consumed_jolloin_positions is not None:
            type(self)._last_positions = self.consumed_jolloin_positions
        if self.consumed_jolloin_contexts is not None:
            type(self)._last_contexts = self.consumed_jolloin_contexts


def _old_jolloin_group(model) -> SurfaceVerbGroup | None:
    if not model.verb_groups:
        return None
    vg = model.verb_groups[0]
    if vg.verb != VerbKind.SIIRTAA or not vg.nodes:
        return None
    for node in vg.nodes:
        w = getattr(node, "witness", None)
        if w is None or w.rule_id != "fi.jolloin_renumber":
            return None
    return vg


def _canon(vg: SurfaceVerbGroup | None) -> Any:
    return None if vg is None else _jsonify(asdict(vg))


def _old_group_and_builder_group(
    text: str,
) -> tuple[SurfaceVerbGroup | None, SurfaceVerbGroup | None]:
    """Parse ``text`` with the old parser; return (old group, builder group)."""
    raw = tokenize(text)
    tokens, pairs = apply_annotations_with_jolloin_pairs(raw)
    assert pairs, f"expected jolloin pairs for: {text!r}"

    surface_parse.Stream = _CapturingStream  # type: ignore
    _CapturingStream._last_positions = None
    _CapturingStream._last_contexts = None
    try:
        old_model = surface_parse.parse(tokens, jolloin_renumber_pairs=pairs)
    finally:
        surface_parse.Stream = _OrigStream

    positions = list(_CapturingStream._last_positions or [])
    contexts = {pos: (sec, ch) for pos, sec, ch in (_CapturingStream._last_contexts or [])}

    old_group = _old_jolloin_group(old_model)
    builder_group = build_jolloin_group(positions, pairs, contexts)
    return old_group, builder_group


# Worked examples: real corpus-shaped jolloin clauses spanning the pair kinds.
_WORKED_EXAMPLES = [
    # Section (P-kind) renumber: "jolloin nykyinen 13 § siirtyy 12 §:ksi".
    (
        "Eduskunnan päätöksen mukaisesti muutetaan apteekkimaksusta annetun lain "
        "(148/46) 2, 7, 10 §, 13 §:n 1 momentti ja 14 §, jolloin nykyinen 13 § "
        "siirtyy 12 §:ksi."
    ),
    # Momentti (M-kind) renumber, anchored to a preceding section: the builder
    # must materialize the anchor section + momentti sub-ref.
    (
        "Eduskunnan päätöksen mukaisesti muutetaan Suomen aluevesien rajoista "
        "annetun lain 1 §:n 1 momentti, jolloin nykyinen 2 momentti siirtyy "
        "3 momentiksi."
    ),
    # Chapter (L-kind) renumber: "jolloin nykyinen N luku siirtyy M luvuksi".
    (
        "Eduskunnan päätöksen mukaisesti lisätään lakiin uusi 3 a luku, jolloin "
        "nykyinen 4 luku siirtyy 5 luvuksi."
    ),
]


@pytest.mark.parametrize("text", _WORKED_EXAMPLES)
def test_builder_byte_identical_to_old_prepended_group(text: str) -> None:
    """The builder's group is byte-identical to the old parser's prepended group."""
    old_group, builder_group = _old_group_and_builder_group(text)
    # Both must exist (these examples all carry an emittable jolloin pair).
    assert old_group is not None, f"old parser emitted no jolloin group for: {text!r}"
    assert builder_group is not None, f"builder emitted no jolloin group for: {text!r}"
    assert _canon(builder_group) == _canon(old_group), (
        f"builder/old jolloin group diverge for: {text!r}\n"
        f"old     = {_canon(old_group)}\n"
        f"builder = {_canon(builder_group)}"
    )


# ---------------------------------------------------------------------------
# Unit-level construction rules (synthetic inputs).
# ---------------------------------------------------------------------------


def test_section_pair_builds_bare_target_plus_tail() -> None:
    """A P-kind pair → bare SECTION target + renumber tail, no chapter/sub-refs."""
    nodes = build_jolloin_nodes(
        consumed_positions=[5],
        jolloin_renumber_pairs={5: [JolloinRenumberPair("13", "12", "P")]},
        jolloin_contexts={},
    )
    assert len(nodes) == 2
    tgt, tail = nodes
    assert isinstance(tgt, SurfaceTargetRef)
    assert tgt.kind == TargetKind.SECTION
    assert tgt.label == "13"
    assert tgt.chapter == ""
    assert tgt.sub_refs == ()
    assert tgt.notes == ("renumber_clause",)
    assert tgt.witness is not None and tgt.witness.rule_id == "fi.jolloin_renumber"
    assert isinstance(tail, SurfaceRenumberTail)
    assert tail.new_label == "12"
    assert tail.witness is not None and tail.witness.rule_id == "fi.jolloin_renumber"


def test_chapter_pair_uses_pair_kind_target() -> None:
    """An L-kind pair → CHAPTER target (the pair-kind drives TargetKind)."""
    nodes = build_jolloin_nodes(
        consumed_positions=[3],
        jolloin_renumber_pairs={3: [JolloinRenumberPair("4", "5", "L")]},
    )
    assert len(nodes) == 2
    tgt = nodes[0]
    assert isinstance(tgt, SurfaceTargetRef)
    assert tgt.kind == TargetKind.CHAPTER
    assert tgt.label == "4"


def test_momentti_pair_materializes_anchor_section() -> None:
    """An M-kind pair with an anchor → SECTION target + momentti sub-ref."""
    nodes = build_jolloin_nodes(
        consumed_positions=[7],
        jolloin_renumber_pairs={7: [JolloinRenumberPair("2", "3", "M")]},
        jolloin_contexts={7: ("1", "5")},
    )
    assert len(nodes) == 2
    tgt = nodes[0]
    assert isinstance(tgt, SurfaceTargetRef)
    assert tgt.kind == TargetKind.SECTION
    assert tgt.label == "1"
    assert tgt.chapter == "5"
    assert tgt.sub_refs == (SurfaceSubRef(momentti=2),)
    assert tgt.notes == ("renumber_clause",)


def test_momentti_pair_without_anchor_is_dropped() -> None:
    """NEGATIVE: an M-kind pair with no anchor section drops the WHOLE pair."""
    nodes = build_jolloin_nodes(
        consumed_positions=[7],
        jolloin_renumber_pairs={7: [JolloinRenumberPair("2", "3", "M")]},
        jolloin_contexts={},  # no context for pos 7 → ("", "")
    )
    assert nodes == []
    # And the group wrapper returns None (no empty group is prepended).
    group = build_jolloin_group([7], {7: [JolloinRenumberPair("2", "3", "M")]}, {})
    assert group is None


def test_empty_inputs_build_no_group() -> None:
    """No consumed positions → no nodes, no group."""
    assert build_jolloin_nodes([], {}) == []
    assert build_jolloin_group([], {}) is None


def test_multiple_pairs_preserve_order() -> None:
    """Multiple pairs at one position emit in order, each target+tail adjacent."""
    nodes = build_jolloin_nodes(
        consumed_positions=[2],
        jolloin_renumber_pairs={
            2: [
                JolloinRenumberPair("10", "11", "P"),
                JolloinRenumberPair("12", "13", "P"),
            ]
        },
    )
    assert len(nodes) == 4
    assert isinstance(nodes[0], SurfaceTargetRef) and nodes[0].label == "10"
    assert isinstance(nodes[1], SurfaceRenumberTail) and nodes[1].new_label == "11"
    assert isinstance(nodes[2], SurfaceTargetRef) and nodes[2].label == "12"
    assert isinstance(nodes[3], SurfaceRenumberTail) and nodes[3].new_label == "13"


def test_group_verb_is_siirtaa() -> None:
    """The wrapped group is always a SIIRTAA verb group."""
    group = build_jolloin_group([1], {1: [JolloinRenumberPair("3", "4", "P")]})
    assert group is not None
    assert group.verb == VerbKind.SIIRTAA
    assert len(group.nodes) == 2
