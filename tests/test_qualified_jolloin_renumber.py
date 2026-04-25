from __future__ import annotations

from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.johtolause.api import parse_clause
from lawvm.finland.johtolause.surface_model import (
    SurfaceRenumberTail,
    SurfaceTargetRef,
    TargetKind,
    VerbKind,
)
from lawvm.tools.inspect_amendment import build_amendment_bundle


def test_parse_clause_handles_qualified_jolloin_chapter_renumber() -> None:
    text = (
        "lisätään lakiin uusi 8 luku, jolloin nykyinen 8 luku, "
        "sellaisena kuin se on mainitussa 23 päivänä toukokuuta 1986 annetussa "
        "laissa, siirtyy 9 luvuksi"
    )

    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None
    assert sc.verb_groups

    first_vg = sc.verb_groups[0]
    assert first_vg.verb == VerbKind.SIIRTAA
    assert len(first_vg.nodes) == 2

    target, tail = first_vg.nodes
    assert isinstance(target, SurfaceTargetRef)
    assert target.kind == TargetKind.CHAPTER
    assert target.label == "8"

    assert isinstance(tail, SurfaceRenumberTail)
    assert tail.new_label == "9"


def test_1990_811_compiles_the_qualified_jolloin_chapter_renumber() -> None:
    bundle = build_amendment_bundle("1978/38", "1990/811", "legal_pit")
    compiled_ops = bundle["compiled_ops"]

    assert "RENUMBER 8 luku" in compiled_ops
    assert "INSERT 8 luku" in compiled_ops


def test_1978_38_preserves_shifted_old_chapter_9_after_1990_811() -> None:
    state = pinned_replay("1978/38", mode="legal_pit", stop_before="1994/16", quiet=True)
    chapter_labels = [
        child.label
        for child in state.ir.children
        if child.kind.value == "chapter"
    ]

    assert "8" in chapter_labels
    assert "9" in chapter_labels
