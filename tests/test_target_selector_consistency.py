"""Corpus-scale codec round-trip test for ``AmendmentOp.target_selector``.

This is the *real* validation of the codec: it does not hand-craft op shapes
(that is the golden-fixture job of ``test_target_selector_codec.py``). Instead it
harvests EVERY ``AmendmentOp`` that flows through the production compile
chokepoint (``compile_amendment_ops``) while replaying a representative slice of
pinned real Finnish statutes, and asserts the codec round-trip invariant
TARGET-03 at corpus scale:

    TargetSelectorCodecV1.from_legacy(TargetSelectorCodecV1.to_legacy(sel)) == sel

for every harvested op's stored ``target_selector`` (W6 Phase C: the selector is
the sole stored representation; ``op.target_cols`` is its legacy projection). If
any *real* op shape cannot round-trip losslessly through the legacy codec, that
is a FINDING (as ``special_raw`` was at Wave 0): the codec needs extending, not
papering over. The test reports the failing shapes explicitly so the finding is
self-evidencing.

Why ``compile_amendment_ops`` is the harvest point: it is the single production
boundary every replayed amendment's ``ops`` list passes through
(``process_pipeline.py``). Harvesting there captures ops from *all* construction
sites (``AmendmentOp.from_lo`` and the direct ``AmendmentOp(...)`` builders in
``frontend_compile`` / ``johtolause_supplements`` / recovery), i.e. the true
production population — not a curated subset.
"""

from __future__ import annotations

from typing import Any

import pytest

from lawvm.finland.ops import AmendmentOp
from lawvm.finland.target_selector_codec import (
    AmendmentOpV1Record,
    TargetSelectorCodecV1,
)

# A slice of pinned statutes chosen for amendment-shape breadth: chaptered codes,
# part/chapter scopes, momentti/kohta/alakohta descendants, heading/intro facets.
# Kept modest so the test stays well under a minute while still yielding several
# hundred real ops. All ids must exist in ORACLE_VERSIONS (pinned).
_CONSISTENCY_CORPUS: tuple[str, ...] = (
    "1929/234",  # heavily chaptered penal-era code — part/chapter scopes
    "1968/360",  # mixed shapes
    "1987/990",  # broadly amended
    "1966/722",
    "1969/327",
    "1974/258",
    "1982/710",
    "1990/1039",
    "1993/1709",
    "1922/312",
    "1947/625",
    "1948/404",
)


def _harvest_ops(monkeypatch: pytest.MonkeyPatch) -> list[AmendmentOp]:
    """Replay the corpus slice, capturing every op passed to compile_amendment_ops."""
    from tests.corpus_pin_helpers import ORACLE_VERSIONS, pinned_replay
    import lawvm.finland.process_pipeline as process_pipeline

    captured: list[AmendmentOp] = []
    real_compile = process_pipeline.compile_amendment_ops

    def _capturing_compile(state: Any, ops: list[AmendmentOp], *args: Any, **kwargs: Any) -> Any:
        captured.extend(ops)
        return real_compile(state, ops, *args, **kwargs)

    monkeypatch.setattr(process_pipeline, "compile_amendment_ops", _capturing_compile)

    for statute_id in _CONSISTENCY_CORPUS:
        if statute_id not in ORACLE_VERSIONS:
            # Pin missing -> skip this id rather than fail the harness; the slice
            # is a representativeness aid, not a fixed contract.
            continue
        pinned_replay(
            statute_id,
            mode="official_consolidation",
            quiet=True,
            build_full_products=False,
        )
    return captured


def test_target_selector_round_trips_at_corpus_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """TARGET-03 at corpus scale: every real op's selector re-encodes byte-exact.

    Asserts ``from_legacy(to_legacy(op.target_selector))`` reproduces the stored
    selector EXACTLY for every op harvested from the pinned replay slice (W6
    Phase C: the selector is the sole stored target representation). Any mismatch
    is reported with the offending op's projected legacy record (self-evidencing
    finding) — a real op shape the legacy codec cannot round-trip losslessly.
    """
    ops = _harvest_ops(monkeypatch)

    # Sanity floor: the slice must actually exercise the codec at scale. If this
    # trips, the harvest point or the corpus pins drifted (a real failure to fix,
    # not a flake) — a green-by-vacuity test would be worthless.
    assert len(ops) >= 200, (
        f"corpus slice yielded only {len(ops)} ops; expected >= 200. The harvest "
        f"chokepoint (compile_amendment_ops) or the pinned corpus slice has drifted."
    )

    mismatches: list[tuple[AmendmentOpV1Record, AmendmentOpV1Record]] = []
    for op in ops:
        selector = op.target_selector
        expected = TargetSelectorCodecV1.to_legacy(selector)
        try:
            relowered = TargetSelectorCodecV1.from_legacy(expected)
            actual = TargetSelectorCodecV1.to_legacy(relowered)
        except Exception as exc:  # noqa: BLE001 — surface the offending shape
            # Sentinel mismatch record: target_unit_kind is the typed literal, so
            # carry the raised-shape text in target_section (a free str field) to
            # keep the failure report informative while staying type-valid.
            actual = AmendmentOpV1Record(
                target_unit_kind="section",
                target_section=f"<raised {type(exc).__name__}: {exc}>",
                target_chapter=None,
                target_part=None,
                target_paragraph=None,
                target_item=None,
                target_subitem=None,
                target_special=None,
            )
        if actual != expected:
            mismatches.append((expected, actual))

    if mismatches:
        # Deduplicate by expected shape so the report is readable.
        seen: set[tuple[Any, ...]] = set()
        lines: list[str] = []
        for expected, actual in mismatches:
            key = (
                expected.target_unit_kind,
                expected.target_section,
                expected.target_chapter,
                expected.target_part,
                expected.target_paragraph,
                expected.target_item,
                expected.target_subitem,
                expected.target_special,
            )
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  expected={expected!r}\n  actual=  {actual!r}")
        raise AssertionError(
            f"{len(mismatches)} op(s) failed the TARGET-03 round-trip "
            f"({len(seen)} distinct shapes). Each is a codec FINDING — extend the "
            f"codec to represent the shape losslessly (do NOT relax the test):\n"
            + "\n".join(lines)
        )


def test_corpus_slice_exercises_descendant_and_scope_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The slice must cover the non-trivial shapes, not just bare sections.

    Guards against a future corpus pin change silently reducing the test to only
    flat ``section`` ops (which would pass TARGET-03 vacuously for the hard cases).
    """
    ops = _harvest_ops(monkeypatch)

    has_scope = any(op.target_cols.target_chapter or op.target_cols.target_part for op in ops)
    has_descendant = any(
        op.target_cols.target_paragraph is not None or op.target_cols.target_item is not None
        for op in ops
    )

    assert has_scope, "corpus slice exercised no chapter/part scope ops"
    assert has_descendant, "corpus slice exercised no momentti/kohta descendant ops"
